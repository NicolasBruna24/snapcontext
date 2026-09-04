#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gestor de sesiones persistentes (v6.28.0) — Agente Fantasma.

Mantiene sesiones de agente vivas entre conexiones, permitiendo que multiples
interfaces (TUI, Web, Telegram, Discord) se conecten a la misma sesion.

Las sesiones se guardan en SQLite (~/.snapcontext/sesiones.db) para sobrevivir
a reinicios del daemon.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "Session",
    "SessionManager",
    "obtener_manager",
]

# Configuracion
SESIONES_DB_PATH = Path.home() / ".snapcontext" / "sesiones.db"
TIMEOUT_DEFECTO = 3600  # 1 hora de inactividad


class Session:
    """Sesion persistente del agente."""

    def __init__(self, id: Optional[str] = None,
                 consulta_inicial: Optional[str] = None) -> None:
        self.id: str = id or str(uuid.uuid4())[:8]
        self.historial: List[Dict[str, str]] = []
        self.estado: Dict[str, Any] = {
            "fase": "inactivo",
            "detalle": "",
            "iteracion": 0,
        }
        self.plan: List[Dict[str, Any]] = []
        self.archivos_modificados: List[str] = []
        self.consulta_inicial: Optional[str] = consulta_inicial
        self.creado: str = datetime.now().isoformat()
        self.ultima_actividad: float = time.time()

    def actualizar_estado(self, estado: Dict[str, Any]) -> None:
        """Actualiza el estado de la sesion."""
        self.estado.update(estado)
        self.ultima_actividad = time.time()

    def añadir_mensaje(self, rol: str, contenido: str) -> None:
        """Añade un mensaje al historial."""
        self.historial.append({
            "rol": rol,
            "contenido": contenido,
            "timestamp": datetime.now().isoformat(),
        })
        self.ultima_actividad = time.time()

    def guardar_plan(self, plan: List[Dict[str, Any]]) -> None:
        """Guarda el plan actual."""
        self.plan = list(plan)
        self.ultima_actividad = time.time()

    def añadir_archivo_modificado(self, archivo: str) -> None:
        """Registra un archivo como modificado."""
        if archivo not in self.archivos_modificados:
            self.archivos_modificados.append(archivo)
        self.ultima_actividad = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Serializa la sesion a dict."""
        return {
            "id": self.id,
            "historial": self.historial[-50:],
            "estado": self.estado,
            "plan": self.plan,
            "archivos_modificados": self.archivos_modificados,
            "consulta_inicial": self.consulta_inicial,
            "creado": self.creado,
            "ultima_actividad": self.ultima_actividad,
        }

    def tiempo_inactividad(self) -> float:
        """Segundos desde la ultima actividad."""
        return time.time() - self.ultima_actividad

    def expirado(self, timeout: int = TIMEOUT_DEFECTO) -> bool:
        """True si la sesion excedio el tiempo de inactividad."""
        return self.tiempo_inactividad() > timeout


class SessionManager:
    """Gestiona todas las sesiones activas."""

    def __init__(self, db_path: Optional[Path] = None,
                 timeout: int = TIMEOUT_DEFECTO) -> None:
        self.db_path = db_path or SESIONES_DB_PATH
        self.timeout = timeout
        self._sesiones: Dict[str, Session] = {}
        self._lock = threading.RLock()
        self._asegurar_db()

    def _asegurar_db(self) -> None:
        """Crea la base de datos y la tabla si no existen."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sesiones (
                    id TEXT PRIMARY KEY,
                    consulta_inicial TEXT,
                    historial_json TEXT DEFAULT '[]',
                    estado_json TEXT DEFAULT '{}',
                    plan_json TEXT DEFAULT '[]',
                    archivos_modificados_json TEXT DEFAULT '[]',
                    creado TEXT NOT NULL,
                    ultima_actividad REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sesiones_actividad
                ON sesiones(ultima_actividad DESC)
            """)
            conn.commit()

    def crear_sesion(self, consulta_inicial: Optional[str] = None) -> str:
        """Crea una nueva sesion y devuelve su ID."""
        with self._lock:
            sesion = Session(consulta_inicial=consulta_inicial)
            self._sesiones[sesion.id] = sesion
            self.persistir_sesion(sesion.id)
            return sesion.id

    def obtener_sesion(self, id: str) -> Optional[Session]:
        """Devuelve la session si existe (None si no)."""
        with self._lock:
            sesion = self._sesiones.get(id)
            if sesion is None:
                sesion = self.cargar_sesion(id)
            if sesion is not None:
                sesion.ultima_actividad = time.time()
            return sesion

    def eliminar_sesion(self, id: str) -> bool:
        """Elimina una sesion. True si existia."""
        with self._lock:
            if id in self._sesiones:
                del self._sesiones[id]
                with sqlite3.connect(str(self.db_path)) as conn:
                    conn.execute("DELETE FROM sesiones WHERE id = ?", (id,))
                    conn.commit()
                return True
            return False

    def listar_sesiones(self) -> List[Dict[str, Any]]:
        """Devuelve lista de sesiones activas con metadatos."""
        with self._lock:
            resultado = []
            for id, sesion in self._sesiones.items():
                resultado.append({
                    "id": id,
                    "consulta_inicial": sesion.consulta_inicial,
                    "estado": sesion.estado.get("fase", "inactivo"),
                    "archivos_modificados": len(sesion.archivos_modificados),
                    "mensajes": len(sesion.historial),
                    "inactivo_segundos": int(sesion.tiempo_inactividad()),
                    "expirado": sesion.expirado(self.timeout),
                })
            return resultado

    def persistir_sesion(self, id: str) -> bool:
        """Guarda una sesion en disco."""
        with self._lock:
            sesion = self._sesiones.get(id)
            if sesion is None:
                return False
            try:
                with sqlite3.connect(str(self.db_path)) as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO sesiones
                        (id, consulta_inicial, historial_json, estado_json,
                         plan_json, archivos_modificados_json, creado,
                         ultima_actividad)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        sesion.id,
                        sesion.consulta_inicial,
                        json.dumps(sesion.historial[-100:], ensure_ascii=False),
                        json.dumps(sesion.estado, ensure_ascii=False),
                        json.dumps(sesion.plan, ensure_ascii=False),
                        json.dumps(sesion.archivos_modificados, ensure_ascii=False),
                        sesion.creado,
                        sesion.ultima_actividad,
                    ))
                    conn.commit()
                return True
            except Exception:
                return False

    def cargar_sesion(self, id: str) -> Optional[Session]:
        """Carga una sesion desde disco."""
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.row_factory = sqlite3.Row
                fila = conn.execute(
                    "SELECT * FROM sesiones WHERE id = ?", (id,)
                ).fetchone()
                if fila is None:
                    return None
                sesion = Session(id=fila["id"])
                sesion.consulta_inicial = fila["consulta_inicial"]
                sesion.historial = json.loads(fila["historial_json"] or "[]")
                sesion.estado = json.loads(fila["estado_json"] or "{}")
                sesion.plan = json.loads(fila["plan_json"] or "[]")
                sesion.archivos_modificados = json.loads(
                    fila["archivos_modificados_json"] or "[]"
                )
                sesion.creado = fila["creado"]
                sesion.ultima_actividad = fila["ultima_actividad"]
                self._sesiones[id] = sesion
                return sesion
        except Exception:
            return None

    def limpiar_expiradas(self) -> int:
        """Elimina sesiones que excedieron el timeout."""
        with self._lock:
            expiradas = [
                id for id, s in self._sesiones.items()
                if s.expirado(self.timeout)
            ]
            for id in expiradas:
                self.eliminar_sesion(id)
            return len(expiradas)

    def ejecutar_comando(self, id: str, comando: str) -> Dict[str, Any]:
        """Ejecuta un comando en una sesion."""
        sesion = self.obtener_sesion(id)
        if sesion is None:
            return {"ok": False, "error": "Sesion no encontrada"}
        sesion.añadir_mensaje("user", comando)
        return {"ok": True, "sesion_id": id, "mensaje": comando}


# Singleton global
_manager: Optional[SessionManager] = None


def obtener_manager(timeout: int = TIMEOUT_DEFECTO) -> SessionManager:
    """Devuelve el singleton del SessionManager."""
    global _manager
    if _manager is None:
        _manager = SessionManager(timeout=timeout)
    return _manager
