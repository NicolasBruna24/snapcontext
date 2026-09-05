#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruning proactivo de contexto (v6.32.0) — edición quirúrgica del historial.

El sistema de resúmenes automáticos (v5.1.0) espera a que el historial sea
grande antes de resumir. Este módulo va un paso más allá: **poda proactivamente**
los datos de herramientas (logs, salidas, diffs) después de cada uso,
reemplazándolos por un resumen de una línea.

Capas de poda (orden de evaluación):
    1. Resultados de herramientas extensos (>N líneas, configurable).
    2. Diffs de git grandes.
    3. Salidas de comandos (stdout/stderr) extensas.

El resumen se genera con el LLM si está disponible (categoría ``"resumen"``)
o con una heurística simple (primera línea + " (y N líneas más)").

Configuración (``config.json -> pruning``)::

    "pruning": {
      "activo": true,
      "umbral_lineas": 10,
      "usar_llm": true,
      "tipos_podables": ["stdout", "stderr", "contenido", "diff", "texto"]
    }

Sin configuración se usan los valores por defecto. Sin este módulo (el fichero
no existe o falla) ``snapcontext`` mantiene el comportamiento actual sin poda.
"""

from typing import Any, Dict, List, Optional

__all__ = [
    "UMBRAL_LINEAS_DEFECTO",
    "TIPOS_PODABLES_DEFECTO",
    "es_resultado_extenso",
    "resumir_linea",
    "obtener_metadatos_clave",
    "prune_resultado",
    "configuracion_pruning",
]

# Valores por defecto (sobrescribibles en config.json).
UMBRAL_LINEAS_DEFECTO: int = 10
TIPOS_PODABLES_DEFECTO: List[str] = [
    "stdout", "stderr", "contenido", "diff", "texto",
]

# Metadatos que siempre se preservan (información crítica para el agente).
METADATOS_CLAVE: List[str] = [
    "ok", "codigo", "ruta", "comando", "error", "url", "total", "lineas",
]


def configuracion_pruning(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Devuelve la configuración efectiva de pruning (v6.32.0).

    Fusiona los valores por defecto con ``config["pruning"]`` si existe.
    """
    efectiva = {
        "activo": True,
        "umbral_lineas": UMBRAL_LINEAS_DEFECTO,
        "usar_llm": True,
        "tipos_podables": list(TIPOS_PODABLES_DEFECTO),
    }
    if not isinstance(config, dict):
        return efectiva
    seccion = config.get("pruning")
    if not isinstance(seccion, dict):
        return efectiva
    for clave in ("activo", "usar_llm"):
        if clave in seccion:
            efectiva[clave] = bool(seccion[clave])
    if "umbral_lineas" in seccion:
        try:
            efectiva["umbral_lineas"] = max(1, int(seccion["umbral_lineas"]))
        except (TypeError, ValueError):
            pass
    if "tipos_podables" in seccion:
        tipos = seccion["tipos_podables"]
        if isinstance(tipos, (list, tuple)) and tipos:
            efectiva["tipos_podables"] = [str(t) for t in tipos]
    return efectiva


def _contar_lineas(valor: Any) -> int:
    """Cuenta las líneas de un valor (str, list o cualquier otro tipo)."""
    if isinstance(valor, str):
        return max(len(valor.splitlines()), 1) if valor.strip() else 0
    if isinstance(valor, list):
        return len(valor)
    if isinstance(valor, (int, float, bool)):
        return 1
    return 0


def es_resultado_extenso(
        resultado: Any,
        umbral_lineas: int = UMBRAL_LINEAS_DEFECTO,
        tipos_podables: Optional[List[str]] = None) -> bool:
    """Determina si un resultado de herramienta es extenso (v6.32.0).

    Evalúa los campos ``tipos_podables`` (por defecto: stdout, stderr,
    contenido, diff, texto). Si alguno supera ``umbral_lineas``, devuelve
    ``True``. Un resultado que no sea ``dict`` nunca es extenso.
    """
    if not isinstance(resultado, dict):
        return False
    if not isinstance(umbral_lineas, int) or umbral_lineas < 1:
        umbral_lineas = UMBRAL_LINEAS_DEFECTO
    tipos = tipos_podables if isinstance(tipos_podables, (list, tuple)) else list(TIPOS_PODABLES_DEFECTO)
    for tipo in tipos:
        if tipo in resultado and _contar_lineas(resultado[tipo]) > umbral_lineas:
            return True
    return False


def obtener_metadatos_clave(
        resultado: dict,
        tipo_herramienta: str = "") -> Dict[str, Any]:
    """Extrae los metadatos críticos de un resultado (v6.32.0).

    Preserva información esencial para la toma de decisiones del agente:
    código de retorno, archivo afectado, error, etc.
    """
    if not isinstance(resultado, dict):
        return {}
    metadatos: Dict[str, Any] = {}
    for clave in METADATOS_CLAVE:
        if clave in resultado:
            metadatos[clave] = resultado[clave]
    if "accion" in resultado:
        metadatos["accion"] = resultado["accion"]
    if tipo_herramienta and "accion" not in metadatos:
        metadatos["accion"] = tipo_herramienta
    return metadatos


def resumir_linea(
        texto: str,
        max_lineas: int = 1,
        usar_llm: bool = False,
        proveedor_llm: Optional[Any] = None) -> str:
    """Genera un resumen de una línea (v6.32.0).

    Si ``usar_llm`` es ``True`` y ``proveedor_llm`` está disponible, delega
    en el LLM para generar un resumen. En caso contrario usa la heurística:
    primera línea no vacía + " (y N líneas más)" si procede.
    """
    if not isinstance(texto, str):
        texto = str(texto) if texto else ""
    lineas = [l for l in texto.splitlines() if l.strip()]
    if not lineas:
        return ""
    if len(lineas) <= max_lineas:
        return lineas[0][:200]
    if usar_llm and proveedor_llm is not None:
        try:
            resumen = _resumir_con_llm(texto, proveedor_llm)
            if resumen:
                return resumen[:200]
        except Exception:
            pass
    primera = lineas[0][:160].rstrip()
    restantes = len(lineas) - 1
    return f"{primera} (y {restantes} líneas más)"


def _resumir_con_llm(texto: str, proveedor_llm: Any) -> Optional[str]:
    """Genera un resumen de una línea vía LLM (v6.32.0)."""
    if proveedor_llm is None:
        return None
    pedido = (
        "Resume en UNA sola línea (máximo 200 caracteres, en español) la "
        "información más relevante de esta salida de herramienta. "
        "Conserva datos críticos: errores, códigos de retorno, archivos "
        "afectados. Solo el resumen, sin introducción.\n\n"
        f"---\n{texto[:4000]}\n---"
    )
    if callable(proveedor_llm):
        resultado = proveedor_llm(pedido)
        return str(resultado) if resultado else None
    if hasattr(proveedor_llm, "_llamar_llm"):
        try:
            resultado = proveedor_llm._llamar_llm(
                [{"role": "user", "content": pedido}], timeout=60)
            return str(resultado) if resultado else None
        except Exception:
            return None
    return None


def prune_resultado(
        resultado: dict,
        tipo_herramienta: str = "",
        umbral_lineas: int = UMBRAL_LINEAS_DEFECTO,
        usar_llm: bool = False,
        proveedor_llm: Optional[Any] = None,
        tipos_podables: Optional[List[str]] = None) -> dict:
    """Poda un resultado de herramienta (v6.32.0).

    Si el resultado es extenso, reemplaza cada campo podable por un resumen
    de una línea y añade ``_pruned: True`` más un campo ``_resumen`` con los
    metadatos clave. Si no es extenso, devuelve el resultado original sin
    cambios. Nunca muta el diccionario original: devuelve una copia.
    """
    if not isinstance(resultado, dict):
        return resultado
    tipos = tipos_podables if isinstance(tipos_podables, (list, tuple)) else list(TIPOS_PODABLES_DEFECTO)
    if not es_resultado_extenso(resultado, umbral_lineas, tipos):
        return resultado
    podado = dict(resultado)
    metadatos = obtener_metadatos_clave(podado, tipo_herramienta)
    lineas_resumen: List[str] = []
    for tipo in tipos:
        valor = podado.get(tipo)
        if valor is None:
            continue
        n_lineas = _contar_lineas(valor)
        if n_lineas <= umbral_lineas:
            continue
        if isinstance(valor, list):
            texto_valor = "\n".join(str(v) for v in valor)
        else:
            texto_valor = str(valor)
        resumen = resumir_linea(texto_valor, max_lineas=1,
                                usar_llm=usar_llm,
                                proveedor_llm=proveedor_llm)
        podado[tipo] = resumen if resumen else f"(salida de {n_lineas} líneas podada)"
        lineas_resumen.append(f"{tipo}: {podado[tipo]}")
    podado["_pruned"] = True
    podado["_pruned_lineas_umbral"] = umbral_lineas
    podado["_resumen"] = " | ".join(lineas_resumen) if lineas_resumen else ""
    if metadatos:
        podado["_metadatos"] = metadatos
    return podado