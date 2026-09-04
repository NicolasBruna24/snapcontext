#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA Tester adversarial — v6.25.0.

Sub-agente especializado en revisión destructiva de código:
- Identifica errores de seguridad, rendimiento, lógica y estilo.
- Genera pruebas maliciosas para explotar vulnerabilidades.
- Propone correcciones concretas.
- Se integra en el bucle Programador ↔ QA Tester del Supervisor.

El QA Tester NO ejecuta pruebas en el sistema del usuario: solo genera
código de pruebas y análisis estático vía LLM.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "revisar_codigo",
    "generar_pruebas",
    "aplicar_correcciones",
    "QA_Tester",
]

# Severidad: controla cuán exigente es el revisor.
SEVERIDAD_MINIMA = {"baja": 0, "media": 1, "alta": 2}

# Plantilla de prompt para el LLM.
_PROMPT_QA = """Eres un QA Tester experto y ADVERSAL. Revisa el siguiente código
y encuentra TODOS los errores, vulnerabilidades y malas prácticas.

Contexto:
- Archivo: {archivo}
- Lenguaje: {lenguaje}
- Severidad mínima: {severidad}

Código a revisar:
```{lenguaje}
{codigo}
```

Analiza:
1. Seguridad (inyección SQL, XSS, path traversal, secretos hardcodeados)
2. Rendimiento (complejidad, fugas de memoria, consultas N+1)
3. Lógica (off-by-one, manejo de nulos, condiciones de carrera)
4. Estilo (código duplicado, nombres poco descriptivos)

Responde SOLO con este JSON (sin markdown ni texto adicional):
{{"aprobado": true/false, "hallazgos": [{{"tipo": "seguridad|rendimiento|logica|estilo", "descripcion": "...", "gravedad": "alta|media|baja"}}], "sugerencias": ["..."], "pruebas": "codigo de pruebas"}}"""


def _detectar_lenguaje(archivo: str) -> str:
    """Detecta el lenguaje de programación por extensión."""
    ext = archivo.rsplit(".", 1)[-1].lower() if "." in archivo else ""
    mapa = {
        "py": "python", "js": "javascript", "ts": "typescript",
        "java": "java", "go": "go", "rs": "rust",
        "c": "c", "cpp": "cpp", "cs": "csharp",
        "rb": "ruby", "php": "php", "swift": "swift",
        "kt": "kotlin", "scala": "scala",
    }
    return mapa.get(ext, "python")


def _llamar_llm(
    proveedor: str,
    modelo: Optional[str],
    mensaje: str,
    categoria: str = "edicion_critica",
) -> str:
    """Llama al LLM usando el sistema de enrutamiento de v6.24.0."""
    try:
        import snapcontext as sc
        if hasattr(sc, "enrutar_tarea") and getattr(sc, "_MODEL_ROUTING_ACTIVO", False):
            ruta = sc.enrutar_tarea(mensaje, {"accion": categoria})
            if ruta.get("enrutado"):
                proveedor = ruta["provider"]
                modelo = ruta["model"]
        respuesta = sc._enviar_al_proveedor(
            proveedor, modelo, [{"role": "user", "content": mensaje}]
        )
        return str(respuesta)
    except Exception as exc:  # noqa: BLE001
        return f"Error llamando al LLM: {exc}"


def _extraer_json(texto: str) -> Optional[dict]:
    """Extrae el primer objeto JSON válido de texto."""
    if not texto:
        return None
    limpio = re.sub(r"```(?:json)?", "", str(texto)).strip().strip("`").strip()
    inicio = limpio.find("{")
    fin = limpio.rfind("}")
    if inicio == -1 or fin == -1 or fin <= inicio:
        return None
    try:
        datos = json.loads(limpio[inicio:fin + 1])
    except json.JSONDecodeError:
        return None
    return datos if isinstance(datos, dict) else None


def revisar_codigo(
    codigo: str,
    contexto: Dict[str, Any],
) -> Dict[str, Any]:
    """Revisa código usando el LLM y devuelve hallazgos."""
    archivo = contexto.get("archivo", "desconocido")
    lenguaje = contexto.get("lenguaje") or _detectar_lenguaje(archivo)
    proveedor = contexto.get("proveedor", "gemini")
    modelo = contexto.get("modelo")
    severidad = contexto.get("severidad", "media")

    prompt = _PROMPT_QA.format(
        archivo=archivo,
        lenguaje=lenguaje,
        severidad=severidad,
        codigo=codigo,
    )

    respuesta = _llamar_llm(proveedor, modelo, prompt, "edicion_critica")
    datos = _extraer_json(respuesta)

    if not datos:
        return {
            "aprobado": False,
            "hallazgos": [],
            "sugerencias": [],
            "pruebas": "",
            "error": "No se pudo analizar la respuesta del LLM",
            "respuesta_cruda": respuesta[:500],
        }

    umbral = SEVERIDAD_MINIMA.get(severidad, 1)
    hallazgos = []
    for h in datos.get("hallazgos", []):
        gravedad = h.get("gravedad", "media")
        if SEVERIDAD_MINIMA.get(gravedad, 1) >= umbral:
            hallazgos.append(h)

    return {
        "aprobado": bool(datos.get("aprobado", False)) and len(hallazgos) == 0,
        "hallazgos": hallazgos,
        "sugerencias": datos.get("sugerencias", []),
        "pruebas": datos.get("pruebas", ""),
    }


def generar_pruebas(
    codigo: str,
    hallazgos: List[Dict[str, Any]],
    lenguaje: str = "python",
) -> str:
    """Genera pruebas específicas para los hallazgos encontrados."""
    if not hallazgos:
        return ""

    lista_hallazgos = "\n".join(
        f"- [{h.get('tipo', 'desconocido')}] {h.get('descripcion', '')}"
        for h in hallazgos
    )

    prompt = f"""Genera pruebas unitarias en {lenguaje} para verificar los siguientes
hallazgos en el código. Las pruebas deben INTENTAR FALLAR si el problema existe.

Hallazgos:
{lista_hallazgos}

Código:
```{lenguaje}
{codigo}
```

Responde SOLO con el código de las pruebas (sin explicaciones)."""

    try:
        import snapcontext as sc
        respuesta = sc._enviar_al_proveedor(
            "gemini", None, [{"role": "user", "content": prompt}]
        )
        return str(respuesta)
    except Exception:  # noqa: BLE001
        return ""


def aplicar_correcciones(
    codigo: str,
    sugerencias: List[str],
) -> str:
    """Aplica correcciones sugeridas al código."""
    if not sugerencias:
        return codigo

    corregido = codigo
    for i, sugerencia in enumerate(sugerencias):
        if "->" in sugerencia:
            partes = sugerencia.split("->", 1)
            buscar = partes[0].strip()
            reemplazar = partes[1].strip()
            if buscar and buscar in corregido:
                corregido = corregido.replace(buscar, reemplazar)
        else:
            corregido += f"\n# [QA Tester sugerencia {i+1}]: {sugerencia}\n"

    return corregido


class QA_Tester:
    """Interfaz de alto nivel para el QA Tester adversarial."""

    def __init__(
        self,
        proveedor: str = "gemini",
        modelo: Optional[str] = None,
        severidad: str = "media",
        max_iteraciones: int = 2,
    ) -> None:
        self.proveedor = proveedor
        self.modelo = modelo
        self.severidad = severidad
        self.max_iteraciones = max_iteraciones

    def revisar(
        self,
        codigo: str,
        archivo: str = "desconocido",
    ) -> Dict[str, Any]:
        """Revisa código y devuelve hallazgos."""
        contexto = {
            "archivo": archivo,
            "proveedor": self.proveedor,
            "modelo": self.modelo,
            "severidad": self.severidad,
        }
        return revisar_codigo(codigo, contexto)

    def revisar_y_corregir(
        self,
        codigo: str,
        archivo: str = "desconocido",
    ) -> Tuple[str, Dict[str, Any]]:
        """Revisa, genera pruebas y aplica correcciones iterativamente."""
        codigo_actual = codigo
        resultado = None

        for _ in range(self.max_iteraciones):
            resultado = self.revisar(codigo_actual, archivo)
            if resultado.get("aprobado"):
                break
            sugerencias = resultado.get("sugerencias", [])
            if sugerencias:
                codigo_actual = aplicar_correcciones(codigo_actual, sugerencias)

        return codigo_actual, resultado or {"aprobado": True, "hallazgos": []}
