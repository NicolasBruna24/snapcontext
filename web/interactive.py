#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hub de la UI Web interactiva (v6.5.0).

Puente ligero y **no bloqueante** entre el agente (ReAct, editor propio) y la
interfaz web (``web/app.py``) mediante una cola de eventos:

- :func:`activar`: activa el modo interactivo fijando la cola que consume el
  servidor web para difundir eventos por ``/ws/interactive``.
- :func:`emitir`: encola un evento (``put_nowait``; si la cola está llena o el
  modo está inactivo, se descarta silenciosamente para no ralentizar al agente).
- :func:`enviar_paso_react`: emite un paso del timeline ReAct
  (``pensamiento``/``accion``/``observacion``/``error``).
- :func:`enviar_conflicto_diff`: envía un ``diff_conflict`` al navegador y
  **espera** (con ``threading.Event`` y timeout) la respuesta del usuario
  (aceptar/rechazar/contenido editado) que llega por ``_recibir_mensaje``.

Seguridad: los contenidos se validan (cadena, tamaño máximo) antes de
encolarse; el contenido devuelto por el cliente se recorta al tamaño máximo
para evitar abuso, y nunca se ejecuta: solo se escribe en el archivo objetivo.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

# Tamaño máximo de contenido transportado por eventos (protección básica).
MAX_CONTENIDO = 400_000

# Estado del hub (protegido por candado).
_ACTIVO: bool = False
_COLA: Optional["queue.Queue[dict]"] = None
_CANDADO = threading.Lock()

# Recepción de mensajes del navegador: ``_recibir_mensaje`` los reparte al
# hilo que espera una respuesta (diff) a través de este registro.
_ESPERAS: Dict[str, Dict[str, Any]] = {}
_CANDADO_ESPERAS = threading.Lock()

# Callback opcional para mensajes del navegador sin espera asociada.
_CALLBACK_LIBRE: Optional[Callable[[dict], None]] = None


def activar(cola: Optional["queue.Queue[dict]"] = None) -> bool:
    """Activa el modo interactivo y fija la cola de eventos del servidor."""
    global _ACTIVO, _COLA
    with _CANDADO:
        _COLA = cola if cola is not None else queue.Queue()
        _ACTIVO = True
    return True


def desactivar() -> None:
    """Desactiva el modo interactivo y limpia esperas pendientes."""
    global _ACTIVO, _COLA
    with _CANDADO:
        _ACTIVO = False
        _COLA = None
    with _CANDADO_ESPERAS:
        for espera in _ESPERAS.values():
            espera["evento"].set()
        _ESPERAS.clear()


def esta_activo() -> bool:
    """¿Está activo el modo web interactivo?"""
    return _ACTIVO


def cola_eventos() -> Optional["queue.Queue[dict]"]:
    """Cola de eventos consumida por ``web.app`` (o ``None``)."""
    return _COLA


def _validar_texto(valor: Any, maximo: int = MAX_CONTENIDO) -> str:
    """Convierte a texto recortado; lanza ``ValueError`` si no es cadena."""
    if valor is None:
        return ""
    if not isinstance(valor, str):
        raise ValueError("Se esperaba texto (str).")
    return valor[:maximo]


def emitir(tipo: str, **datos: Any) -> bool:
    """Encola un evento para los clientes web. Nunca lanza ni bloquea.

    Devuelve ``True`` si el evento se encoló.
    """
    if not _ACTIVO or _COLA is None:
        return False
    try:
        evento = {"tipo": str(tipo), "timestamp": round(time.time(), 3)}
        for clave, valor in datos.items():
            if isinstance(valor, str):
                evento[clave] = valor[:MAX_CONTENIDO]
            elif isinstance(valor, (int, float, bool)) or valor is None:
                evento[clave] = valor
            else:
                evento[clave] = str(valor)[:MAX_CONTENIDO]
        _COLA.put_nowait(evento)
        return True
    except Exception:                        # noqa: BLE001 — nunca bloquear
        return False


# --------------------------------------------------------------------------
# Timeline de ReAct (v6.5.0)
# --------------------------------------------------------------------------
def enviar_paso_react(iteracion: int, fase: str, contenido: str,
                      **extra: Any) -> bool:
    """Emite un paso del timeline ReAct (``react_step``).

    ``fase`` ∈ ``{"pensamiento", "accion", "observacion", "error", "estado"}``.
    """
    if fase not in ("pensamiento", "accion", "observacion", "error", "estado"):
        fase = "observacion"
    return emitir("react_step", iteracion=int(iteracion), fase=fase,
                  contenido=str(contenido or "")[:MAX_CONTENIDO], **extra)


def enviar_estado(estado: str, detalle: str = "") -> bool:
    """Emite el estado del agente (``agent_status``) para el panel de control."""
    return emitir("agent_status", estado=str(estado)[:120],
                  detalle=str(detalle or "")[:MAX_CONTENIDO])


def enviar_log(nivel: str, texto: str) -> bool:
    """Emite un log estructurado (info/warning/error) para la consola web."""
    if nivel not in ("info", "warning", "error"):
        nivel = "info"
    return emitir("log_interactivo", nivel=nivel,
                  texto=str(texto or "")[:MAX_CONTENIDO])


# --------------------------------------------------------------------------
# Resolución visual de conflictos de parche (diff viewer)
# --------------------------------------------------------------------------
def enviar_conflicto_diff(ruta: str, original: str, propuesto: str,
                          timeout: int = 600) -> Optional[Dict[str, Any]]:
    """Envía un ``diff_conflict`` al navegador y espera la respuesta.

    Devuelve el dict del cliente, p. ej.
    ``{"tipo": "diff_respuesta", "id": ..., "decision": "aceptar"|"rechazar",
    "contenido": "<nuevo texto>"}``, o ``None`` si expira el tiempo, el modo no
    está activo o la respuesta es inválida. Nunca lanza.
    """
    if not esta_activo():
        return None
    try:
        original_t = _validar_texto(original)
        propuesto_t = _validar_texto(propuesto)
    except ValueError:
        return None
    id_conflicto = uuid.uuid4().hex[:12]
    espera = {"evento": threading.Event(), "respuesta": None}
    with _CANDADO_ESPERAS:
        _ESPERAS[id_conflicto] = espera
    try:
        emitido = emitir("diff_conflict", id=id_conflicto, ruta=str(ruta)[:500],
                         original=original_t, propuesto=propuesto_t)
        if not emitido:
            return None
        if espera["evento"].wait(timeout=max(1, int(timeout))):
            return espera["respuesta"]
        return None
    finally:
        with _CANDADO_ESPERAS:
            _ESPERAS.pop(id_conflicto, None)


def _recibir_mensaje(mensaje: dict) -> None:
    """Recibe un mensaje del navegador (lo llama ``/ws/interactive``).

    Reparte ``diff_respuesta`` al hilo que espera ese id; el resto (si hay
    callback libre registrado) se delega. Nunca lanza.
    """
    if not isinstance(mensaje, dict):
        return
    tipo = str(mensaje.get("tipo") or "").strip()
    if tipo == "diff_respuesta":
        id_conflicto = str(mensaje.get("id") or "").strip()
        with _CANDADO_ESPERAS:
            espera = _ESPERAS.get(id_conflicto)
        if espera is None:
            return                      # conflicto ya resuelto o desconocido
        decision = str(mensaje.get("decision") or "").strip().lower()
        if decision not in ("aceptar", "rechazar"):
            return                      # respuesta inválida: se ignora
        contenido = mensaje.get("contenido")
        if not isinstance(contenido, str):
            contenido = ""
        espera["respuesta"] = {
            "tipo": "diff_respuesta", "id": id_conflicto,
            "decision": decision, "contenido": contenido[:MAX_CONTENIDO]}
        espera["evento"].set()
        return
    if _CALLBACK_LIBRE is not None:
        try:
            _CALLBACK_LIBRE(mensaje)
        except Exception:                # noqa: BLE001 — blindaje
            pass


def registrar_callback_libre(callback: Optional[Callable[[dict], None]]) -> None:
    """Registra el callback para mensajes del navegador sin espera asociada."""
    global _CALLBACK_LIBRE
    _CALLBACK_LIBRE = callback


def reiniciar() -> None:
    """Restablece todo el estado del hub (uso en tests)."""
    desactivar()
    registrar_callback_libre(None)


__all__ = ["activar", "desactivar", "esta_activo", "cola_eventos", "emitir",
           "enviar_paso_react", "enviar_estado", "enviar_log",
           "enviar_conflicto_diff", "_recibir_mensaje",
           "registrar_callback_libre", "reiniciar", "MAX_CONTENIDO"]
