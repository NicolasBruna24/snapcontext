#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integración Graph RAG + LSP (v6.33.0) — contexto preciso por símbolos.

El Graph RAG (v5.5.0) mapea dependencias entre archivos; el cliente LSP
(v6.14.0) resuelve definiciones/referencias exactas. Este módulo los une:

1. Usa LSP para obtener símbolos exactos (definiciones, referencias, tipos).
2. Usa Graph RAG para priorizar qué símbolos son más relevantes.
3. Inyecta solo los símbolos relevantes en el prompt (no archivos completos).

Es 100 % opcional (``--graph-rag-lsp``): sin el flag el comportamiento es
idéntico al actual. Las llamadas al LSP son perezosas y con caché.
"""

from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "simbolos_defecto",
    "obtener_contexto_preciso",
    "inyectar_contexto_preciso",
    "configuracion_graph_lsp",
    "GraphLSPIntegrator",
]

# Configuración por defecto.
simbolos_defecto: Dict[str, Any] = {
    "activo": False,
    "profundidad": 2,
    "simbolos_max": 10,
}


def configuracion_graph_lsp(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Devuelve la configuración efectiva Graph RAG + LSP (v6.33.0).

    Fusiona los valores por defecto con ``config["graph_lsp"]`` si existe.
    """
    efectiva = dict(simbolos_defecto)
    if not isinstance(config, dict):
        return efectiva
    seccion = config.get("graph_lsp")
    if not isinstance(seccion, dict):
        return efectiva
    if "activo" in seccion:
        efectiva["activo"] = bool(seccion["activo"])
    if "profundidad" in seccion:
        try:
            efectiva["profundidad"] = max(1, int(seccion["profundidad"]))
        except (TypeError, ValueError):
            pass
    if "simbolos_max" in seccion:
        try:
            efectiva["simbolos_max"] = max(1, int(seccion["simbolos_max"]))
        except (TypeError, ValueError):
            pass
    return efectiva


class GraphLSPIntegrator:
    """Integra Graph RAG y LSP para obtener contexto preciso (v6.33.0).

    Uso típico::

        integ = GraphLSPIntegrator(grafo, config)
        simbolos = integ.obtener_contexto_preciso("main.py", 42, "funcion")
        prompt = integ.inyectar_contexto_preciso(contexto_actual, simbolos)
    """

    def __init__(
        self,
        grafo: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        proveedor_lsp: Optional[Any] = None,
    ):
        self.grafo = grafo or {}
        self.config = configuracion_graph_lsp(config)
        self.proveedor_lsp = proveedor_lsp
        self._cache_simbolos: Dict[str, Any] = {}

    def obtener_contexto_preciso(
        self,
        archivo: str,
        linea: Optional[int] = None,
        tipo: str = "funcion",
        max_simbolos: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Obtiene símbolos precisos de un archivo (v6.33.0).

        Usa LSP para definiciones/referencias y Graph RAG para expandir el
        contexto con dependencias. Devuelve una lista de símbolos con su
        contenido, priorizados por relevancia (frecuencia de llamadas).
        """
        max_sim = max_simbolos or self.config.get("simbolos_max", 10)
        profundidad = self.config.get("profundidad", 2)
        cache_key = f"{archivo}:{linea}:{tipo}:{max_sim}:{profundidad}"
        if cache_key in self._cache_simbolos:
            return self._cache_simbolos[cache_key]
        simbolos: List[Dict[str, Any]] = []
        lsp = self._obtener_simbolos_lsp(archivo, linea)
        if lsp:
            simbolos.extend(lsp)
        expandidos = self._expandir_con_grafo(archivo, profundidad, max_sim)
        nombres_existentes = {s.get("nombre") for s in simbolos}
        for sim in expandidos:
            if sim.get("nombre") not in nombres_existentes:
                simbolos.append(sim)
                nombres_existentes.add(sim.get("nombre"))
        simbolos.sort(key=lambda s: s.get("relevancia", 0), reverse=True)
        resultado = simbolos[:max_sim]
        self._cache_simbolos[cache_key] = resultado
        return resultado

    def _obtener_simbolos_lsp(
        self,
        archivo: str,
        linea: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Obtiene símbolos vía LSP con caché (v6.33.0)."""
        cache_key = f"{archivo}:{linea}"
        if cache_key in self._cache_simbolos:
            return self._cache_simbolos[cache_key]
        simbolos: List[Dict[str, Any]] = []
        try:
            import lsp_client as lsp
            if self.proveedor_lsp is not None and hasattr(self.proveedor_lsp, "obtener_simbolos"):
                crudos = self.proveedor_lsp.obtener_simbolos(archivo, linea)
            elif hasattr(lsp, "obtener_simbolos"):
                crudos = lsp.obtener_simbolos(archivo, linea)
            else:
                crudos = []
            for raw in crudos if isinstance(crudos, (list, tuple)) else []:
                if isinstance(raw, dict):
                    simbolos.append(raw)
                elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
                    simbolos.append({
                        "nombre": str(raw[0]),
                        "linea": int(raw[1]) if len(raw) > 1 else 0,
                        "archivo": archivo,
                        "tipo": str(raw[2]) if len(raw) > 2 else "simbolo",
                        "relevancia": int(raw[3]) if len(raw) > 3 else 0,
                    })
        except Exception:
            pass
        self._cache_simbolos[cache_key] = simbolos
        return simbolos

    def _expandir_con_grafo(
        self,
        archivo: str,
        profundidad: int,
        max_sim: int,
    ) -> List[Dict[str, Any]]:
        """Usa Graph RAG para expandir contexto con dependencias (v6.33.0)."""
        simbolos: List[Dict[str, Any]] = []
        try:
            import graph_rag as gr
            if not self.grafo or not hasattr(gr, "expandir_contexto"):
                return simbolos
            vecinos = []
            if hasattr(gr, "obtener_vecinos"):
                vecinos = gr.obtener_vecinos(self.grafo, archivo, profundidad)
            elif hasattr(gr, "expandir_contexto"):
                vecinos = gr.expandir_contexto([archivo], self.grafo,
                                                max_adicionales=max_sim)
                vecinos = [v for v in vecinos if v != archivo]
            for vecino in vecinos[:max_sim]:
                simbolos.append({
                    "nombre": vecino,
                    "archivo": vecino,
                    "linea": 1,
                    "tipo": "dependencia",
                    "contenido": "",
                    "relevancia": 1,
                })
        except Exception:
            pass
        return simbolos

    def inyectar_contexto_preciso(
        self,
        contexto_actual: str,
        simbolos: List[Dict[str, Any]],
    ) -> str:
        """Inyecta símbolos precisos en el contexto (v6.33.0).

        Reemplaza archivos completos por los símbolos extraídos. Si no hay
        símbolos, devuelve el contexto_actual sin cambios.
        """
        if not simbolos:
            return contexto_actual
        lineas = ["# Contexto preciso (Graph RAG + LSP):"]
        for sim in simbolos:
            nombre = sim.get("nombre", "?")
            archivo = sim.get("archivo", "?")
            linea = sim.get("linea", 1)
            tipo = sim.get("tipo", "simbolo")
            contenido = sim.get("contenido", "")
            prefijo = f"  - {tipo} {nombre} ({archivo}:{linea})"
            if contenido:
                contenido_lineas = str(contenido).splitlines()[:5]
                prefijo += ":\n      " + "\n      ".join(contenido_lineas)
            lineas.append(prefijo)
        lineas.append("")
        if contexto_actual:
            lineas.append("# Contexto previo (parcial):")
            lineas.append(contexto_actual[:500])
        return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Funciones standalone
# ---------------------------------------------------------------------------
def obtener_contexto_preciso(
    archivo: str,
    linea: Optional[int] = None,
    tipo: str = "funcion",
    grafo: Optional[Dict[str, Any]] = None,
    max_simbolos: int = 10,
    config: Optional[Dict[str, Any]] = None,
    proveedor_lsp: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Función standalone: obtiene símbolos precisos (v6.33.0)."""
    integ = GraphLSPIntegrator(grafo=grafo, config=config,
                               proveedor_lsp=proveedor_lsp)
    return integ.obtener_contexto_preciso(archivo, linea, tipo, max_simbolos)


def inyectar_contexto_preciso(
    contexto_actual: str,
    simbolos: List[Dict[str, Any]],
) -> str:
    """Función standalone: inyecta símbolos precisos (v6.33.0)."""
    integ = GraphLSPIntegrator()
    return integ.inyectar_contexto_preciso(contexto_actual, simbolos)