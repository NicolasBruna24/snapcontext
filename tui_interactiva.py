#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TUI interactiva extendida (v6.27.0) — centro de control con edicion de plan
y visualizacion del grafo de dependencias.

Este modulo extiende la TUI basica (tui_app.py) con dos nuevas pestañas:

- **Planificador**: lista interactiva de pasos del plan que permite
  reordenar, eliminar, insertar y editar pasos antes de ejecutarlos.
- **Grafo**: visualizacion del grafo de dependencias (GraphRAG) con
  expansion/colapso de nodos.

Si Textual no esta instalado, las importaciones fallan silenciosamente
y la TUI basica (tui_app.py) sigue funcionando sin estas mejoras.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

__all__ = [
    "esquema_pasos_a_texto",
    "texto_a_esquema_pasos",
    "grafo_a_texto",
    "validar_paso",
]

# Dependencias opcionales
try:
    from textual.widgets import DataTable, ListView, Tree
    from textual.containers import Vertical, Horizontal
    from textual.widgets import Button, Input, Label, Static
    TEXTUAL_DISPONIBLE = True
except Exception:  # pragma: no cover
    TEXTUAL_DISPONIBLE = False


# ---------------------------------------------------------------------------
# Utilidades de formato (no requieren Textual)
# ---------------------------------------------------------------------------

def esquema_pasos_a_texto(pasos: List[Dict[str, Any]]) -> str:
    """Convierte una lista de pasos a texto formateado para mostrar en TUI."""
    lineas = []
    for i, paso in enumerate(pasos, 1):
        estado = paso.get("estado", "pendiente")
        desc = paso.get("descripcion", paso.get("nombre", "Paso sin nombre"))
        icono = {"pendiente": "○", "en_progreso": "◉", "completado": "✓"}.get(estado, "?")
        lineas.append(f"{i:3d}. [{icono}] {desc}")
    return "\n".join(lineas)


def texto_a_esquema_pasos(texto: str) -> List[Dict[str, Any]]:
    """Parsea texto formateado de pasos a lista de dicts."""
    pasos = []
    for linea in texto.strip().splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if ". " in linea[:5]:
            resto = linea.split(". ", 1)[1]
        else:
            resto = linea

        estado = "pendiente"
        desc = resto

        if resto.startswith("[") and "]" in resto:
            fin = resto.index("]")
            marcador = resto[1:fin].strip()
            desc = resto[fin+1:].strip()
            if marcador in ("✓", "completado"):
                estado = "completado"
            elif marcador in ("◉", "en_progreso"):
                estado = "en_progreso"

        pasos.append({"descripcion": desc, "estado": estado})
    return pasos


def grafo_a_texto(grafo: Dict[str, Any], expandir: bool = True) -> str:
    """Convierte un grafo (GraphRAG) a representacion textual."""
    if not grafo:
        return "(grafo vacio)"

    nodos = grafo.get("nodos", {})
    aristas = grafo.get("aristas", [])

    if not nodos and not aristas:
        return "(grafo vacio)"

    lineas = []
    archivos: Dict[str, List[str]] = {}
    for nodo_id, info in nodos.items():
        archivo = nodo_id.split("::")[0] if "::" in nodo_id else "general"
        archivos.setdefault(archivo, []).append(nodo_id)

    if not archivos and aristas:
        for arista in aristas:
            origen = arista.get("origen", "").split("::")[0]
            destino = arista.get("destino", "").split("::")[0]
            if origen:
                archivos.setdefault(origen, [])
            if destino:
                archivos.setdefault(destino, [])

    for archivo, nodos_archivo in sorted(archivos.items()):
        lineas.append(f"[bold cyan]{archivo}[/]")
        if expandir:
            for nodo in sorted(nodos_archivo):
                nombre = nodo.split("::")[-1] if "::" in nodo else nodo
                tipo = nodos.get(nodo, {}).get("tipo", "desconocido") if isinstance(nodos.get(nodo), dict) else "nodo"
                icono = {"funcion": "f", "clase": "C", "archivo": ""}.get(tipo, "?")
                lineas.append(f"  {icono} {nombre}")

    return "\n".join(lineas)


def validar_paso(paso: Dict[str, Any]) -> tuple:
    """Valida que un paso tenga la estructura minima requerida."""
    if not isinstance(paso, dict):
        return False, "El paso debe ser un diccionario"
    if not paso.get("descripcion"):
        return False, "El paso debe tener una descripcion"
    if len(paso.get("descripcion", "")) > 500:
        return False, "Descripcion demasiado larga (max 500 chars)"
    return True, ""


# ---------------------------------------------------------------------------
# Widgets interactivos (requieren Textual)
# ---------------------------------------------------------------------------

if TEXTUAL_DISPONIBLE:
    from textual.widget import Widget
    from textual.message import Message
    from textual.reactive import reactive

    class ListaPasosInteractiva(ListView):
        """Lista de pasos editable con teclas rapidas.

        Teclas:
            up/down : mover seleccion
            d       : eliminar paso seleccionado
            i       : insertar paso despues del seleccionado
            e       : editar descripcion del paso seleccionado
        """

        pasos: reactive[List[Dict[str, Any]]] = reactive(list, always_update=True)

        class PasosModificados(Message):
            """Se envia cuando el usuario modifica la lista de pasos."""
            def __init__(self, pasos: List[Dict[str, Any]]) -> None:
                super().__init__()
                self.pasos = pasos

        def __init__(self, pasos: Optional[List[Dict[str, Any]]] = None,
                     **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.pasos = list(pasos or [])
            self._indice = 0

        def compose(self) -> Any:
            if not self.pasos:
                yield Label("(No hay pasos. Presiona 'i' para insertar)")
            for i, paso in enumerate(self.pasos):
                estado = paso.get("estado", "pendiente")
                desc = paso.get("descripcion", "Sin descripcion")
                icono = {"pendiente": "○", "en_progreso": "◉", "completado": "✓"}.get(estado, "?")
                yield Label(f"{i+1}. [{icono}] {desc}", id=f"paso-{i}")

        def on_key(self, event: Any) -> None:
            key = event.key
            if key == "up":
                self._indice = max(0, self._indice - 1)
            elif key == "down":
                self._indice = min(len(self.pasos) - 1, self._indice + 1)
            elif key == "d":
                self._eliminar_paso()
            elif key == "i":
                self._insertar_paso()

        def _eliminar_paso(self) -> None:
            if self.pasos and 0 <= self._indice < len(self.pasos):
                self.pasos.pop(self._indice)
                self._indice = min(self._indice, len(self.pasos) - 1)
                self.refresh()
                self.post_message(self.PasosModificados(self.pasos))

        def _insertar_paso(self) -> None:
            nuevo = {"descripcion": "Nuevo paso", "estado": "pendiente"}
            self.pasos.insert(self._indice + 1, nuevo)
            self._indice += 1
            self.refresh()
            self.post_message(self.PasosModificados(self.pasos))


    class VistaGrafo(Tree):
        """Visualizacion del grafo de dependencias con expansion/colapso."""

        def __init__(self, grafo: Optional[Dict[str, Any]] = None,
                     **kwargs: Any) -> None:
            super().__init__("Grafo", **kwargs)
            self.grafo = grafo or {}
            self._cargar_grafo()

        def _cargar_grafo(self) -> None:
            if not self.grafo:
                return
            nodos = self.grafo.get("nodos", {})
            for nodo_id, info in nodos.items():
                partes = nodo_id.split("::")
                archivo = partes[0] if partes else "general"
                nombre = partes[-1] if len(partes) > 1 else nodo_id
                if archivo != nombre:
                    try:
                        rama = self.root.add(archivo, expand=True)
                        rama.add_leaf(nombre)
                    except Exception:
                        pass
                else:
                    try:
                        self.root.add_leaf(nombre)
                    except Exception:
                        pass

        def actualizar(self, grafo: Dict[str, Any]) -> None:
            self.grafo = grafo
            self.root.remove_children()
            self._cargar_grafo()
