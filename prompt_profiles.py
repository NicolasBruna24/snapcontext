"""Perfiles de prompt optimizados por modelo (Fase 17).

Selecciona un prompt y parámetros específicos según el proveedor/modelo en
uso y el tipo de tarea (simple, planificación, edición, ...). Es **solo**
generación de prompt: nunca cambia la lógica de los proveedores.

Cada perfil tiene:

- ``system_prompt``        : instrucciones de sistema para ese modelo.
- ``user_prompt_template`` : plantilla del mensaje de usuario (placeholders
                             ``{consulta}`` y ``{contexto}``).
- ``config``               : parámetros adicionales (``temperature``,
                             ``max_tokens``, ``cache_control``...).
- ``variantes`` (opcional) : overrides de system prompt para tipos de tarea
                             concretos (``planificacion``, ``edicion``...).

Reglas:

- ``obtener_perfil`` normaliza el proveedor (``anthropic`` → ``claude``) y
  devuelve el perfil específico o ``PERFIL_GENERICO`` como fallback (nunca
  rompe el flujo).
- ``aplicar_perfil`` inyecta el ``system_prompt`` y ajusta el mensaje de
  usuario **solo** en tareas conversacionales. Para tareas estructuradas
  (plan/edición/AST/QA) NO reformatea el prompt del usuario para no romper el
  formato JSON esperado por el parser posterior.
- Los usuarios pueden personalizar (o añadir proveedores) sin modificar el
  código central: se cargan perfiles desde el campo ``prompt_profiles`` de
  ``config.json`` (merge sobre los valores por defecto).
"""

from __future__ import annotations

import copy
from typing import Any

# Alias de nombre de proveedor → clave del diccionario PERFILES. Claude se
# registra como "anthropic" en PROVEEDORES, pero su perfil vive bajo "claude".
ALIASES = {
    "anthropic": "claude",
    "claude": "claude",
    "openai": "openai",
    "openai-compatible": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "deepseek": "deepseek",
    "ollama": "ollama",
    "local": "ollama",
    "xpu": "xpu",
    "groq": "groq",
}

# Tipos de tarea reconocidos por ``obtener_perfil``.
TIPOS_TAREA = (
    "general",
    "simple",
    "planificacion",
    "edicion",
    "edicion_critica",
    "razonamiento",
    "razonamiento_complejo",
    "qa",
    "test",
)

# Tareas cuyo prompt de usuario está estructurado (JSON/AST) y NO debe
# reescribirse con la plantilla del perfil (solo se aplica el system_prompt).
_TAREAS_NO_REFORMATEAR = {
    "plan",
    "planificacion",
    "edicion",
    "edicion_critica",
    "ast",
    "editor",
    "test",
    "qa",
    "razonamiento",
    "razonamiento_complejo",
    "llamada_de_reparacion",
    "reparacion",
}


def _normaliza_tipo_tarea(t: str | None) -> str:
    """Normaliza una etiqueta de tarea/`categoria` a un tipo de perfil.

    Acepta etiquetas internas (por ejemplo de ``model_router``: ``edicion_critica``,
    ``planificacion_simple``) y las mapea a los tipos de ``TIPOS_TAREA``.
    """
    if not t:
        return "general"
    clave = str(t).strip().lower().replace(" ", "_").replace("-", "_")
    if clave in TIPOS_TAREA:
        return clave
    # Coincidencias por prefijos de categorías internas.
    for base in ("planificacion", "edicion", "razonamiento"):
        if clave.startswith(base):
            return base
    if clave.startswith("test"):
        return "test"
    if clave.startswith("qa"):
        return "qa"
    if clave in ("chat", "react", "conversacion", "simple"):
        return "simple"
    if "edicion" in clave or "edit" in clave:
        return "edicion"
    if "plan" in clave:
        return "planificacion"
    return "general"


PERFIL_GENERICO: dict[str, Any] = {
    "system_prompt": (
        "Eres un asistente de programación experto de SnapContext. "
        "Responde de forma clara, correcta y útil, justificando las decisiones "
        "importantes. Si editas código, usa bloques de búsqueda y reemplazo."
    ),
    "user_prompt_template": "Contexto:\n{contexto}\n\nConsulta:\n{consulta}",
    "config": {"temperature": 0.3, "max_tokens": 2048, "cache_control": False},
    "variantes": {},
}


PERFILES: dict[str, dict[str, Any]] = {
    "claude": {
        "system_prompt": (
            "Eres un asistente de programación experto. Usa tu capacidad de "
            "razonamiento profundo para analizar el problema antes de responder. "
            "Si necesitas editar código, usa bloques de búsqueda y reemplazo "
            "(fuzzy). Piensa paso a paso cuando la tarea sea compleja."
        ),
        "user_prompt_template": "Contexto:\n{contexto}\n\nConsulta:\n{consulta}",
        "config": {
            "temperature": 0.2,
            "max_tokens": 4096,
            # v6.34+: ayuda a activar el prompt caching de Anthropic.
            "cache_control": True,
        },
        "variantes": {
            "planificacion": {
                "system_prompt": (
                    "Eres un arquitecto que descompone tareas en pasos de "
                    "ejecución concisos. Devuelve el plan SOLO en el formato "
                    "JSON que se te pide, sin explicaciones fuera del JSON."
                ),
            },
        },
    },
    "gemini": {
        "system_prompt": (
            "Eres un asistente de programación útil. Responde de forma clara y "
            "directa, con ejemplos concretos. Evita rodeos; ve al grano."
        ),
        "user_prompt_template": "Pregunta: {consulta}\n\nContexto:\n{contexto}",
        "config": {"temperature": 0.3, "max_tokens": 2048, "cache_control": False},
        "variantes": {},
    },
    "openai": {
        "system_prompt": (
            "You are an expert programming assistant. Be concise and precise. "
            "When editing, use search-and-replace blocks."
        ),
        "user_prompt_template": "Context:\n{contexto}\n\nRequest:\n{consulta}",
        "config": {"temperature": 0.2, "max_tokens": 2048, "cache_control": False},
        "variantes": {},
    },
    "deepseek": {
        "system_prompt": (
            "Eres un asistente de programación experto. Sé preciso y eficiente. "
            "Devuelve código correcto y explica poco cuando no se te pida."
        ),
        "user_prompt_template": "Consulta:\n{consulta}\n\nContexto:\n{contexto}",
        "config": {"temperature": 0.1, "max_tokens": 2048, "cache_control": True},
        "variantes": {},
    },
    "ollama": {
        "system_prompt": (
            "Responde de forma concisa y directa. Si no estás seguro, dilo. "
            "No divagues ni inventes información."
        ),
        "user_prompt_template": "Pregunta: {consulta}\n\nContexto: {contexto}",
        "config": {"temperature": 0.1, "max_tokens": 1024, "cache_control": False},
        "variantes": {},
    },
    "xpu": {
        "system_prompt": (
            "Responde de forma breve y concisa. Contexto muy reducido; no "
            "alucines. Prioriza la simplicidad."
        ),
        "user_prompt_template": "Consulta: {consulta}\nContexto: {contexto}",
        "config": {"temperature": 0.1, "max_tokens": 500, "cache_control": False},
        "variantes": {},
    },
    "groq": {
        "system_prompt": (
            "Eres un asistente de programación experto y directo. Responde "
            "con código correcto y sin rodeos."
        ),
        "user_prompt_template": "Consulta: {consulta}\n\nContexto:\n{contexto}",
        "config": {"temperature": 0.2, "max_tokens": 2048, "cache_control": False},
        "variantes": {},
    },
}


def _merge_profundos(base: dict, extra: dict) -> dict:
    """Fusiona ``extra`` sobre ``base`` recursivamente (dicts anidados)."""
    result = copy.deepcopy(base)
    for clave, valor in (extra or {}).items():
        if isinstance(valor, dict) and isinstance(result.get(clave), dict):
            result[clave] = _merge_profundos(result[clave], valor)
        else:
            result[clave] = copy.deepcopy(valor)
    return result


def _perfiles_personalizados(config: dict | None = None) -> dict:
    """Perfiles definidos por el usuario (campo ``prompt_profiles`` de config).

    Permite sobreescribir o añadir perfiles por proveedor sin tocar el código.
    """
    datos: dict = {}
    try:
        if config is None:
            import configuracion as _cfg

            config = _cfg.cargar_configuracion()
        seccion = (config or {}).get("prompt_profiles") or {}
        if isinstance(seccion, dict):
            datos = seccion
    except Exception:
        datos = {}
    return datos


def obtener_perfil(
    proveedor: str,
    tipo_tarea: str | None = None,
    personalizados: dict | None = None,
) -> dict[str, Any]:
    """Devuelve el perfil de prompt para ``proveedor`` y ``tipo_tarea``.

    Normaliza el nombre del proveedor (p. ej. ``anthropic`` → ``claude``) y el
    tipo de tarea. Si el proveedor no tiene perfil, usa ``PERFIL_GENERICO``.
    Nunca lanza. Devuelve siempre un dict con ``system_prompt``,
    ``user_prompt_template``, ``config`` y ``variantes``.
    """
    tipo = _normaliza_tipo_tarea(tipo_tarea)
    clave = str(proveedor or "").strip().lower()
    clave = ALIASES.get(clave, clave)

    perfil = copy.deepcopy(PERFILES.get(clave) or PERFIL_GENERICO)

    # Merge de personalizaciones del usuario (por alias o por nombre).
    usuario = (personalizados if isinstance(personalizados, dict) else {}).get(clave)
    if usuario is None and proveedor is not None:
        usuario = (personalizados if isinstance(personalizados, dict) else {}).get(str(proveedor))
    if isinstance(usuario, dict):
        perfil = _merge_profundos(perfil, usuario)

    # Si existe una variante para el tipo de tarea, se fusiona sobre la base.
    variantes = perfil.get("variantes") or {}
    variante = variantes.get(tipo)
    if isinstance(variante, dict):
        perfil = _merge_profundos(perfil, variante)

    # Garantiza las claves mínimas.
    perfil.setdefault("system_prompt", PERFIL_GENERICO["system_prompt"])
    perfil.setdefault("user_prompt_template", PERFIL_GENERICO["user_prompt_template"])
    perfil.setdefault("config", dict(PERFIL_GENERICO["config"]))
    perfil.setdefault("variantes", {})
    return perfil


def _aplicar_template(plantilla: str, consulta: str, contexto: str) -> str:
    """Rellena la plantilla con ``{consulta}`` y ``{contexto}``."""
    try:
        return plantilla.format(consulta=consulta, contexto=contexto)
    except (KeyError, IndexError, ValueError):
        # Plantilla con placeholders extra no soportados → no reformatear.
        return consulta


def aplicar_perfil(
    mensajes: list[dict],
    proveedor: str,
    tipo_tarea: str | None = None,
    contexto_extra: str = "",
    personalizados: dict | None = None,
) -> tuple[list[dict], dict[str, Any]]:
    """Aplica el perfil de ``proveedor`` a ``mensajes``.

    - Inyecta (o fusiona) el ``system_prompt`` del perfil.
    - Solo en tareas conversacionales reescribe el último mensaje de usuario
      con ``user_prompt_template`` (para no romper los prompts estructurados).
    - Devuelve ``(mensajes_nuevos, config)``; si algo falla, devuelve los
      mensajes originales y ``{}`` (nunca rompe el flujo).
    """
    if not isinstance(mensajes, list) or not mensajes:
        return mensajes, {}
    try:
        perfil = obtener_perfil(proveedor, tipo_tarea, personalizados)
        tipo = _normaliza_tipo_tarea(tipo_tarea)
        # Solo reformateamos la plantilla de usuario cuando la conversación es
        # de un solo turno (consulta directa). En historiales multi-turno
        # (ReAct/chat con assistant) solo se inyecta el system_prompt, para no
        # alterar el formato del bucle agéntico.
        num_user = sum(1 for m in mensajes if m.get("role") == "user")
        reformatear = tipo not in _TAREAS_NO_REFORMATEAR and num_user == 1
        nuevo: list[dict] = []

        # 1) system_prompt (fusionando un system existente si lo hay).
        system = str(perfil.get("system_prompt") or "").strip()
        if system and mensajes[0].get("role") == "system":
            previo = str(mensajes[0].get("content") or "")
            nuevo.append(
                {"role": "system", "content": system + ("\n\n" + previo if previo else "")}
            )
        elif system:
            nuevo.append({"role": "system", "content": system})

        # 2) cuerpo (se copian todos; se reformatea el usuario si aplica).
        for idx, mensaje in enumerate(mensajes):
            msj = dict(mensaje)
            if (
                idx == 0
                and msj.get("role") == "system"
                and system
                and nuevo
                and nuevo[0].get("role") == "system"
            ):
                continue  # ya fusionado arriba
            if reformatear and msj.get("role") == "user":
                plantilla = str(perfil.get("user_prompt_template") or "")
                if plantilla:
                    contenido = str(msj.get("content") or "")
                    msj["content"] = _aplicar_template(
                        plantilla, contenido, str(contexto_extra or "")
                    )
            nuevo.append(msj)

        return nuevo, dict(perfil.get("config") or {})
    except Exception:
        return mensajes, {}


def perfiles_disponibles() -> list[str]:
    """Lista de claves de perfiles definidos por defecto."""
    return sorted(PERFILES.keys())


__all__ = [
    "ALIASES",
    "PERFILES",
    "PERFIL_GENERICO",
    "TIPOS_TAREA",
    "_normaliza_tipo_tarea",
    "aplicar_perfil",
    "obtener_perfil",
    "perfiles_disponibles",
]
