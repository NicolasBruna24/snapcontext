#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt Caching por Capas (v6.31.0) — prompt estructurado en capas inmutables.

El caching básico de v6.16.0 añade marcas ``cache_control`` sobre mensajes
sueltos (sistema, herramientas, CLAUDE.md). Este módulo va un paso más allá:
estructura el prompt en TRES CAPAS con orden estricto para maximizar el
prefijo idéntico entre peticiones y activar la caché de la API (Anthropic,
DeepSeek) con la máxima eficiencia:

    1. CAPA ESTÁTICA      : system prompt + definiciones de herramientas.
    2. CAPA SEMI-ESTÁTICA : mapa de dependencias (GraphRAG), memoria del
                            repositorio (CLAUDE.md / SNAPCONTEXT.md), reglas.
    3. CAPA VOLÁTIL       : mensajes recientes (usuario/asistente), resultados
                            de herramientas, diffs de git, estado del sandbox.

Las capas estática y semi-estática son inmutables durante la sesión y reciben
``cache_control: {"type": "ephemeral"}``; la capa volátil se envía sin marcas
(cambia en cada turno).

Diseño:
- ``es_capa_estatica`` / ``es_capa_semi_estatica`` / ``es_capa_volatil``:
  detección por marcadores de contenido/roles (rápida, sin IA). Prioridad:
  estática > semi-estática > volátil (un mensaje solo pertenece a una capa).
- ``clasificar_mensajes``           : reparte una lista plana en las 3 capas.
- ``ensamblar_prompt_estructurado`` : orden estricto + marcas. Nunca muta las
  entradas; devuelve una lista nueva.
- ``metricas_capas``                : tokens estimados por capa (1 token ≈ 4
  caracteres, misma heurística de ``snapcontext._contar_tokens``).

Configuración (``config.json -> prompt_caching.capas``): el usuario puede
ajustar qué partes son estáticas/semi-estáticas/volátiles::

    "prompt_caching": {
      "activo": true,
      "capas": {
        "estatica": ["system", "tools"],
        "semi_estatica": ["claude_md", "graph_rag", "reglas"],
        "volatil": ["user_messages", "tool_results", "diffs"]
      }
    }

Sin configuración se usan los valores por defecto anteriores. Sin este módulo
(el fichero no existe o falla) ``snapcontext`` degrada al caching básico de
v6.16.0 sin cambios de comportamiento.
"""

from typing import Any, Dict, List, Optional

__all__ = [
    "CAPAS_DEFECTO", "MARCA_EFEMERAL",
    "es_capa_estatica", "es_capa_semi_estatica", "es_capa_volatil",
    "clasificar_mensajes", "ensamblar_prompt_estructurado",
    "metricas_capas", "contar_tokens",
]

# Marca estándar de Anthropic/DeepSeek para mensajes cacheables.
MARCA_EFEMERAL: Dict[str, str] = {"type": "ephemeral"}

# Composición por defecto de cada capa (sobrescribible en config.json).
CAPAS_DEFECTO: Dict[str, List[str]] = {
    "estatica": ["system", "tools"],
    "semi_estatica": ["claude_md", "graph_rag", "reglas"],
    "volatil": ["user_messages", "tool_results", "diffs"],
}

# Registro de marcadores por "nombre de parte" (roles y contenido).
# Ligero (solo `in` sobre el contenido): no afecta al prompt, no añade latencia.
_MARCADORES_CAPA: Dict[str, Dict[str, Any]] = {
    # -- capa estática ------------------------------------------------------
    "system": {"roles": ("system",), "marcadores": ()},
    "tools": {"roles": (), "marcadores": (
        "HERRAMIENTAS", "herramienta", "MCP", "editar_archivo",
        "ejecutar_comando")},
    # -- capa semi-estática -------------------------------------------------
    "claude_md": {"roles": (), "marcadores": ("CLAUDE.md", "SNAPCONTEXT.md")},
    "graph_rag": {"roles": (), "marcadores": (
        "GRAFO DE DEPENDENCIAS", "grafo de dependencias",
        "dependencias inversas", "GRAPH_RAG", "mapa de dependencias")},
    "reglas": {"roles": (), "marcadores": (
        "reglas del repositorio", "REGLAS DEL REPOSITORIO", "permisos.json")},
    # -- capa volátil -------------------------------------------------------
    "user_messages": {"roles": ("user", "assistant"), "marcadores": ()},
    "tool_results": {"roles": ("tool",), "marcadores": (
        "resultado de herramienta")},
    "diffs": {"roles": (), "marcadores": (
        "parche unificado", "unified diff", "diff --git")},
}


def _seccion_capas(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extrae ``prompt_caching.capas`` de ``config`` (tolerante a errores).

    ``config`` acepta el dict completo de ``config.json`` o directamente la
    sección ``prompt_caching``. Sin configuración válida → ``{}`` (defectos).
    """
    if not isinstance(config, dict):
        return {}
    seccion = config.get("prompt_caching", config)
    if not isinstance(seccion, dict):
        return {}
    capas = seccion.get("capas")
    return capas if isinstance(capas, dict) else {}


def _nombres_capa(config: Optional[Dict[str, Any]], clave: str) -> List[str]:
    """Nombres de partes que componen la capa ``clave`` (con defectos)."""
    nombres = _seccion_capas(config).get(clave)
    if not isinstance(nombres, (list, tuple)) or not nombres:
        nombres = CAPAS_DEFECTO.get(clave, [])
    return [str(n).strip().lower() for n in nombres if str(n).strip()]


def _mensaje_pertenece(mensaje: Any, nombre: str) -> bool:
    """¿El mensaje encaja con los marcadores del nombre de parte ``nombre``?"""
    if not isinstance(mensaje, dict):
        return False
    regla = _MARCADORES_CAPA.get(nombre)
    if not regla:
        return False
    rol = str(mensaje.get("role") or "").strip().lower()
    if rol in regla["roles"]:
        return True
    contenido = str(mensaje.get("content") or "")
    if not contenido:
        return False
    return any(marcador in contenido or marcador.lower() in contenido.lower()
               for marcador in regla["marcadores"])


def es_capa_estatica(mensaje: Any,
                     config: Optional[Dict[str, Any]] = None) -> bool:
    """¿Pertenece ``mensaje`` a la capa estática (v6.31.0)?

    Capa estática = system prompt + definiciones de herramientas (por defecto:
    ``["system", "tools"]``; configurable en ``prompt_caching.capas``).
    """
    return any(_mensaje_pertenece(mensaje, nombre)
               for nombre in _nombres_capa(config, "estatica"))


def es_capa_semi_estatica(mensaje: Any,
                          config: Optional[Dict[str, Any]] = None) -> bool:
    """¿Pertenece ``mensaje`` a la capa semi-estática (v6.31.0)?

    Capa semi-estática = GraphRAG (mapa de dependencias), memoria del
    repositorio (CLAUDE.md / SNAPCONTEXT.md), reglas (por defecto:
    ``["claude_md", "graph_rag", "reglas"]``). Un mensaje que también encaje
    con la capa estática se considera estático (prioridad estática > semi).
    """
    return (any(_mensaje_pertenece(mensaje, nombre)
                for nombre in _nombres_capa(config, "semi_estatica"))
            and not es_capa_estatica(mensaje, config))


def es_capa_volatil(mensaje: Any,
                    config: Optional[Dict[str, Any]] = None) -> bool:
    """¿Pertenece ``mensaje`` a la capa volátil (v6.31.0)?

    Capa volátil = mensajes de usuario/asistente, resultados de herramientas,
    diffs de git y estado del sandbox: todo lo que NO es estático ni
    semi-estático (cambia en cada turno, no se cachea).
    """
    return (not es_capa_estatica(mensaje, config)
            and not es_capa_semi_estatica(mensaje, config))


def clasificar_mensajes(
        mensajes: List[Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Reparte ``mensajes`` (lista plana) en las 3 capas (v6.31.0).

    Devuelve ``{"estatica": [...], "semi_estatica": [...], "volatil": [...]}``
    preservando el orden relativo dentro de cada capa. Prioridad de capa:
    estática > semi-estática > volátil (un mensaje solo está en una capa).
    El primer mensaje sin ``role`` se considera estático (system prompt,
    misma heurística que ``snapcontext._aplicar_cache_control``). Nunca muta
    ``mensajes``.
    """
    resultado: Dict[str, List[Dict[str, Any]]] = {
        "estatica": [], "semi_estatica": [], "volatil": []}
    for indice, mensaje in enumerate(mensajes or []):
        copia = dict(mensaje) if isinstance(mensaje, dict) else mensaje
        if not isinstance(copia, dict):
            continue                       # entradas no-dict: se ignoran
        es_estatica = (es_capa_estatica(copia, config)
                       or (indice == 0 and not copia.get("role")))
        if es_estatica:
            resultado["estatica"].append(copia)
        elif es_capa_semi_estatica(copia, config):
            resultado["semi_estatica"].append(copia)
        else:
            resultado["volatil"].append(copia)
    return resultado


def contar_tokens(texto: str) -> int:
    """Estimación ligera de tokens (v6.31.0): 1 token ≈ 4 caracteres.

    Misma heurística que ``snapcontext._contar_tokens`` (solo para métricas
    de depuración; nunca para limitar el contexto).
    """
    return max(len(texto) // 4, 0)


def _normalizar_entrada(entrada: Any) -> List[Dict[str, Any]]:
    """Normaliza una entrada a lista de mensajes-dict (v6.31.0).

    Acepta:
      - ``None`` → ``[]``
      - ``str`` → ``[{"role": "system", "content": entrada}]``
      - ``dict`` → ``[entrada]``
      - ``list`` → la misma (solo elementos dict)
    Cualquier otro tipo se ignora (``[]``). Nunca muta la entrada original.
    """
    if entrada is None:
        return []
    if isinstance(entrada, str):
        return [{"role": "system", "content": entrada}] if entrada.strip() else []
    if isinstance(entrada, dict):
        return [entrada]
    if isinstance(entrada, list):
        return [dict(m) for m in entrada if isinstance(m, dict)]
    return []


def ensamblar_prompt_estructurado(
        sistema: Any,
        contexto_estatico: Any,
        contexto_semi_estatico: Any,
        mensajes_recientes: Any,
        config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Ensambla el prompt en orden estricto por capas (v6.31.0).

    Orden garantizado (prefijo idéntico entre peticiones → caché de la API):

        1. Capa ESTÁTICA      : ``sistema`` (system prompt) + ``contexto_estatico``
           (herramientas, etc.).
        2. Capa SEMI-ESTÁTICA : ``contexto_semi_estatico`` (GraphRAG, CLAUDE.md,
           reglas del repositorio).
        3. Capa VOLÁTIL       : ``mensajes_recientes`` (usuario/asistente,
           tool_results, diffs, estado del sandbox).

    Añade ``cache_control: {"type": "ephemeral"}`` a las capas estática y
    semi-estática (inmutables durante la sesión); la volátil se envía sin
    marcas. ``sistema`` acepta str (se envuelve como ``{"role": "system"}``),
    dict o lista; el resto acepta dict, lista o None. Nunca muta las entradas:
    devuelve una lista nueva. ``config`` (opcional) permite ajustar las capas
    vía ``prompt_caching.capas``.
    """
    estatica = _normalizar_entrada(sistema) + _normalizar_entrada(
        contexto_estatico)
    semi = _normalizar_entrada(contexto_semi_estatico)
    volatil = _normalizar_entrada(mensajes_recientes)

    salida: List[Dict[str, Any]] = []
    for mensaje in estatica:
        copia = dict(mensaje)
        copia["cache_control"] = dict(MARCA_EFEMERAL)
        salida.append(copia)
    for mensaje in semi:
        copia = dict(mensaje)
        copia["cache_control"] = dict(MARCA_EFEMERAL)
        salida.append(copia)
    # Capa volátil: sin marcas (cambia en cada turno).
    salida.extend(dict(mensaje) for mensaje in volatil)
    return salida


def metricas_capas(
        mensajes: List[Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Tokens estimados por capa (v6.31.0), para el modo ``--depurar``.

    Devuelve ``{"estatica": X, "semi_estatica": Y, "volatil": Z}`` usando la
    heurística 1 token ≈ 4 caracteres (misma de ``snapcontext._contar_tokens``).
    """
    tokens: Dict[str, int] = {"estatica": 0, "semi_estatica": 0, "volatil": 0}
    clasificados = clasificar_mensajes(mensajes, config)
    for clave, lista in clasificados.items():
        tokens[clave] = sum(
            contar_tokens(str(m.get("content") or ""))
            for m in lista if isinstance(m, dict))
    return tokens
