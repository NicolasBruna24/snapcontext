#!/usr/bin/env python3
"""Interfaz web de SnapContext (FastAPI + WebSockets), v1.2.0.

Sirve ``static/index.html`` y expone el endpoint ``/ws``. La UI envía mensajes
JSON y el servidor responde/reenvía eventos por el WebSocket:

- ``{"tipo": "tarea", ...}`` (o con ``consulta``): ejecuta el orquestador en un
  hilo y reenvía cada evento (``log``, ``selección``, ``aider``, ``test``,
  ``final``, …) para mostrar el avance en tiempo real.
- ``{"tipo": "leer_archivo", "ruta", "directorio"}`` → ``archivo_seleccionado``
  (contenido + lenguaje para el editor Monaco).
- ``{"tipo": "guardar_archivo", "ruta", "contenido", "directorio"}`` →
  ``archivo_guardado``.
- ``{"tipo": "dependencias", "directorio"}`` → ``dependencias_actualizadas``
  con el grafo (nodos + enlaces) del proyecto.
- ``{"tipo": "semantica", "consulta", "directorio"}`` → ``semanticos`` con los
  resultados de la búsqueda por embeddings (extra ``embeddings``).
- ``{"tipo": "explorar", "tema", "directorio"}`` → ``exploracion``.
- ``{"tipo": "accion", "accion", ...}`` → acción rápida (Fix/Review/Plan/Run/
  Search/Explorar) → ``accion_ejecutada`` + avance ``log``.
"""

import asyncio
import json
import os
import queue
import shlex
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse

from exceptions import RutaInseguraError
from web import filesystem as fs_web
from web import remota as remota_web
from web import seguridad

#: Alias de la frontera de filesystem activa (B9.61-B). Todo path del gateway
#: pasa por aquí; no hay validación de rutas por endpoint.
frontera = fs_web.frontera_activa
ErrorFrontera = fs_web.ErrorFrontera
WorkspaceRootInvalido = fs_web.WorkspaceRootInvalido

_ESTATICO = (
    Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "web" / "static"
)
# En el ejecutable PyInstaller (v1.5.0), ``web/static`` va a ``_MEIPASS``;
# en desarrollo, ``__file__`` es ``web/app.py`` y el estático queda al lado.
# ``directorio`` por defecto si la UI no lo indica (directorio de trabajo).
_DIRECTORIO_DEFECTO = "."


# --------------------------------------------------------------------------
# API pública (v3.6.0): estado de tareas asíncronas y control del daemon.
# --------------------------------------------------------------------------
_TAREAS_API: dict[str, dict] = {}
_CANDADO_TAREAS_API = threading.Lock()
_DAEMON_HILO: threading.Thread | None = None
_DAEMON_PARAR = threading.Event()

# B9.61-D (D-03): registro idempotente del hook de invalidación de tareas.
_HOOK_TAREAS_REGISTRADO: dict[str, bool] = {"activo": False}

import task_queue as tq  # noqa: E402  (cola de tareas: contexto de seguridad D-02)


def credencial_efectiva_propietaria() -> str:
    """Identidad (no secreto) de la credencial vigente del gateway.

    B9.59 §10: el owner de una tarea es la **identidad** de la credencial que
    autenticó el request, nunca el secreto. Se usa una huella SHA-256 truncada
    para poder asociar y comparar tareas sin almacenar material sensible.
    """
    import hashlib

    cred = seguridad.cargar_credencial()
    if not cred:
        return "sin-credencial"
    return "api:" + hashlib.sha256(cred.encode("utf-8")).hexdigest()[:16]

API_PREFIJO = "/api/v1"


# ---------------------------------------------------------------------------
# Capability model cerrado (B9.59 §7 / B9.60 §7)
#
# CAPABILITY_MAP es la ÚNICA fuente de verdad superficie → capability/política:
#   - clave HTTP = plantilla de la ruta (p. ej. "/api/v1/skills");
#   - clave WS handshake = "WS <ruta>"; clave WS mensaje = "WS <ruta>:<tipo>"
#     y para acciones anidadas "WS <ruta>:accion:<accion>".
# Sin entrada en el mapa → DENY (403/1008), nunca comportamiento implícito.
# ---------------------------------------------------------------------------
_D = seguridad.DeclaracionSuperficie

_PUBLICA = _D(publica=True)
_DOC = _D(publica=True, solo_loopback=True)
_READ = _D(capacidad="READ_WORKSPACE", requiere_auth=True)
_WRITE = _D(capacidad="WRITE_WORKSPACE", requiere_auth=True)
_EXECUTE = _D(capacidad="EXECUTE_REMOTE", requiere_auth=True)
_TASKS = _D(capacidad="MANAGE_TASKS", requiere_auth=True, solo_loopback=True)
_DAEMON_ESTADO = _D(capacidad="READ_WORKSPACE", requiere_auth=True)
_DAEMON_GESTION = _D(capacidad="MANAGE_DAEMON", requiere_auth=True, solo_loopback=True)
_PLUGINS = _D(capacidad="USE_PLUGINS", requiere_auth=True, solo_loopback=True)
_INTERACTIVO = _D(capacidad="USE_INTERACTIVE", requiere_auth=True, solo_loopback=True)
_CHAT = _D(capacidad="USE_CHAT", requiere_auth=True)
_WEBHOOK = _D(capacidad="WEBHOOK_RECEIVE")
_HANDSHAKE_WS = _D(requiere_auth=True)
_LIVENESS = _D()

CAPABILITY_MAP: dict[str, seguridad.DeclaracionSuperficie] = {
    # --- superficies públicas mínimas (sin capability) ---
    "/": _PUBLICA,
    "/health": _PUBLICA,
    f"{API_PREFIJO}/health": _PUBLICA,
    "/interactive": _PUBLICA,
    "/interactive/health": _PUBLICA,
    "/static": _PUBLICA,
    # --- documentación / schema: loopback, DENY fuera (B9.59 §12) ---
    "/docs": _DOC,
    "/redoc": _DOC,
    "/openapi.json": _DOC,
    "/docs/oauth2-redirect": _DOC,
    # --- API autenticada ---
    f"{API_PREFIJO}/query": _EXECUTE,
    f"{API_PREFIJO}/plan": _EXECUTE,
    f"{API_PREFIJO}/chat": _CHAT,
    f"{API_PREFIJO}/skills": _READ,
    f"{API_PREFIJO}/tasks/{{task_id}}": _TASKS,
    f"{API_PREFIJO}/daemon": _DAEMON_ESTADO,  # "iniciar"/"detener" → MANAGE_DAEMON (abajo)
    # --- webhooks: firma del proveedor (sin key-auth); rediseño en B9.61-C ---
    "/webhook/telegram": _WEBHOOK,
    "/webhook/discord": _WEBHOOK,
    "/webhook/github": _WEBHOOK,
    "/api/github/webhook": _WEBHOOK,
    # --- WebSockets: handshake (auth pre-accept) ---
    "WS /ws": _HANDSHAKE_WS,
    "WS /ws/interactive": _INTERACTIVO,
    # --- WebSockets: mensajes de /ws (dispatcher cerrado, sin fallback) ---
    "WS /ws:tarea": _EXECUTE,
    "WS /ws:accion:run": _EXECUTE,
    "WS /ws:accion:fix": _EXECUTE,
    "WS /ws:accion:review": _EXECUTE,
    "WS /ws:accion:plan": _EXECUTE,
    "WS /ws:accion:search": _EXECUTE,
    "WS /ws:accion:explorar": _READ,
    "WS /ws:accion:asesor": _READ,
    "WS /ws:accion:plugins": _PLUGINS,
    "WS /ws:accion:plugin_install": _PLUGINS,
    "WS /ws:accion:plugin_remove": _PLUGINS,
    "WS /ws:leer_archivo": _READ,
    "WS /ws:guardar_archivo": _WRITE,
    "WS /ws:dependencias": _READ,
    "WS /ws:semantica": _READ,
    "WS /ws:explorar": _READ,
    "WS /ws:ping": _LIVENESS,
}


def _clave_despacho_ws(tipo: str, mensaje: dict) -> str:
    """Clave de dispatch WS para un mensaje (registro cerrado, sin fallback)."""
    if tipo == "accion":
        sub = (mensaje.get("accion") or "").strip()
        return f"WS /ws:accion:{sub}"
    return f"WS /ws:{tipo}"


async def _cuerpo_json(request: Request) -> dict | None:
    """Cuerpo JSON ya leído por FastAPI (cacheado); None si no es utilizable."""
    try:
        cuerpo = await request.json()
    except Exception:
        return None
    return cuerpo if isinstance(cuerpo, dict) else None


async def _cerrar_ws_seguro(websocket: WebSocket) -> None:
    """Cierra un WebSocket (usado por el hook de rotación, código 1008)."""
    try:
        await websocket.close(code=1008)
    except Exception:
        pass


def _ejecutar_tarea_api(task_id: str, tipo: str, cuerpo: dict, base: Path) -> None:
    """Ejecuta una tarea larga de la API en segundo plano.

    ``base`` es el directorio **ya validado y congelado** por la frontera de
    filesystem en el momento del request (B9.61-B §8/§10): el hilo en segundo
    plano nunca vuelve a interpretar el ``directorio`` crudo del cliente.
    """
    import snapcontext as sc

    with _CANDADO_TAREAS_API:
        registro = _TAREAS_API[task_id]
        registro["estado"] = "ejecutando"
    try:
        consulta = (cuerpo.get("consulta") or "").strip()
        argv = [consulta] if tipo == "query" else ["--plan", consulta]
        if str(base) != str(Path.cwd()):
            argv += ["--directorio", str(base)]
        args = sc.crear_parser().parse_args(argv)
        # La API nunca es interactiva.
        args.auto = True
        args.no_confirmar = True
        args.depurar = False
        if hasattr(args, "confirmar"):
            args.confirmar = False
        if tipo == "query":
            codigo = sc.flujo_principal(args)
        else:
            codigo = sc._ejecutar_planificador(args)
        ok = codigo == 0
        with _CANDADO_TAREAS_API:
            registro.update(
                estado="completada" if ok else "fallida", resultado={"codigo_salida": codigo}
            )
    except Exception as exc:
        with _CANDADO_TAREAS_API:
            registro.update(estado="error", error=str(exc))


def _lanzar_tarea_api(tipo: str, cuerpo: dict) -> dict:
    """Registra y lanza una tarea asíncrona. Devuelve task_id + URL.

    B9.61-B: el ``directorio`` del cliente se valida por la frontera **antes**
    de crear la tarea; si se deniega se lanza ``ErrorFrontera`` y el endpoint
    la traduce a 400. La tarea hereda el directorio ya resuelto y congelado.

    B9.61-D (D-02): la entrada del registro guarda además ``owner`` y el
    ``contexto`` de seguridad, para que el estado quede asociado a su creador.
    """
    base = fs_web.frontera_activa().base_operacion(cuerpo.get("directorio"))
    task_id = uuid.uuid4().hex
    contexto = tq.contexto_por_defecto(
        owner=credencial_efectiva_propietaria(),
        workspace_root=base,
        capabilities=("EXECUTE_REMOTE",),
        policy="m1",
        origen="gateway",
    )
    with _CANDADO_TAREAS_API:
        _TAREAS_API[task_id] = {
            "task_id": task_id,
            "tipo": tipo,
            "estado": "pendiente",
            "creado": time.time(),
            "resultado": None,
            "error": None,
            "owner": contexto.owner,
            "contexto": contexto.a_dict(),
        }
    threading.Thread(
        target=_ejecutar_tarea_api, args=(task_id, tipo, dict(cuerpo), base), daemon=True
    ).start()
    return {"task_id": task_id, "estado": "pendiente", "url": f"{API_PREFIJO}/tasks/{task_id}"}


def crear_app(  # noqa: C901  (refactor de complejidad: Fase 10c)
    api_token: str | None = None,
    interactiva: bool = False,
    *,
    host: str = "127.0.0.1",
    workspace_root: str | None = None,
) -> FastAPI:
    """Construye y devuelve la app FastAPI (rutas + WebSocket + API v3.6.0).

    ``api_token`` fija la clave exigida en las superficies autenticadas; si se
    omite, se usa la de ``~/.snapcontext/config.json`` (``api_key``, permisos
    0600) o la override de entorno ``SNAPCONTEXT_API_KEY``; la primera
    ejecución en loopback genera y entrega la clave una sola vez por stdout.
    En no-loopback sin credencial válida (≥32 caracteres, 0600) el arranque
    aborta (B9.59 §9 / B9.60 §4).

    ``host`` declara el bind del servidor: permite distinguir el arranque en
    loopback (puede generar credencial) de uno expuesto (nunca genera).

    ``workspace_root`` (``--workspace-root``) fija la raíz que confina TODA
    operación de filesystem del gateway (B9.59 §4 / B9.60 §9 / B9.61-B):

    - **loopback** sin ``workspace_root``: la raíz efectiva es el
      ``directorio`` de cada request (``resolver_raiz``), como antes; no se
      introduce una restricción arbitraria que rompa el uso local.
    - **cualquier modo** con ``workspace_root``: raíz congelada (realpath);
      todo ``directorio`` y toda ``ruta`` deben quedar dentro.
    - **no-loopback** sin ``workspace_root``: :class:`WorkspaceRootInvalido`
      → el arranque **aborta** (fail-closed, nunca se degrada a cwd).

    ``interactiva=True`` (v6.5.0, ``--web-interactive``) activa además el
    centro de control interactivo: ruta ``/interactive`` (timeline de ReAct +
    diff viewer) y WebSocket ``/ws/interactive``.
    """
    no_loopback = not seguridad.es_bind_loopback(host)
    credencial_inicial = seguridad.preparar_credencial_inicio(
        token_explicito=api_token, permitir_generar=not no_loopback
    )
    if no_loopback and (
        not credencial_inicial or len(credencial_inicial) < seguridad.MIN_LONGITUD_CREDENCIAL
    ):
        raise RuntimeError(
            "Modo no-loopback sin credencial válida: se exige una API key de al "
            "menos 32 caracteres (config.json con permisos 0600 o la variable "
            "SNAPCONTEXT_API_KEY). Arranque abortado."
        )
    # Frontera de filesystem (B9.61-B): se fija ANTES de servir nada. Una raíz
    # inválida o ausente fuera de loopback aborta el arranque (fail-closed).
    fs_web.configurar_frontera(
        exposicion="lan" if no_loopback else "loopback",
        workspace_root=workspace_root,
    )

    # B9.63-A: la versión de la API deriva de la fuente única del proyecto
    # (`VERSION`), no de un literal propio del Web Gateway.
    _sc_version = _importar_snapcontext()
    app = FastAPI(
        title="SnapContext Web",
        version=(_sc_version.VERSION if _sc_version is not None else None) or "desconocida",
    )

    # ---- Modo interactivo (v6.5.0) ---------------------------------------
    _clientes_interactivos: dict[int, queue.Queue] = {}
    _tarea_difusor: asyncio.Task | None = None
    if interactiva:
        import web.interactive as wi

        wi.activar()  # cola compartida del hub

    # ---- Autenticación + capability enforcement central (B9.61-A) --------
    # Fuente de verdad de la credencial: web.seguridad (config.json / env /
    # token de arranque); NUNCA un global de módulo (sin _API_TOKEN).
    limitador = seguridad.LimitadorFallos()
    _ws_activas: dict[WebSocket, object] = {}

    def _al_rotar_credencial() -> None:
        """Hook de rotación: cierra los WS autenticados (<60 s, B9.59 §9)."""
        for ws, bucle in list(_ws_activas.items()):
            try:
                bucle.call_soon_threadsafe(asyncio.ensure_future, _cerrar_ws_seguro(ws))  # type: ignore[union-attr]
            except RuntimeError:
                pass  # bucle del evento ya cerrado

    seguridad.registrar_hook_rotacion(_al_rotar_credencial)

    # B9.61-D (D-03, §5.1): la invalidación de tareas es un efecto **global del
    # proceso**, no por app. `crear_app` puede invocarse varias veces (tests,
    # reinicio); sin este guardia el hook se acumularía y la invalidación se
    # ejecutaría N veces por rotación. El cierre de WebSockets sí se registra
    # por app (cada app tiene su propio conjunto de conexiones).
    if not _HOOK_TAREAS_REGISTRADO["activo"]:

        def _al_rotar_credencial_tareas() -> None:
            """D-03: invalida las tareas encoladas bajo la credencial anterior.

            Solo estados no terminales: no se mata trabajo ya en ejecución
            (B9.59 §10). Nunca rompe la rotación si la cola no está disponible.
            """
            try:
                import task_queue as tq

                canceladas = tq.cancelar_pendientes_por_rotacion()
                if canceladas:
                    print(f"🔑 Rotación: {canceladas} tarea(s) cancelada(s) por rotación.")
            except Exception:  # la rotación de credencial no debe romperse
                pass

        seguridad.registrar_hook_rotacion(_al_rotar_credencial_tareas)
        _HOOK_TAREAS_REGISTRADO["activo"] = True

    def _autenticar(
        *, request: Request, x_api_key: str | None, api_key_query: str | None = None
    ) -> None:
        """Autenticación central HTTP: rate + compare_digest, fail-closed."""
        cliente = request.client
        if limitador.bloqueado(cliente):
            raise HTTPException(status_code=429, detail="Demasiados intentos.")
        credencial = seguridad.cargar_credencial(token_explicito=api_token)
        recibida = x_api_key or api_key_query or ""
        if not seguridad.verificar_credencial(recibida, credencial):
            limitador.registrar_fallo(cliente)
            raise HTTPException(
                status_code=401,
                detail="No autorizado.",
                headers={"WWW-Authenticate": 'ApiKey realm="snapcontext"'},
            )

    def _protector(
        ruta_clave: str,
        resolver: Callable[[dict | None], seguridad.DeclaracionSuperficie] | None = None,
    ):
        """Dependencia central única: authentication → capability → política.

        Se declara en el registro de cada ruta del factory; el middleware de
        cobertura deniega cualquier ruta que no lleve esta dependencia ni
        entrada en ``CAPABILITY_MAP`` (invariancia de B9.59 §7).
        ``resolver`` permite derivar la capability por cuerpo (p. ej. daemon).
        """
        decl = CAPABILITY_MAP.get(ruta_clave)
        if decl is None:
            raise RuntimeError(f"Ruta sin declaración en CAPABILITY_MAP: {ruta_clave}")

        async def _dependencia(
            request: Request,
            x_api_key: str | None = Header(default=None),
            api_key: str | None = Query(default=None),
        ) -> None:
            if not seguridad.host_valido(request.headers):
                raise HTTPException(status_code=400, detail="Host no permitido.")
            exposicion = seguridad.clasificar_exposicion(request.client, request.headers)
            if decl.requiere_auth:
                _autenticar(request=request, x_api_key=x_api_key, api_key_query=api_key)
            decl_efectiva = decl
            if resolver is not None:
                decl_efectiva = resolver(await _cuerpo_json(request))
            seguridad.exigir_politica(decl_efectiva, exposicion)

        _dependencia._proteccion_central = True  # type: ignore[attr-defined]
        return _dependencia

    def _resolver_daemon(cuerpo: dict | None) -> seguridad.DeclaracionSuperficie:
        """`estado` → READ_WORKSPACE (LAN ok); `iniciar`/`detener` →
        MANAGE_DAEMON localhost-only (B9.59 §12); cuerpo no utilizable → la
        declaración base (el endpoint responde 400/422 sin ejecutar nada)."""
        accion = ""
        if isinstance(cuerpo, dict):
            accion = str(cuerpo.get("accion") or "estado").strip().lower()
        if accion in ("iniciar", "detener"):
            return _DAEMON_GESTION
        return CAPABILITY_MAP[f"{API_PREFIJO}/daemon"]

    # Cobertura de superficies (toda ruta ∈ CAPABILITY_MAP, sin fallback) +
    # TrustedHost básico + log EXPOSING/NON-LOOPBACK-EQUIVALENT.
    app.add_middleware(
        seguridad.MiddlewareSeguridad,
        mapa=CAPABILITY_MAP,
        obtener_rutas=lambda: app.routes,
    )

    @app.get(
        f"{API_PREFIJO}/health",
        dependencies=[Depends(_protector(f"{API_PREFIJO}/health"))],
    )
    async def api_health():
        """Estado del servidor (endpoint público)."""
        sc = _importar_snapcontext()
        return {
            "estado": "ok",
            "servicio": "snapcontext",
            "version": getattr(sc, "VERSION", "?") if sc else "?",
            "docs": "/docs",
            "redoc": "/redoc",
        }

    @app.post(
        f"{API_PREFIJO}/query",
        status_code=202,
        dependencies=[Depends(_protector(f"{API_PREFIJO}/query"))],
    )
    async def api_query(cuerpo: dict = Body(...)):
        """Ejecuta una consulta (equivale a ``snapcontext "consulta"``).

        Responde 202 con un ``task_id``; el progreso se consulta en
        ``/api/v1/tasks/{task_id}``.
        """
        if not (cuerpo.get("consulta") or "").strip():
            raise HTTPException(status_code=400, detail="Falta el campo 'consulta'.")
        try:
            return _lanzar_tarea_api("query", cuerpo)
        except fs_web.ErrorFrontera as exc:
            raise HTTPException(status_code=400, detail=_error_filesystem(exc)) from None

    @app.post(
        f"{API_PREFIJO}/plan",
        status_code=202,
        dependencies=[Depends(_protector(f"{API_PREFIJO}/plan"))],
    )
    async def api_plan(cuerpo: dict = Body(...)):
        """Ejecuta un plan (equivale a ``snapcontext --plan "consulta"``)."""
        if not (cuerpo.get("consulta") or "").strip():
            raise HTTPException(status_code=400, detail="Falta el campo 'consulta'.")
        try:
            return _lanzar_tarea_api("plan", cuerpo)
        except fs_web.ErrorFrontera as exc:
            raise HTTPException(status_code=400, detail=_error_filesystem(exc)) from None

    @app.post(f"{API_PREFIJO}/chat", dependencies=[Depends(_protector(f"{API_PREFIJO}/chat"))])
    async def api_chat(cuerpo: dict = Body(...)):
        """Envía un mensaje al proveedor y devuelve la respuesta (síncrono).

        Acepta ``historial`` (lista de mensajes role/content, últimos 20)
        para mantener contexto conversacional.
        """
        sc = _importar_snapcontext()
        if sc is None:
            raise HTTPException(status_code=500, detail="snapcontext no disponible.")
        mensaje = (cuerpo.get("mensaje") or "").strip()
        if not mensaje:
            raise HTTPException(status_code=400, detail="Falta el campo 'mensaje'.")
        preferencias = sc.cargar_configuracion()
        proveedor = cuerpo.get("proveedor") or preferencias.get("provider") or sc.PROVEEDOR_DEFECTO
        historial = list(cuerpo.get("historial") or [])[-20:]
        mensajes = [*historial, {"role": "user", "content": mensaje}]
        try:
            respuesta = sc._enviar_al_proveedor(proveedor, cuerpo.get("modelo"), mensajes)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"respuesta": respuesta, "proveedor": proveedor}

    @app.get(f"{API_PREFIJO}/skills", dependencies=[Depends(_protector(f"{API_PREFIJO}/skills"))])
    async def api_skills(archivados: bool = Query(default=False)):
        """Lista las skills aprendidas por la memoria persistente."""
        sc = _importar_snapcontext()
        filas = sc._skill_listar(incluir_archivados=bool(archivados)) if sc else []
        return {"total": len(filas), "skills": filas}

    @app.post(
        f"{API_PREFIJO}/daemon",
        dependencies=[Depends(_protector(f"{API_PREFIJO}/daemon", resolver=_resolver_daemon))],
    )
    async def api_daemon(cuerpo: dict = Body(...)):
        """Gestiona el daemon: ``{"accion": "estado"|"iniciar"|"detener"}``."""
        global _DAEMON_HILO
        accion = (cuerpo.get("accion") or "estado").strip().lower()
        vivo = bool(_DAEMON_HILO and _DAEMON_HILO.is_alive())
        if accion == "estado":
            return {"accion": "estado", "activo": vivo}
        sc = _importar_snapcontext()
        if sc is None:
            raise HTTPException(status_code=500, detail="snapcontext no disponible.")
        if accion == "iniciar":
            if vivo:
                return {
                    "accion": "iniciar",
                    "activo": True,
                    "detalle": "El daemon ya estaba en ejecución.",
                }
            intervalo = cast(
                int,
                cuerpo.get("intervalo_horas") or getattr(sc, "DAEMON_INTERVALO_HORAS_DEFECTO", 6),
            )
            pausa = int(getattr(sc, "DAEMON_PAUSA_SEGUNDOS", 3600))
            _DAEMON_PARAR.clear()

            def _bucle_daemon():
                while not _DAEMON_PARAR.is_set():
                    try:
                        sc._daemon_tick(intervalo_horas=intervalo)
                    except Exception:
                        pass
                    _DAEMON_PARAR.wait(timeout=max(1, pausa))

            _DAEMON_HILO = threading.Thread(target=_bucle_daemon, daemon=True)
            _DAEMON_HILO.start()
            return {"accion": "iniciar", "activo": True, "intervalo_horas": intervalo}
        if accion == "detener":
            _DAEMON_PARAR.set()
            if _DAEMON_HILO is not None:
                _DAEMON_HILO.join(timeout=5)
            return {"accion": "detener", "activo": bool(_DAEMON_HILO and _DAEMON_HILO.is_alive())}
        raise HTTPException(
            status_code=400, detail=("Acción inválida; usa 'estado', 'iniciar' o 'detener'.")
        )

    @app.post("/webhook/telegram", dependencies=[Depends(_protector("/webhook/telegram"))])
    async def webhook_telegram(update_data: dict = Body(...)):
        """Recibe un Update de Telegram y lanza su procesamiento en segundo plano.

        - 503 si no hay ``TELEGRAM_BOT_TOKEN`` configurado.
        - 200 OK inmediato (Telegram corta si no respondemos en ~30 s); el
          pipeline corre con ``asyncio.create_task`` vía el gateway.
        """
        try:
            import telegram_gateway as tg
        except ImportError:
            raise HTTPException(
                status_code=503, detail="Gateway de Telegram no disponible (instala httpx)."
            )
        if not tg.obtener_token():
            raise HTTPException(
                status_code=503,
                detail="TELEGRAM_BOT_TOKEN no configurado. Usa "
                "`snapcontext telegram setup --token <TOKEN>`.",
            )
        await tg.handle_telegram_update(update_data)
        return {"ok": True}

    @app.post("/webhook/discord", dependencies=[Depends(_protector("/webhook/discord"))])
    async def discord_webhook(request: Request):
        """Recibe una interacción de Discord con verificación de firma Ed25519.

        - 503 si falta ``DISCORD_PUBLIC_KEY``.
        - 401 si la firma es inválida (petición falsificada).
        - PING de Discord (type 1) → ``{"type": 1}`` inmediato.
        - Comandos → respuesta diferida ``{"type": 5}`` y el pipeline corre
          en segundo plano vía el gateway (Discord exige <3 s).
        """
        try:
            import discord_gateway as dg
        except ImportError:
            raise HTTPException(
                status_code=503,
                detail="Gateway de Discord no disponible (instala httpx y cryptography).",
            )
        if not dg.obtener_public_key():
            raise HTTPException(
                status_code=503,
                detail="DISCORD_PUBLIC_KEY no configurada. Usa "
                "`snapcontext discord setup --public-key <KEY>`.",
            )
        firma = request.headers.get("X-Signature-Ed25519") or ""
        marca = request.headers.get("X-Signature-Timestamp") or ""
        cuerpo = await request.body()
        try:
            dg.verify_signature(cuerpo, firma, marca)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=f"Firma inválida: {exc}")
        interaction_data = json.loads(cuerpo.decode("utf-8") or "{}")
        respuesta = await dg.handle_discord_interaction(interaction_data)
        if respuesta is None:
            return {"ok": True}
        return respuesta

    @app.post("/webhook/github", dependencies=[Depends(_protector("/webhook/github"))])
    @app.post(
        "/api/github/webhook",
        dependencies=[Depends(_protector("/api/github/webhook"))],
    )
    async def github_webhook(request: Request):
        """Recibe un webhook de GitHub con verificación de firma HMAC SHA-256 (v6.8.0)."""
        try:
            import github_gateway as gh
        except ImportError:
            raise HTTPException(status_code=503, detail="Gateway de GitHub no disponible.")

        secreto = gh.obtener_webhook_secreto()
        cuerpo = await request.body()
        firma = (
            request.headers.get("X-Hub-Signature-256")
            or request.headers.get("X-Hub-Signature")
            or ""
        )

        # B9.61-D (D-01) — fail-CLOSED. Antes: `if secreto and not validar_firma(...)`,
        # con lo que la AUSENCIA de secreto dejaba pasar cualquier petición
        # (fail-open → webhook no autenticado → task_queue → shell). Ahora la
        # ausencia de secreto es una funcionalidad no configurada: 503.
        if not secreto:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Webhook de GitHub no configurado: falta el secreto. "
                    "Configúralo con `snapcontext github setup --secret <SECRETO>` "
                    "o GITHUB_WEBHOOK_SECRET."
                ),
            )
        if not gh.validar_firma(cuerpo, firma, secreto):
            raise HTTPException(status_code=401, detail="Firma de GitHub inválida.")

        tipo_evento = request.headers.get("X-GitHub-Event", "pull_request")
        try:
            datos = json.loads(cuerpo.decode("utf-8") or "{}")
        except Exception:
            raise HTTPException(status_code=400, detail="Cuerpo JSON inválido.")

        evento_parseado = gh.parsear_evento(datos, tipo_evento=tipo_evento)
        tarea_id = gh.procesar_evento(evento_parseado)
        return {"ok": True, "evento": tipo_evento, "tarea_id": tarea_id}

    @app.get(
        f"{API_PREFIJO}/tasks/{{task_id}}",
        dependencies=[Depends(_protector(f"{API_PREFIJO}/tasks/{{task_id}}"))],
    )
    async def api_task(task_id: str):
        """Devuelve el estado de una tarea asíncrona."""
        with _CANDADO_TAREAS_API:
            registro = _TAREAS_API.get(task_id)
            datos = dict(registro) if registro else None
        if datos is None:
            raise HTTPException(status_code=404, detail="Tarea no encontrada.")
        return datos

    @app.get("/", dependencies=[Depends(_protector("/"))])
    async def raiz():
        return FileResponse(_ESTATICO / "index.html")

    # ---- UI interactiva (v6.5.0) -----------------------------------------
    if interactiva:
        from fastapi.staticfiles import StaticFiles

        app.mount("/static", StaticFiles(directory=str(_ESTATICO)))

        @app.get("/interactive", dependencies=[Depends(_protector("/interactive"))])
        async def interactiva_ruta():
            """Página del centro de control interactivo."""
            return FileResponse(_ESTATICO / "interactive.html")

        @app.get(
            "/interactive/health",
            dependencies=[Depends(_protector("/interactive/health"))],
        )
        async def interactiva_salud():
            return {"estado": "ok", "modo": "interactivo"}

        @app.websocket("/ws/interactive")
        async def _ws_interactivo(websocket: WebSocket):
            """WebSocket del centro de control interactivo (v6.5.0).

            Frontera de autenticación/autorización ANTES de ``accept()``
            (B9.59 §5-§8 / B9.60 §6): credencial + capacidad
            ``USE_INTERACTIVE`` + Origin/Host + localhost-only M1. Difunde los
            eventos del hub (``react_step``, ``diff_conflict``,
            ``agent_status``, ``log_interactivo``) a todos los clientes y
            reenvía sus respuestas (``diff_respuesta``) al hub.
            """
            try:
                exposicion_ws = seguridad.clasificar_exposicion(websocket.client, websocket.headers)
                if exposicion_ws != "loopback":
                    seguridad.logger.warning(
                        "EXPOSING/NON-LOOPBACK-EQUIVALENT ws=/ws/interactive exposicion=%s",
                        exposicion_ws,
                    )
                _autorizar_ws(
                    websocket,
                    credencial=seguridad.cargar_credencial(token_explicito=api_token),
                    exposicion=exposicion_ws,
                    limitador=limitador,
                    capacidad="USE_INTERACTIVE",
                    requiere_loopback=True,
                )
            except Exception:
                await websocket.close(code=1008)
                return
            await websocket.accept()
            _ws_activas[websocket] = asyncio.get_running_loop()
            cola_cliente: queue.Queue[dict] = queue.Queue()
            _clientes_interactivos[id(cola_cliente)] = cola_cliente

            async def _difundir():
                while True:
                    try:
                        evento = await asyncio.to_thread(cola_cliente.get, True, 0.5)
                    except queue.Empty:
                        continue
                    try:
                        await websocket.send_json(evento)
                    except Exception:
                        return

            tarea_difusion = asyncio.create_task(_difundir())
            try:
                while True:
                    datos = await websocket.receive_text()
                    try:
                        mensaje = dict(json.loads(datos))
                    except (json.JSONDecodeError, ValueError):
                        await websocket.send_json(
                            {
                                "tipo": "log_interactivo",
                                "nivel": "error",
                                "texto": "✖ Mensaje JSON inválido.",
                            }
                        )
                        continue
                    if (
                        isinstance(mensaje.get("contenido"), str)
                        and len(mensaje["contenido"]) > 400_000
                    ):
                        mensaje["contenido"] = mensaje["contenido"][:400_000]
                    try:
                        await asyncio.to_thread(wi._recibir_mensaje, mensaje)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                tarea_difusion.cancel()
                _clientes_interactivos.pop(id(cola_cliente), None)
                _ws_activas.pop(websocket, None)

        @app.on_event("startup")
        async def _arrancar_difusor_interactivo():
            """Hilo que reparte los eventos del hub a todos los clientes."""
            nonlocal _tarea_difusor

            async def _bucle():
                cola_hub = wi.cola_eventos()
                while cola_hub is not None:
                    try:
                        evento = await asyncio.to_thread(cola_hub.get, True, 0.5)
                    except queue.Empty:
                        continue
                    except Exception:
                        return
                    for cola_cliente in list(_clientes_interactivos.values()):
                        cola_cliente.put(evento)

            _tarea_difusor = asyncio.create_task(_bucle())

    @app.get("/health")
    async def salud():
        return {"estado": "ok", "servicio": "snapcontext"}

    @app.websocket("/ws")
    async def _ws_punto(websocket: WebSocket):
        # Frontera pre-accept (B9.60 §6): auth + Origin/Host + exposición
        # ANTES de accept(); una credencial inválida cierra con 1008 sin
        # procesar ningún mensaje.
        try:
            exposicion_ws = seguridad.clasificar_exposicion(websocket.client, websocket.headers)
            if exposicion_ws != "loopback":
                seguridad.logger.warning(
                    "EXPOSING/NON-LOOPBACK-EQUIVALENT ws=/ws exposicion=%s",
                    exposicion_ws,
                )
            _autorizar_ws(
                websocket,
                credencial=seguridad.cargar_credencial(token_explicito=api_token),
                exposicion=exposicion_ws,
                limitador=limitador,
            )
        except Exception:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        _ws_activas[websocket] = asyncio.get_running_loop()
        cola: queue.Queue[dict] = queue.Queue()
        try:
            while True:
                datos = await websocket.receive_text()
                try:
                    mensaje = json.loads(datos)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"tipo": "log", "nivel": "error", "texto": "✖ Mensaje JSON inválido."}
                    )
                    continue
                if not isinstance(mensaje, dict):
                    await websocket.send_json(
                        {"tipo": "log", "nivel": "error", "texto": "✖ Mensaje JSON inválido."}
                    )
                    continue
                tipo = (mensaje.get("tipo") or "").strip()
                if not tipo and (mensaje.get("consulta") or "").strip():
                    tipo = "tarea"  # compat. con el viejo protocolo
                if not tipo:
                    continue

                # Dispatcher central cerrado: clave → (capability, handler).
                # Sin mapeo (tipo o sub-acción desconocida) → denegación
                # explícita; sin fallback a capability genérica (B9.60 §6/§7).
                clave = _clave_despacho_ws(tipo, mensaje)
                decl = CAPABILITY_MAP.get(clave)
                manejador = _MANEJADORES_WS.get(clave)
                if decl is None or manejador is None:
                    await websocket.send_json(
                        {
                            "tipo": "error",
                            "nivel": "error",
                            "texto": "✖ Acción no permitida.",
                        }
                    )
                    continue
                if decl.capacidad is not None and decl.capacidad not in seguridad.CAPABILIDADES:
                    await websocket.send_json(
                        {
                            "tipo": "error",
                            "nivel": "error",
                            "texto": "✖ Acción no permitida.",
                        }
                    )
                    continue
                if exposicion_ws == "internet" or (
                    decl.solo_loopback and exposicion_ws != "loopback"
                ):
                    await websocket.send_json(
                        {
                            "tipo": "error",
                            "nivel": "error",
                            "texto": "✖ Acción no disponible en esta exposición.",
                        }
                    )
                    continue
                await manejador(websocket, mensaje, cola)
        except Exception:
            pass  # conexión cerrada o mensaje inválido
        finally:
            _ws_activas.pop(websocket, None)
            sc = _importar_snapcontext()
            if sc is not None:
                sc.fijar_evento_callback(None)

    _validar_superficies(app)
    return app


async def _reenviar_hasta(websocket: WebSocket, cola, hilo: threading.Thread):
    """Reenvía los eventos de la cola al WebSocket hasta que ``hilo`` termina."""
    while hilo.is_alive() or not cola.empty():
        while not cola.empty():
            try:
                await websocket.send_json(cola.get_nowait())
            except queue.Empty:
                break
        await asyncio.sleep(0.03)
    while not cola.empty():
        try:
            await websocket.send_json(cola.get_nowait())
        except queue.Empty:
            break


def _importar_snapcontext():
    """Devuelve el módulo snapcontext (import diferido) o None si no se puede."""
    try:
        import snapcontext

        return snapcontext
    except Exception:
        return None


# --------------------------------------------------------------------------
# Tarea / pipeline clásico del orquestador
# --------------------------------------------------------------------------
def _ejecutar_tarea(args, cola) -> None:
    """Ejecuta el orquestador en un hilo, enviando sus eventos a la cola.

    Emite además un evento ``inicio`` y enriquece cada evento reenviado con el
    tiempo transcurrido (clave ``tiempo``).
    """
    t0 = time.monotonic()
    cola.put({"tipo": "inicio"})

    def _on_evento(dicto: dict) -> None:
        evento = dict(dicto)
        evento.setdefault("tiempo", round(time.monotonic() - t0, 1))
        cola.put(evento)

    try:
        from orquestador import Orquestador

        Orquestador(evento_callback=_on_evento).ejecutar_flujo(args)
    except Exception as exc:
        cola.put({"tipo": "log", "nivel": "error", "texto": f"✖ {exc}"})
        cola.put(
            {
                "tipo": "final",
                "ok": False,
                "error": str(exc),
                "tiempo": round(time.monotonic() - t0, 1),
            }
        )
    finally:
        sc = _importar_snapcontext()
        if sc is not None:
            sc.fijar_evento_callback(None)


def _construir_args(mensaje: dict):
    """Construye un argparse.Namespace válido a partir del JSON de la UI.

    B9.61-B: ``directorio`` ya no viaja crudo al pipeline. Se resuelve por la
    frontera y lo que se pasa a ``--directorio`` es la ruta ya confinada (o se
    lanza ``ErrorFrontera``, que el dispatcher traduce a denegación), de modo
    que ``flujo_principal``/el orquestador nunca reciben una ruta externa sin
    validar.
    """
    import snapcontext as sc

    consulta = (mensaje.get("consulta") or "").strip()
    argv: list[str] = [consulta]

    if mensaje.get("directorio"):
        base = fs_web.frontera_activa().base_operacion(mensaje["directorio"])
        argv += ["--directorio", str(base)]
    if mensaje.get("local"):
        argv.append("--local")
    if mensaje.get("vista_previa"):
        argv.append("--vista-previa")
    if mensaje.get("max_archivos"):
        argv += ["--max-archivos", str(int(mensaje["max_archivos"]))]
    if mensaje.get("carpetas"):
        argv.append("--carpetas")
        argv += [str(c) for c in mensaje["carpetas"]]
    if mensaje.get("test_loop"):
        argv.append("--test-loop")
        if mensaje.get("comando_test"):
            argv += ["--comando-test", str(mensaje["comando_test"])]
        if mensaje.get("max_iteraciones"):
            argv += ["--max-iteraciones", str(int(mensaje["max_iteraciones"]))]

    return sc.crear_parser().parse_args(argv)


# --------------------------------------------------------------------------
# Acciones rápidas (Fix / Review / Plan / Run / Search / Explorar) — v1.2.0
# --------------------------------------------------------------------------
def _ejecutar_accion(mensaje: dict, cola) -> None:  # noqa: C901  (bloque H1b preexistente)
    """Ejecuta una acción rápida del panel web, emitiendo eventos a la cola."""
    import snapcontext as sc

    t0 = time.monotonic()
    accion = (mensaje.get("accion") or "").strip()
    consulta = (mensaje.get("consulta") or "").strip()
    archivo = (mensaje.get("archivo") or "").strip()
    directorio = (mensaje.get("directorio") or _DIRECTORIO_DEFECTO).strip()
    comando = (mensaje.get("comando") or "").strip()

    def ev(dicto: dict) -> None:
        evento = dict(dicto)
        evento.setdefault("tiempo", round(time.monotonic() - t0, 1))
        cola.put(evento)

    # B9.61-B: el `directorio` se valida ANTES de cualquier operación. Si la
    # frontera lo deniega, ninguna rama (run con cwd, search, explorar,
    # dependencias, fix/review/plan) llega a tocar el filesystem.
    try:
        base = fs_web.frontera_activa().resolver_cwd(directorio)
    except fs_web.ErrorFrontera as exc:
        ev({"tipo": "log", "nivel": "error", "texto": f"✖ {_error_filesystem(exc)}"})
        ev(
            {
                "tipo": "accion_ejecutada",
                "accion": accion,
                "ok": False,
                "error": _error_filesystem(exc),
            }
        )
        return

    try:
        if accion == "run":
            objetivo = comando or consulta
            if not objetivo:
                ev(
                    {
                        "tipo": "log",
                        "nivel": "error",
                        "texto": "✖ La acción Run necesita un comando o consulta.",
                    }
                )
                ev({"tipo": "accion_ejecutada", "accion": accion, "ok": False})
                return
            # B9.61-C (OD-1): política de EJECUCIÓN REMOTA, evaluada antes de
            # C2. La allowlist remota de M1 está vacía ⇒ DENY ALL. La
            # identidad se calcula sobre argv estructurado; nunca se ejecuta
            # un string ni se usa `shell=True` en este camino.
            argv = _argv_estructurado(objetivo)
            permitido, motivo, _identidad = remota_web.autorizar_comando_remoto(argv)
            if not permitido:
                ev(
                    {
                        "tipo": "log",
                        "nivel": "error",
                        "texto": f"✖ Comando rechazado por seguridad remota ({motivo}).",
                    }
                )
                ev(
                    {
                        "tipo": "accion_ejecutada",
                        "accion": accion,
                        "ok": False,
                        "error": motivo,
                    }
                )
                return
            # H1b (C2): clasificar ANTES de ejecutar. Solo nivel "directo"
            # corre en el host; lo demás se rechaza (nunca ejecución directa
            # sin clasificar, nunca sandbox silencioso desde la red).
            permitido, motivo = _ws_clasificar_run(objetivo)
            if not permitido:
                ev(
                    {
                        "tipo": "log",
                        "nivel": "error",
                        "texto": f"✖ Comando rechazado por seguridad ({motivo}).",
                    }
                )
                ev({"tipo": "accion_ejecutada", "accion": accion, "ok": False, "error": motivo})
                return
            # B9.61-B: `cwd` = directorio ya validado por la frontera.
            codigo, stdout, stderr = sc._ejecutar_comando(objetivo, str(base), timeout=180)
            ok = codigo == 0
            resumen = (stdout or stderr or f"código {codigo}")[:300]
            ev({"tipo": "log", "nivel": "info", "texto": resumen})
            ev({"tipo": "accion_ejecutada", "accion": accion, "ok": ok, "resumen": resumen})
        elif accion == "search":
            resultados = _semantica_web(mensaje)
            ev(
                {
                    "tipo": "accion_ejecutada",
                    "accion": accion,
                    "ok": True,
                    "resumen": f"{len(resultados)} resultado(s) semántico(s).",
                }
            )
            if resultados:
                ev({"tipo": "semanticos", "resultados": resultados})
        elif accion == "explorar":
            tema = consulta or archivo
            mensaje2 = dict(mensaje)
            mensaje2["tema"] = tema or "."
            lineas = _explorar_web(mensaje2)
            ev(
                {
                    "tipo": "accion_ejecutada",
                    "accion": accion,
                    "ok": True,
                    "resumen": f"{len(lineas)} coincidencia(s).",
                }
            )
            if lineas:
                ev({"tipo": "exploracion", "lineas": lineas[:50]})
        elif accion == "asesor":
            # v3.5.0: asesor de código proactivo → panel de sugerencias.
            sugerencias = sc._asesor_analizar(mensaje.get("directorio") or _DIRECTORIO_DEFECTO)
            ev({"tipo": "asesor", "sugerencias": sugerencias})
            ev(
                {
                    "tipo": "accion_ejecutada",
                    "accion": accion,
                    "ok": True,
                    "resumen": f"{len(sugerencias)} sugerencia(s) de mejora.",
                }
            )
        elif accion in ("plugins", "plugin_install", "plugin_remove"):
            # v4.0.0: ecosistema de plugins → panel de plugins.
            try:
                if accion == "plugin_install":
                    origen_texto = (
                        mensaje.get("origen") or mensaje.get("consulta") or ""
                    ).strip()
                    codigo = 1
                    if origen_texto:
                        # OD-7: clasificacion explicita (LocalPath | RemoteURL).
                        origen = _origen_plugin_por_frontera(origen_texto)
                        if isinstance(origen, RemoteURL):
                            # OD-6 (M1): la instalacion de plugins REMOTOS se
                            # deniega. No se descarga ni se instala codigo
                            # remoto; queda como operacion local del operador.
                            raise fs_web.ErrorFrontera(
                                "not_allowed",
                                "instalación remota de plugins deshabilitada",
                            )
                        codigo = sc._plugin_instalar(
                            origen.como_texto(), auto=True
                        )
                elif accion == "plugin_remove":
                    nombre = (mensaje.get("nombre") or mensaje.get("consulta") or "").strip()
                    # Desde la web no hay TTY: se omite la confirmación.
                    codigo = sc._plugin_remove(nombre, confirmar=False) if nombre else 1
                else:
                    codigo = 0
                plugins = list(sc._plugins_instalados().values())
                for p in plugins:
                    p.pop("ruta", None)
                ev({"tipo": "plugins", "plugins": plugins})
                resumen = (
                    {0: "OK"}.get(codigo, "fallo en la operación del plugin")
                    if accion != "plugins"
                    else f"{len(plugins)} plugin(s)."
                )
                ev(
                    {
                        "tipo": "accion_ejecutada",
                        "accion": accion,
                        "ok": codigo == 0,
                        "resumen": resumen,
                    }
                )
            except Exception as exc:
                ev({"tipo": "accion_ejecutada", "accion": accion, "ok": False, "error": str(exc)})
        else:
            # fix / review / plan → pipeline o planificador de snapcontext.
            sc.fijar_evento_callback(ev)
            try:
                args = _construir_args_accion(accion, consulta, str(base))
                if accion == "plan":
                    codigo = sc._ejecutar_planificador(args)
                else:
                    codigo = sc.flujo_principal(args)
                ev(
                    {
                        "tipo": "accion_ejecutada",
                        "accion": accion,
                        "ok": codigo == 0,
                        "resumen": f"{accion} finalizado.",
                    }
                )
            finally:
                sc.fijar_evento_callback(None)
    except Exception as exc:
        ev({"tipo": "log", "nivel": "error", "texto": f"✖ {exc}"})
        ev({"tipo": "accion_ejecutada", "accion": accion, "ok": False, "error": str(exc)})


def _construir_args_accion(accion: str, consulta: str, directorio: str):
    """Construye argparse.Namespace para una acción rápida de pipeline.

    B9.61-B: igual que ``_construir_args``, ``directorio`` pasa por la frontera
    antes de convertirse en ``--directorio`` del pipeline.
    """
    import snapcontext as sc

    if accion == "fix":
        argv = sc._preparar_argv_aliases(["fix", *shlex.split(consulta)])
    elif accion == "review":
        argv = sc._preparar_argv_aliases(["review", *shlex.split(consulta)])
    elif accion == "plan":
        argv = ["--plan", consulta or ""]
    else:
        argv = [consulta or ""]
    if directorio and directorio != _DIRECTORIO_DEFECTO:
        base = fs_web.frontera_activa().base_operacion(directorio)
        argv += ["--directorio", str(base)]

    args = sc.crear_parser().parse_args(argv)
    # En web nunca hay confirmación interactiva ni menú por paso.
    args.auto = True
    args.no_confirmar = True
    args.depurar = False
    return args


# --------------------------------------------------------------------------
# Editor web (leer / guardar archivos) — v1.2.0
# --------------------------------------------------------------------------
def _autorizar_ws(
    websocket: WebSocket,
    *,
    credencial: str | None,
    exposicion: str,
    limitador: seguridad.LimitadorFallos,
    capacidad: str | None = None,
    requiere_loopback: bool = False,
) -> None:
    """Autorización pre-accept de un WebSocket con la credencial del gateway.

    Adaptador WS de la misma política de credencial que HTTP (sin duplicar
    mecanismos): la credencial y la política llegan como argumentos
    explícitos desde ``crear_app`` (nunca un global tipo ``_API_TOKEN``).

    Comprobaciones, en orden (todas → ``WebSocketDisconnect(1008)``, cerrado
    ANTES de ``accept()`` por el caller, B9.60 §6/§13):

    1. Host conforme a TrustedHost básico.
    2. Origin según las reglas 1-3 de B9.60 §13 (ausente → solo loopback).
    3. Exposición: ``internet`` → DENY (matriz B9.59 §12); ``requiere_loopback``
       exige exposición loopback (localhost-only, p. ej. USE_INTERACTIVE).
    4. Rate básico de fallos (429 equivalente → 1008).
    5. Credencial: ``?api_key=`` (solo compat WS, con warning) o header
       ``X-API-Key``; comparación en tiempo constante; sin credencial
       vigente → DENY (fail-closed, sin el viejo «sin clave → abrir»).

    ``capacidad`` (si se indica) debe pertenecer al conjunto cerrado de las 9;
    capacidad desconocida → DENY.
    """
    from starlette.websockets import WebSocketDisconnect

    def _denegar(motivo: str) -> None:
        seguridad.logger.warning("WS rechazado pre-accept: %s", motivo)
        raise WebSocketDisconnect(code=1008, reason="No autorizado.")

    cabeceras = websocket.headers
    if not seguridad.host_valido(cabeceras):
        _denegar("Host no permitido.")
    if not seguridad.origin_admitido(cabeceras.get("origin"), cabeceras.get("host"), exposicion):
        _denegar("Origen no permitido.")
    if exposicion == "internet":
        _denegar("Exposición no permitida.")
    if requiere_loopback and exposicion != "loopback":
        _denegar("Solo loopback.")
    if capacidad is not None and capacidad not in seguridad.CAPABILIDADES:
        _denegar("Capability desconocida.")
    cliente = websocket.client
    if limitador.bloqueado(cliente):
        _denegar("Demasiados intentos.")
    if "api_key" in websocket.query_params:
        seguridad.logger.warning(
            "Uso de ?api_key= en WS: compatibilidad limitada (B9.59 §8(4)); "
            "prefiere la cabecera X-API-Key."
        )
    recibida = websocket.query_params.get("api_key") or cabeceras.get("x-api-key") or ""
    if not seguridad.verificar_credencial(recibida, credencial):
        limitador.registrar_fallo(cliente)
        _denegar("Credencial inválida o ausente.")


def _argv_estructurado(objetivo: str) -> list[str]:
    """Convierte el comando del cliente en argv estructurado (OD-2).

    No devuelve nunca un string opaco a la política remota: si el texto no es
    interpretable como lista de tokens, produce una lista vacía (que la
    política deniega). El parseo NO es una frontera de seguridad: la decisión
    la toma ``web.remota`` sobre la identidad resuelta.
    """
    import shlex as _shlex

    try:
        partes = _shlex.split(str(objetivo or ""))
    except ValueError:
        return []
    return [p for p in partes if p]


def _ws_clasificar_run(objetivo: str) -> tuple[bool, str]:
    """Clasifica un comando de la acción WS ``run`` con C2 (H1b).

    Devuelve ``(permitido, motivo)``. Solo ``nivel == "directo"`` se
    ejecuta en el host; cualquier otro nivel se rechaza (nunca ejecución
    directa sin clasificar, nunca sandbox silencioso desde la red: sin
    Docker disponible el comando NO corre, se rechaza con el motivo).
    """
    from sandbox_utils import clasificar_comando

    nivel, motivo = clasificar_comando(objetivo)
    if nivel == "directo":
        return True, motivo
    return False, motivo


def _resolver_camino(ruta: str, directorio: str) -> Path:
    """Resuelve ``ruta`` contra ``directorio`` a través de la frontera (B9.61-B).

    Ya NO es una comprobación propia: delega íntegramente en la frontera
    central (``web.filesystem.Frontera.resolver_ruta``), que aplica
    ``resolve → containment semántico → política``. La contención se hace
    sobre la ruta resuelta con ``relative_to``/``parents`` (nunca con
    ``str.startswith``).

    Lanza :class:`web.filesystem.ErrorFrontera` (subclase de ``Exception``,
    **no** de ``ValueError``) con código estable cuando la ruta se rechaza.
    """
    return fs_web.frontera_activa().resolver_ruta_en(ruta, directorio)


def _resolver_directorio_web(mensaje: dict) -> Path:
    """Resuelve el ``directorio`` de un mensaje por la frontera (B9.61-B).

    Es el punto único para *propagar* ``directorio`` hacia cualquier
    operación filesystem (dependencias, semántica, exploración, ``cwd`` de
    subproceso, pipeline). Nunca devuelve una ruta sin validar.
    """
    return fs_web.frontera_activa().base_operacion(mensaje.get("directorio"))


@dataclass(frozen=True)
class LocalPath:
    """Referencia de plugin LOCAL: una ruta ya canónica bajo el workspace.

    La semántica la fija el consumidor, no una heuristica de forma (OD-7).
    B9.61-C elimina la devolucion de texto crudo ambiguo (R3-01).
    """

    ruta: Path

    @property
    def clase(self) -> str:
        return "local"

    def como_texto(self) -> str:
        return str(self.ruta)


@dataclass(frozen=True)
class RemoteURL:
    """Referencia de plugin REMOTO: una URL https absoluta (OD-7)."""

    url: str

    @property
    def clase(self) -> str:
        return "remoto"

    def como_texto(self) -> str:
        return self.url


#: Único esquema remoto soportado por el sistema en esta fase (C-R1-02).
#: `http`, `ftp`, `file` o cualquier esquema arbitrario NO son `RemoteURL`: se
#: deniegan explícitamente y nunca se degradan a `LocalPath`.
ESQUEMAS_REMOTOS_SOPORTADOS: frozenset[str] = frozenset({"https"})


def _prefijo_esquema(texto: str) -> str | None:
    """Esquema explícito de la forma ``esquema://`` (minúsculas) o ``None``.

    No infiere nada por forma: ``user/repo``, ``./a/b`` o ``plugin.zip`` no
    tienen esquema y se tratan como rutas locales (R3-01 cerrado).
    """
    marca = texto.find("://")
    if marca <= 0:
        return None
    return texto[:marca].lower()


def _origen_plugin_por_frontera(origen: str) -> LocalPath | RemoteURL:
    """Clasifica y valida el ``origen`` de ``plugin_install`` (OD-6/OD-7).

    Devuelve **siempre** una representacion semantica explicita
    (:class:`LocalPath` o :class:`RemoteURL`) o lanza
    :class:`web.filesystem.ErrorFrontera`. Nunca devuelve un ``str`` que un
    consumidor pueda reinterpretar como ruta local (R3-01 resuelto: la rama
    ``return texto`` desaparece por construccion).

    - ``https://…`` → :class:`RemoteURL` (unico esquema remoto soportado).
    - Cualquier otro esquema (``http``, ``ftp``, ``file``, ``a``, ``custom``…)
      → ``ErrorFrontera``: es un origen remoto **no soportado**, nunca una ruta
      local (C-R1-02).
    - Cualquier otra forma → se canonicaliza **contra la raiz del
      workspace** y se confina; fuera de la raiz → ``outside_workspace``.
    - En M1 la instalacion remota se deniega en el dispatcher (OD-6), no aqui:
      esta funcion solo clasifica y confina.
    """
    texto = (origen or "").strip()
    if not texto:
        raise fs_web.ErrorFrontera("invalid_path", "Origen no válido.")
    esquema = _prefijo_esquema(texto)
    if esquema is not None:
        if esquema in ESQUEMAS_REMOTOS_SOPORTADOS:
            return RemoteURL(url=texto)
        # Esquema explicito no soportado: DENY. No se degrada a LocalPath
        # (evitaría que `file://` se leyera como ruta local arbitraria).
        raise fs_web.ErrorFrontera("unsupported_origin", "Origen remoto no soportado.")

    base = fs_web.frontera_activa().raiz_efectiva()
    candidato = Path(texto).expanduser()
    if not candidato.is_absolute():
        candidato = base / candidato
    try:
        real = Path(os.path.realpath(candidato))
    except (OSError, ValueError):
        raise fs_web.ErrorFrontera("invalid_path", "Origen no válido.") from None

    # Toda ruta local debe quedar dentro de la raiz del workspace.
    if real != base and base not in real.parents:
        raise fs_web.ErrorFrontera("outside_workspace", "Origen fuera del workspace.")
    return LocalPath(ruta=real)


def _es_origen_plugin_remoto(texto: str) -> bool:
    """``True`` SOLO si el origen es un ``RemoteURL`` realmente soportado.

    C-R1-02: el único formato remoto soportado es ``https://…``. ``http://``,
    ``ftp://``, ``file://`` o cualquier esquema arbitrario tienen semántica
    remota pero **no soportada**: se deniegan en
    :func:`_origen_plugin_por_frontera` (fail-closed) y aquí devuelven ``False``
    porque no son un ``RemoteURL`` válido.

    El resto se trata como ruta local y pasa por la frontera: no se infiere el
    caracter remoto del numero de segmentos, las extensiones o la forma
    ``user/repo`` (R3-01 no se reabre).
    """
    return _prefijo_esquema(texto or "") in ESQUEMAS_REMOTOS_SOPORTADOS


def _error_filesystem(exc: Exception) -> str:
    """Mensaje público y estable para un error de la frontera (sin fuga)."""
    if isinstance(exc, fs_web.ErrorFrontera):
        return exc.mensaje
    return "Ruta no permitida."


def _leer_archivo_web(mensaje: dict) -> dict:
    """Lee un archivo para el editor web. Devuelve contenido + lenguaje.

    B9.61-B: la lectura pasa por la frontera central (``Frontera.leer``), que
    resuelve, confina y abre con ``O_NOFOLLOW``. No hay lectura arbitraria:
    ``/etc/passwd``, ``~/.ssh/...`` o un symlink que apunte fuera del
    workspace producen un ``error`` controlado, sin contenido y sin revelar
    la ruta real.
    """
    import snapcontext as sc

    ruta = (mensaje.get("ruta") or "").strip()
    if not ruta:
        return {
            "ruta": ruta,
            "contenido": None,
            "lenguaje": None,
            "error": "Falta la ruta del archivo.",
        }
    try:
        contenido = fs_web.frontera_activa().leer(ruta, mensaje.get("directorio"))
    except fs_web.ErrorFrontera as exc:
        return {
            "ruta": ruta,
            "contenido": None,
            "lenguaje": None,
            "error": _error_filesystem(exc),
        }
    return {
        "ruta": ruta,
        "contenido": contenido,
        "lenguaje": sc._comando_para_monaco(ruta.replace("\\", "/").split("/")[-1]),
    }


def _guardar_archivo_web(mensaje: dict) -> dict:
    """Guarda el contenido editado vía C3. Devuelve ok/error.

    B9.61-B: la cadena es ``user path → frontera (resolve + containment) →
    C3 (utils.escribir_archivo_seguro)``. C3 **no** se sustituye ni se
    debilita: sigue con ``O_NOFOLLOW``, ``O_EXCL`` en creación, verificación
    de padre seguro y control de overwrite. La frontera solo decide qué ruta
    relativa (ya resuelta y contenida) se le entrega.
    """
    ruta = (mensaje.get("ruta") or "").strip()
    contenido = mensaje.get("contenido") or ""
    if not ruta:
        return {"ruta": ruta, "ok": False, "error": "Falta la ruta."}
    try:
        camino = fs_web.frontera_activa().escribir(
            ruta,
            contenido if isinstance(contenido, str) else "",
            mensaje.get("directorio"),
            datos_binarios=None if isinstance(contenido, str) else bytes(contenido),
        )
        return {"ruta": str(camino), "ok": True}
    except fs_web.ErrorFrontera as exc:
        return {"ruta": ruta, "ok": False, "error": _error_filesystem(exc)}
    except (RutaInseguraError, ValueError):
        return {"ruta": ruta, "ok": False, "error": "Ruta no permitida."}
    except OSError:
        return {"ruta": ruta, "ok": False, "error": "No se pudo escribir el archivo."}


# --------------------------------------------------------------------------
# Dependencias / búsqueda / exploración — v1.2.0
# --------------------------------------------------------------------------
def _dependencias_web(mensaje: dict) -> dict:
    """Construye el grafo de dependencias del proyecto para la UI.

    B9.61-B: ``directorio`` **deja de propagarse crudo**. Se resuelve por la
    frontera y lo que llega a ``sc._grafo_dependencias`` es un ``Path`` ya
    confinado; si la frontera deniega, se devuelve un error controlado sin
    tocar el filesystem.
    """
    import snapcontext as sc

    ruta_cli = (mensaje.get("ruta") or "").strip()
    try:
        base = _resolver_directorio_web(mensaje)
    except fs_web.ErrorFrontera as exc:
        return {"nodos": [], "enlaces": [], "ruta": ruta_cli, "error": _error_filesystem(exc)}
    try:
        grafo = sc._grafo_dependencias(str(base))
        grafo["ruta"] = ruta_cli
        return dict(grafo)
    except Exception:
        return {"nodos": [], "enlaces": [], "ruta": ruta_cli, "error": "No se pudo completar."}


def _semantica_web(mensaje: dict) -> list:
    """Búsqueda semántica (embeddings). Devuelve [] si no está disponible.

    B9.61-B: ``directorio`` se valida por la frontera antes de llegar al
    índice vectorial; denegado → ``[]`` sin acceso al filesystem.
    """
    import snapcontext as sc

    consulta = (mensaje.get("consulta") or "").strip()
    if not consulta:
        return []
    try:
        base = _resolver_directorio_web(mensaje)
    except fs_web.ErrorFrontera as exc:
        seguridad.logger.warning("FILESYSTEM/DENY operacion=semantica %s", exc.codigo)
        return []
    if not sc._embeddings_disponibles():
        return []
    try:
        return sc._buscar_semanticamente(  # type: ignore[no-any-return]
            consulta, str(base), max_resultados=20
        )
    except Exception:
        return []


def _explorar_web(mensaje: dict) -> list:
    """Exploración por código (rg/grep/findstr). Devuelve lista de líneas.

    B9.61-B: la búsqueda exploratoria (que puede recorrer el árbol entero) solo
    se lanza sobre un directorio ya validado y confinado por la frontera.
    """
    import snapcontext as sc

    tema = (mensaje.get("tema") or "").strip()
    try:
        base = _resolver_directorio_web(mensaje)
    except fs_web.ErrorFrontera as exc:
        seguridad.logger.warning("FILESYSTEM/DENY operacion=explorar %s", exc.codigo)
        return []
    try:
        return sc._buscar_en_codigo(tema, str(base))  # type: ignore[no-any-return]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Dispatcher central de /ws: registro cerrado clave → handler (B9.60 §6)
# ---------------------------------------------------------------------------
# Biccionario exacto con las claves "WS /ws:..." de CAPABILITY_MAP (validado
# en _validar_superficies al crear la app): una acción nueva sin capability o
# una capability sin handler impide arrancar, y en runtime se deniega.
async def _ws_msg_tarea(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    """Ejecuta el orquestador en un hilo y reenvía sus eventos.

    B9.61-B: la construcción de args (que valida ``directorio`` por la frontera)
    ocurre en el dispatcher, no en el hilo: una denegación produce un error
    controlado sin lanzar nunca el orquestador.
    """
    try:
        args = _construir_args(mensaje)
    except fs_web.ErrorFrontera as exc:
        await websocket.send_json(
            {
                "tipo": "log",
                "nivel": "error",
                "texto": f"✖ {_error_filesystem(exc)}",
            }
        )
        await websocket.send_json({"tipo": "final", "ok": False, "error": _error_filesystem(exc)})
        return
    hilo = threading.Thread(target=_ejecutar_tarea, args=(args, cola), daemon=True)
    hilo.start()
    await _reenviar_hasta(websocket, cola, hilo)


async def _ws_msg_accion(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    """Ejecuta una acción rápida en un hilo (capability según sub-acción)."""
    hilo = threading.Thread(target=_ejecutar_accion, args=(mensaje, cola), daemon=True)
    hilo.start()
    await _reenviar_hasta(websocket, cola, hilo)


async def _ws_msg_leer_archivo(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    await websocket.send_json({"tipo": "archivo_seleccionado", **_leer_archivo_web(mensaje)})


async def _ws_msg_guardar_archivo(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    await websocket.send_json({"tipo": "archivo_guardado", **_guardar_archivo_web(mensaje)})


async def _ws_msg_dependencias(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    await websocket.send_json({"tipo": "dependencias_actualizadas", **_dependencias_web(mensaje)})


async def _ws_msg_semantica(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    await websocket.send_json({"tipo": "semanticos", "resultados": _semantica_web(mensaje)})


async def _ws_msg_explorar(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    await websocket.send_json({"tipo": "exploracion", "lineas": _explorar_web(mensaje)})


async def _ws_msg_ping(websocket: WebSocket, mensaje: dict, cola: queue.Queue) -> None:
    await websocket.send_json({"tipo": "pong"})


_MANEJADORES_WS: dict[str, Callable[..., Any]] = {
    "WS /ws:tarea": _ws_msg_tarea,
    "WS /ws:accion:run": _ws_msg_accion,
    "WS /ws:accion:fix": _ws_msg_accion,
    "WS /ws:accion:review": _ws_msg_accion,
    "WS /ws:accion:plan": _ws_msg_accion,
    "WS /ws:accion:search": _ws_msg_accion,
    "WS /ws:accion:explorar": _ws_msg_accion,
    "WS /ws:accion:asesor": _ws_msg_accion,
    "WS /ws:accion:plugins": _ws_msg_accion,
    "WS /ws:accion:plugin_install": _ws_msg_accion,
    "WS /ws:accion:plugin_remove": _ws_msg_accion,
    "WS /ws:leer_archivo": _ws_msg_leer_archivo,
    "WS /ws:guardar_archivo": _ws_msg_guardar_archivo,
    "WS /ws:dependencias": _ws_msg_dependencias,
    "WS /ws:semantica": _ws_msg_semantica,
    "WS /ws:explorar": _ws_msg_explorar,
    "WS /ws:ping": _ws_msg_ping,
}


def _validar_superficies(app: FastAPI) -> None:
    """Validación de arranque: cobertura total y cierre del mapa (B9.60 §7).

    - Toda ruta registrada tiene entrada en ``CAPABILITY_MAP`` (si no, la app
      no arranca: una ruta nueva sin capability no es ejecutable).
    - Toda capacidad del mapa pertenece a las 9 cerradas.
    - Toda ruta autenticada HTTP lleva la dependencia central.
    - Biccionario exacto entre claves WS de mensajes del mapa y el registro
      de handlers (sin acciones «sueltas» y sin handlers sin capacidad).
    """
    claves_ws_mensajes = {clave for clave in CAPABILITY_MAP if clave.startswith("WS /ws:")}
    if claves_ws_mensajes != set(_MANEJADORES_WS):
        raise RuntimeError(
            "CAPABILITY_MAP y _MANEJADORES_WS deben coincidir exactamente "
            f"(solo mapa: {sorted(claves_ws_mensajes - set(_MANEJADORES_WS))}; "
            f"solo handlers: {sorted(set(_MANEJADORES_WS) - claves_ws_mensajes)})."
        )
    for clave, decl in CAPABILITY_MAP.items():
        if decl.capacidad is not None and decl.capacidad not in seguridad.CAPABILIDADES:
            raise RuntimeError(f"Capability desconocida en CAPABILITY_MAP: {clave}")
    for ruta in app.routes:
        clave = seguridad.clave_de_ruta(ruta)
        if clave not in CAPABILITY_MAP:
            raise RuntimeError(f"Ruta registrada sin declaración en CAPABILITY_MAP: {clave}")
        decl = CAPABILITY_MAP[clave]
        es_ws = clave.startswith("WS ")
        if decl.requiere_auth and not es_ws and not seguridad.tiene_proteccion_central(ruta):
            raise RuntimeError(f"Ruta autenticada fuera del enforcement central: {clave}")


def arrancar_servidor(puerto: int = 8000, interactiva: bool = False) -> None:
    """Arranca uvicorn con la app FastAPI (bloquea hasta detenerse).

    ``interactiva=True`` (v6.5.0) añade el centro de control interactivo
    (``/interactive`` + ``/ws/interactive``).
    """
    import uvicorn

    uvicorn.run(
        crear_app(interactiva=interactiva),
        host="127.0.0.1",
        port=int(puerto),
        log_level="warning",
    )


def arrancar_api(
    puerto: int = 8001,
    host: str = "127.0.0.1",
    token: str | None = None,
    *,
    workspace_root: str | None = None,
) -> None:
    """Arranca la API pública v3.6.0 (bloquea hasta detenerse).

    ``token`` fija la API key; si es ``None``, se usa la de config.json o la
    override de entorno (la primera ejecución en loopback la genera y la
    entrega una sola vez por stdout). Un bind no-loopback sin credencial
    válida (≥32, 0600) aborta el arranque (B9.59 §9 / B9.60 §4).

    ``workspace_root`` (``--workspace-root``) fija la raíz que confina toda
    operación de filesystem del gateway; fuera de loopback es obligatoria y una
    raíz inválida aborta el arranque (B9.59 §4 / B9.60 §9 / B9.61-B).
    """
    import uvicorn

    host_efectivo = host or "127.0.0.1"
    try:
        app = crear_app(api_token=token, host=host_efectivo, workspace_root=workspace_root)
    except RuntimeError as exc:
        print(f"✖ {exc}")
        raise SystemExit(1) from None
    uvicorn.run(
        app,
        host=host_efectivo,
        port=int(puerto),
        log_level="warning",
    )


__all__ = ["arrancar_api", "arrancar_servidor", "crear_app"]
