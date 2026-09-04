#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Router (v6.24.0) — orquestación inteligente de modelos para SnapContext.

Clasifica cada tarea por complejidad/tipo y asigna el modelo más adecuado
(Ollama local para tareas simples, Gemini/DeepSeek para intermedias, Claude
para críticas), reduciendo costes sin sacrificar calidad.

Diseño:
- ``clasificar_tarea``  : heurísticas rápidas (sin llamadas a la IA).
- ``seleccionar_modelo``: elige (proveedor, modelo) según ``config.json``
  (sección ``model_routing``). Sin configuración → ``(None, None)``,
  lo que significa "usa el modelo por defecto actual" (compatibilidad total).
- ``enrutar_tarea``     : combina ambas y devuelve un dict listo para usar.

Extensible: basta con añadir la categoría a ``CATEGORIAS`` y su regla en
``config.json`` (``model_routing.<categoria>``).
"""

from typing import Any, Dict, Optional, Tuple

__all__ = [
    "CATEGORIAS", "ROUTING_DEFECTO", "clasificar_tarea",
    "seleccionar_modelo", "enrutar_tarea",
]

# Categorías soportadas (extensibles).
CATEGORIAS: Tuple[str, ...] = (
    "indexacion",              # generar/actualizar índices, embeddings
    "busqueda_semantica",      # búsqueda semántica / selección de archivos
    "planificacion_simple",    # descomponer tareas en pasos
    "edicion_critica",         # editar archivos (cambios en el código)
    "razonamiento_complejo",   # análisis largo, arquitectura, debugging difícil
    "chat_general",            # conversación / consultas genéricas
)

# Sin configuración del usuario NO se reenruta nada: ``(None, None)`` significa
# "mantener el proveedor/modelo por defecto actual" (compatibilidad total).
ROUTING_DEFECTO: Dict[str, Dict[str, Optional[str]]] = {
    categoria: {"provider": None, "model": None}
    for categoria in CATEGORIAS
}

# Raíces verbales / palabras clave por categoría (heurística rápida).
_KW_INDEXACION: Tuple[str, ...] = (
    "indexa", "índice", "indice", "reindexa", "embeddings", "indexar",
)
_KW_BUSQUEDA: Tuple[str, ...] = (
    "busca", "buscar", "búsqueda", "busqueda", "semántica", "semantica",
    "similar", "encuentra", "dónde está", "donde esta", "localiza",
)
_KW_EDICION: Tuple[str, ...] = (
    "arregl", "corrig", "correg", "refactoriz", "añad", "anad", "cambi",
    "elimin", "edita", "renombr", "mueve", "borra", "crea el archivo",
)
_UMBRAL_MEDIA = 12          # palabras → preferencia por edición/planificación


def clasificar_tarea(consulta: Optional[str],
                     contexto: Optional[Dict[str, Any]] = None) -> str:
    """Clasifica ``consulta`` en una de :data:`CATEGORIAS` (rápido, sin IA).

    ``contexto`` (opcional) puede traer pistas del llamador:

      - ``accion``  : acción explícita ("indexar", "busqueda", "plan",
        "edicion", "react", "chat") — tiene prioridad sobre las heurísticas.
      - ``archivos``: lista de archivos a editar → ``edicion_critica``.

    Consulta vacía → ``chat_general``.
    """
    contexto = contexto or {}
    # 1) Pista explícita del llamador (acción del pipeline).
    accion = str(contexto.get("accion") or "").strip().lower()
    mapa_accion = {
        "indexar": "indexacion", "indexacion": "indexacion",
        "busqueda": "busqueda_semantica", "buscar": "busqueda_semantica",
        "seleccion": "busqueda_semantica",
        "plan": "planificacion_simple", "planificar": "planificacion_simple",
        "edicion": "edicion_critica", "editar": "edicion_critica",
        "editor": "edicion_critica",
        "react": "razonamiento_complejo",
        "razonamiento": "razonamiento_complejo",
        "chat": "chat_general",
    }
    if accion in mapa_accion:
        return mapa_accion[accion]

    consulta = str(consulta or "").strip()
    if not consulta:
        return "chat_general"
    baja = consulta.lower()
    num_palabras = len(consulta.split())

    # 2) Archivos a editar → edición crítica.
    if contexto.get("archivos"):
        return "edicion_critica"

    # 3) Heurísticas por palabras clave (orden de especificidad).
    if any(p in baja for p in _KW_INDEXACION):
        return "indexacion"
    if any(p in baja for p in _KW_BUSQUEDA):
        return "busqueda_semantica"
    if any(p in baja for p in _KW_EDICION):
        return "edicion_critica"
    if any(p in baja for p in _KW_PLAN) and num_palabras <= _UMBRAL_LARGA:
        return "planificacion_simple"
    if num_palabras > _UMBRAL_LARGA or any(p in baja for p in _KW_RAZONAMIENTO):
        return "razonamiento_complejo"

    # 4) Consulta corta genérica → chat.
    return "chat_general"
_KW_PLAN: Tuple[str, ...] = (
    "planifica", "plan", "pasos", "descompón", "descompon", "lista de tareas",
)
_KW_RAZONAMIENTO: Tuple[str, ...] = (
    "analiza", "diseña", "disena", "arquitectura", "estrategia", "por qué",
    "porque", "evalúa", "evalua", "compara", "depura", "optimiza",
)
_UMBRAL_LARGA = 60          # palabras → razonamiento_complejo
def _seccion_routing(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extrae la sección ``model_routing`` de ``config`` (tolerante a errores)."""
    if not isinstance(config, dict):
        return {}
    seccion = config.get("model_routing")
    return seccion if isinstance(seccion, dict) else {}


def seleccionar_modelo(categoria: str,
                       config: Optional[Dict[str, Any]] = None
                       ) -> Tuple[Optional[str], Optional[str]]:
    """Devuelve ``(proveedor, modelo)`` para ``categoria``.

    - Config del usuario: ``config["model_routing"][categoria]`` con las claves
      ``provider`` y ``model`` (ver README, sección "Autonomía Multi-Modelo").
    - Sin entrada específica → ``ROUTING_DEFECTO`` → ``(None, None)``, que el
      llamador interpreta como "usa el modelo por defecto actual".
    """
    seccion = _seccion_routing(config)
    entrada = seccion.get(categoria)
    if not isinstance(entrada, dict):
        entrada = ROUTING_DEFECTO.get(categoria) or {"provider": None,
                                                     "model": None}
    proveedor = entrada.get("provider") or None
    modelo = entrada.get("model") or None
    return proveedor, modelo


def enrutar_tarea(consulta: Optional[str],
                  contexto: Optional[Dict[str, Any]] = None,
                  config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Combina clasificación + selección.

    Devuelve ``{"provider", "model", "categoria", "enrutado"}`` donde
    ``enrutado`` es ``False`` cuando no hay configuración (se usa el modelo
    por defecto actual) y ``True`` cuando el router asignó un modelo propio.
    """
    categoria = clasificar_tarea(consulta, contexto)
    proveedor, modelo = seleccionar_modelo(categoria, config)
    return {
        "provider": proveedor,
        "model": modelo,
        "categoria": categoria,
        "enrutado": bool(proveedor),
    }


if __name__ == "__main__":                      # pequeña demo manual
    import json
    import sys
    texto = " ".join(sys.argv[1:]) or "arregla el botón de pago"
    print(json.dumps(enrutar_tarea(texto), ensure_ascii=False, indent=2))