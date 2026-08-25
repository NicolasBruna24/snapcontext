#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
import queue
import shlex
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, \
    WebSocket
from fastapi.responses import FileResponse

_ESTATICO = Path(getattr(sys, "_MEIPASS",
                         Path(__file__).resolve().parent.parent)) / "web" / "static"
# En el ejecutable PyInstaller (v1.5.0), ``web/static`` va a ``_MEIPASS``;
# en desarrollo, ``__file__`` es ``web/app.py`` y el estático queda al lado.
# ``directorio`` por defecto si la UI no lo indica (directorio de trabajo).
_DIRECTORIO_DEFECTO = "."


# --------------------------------------------------------------------------
# API pública (v3.6.0): estado de tareas asíncronas y control del daemon.
# --------------------------------------------------------------------------
_TAREAS_API: Dict[str, dict] = {}
_CANDADO_TAREAS_API = threading.Lock()
_DAEMON_HILO: Optional[threading.Thread] = None
_DAEMON_PARAR = threading.Event()

API_PREFIJO = "/api/v1"


def _clave_api_efectiva(token: Optional[str]) -> str:
    """Resuelve la clave API: explícita → config.json → generar y guardar."""
    if token:
        return token
    sc = _importar_snapcontext()
    if sc is not None:
        try:
            clave = (sc.cargar_configuracion().get("api_key") or "").strip()
            if clave:
                return clave
            clave = sc._generar_clave_api()
            print("⚠ No había API key configurada; se generó una nueva y "
                  "se guardó en ~/.snapcontext/config.json ('api_key').")
            return clave
        except Exception:      # noqa: BLE001 — nunca romper el arranque
            pass
    return ""


def _ejecutar_tarea_api(task_id: str, tipo: str, cuerpo: dict) -> None:
    """Ejecuta una tarea larga de la API en segundo plano."""
    import snapcontext as sc

    with _CANDADO_TAREAS_API:
        registro = _TAREAS_API[task_id]
        registro["estado"] = "ejecutando"
    try:
        consulta = (cuerpo.get("consulta") or "").strip()
        argv = [consulta] if tipo == "query" else ["--plan", consulta]
        directorio = (cuerpo.get("directorio") or "").strip()
        if directorio and directorio != _DIRECTORIO_DEFECTO:
            argv += ["--directorio", directorio]
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
                estado="completada" if ok else "fallida",
                resultado={"codigo_salida": codigo})
    except Exception as exc:   # noqa: BLE001 — se reporta en la tarea
        with _CANDADO_TAREAS_API:
            registro.update(estado="error", error=str(exc))


def _lanzar_tarea_api(tipo: str, cuerpo: dict) -> dict:
    """Registra y lanza una tarea asíncrona. Devuelve task_id + URL."""
    task_id = uuid.uuid4().hex
    with _CANDADO_TAREAS_API:
        _TAREAS_API[task_id] = {
            "task_id": task_id, "tipo": tipo, "estado": "pendiente",
            "creado": time.time(), "resultado": None, "error": None}
    threading.Thread(target=_ejecutar_tarea_api,
                     args=(task_id, tipo, dict(cuerpo)),
                     daemon=True).start()
    return {"task_id": task_id, "estado": "pendiente",
            "url": f"{API_PREFIJO}/tasks/{task_id}"}


def crear_app(api_token: Optional[str] = None) -> FastAPI:
    """Construye y devuelve la app FastAPI (rutas + WebSocket + API v3.6.0).

    ``api_token`` fija la clave exigida en los endpoints ``/api/v1/*``; si se
    omite, se usa (o genera) la guardada en ``~/.snapcontext/config.json``
    bajo la clave ``"api_key"``.
    """
    app = FastAPI(title="SnapContext Web", version="3.6.0")

    # ---- Autenticación de la API (v3.6.0) -------------------------------
    _clave_api = _clave_api_efectiva(api_token)

    def _autorizar(x_api_key: Optional[str] = Header(default=None),
                   api_key: Optional[str] = Query(default=None)) -> None:
        """Exige la API key en el header ``X-API-Key`` (o query param)."""
        if not _clave_api:
            return                      # sin clave configurada: sin auth
        if x_api_key != _clave_api and api_key != _clave_api:
            raise HTTPException(
                status_code=401, detail="API key inválida o ausente.")

    @app.get(f"{API_PREFIJO}/health")
    async def api_health():
        """Estado del servidor (endpoint público)."""
        sc = _importar_snapcontext()
        return {"estado": "ok", "servicio": "snapcontext",
                "version": getattr(sc, "VERSION", "?") if sc else "?",
                "docs": "/docs", "redoc": "/redoc"}

    @app.post(f"{API_PREFIJO}/query", status_code=202,
              dependencies=[Depends(_autorizar)])
    async def api_query(cuerpo: dict = Body(...)):
        """Ejecuta una consulta (equivale a ``snapcontext "consulta"``).

        Responde 202 con un ``task_id``; el progreso se consulta en
        ``/api/v1/tasks/{task_id}``.
        """
        if not (cuerpo.get("consulta") or "").strip():
            raise HTTPException(status_code=400,
                                detail="Falta el campo 'consulta'.")
        return _lanzar_tarea_api("query", cuerpo)

    @app.post(f"{API_PREFIJO}/plan", status_code=202,
              dependencies=[Depends(_autorizar)])
    async def api_plan(cuerpo: dict = Body(...)):
        """Ejecuta un plan (equivale a ``snapcontext --plan "consulta"``)."""
        if not (cuerpo.get("consulta") or "").strip():
            raise HTTPException(status_code=400,
                                detail="Falta el campo 'consulta'.")
        return _lanzar_tarea_api("plan", cuerpo)

    @app.post(f"{API_PREFIJO}/chat", dependencies=[Depends(_autorizar)])
    async def api_chat(cuerpo: dict = Body(...)):
        """Envía un mensaje al proveedor y devuelve la respuesta (síncrono).

        Acepta ``historial`` (lista de mensajes role/content, últimos 20)
        para mantener contexto conversacional.
        """
        sc = _importar_snapcontext()
        if sc is None:
            raise HTTPException(status_code=500,
                                detail="snapcontext no disponible.")
        mensaje = (cuerpo.get("mensaje") or "").strip()
        if not mensaje:
            raise HTTPException(status_code=400,
                                detail="Falta el campo 'mensaje'.")
        preferencias = sc.cargar_configuracion()
        proveedor = (cuerpo.get("proveedor")
                     or preferencias.get("provider")
                     or sc.PROVEEDOR_DEFECTO)
        historial = list(cuerpo.get("historial") or [])[-20:]
        mensajes = historial + [{"role": "user", "content": mensaje}]
        try:
            respuesta = sc._enviar_al_proveedor(
                proveedor, cuerpo.get("modelo"), mensajes)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"respuesta": respuesta, "proveedor": proveedor}

    @app.get(f"{API_PREFIJO}/skills", dependencies=[Depends(_autorizar)])
    async def api_skills(archivados: bool = Query(default=False)):
        """Lista las skills aprendidas por la memoria persistente."""
        sc = _importar_snapcontext()
        filas = (sc._skill_listar(incluir_archivados=bool(archivados))
                 if sc else [])
        return {"total": len(filas), "skills": filas}

    @app.post(f"{API_PREFIJO}/daemon", dependencies=[Depends(_autorizar)])
    async def api_daemon(cuerpo: dict = Body(...)):
        """Gestiona el daemon: ``{"accion": "estado"|"iniciar"|"detener"}``."""
        global _DAEMON_HILO
        accion = (cuerpo.get("accion") or "estado").strip().lower()
        vivo = bool(_DAEMON_HILO and _DAEMON_HILO.is_alive())
        if accion == "estado":
            return {"accion": "estado", "activo": vivo}
        sc = _importar_snapcontext()
        if sc is None:
            raise HTTPException(status_code=500,
                                detail="snapcontext no disponible.")
        if accion == "iniciar":
            if vivo:
                return {"accion": "iniciar", "activo": True,
                        "detalle": "El daemon ya estaba en ejecución."}
            intervalo = int(cuerpo.get("intervalo_horas")
                            or getattr(sc, "DAEMON_INTERVALO_HORAS_DEFECTO",
                                       6))
            pausa = int(getattr(sc, "DAEMON_PAUSA_SEGUNDOS", 3600))
            _DAEMON_PARAR.clear()

            def _bucle_daemon():
                while not _DAEMON_PARAR.is_set():
                    try:
                        sc._daemon_tick(intervalo_horas=intervalo)
                    except Exception:   # noqa: BLE001 — el daemon continúa
                        pass
                    _DAEMON_PARAR.wait(timeout=max(1, pausa))

            _DAEMON_HILO = threading.Thread(target=_bucle_daemon,
                                            daemon=True)
            _DAEMON_HILO.start()
            return {"accion": "iniciar", "activo": True,
                    "intervalo_horas": intervalo}
        if accion == "detener":
            _DAEMON_PARAR.set()
            if _DAEMON_HILO is not None:
                _DAEMON_HILO.join(timeout=5)
            return {"accion": "detener",
                    "activo": bool(_DAEMON_HILO and _DAEMON_HILO.is_alive())}
        raise HTTPException(status_code=400, detail=(
            "Acción inválida; usa 'estado', 'iniciar' o 'detener'."))

    @app.get(f"{API_PREFIJO}/tasks/{{task_id}}",
             dependencies=[Depends(_autorizar)])
    async def api_task(task_id: str):
        """Devuelve el estado de una tarea asíncrona."""
        with _CANDADO_TAREAS_API:
            registro = _TAREAS_API.get(task_id)
            datos = dict(registro) if registro else None
        if datos is None:
            raise HTTPException(status_code=404, detail="Tarea no encontrada.")
        return datos

    @app.get("/")
    async def raiz():
        return FileResponse(_ESTATICO / "index.html")

    @app.get("/health")
    async def salud():
        return {"estado": "ok", "servicio": "snapcontext"}

    @app.websocket("/ws")
    async def _ws_punto(websocket: WebSocket):
        await websocket.accept()
        cola: "queue.Queue[dict]" = queue.Queue()
        try:
            while True:
                datos = await websocket.receive_text()
                try:
                    mensaje = json.loads(datos)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"tipo": "log", "nivel": "error",
                         "texto": "✖ Mensaje JSON inválido."})
                    continue
                tipo = (mensaje.get("tipo") or "").strip()
                if not tipo and (mensaje.get("consulta") or "").strip():
                    tipo = "tarea"        # compat. con el viejo protocolo
                if not tipo:
                    continue

                if tipo in ("tarea", "accion"):
                    hilo = threading.Thread(
                        target=(_ejecutar_tarea if tipo == "tarea"
                                else _ejecutar_accion),
                        args=((_construir_args(mensaje), cola)
                              if tipo == "tarea" else (mensaje, cola)),
                        daemon=True)
                    hilo.start()
                    await _reenviar_hasta(websocket, cola, hilo)
                elif tipo == "leer_archivo":
                    await websocket.send_json(
                        {"tipo": "archivo_seleccionado",
                         **_leer_archivo_web(mensaje)})
                elif tipo == "guardar_archivo":
                    await websocket.send_json(
                        {"tipo": "archivo_guardado",
                         **_guardar_archivo_web(mensaje)})
                elif tipo == "dependencias":
                    await websocket.send_json(
                        {"tipo": "dependencias_actualizadas",
                         **_dependencias_web(mensaje)})
                elif tipo == "semantica":
                    await websocket.send_json(
                        {"tipo": "semanticos",
                         "resultados": _semantica_web(mensaje)})
                elif tipo == "explorar":
                    await websocket.send_json(
                        {"tipo": "exploracion",
                         "lineas": _explorar_web(mensaje)})
                elif tipo == "ping":
                    await websocket.send_json({"tipo": "pong"})
        except Exception:
            pass                     # conexión cerrada o mensaje inválido
        finally:
            sc = _importar_snapcontext()
            if sc is not None:
                sc.fijar_evento_callback(None)
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
    except Exception as exc:  # noqa: BLE001  → se reporta a la UI
        cola.put({"tipo": "log", "nivel": "error", "texto": f"✖ {exc}"})
        cola.put({"tipo": "final", "ok": False, "error": str(exc),
                  "tiempo": round(time.monotonic() - t0, 1)})
    finally:
        sc = _importar_snapcontext()
        if sc is not None:
            sc.fijar_evento_callback(None)


def _construir_args(mensaje: dict):
    """Construye un argparse.Namespace válido a partir del JSON de la UI."""
    import snapcontext as sc

    consulta = (mensaje.get("consulta") or "").strip()
    argv: List[str] = [consulta]

    if mensaje.get("directorio"):
        argv += ["--directorio", str(mensaje["directorio"])]
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
def _ejecutar_accion(mensaje: dict, cola) -> None:
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

    try:
        if accion == "run":
            objetivo = comando or consulta
            if not objetivo:
                ev({"tipo": "log", "nivel": "error",
                    "texto": "✖ La acción Run necesita un comando o consulta."})
                ev({"tipo": "accion_ejecutada", "accion": accion, "ok": False})
                return
            codigo, stdout, stderr = sc._ejecutar_comando(
                objetivo, directorio, timeout=180)
            ok = codigo == 0
            resumen = (stdout or stderr or f"código {codigo}")[:300]
            ev({"tipo": "log", "nivel": "info", "texto": resumen})
            ev({"tipo": "accion_ejecutada", "accion": accion, "ok": ok,
                "resumen": resumen})
        elif accion == "search":
            resultados = _semantica_web(mensaje)
            ev({"tipo": "accion_ejecutada", "accion": accion, "ok": True,
                "resumen": f"{len(resultados)} resultado(s) semántico(s)."})
            if resultados:
                ev({"tipo": "semanticos", "resultados": resultados})
        elif accion == "explorar":
            tema = consulta or archivo
            mensaje2 = dict(mensaje)
            mensaje2["tema"] = tema or "."
            lineas = _explorar_web(mensaje2)
            ev({"tipo": "accion_ejecutada", "accion": accion, "ok": True,
                "resumen": f"{len(lineas)} coincidencia(s)."})
            if lineas:
                ev({"tipo": "exploracion", "lineas": lineas[:50]})
        elif accion == "asesor":
            # v3.5.0: asesor de código proactivo → panel de sugerencias.
            sugerencias = sc._asesor_analizar(
                mensaje.get("directorio") or _DIRECTORIO_DEFECTO)
            ev({"tipo": "asesor", "sugerencias": sugerencias})
            ev({"tipo": "accion_ejecutada", "accion": accion, "ok": True,
                "resumen": f"{len(sugerencias)} sugerencia(s) de mejora."})
        elif accion in ("plugins", "plugin_install", "plugin_remove"):
            # v4.0.0: ecosistema de plugins → panel de plugins.
            try:
                if accion == "plugin_install":
                    origen = (mensaje.get("origen")
                              or mensaje.get("consulta") or "").strip()
                    codigo = sc._plugin_instalar(origen, auto=True) \
                        if origen else 1
                elif accion == "plugin_remove":
                    nombre = (mensaje.get("nombre")
                              or mensaje.get("consulta") or "").strip()
                    # Desde la web no hay TTY: se omite la confirmación.
                    codigo = sc._plugin_remove(nombre, confirmar=False) \
                        if nombre else 1
                else:
                    codigo = 0
                plugins = list(sc._plugins_instalados().values())
                for p in plugins:
                    p.pop("ruta", None)
                ev({"tipo": "plugins", "plugins": plugins})
                resumen = {0: "OK"}.get(codigo,
                                        "fallo en la operación del plugin") \
                    if accion != "plugins" else f"{len(plugins)} plugin(s)."
                ev({"tipo": "accion_ejecutada", "accion": accion,
                    "ok": codigo == 0, "resumen": resumen})
            except Exception as exc:   # noqa: BLE001 — reportado a la UI
                ev({"tipo": "accion_ejecutada", "accion": accion, "ok": False,
                    "error": str(exc)})
        else:
            # fix / review / plan → pipeline o planificador de snapcontext.
            sc.fijar_evento_callback(ev)
            try:
                args = _construir_args_accion(accion, consulta, directorio)
                if accion == "plan":
                    codigo = sc._ejecutar_planificador(args)
                else:
                    codigo = sc.flujo_principal(args)
                ev({"tipo": "accion_ejecutada", "accion": accion,
                    "ok": codigo == 0, "resumen": f"{accion} finalizado."})
            finally:
                sc.fijar_evento_callback(None)
    except Exception as exc:  # noqa: BLE001  → se reporta a la UI
        ev({"tipo": "log", "nivel": "error", "texto": f"✖ {exc}"})
        ev({"tipo": "accion_ejecutada", "accion": accion, "ok": False,
            "error": str(exc)})


def _construir_args_accion(accion: str, consulta: str, directorio: str):
    """Construye argparse.Namespace para una acción rápida de pipeline."""
    import snapcontext as sc

    if accion == "fix":
        argv = sc._preparar_argv_aliases(["fix"] + shlex.split(consulta))
    elif accion == "review":
        argv = sc._preparar_argv_aliases(["review"] + shlex.split(consulta))
    elif accion == "plan":
        argv = ["--plan", consulta or ""]
    else:
        argv = [consulta or ""]
    if directorio and directorio != _DIRECTORIO_DEFECTO:
        argv += ["--directorio", directorio]

    args = sc.crear_parser().parse_args(argv)
    # En web nunca hay confirmación interactiva ni menú por paso.
    args.auto = True
    args.no_confirmar = True
    args.depurar = False
    return args

# --------------------------------------------------------------------------
# Editor web (leer / guardar archivos) — v1.2.0
# --------------------------------------------------------------------------
def _resolver_camino(ruta: str, directorio: str) -> Path:
    """Convierte ``ruta`` (relativa o absoluta) en una ruta absoluta resuelta."""
    camino = Path(ruta)
    if not camino.is_absolute():
        base = Path(directorio or _DIRECTORIO_DEFECTO)
        camino = (base if base.is_absolute() else Path.cwd() / base) / camino
    return camino.resolve()


def _leer_archivo_web(mensaje: dict) -> dict:
    """Lee un archivo para el editor web. Devuelve contenido + lenguaje."""
    import snapcontext as sc

    ruta = (mensaje.get("ruta") or "").strip()
    directorio = (mensaje.get("directorio") or _DIRECTORIO_DEFECTO).strip()
    if not ruta:
        return {"ruta": ruta, "contenido": None, "lenguaje": None,
                "error": "Falta la ruta del archivo."}
    camino = _resolver_camino(ruta, directorio)
    contenido = sc._leer_archivo(camino)
    if contenido is None:
        return {"ruta": str(camino), "contenido": None,
                "lenguaje": None, "error": "No se pudo leer el archivo."}
    return {"ruta": str(camino), "contenido": contenido,
            "lenguaje": sc._comando_para_monaco(ruta.split("/")[-1])}


def _guardar_archivo_web(mensaje: dict) -> dict:
    """Guarda el contenido editado. Devuelve ok/error."""
    ruta = (mensaje.get("ruta") or "").strip()
    contenido = mensaje.get("contenido") or ""
    directorio = (mensaje.get("directorio") or _DIRECTORIO_DEFECTO).strip()
    if not ruta:
        return {"ruta": ruta, "ok": False, "error": "Falta la ruta."}
    camino = _resolver_camino(ruta, directorio)
    try:
        camino.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contenido, str):
            camino.write_text(contenido, encoding="utf-8")
        else:
            camino.write_bytes(contenido)
        return {"ruta": str(camino), "ok": True}
    except OSError as exc:
        return {"ruta": str(camino), "ok": False, "error": str(exc)}

# --------------------------------------------------------------------------
# Dependencias / búsqueda / exploración — v1.2.0
# --------------------------------------------------------------------------
def _dependencias_web(mensaje: dict) -> dict:
    """Construye el grafo de dependencias del proyecto para la UI."""
    import snapcontext as sc

    directorio = (mensaje.get("directorio") or _DIRECTORIO_DEFECTO).strip()
    try:
        grafo = sc._grafo_dependencias(directorio)
        grafo["ruta"] = (mensaje.get("ruta") or "").strip()
        return grafo
    except Exception as exc:  # noqa: BLE001
        return {"nodos": [], "enlaces": [],
                "ruta": (mensaje.get("ruta") or "").strip(), "error": str(exc)}


def _semantica_web(mensaje: dict) -> list:
    """Búsqueda semántica (embeddings). Devuelve [] si no está disponible."""
    import snapcontext as sc

    consulta = (mensaje.get("consulta") or "").strip()
    directorio = (mensaje.get("directorio") or _DIRECTORIO_DEFECTO).strip()
    if not consulta or not sc._embeddings_disponibles():
        return []
    try:
        return sc._buscar_semanticamente(consulta, directorio,
                                         max_resultados=20)
    except Exception:  # noqa: BLE001
        return []


def _explorar_web(mensaje: dict) -> list:
    """Exploración por código (rg/grep/findstr). Devuelve lista de líneas."""
    import snapcontext as sc

    tema = (mensaje.get("tema") or "").strip()
    directorio = (mensaje.get("directorio") or _DIRECTORIO_DEFECTO).strip()
    try:
        return sc._buscar_en_codigo(tema, directorio)
    except Exception:  # noqa: BLE001
        return []


def arrancar_servidor(puerto: int = 8000) -> None:
    """Arranca uvicorn con la app FastAPI (bloquea hasta detenerse)."""
    import uvicorn

    uvicorn.run(
        crear_app(),
        host="127.0.0.1",
        port=int(puerto),
        log_level="warning",
    )


def arrancar_api(puerto: int = 8001, host: str = "127.0.0.1",
                 token: Optional[str] = None) -> None:
    """Arranca la API pública v3.6.0 (bloquea hasta detenerse).

    ``token`` fija la API key; si es ``None``, se usa (o genera) la guardada
    en ``~/.snapcontext/config.json``.
    """
    import uvicorn

    uvicorn.run(
        crear_app(api_token=token),
        host=host or "127.0.0.1",
        port=int(puerto),
        log_level="warning",
    )


__all__ = ["crear_app", "arrancar_servidor", "arrancar_api"]