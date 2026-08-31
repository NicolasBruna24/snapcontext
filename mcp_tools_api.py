#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Herramientas MCP nativas de APIs externas (v6.7.0).

Expone herramientas de inspección HTTP para el agente ReAct y el
planificador:

- :func:`api_request`: petición HTTP (GET por defecto; también POST/PUT/
  DELETE/PATCH/HEAD/OPTIONS) con ``httpx``, parsea JSON si procede y trunca
  cuerpos enormes.
- :func:`api_inspect`: GET de análisis con métricas (status, tiempo, tamaño,
  tipo de contenido, servidor).

Seguridad: timeout obligatorio, tamaño máximo de cuerpo (``MAX_CUERPO``),
cabeceras sensibles ocultas en las respuestas y nunca lanza: devuelve
``{"ok": False, "error": ...}``. ``httpx`` es dependencia opcional.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

MAX_CUERPO = 200_000
TIMEOUT_DEFECTO = 15.0

_METODOS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}

# Cabeceras que no se devuelven tal cual (contienen secretos).
_CABECERAS_SENSIBLES = {"authorization", "cookie", "set-cookie",
                        "x-api-key", "proxy-authorization"}


def _validar_url(url: str) -> str:
    """Valida esquema/host de la URL; lanza ``ValueError`` si es inválida."""
    url = str(url or "").strip()
    if not url:
        raise ValueError("Falta la URL.")
    parseado = urlparse(url)
    if parseado.scheme not in ("http", "https"):
        raise ValueError("La URL debe usar http:// o https://")
    if not parseado.netloc:
        raise ValueError("La URL no tiene host válido.")
    return url


def _httpx():
    try:
        import httpx                          # type: ignore
        return httpx
    except ImportError as exc:                # pragma: no cover
        raise RuntimeError(
            "Las herramientas de API necesitan httpx: "
            "pip install snapcontext[web] (o pip install httpx)") from exc


def _filtrar_cabeceras(cabeceras: Any) -> Dict[str, str]:
    """Copia cabeceras ocultando las sensibles."""
    salida: Dict[str, str] = {}
    try:
        for clave, valor in dict(cabeceras).items():
            if str(clave).lower() in _CABECERAS_SENSIBLES:
                salida[str(clave)] = "***"
            else:
                salida[str(clave)] = str(valor)[:200]
    except Exception:                         # noqa: BLE001
        pass
    return salida


def api_request(url: str, metodo: str = "GET",
                headers: Optional[Dict[str, str]] = None,
                body: str = "", timeout: float = TIMEOUT_DEFECTO
                ) -> Dict[str, Any]:
    """Ejecuta una petición HTTP y devuelve status, cabeceras y cuerpo.

    Si la respuesta es JSON válido se incluye en ``json``; el texto siempre
    va en ``body`` (truncado a ``MAX_CUERPO``). Nunca lanza.
    """
    try:
        url = _validar_url(url)
        metodo = str(metodo or "GET").upper()
        if metodo not in _METODOS:
            return {"ok": False, "error": f"Método no soportado: {metodo}"}
        httpx = _httpx()
        cabeceras = {str(k): str(v)[:500] for k, v in (headers or {}).items()}
        inicio = time.monotonic()
        with httpx.Client(timeout=max(1.0, float(timeout)),
                          follow_redirects=True) as cliente:
            respuesta = cliente.request(
                metodo, url, headers=cabeceras,
                content=str(body) if body else None)
        duracion = round(time.monotonic() - inicio, 3)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:                  # noqa: BLE001 — herramienta
        return {"ok": False, "error": f"Error en la petición: {exc}"}
    texto = respuesta.text[:MAX_CUERPO]
    salida: Dict[str, Any] = {
        "ok": True, "status": respuesta.status_code,
        "headers": _filtrar_cabeceras(respuesta.headers),
        "body": texto, "tiempo": duracion, "url": str(respuesta.url),
    }
    tipo = respuesta.headers.get("content-type", "")
    if "json" in tipo.lower():
        try:
            salida["json"] = respuesta.json()
        except Exception:                     # noqa: BLE001
            pass
    return salida


def api_inspect(url: str, timeout: float = TIMEOUT_DEFECTO) -> Dict[str, Any]:
    """GET de análisis: status, tiempo de respuesta, tamaño y metadatos."""
    resultado = api_request(url, "GET", timeout=timeout)
    if not resultado.get("ok"):
        return resultado
    cuerpo = resultado.get("body") or ""
    salida: Dict[str, Any] = {
        "ok": True,
        "status": resultado.get("status"),
        "tiempo": resultado.get("tiempo"),
        "tamaño": len(cuerpo.encode("utf-8", errors="replace")),
        "content_type": resultado.get("headers", {}).get("content-type", ""),
        "server": resultado.get("headers", {}).get("server", ""),
        "es_json": "json" in resultado,
        "truncado": len(cuerpo) >= MAX_CUERPO,
    }
    if "json" in resultado:
        salida["json"] = resultado["json"]
    return salida


def reiniciar() -> None:
    """No hay estado global; presente por simetría (uso en tests)."""
    return None


__all__ = ["api_request", "api_inspect", "reiniciar", "MAX_CUERPO",
           "TIMEOUT_DEFECTO", "_validar_url", "_filtrar_cabeceras"]