#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Interfaz web de SnapContext (FastAPI + WebSockets).

Sirve ``static/index.html`` y expone el endpoint ``/ws``. Cuando el usuario envía
una consulta, lanza el :class:`orquestador.Orquestador` en un hilo y reenvía por
el WebSocket cada evento que este emite (``log``, ``selección``, ``aider``,
``test``, ``final``), para que la UI muestre el avance en tiempo real.
"""

import asyncio
import json
import queue
import threading
import time
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse

# Carpetas del paquete: static/ (interface HTML) junto a este archivo.
_ESTATICO = Path(__file__).resolve().parent / "static"


def crear_app() -> FastAPI:
    """Construye y devuelve la app FastAPI (rutas + WebSocket)."""
    app = FastAPI(title="SnapContext Web", version="0.8.0")

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

        def _evento(dicto: dict) -> None:
            cola.put(dicto)

        try:
            while True:
                datos = await websocket.receive_text()
                mensaje = json.loads(datos)
                consulta = (mensaje.get("consulta") or "").strip()
                if not consulta:
                    await websocket.send_json(
                        {"tipo": "log", "nivel": "error",
                         "texto": "✖ Falta la consulta."}
                    )
                    await websocket.send_json({"tipo": "final", "ok": False})
                    continue

                args = _construir_args(mensaje)
                hilo = threading.Thread(
                    target=_ejecutar_tarea, args=(args, cola), daemon=True
                )
                hilo.start()
                # Reenvío de eventos a la interfaz hasta que termine el hilo.
                while hilo.is_alive() or not cola.empty():
                    while not cola.empty():
                        try:
                            await websocket.send_json(cola.get_nowait())
                        except queue.Empty:
                            break
                    await asyncio.sleep(0.03)
                while not cola.empty():
                    await websocket.send_json(cola.get_nowait())
        except Exception:
            # Conexión cerrada o mensaje inválido; se sale del bucle.
            pass
        finally:
            sc = _importar_snapcontext()
            if sc is not None:
                sc.fijar_evento_callback(None)

    return app


def _importar_snapcontext():
    """Devuelve el módulo snapcontext (import diferido) o None si no se puede."""
    try:
        import snapcontext
        return snapcontext
    except Exception:
        return None


def _ejecutar_tarea(args, cola) -> None:
    """Ejecuta el orquestador en un hilo, enviando sus eventos a la cola.

    Emite además un evento ``inicio`` (para que la UI ponga en marcha el
    cronómetro) y enriquece cada evento reenviado con el tiempo transcurrido
    (clave ``tiempo``) desde el arranque, en segundos.
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