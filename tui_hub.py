#!/usr/bin/env python3
"""Hub de la TUI inmersiva (v6.12.0).

Puente ligero y **no bloqueante** entre el agente (ReAct, editor propio) y la
TUI (``tui_app.py``) mediante una cola de eventos. Mismo patrón que
``web/interactive.py`` pero sin servidor: la TUI consume la cola directamente.

- :func:`activar`: activa el modo TUI fijando la cola que consume la app.
- :func:`emitir`: encola un evento (``put_nowait``; si la cola está llena o el
  modo está inactivo, se descarta silenciosamente para no ralentizar al agente).
- :func:`enviar_log` / :func:`enviar_paso_react` / :func:`enviar_estado` /
  :func:`enviar_diff` / :func:`enviar_fin`: helpers tipados.

Eventos emitidos (dict):
  ``{"tipo": "log", "nivel": "info|warning|error", "texto": "..."}``
  ``{"tipo": "react_step", "iteracion": n, "fase": ..., "contenido": "..."}``
  ``{"tipo": "estado", "estado": "...", "detalle": "..."}``
  ``{"tipo": "diff", "ruta": "...", "diff": "..."}``
  ``{"tipo": "fin", "ok": bool, "resultado": "..."}``

Seguridad: los contenidos se validan (cadena, tamaño máximo) antes de encolarse.
Este módulo NO depende de Textual, por lo que se puede probar y reutilizar sin
abrir ninguna interfaz.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

# Tamaño máximo de contenido transportado por eventos (protección básica).
MAX_CONTENIDO = 400_000

# Estado del hub (protegido por candado).
_ACTIVO: bool = False
_COLA: queue.Queue[dict] | None = None
_CANDADO = threading.Lock()

NIVELES_VALIDOS = ("info", "warning", "error")
FASES_VALIDAS = ("pensamiento", "accion", "observacion", "error", "estado")


def activar(cola: queue.Queue[dict] | None = None) -> bool:
    """Activa el modo TUI y fija la cola de eventos que consume la app."""
    global _ACTIVO, _COLA
    with _CANDADO:
        _COLA = cola if cola is not None else queue.Queue()
        _ACTIVO = True
    return True


def desactivar() -> None:
    """Desactiva el modo TUI y limpia la cola."""
    global _ACTIVO, _COLA
    with _CANDADO:
        _ACTIVO = False
        _COLA = None


def esta_activo() -> bool:
    """¿Está activo el modo TUI?"""
    return _ACTIVO


def cola_eventos() -> queue.Queue[dict] | None:
    """Cola de eventos consumida por la TUI (o ``None``)."""
    return _COLA


def emitir(tipo: str, **datos: Any) -> bool:
    """Encola un evento para la TUI. Nunca lanza ni bloquea.

    Devuelve ``True`` si el evento se encoló.
    """
    if not _ACTIVO or _COLA is None:
        return False
    try:
        evento: dict = {"tipo": str(tipo), "timestamp": round(time.time(), 3)}
        for clave, valor in datos.items():
            if isinstance(valor, str):
                evento[clave] = valor[:MAX_CONTENIDO]
            elif isinstance(valor, (int, float, bool)) or valor is None:
                evento[clave] = valor
            else:
                evento[clave] = str(valor)[:MAX_CONTENIDO]
        _COLA.put_nowait(evento)
        return True
    except Exception:
        return False


def enviar_log(nivel: str, texto: str) -> bool:
    """Emite un log estructurado (info/warning/error) para la pestaña Logs."""
    if nivel not in NIVELES_VALIDOS:
        nivel = "info"
    return emitir("log", nivel=nivel, texto=str(texto or "")[:MAX_CONTENIDO])


def enviar_paso_react(iteracion: int, fase: str, contenido: str, **extra: Any) -> bool:
    """Emite un paso del timeline ReAct (``react_step``).

    ``fase`` ∈ ``{"pensamiento", "accion", "observacion", "error", "estado"}``.
    """
    if fase not in FASES_VALIDAS:
        fase = "observacion"
    return emitir(
        "react_step",
        iteracion=int(iteracion),
        fase=fase,
        contenido=str(contenido or "")[:MAX_CONTENIDO],
        **extra,
    )


def enviar_estado(estado: str, detalle: str = "") -> bool:
    """Emite el estado del agente (``estado``) para el panel de control."""
    return emitir("estado", estado=str(estado)[:120], detalle=str(detalle or "")[:MAX_CONTENIDO])


def enviar_diff(ruta: str, diff: str) -> bool:
    """Emite un diff generado por el editor propio para la pestaña Diffs."""
    return emitir("diff", ruta=str(ruta)[:500], diff=str(diff or "")[:MAX_CONTENIDO])


def enviar_fin(ok: bool, resultado: str) -> bool:
    """Emite la finalización del agente (``fin``)."""
    return emitir("fin", ok=bool(ok), resultado=str(resultado or "")[:MAX_CONTENIDO])


def reiniciar() -> None:
    """Restablece todo el estado del hub (uso en tests)."""
    desactivar()


__all__ = [
    "FASES_VALIDAS",
    "MAX_CONTENIDO",
    "NIVELES_VALIDOS",
    "activar",
    "cola_eventos",
    "desactivar",
    "emitir",
    "enviar_diff",
    "enviar_estado",
    "enviar_fin",
    "enviar_log",
    "enviar_paso_react",
    "esta_activo",
    "reiniciar",
]
