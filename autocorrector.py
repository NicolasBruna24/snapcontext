#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sistema de autocuracion (v6.29.0) — bucle de pruebas + correccion automatica.

Integra:
- Sandbox persistente (v6.4.0) para mantener dependencias entre iteraciones.
- Deteccion automatica de comandos de test (v5.3.0).
- Analisis de errores con LLM para identificar causa raiz.
- Editor propio (v4.6.0+) para aplicar correcciones.

Flujo:
    1. Ejecutar pruebas.
    2. Si fallan, analizar error con LLM.
    3. Proponer y aplicar correccion.
    4. Repetir hasta exito o max_iteraciones.
"""

from __future__ import annotations

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "ejecutar_bucle_correccion",
    "analizar_error",
    "aplicar_correccion",
    "Autocorrector",
]

CATEGORIA_AUTOCORRECCION = "autocorreccion"


def _detectar_comando_test(directorio: str) -> str:
    """Detecta el comando de test para el proyecto."""
    try:
        import snapcontext as sc
        from detector_tests import deteccion_tests as det
        resultado = det.detectar_automaticamente(directorio)
        if resultado and resultado.get("comando"):
            return resultado["comando"]
    except Exception:
        pass
    if Path(directorio, "pytest.ini").exists() or Path(directorio, "setup.py").exists():
        return "pytest -q"
    if Path(directorio, "package.json").exists():
        return "npm test"
    if Path(directorio, "go.mod").exists():
        return "go test ./..."
    return "pytest -q"


def _ejecutar_en_sandbox(comando: str, directorio: str,
                        timeout: int = 600) -> Tuple[int, str, str]:
    """Ejecuta un comando, preferentemente en sandbox si esta disponible."""
    try:
        import snapcontext as sc
        if hasattr(sc, "_sandbox_session_activa") and sc._sandbox_session_activa:
            try:
                import sandbox_session as ss
                return ss.ejecutar(comando, cwd=directorio, timeout=timeout)
            except Exception:
                pass
        return sc._ejecutar_comando(comando, directorio, timeout=timeout)
    except Exception:
        try:
            resultado = subprocess.run(
                comando, shell=True, cwd=directorio,
                capture_output=True, text=True, timeout=timeout
            )
            return resultado.returncode, resultado.stdout, resultado.stderr
        except Exception as exc:
            return -1, "", str(exc)


def analizar_error(salida_error: str, proveedor: str = "ollama",
                   modelo: Optional[str] = None) -> Dict[str, Any]:
    """Analiza un error de test usando el LLM."""
    if not salida_error or not salida_error.strip():
        return {
            "archivo": "", "linea": 0, "tipo": "desconocido",
            "mensaje": "Sin salida de error", "sugerencia": "",
        }

    prompt = f"""Analiza el siguiente error de pruebas y extrae informacion util.

Error:
```
{salida_error[:3000]}
```

Responde SOLO con este JSON (sin markdown ni texto adicional):
{{"archivo": "ruta/del/archivo.py", "linea": 0, "tipo": "TipoError", "mensaje": "descripcion corta", "sugerencia": "que y como corregir"}}"""

    try:
        import snapcontext as sc
        respuesta = sc._enviar_al_proveedor(
            proveedor, modelo, [{"role": "user", "content": prompt}]
        )
        texto = str(respuesta)
        json_match = re.search(r'\{.*\}', texto, re.DOTALL)
        if json_match:
            datos = json.loads(json_match.group())
            return {
                "archivo": datos.get("archivo", ""),
                "linea": int(datos.get("linea", 0)),
                "tipo": datos.get("tipo", "desconocido"),
                "mensaje": datos.get("mensaje", ""),
                "sugerencia": datos.get("sugerencia", ""),
            }
    except Exception:
        pass

    return _parseo_basico_error(salida_error)


def _parseo_basico_error(salida: str) -> Dict[str, Any]:
    """Parseo basico de errores sin LLM (fallback)."""
    archivo = ""
    linea = 0
    tipo = "error"
    mensaje = salida[:200]

    file_match = re.search(r'File "([^"]+)", line (\d+)', salida)
    if file_match:
        archivo = file_match.group(1)
        linea = int(file_match.group(2))

    error_match = re.search(r'(\w+Error|FAILED|AssertionError)', salida)
    if error_match:
        tipo = error_match.group(1)

    return {
        "archivo": archivo, "linea": linea, "tipo": tipo,
        "mensaje": mensaje, "sugerencia": "",
    }


def aplicar_correccion(archivo: str, sugerencia: str, directorio: str,
                       contexto: Optional[Dict[str, Any]] = None) -> bool:
    """Aplica una correccion a un archivo usando el editor propio."""
    ruta = Path(directorio) / archivo
    if not ruta.exists():
        return False

    try:
        import snapcontext as sc
        contenido = ruta.read_text(encoding="utf-8")

        prompt = f"""Corrige el siguiente error en el archivo {archivo}.

Error: {sugerencia}
Linea: {contexto.get('linea', 'desconocida') if contexto else 'desconocida'}

Codigo actual:
```python
{contenido[:5000]}
```

Responde SOLO con el codigo corregido completo (sin explicaciones)."""

        respuesta = sc._enviar_al_proveedor(
            "ollama", None, [{"role": "user", "content": prompt}]
        )
        correccion = str(respuesta)

        codigo_corregido = _extraer_codigo(correccion, contenido)
        if codigo_corregido and codigo_corregido != contenido:
            ruta.write_text(codigo_corregido, encoding="utf-8")
            return True
    except Exception:
        pass

    return False


def _extraer_codigo(respuesta: str, original: str) -> str:
    """Extrae codigo de una respuesta del LLM."""
    code_match = re.search(r'```(?:python)?\s*\n(.*?)```', respuesta, re.DOTALL)
    if code_match:
        return code_match.group(1).strip()
    return original


def ejecutar_bucle_correccion(
    comando_test: str,
    directorio: str,
    max_iteraciones: int = 3,
    proveedor: str = "ollama",
    modelo: Optional[str] = None,
) -> Dict[str, Any]:
    """Ejecuta el bucle de autocorreccion."""
    iteraciones = 0
    ultimo_error = ""

    for i in range(1, max_iteraciones + 1):
        iteraciones = i

        codigo, stdout, stderr = _ejecutar_en_sandbox(
            comando_test, directorio
        )

        if codigo == 0:
            return {
                "exito": True,
                "iteraciones": iteraciones,
                "resultado": "pruebas superadas",
                "stdout": stdout,
            }

        ultimo_error = stderr or stdout or "Error desconocido"

        if i < max_iteraciones:
            analisis = analizar_error(
                ultimo_error, proveedor=proveedor, modelo=modelo
            )

            archivo = analisis.get("archivo", "")
            sugerencia = analisis.get("sugerencia", "")

            if archivo and sugerencia:
                aplicar_correccion(
                    archivo, sugerencia, directorio,
                    contexto=analisis,
                )

    return {
        "exito": False,
        "iteraciones": iteraciones,
        "error": f"Pruebas fallidas tras {max_iteraciones} ciclos",
        "detalle": ultimo_error[:1000],
    }


class Autocorrector:
    """Interfaz de alto nivel para el sistema de autocorreccion."""

    def __init__(self, directorio: str = ".",
                 max_iteraciones: int = 3,
                 proveedor: str = "ollama",
                 modelo: Optional[str] = None) -> None:
        self.directorio = str(Path(directorio).resolve())
        self.max_iteraciones = max_iteraciones
        self.proveedor = proveedor
        self.modelo = modelo

    def ejecutar(self, comando_test: Optional[str] = None) -> Dict[str, Any]:
        """Ejecuta el bucle de autocorreccion."""
        if comando_test is None:
            comando_test = _detectar_comando_test(self.directorio)

        return ejecutar_bucle_correccion(
            comando_test=comando_test,
            directorio=self.directorio,
            max_iteraciones=self.max_iteraciones,
            proveedor=self.proveedor,
            modelo=self.modelo,
        )
