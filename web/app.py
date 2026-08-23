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
import threading
import time
from pathlib import Path
from typing import List

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

_ESTATICO = Path(__file__).resolve().parent / "static"
# ``directorio`` por defecto si la UI no lo indica (directorio de trabajo).
_DIRECTORIO_DEFECTO = "."


def crear_app() -> FastAPI:
    """Construye y devuelve la app FastAPI (rutas + WebSocket)."""
    app = FastAPI(title="SnapContext Web", version="1.4.0")

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


__all__ = ["crear_app", "arrancar_servidor"]