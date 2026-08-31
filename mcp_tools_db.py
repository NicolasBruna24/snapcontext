#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Herramientas MCP nativas de bases de datos (v6.7.0).

Expone herramientas de **solo lectura** para que el agente ReAct, el
planificador y el usuario exploren bases de datos:

- :func:`db_connect`: conexión perezosa a SQLite (nativo), PostgreSQL
  (``psycopg2``) o MySQL (``pymysql``); guardada en contexto de sesión.
- :func:`db_query`: ejecuta consultas de lectura (SELECT/SHOW/DESCRIBE/
  EXPLAIN/PRAGMA) previa validación estricta; pide confirmación en modo
  interactivo salvo ``auto=True``.
- :func:`db_schema`: esquema completo (tablas, columnas).
- :func:`db_disconnect`: cierra la conexión (fin de sesión).

Seguridad: ``es_consulta_solo_lectura`` rechaza modificaciones, múltiples
sentencias y comentarios-truco; límite de filas (``MAX_FILAS``); las
herramientas públicas nunca lanzan: devuelven ``{"ok": False, "error": ...}``.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional

MAX_FILAS = 200
MAX_CELDA = 500

# Palabras clave de lectura permitidas (primera palabra de la consulta).
PALABRAS_LECTURA = {"select", "show", "describe", "explain", "pragma"}

_PALABRA_RE = re.compile(r"[A-Za-z_]+")

_ESTADO = {"conexion": None, "url": "", "driver": ""}
_CANDADO = threading.Lock()

# Hook de confirmación inyectable (para tests); por defecto pregunta en
# terminal. Debe devolver True/False.
_CONFIRMAR: Optional[Any] = None


def _confirmar_defecto(consulta: str) -> bool:
    try:
        respuesta = input(f"¿Ejecutar consulta: {consulta}? (s/n) ").strip().lower()
        return respuesta in ("s", "si", "sí", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _pedir_confirmacion(consulta: str) -> bool:
    func = _CONFIRMAR if _CONFIRMAR is not None else _confirmar_defecto
    try:
        return bool(func(consulta))
    except Exception:                        # noqa: BLE001 — blindaje
        return False


def fijar_confirmador(funcion: Optional[Any]) -> None:
    """Inyecta el callback de confirmación (uso en tests / UI)."""
    global _CONFIRMAR
    _CONFIRMAR = funcion


def es_consulta_solo_lectura(consulta: str) -> bool:
    """¿Es ``consulta`` estrictamente de lectura?

    Acepta una única sentencia cuya primera palabra sea SELECT/SHOW/
    DESCRIBE/EXPLAIN/PRAGMA. Rechaza modificaciones, varias sentencias
    (``;`` interior), comentarios-truco o cadena vacía.
    """
    if not isinstance(consulta, str):
        return False
    texto = consulta.strip()
    if not texto:
        return False
    texto = re.sub(r"--[^\n]*", " ", texto)
    texto = re.sub(r"/\*.*?\*/", " ", texto, flags=re.S)
    texto = texto.strip().rstrip(";").strip()
    if not texto:
        return False
    sin_cadenas = re.sub(r"'[^']*'", "''", texto)
    if ";" in sin_cadenas:
        return False
    primera = _PALABRA_RE.match(texto)
    if not primera:
        return False
    return primera.group(0).lower() in PALABRAS_LECTURA


def _detectar_driver(url: str, driver: Optional[str] = None) -> str:
    """Resuelve el driver: explícito → prefijo de URL → error."""
    if driver:
        return driver.lower()
    bajo = url.lower()
    for prefijo in ("sqlite", "postgresql", "postgres", "mysql"):
        if bajo.startswith(prefijo):
            return "sqlite" if prefijo == "sqlite" else (
                "postgresql" if prefijo.startswith("postgres") else "mysql")
    raise ValueError(
        "No se pudo detectar el driver de la URL. Usa --db-driver "
        "(sqlite, postgresql, mysql) o una URL con prefijo "
        "sqlite:///, postgresql:// o mysql://")


def _ruta_sqlite(url: str) -> str:
    """Extrae la ruta de archivo de una URL sqlite:/// (o ruta directa)."""
    for prefijo in ("sqlite:///", "sqlite://"):
        if url.lower().startswith(prefijo):
            return url[len(prefijo):]
    return url


def _conectar_driver(url: str, driver: str):
    """Abre la conexión según el driver (perezoso, solo cuando se necesita)."""
    if driver == "sqlite":
        return sqlite3.connect(_ruta_sqlite(url), timeout=10)
    if driver in ("postgresql", "postgres"):
        try:
            import psycopg2                     # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL necesita la dependencia opcional psycopg2: "
                "pip install snapcontext[db] (o pip install psycopg2-binary)"
            ) from exc
        return psycopg2.connect(url)
    if driver == "mysql":
        try:
            import pymysql                      # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "MySQL necesita la dependencia opcional pymysql: "
                "pip install snapcontext[db] (o pip install pymysql)"
            ) from exc
        return pymysql.connect(url)
    raise ValueError(f"Driver no soportado: {driver}")


def db_connect(url: str, driver: Optional[str] = None) -> Dict[str, Any]:
    """Conecta a la base de datos y guarda la conexión en la sesión.

    Devuelve ``{"ok": True, "mensaje": "Conectado a <driver>"}`` o
    ``{"ok": False, "error": ...}``. Si ya había conexión, la reemplaza.
    """
    try:
        url = str(url or "").strip()
        if not url:
            return {"ok": False, "error": "Falta la URL de la base de datos."}
        drv = _detectar_driver(url, driver)
        conexion = _conectar_driver(url, drv)
    except Exception as exc:                 # noqa: BLE001 — herramienta
        return {"ok": False, "error": f"Error de conexión: {exc}"}
    with _CANDADO:
        previa = _ESTADO.get("conexion")
        _ESTADO["conexion"] = conexion
        _ESTADO["url"] = url
        _ESTADO["driver"] = drv
    if previa is not None:
        try:
            previa.close()
        except Exception:                    # noqa: BLE001
            pass
    return {"ok": True, "mensaje": f"Conectado a {drv}", "driver": drv}


def _conexion_activa() -> Optional[Any]:
    with _CANDADO:
        return _ESTADO.get("conexion")


def db_query(consulta: str, auto: bool = False,
             confirmar: Optional[Any] = None) -> Dict[str, Any]:
    """Ejecuta una consulta de SOLO LECTURA sobre la conexión de sesión.

    Valida estrictamente la consulta (``es_consulta_solo_lectura``); en modo
    interactivo pide confirmación salvo ``auto=True``. Devuelve
    ``{"ok": True, "resultados": [...], "columnas": [...], "filas": n}`` o
    ``{"ok": False, "error": ...}``. Nunca lanza.
    """
    conexion = _conexion_activa()
    if conexion is None:
        return {"ok": False, "error": "No hay conexión activa. Usa db_connect."}
    try:
        consulta = str(consulta or "").strip()
    except Exception:                        # noqa: BLE001
        return {"ok": False, "error": "Consulta inválida."}
    if not consulta:
        return {"ok": False, "error": "Consulta vacía."}
    if not es_consulta_solo_lectura(consulta):
        return {
            "ok": False,
            "error": ("Solo se permiten consultas de lectura (SELECT, SHOW, "
                      "DESCRIBE, EXPLAIN, PRAGMA). Consultas de modificación "
                      "(INSERT/UPDATE/DELETE/DROP/...) están bloqueadas."),
        }
    # Confirmación en modo interactivo (el auto no pregunta).
    if not auto:
        pedir = confirmar if confirmar is not None else _pedir_confirmacion
        try:
            if not bool(pedir(consulta)):
                return {"ok": False, "error": "Consulta cancelada por el usuario."}
        except Exception:                    # noqa: BLE001
            return {"ok": False, "error": "Consulta cancelada."}
    try:
        cursor = conexion.execute(consulta)
    except Exception as exc:                 # noqa: BLE001 — herramienta
        return {"ok": False, "error": f"Error al ejecutar la consulta: {exc}"}
    try:
        columnas = [str(d[0]) for d in (cursor.description or [])]
        filas = cursor.fetchmany(MAX_FILAS + 1)
    except Exception as exc:                 # noqa: BLE001
        return {"ok": False, "error": f"Error al leer resultados: {exc}"}
    hay_mas = len(filas) > MAX_FILAS
    filas = filas[:MAX_FILAS]
    resultados = []
    for fila in filas:
        resultados.append([
            (str(v)[:MAX_CELDA] if v is not None else None) for v in fila])
    salida = {"ok": True, "resultados": resultados, "columnas": columnas,
              "filas": len(resultados)}
    if hay_mas:
        salida["truncado"] = True
    return salida


def db_schema() -> Dict[str, Any]:
    """Devuelve el esquema completo: tablas con sus columnas (solo lectura)."""
    conexion = _conexion_activa()
    if conexion is None:
        return {"ok": False, "error": "No hay conexión activa. Usa db_connect."}
    try:
        driver = _ESTADO.get("driver", "")
        tablas: List[Dict[str, Any]] = []
        if driver == "sqlite":
            nombres = [r[0] for r in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for nombre in nombres:
                cols = [{"nombre": c[1], "tipo": c[2], "pk": bool(c[5])}
                        for c in conexion.execute(
                            f"PRAGMA table_info('{nombre}')")]
                tablas.append({"nombre": nombre, "columnas": cols})
        elif driver == "mysql":
            nombres = [r[0] for r in conexion.execute("SHOW TABLES")]
            for nombre in nombres:
                cur = conexion.execute(f"DESCRIBE `{nombre}`")
                cols = [{"nombre": c[0], "tipo": c[1], "pk": c[3] == "PRI"}
                        for c in cur.fetchall()]
                tablas.append({"nombre": nombre, "columnas": cols})
        else:                                # postgresql
            cur = conexion.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name")
            nombres = [r[0] for r in cur.fetchall()]
            for nombre in nombres:
                cur = conexion.execute(
                    "SELECT column_name, data_type FROM "
                    "information_schema.columns WHERE table_name=%s "
                    "ORDER BY ordinal_position", (nombre,))
                cols = [{"nombre": c[0], "tipo": c[1]} for c in cur.fetchall()]
                tablas.append({"nombre": nombre, "columnas": cols})
        return {"ok": True, "tablas": tablas}
    except Exception as exc:                 # noqa: BLE001 — herramienta
        return {"ok": False, "error": f"Error al obtener el esquema: {exc}"}


def db_disconnect() -> Dict[str, Any]:
    """Cierra la conexión de sesión (idempotente)."""
    with _CANDADO:
        conexion = _ESTADO.get("conexion")
        _ESTADO["conexion"] = None
        _ESTADO["url"] = ""
        _ESTADO["driver"] = ""
    if conexion is not None:
        try:
            conexion.close()
        except Exception:                    # noqa: BLE001
            pass
    return {"ok": True, "mensaje": "Conexión cerrada."}


def descripcion_herramientas() -> List[str]:
    """Líneas de descripción para el prompt del agente (solo si conectado)."""
    return [
        "db_query(consulta): ejecuta una consulta SQL de SOLO LECTURA "
        "(SELECT/SHOW/DESCRIBE/EXPLAIN/PRAGMA) sobre la base de datos "
        "conectada y devuelve filas y columnas.",
        "db_schema(): devuelve el esquema de la base de datos conectada "
        "(tablas y columnas).",
    ]


def contexto_para_prompt(max_tablas: int = 15) -> str:
    """Resumen del esquema para enriquecer el prompt del planificador."""
    esquema = db_schema()
    if not esquema.get("ok"):
        return ""
    lineas = ["Esquema de la base de datos conectada:"]
    for tabla in esquema["tablas"][:max_tablas]:
        cols = ", ".join(
            f"{c['nombre']} {c.get('tipo', '')}".strip()
            for c in tabla.get("columnas", [])[:12])
        lineas.append(f"- {tabla['nombre']}({cols})")
    return "\n".join(lineas)


def reiniciar() -> None:
    """Cierra conexión y restablece el estado (uso en tests)."""
    db_disconnect()
    fijar_confirmador(None)


