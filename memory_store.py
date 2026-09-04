#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memoria a largo plazo (v6.26.0) — historial de decisiones en SQLite.

Almacena las decisiones tomadas por el agente (tareas, archivos modificados,
razonamiento, resultados) para reutilizarlas en futuras sesiones.

Integracion:
- ``graph_rag.py``: expandir_contexto incluye decisiones pasadas relevantes.
- ``orquestador.py``: consulta el historial antes de planificar.
- ``react_agent.py``: muestra decisiones similares al inicio de una tarea.

La base de datos es local (``~/.snapcontext/memoria.db``) y no se envia a
ningun servidor externo.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "guardar_decision",
    "buscar_decisiones",
    "obtener_contexto_memoria",
    "listar_decisiones",
    "limpiar_historial",
    "MEMORIA_ACTIVA",
    "MAX_MEMORIA_DEFECTO",
]

# Configuracion por defecto
MAX_MEMORIA_DEFECTO = 100
MEMORIA_ACTIVA = True

# Variable global para activar/desactivar desde CLI
_memoria_activa: bool = True
_max_memoria: int = MAX_MEMORIA_DEFECTO

# Cache de la ruta de la base de datos (misma que snapcontext)
_DB_PATH = None


def _get_db_path() -> Path:
    """Devuelve la ruta de la base de datos SQLite."""
    global _DB_PATH
    if _DB_PATH is None:
        config_dir = Path.home() / ".snapcontext"
        config_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = config_dir / "memoria.db"
    return _DB_PATH


def _obtener_conexion() -> sqlite3.Connection:
    """Devuelve una conexion SQLite (thread-safe)."""
    db_path = _get_db_path()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _asegurar_tabla(conn)
    return conn


def _asegurar_tabla(conn: sqlite3.Connection) -> None:
    """Crea la tabla historial_decisiones si no existe."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historial_decisiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarea TEXT NOT NULL,
            archivos_afectados TEXT,
            descripcion TEXT,
            proveedor TEXT,
            modelo TEXT,
            razonamiento TEXT,
            resultado TEXT,
            metadatos TEXT,
            hash_tarea TEXT UNIQUE,
            creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_historial_creado
        ON historial_decisiones(creado DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_historial_resultado
        ON historial_decisiones(resultado)
    """)
    conn.commit()


def _generar_hash(tarea: str, archivos: List[str]) -> str:
    """Genera un hash unico para evitar duplicados."""
    contenido = f"{tarea}:{sorted(archivos or [])}"
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()[:16]


def configurar_memoria(activa: bool, max_memoria: int = MAX_MEMORIA_DEFECTO) -> None:
    """Configura la memoria a largo plazo."""
    global _memoria_activa, _max_memoria
    _memoria_activa = bool(activa)
    _max_memoria = max(1, int(max_memoria))


def esta_activa() -> bool:
    """True si la memoria a largo plazo esta activa."""
    return _memoria_activa


def contar_decisiones() -> int:
    """Devuelve el numero de decisiones guardadas."""
    if not _memoria_activa:
        return 0
    try:
        conn = _obtener_conexion()
        cursor = conn.execute("SELECT COUNT(*) FROM historial_decisiones")
        return cursor.fetchone()[0]
    except Exception:
        return 0


def guardar_decision(
    tarea: str,
    archivos_afectados: Optional[List[str]] = None,
    descripcion: str = "",
    proveedor: str = "",
    modelo: str = "",
    razonamiento: str = "",
    resultado: str = "exito",
    metadatos: Optional[Dict[str, Any]] = None,
) -> bool:
    """Guarda una decision en el historial."""
    if not _memoria_activa or not tarea:
        return False

    try:
        conn = _obtener_conexion()
        hash_tarea = _generar_hash(tarea, archivos_afectados or [])

        existente = conn.execute(
            "SELECT id FROM historial_decisiones WHERE hash_tarea = ?",
            (hash_tarea,)
        ).fetchone()

        if existente:
            conn.execute("""
                UPDATE historial_decisiones
                SET resultado = ?, creado = CURRENT_TIMESTAMP
                WHERE hash_tarea = ?
            """, (resultado, hash_tarea))
            conn.commit()
            return True

        conn.execute("""
            INSERT INTO historial_decisiones
            (tarea, archivos_afectados, descripcion, proveedor, modelo,
             razonamiento, resultado, metadatos, hash_tarea)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tarea,
            json.dumps(archivos_afectados or [], ensure_ascii=False),
            descripcion,
            proveedor,
            modelo,
            razonamiento[:2000] if razonamiento else "",
            resultado,
            json.dumps(metadatos or {}, ensure_ascii=False),
            hash_tarea,
        ))

        _limitar_historial(conn)
        conn.commit()
        return True
    except Exception:
        return False


def _limitar_historial(conn: sqlite3.Connection) -> None:
    """Elimina entradas antiguas si se supera el limite."""
    count = conn.execute("SELECT COUNT(*) FROM historial_decisiones").fetchone()[0]
    if count > _max_memoria:
        exceso = count - _max_memoria
        conn.execute("""
            DELETE FROM historial_decisiones
            WHERE id IN (
                SELECT id FROM historial_decisiones
                ORDER BY creado ASC
                LIMIT ?
            )
        """, (exceso,))


def buscar_decisiones(
    consulta: str,
    limite: int = 5,
) -> List[Dict[str, Any]]:
    """Busca decisiones relevantes por similitud de texto."""
    if not _memoria_activa or not consulta:
        return []

    try:
        conn = _obtener_conexion()
        palabras = [p for p in consulta.lower().split() if len(p) >= 3]
        if not palabras:
            return []

        condiciones = " OR ".join(
            ["tarea LIKE ? OR descripcion LIKE ? OR archivos_afectados LIKE ?"]
            * len(palabras)
        )
        parametros = []
        for palabra in palabras:
            patron = f"%{palabra}%"
            parametros.extend([patron, patron, patron])

        cursor = conn.execute(f"""
            SELECT * FROM historial_decisiones
            WHERE {condiciones}
            ORDER BY creado DESC
            LIMIT ?
        """, parametros + [limite])

        resultados = []
        for fila in cursor.fetchall():
            resultados.append({
                "id": fila["id"],
                "tarea": fila["tarea"],
                "archivos_afectados": json.loads(fila["archivos_afectados"] or "[]"),
                "descripcion": fila["descripcion"],
                "proveedor": fila["proveedor"],
                "modelo": fila["modelo"],
                "resultado": fila["resultado"],
                "creado": fila["creado"],
            })
        return resultados
    except Exception:
        return []


def obtener_contexto_memoria(consulta: str, limite: int = 3) -> str:
    """Devuelve un resumen textual de decisiones pasadas relevantes."""
    decisiones = buscar_decisiones(consulta, limite=limite)
    if not decisiones:
        return ""

    lineas = ["Decisiones previas similares:"]
    for i, d in enumerate(decisiones, 1):
        resultado = d.get("resultado", "?")
        descripcion = d.get("descripcion") or d.get("tarea", "")
        if len(descripcion) > 100:
            descripcion = descripcion[:97] + "..."
        lineas.append(f"  {i}) [{resultado}] {descripcion}")

    return "\n".join(lineas)


def listar_decisiones(limite: int = 10) -> List[Dict[str, Any]]:
    """Devuelve las ultimas N decisiones."""
    if not _memoria_activa:
        return []

    try:
        conn = _obtener_conexion()
        cursor = conn.execute("""
            SELECT * FROM historial_decisiones
            ORDER BY creado DESC
            LIMIT ?
        """, (limite,))

        return [
            {
                "id": f["id"],
                "tarea": f["tarea"],
                "descripcion": f["descripcion"],
                "resultado": f["resultado"],
                "creado": f["creado"],
            }
            for f in cursor.fetchall()
        ]
    except Exception:
        return []


def limpiar_historial() -> int:
    """Elimina todas las decisiones. Devuelve el numero eliminado."""
    try:
        conn = _obtener_conexion()
        count = conn.execute("SELECT COUNT(*) FROM historial_decisiones").fetchone()[0]
        conn.execute("DELETE FROM historial_decisiones")
        conn.commit()
        return count
    except Exception:
        return 0
