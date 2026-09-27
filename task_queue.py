#!/usr/bin/env python3
"""Sistema de Cola de Tareas Asíncronas y Worker en Segundo Plano (v6.8.0).

Permite encolar tareas pesadas (pruebas, revisión de PRs, planes) desde
gateways (Telegram, Discord, GitHub) o CLI, ejecutarlas de forma asíncrona
mediante un worker demonio y enviar notificaciones push al finalizar.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".snapcontext"
DB_PATH = CONFIG_DIR / "memoria.db"

_CANDADO_COLA = threading.Lock()
_WORKER_HILO: threading.Thread | None = None
_WORKER_PARAR = threading.Event()
# v6.9.0: evento para despertar al worker sin polling. Se SET al encolar una
# nueva tarea; el worker bloquea con `wait()` en lugar de `time.sleep`, de modo
# que el consumo de CPU en reposo es 0% (solo se despierta ante trabajo nuevo o
# por timeout `intervalo_segundos` como red de seguridad para tareas de otros
# procesos/CLI).
_WORKER_DESPERTAR = threading.Event()

# ---------------------------------------------------------------------------
# D-02 — Frontera de tipos ejecutables (B9.59 §5, B9.60 §10)
#
# Solo `query` y `plan` son tipos ejecutables en M1. `tests`, `pr_review` y
# cualquier tipo desconocido quedan DENY **explícito**: no existe rama `else`
# que los ejecute. Consecuencia ratificada: los eventos de GitHub siguen
# autenticándose y encolándose, pero los tipos `tests`/`pr_review` no se
# ejecutan (sin `git checkout`, sin `pytest`, sin salida de red).
# ---------------------------------------------------------------------------
TIPOS_PERMITIDOS: frozenset[str] = frozenset({"query", "plan"})

#: Estados considered "encolados" (aún no terminal) para la invalidación.
ESTADOS_NO_TERMINALES: tuple[str, ...] = ("pendiente",)

#: Estado terminal aplicado a las tareas invalidadas por rotación de credencial
#: (B9.59 §9/§10, B9.60 §4/§10).
ESTADO_CANCELADA_ROTACION = "cancelada-por-rotacion"


@dataclass(frozen=True)
class TaskSecurityContext:
    """Contexto de seguridad **congelado** en el momento de encolar (D-02).

    B9.59 §10 congela e inmoviliza al crear la tarea: raíz resuelta,
    capabilities del creator, política aplicable e identidad de la credencial
    (nunca el secreto). El worker **revalida** contra este snapshot antes de
    ejecutar; nunca reconstruye una política más permisiva desde globals.

    Es ``frozen``: el worker no puede mutarlo, solo compararlo.
    """

    owner: str
    workspace_root: str
    capabilities: tuple[str, ...] = ()
    policy: str = "m1"
    generacion: int = 0
    origen: str = "local"

    def a_dict(self) -> dict[str, Any]:
        datos = asdict(self)
        datos["capabilities"] = list(self.capabilities)
        return datos

    @staticmethod
    def desde_dict(bruto: Any) -> TaskSecurityContext | None:
        """Reconstruye el contexto; **fail-closed** ante cualquier campo ausente
        o corrupto (B9.59 §10: "campo ausente/corrupto -> abortar")."""
        if not isinstance(bruto, dict):
            return None
        try:
            owner = str(bruto["owner"])
            root = str(bruto["workspace_root"])
            caps = bruto["capabilities"]
            policy = str(bruto["policy"])
            gen = int(bruto["generacion"])
            origen = str(bruto["origen"])
        except (KeyError, TypeError, ValueError):
            return None
        if not owner or not root or not policy or not origen:
            return None
        if not isinstance(caps, (list, tuple)):
            return None
        return TaskSecurityContext(
            owner=owner,
            workspace_root=root,
            capabilities=tuple(str(c) for c in caps),
            policy=policy,
            generacion=gen,
            origen=origen,
        )


# ---------------------------------------------------------------------------
# Generación de credencial (D-03)
#
# Se incrementa en cada rotación. Las tareas encoladas con una generación
# anterior dejan de ser ejecutables (revalidación del worker) y se marcan
# `cancelada-por-rotacion` (invalidación de las pendientes).
# ---------------------------------------------------------------------------
_CANDADO_GENERACION = threading.Lock()
_GENERACION = 0


def generacion_actual() -> int:
    """Generación vigente de la credencial del gateway."""
    with _CANDADO_GENERACION:
        return _GENERACION


def _avanzar_generacion() -> int:
    global _GENERACION
    with _CANDADO_GENERACION:
        _GENERACION += 1
        return _GENERACION


def contexto_por_defecto(
    *,
    owner: str | None = None,
    workspace_root: str | Path | None = None,
    capabilities: tuple[str, ...] | list[str] = (),
    policy: str = "m1",
    origen: str = "local",
) -> TaskSecurityContext:
    """Snapshot explícito para una tarea creada por una superficie local.

    ``encolar_tarea`` **siempre** persiste un contexto: si el llamador no
    aporta uno, se crea uno local explícito. Así el worker nunca ejecuta una
    tarea sin contexto de seguridad verificable (fail-closed) y la tarea nunca
    depende de globals re-resueltos en el worker.
    """
    raiz = Path(workspace_root) if workspace_root else Path.cwd()
    try:
        raiz_resuelto = str(raiz.resolve())
    except OSError:
        raiz_resuelto = str(raiz)
    return TaskSecurityContext(
        owner=owner or f"local:{os.getpid()}",
        workspace_root=raiz_resuelto,
        capabilities=tuple(str(c) for c in capabilities),
        policy=policy,
        generacion=generacion_actual(),
        origen=origen,
    )


# ---------------------------------------------------------------------------
# Inicialización y Conexión a Base de Datos
# ---------------------------------------------------------------------------
def _get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Abre o reutiliza conexión SQLite asegurando la existencia de la tabla `tareas`."""
    ruta = Path(db_path) if db_path else DB_PATH
    if str(ruta) != ":memory:":
        ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta), check_same_thread=False)
    con.row_factory = sqlite3.Row
    init_db(con)
    return con


def init_db(con_or_path: sqlite3.Connection | str | Path | None = None) -> None:
    """Crea la tabla `tareas` si no existe."""
    if isinstance(con_or_path, sqlite3.Connection):
        con = con_or_path
        debe_cerrar = False
    else:
        ruta = Path(con_or_path) if con_or_path else DB_PATH
        if str(ruta) != ":memory:":
            ruta.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(ruta), check_same_thread=False)
        debe_cerrar = True

    with con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS tareas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tipo TEXT NOT NULL,
                estado TEXT NOT NULL,
                datos TEXT NOT NULL,
                resultado TEXT,
                chat_id TEXT,
                canal TEXT,
                creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                actualizado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # B9.61-D (D-02): owner + contexto de seguridad congelado. Migración
        # aditiva e idempotente: nunca destruye tareas ya encoladas.
        columnas = {f[1] for f in con.execute("PRAGMA table_info(tareas)").fetchall()}
        if "owner" not in columnas:
            con.execute("ALTER TABLE tareas ADD COLUMN owner TEXT")
        if "contexto" not in columnas:
            con.execute("ALTER TABLE tareas ADD COLUMN contexto TEXT")
        con.execute("CREATE INDEX IF NOT EXISTS idx_tareas_estado ON tareas(estado);")
    if debe_cerrar:
        con.close()


# ---------------------------------------------------------------------------
# Operaciones de Cola
# ---------------------------------------------------------------------------
def encolar_tarea(
    tipo: str,
    datos: dict[str, Any],
    chat_id: str | int | None = None,
    canal: str | None = None,
    db_path: str | Path | None = None,
    contexto: TaskSecurityContext | None = None,
) -> int:
    """Inserta una nueva tarea en estado 'pendiente' y devuelve su ID.

    B9.61-D (D-02): el **snapshot del contexto de seguridad se toma aquí**, en el
    punto de creación, nunca en el worker. Si el llamador no aporta contexto se
    crea uno local explícito, de modo que toda tarea persistida lleva owner +
    raíz + capabilities + política + generación. El worker solo lo revalida.
    """
    ctx = contexto or contexto_por_defecto(origen="local")
    con = _get_connection(db_path)
    datos_json = json.dumps(datos, ensure_ascii=False)
    contexto_json = json.dumps(ctx.a_dict(), ensure_ascii=False)
    chat_str = str(chat_id) if chat_id is not None else None
    canal_str = str(canal).lower() if canal else None

    try:
        with _CANDADO_COLA:
            with con:
                cur = con.execute(
                    """
                    INSERT INTO tareas (tipo, estado, datos, chat_id, canal, owner, contexto,
                                        creado, actualizado)
                    VALUES (?, 'pendiente', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (tipo, datos_json, chat_str, canal_str, ctx.owner, contexto_json),
                )
                tarea_id = int(cur.lastrowid or 0)
        # v6.9.0: despertar al worker al instante (sin esperar el polling).
        _WORKER_DESPERTAR.set()
        return tarea_id
    finally:
        if str(db_path) != ":memory:":
            con.close()


def consumir_tarea(db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Obtiene la tarea pendiente más antigua y la marca como 'ejecutando'."""
    con = _get_connection(db_path)
    try:
        with _CANDADO_COLA:
            with con:
                cur = con.execute(
                    "SELECT * FROM tareas WHERE estado = 'pendiente' ORDER BY id ASC LIMIT 1"
                )
                fila = cur.fetchone()
                if not fila:
                    return None

                tarea_id = fila["id"]
                con.execute(
                    "UPDATE tareas SET estado = 'ejecutando', actualizado = CURRENT_TIMESTAMP WHERE id = ?",
                    (tarea_id,),
                )
                cur2 = con.execute("SELECT * FROM tareas WHERE id = ?", (tarea_id,))
                fila_actualizada = cur2.fetchone()

        resultado = dict(fila_actualizada) if fila_actualizada else None
        if resultado and "datos" in resultado and isinstance(resultado["datos"], str):
            try:
                resultado["datos"] = json.loads(resultado["datos"])
            except Exception:
                pass
        if resultado and isinstance(resultado.get("contexto"), str):
            try:
                resultado["contexto"] = json.loads(resultado["contexto"])
            except Exception:
                pass  # se deja como str: _revalidar_contexto lo denegará
        return resultado
    finally:
        if str(db_path) != ":memory:":
            con.close()


def actualizar_estado_tarea(
    tarea_id: int,
    estado: str,
    resultado: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> bool:
    """Actualiza el estado y resultado de una tarea."""
    con = _get_connection(db_path)
    res_json = json.dumps(resultado, ensure_ascii=False) if resultado is not None else None
    try:
        with _CANDADO_COLA:
            with con:
                cur = con.execute(
                    """
                    UPDATE tareas
                    SET estado = ?, resultado = ?, actualizado = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (estado, res_json, tarea_id),
                )
                filas_afectadas = cur.rowcount
        return filas_afectadas > 0
    finally:
        if str(db_path) != ":memory:":
            con.close()


def obtener_tarea(tarea_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Recupera la información completa de una tarea por su ID."""
    con = _get_connection(db_path)
    try:
        with con:
            cur = con.execute("SELECT * FROM tareas WHERE id = ?", (tarea_id,))
            fila = cur.fetchone()

        if not fila:
            return None

        tarea = dict(fila)
        if "datos" in tarea and isinstance(tarea["datos"], str):
            try:
                tarea["datos"] = json.loads(tarea["datos"])
            except Exception:
                pass
        if "resultado" in tarea and isinstance(tarea["resultado"], str) and tarea["resultado"]:
            try:
                tarea["resultado"] = json.loads(tarea["resultado"])
            except Exception:
                pass
        # B9.61-D (D-02): el contexto se devuelve parseado para que el consumidor
        # pueda inspeccionarlo (tests, auditoría). Si estuviera corrupto se deja
        # como texto: `_revalidar_contexto` lo denegará (fail-closed).
        if isinstance(tarea.get("contexto"), str):
            try:
                tarea["contexto"] = json.loads(tarea["contexto"])
            except Exception:
                pass

        return tarea
    finally:
        if str(db_path) != ":memory:":
            con.close()


def listar_tareas(
    estados: list[str] | None = None,
    limite: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Lista las tareas filtrando opcionalmente por estado."""
    con = _get_connection(db_path)
    try:
        with con:
            if estados:
                placeholders = ",".join(["?"] * len(estados))
                cur = con.execute(
                    f"SELECT * FROM tareas WHERE estado IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                    (*estados, limite),
                )
            else:
                cur = con.execute("SELECT * FROM tareas ORDER BY id DESC LIMIT ?", (limite,))
            filas = cur.fetchall()

        salida = []
        for f in filas:
            t = dict(f)
            if "datos" in t and isinstance(t["datos"], str):
                try:
                    t["datos"] = json.loads(t["datos"])
                except Exception:
                    pass
            if "resultado" in t and isinstance(t["resultado"], str) and t["resultado"]:
                try:
                    t["resultado"] = json.loads(t["resultado"])
                except Exception:
                    pass
            salida.append(t)

        return salida
    finally:
        if str(db_path) != ":memory:":
            con.close()


def cancelar_tarea(tarea_id: int, db_path: str | Path | None = None) -> bool:
    """Cancela una tarea si está en estado 'pendiente'."""
    con = _get_connection(db_path)
    try:
        with _CANDADO_COLA:
            with con:
                cur = con.execute(
                    "UPDATE tareas SET estado = 'cancelada', actualizado = CURRENT_TIMESTAMP WHERE id = ? AND estado = 'pendiente'",
                    (tarea_id,),
                )
                ok = cur.rowcount > 0
        return ok
    finally:
        if str(db_path) != ":memory:":
            con.close()


# ---------------------------------------------------------------------------
# Notificaciones Push
# ---------------------------------------------------------------------------
def enviar_notificacion(
    chat_id: str | int | None, mensaje: str, canal: str | None = "telegram"
) -> bool:
    """Envía un mensaje de notificación al canal configurado (Telegram / Discord)."""
    if not chat_id or not mensaje:
        return False

    canal_limpio = (canal or "telegram").lower()

    if canal_limpio == "telegram":
        try:
            import telegram_gateway as tg

            # Si ya hay un event loop activo, lo corremos en un hilo o task
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Fire-and-forget intencional (notificación asíncrona).
                    asyncio.create_task(  # noqa: RUF006
                        tg.send_telegram_message(str(chat_id), mensaje)
                    )
                    return True
                return loop.run_until_complete(tg.send_telegram_message(str(chat_id), mensaje))
            except RuntimeError:
                return asyncio.run(tg.send_telegram_message(str(chat_id), mensaje))
        except Exception as exc:
            print(f"✖ [task_queue] Error enviando notificación Telegram: {exc}")
            return False

    elif canal_limpio == "discord":
        try:
            import discord_gateway as dg

            webhook_url = dg.obtener_webhook_url()
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Fire-and-forget intencional (notificación asíncrona).
                    asyncio.create_task(  # noqa: RUF006
                        dg.send_discord_message(webhook_url, mensaje)
                    )
                    return True
                return loop.run_until_complete(dg.send_discord_message(webhook_url, mensaje))
            except RuntimeError:
                return asyncio.run(dg.send_discord_message(webhook_url, mensaje))
        except Exception as exc:
            print(f"✖ [task_queue] Error enviando notificación Discord: {exc}")
            return False

    return False


# ---------------------------------------------------------------------------
# D-02 / D-03 — Revalidación del contexto y frontera de tipos
# ---------------------------------------------------------------------------
def _revalidar_contexto(tarea: dict[str, Any]) -> tuple[TaskSecurityContext | None, str]:
    """Revalida el snapshot antes de ejecutar. Fail-closed en toda anomalía.

    Devuelve ``(contexto, "")`` si la tarea es ejecutable, o ``(None, motivo)``.
    Comprobaciones (B9.59 §10): contexto presente y no corrupto; tipo en
    :data:`TIPOS_PERMITIDOS`; generación vigente (si la credencial rotó, la
    tarea ya no es válida); raíz del workspace todavía en el mismo realpath.
    """
    tipo = str(tarea.get("tipo") or "")
    if tipo not in TIPOS_PERMITIDOS:
        return None, f"tipo no permitido: {tipo or '(vacio)'}"

    crudo = tarea.get("contexto")
    if isinstance(crudo, str):
        try:
            crudo = json.loads(crudo)
        except (TypeError, ValueError):
            return None, "contexto corrupto"
    ctx = TaskSecurityContext.desde_dict(crudo)
    if ctx is None:
        return None, "contexto ausente o invalido"

    if ctx.generacion != generacion_actual():
        return None, ESTADO_CANCELADA_ROTACION

    try:
        actual = str(Path(ctx.workspace_root).resolve())
    except OSError:
        return None, "raiz del workspace no resoluble"
    if actual != ctx.workspace_root:
        return None, "raiz del workspace cambiada desde el snapshot"

    return ctx, ""


def cancelar_pendientes_por_rotacion(db_path: str | Path | None = None) -> int:
    """D-03: invalida las tareas encoladas bajo la credencial anterior.

    Solo afecta a estados **no terminales** (pendientes). Las ya terminadas o en
    ejecución no se tocan: no se mata trabajo local ya empezado
    (B9.59 §9/§10, B9.60 §4/§10).
    """
    _avanzar_generacion()
    con = _get_connection(db_path)
    try:
        with _CANDADO_COLA:
            with con:
                marcadores = ", ".join("?" for _ in ESTADOS_NO_TERMINALES)
                cur = con.execute(
                    f"""
                    UPDATE tareas
                    SET estado = ?, resultado = ?, actualizado = CURRENT_TIMESTAMP
                    WHERE estado IN ({marcadores})
                    """,
                    (
                        ESTADO_CANCELADA_ROTACION,
                        json.dumps(
                            {
                                "ok": False,
                                "error": ESTADO_CANCELADA_ROTACION,
                                "mensaje": "Tarea cancelada por rotacion de credencial.",
                            },
                            ensure_ascii=False,
                        ),
                        *ESTADOS_NO_TERMINALES,
                    ),
                )
                affected = cur.rowcount
        _WORKER_DESPERTAR.set()
        return affected
    finally:
        if str(db_path) != ":memory:":
            con.close()
def _argv_con_snapshot(consulta: str, flags: list[str], ctx: TaskSecurityContext) -> list[str]:
    """Construye el argv con el `--directorio` congelado, sin shell.

    El directorio sale del **snapshot**, nunca del ``directorio`` crudo ni del
    cwd del worker (B9.59 §10: "prohibido re-resolver directorio crudo").
    """
    argv = [consulta, *flags, "--auto", "--no-confirmar"]
    try:
        actual = str(Path.cwd().resolve())
    except OSError:
        actual = ""
    if ctx.workspace_root and ctx.workspace_root != actual:
        argv += ["--directorio", ctx.workspace_root]
    return argv


def ejecutar_tarea(tarea: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta la tarea **solo** si su contexto de seguridad sigue vigente.

    B9.61-D (D-02/D-03): frontera fail-closed. Se revalida el contexto
    congelado y el tipo antes de hacer nada. ``tests``, ``pr_review`` y
    cualquier tipo desconocido reciben DENY explícito — **no existe rama
    ``else`` ejecutable**, y en particular no hay ninguna ruta desde esta
    función hacia un shell ni hacia la red (B9.59 §5, B9.60 §10: sin
    ``git checkout``, sin ``pytest``, sin ``obtener_pr_diff``).
    """
    import snapcontext as sc

    tipo = str(tarea.get("tipo") or "")
    datos = tarea.get("datos") or {}
    if isinstance(datos, str):
        try:
            datos = json.loads(datos)
        except Exception:
            datos = {}

    ctx, motivo = _revalidar_contexto(tarea)
    if ctx is None:
        cancelada = motivo == ESTADO_CANCELADA_ROTACION
        return {
            "ok": False,
            "estado": ESTADO_CANCELADA_ROTACION if cancelada else "fallida",
            "error": motivo,
            "mensaje": f"Tarea no ejecutada: {motivo}.",
        }

    resultado: dict[str, Any] = {"ok": False}

    try:
        if tipo == "query":
            consulta = datos.get("consulta") or datos.get("instruccion") or "Tarea"
            args = sc.crear_parser().parse_args(_argv_con_snapshot(consulta, [], ctx))
            codigo = sc.flujo_principal(args)
            resultado = {
                "ok": codigo == 0,
                "codigo_salida": codigo,
                "mensaje": "Tarea ejecutada." if codigo == 0 else "Tarea finalizada con errores.",
            }
        elif tipo == "plan":
            consulta = datos.get("consulta") or datos.get("instruccion") or "Planificar cambios"
            args = sc.crear_parser().parse_args(_argv_con_snapshot(consulta, ["--plan"], ctx))
            codigo = sc._ejecutar_planificador(args)
            resultado = {
                "ok": codigo == 0,
                "codigo_salida": codigo,
                "mensaje": "Plan generado." if codigo == 0 else "Plan finalizado con errores.",
            }
        else:  # pragma: no cover - _revalidar_contexto ya habría denegado
            return {
                "ok": False,
                "error": f"tipo no permitido: {tipo}",
                "mensaje": f"Tarea no ejecutada: tipo no permitido: {tipo}.",
            }
    except Exception as exc:
        resultado = {
            "ok": False,
            "error": str(exc),
            "mensaje": f"Excepción durante ejecución: {exc}",
        }

    return resultado


def procesar_siguiente_tarea(db_path: str | Path | None = None) -> dict[str, Any] | None:
    """Consume una tarea pendiente, la ejecuta, actualiza la base de datos y notifica."""
    tarea = consumir_tarea(db_path=db_path)
    if not tarea:
        return None

    tarea_id = tarea["id"]
    tipo = tarea.get("tipo", "")
    chat_id = tarea.get("chat_id")
    canal = tarea.get("canal") or "telegram"

    res = ejecutar_tarea(tarea)
    # D-03: si la revalidación invalidó la tarea, se respeta su estado terminal
    # (`cancelada-por-rotacion`) en vez de degradarla a "fallida".
    estado_ctx = res.get("estado")
    if estado_ctx:
        nuevo_estado = str(estado_ctx)
    else:
        nuevo_estado = "completada" if res.get("ok") else "fallida"
    actualizar_estado_tarea(tarea_id, nuevo_estado, resultado=res, db_path=db_path)

    # Notificación de resultado
    if chat_id:
        if res.get("ok"):
            msg = f"✅ Tarea {tarea_id} ({tipo}) completada: {res.get('mensaje', 'Éxito')}"
        else:
            msg = f"❌ Tarea {tarea_id} ({tipo}) falló: {res.get('error') or res.get('mensaje', 'Error')}"
        enviar_notificacion(chat_id, msg, canal=canal)

    return {
        "id": tarea_id,
        "tipo": tipo,
        "estado": nuevo_estado,
        "resultado": res,
    }


# ---------------------------------------------------------------------------
# Worker Demonio
# ---------------------------------------------------------------------------
def _bucle_worker(intervalo_segundos: float = 2.0, db_path: str | Path | None = None) -> None:
    """Bucle continuo del worker consumiendo tareas de la cola (v6.9.0).

    Sin polling: espera en ``_WORKER_DESPERTAR.wait()`` en lugar de
    ``time.sleep``. El evento se setea desde ``encolar_tarea``, así el worker se
    despierta al instante cuando hay trabajo nuevo (CPU idle 0%). El
    ``timeout=intervalo_segundos`` actúa como red de seguridad por si la tarea
    la encoló otro proceso (CLI/gateway) que no comparte este evento.
    """
    while not _WORKER_PARAR.is_set():
        try:
            tarea_procesada = procesar_siguiente_tarea(db_path=db_path)
            if not tarea_procesada:
                _WORKER_DESPERTAR.wait(intervalo_segundos)
                _WORKER_DESPERTAR.clear()
        except Exception:
            _WORKER_DESPERTAR.wait(intervalo_segundos)
            _WORKER_DESPERTAR.clear()


def iniciar_worker(
    daemon: bool = True,
    intervalo_segundos: float = 2.0,
    db_path: str | Path | None = None,
) -> threading.Thread:
    """Inicia el worker de la cola de tareas en un hilo secundario."""
    global _WORKER_HILO
    _WORKER_PARAR.clear()
    # v6.9.0: empezar limpio (el worker arranca procesando de inmediato).
    _WORKER_DESPERTAR.clear()
    if _WORKER_HILO is not None and _WORKER_HILO.is_alive():
        return _WORKER_HILO

    _WORKER_HILO = threading.Thread(
        target=_bucle_worker,
        args=(intervalo_segundos, db_path),
        name="snap-task-worker",
        daemon=daemon,
    )
    _WORKER_HILO.start()
    return _WORKER_HILO


def detener_worker(timeout: float = 5.0) -> None:
    """Detiene el worker si está en ejecución."""
    global _WORKER_HILO
    _WORKER_PARAR.set()
    # v6.9.0: despertar al worker para que abandone el `wait()` y termine ya.
    _WORKER_DESPERTAR.set()
    if _WORKER_HILO is not None and _WORKER_HILO.is_alive():
        _WORKER_HILO.join(timeout=timeout)
    _WORKER_HILO = None
