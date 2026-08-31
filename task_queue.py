#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sistema de Cola de Tareas Asíncronas y Worker en Segundo Plano (v6.8.0).

Permite encolar tareas pesadas (pruebas, revisión de PRs, planes) desde
gateways (Telegram, Discord, GitHub) o CLI, ejecutarlas de forma asíncrona
mediante un worker demonio y enviar notificaciones push al finalizar.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_DIR = Path.home() / ".snapcontext"
DB_PATH = CONFIG_DIR / "memoria.db"

_CANDADO_COLA = threading.Lock()
_WORKER_HILO: Optional[threading.Thread] = None
_WORKER_PARAR = threading.Event()


# ---------------------------------------------------------------------------
# Inicialización y Conexión a Base de Datos
# ---------------------------------------------------------------------------
def _get_connection(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """Abre o reutiliza conexión SQLite asegurando la existencia de la tabla `tareas`."""
    ruta = Path(db_path) if db_path else DB_PATH
    if str(ruta) != ":memory:":
        ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta), check_same_thread=False)
    con.row_factory = sqlite3.Row
    init_db(con)
    return con


def init_db(con_or_path: Optional[sqlite3.Connection | str | Path] = None) -> None:
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
        con.execute("CREATE INDEX IF NOT EXISTS idx_tareas_estado ON tareas(estado);")
    if debe_cerrar:
        con.close()


# ---------------------------------------------------------------------------
# Operaciones de Cola
# ---------------------------------------------------------------------------
def encolar_tarea(
    tipo: str,
    datos: Dict[str, Any],
    chat_id: Optional[str | int] = None,
    canal: Optional[str] = None,
    db_path: Optional[str | Path] = None,
) -> int:
    """Inserta una nueva tarea en estado 'pendiente' y devuelve su ID."""
    con = _get_connection(db_path)
    datos_json = json.dumps(datos, ensure_ascii=False)
    chat_str = str(chat_id) if chat_id is not None else None
    canal_str = str(canal).lower() if canal else None

    try:
        with _CANDADO_COLA:
            with con:
                cur = con.execute(
                    """
                    INSERT INTO tareas (tipo, estado, datos, chat_id, canal, creado, actualizado)
                    VALUES (?, 'pendiente', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (tipo, datos_json, chat_str, canal_str),
                )
                tarea_id = int(cur.lastrowid)
        return tarea_id
    finally:
        if str(db_path) != ":memory:":
            con.close()


def consumir_tarea(db_path: Optional[str | Path] = None) -> Optional[Dict[str, Any]]:
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
            except Exception:  # noqa: BLE001
                pass
        return resultado
    finally:
        if str(db_path) != ":memory:":
            con.close()


def actualizar_estado_tarea(
    tarea_id: int,
    estado: str,
    resultado: Optional[Dict[str, Any]] = None,
    db_path: Optional[str | Path] = None,
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


def obtener_tarea(tarea_id: int, db_path: Optional[str | Path] = None) -> Optional[Dict[str, Any]]:
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
            except Exception:  # noqa: BLE001
                pass
        if "resultado" in tarea and isinstance(tarea["resultado"], str) and tarea["resultado"]:
            try:
                tarea["resultado"] = json.loads(tarea["resultado"])
            except Exception:  # noqa: BLE001
                pass

        return tarea
    finally:
        if str(db_path) != ":memory:":
            con.close()


def listar_tareas(
    estados: Optional[List[str]] = None,
    limite: int = 20,
    db_path: Optional[str | Path] = None,
) -> List[Dict[str, Any]]:
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
                except Exception:  # noqa: BLE001
                    pass
            if "resultado" in t and isinstance(t["resultado"], str) and t["resultado"]:
                try:
                    t["resultado"] = json.loads(t["resultado"])
                except Exception:  # noqa: BLE001
                    pass
            salida.append(t)

        return salida
    finally:
        if str(db_path) != ":memory:":
            con.close()


def cancelar_tarea(tarea_id: int, db_path: Optional[str | Path] = None) -> bool:
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
def enviar_notificacion(chat_id: Optional[str | int], mensaje: str, canal: Optional[str] = "telegram") -> bool:
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
                    asyncio.create_task(tg.send_telegram_message(str(chat_id), mensaje))
                    return True
                return loop.run_until_complete(tg.send_telegram_message(str(chat_id), mensaje))
            except RuntimeError:
                return asyncio.run(tg.send_telegram_message(str(chat_id), mensaje))
        except Exception as exc:  # noqa: BLE001
            print(f"✖ [task_queue] Error enviando notificación Telegram: {exc}")
            return False

    elif canal_limpio == "discord":
        try:
            import discord_gateway as dg

            webhook_url = dg.obtener_webhook_url()
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(dg.send_discord_message(webhook_url, mensaje))
                    return True
                return loop.run_until_complete(dg.send_discord_message(webhook_url, mensaje))
            except RuntimeError:
                return asyncio.run(dg.send_discord_message(webhook_url, mensaje))
        except Exception as exc:  # noqa: BLE001
            print(f"✖ [task_queue] Error enviando notificación Discord: {exc}")
            return False

    return False


# ---------------------------------------------------------------------------
# Ejecutor de Tareas
# ---------------------------------------------------------------------------
def ejecutar_tarea(tarea: Dict[str, Any]) -> Dict[str, Any]:
    """Ejecuta la tarea asignada según su tipo usando el motor de SnapContext."""
    import snapcontext as sc

    tipo = tarea.get("tipo", "")
    datos = tarea.get("datos") or {}
    if isinstance(datos, str):
        try:
            datos = json.loads(datos)
        except Exception:  # noqa: BLE001
            datos = {}

    resultado: Dict[str, Any] = {"ok": False}

    try:
        if tipo in ("tests", "ejecutar_pruebas"):
            rama = datos.get("rama")
            comando = f"git checkout {rama} && " if rama else ""
            comando += sc.COMANDO_TEST_DEFECTO if hasattr(sc, "COMANDO_TEST_DEFECTO") else "pytest"
            codigo, stdout, stderr = sc._ejecutar_comando(comando, timeout=300)
            resultado = {
                "ok": codigo == 0,
                "codigo_salida": codigo,
                "salida": (stdout + "\n" + stderr).strip(),
                "mensaje": "Pruebas pasaron con éxito" if codigo == 0 else f"Pruebas fallaron (código {codigo})",
            }

        elif tipo in ("pr_review", "review"):
            repo = datos.get("repositorio") or ""
            numero = datos.get("numero") or 0
            titulo = datos.get("titulo") or ""
            cuerpo = datos.get("cuerpo") or ""
            diff = ""
            if repo and numero:
                try:
                    import github_gateway as gh
                    diff = gh.obtener_pr_diff(repo, numero) or ""
                except Exception:  # noqa: BLE001
                    diff = ""

            consulta = f"Revisar Pull Request #{numero}: {titulo}\n{cuerpo}\nDiff:\n{diff[:5000]}"
            parser = sc.crear_parser()
            args = parser.parse_args([consulta, "--experto", "--auto", "--no-confirmar"])
            codigo = sc.flujo_principal(args)
            resultado = {
                "ok": codigo == 0,
                "codigo_salida": codigo,
                "mensaje": f"Revisión de PR #{numero} completada con éxito." if codigo == 0 else f"Revisión de PR #{numero} finalizó con advertencias.",
            }

        elif tipo == "plan":
            consulta = datos.get("consulta") or datos.get("instruccion") or "Planificar cambios"
            parser = sc.crear_parser()
            args = parser.parse_args([consulta, "--plan", "--auto", "--no-confirmar"])
            codigo = sc._ejecutar_planificador(args)
            resultado = {
                "ok": codigo == 0,
                "codigo_salida": codigo,
                "mensaje": "Plan generado y ejecutado con éxito." if codigo == 0 else "Plan finalizó con errores.",
            }

        else:
            consulta = datos.get("consulta") or datos.get("instruccion") or "Tarea"
            parser = sc.crear_parser()
            args = parser.parse_args([consulta, "--auto", "--no-confirmar"])
            codigo = sc.flujo_principal(args)
            resultado = {
                "ok": codigo == 0,
                "codigo_salida": codigo,
                "mensaje": f"Tarea ejecutada (código {codigo}).",
            }

    except Exception as exc:  # noqa: BLE001
        resultado = {"ok": False, "error": str(exc), "mensaje": f"Excepción durante ejecución: {exc}"}

    return resultado


def procesar_siguiente_tarea(db_path: Optional[str | Path] = None) -> Optional[Dict[str, Any]]:
    """Consume una tarea pendiente, la ejecuta, actualiza la base de datos y notifica."""
    tarea = consumir_tarea(db_path=db_path)
    if not tarea:
        return None

    tarea_id = tarea["id"]
    tipo = tarea.get("tipo", "")
    chat_id = tarea.get("chat_id")
    canal = tarea.get("canal") or "telegram"

    res = ejecutar_tarea(tarea)
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
def _bucle_worker(intervalo_segundos: float = 2.0, db_path: Optional[str | Path] = None) -> None:
    """Bucle continuo del worker consumiendo tareas de la cola."""
    while not _WORKER_PARAR.is_set():
        try:
            tarea_procesada = procesar_siguiente_tarea(db_path=db_path)
            if not tarea_procesada:
                time.sleep(intervalo_segundos)
        except Exception as exc:  # noqa: BLE001
            time.sleep(intervalo_segundos)


def iniciar_worker(
    daemon: bool = True,
    intervalo_segundos: float = 2.0,
    db_path: Optional[str | Path] = None,
) -> threading.Thread:
    """Inicia el worker de la cola de tareas en un hilo secundario."""
    global _WORKER_HILO
    _WORKER_PARAR.clear()
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
    if _WORKER_HILO is not None and _WORKER_HILO.is_alive():
        _WORKER_HILO.join(timeout=timeout)
    _WORKER_HILO = None
