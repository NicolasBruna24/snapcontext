#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Router (v6.30.0) — orquestación inteligente de modelos para SnapContext.

Clasifica cada tarea por complejidad/tipo y asigna el modelo más adecuado
(Ollama local para tareas simples, Gemini/DeepSeek para intermedias, Claude
para críticas), reduciendo costes sin sacrificar calidad.

Diseño:
- ``clasificar_tarea``  : heurísticas rápidas (sin llamadas a la IA).
- ``seleccionar_modelo``: elige (proveedor, modelo) según ``config.json``
  (sección ``model_routing``). Sin configuración → ``(None, None)``,
  lo que significa "usa el modelo por defecto actual" (compatibilidad total).
- ``enrutar_tarea``     : combina ambas y devuelve un dict listo para usar.

v6.30.0 — Enrutamiento híbrido Local-Nube:
- ``es_tarea_compleja``              : detecta tareas complejas con heurísticas
  rápidas (longitud de la consulta, tamaño de archivos, nº de archivos,
  comandos complejos). Sin llamadas a la IA.
- ``obtener_orden_prioridad``        : cadena de ``(proveedor, modelo)`` —
  local→nube para tareas simples, nube→local para complejas.
- ``seleccionar_modelo_con_fallback``: primer modelo de la cadena; sin
  prioridades configuradas delega en ``seleccionar_modelo`` (compatibilidad).
- ``es_proveedor_local``             : ¿es un proveedor local (Ollama...)?

Extensible: basta con añadir la categoría a ``CATEGORIAS`` y su regla en
``config.json`` (``model_routing.<categoria>``).
"""

import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "CATEGORIAS", "ROUTING_DEFECTO", "clasificar_tarea",
    "seleccionar_modelo", "enrutar_tarea",
    # v6.30.0: enrutamiento híbrido Local-Nube.
    "es_tarea_compleja", "obtener_orden_prioridad",
    "seleccionar_modelo_con_fallback", "es_proveedor_local",
    "PROVEEDORES_LOCALES",
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


# ═══════════════════════════════════════════════════════════════════════════
# v6.30.0 — ENRUTAMIENTO HÍBRIDO LOCAL-NUBE
#
# Detecta la complejidad de la tarea con heurísticas rápidas (sin llamadas a
# la IA) y devuelve una CADENA de prioridad de modelos: las tareas simples
# empiezan por modelos locales (Ollama) y las complejas por modelos cloud
# (Gemini, Claude, DeepSeek); ante un fallo de API/timeout el llamador
# continúa con el siguiente modelo de la cadena. Los errores de autenticación
# NO se reintentan (restricción de seguridad).
#
# Todo es configurable desde config.json (``model_routing``):
#
#   "prioridad_local": ["ollama/qwen3.5:9b", "ollama/llama3.2"],
#   "prioridad_nube":  ["gemini/gemini-2.5-pro", "anthropic/claude-3.7-sonnet"],
#   "umbral_complejidad": {"longitud_consulta": 100, "tamano_archivo": 1000,
#                          "num_archivos": 3},
#   "fallback_automatico": true
#
# Sin prioridades configuradas el comportamiento es idéntico al de v6.24.0
# (enrutado por categoría), garantizando compatibilidad total.
# ═══════════════════════════════════════════════════════════════════════════

# Proveedores considerados "locales" (para los mensajes de usuario).
PROVEEDORES_LOCALES: Tuple[str, ...] = ("ollama",)

# Umbrales de complejidad por defecto (sobrescribibles en config.json).
_UMBRAL_DEFECTO: Dict[str, int] = {
    "longitud_consulta": 100,   # palabras de la consulta
    "tamano_archivo": 1000,     # líneas del archivo
    "num_archivos": 3,          # archivos a editar simultáneamente
}

# Marcas de comandos/operaciones "complejas" en la consulta (heurística).
_KW_COMANDOS_COMPLEJOS: Tuple[str, ...] = (
    "&&", "||", "| grep", "| awk", "sudo ", "rm -rf", "git rebase",
    "git reset --hard", "git cherry-pick", "docker ", "docker-compose",
    "kubectl ", "terraform ", "ansible ", "systemctl ", "chmod ", "chown ",
    "crontab", "curl ", "wget ", "pip install", "npm install", "yarn add",
    "drop table", "truncate table", "migraci", "migrar", "migrat",
    "deploy", "despliegue",
)

# Los archivos gigantes no se leen para contar líneas (rendimiento).
_MAX_BYTES_CONTEO = 64 * 1024 * 1024


def _umbral(seccion: Dict[str, Any], clave: str) -> int:
    """Lee ``umbral_complejidad.<clave>`` de la sección (tolerante a errores)."""
    umbrales = seccion.get("umbral_complejidad")
    defecto = _UMBRAL_DEFECTO.get(clave, 0)
    if not isinstance(umbrales, dict):
        return defecto
    try:
        return int(umbrales.get(clave, defecto))
    except (TypeError, ValueError):
        return defecto


def _archivos_contexto(contexto: Dict[str, Any]) -> List[str]:
    """Normaliza ``contexto["archivos"]`` a una lista de rutas (str)."""
    archivos = contexto.get("archivos") or []
    if isinstance(archivos, str):
        archivos = [archivos]
    if not isinstance(archivos, (list, tuple)):
        return []
    rutas: List[str] = []
    for entrada in archivos:
        if isinstance(entrada, str) and entrada.strip():
            rutas.append(entrada)
        elif isinstance(entrada, dict):
            ruta = (entrada.get("ruta") or entrada.get("path")
                    or entrada.get("archivo"))
            if isinstance(ruta, str) and ruta.strip():
                rutas.append(ruta)
    return rutas


def _lineas_archivo(ruta: str) -> Optional[int]:
    """Cuenta las líneas de ``ruta`` (lectura binaria, rápida).

    Devuelve ``None`` si el archivo no existe, es ilegible o es patológico
    (>64 MB: se omite para no penalizar la detección).
    """
    try:
        if not os.path.isfile(ruta):
            return None
        if os.path.getsize(ruta) > _MAX_BYTES_CONTEO:
            return None
        with open(ruta, "rb") as fh:
            datos = fh.read()
    except OSError:
        return None
    if not datos:
        return 0
    return datos.count(b"\n") + (0 if datos.endswith(b"\n") else 1)


def es_tarea_compleja(consulta: Optional[str],
                      contexto: Optional[Dict[str, Any]] = None,
                      config: Optional[Dict[str, Any]] = None) -> bool:
    """Detecta si una tarea es compleja con heurísticas rápidas (v6.30.0).

    Sin llamadas a la IA: solo longitud de la consulta, tamaño de los archivos
    implicados, número de archivos a editar y presencia de comandos complejos.
    Los umbrales se leen de ``config["model_routing"]["umbral_complejidad"]``
    (defectos: 100 palabras, 1000 líneas, 3 archivos).

    ``contexto`` (opcional) puede traer:
      - ``archivos``: rutas (str) o dicts con ``ruta``/``path``/``archivo``.
      - ``lineas`` / ``lineas_archivo`` / ``lineas_por_archivo``: hints de
        tamaño para no tocar el disco (el llamador ya conoce las líneas).
    """
    contexto = contexto if isinstance(contexto, dict) else {}
    seccion = _seccion_routing(config)
    texto = str(consulta or "")

    # 1) Longitud de la consulta (en palabras). Barato → primero.
    if len(texto.split()) > _umbral(seccion, "longitud_consulta"):
        return True

    # 2) Comandos/operaciones complejas (deploy, migraciones, sistema...).
    baja = texto.lower()
    if any(marca in baja for marca in _KW_COMANDOS_COMPLEJOS):
        return True

    # 3) Múltiples archivos a editar.
    archivos = _archivos_contexto(contexto)
    if len(archivos) > _umbral(seccion, "num_archivos"):
        return True

    # 4) Archivos grandes: hints del llamador (sin I/O) y disco (I/O al final).
    tope_lineas = _umbral(seccion, "tamano_archivo")
    for hint in (contexto.get("lineas"), contexto.get("lineas_archivo")):
        if isinstance(hint, int) and hint > tope_lineas:
            return True
    lineas_por_archivo = contexto.get("lineas_por_archivo")
    if isinstance(lineas_por_archivo, dict):
        for valor in lineas_por_archivo.values():
            if isinstance(valor, int) and valor > tope_lineas:
                return True
    for ruta in archivos:
        lineas = _lineas_archivo(ruta)
        if lineas is not None and lineas > tope_lineas:
            return True
    return False


def _parsear_prioridad(entrada: Any) -> List[Tuple[str, Optional[str]]]:
    """Convierte una lista de prioridad en tuplas ``(proveedor, modelo)``.

    Acepta ``["ollama/qwen3.5:9b", ...]``, un str único
    ``"ollama/qwen3.5:9b"`` y ``[{"provider": ..., "model": ...}]``; ignora
    silenciosamente las entradas inválidas (sin proveedor o sin ``/``).
    """
    if entrada is None:
        return []
    if isinstance(entrada, str):
        entrada = [entrada]
    if not isinstance(entrada, (list, tuple)):
        return []
    resultado: List[Tuple[str, Optional[str]]] = []
    for item in entrada:
        if isinstance(item, str):
            texto = item.strip()
            if "/" not in texto:
                continue                # inválida: sin "proveedor/modelo"
            proveedor, _, modelo = texto.partition("/")
            proveedor = proveedor.strip().lower()
            if proveedor:
                resultado.append((proveedor, modelo.strip() or None))
        elif isinstance(item, dict):
            proveedor = str(item.get("provider") or "").strip().lower()
            modelo = item.get("model")
            if proveedor:
                resultado.append((proveedor, str(modelo) if modelo else None))
    return resultado


def obtener_orden_prioridad(
        compleja: bool,
        config: Optional[Dict[str, Any]] = None
) -> List[Tuple[str, Optional[str]]]:
    """Cadena de prioridad ``(proveedor, modelo)`` según complejidad (v6.30.0).

    - Tarea **compleja** → ``prioridad_nube`` + ``prioridad_local`` (si la
      nube falla, se intenta un modelo local como último recurso).
    - Tarea **simple**   → ``prioridad_local`` + ``prioridad_nube`` (si no hay
      modelos locales disponibles, se escala a la nube).

    Sin listas configuradas devuelve ``[]``: el llamador mantiene su modelo
    por defecto (compatibilidad total con v6.24.0).
    """
    seccion = _seccion_routing(config)
    lista_local = _parsear_prioridad(seccion.get("prioridad_local"))
    lista_nube = _parsear_prioridad(seccion.get("prioridad_nube"))
    orden = ((lista_nube + lista_local) if compleja
             else (lista_local + lista_nube))
    vistos: set = set()
    resultado: List[Tuple[str, Optional[str]]] = []
    for par in orden:
        if par not in vistos:
            vistos.add(par)
            resultado.append(par)
    return resultado


def seleccionar_modelo_con_fallback(
        categoria: str,
        config: Optional[Dict[str, Any]] = None,
        compleja: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """Primer modelo ``(proveedor, modelo)`` de la cadena de prioridad.

    Con ``prioridad_local``/``prioridad_nube`` configuradas devuelve la cabeza
    de la cadena (local para tareas simples, cloud para complejas). Sin
    prioridades configuradas delega en :func:`seleccionar_modelo` (enrutado
    por categoría de v6.24.0 → compatibilidad total).
    """
    orden = obtener_orden_prioridad(compleja, config)
    if orden:
        return orden[0]
    return seleccionar_modelo(categoria, config)


def es_proveedor_local(proveedor: Optional[str],
                       config: Optional[Dict[str, Any]] = None) -> bool:
    """¿Es ``proveedor`` un proveedor local (p. ej. Ollama)? (v6.30.0).

    La lista de proveedores locales se puede ampliar en config.json con
    ``model_routing.proveedores_locales``.
    """
    if not proveedor:
        return False
    seccion = _seccion_routing(config)
    personalizados = seccion.get("proveedores_locales")
    if isinstance(personalizados, (list, tuple)) and personalizados:
        return str(proveedor).strip().lower() in {
            str(p).strip().lower() for p in personalizados}
    return str(proveedor).strip().lower() in PROVEEDORES_LOCALES


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
    """Combina clasificación + complejidad + selección (v6.30.0).

    Devuelve ``{"provider", "model", "categoria", "enrutado", "compleja"}``
    donde ``enrutado`` es ``False`` cuando no hay configuración (se usa el
    modelo por defecto actual) y ``True`` cuando el router asignó un modelo
    propio; ``compleja`` es el resultado de :func:`es_tarea_compleja` y
    decide si la cadena de prioridad empieza por la nube o por modelos
    locales (híbrido Local-Nube).
    """
    categoria = clasificar_tarea(consulta, contexto)
    compleja = es_tarea_compleja(consulta, contexto, config)
    proveedor, modelo = seleccionar_modelo_con_fallback(categoria, config,
                                                        compleja)
    return {
        "provider": proveedor,
        "model": modelo,
        "categoria": categoria,
        "enrutado": bool(proveedor),
        "compleja": compleja,
    }


if __name__ == "__main__":                      # pequeña demo manual
    import json
    import sys
    texto = " ".join(sys.argv[1:]) or "arregla el botón de pago"
    print(json.dumps(enrutar_tarea(texto), ensure_ascii=False, indent=2))