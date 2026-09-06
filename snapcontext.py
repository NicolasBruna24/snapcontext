#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SnapContext ‚Äî Asistente de IA para desarrollo con contexto autom·tico.

Pipeline:
    1) Detecta autom·ticamente el tipo de proyecto (Flutter, Node, Python, Go, Rust, etc.)
       y ajusta carpetas/extensiones por defecto.
    2) Escanea autom·ticamente el repositorio (por defecto seg˙n el tipo de proyecto):
       buscando archivos relevantes para la consulta del usuario.
    3) Usa Gemini (Google AI Studio) para seleccionar los archivos m·s
       relevantes, sin que el desarrollador tenga que listarlos a mano.
    4) Ejecuta Aider con los archivos seleccionados y la consulta original.
    5) (Opcional, --test-loop) DespuÈs de Aider ejecuta las pruebas
       (flutter test) y, si fallan, vuelve a llamar a Aider con el error
       para que las arregle.

Requisitos:
    - Python 3.9+
    - pip install google-generativeai   (proveedor por defecto: Gemini)
    - pip install openai                (DeepSeek, Groq y Ollama)
    - Variable de entorno seg˙n proveedor (GEMINI_API_KEY, DEEPSEEK_API_KEY,
      GROQ_API_KEY) y, opcionalmente, OLLAMA_URL para Ollama local
    - Aider instalado: pip install aider-chat

Uso:
    snapcontext "el botÛn de pago no funciona"
    snapcontext "el botÛn de pago no funciona" --test-loop
    snapcontext "arreglar el checkout" --server-loop      # servidor autom·tico
    snapcontext "arreglar login" --manual-loop            # servidor manual
    snapcontext "revisar pago" --experto                  # revisar/editar archivos
    snapcontext fix "el botÛn de pago no funciona"        # alias: test-loop
    snapcontext review "revisar cÛdigo"                   # alias: vista-previa + experto
    snapcontext server "iniciar servidor"                 # alias: server-loop
    snapcontext "..." --provider groq --model llama-3.3-70b-versatile

Open-source y pensado para ser f·cil de extender (ver ejecutar_bucle_test).
"""

import argparse
import ast
import contextlib
import difflib
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import warnings
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# v5.4.0: detecciÛn de comandos peligrosos para el sandboxing inteligente.
from sandbox_utils import es_comando_peligroso
# seguridad: ejecuciÛn segura de comandos (shell=False por defecto).
from sandbox_utils import ejecutar_comando_con_politica as _ejecutar_con_politica

# v4.8.0: capa de presentaciÛn centralizada (Rich). DegradaciÛn elegante:
# ui.py funciona tambiÈn sin `rich` (print plano), asÌ que la importaciÛn
# nunca rompe el CLI.
from ui import (configurar_auto as _ui_configurar_auto,
                es_auto as _ui_es_auto,
                mostrar_banner as _ui_mostrar_banner,
                mostrar_progreso as _ui_mostrar_progreso)
from urllib.parse import urlparse

# ‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê
# v6.9.0 ‚Äî IMPORTS PEREZOSOS (rendimiento de arranque)
# Los SDK pesados (google-generativeai, openai, anthropic, sentence-transformers
# y tree-sitter) ya NO se importan al cargar el mÛdulo. Se cargan solo cuando se
# usan de verdad (vÌa `_importar_*()`) o cuando el usuario los referencia
# (vÌa `__getattr__` de mÛdulo). AsÌ `--help`, la selecciÛn heurÌstica y el
# resto de la CLI arrancan en <0.3s sin pagar el coste de cargar torch/tf.
#
# Compatibilidad: se mantienen los nombres de mÛdulo (`genai`, `openai`,
# `anthropic`, `SentenceTransformer`, `tree_sitter`, `Language`, `_ts_lang`)
# para que el resto del cÛdigo y los tests sigan funcionando; ahora son
# atributos que se resuelven de forma perezosa y respetan los valores que los
# tests/usuarios asignen explÌcitamente (nunca se sobrescriben).
# ‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê

_SIN_CARGAR = object()          # centinela: la importaciÛn a˙n no se intentÛ


def _importar_genai():
    """Carga `google.generativeai` una sola vez (o None si falta)."""
    _actual = globals().get("genai", _SIN_CARGAR)
    if _actual is not _SIN_CARGAR:          # ya cargado o asignado explÌcitamente
        return _actual
    global genai                            # noqa: PLW0603
    try:
        with warnings.catch_warnings():
            # Silenciamos SOLO el FutureWarning de puesta al dÌa de la librerÌa.
            warnings.simplefilter("ignore", FutureWarning)
            import google.generativeai as _genai
        genai = _genai
    except ImportError:                     # pragma: no cover
        genai = None
    return genai


def _importar_openai():
    """Carga la librerÌa `openai` (Groq, DeepSeek, Ollama‚Ä¶) una sola vez."""
    _actual = globals().get("openai", _SIN_CARGAR)
    if _actual is not _SIN_CARGAR:
        return _actual
    global openai                           # noqa: PLW0603
    try:
        import openai as _openai
        openai = _openai
    except ImportError:                     # pragma: no cover
        openai = None
    return openai


def _importar_anthropic():
    """Carga el SDK de Claude (`anthropic`) una sola vez (o None si falta)."""
    _actual = globals().get("anthropic", _SIN_CARGAR)
    if _actual is not _SIN_CARGAR:
        return _actual
    global anthropic                        # noqa: PLW0603
    try:
        import anthropic as _anthropic      # type: ignore
        anthropic = _anthropic
    except ImportError:                     # pragma: no cover
        anthropic = None
    return anthropic


def _importar_sentence_transformer():
    """Carga `sentence_transformers.SentenceTransformer` (o None si falta)."""
    _actual = globals().get("SentenceTransformer", _SIN_CARGAR)
    if _actual is not _SIN_CARGAR:
        return _actual
    global SentenceTransformer              # noqa: PLW0603
    try:
        from sentence_transformers import SentenceTransformer as _ST  # type: ignore
        SentenceTransformer = _ST
    except ImportError:                     # pragma: no cover
        SentenceTransformer = None
    return SentenceTransformer


def _importar_tree_sitter():
    """Carga el motor tree-sitter completo (tree_sitter, Language, _ts_lang)."""
    _actual = globals().get("tree_sitter", _SIN_CARGAR)
    if _actual is not _SIN_CARGAR:
        return _actual
    global tree_sitter, Language, _ts_lang  # noqa: PLW0603
    try:
        import tree_sitter as _ts           # type: ignore
        from tree_sitter import Language as _Lang  # type: ignore
        tree_sitter = _ts
        Language = _Lang
        try:
            import tree_sitter_languages as _tsl  # type: ignore
            _ts_lang = _tsl
        except ImportError:                 # pragma: no cover
            _ts_lang = None
    except ImportError:                     # pragma: no cover
        tree_sitter = None
        Language = None
        _ts_lang = None
    return tree_sitter


def __getattr__(nombre: str):
    """Carga perezosa por acceso a atributo de mÛdulo (v6.9.0).

    Permite que `sc.genai`, `sc.openai`, `sc.anthropic`, `sc.SentenceTransformer`
    o `sc.tree_sitter` disparen la importaciÛn real solo la primera vez que se
    referencian (y devuelven None si la librerÌa no est· instalada), sin
    penalizar el arranque del CLI.
    """
    if nombre == "genai":
        return _importar_genai()
    if nombre == "openai":
        return _importar_openai()
    if nombre == "anthropic":
        return _importar_anthropic()
    if nombre == "SentenceTransformer":
        return _importar_sentence_transformer()
    if nombre in ("tree_sitter", "Language", "_ts_lang"):
        _importar_tree_sitter()
        return globals().get(nombre)
    raise AttributeError(f"mÛdulo 'snapcontext' no tiene atributo {nombre!r}")

# EjecuciÛn en paralelo de pasos del plan (v1.3.0) ‚Äî stdlib, sin deps extra.
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

VERSION = "6.34.2"

# v6.9.0: instante de carga del mÛdulo (lo usa `--benchmark` para medir el
# tiempo de inicio del CLI).
_TIEMPO_INICIO_MODULO = time.perf_counter()

# v4.7.0: lÌmite de lÌneas de un archivo para inyectarlo completo en el prompt
# de ediciÛn. Por encima de este umbral se usa contexto selectivo (resumen AST
# + bloques relevantes) para no explotar la ventana de contexto del modelo.
MAX_CONTEXT_LINES = 600

# v6.1.0: lÌmite de TOKENS estimados a enviar al proveedor en una peticiÛn de
# ediciÛn. Los modelos locales (deepseek-r1:14b, llama3.2‚Ä¶) tienen a menudo
# solo 4096 tokens de contexto; por encima de este umbral se usa
# context_utils.seleccionar_contexto. Configurable con --max-context-tokens.
MAX_CONTEXT_TOKENS = 3000

# v3.1.0 ‚Äî Claves de API reconocidas para el modo por defecto (offline).
CLAVES_API_CONOCIDAS = (
    "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GROQ_API_KEY", "OPENAI_API_KEY",
)

# v3.1.0 ‚Äî Modelos ligeros preferidos en modo offline (por orden de prioridad).
MODELOS_LIGEROS_OLLAMA = ("llama3.2:1b", "llama3.2", "phi3", "gemma2:2b",
                          "qwen2.5:0.5b")

# v3.1.0 ‚Äî Mensaje cuando no hay ni API key ni Ollama disponible.
MENSAJE_SIN_CLAVE_NI_OLLAMA = (
    "No se encontrÛ una API key ni Ollama.\n"
    "Puedes instalar Ollama desde https://ollama.com o configurar una API\n"
    "key con 'snapcontext --init'.\n"
    "Alternativas:\n"
    "  PowerShell :  $env:GEMINI_API_KEY=\"tu_clave\"\n"
    "  Linux/Mac  :  export GEMINI_API_KEY=tu_clave\n"
    "  DiagnÛstico:  snapcontext --diagnostico"
)


# ‚îÄ‚îÄ‚îÄ ConfiguraciÛn por tipo de proyecto ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
# DetecciÛn autom·tica de carpetas y extensiones seg˙n el tipo de proyecto detectado.
_CORRECTORES_CARPETAS_PROYECTO = {
    "flutter": {
        "carpetas_defecto": ["lib", "test", "web"],
        "extensiones": [".dart"]
    },
    "node": {
        "carpetas_defecto": ["src", "backend", "frontend", "lib"],
        "extensiones": [".js", ".ts", ".jsx", ".tsx", ".json"]
    },
    "python": {
        "carpetas_defecto": ["src", "app", "lib", "tests", "scripts"],
        "extensiones": [".py", ".pyx", ".pxd"]
    },
    "go": {
        "carpetas_defecto": ["cmd", "internal", "pkg"],
        "extensiones": [".go"]
    },
    "rust": {
        "carpetas_defecto": ["src", "tests"],
        "extensiones": [".rs", ".toml", ".md"]
    },
    "kotlin": {
        "carpetas_defecto": ["app/src/main/kotlin", "app/src/test/kotlin"],
        "extensiones": [".kt", ".kts"]
    },
    "swift": {
        "carpetas_defecto": ["Sources", "Tests"],
        "extensiones": [".swift"]
    }
}

_CORRECTORES_EXTENSIONES_PROYECTO = {
    "flutter": [".dart"],
    "node": [".js", ".ts", ".jsx", ".tsx", ".json", ".vue", ".svelte"],
    "python": [".py", ".pyx", ".pxd", ".ipynb"],
    "go": [".go", ".mod", ".sum"],
    "rust": [".rs", ".toml", ".md"],
    "kotlin": [".kt", ".kts"],
    "swift": [".swift"]
}

# Mapeo de archivos clave para la detecciÛn autom·tica de tipo de proyecto.
_CORRECTORES_ARCHIVOS_IDENTIFICADORES = {
    "pubspec.yaml": "flutter",
    "package.json": "node",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "build.gradle": "kotlin",
    "Podfile": "swift"
}


_LOGO = r"""
   ‚îå‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îê
   ‚îÇ                                                          ‚îÇ
   ‚îÇ                                                          ‚îÇ
   ‚îÇ    ‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó‚ñà‚ñà‚ñà‚ïó   ‚ñà‚ñà‚ïó ‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó ‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó  ‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó ‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó   ‚îÇ
   ‚îÇ    ‚ñà‚ñà‚ïî‚ïê‚ïê‚ïê‚ïê‚ïù‚ñà‚ñà‚ñà‚ñà‚ïó  ‚ñà‚ñà‚ïë‚ñà‚ñà‚ïî‚ïê‚ïê‚ñà‚ñà‚ïó‚ñà‚ñà‚ïî‚ïê‚ïê‚ñà‚ñà‚ïó‚ñà‚ñà‚ïî‚ïê‚ïê‚ïê‚ïê‚ïù‚ñà‚ñà‚ïî‚ïê‚ïê‚ïê‚ïê‚ïù   ‚îÇ
   ‚îÇ    ‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó‚ñà‚ñà‚ïî‚ñà‚ñà‚ïó ‚ñà‚ñà‚ïë‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïë‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïî‚ïù‚ñà‚ñà‚ïë     ‚ñà‚ñà‚ïë        ‚îÇ
   ‚îÇ    ‚ïö‚ïê‚ïê‚ïê‚ïê‚ñà‚ñà‚ïë‚ñà‚ñà‚ïë‚ïö‚ñà‚ñà‚ïó‚ñà‚ñà‚ïë‚ñà‚ñà‚ïî‚ïê‚ïê‚ïê‚ïù ‚ñà‚ñà‚ïë     ‚ñà‚ñà‚ïë     ‚ñà‚ñà‚ïë        ‚îÇ
   ‚îÇ    ‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïë‚ñà‚ñà‚ïë ‚ïö‚ñà‚ñà‚ñà‚ñà‚ïë‚ñà‚ñà‚ïë  ‚ñà‚ñà‚ïë‚ñà‚ñà‚ïë     ‚ïö‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó‚ïö‚ñà‚ñà‚ñà‚ñà‚ñà‚ñà‚ïó   ‚îÇ
   ‚îÇ    ‚ïö‚ïê‚ïê‚ïê‚ïê‚ïê‚ïê‚ïù‚ïö‚ïê‚ïù  ‚ïö‚ïê‚ïê‚ïê‚ïù‚ïö‚ïê‚ïù  ‚ïö‚ïê‚ïù      ‚ïö‚ïê‚ïê‚ïê‚ïê‚ïê‚ïù ‚ïö‚ïê‚ïê‚ïê‚ïê‚ïê‚ïù   ‚îÇ
   ‚îÇ                                                          ‚îÇ
   ‚îÇ    ¬ª SelecciÛn inteligente de archivos                  ‚îÇ
   ‚îÇ    ¬ª Soporte: Gemini ¬∑ Ollama ¬∑ DeepSeek ¬∑ Groq        ‚îÇ
   ‚îÇ    ¬ª __VERSION__                                             ‚îÇ
   ‚îÇ                                                          ‚îÇ
   ‚îî‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îò
""".replace("__VERSION__", "v" + VERSION)


def _detectar_tipo_proyecto(directorio: str) -> Optional[str]:
    """Detecta autom·ticamente el tipo de proyecto buscando archivos clave.

    Args:
        directorio: Ruta del directorio a analizar (raÌz ya resuelta).

    Returns:
        El tipo detectado (flutter, node, python, go, rust, ‚Ä¶) o None si no hay.
    """
    ruta = Path(directorio)
    if not ruta.is_dir():
        return None

    for nombre_archivo, tipo in _CORRECTORES_ARCHIVOS_IDENTIFICADORES.items():
        if (ruta / nombre_archivo).exists():
            depurar(f"[DetecciÛn] {nombre_archivo} encontrado ‚Üí tipo: {tipo}")
            return tipo

    # Sin archivo identificador, se busca una carpeta tÌpica por tipo.
    for tipo, info in _CORRECTORES_CARPETAS_PROYECTO.items():
        for carpeta in info.get("carpetas_defecto", []):
            if (ruta / carpeta).exists():
                depurar(f"[DetecciÛn] Carpetilla tÌpica de {tipo}: {carpeta}/")
                return tipo
    return None


# Archivos y carpetas que indican que un directorio es raÌz de un proyecto.
# Usado por _es_directorio_proyecto() para la verificaciÛn temprana en main().
_ARCHIVOS_PROYECTO = (
    "package.json", "go.mod", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "pubspec.yaml", "Gemfile", "mix.exs",
)
_CARPETAS_PROYECTO = ("src", "lib", "tests", "app", "scripts")


def _es_directorio_proyecto(directorio: str) -> bool:
    """Indica si ``directorio`` parece ser la raÌz de un proyecto.

    Devuelve ``True`` si existe al menos uno de los archivos/carpetas
    reconocidos como indicadores de proyecto (``src/``, ``package.json``,
    ``go.mod``, ``pyproject.toml``, ``Cargo.toml``, etc.).

    Devuelve ``False`` si el directorio est· vacÌo o solo contiene archivos
    sueltos sin estructura reconocible.
    """
    ruta = Path(directorio)
    if not ruta.is_dir():
        return False

    try:
        entradas = list(ruta.iterdir())
    except OSError:
        return False

    if not entradas:
        return False

    for archivo in _ARCHIVOS_PROYECTO:
        if (ruta / archivo).is_file():
            return True

    # *.csproj (C#): busca cualquier archivo con esa extensiÛn en la raÌz.
    if any(f.suffix == ".csproj" for f in entradas if f.is_file()):
        return True

    for carpeta in _CARPETAS_PROYECTO:
        if (ruta / carpeta).is_dir():
            return True

    return False


def _advertencia_directorio_proyecto(args: argparse.Namespace) -> Optional[int]:
    """Advertencia temprana si el directorio no parece una raÌz de proyecto.

    Se muestra despuÈs del banner y antes de cualquier operaciÛn. En modo
    interactivo ofrece ``[c]`` continuar, ``[d]`` ejecutar la demo o ``[s]``
    salir; en ``--auto`` (o sin entrada interactiva) contin˙a (``c``). Devuelve
    un cÛdigo de salida si debe terminar (``d``/``s``) o ``None`` para seguir
    con el flujo normal. No se muestra si se usÛ ``--no-validar-proyecto`` o un
    flag que no requiera proyecto (``--demo``, ``--init``, ``--chat``‚Ä¶).
    """
    _directorios_proyecto_sin_avisar = frozenset({
        "demo", "init", "chat", "web", "api", "api_generate_key",
        "bienvenida", "diagnostico", "reparar", "historial",
        "historial_limpiar", "setup_path", "iniciar_proyecto",
        "curador", "daemon", "skills", "local",
    })
    _usa_flag_sin_proyecto = any(
        getattr(args, attr, False) for attr in _directorios_proyecto_sin_avisar
    )
    if getattr(args, "no_validar_proyecto", False) or _usa_flag_sin_proyecto:
        return None
    _directorio_actual = getattr(args, "directorio", ".") or "."
    if _es_directorio_proyecto(_directorio_actual):
        return None
    _ui_mostrar_banner(VERSION)
    import ui
    aviso = ("‚Ñπ SnapContext funciona mejor desde la raÌz de un proyecto.\n"
             "No se detectaron archivos de proyecto en este directorio.")
    ui.mostrar_estado(aviso, emoji="??")
    # En modo --auto (o sin entrada interactiva) se contin˙a sin preguntar.
    if getattr(args, "auto", False):
        return None
    opciones = [
        ("c", "Continuar de todas formas"),
        ("d", "Ejecutar demo (--demo)"),
        ("s", "Salir"),
    ]
    eleccion = ui.preguntar_interactivo(
        opciones, "¬øQuÈ quieres hacer?", defecto="c")
    if eleccion == "d":
        return _ejecutar_demo()
    if eleccion == "s":
        info("Hasta luego. Ejecuta snapcontext en la raÌz de tu proyecto.")
        return 0
    return None


def _ajustar_parametros_por_tipo(tipo: Optional[str], args):
    """Ajusta ``args`` seg˙n el tipo de proyecto detectado.

    Solo modifica ``carpetas`` y ``extensiones`` si NO se pasaron explÌcitamente
    por CLI, y es transparente para el usuario (nada se muestra salvo ``--depurar``).
    """
    if getattr(args, "carpetas", None):
        depurar("[DetecciÛn] Carpetas explÌcitas ‚Äî no se sobrescriben.")
        return args

    if not tipo:
        depurar("[DetecciÛn] Sin tipo detectado ‚Äî carpetas por defecto actuales.")
        return args

    info = _CORRECTORES_CARPETAS_PROYECTO.get(tipo)
    if info and not getattr(args, "carpetas", None):
        args.carpetas = list(info["carpetas_defecto"])
        depurar(f"[DetecciÛn] Carpetas para {tipo}: {args.carpetas}")

    extensiones = _CORRECTORES_EXTENSIONES_PROYECTO.get(tipo)
    if extensiones and not getattr(args, "extensiones", None):
        args.extensiones = list(extensiones)
        depurar(f"[DetecciÛn] Extensiones para {tipo}: {args.extensiones}")

    return args

_LOGO_SMALL = f"""
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551           SnapContext  v{VERSION: <9}            \u2551
\u2551   Asistente de IA con contexto autom\u00e1tico    \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d
"""

# ---------------------------------------------------------------------------
# ConfiguraciÛn por defecto (se puede sobrescribir con argumentos CLI o env)
# ---------------------------------------------------------------------------
CARPETAS_DEFECTO = ("lib", "supabase")   # carpetas que se escanean
CARPETAS_PROYECTO_VALIDAS = ("lib", "src", "supabase", "app", "packages", "backend")
# Archivos de configuraciÛn que indican un proyecto v·lido aunque estÈn vacÌos
# (v1.3.0: la validaciÛn ya no exige contenido, solo su presencia).
ARCHIVOS_CONFIG_PROYECTO = frozenset((
    "pubspec.yaml", "package.json", "requirements.txt", "go.mod",
    "cargo.toml", "setup.py", "pyproject.toml",
))
# Extensiones de cÛdigo que, presentes en la raÌz (aunque el archivo estÈ
# vacÌo), tambiÈn validan la carpeta como proyecto.
EXT_CODIGO_RAIZ = frozenset((
    ".py", ".dart", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".kt", ".swift", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
))
PROVEEDOR_DEFECTO = os.environ.get("SNAPCONTEXT_PROVIDER", "gemini")
# SNAPCONTEXT_MODELO (opcional) sobrescribe el modelo por defecto del proveedor.
MODELO_DEFECTO = os.environ.get("SNAPCONTEXT_MODELO") or None
# Preferencias guardadas (~/.snapcontext/config.json): proveedor y modelo.
CONFIG_DIR = Path.home() / ".snapcontext"
CONFIG_PATH = CONFIG_DIR / "config.json"
# v3.1.1: estado ligero de primer uso (~/.snapcontext/estado.json).
ESTADO_PATH = CONFIG_DIR / "estado.json"
BACKUPS_DIR = CONFIG_DIR / "backups"
MAX_ARCHIVOS_DEFECTO = 3                           # archivos que recibe Aider
MAX_CANDIDATOS_DEFECTO = 80                        # candidatos que se envÌan al selector IA
MAX_ITERACIONES_TEST_DEFECTO = 3
COMANDO_TEST_DEFECTO = "flutter test"
MAX_INTENTOS_VALIDACION = 3                        # reintentos de validaciÛn del editor propio

# ---------------------------------------------------------------------------
# Proveedores de IA para la selecciÛn de archivos
# ---------------------------------------------------------------------------
#  tipo           : "gemini" usa la librerÌa google.generativeai;
#                   "openai" usa la librerÌa openai (APIs compatibles con OpenAI).
#  requiere_clave : True exige la variable de entorno `clave_env`.
#  Ollama se conecta a `OLLAMA_URL` (por defecto http://localhost:11434) y no
#  exige clave (opcional: OLLAMA_API_KEY si tu servidor la pidiera).
PROVEEDORES = {
    "gemini": {
        "nombre": "Gemini",
        "tipo": "gemini",
        "clave_env": "GEMINI_API_KEY",
        "requiere_clave": True,
        "modelo_default": "gemini-2.5-flash",
    },
    "ollama": {
        "nombre": "Ollama",
        "tipo": "openai",
        "clave_env": "OLLAMA_API_KEY",       # opcional: servidor local
        "requiere_clave": False,
        "url_env": "OLLAMA_URL",
        "url_default": "http://localhost:11434",
        "modelo_default": "llama3.2",
    },
    "deepseek": {
        "nombre": "DeepSeek",
        "tipo": "openai",
        "clave_env": "DEEPSEEK_API_KEY",
        "requiere_clave": True,
        "base_url": "https://api.deepseek.com",     # API compatible con OpenAI
        "modelo_default": "deepseek-chat",
        # v6.11.0: DeepSeek soporta marcas cache_control (ephemeral).
        "soporta_caching": True,
    },
    "groq": {
        "nombre": "Groq",
        "tipo": "openai",
        "clave_env": "GROQ_API_KEY",
        "requiere_clave": True,
        "base_url": "https://api.groq.com/openai/v1",
        "modelo_default": "llama-3.3-70b-versatile",
    },
    "anthropic": {
        "nombre": "Claude",
        "tipo": "anthropic",                 # SDK oficial `anthropic`
        "clave_env": "ANTHROPIC_API_KEY",
        "requiere_clave": True,
        "url_base": None,                    # se usa la URL oficial por defecto
        "modelo_default": "claude-3-5-sonnet-20241022",
        # v6.11.0: Anthropic (Claude) soporta marcas cache_control (ephemeral).
        "soporta_caching": True,
    },
    # v6.34.0: soporte para GPUs Intel XPU (Intel Arc) vÌa IPEX.
    "xpu": {
        "nombre": "Intel XPU",
        "tipo": "xpu",                       # backend local con IPEX
        "clave_env": None,
        "requiere_clave": False,
        "modelo_default": "Qwen/Qwen3.5-35B-A3B",
        "soporta_caching": False,
    },
}

# Carpetas / extensiones que se ignoran al escanear manualmente. Con git no
# suelen aparecer porque .gitignore ya las excluye, pero sirven de red de
# seguridad en repositorios sin git.
DIRS_IGNORADOS = {
    ".git", ".dart_tool", "build", ".idea", ".vscode", "node_modules",
    "__pycache__", ".venv", "venv", ".pub-cache", "coverage",
}
EXT_IGNORADAS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".icns",
    ".ttf", ".otf", ".woff", ".woff2", ".zip", ".tar", ".gz", ".rar",
    ".7z", ".mp4", ".mp3", ".wav", ".mov", ".pdf", ".class", ".jar",
}

# Palabras vacÌas (espaÒol/inglÈs) que no aportan informaciÛn al buscar.
PALABRAS_VACIAS = {
    "a", "al", "ante", "bajo", "con", "contra", "de", "del", "desde", "e",
    "el", "en", "entre", "es", "esa", "ese", "eso", "esta", "este", "esto",
    "la", "las", "le", "lo", "los", "ni", "no", "o", "para", "pero", "por",
    "que", "se", "si", "sin", "sobre", "su", "una", "unas", "unos", "the",
    "and", "for", "of", "to", "in", "is", "are", "was", "were", "has",
    "have", "it", "this", "that", "with", "on", "at", "from", "by", "as",
    "como", "cada", "cuando", "mas", "muy", "hay", "ya", "tambien",
    "ser", "tu", "sus", "mi", "me", "te", "nos",
}

MAX_LINEAS_CONTENIDO = 250       # lÌneas por archivo que se punt˙an al escanear
TAMANO_MAX_ARCHIVO = 512 * 1024  # bytes; archivos m·s grandes no se leen
MAX_ERROR_SALIDA = 6000          # caracteres de salida de test que se muestran a Aider

# En Windows la consola puede usar cp1252/cp437 y los sÌmbolos unicode rompen
# los print. AquÌ forzamos UTF-8 con reemplazo seguro y, adem·s, tenemos una
# red de seguridad ASCII (ver _texto_seguro / _emitir).
if os.name == "nt":
    for _flujo in (sys.stdout, sys.stderr):
        try:
            _flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

# ---------------------------------------------------------------------------
# Salida por consola (colores ANSI con soporte Windows y NO_COLOR)
# ---------------------------------------------------------------------------
_GLIFOS_ASCII = {
    "\u2139": "[i]",           # ‚Ñπ
    "\u2714": "[OK]",          # ‚úî
    "\u26a0": "[!]",           # ‚ö†
    "\u2716": "[ERROR]",       # ‚úñ
    "\u2022": "-",             # ‚Ä¢
    "\u2192": "->",            # ‚Üí
    "\u2014": "-",            # ‚Äî (em dash)
}


def _consola_es_utf8() -> bool:
    """HeurÌstica: todas las salidas est·ndar soportan UTF-8 sin excepciÛn."""
    for _flujo in (sys.stdout, sys.stderr):
        try:
            codificacion = (_flujo.encoding or "").lower().replace("-", "")
        except Exception:
            codificacion = ""
        if codificacion and "utf" not in codificacion:
            return False
    return True


def _texto_seguro(texto: str) -> str:
    """Reemplaza sÌmbolos Unicode por alternativas ASCII si la consola no es UTF-8."""
    if _consola_es_utf8():
        return texto
    for simbolo, alternativo in _GLIFOS_ASCII.items():
        texto = texto.replace(simbolo, alternativo)
    return texto


# Callback global de eventos hacia la interfaz web (y otros consumidores).
# Recibe dicts con al menos {"tipo": ...}. Se activa con fijar_evento_callback.
EVENTO_CALLBACK = None  # type: ignore[assignment]


def fijar_evento_callback(manejador) -> None:
    """Registra un manejador de eventos (p. ej. la interfaz web).

    ``manejador(dict)`` recibe eventos como ``{\"tipo\": \"log\", ...}`` para
    mostrar en tiempo real lo que hacen el orquestador y los agentes. Pasa
    ``None`` para limpiar el registro.
    """
    global EVENTO_CALLBACK
    EVENTO_CALLBACK = manejador


def _emitir(stream, texto: str) -> None:
    """Escribe texto con seguridad ante codificaciones limitadas."""
    seguro = _texto_seguro(texto)
    try:
        print(seguro, file=stream)
    except UnicodeEncodeError:
        print(seguro.encode("ascii", "replace").decode("ascii"), file=stream)
    # Si hay un manejador registrado (interfaz web), se le difunde el log en
    # tiempo real junto con su nivel, para que la UI lo muestre mientras corre.
    if EVENTO_CALLBACK is not None:
        try:
            EVENTO_CALLBACK({
                "tipo": "log",
                "nivel": "error" if stream is sys.stderr else "info",
                "texto": seguro,
            })
        except Exception:
            pass


def _soporta_color() -> bool:
    """Activa colores solo en terminal interactiva (respeta NO_COLOR)."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") not in (None, "", "0"):
        return True
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


if _soporta_color():
    _VERDE, _AMARILLO, _ROJO, _CYAN, _GRIS, _REINICIO = (
        "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[90m", "\033[0m",
    )
else:
    _VERDE = _AMARILLO = _ROJO = _CYAN = _GRIS = _REINICIO = ""

DEPURAR = False  # se activa con --depurar


def _pintar(texto: str, codigo: str) -> str:
    return f"{codigo}{texto}{_REINICIO}"


_TUI_HUB: object = False  # cachÈ perezosa del hub TUI (False = no probado)


def _tui_log(nivel: str, msg: str) -> None:
    """ReenvÌa un log a la TUI (v6.12.0) si el modo est· activo.

    Nunca lanza ni bloquea: si Textual/tui_hub no est· disponible o la cola
    est· llena, el evento simplemente se descarta. Coste ~0 cuando la TUI
    est· inactiva (una comprobaciÛn booleana).
    """
    global _TUI_HUB
    if _TUI_HUB is False:
        try:
            import tui_hub as _hub
            _TUI_HUB = _hub
        except Exception:                        # noqa: BLE001
            _TUI_HUB = None
    if _TUI_HUB and getattr(_TUI_HUB, "esta_activo", lambda: False)():
        try:
            _TUI_HUB.enviar_log(nivel, str(msg))
        except Exception:                        # noqa: BLE001 ‚Äî nunca romper
            pass


def info(msg: str) -> None:
    _tui_log("info", msg)
    _emitir(sys.stdout, _pintar("\u2139 " + msg, _CYAN))


def exito(msg: str) -> None:
    _tui_log("info", msg)
    _emitir(sys.stdout, _pintar("\u2714 " + msg, _VERDE))


def aviso(msg: str) -> None:
    _tui_log("warning", msg)
    _emitir(sys.stdout, _pintar("\u26a0 " + msg, _AMARILLO))


def error(msg: str) -> None:
    _tui_log("error", msg)
    _emitir(sys.stderr, _pintar("\u2716 " + msg, _ROJO))


def depurar(msg: str) -> None:
    if DEPURAR:
        _emitir(sys.stdout, _pintar("  [depuraciÛn] " + msg, _GRIS))


# ---------------------------------------------------------------------------
# Mensajes de error reutilizables
# ---------------------------------------------------------------------------
MENSAJE_GENAI_FALTANTE = (
    "No se encontrÛ la librerÌa 'google.generativeai'.\n"
    "Inst·lala con:  pip install google-generativeai   (o: pip install -e .)"
)
MENSAJE_API_KEY = (
    "No se encontrÛ la variable de entorno GEMINI_API_KEY.\n"
    "Crea una API key en https://aistudio.google.com/apikey y config˙rala:\n"
    "  PowerShell:  $env:GEMINI_API_KEY=\"tu_clave\"\n"
    "  Linux/Mac :  export GEMINI_API_KEY=tu_clave"
)
MENSAJE_AIDER_FALTANTE = (
    "No se encontrÛ el comando 'aider' en el PATH.\n"
    "Inst·lalo con:  pip install aider-chat"
)
MENSAJE_OPENAI_FALTANTE = (
    "Este proveedor usa la librerÌa 'openai' (API compatible con OpenAI).\n"
    "Inst·lala con:  pip install openai"
)
MENSAJE_ANTHROPIC_FALTANTE = (
    "Este proveedor usa la librerÌa 'anthropic' (API oficial de Claude).\n"
    "Inst·lala con:  pip install snapcontext[anthropic]\n"
    "  (o directamente: pip install anthropic>=0.30.0)"
)
# Memoria persistente (~/.snapcontext/historial.json): ˙ltimas tareas realizadas.
HISTORIAL_PATH = CONFIG_DIR / "historial.json"
MAX_HISTORIAL_ENTRADAS = 200      # se recorta para que el archivo no crezca sin lÌmite

# ---------------------------------------------------------------------------
# SeÒales y cierre limpio (Ctrl+C / SIGTERM) ‚Äî multiplataforma
# ---------------------------------------------------------------------------
# Registro de subprocesos activos (servidores Flutter...) para poder cerrarlos
# desde el manejador de seÒales y no dejar procesos huÈrfanos.
_PROCESOS_ACTIVOS: set = set()


def _apagar_subprocesos() -> None:
    """Termina todos los subprocesos registrados (no espera a que salgan)."""
    for proceso in list(_PROCESOS_ACTIVOS):
        try:
            if proceso.poll() is None:
                proceso.terminate()
        except (AttributeError, OSError, ValueError):
            pass


def _registrar_manejadores_senales() -> None:
    """Instala manejadores para SIGINT (Ctrl+C) y, en Unix, SIGTERM.

    Finalizan de forma limpia: cierran los subprocesos activos y salen con
    cÛdigo 0 (cierre controlado en lugar de la excepciÛn por defecto). Se
    protege con try/except por si la plataforma no permite registrar alguna
    seÒal (p. ej. SIGTERM no se entrega en Windows).
    """
    def _manejar(signum, frame):  # noqa: ARG001
        _apagar_subprocesos()
        # v6.4.0: si hay una sesiÛn Docker persistente, destruirla en Ctrl+C /
        # SIGTERM para no dejar contenedores huÈrfanos.
        try:
            _destruir_sesion_si_aplica()
        except Exception:                                  # noqa: BLE001
            pass
        error(f"SeÒal {signum} recibida. SnapContext se est· cerrando...")
        raise SystemExit(0)

    for senal in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if senal is None:
            continue
        try:
            signal.signal(senal, _manejar)
        except (ValueError, OSError):
            continue

# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    """Min˙sculas y sin acentos. 'botÛn' -> 'boton' (clave para buscar en espaÒol)."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def tokenizar(consulta: str) -> List[str]:
    """Convierte la consulta en palabras clave ˙tiles (sin stopwords)."""
    tokens = re.findall(r"[a-z0-9_]+", normalizar(consulta))
    return [t for t in tokens if len(t) > 1 and t not in PALABRAS_VACIAS]


# ---------------------------------------------------------------------------
# ResoluciÛn del repositorio
# ---------------------------------------------------------------------------
def encontrar_raiz_git(inicio: Path) -> Optional[Path]:
    """Busca hacia arriba un directorio .git partiendo de `inicio`."""
    actual = inicio
    while True:
        if (actual / ".git").exists():
            return actual
        if actual.parent == actual:
            return None
        actual = actual.parent


def resolver_raiz(directorio: str) -> Path:
    """Resuelve el directorio objetivo.

    - Si el usuario pasa `--directorio` explÌcito, se usa esa ruta tal cual
      (solo se comporta como repo git si contiene .git directamente). AsÌ un
      directorio suelto (p. ej. una copia en %TEMP%) no "hereda" repos git
      de carpetas padre (como el home de usuario).
    - Si no se pasa directorio (por defecto: '.'), se busca la raÌz del repo
      git hacia arriba, para que el escaneo funcione desde cualquier subcarpeta
      del proyecto.
    """
    ruta = Path(directorio).expanduser().resolve()
    if not ruta.is_dir():
        raise RuntimeError(f"El directorio no existe: {directorio}")
    if directorio not in (".", ""):
        return ruta
    return encontrar_raiz_git(ruta) or ruta


def _es_proyecto_valido(directorio: Union[str, Path]) -> bool:
    """Devuelve True si 'directorio' tiene indicios de ser un proyecto.

    Criterios (v1.3.0, m·s permisivos para proyectos nuevos):
      - Existe al menos una carpeta tÌpica (lib/, src/, supabase/, app/,
        packages/, backend/), AUNQUE EST√â VAC√çA.
      - O existe al menos un archivo de cÛdigo en la raÌz (.py, .dart, .js,
        .ts, .go, .rs, .java, ...), AUNQUE EST√â VAC√çO.
      - O existe un archivo de configuraciÛn tÌpico (pubspec.yaml,
        package.json, requirements.txt, go.mod, Cargo.toml, setup.py,
        pyproject.toml), AUNQUE EST√â VAC√çO.
    """
    ruta = Path(directorio)
    if not ruta.is_dir():
        return False
    # 1) Carpetas tÌpicas (aunque estÈn vacÌas).
    if any((ruta / carpeta).is_dir() for carpeta in CARPETAS_PROYECTO_VALIDAS):
        return True
    try:
        entradas = list(ruta.iterdir())
    except OSError:
        return False
    for entrada in entradas:
        nombre = entrada.name.lower()
        # 2) Archivo de configuraciÛn tÌpico en la raÌz (aunque vacÌo).
        if nombre in ARCHIVOS_CONFIG_PROYECTO:
            return True
        # 3) Archivo de cÛdigo en la raÌz (aunque vacÌo).
        if entrada.is_file() and entrada.suffix.lower() in EXT_CODIGO_RAIZ:
            return True
    return False


def _normalizar_relativa(ruta: str) -> str:
    """Normaliza una ruta relativa a POSIX sin '.' ni '..' ni dobles '//'.

    Se usa para que los archivos que pasan a Aider (o que aÒade el usuario)
    sean siempre rutas limpias relativas al repositorio.
    """
    limpia = ruta.replace("\\", "/").strip()
    if limpia.startswith("./"):
        limpia = limpia[2:]
    partes = []
    for p in limpia.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if partes:
                partes.pop()
            continue
        partes.append(p)
    return "/".join(partes)


def _esta_dentro(raiz: Path, relativa: str) -> bool:
    """True si `raiz / relativa` resuelve dentro de `raiz` (bloquea '..')."""
    try:
        (raiz / relativa).resolve().relative_to(raiz.resolve())
        return True
    except ValueError:
        return False

# ---------------------------------------------------------------------------
# Escaneo del repositorio (b˙squeda local de candidatos)
# ---------------------------------------------------------------------------
def _pertenece_a_carpetas(ruta: str, carpetas: List[str]) -> bool:
    """True si la ruta relativa cae dentro de alguna carpeta de interÈs."""
    for carpeta in carpetas:
        prefijo = carpeta.replace("\\", "/").rstrip("/") + "/"
        if ruta.startswith(prefijo) or ruta == carpeta.rstrip("/"):
            return True
    return False


def _es_archivo_indexable(ruta: str) -> bool:
    """Descarta binarios, im·genes, fuentes y archivos en carpetas ignoradas."""
    if Path(ruta).suffix.lower() in EXT_IGNORADAS:
        return False
    partes = ruta.split("/")
    return not any(p in DIRS_IGNORADOS for p in partes[:-1])


def listar_archivos_candidatos(raiz: Path, carpetas: List[str],
                               extensiones: Optional[List[str]] = None) -> List[str]:
    """Devuelve las rutas (relativas, formato POSIX) de `carpetas` bajo `raiz`.

    Prioridad:
      1. `git ls-files -c -o --exclude-standard`: respeta .gitignore e incluye
         archivos nuevos a˙n sin commitear.
      2. Si no hay repo git (o falla), recorre el ·rbol con os.walk.
    """
    coleccion: List[str] = []
    usa_git = (raiz / ".git").exists()

    if usa_git:
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.run(
                ["git", "ls-files", "-c", "-o", "--exclude-standard"],
                cwd=str(raiz), capture_output=True, text=True,
                timeout=60, creationflags=flags,
            )
            if proc.returncode == 0:
                for linea in proc.stdout.splitlines():
                    ruta = linea.strip()
                    if ruta and not ruta.startswith('"'):
                        coleccion.append(ruta.replace("\\", "/"))
            else:
                depurar(f"git ls-files devolviÛ {proc.returncode}; se usar· os.walk")
                usa_git = False
        except (OSError, subprocess.SubprocessError):
            depurar("git no est· disponible; se usar· os.walk")
            usa_git = False

    if not usa_git or not coleccion:
        for carpeta in carpetas:
            base = raiz / carpeta
            if base.is_dir():
                for directorio_actual, subdirs, archivos in os.walk(base):
                    subdirs[:] = [d for d in subdirs if d not in DIRS_IGNORADOS]
                    for nombre in archivos:
                        ruta = (Path(directorio_actual) / nombre).relative_to(raiz).as_posix()
                        coleccion.append(ruta)

    def _cumple_extension(ruta: str) -> bool:
        if not extensiones:
            return True
        permitidas = {ext.lower() for ext in extensiones}
        return Path(ruta).suffix.lower() in permitidas

    return sorted({
        ruta for ruta in coleccion
        if _pertenece_a_carpetas(ruta, carpetas)
        and _es_archivo_indexable(ruta)
        and _cumple_extension(ruta)
    })


def puntuar_ruta(ruta: str, tokens: List[str]) -> float:
    """Puntos por coincidencia en la ruta: el nombre del archivo pesa m·s que
    el directorio. Ej.: 'procesar-pago-mp/index.ts' y tokens ['boton','pago']
    reciben puntos por 'pago' en la carpeta."""
    partes = [normalizar(p) for p in ruta.split("/")]
    nombre = partes[-1] if partes else ""
    puntuacion = 0.0
    for tk in tokens:
        if tk in nombre:
            puntuacion += 3.0
        for parte in partes[:-1]:
            if tk in parte:
                puntuacion += 1.0
                break  # un punto por carpeta coincidente, como m·ximo
    return puntuacion


def puntuar_contenido(archivo: Path, tokens: List[str]) -> float:
    """Lee las primeras lÌneas del archivo y suma cu·ntas veces aparece cada
    token (limitado para darle balanza a los archivos muy verbosos)."""
    try:
        if archivo.stat().st_size > TAMANO_MAX_ARCHIVO:
            return 0.0
    except OSError:
        return 0.0

    try:
        lineas: List[str] = []
        with open(archivo, "r", encoding="utf-8", errors="ignore") as fh:
            for _ in range(MAX_LINEAS_CONTENIDO):
                linea = fh.readline()
                if not linea:
                    break
                lineas.append(linea)
    except OSError:
        return 0.0

    texto = normalizar(" ".join(lineas))
    return float(sum(min(texto.count(tk), 12) for tk in tokens))


def escanear_repositorio(consulta: str, directorio: str = ".",
                         carpetas: Optional[List[str]] = None,
                         extensiones: Optional[List[str]] = None,
                         max_candidatos: int = MAX_CANDIDATOS_DEFECTO) -> List[str]:
    """Escanea el repositorio y devuelve los mejores candidatos (heurÌstica
    local) para la consulta, ordenados de m·s a menos relevante.

    Fases:
      1. Listar archivos de `carpetas` (con git o walking).
      2. Si hay muchos, pre-filtrar por coincidencia en la ruta.
      3. Puntuar tambiÈn el contenido de los que quedaron.
      4. Devolver los `max_candidatos` mejores para que Gemini elija.
    """
    carpetas = list(carpetas) if carpetas else list(CARPETAS_DEFECTO)
    raiz = resolver_raiz(directorio)
    archivos = listar_archivos_candidatos(raiz, carpetas, extensiones=extensiones)
    if not archivos:
        return []

    tokens = tokenizar(consulta)
    if not tokens:
        # Consulta sin palabras clave ˙tiles: se devuelve una muestra ordenada.
        return archivos[:max_candidatos]

    if len(archivos) > 200:
        # Pre-filtro por ruta para no leer el contenido de miles de archivos.
        archivos = sorted(
            archivos, key=lambda p: puntuar_ruta(p, tokens), reverse=True
        )[:200]

    puntuados: List[tuple] = []
    # v4.8.0: barra de progreso durante el escaneo (silenciosa con --auto).
    for ruta in _ui_mostrar_progreso(archivos,
                                     "‚öôÔ∏è Escaneando archivos del repo..."):
        puntuados.append(
            (ruta,
             puntuar_ruta(ruta, tokens)
             + 0.5 * puntuar_contenido(raiz / ruta, tokens))
        )
    puntuados.sort(key=lambda par: par[1], reverse=True)
    return [p for p, _ in puntuados[:max_candidatos]]

# ---------------------------------------------------------------------------
# SelecciÛn con Gemini (elige los archivos m·s relevantes entre candidatos)
# ---------------------------------------------------------------------------
def construir_prompt_seleccion(consulta: str, archivos: List[str],
                               max_archivos: int) -> str:
    """Prompt que pide a Gemini elegir las `max_archivos` rutas m·s relevantes
    respondiendo solo con JSON (facilita el parseo)."""
    lista = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(archivos))
    return (
        "Eres el mÛdulo de selecciÛn de archivos de SnapContext, una herramienta "
        "de IA para desarrollo con Flutter y Supabase.\n\n"
        f"TAREA A RESOLVER (la pidiÛ el desarrollador):\n\"{consulta}\"\n\n"
        f"ARCHIVOS CANDIDATOS (rutas relativas al repositorio):\n{lista}\n\n"
        f"Devuelve EXCLUSIVAMENTE un ˙nico objeto JSON v·lido, sin markdown y sin "
        f"texto adicional, con la clave \"archivos\" cuyo valor es un array con "
        f"EXACTAMENTE {max_archivos} rutas tomadas de la lista anterior, escritas "
        "en el MISMO formato exacto (sin \"./\" y sin modificarlas), ordenadas de "
        "m·s a menos relevantes para resolver la tarea.\n"
        "Prioriza los archivos que probablemente necesiten MODIFICARSE, no solo "
        "los que aportan contexto.\n"
        'Ejemplo de formato:\n{"archivos": ["lib/features/.../a.dart", '
        '"supabase/migrations/b.sql"]}'
    )


def parsear_json(texto) -> Optional[object]:
    """Convierte la respuesta del modelo en Python de forma tolerante: quita
    cercas de cÛdigo ```json y busca el bloque JSON m·s grande de la respuesta."""
    if not texto:
        return None
    candidato = texto.strip()
    candidato = re.sub(r"^```(?:json)?\s*", "", candidato, flags=re.I)
    candidato = re.sub(r"\s*```$", "", candidato)

    for patron in (r"\{.*\}", r"\[.*\]"):
        coincidencia = re.search(patron, candidato, re.S)
        if not coincidencia:
            continue
        try:
            return json.loads(coincidencia.group(0))
        except json.JSONDecodeError:
            continue
    return None


def normalizar_seleccion(datos, disponibles: List[str],
                         max_archivos: int) -> List[str]:
    """Valida y deduplica las rutas devueltas por el modelo: solo se aceptan
    rutas de la lista de disponibles y respetando el lÌmite."""
    rutas = []
    if isinstance(datos, dict):
        for clave in ("archivos", "files", "rutas", "seleccion"):
            if isinstance(datos.get(clave), list):
                rutas = datos[clave]
                break
    elif isinstance(datos, list):
        rutas = datos

    disponibles_set = set(disponibles)
    seleccion: List[str] = []
    for ruta in rutas:
        if not isinstance(ruta, str):
            continue
        limpia = ruta.strip().replace("\\", "/").lstrip("./")
        if limpia in disponibles_set and limpia not in seleccion:
            seleccion.append(limpia)
        if len(seleccion) >= max_archivos:
            break
    return seleccion


def seleccionar_archivos_con_gemini(consulta: str, archivos: List[str],
                                    max_archivos: int = MAX_ARCHIVOS_DEFECTO,
                                    modelo: Optional[str] = None) -> List[str]:
    """Usa Gemini para quedarse con los `max_archivos` candidatos relevantes.

    Errores controlados con mensajes claros:
      - librerÌa google.generativeai no instalada  -> MENSAJE_GENAI_FALTANTE
      - variable GEMINI_API_KEY sin configurar      -> MENSAJE_API_KEY
      - errores de red/API de Google                -> RuntimeError descriptivo
    """
    if _importar_genai() is None:
        raise RuntimeError(MENSAJE_GENAI_FALTANTE)

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(MENSAJE_API_KEY)

    modelo = modelo or PROVEEDORES["gemini"]["modelo_default"]
    info(f"Seleccionando con Gemini ({modelo})...")

    genai.configure(api_key=api_key)
    prompt = construir_prompt_seleccion(consulta, archivos, max_archivos)
    depurar(f"Prompt de selecciÛn: {len(prompt)} caracteres, {len(archivos)} candidatos")

    generador = genai.GenerativeModel(model_name=modelo)
    configuracion = genai.types.GenerationConfig(
        temperature=0.2,
        response_mime_type="application/json",  # pedimos JSON estructurado
    )

    try:
        respuesta = generador.generate_content(prompt, generation_config=configuracion)
    except Exception as exc:  # errores de red, cuota agotada, modelo inv·lido...
        raise RuntimeError(f"Error al llamar a Gemini: {exc}") from exc

    depurar(f"Respuesta de Gemini ({len(respuesta.text)} caracteres): {respuesta.text[:200]}")
    datos = parsear_json(respuesta.text)
    return normalizar_seleccion(datos, archivos, max_archivos)


def _resolver_url_openai(cfg: dict) -> str:
    """URL base para proveedores de tipo 'openai'.

    DeepSeek/Groq traen su base_url en la configuraciÛn. Ollama se conecta a
    `OLLAMA_URL` (por defecto http://localhost:11434) y se completa con /v1,
    que es su endpoint compatible con la API de OpenAI.
    """
    if cfg.get("base_url"):
        return cfg["base_url"]
    url = os.environ.get(cfg["url_env"], "").strip() or cfg["url_default"]
    url = url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _mensaje_clave_faltante(proveedor: str, cfg: dict) -> str:
    """Mensaje claro cuando falta la clave de un proveedor OpenAI-compatible."""
    var = cfg["clave_env"]
    return (
        f"No se encontrÛ la variable de entorno {var} (necesaria para "
        f"{cfg['nombre']}, proveedor '{proveedor}').\n"
        f"  PowerShell:  $env:{var}=\"tu_clave\"\n"
        f"  Linux/Mac :  export {var}=tu_clave"
    )


def seleccionar_archivos_con_openai(consulta: str, archivos: List[str],
                                    proveedor: str, modelo: str,
                                    max_archivos: int = MAX_ARCHIVOS_DEFECTO) -> List[str]:
    """Selecciona archivos con DeepSeek, Groq u Ollama (APIs estilo OpenAI).

    Modelos sugeridos por proveedor (se sobrescriben con --model):
      - DeepSeek : deepseek-chat / deepseek-reasoner
      - Groq     : llama-3.3-70b-versatile, llama-3.1-8b-instant...
      - Ollama   : llama3.2, qwen2.5, codellama... (deben estar descargados)
    """
    if _importar_openai() is None:
        raise RuntimeError(MENSAJE_OPENAI_FALTANTE)

    cfg = PROVEEDORES[proveedor]
    api_key = os.environ.get(cfg["clave_env"], "").strip()
    if cfg["requiere_clave"] and not api_key:
        raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))

    base_url = _resolver_url_openai(cfg)
    info(f"Seleccionando con {cfg['nombre']} ({modelo})...")
    depurar(f"{cfg['nombre']} ‚Üí base_url={base_url}, modelo={modelo}")

    cliente = openai.OpenAI(
        api_key=api_key or "ollama-local",  # Ollama no exige clave; el SDK pide un valor
        base_url=base_url,
        timeout=120,
    )
    prompt = construir_prompt_seleccion(consulta, archivos, max_archivos)
    depurar(f"Prompt de selecciÛn: {len(prompt)} caracteres, {len(archivos)} candidatos")

    mensajes = [{"role": "user", "content": prompt}]
    try:
        try:
            respuesta = cliente.chat.completions.create(
                model=modelo, messages=mensajes,
                temperature=0.2, response_format={"type": "json_object"},
            )
        except Exception:
            # Algunos endpoints (p. ej. ciertas versiones de Ollama) no aceptan
            # response_format; reintentamos sin Èl (el prompt ya pide JSON).
            respuesta = cliente.chat.completions.create(
                model=modelo, messages=mensajes, temperature=0.2,
            )
    except Exception as exc:  # red, clave inv·lida, modelo inexistente...
        raise RuntimeError(f"Error al llamar a {cfg['nombre']}: {exc}") from exc

    texto = ""
    try:
        texto = respuesta.choices[0].message.content or ""
    except Exception:
        texto = ""
    depurar(f"Respuesta de {cfg['nombre']} ({len(texto)} caracteres): {texto[:200]}")

    return normalizar_seleccion(parsear_json(texto), archivos, max_archivos)


def seleccionar_archivos_con_anthropic(consulta: str, archivos: List[str],
                                       max_archivos: int = MAX_ARCHIVOS_DEFECTO,
                                       modelo: Optional[str] = None) -> List[str]:
    """Selecciona archivos con Claude (Anthropic) usando su SDK oficial.

    Errores controlados con mensajes claros:
      - librerÌa `anthropic` no instalada        -> MENSAJE_ANTHROPIC_FALTANTE
      - variable ANTHROPIC_API_KEY sin configurar -> mensaje con la var exacta
      - errores de red/API de Anthropic           -> RuntimeError descriptivo
    """
    if _importar_anthropic() is None:
        raise RuntimeError(MENSAJE_ANTHROPIC_FALTANTE)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "No se encontrÛ la variable de entorno ANTHROPIC_API_KEY "
            "(necesaria para Claude, proveedor 'anthropic').\n"
            "  PowerShell:  $env:ANTHROPIC_API_KEY=\"tu_clave\"\n"
            "  Linux/Mac :  export ANTHROPIC_API_KEY=tu_clave"
        )

    modelo = modelo or PROVEEDORES["anthropic"]["modelo_default"]
    info(f"Seleccionando con Claude ({modelo})...")

    cliente = anthropic.Anthropic(api_key=api_key)
    prompt = construir_prompt_seleccion(consulta, archivos, max_archivos)
    depurar(f"Prompt de selecciÛn: {len(prompt)} caracteres, {len(archivos)} candidatos")

    try:
        respuesta = cliente.messages.create(
            model=modelo,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
    except Exception as exc:  # red, clave inv·lida, modelo inexistente...
        raise RuntimeError(f"Error al llamar a Claude (Anthropic): {exc}") from exc

    texto = ""
    try:
        # messages.create devuelve bloques de contenido; concatenamos los de texto.
        texto = "".join(
            bloque.text for bloque in respuesta.content
            if getattr(bloque, "type", None) == "text"
        )
    except Exception:
        texto = ""
    depurar(f"Respuesta de Claude ({len(texto)} caracteres): {texto[:200]}")

    return normalizar_seleccion(parsear_json(texto), archivos, max_archivos)


# ---------------------------------------------------------------------------
# Estado de primer uso (v3.1.1): ~/.snapcontext/estado.json
# ---------------------------------------------------------------------------
def _cargar_estado() -> dict:
    """Lee ~/.snapcontext/estado.json. Devuelve {} si no existe o falla."""
    try:
        if ESTADO_PATH.is_file():
            datos = json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                return datos
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _guardar_estado(datos: dict) -> bool:
    """Escribe el dict de estado en ESTADO_PATH. True si tuvo Èxito."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ESTADO_PATH.write_text(
            json.dumps(datos, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return True
    except OSError:
        return False


def _primer_uso_pendiente() -> bool:
    """True si la bienvenida a˙n no se ha mostrado (estado ausente o True)."""
    estado = _cargar_estado()
    return bool(estado.get("primer_uso", True))


def _marcar_primer_uso_completado() -> None:
    """Guarda primer_uso=False (nunca rompe el flujo principal)."""
    estado = _cargar_estado()
    estado["primer_uso"] = False
    _guardar_estado(estado)


def _entrada_interactiva() -> bool:
    """True si stdin es un terminal (evita bloqueos en tests/CI/scripts)."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):       # pragma: no cover
        return False


def cargar_configuracion() -> dict:
    """Lee la configuraciÛn guardada en ~/.snapcontext/config.json.

    El archivo es un JSON con las claves 'provider' y, opcionalmente, 'model'.
    Si no existe o est· corrupto, se devuelve un dict vacÌo.

    CORRECCI√ìN 0.6.0: Manejo explÌcito de FileNotFoundError y json.JSONDecodeError
    para evitar silenciar errores importantes sin aviso.
    """
    try:
        if CONFIG_PATH.is_file():
            datos = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                return datos
        elif not CONFIG_PATH.exists():
            # Archivo no existe a˙n; devolver vacÌo sin error
            return {}
    except FileNotFoundError:
        aviso(f"Archivo de configuraciÛn no encontrado: {CONFIG_PATH}")
        pass  # Devolver {} si el directorio/config no existe a˙n
    except json.JSONDecodeError as exc:
        error(f"ConfiguraciÛn corrupta en {CONFIG_PATH}: {exc}")
        pass  # No intentar recuperar, devolver {} para evitar estado inconsistente
    except (OSError, ValueError) as exc:
        aviso(f"Error leyendo configuraciÛn: {type(exc).__name__}: {exc}")
        pass  # Opcional: continuar sin la configuraciÛn previa
    return {}


def guardar_configuracion(provider: str, model: Optional[str] = None,
                          api_keys: Optional[dict] = None) -> bool:
    """Guarda el proveedor preferido, modelo opcional y claves API.

    Recibe adem·s `api_keys` (dict {proveedor: clave}) que se mezcla con las
    existentes, de modo que guardar solo el proveedor (como hace
    `_determinador_proveedor`) no borre las claves ya configuradas con --init.
    Devuelve True si se escribiÛ correctamente en ~/.snapcontext/config.json.
    """
    try:
        existente = cargar_configuracion()
        claves = dict(existente.get("api_keys") or {})
        if api_keys:
            claves.update({k: v for k, v in api_keys.items() if v})

        datos: dict = {"provider": provider}
        if model:
            datos["model"] = model
        if claves:
            datos["api_keys"] = claves

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError:
        return False


def _actualizar_clave_configuracion(clave: str, valor) -> bool:
    """Actualiza una clave arbitraria de ~/.snapcontext/config.json.

    A diferencia de :func:`guardar_configuracion` (que reescribe solo
    proveedor/modelo/claves), preserva el resto del JSON (asesor, api_key...).
    """
    try:
        datos = cargar_configuracion()
        datos[clave] = valor
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def _generar_clave_api(guardar: bool = True) -> str:
    """Genera una clave API segura (url-safe, 32 bytes) para la API v3.6.0.

    Si ``guardar`` es True, la persiste en ``~/.snapcontext/config.json``
    bajo la clave ``"api_key"``.
    """
    import secrets

    clave = secrets.token_urlsafe(32)
    if guardar:
        _actualizar_clave_configuracion("api_key", clave)
    return clave


def _importar_questionary():
    """Devuelve el mÛdulo 'questionary' o None si no est· instalado."""
    try:
        import questionary
        return questionary
    except ImportError:  # pragma: no cover
        return None


def _listar_modelos_ollama() -> tuple:
    """Devuelve (modelos, error) consultando los modelos locales vÌa `ollama list`.

    La primera columna de cada fila (la cabecera se ignora) es el nombre del
    modelo. Si `ollama` no est· o falla, devuelve ([], mensaje de error).
    """
    try:
        proc = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError:
        return [], "No se encontrÛ 'ollama' en el PATH. ¬øEst· instalado?"
    except subprocess.TimeoutExpired:
        return [], "El comando 'ollama list' tardÛ demasiado (60 s)."
    except OSError as exc:
        return [], f"No se pudo ejecutar 'ollama list': {exc}"

    if proc.returncode != 0:
        fallo = (proc.stderr or proc.stdout or "").strip()
        return [], fallo or "El comando 'ollama list' devolviÛ un error."

    modelos: List[str] = []
    for num_linea, linea in enumerate((proc.stdout or "").splitlines()):
        if num_linea == 0:          # cabecera: ID  NAME  SIZE  MODIFIED
            continue
        partes = linea.split()
        if partes:
            modelos.append(partes[0])
    return modelos, None


def seleccionar_proveedor_interactivo() -> tuple:
    """Men˙ interactivo (questionary) para elegir proveedor y, si es Ollama,
    su modelo local. Devuelve (provider, model).

    - Pregunta primero si se quiere elegir el proveedor ahora.
    - Si se elige Ollama, se auto-detectan los modelos con `ollama list`.
      Sin modelos / sin ollama instalado, se avisa y se ofrece volver al men˙
      de proveedores o usar Gemini por defecto.
    - Sin questionary se avisa y se usa PROVEEDOR_DEFECTO (gemini), model None.
    """
    questionary = _importar_questionary()
    if questionary is None:
        _emitir(
            sys.stdout,
            "?? Para usar el modo interactivo, instala: pip install questionary",
        )
        return (PROVEEDOR_DEFECTO, None)

    if not questionary.confirm("¬øDeseas seleccionar el proveedor de IA ahora?").ask():
        return (PROVEEDOR_DEFECTO, None)

    while True:
        opciones = [
            questionary.Choice("Gemini (Google)", value="gemini"),
            questionary.Choice("Claude (Anthropic)", value="anthropic"),
            questionary.Choice("Ollama (local)", value="ollama"),
            questionary.Choice("DeepSeek (API)", value="deepseek"),
            questionary.Choice("Groq (API)", value="groq"),
        ]
        proveedor = questionary.select(
            "?? Selecciona el proveedor de IA:",
            choices=opciones,
        ).ask() or PROVEEDOR_DEFECTO

        # Ollama ‚Üí auto-detecciÛn de modelos locales (Mejora 2).
        if proveedor == "ollama":
            modelos, error = _listar_modelos_ollama()
            if modelos:
                elegido = questionary.select(
                    "?? Selecciona el modelo de Ollama:",
                    choices=list(modelos),
                ).ask()
                return ("ollama", elegido or modelos[0])

            if error:
                aviso(f"No se pudieron listar modelos de Ollama: {error}")
            else:
                aviso("Ollama no tiene modelos instalados. "
                      "Prueba: ollama pull llama3.2")
            usar_gemini = questionary.confirm(
                "¬øQuieres usar Gemini por defecto? (No = volver al proveedor)"
            ).ask()
            if usar_gemini:
                return ("gemini", None)
            # Si responde "no": vuelve al men˙ de proveedores.
            continue

        return (proveedor, None)


def _preguntar_guardar_config() -> bool:
    """Pregunta si guardar el proveedor elegido como predeterminado.

    Solo hace la pregunta si questionary est· instalada; si no, devuelve False
    y no se persiste nada (comportamiento elegante sin dependencia extra).
    """
    questionary = _importar_questionary()
    if questionary is None:
        return False
    return bool(
        questionary.confirm(
            "¬øGuardar este proveedor como predeterminado?"
        ).ask()
    )


def _probar_conexion_proveedor(provider: str, model: Optional[str] = None) -> bool:
    """Comprueba la conexiÛn con la API del proveedor elegido (usado por --init).

    Reutiliza la clave guardada en la configuraciÛn o, como plan B, la variable
    de entorno correspondiente. Hace una llamada mÌnima y devuelve True si ok.
    """
    cfg = PROVEEDORES[provider]
    api_keys = cargar_configuracion().get("api_keys") or {}

    if provider == "gemini":
        if _importar_genai() is None:
            aviso("Falta google-generativeai. Instala: pip install google-generativeai")
            return False
        clave = (api_keys.get("gemini") or "").strip() \
            or os.environ.get("GEMINI_API_KEY", "").strip()
        if not clave:
            aviso("No se encontrÛ ninguna clave de Gemini.")
            return False
        try:
            genai.configure(api_key=clave)
            genai.GenerativeModel(model or cfg["modelo_default"]).generate_content("responde ok")
            return True
        except Exception:
            return False

    # Claude (Anthropic): SDK oficial, distinto de la API estilo OpenAI.
    if provider == "anthropic":
        if _importar_anthropic() is None:
            aviso(MENSAJE_ANTHROPIC_FALTANTE)
            return False
        clave = (api_keys.get("anthropic") or "").strip() \
            or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not clave:
            aviso("No se encontrÛ ninguna clave de Anthropic.")
            return False
        try:
            cliente = anthropic.Anthropic(api_key=clave)
            cliente.messages.create(
                model=model or cfg["modelo_default"],
                max_tokens=5,
                messages=[{"role": "user", "content": "responde ok"}],
            )
            return True
        except Exception:
            return False

    # Proveedores con API estilo OpenAI (Groq, DeepSeek y Ollama).
    if _importar_openai() is None:
        aviso(MENSAJE_OPENAI_FALTANTE)
        return False
    clave = (api_keys.get(provider) or "").strip() \
        or os.environ.get(cfg["clave_env"], "").strip()
    base_url = _resolver_url_openai(cfg)
    try:
        cliente = openai.OpenAI(api_key=clave or "ollama", base_url=base_url)
        cliente.chat.completions.create(
            model=model or cfg["modelo_default"],
            messages=[{"role": "user", "content": "responde ok"}],
            max_tokens=5,
        )
        return True
    except Exception:
        return False


def asistente_configuracion_inicial() -> int:
    """Asistente interactivo de configuraciÛn inicial (SNAPCONTEXT --init).

    GuÌa en la configuraciÛn de claves API y el proveedor/modelo favorito en
    ~/.snapcontext/config.json. Devuelve el cÛdigo de salida (0 = Èxito).
    """
    questionary = _importar_questionary()
    if questionary is None:
        aviso(
            "El asistente requiere questionary. "
            "Inst·lalo con: pip install questionary"
            "  (o: pip install snapcontext[interactive])"
        )
        return 1

    if CONFIG_PATH.exists() and not questionary.confirm(
        "¬øYa existe una configuraciÛn. ¬øQuieres sobrescribirla?"
    ).ask():
        aviso("ConfiguraciÛn no modificada.")
        return 0

    exito("ConfiguraciÛn inicial de SnapContext")
    api_keys: dict = dict(cargar_configuracion().get("api_keys") or {})

    clave = questionary.password(
        "Clave de API de Gemini (GEMINI_API_KEY):",
        default=api_keys.get("gemini", ""),
    ).ask()
    if clave and clave.strip():
        api_keys["gemini"] = clave.strip()

    if questionary.confirm(
        "¬øQuieres configurar otros proveedores (Groq, DeepSeek)?"
    ).ask():
        for prov in ("groq", "deepseek"):
            env = PROVEEDORES[prov]["clave_env"]
            # CORRECCI√ìN 0.6.0: Usar questionary.password() en lugar de text(password=True)
            valor = questionary.password(
                f"Clave de API de {PROVEEDORES[prov]['nombre']} ({env}):",
                default=api_keys.get(prov, ""),
            ).ask()
            if valor and valor.strip():
                api_keys[prov] = valor.strip()
        aviso("Ollama es local y no necesita clave (opcional: OLLAMA_API_KEY).")

    aviso("Ahora elige tu proveedor y modelo favoritos (con las flechas).")
    proveedor, modelo = seleccionar_proveedor_interactivo()

    if not guardar_configuracion(proveedor, modelo, api_keys):
        error(f"No se pudo escribir la configuraciÛn en {CONFIG_PATH}")
        return 1
    exito(f"ConfiguraciÛn guardada en {CONFIG_PATH}")

    if questionary.confirm("¬øQuieres probar la conexiÛn con la API ahora?").ask():
        if _probar_conexion_proveedor(proveedor, modelo):
            exito("¬°ConexiÛn con la API verificada correctamente!")
        else:
            error("No se pudo conectar con la API. Revisa la clave.")
            return 1

    # ‚îÄ‚îÄ v3.1.0: Ollama, proyecto de prueba y tutorial ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
    if questionary.confirm(
        "¬øQuieres configurar Ollama (modo offline, sin API key)?"
    ).ask():
        estado_ol = _estado_ollama()
        if estado_ol["modelos"]:
            ligero = _elegir_modelo_ligero(estado_ol["modelos"])
            exito(f"Ollama ya est· listo (modelo m·s ligero: '{ligero}').")
            if questionary.confirm(
                "¬øUsar Ollama como proveedor por defecto?"
            ).ask():
                guardar_configuracion("ollama", ligero, api_keys)
                proveedor, modelo = "ollama", ligero
                exito(f"Proveedor guardado: ollama / {ligero}.")
        else:
            aviso("Ollama no est· instalado o no tiene modelos descargados.")
            info("Desc·rgalo desde https://ollama.com y despuÈs ejecuta:")
            info("  ollama pull llama3.2")
            try:
                import webbrowser
                if questionary.confirm(
                    "¬øAbrir https://ollama.com en el navegador?"
                ).ask():
                    webbrowser.open("https://ollama.com")
            except Exception:
                pass

    if questionary.confirm(
        "¬øQuieres crear un proyecto de prueba para empezar?"
    ).ask():
        try:
            destino = input(_pintar(
                "Carpeta del proyecto de prueba "
                "(Enter = ./snapcontext-prueba): ", _CYAN)).strip() or \
                "snapcontext-prueba"
        except EOFError:
            destino = ""
        if destino:
            ruta = Path(destino).expanduser().resolve()
            try:
                _crear_demo_proyecto(ruta)
                exito(f"Proyecto de prueba creado en: {ruta}")
                info("PruÈbalo con:")
                info(f'  cd "{ruta}" && snapcontext '
                     '"describe este proyecto" --vista-previa --local')
            except OSError as exc:
                error(f"No se pudo crear el proyecto: {exc}")

    if questionary.confirm(
        "¬øQuieres ejecutar el tutorial interactivo ahora (--bienvenida)?"
    ).ask():
        return _tutorial_interactivo()
    return 0


# ---------------------------------------------------------------------------
# Modo offline por defecto (v3.1.0): sin API key ‚Üí Ollama autom·ticamente
# ---------------------------------------------------------------------------
def hay_api_key_configurada() -> bool:
    """True si hay alguna clave de API en el entorno o en la configuraciÛn.

    Comprueba las variables GEMINI_API_KEY / ANTHROPIC_API_KEY /
    DEEPSEEK_API_KEY / GROQ_API_KEY / OPENAI_API_KEY y, adem·s, las claves
    guardadas en ~/.snapcontext/config.json (secciÛn 'api_keys').
    """
    for env in CLAVES_API_CONOCIDAS:
        if (os.environ.get(env) or "").strip():
            return True
    try:
        claves = cargar_configuracion().get("api_keys") or {}
    except Exception:
        claves = {}
    for valor in claves.values():
        if isinstance(valor, str) and valor.strip():
            return True
    return False


def _estado_ollama() -> dict:
    """Devuelve {'instalado': bool, 'modelos': [str], 'error': str|None}."""
    modelos, fallo = _listar_modelos_ollama()
    return {
        "instalado": bool(modelos) or (fallo is not None and "PATH" not in fallo),
        "modelos": modelos,
        "error": fallo,
    }


def _elegir_modelo_ligero(modelos: List[str]) -> Optional[str]:
    """Elige el modelo m·s ligero disponible seg˙n MODELOS_LIGEROS_OLLAMA.

    Devuelve None si la lista est· vacÌa.
    """
    if not modelos:
        return None
    for preferido in MODELOS_LIGEROS_OLLAMA:
        for m in modelos:
            if m == preferido or m.startswith(preferido + ":"):
                return m
    # Coincidencia parcial (p. ej. "llama3.2:latest").
    for preferido in MODELOS_LIGEROS_OLLAMA:
        for m in modelos:
            if preferido in m:
                return m
    return modelos[0]


def _proveedor_offline() -> Optional[dict]:
    """Intenta configurar Ollama como proveedor offline.

    Devuelve {'provider': 'ollama', 'model': <ligero>} si Ollama tiene
    modelos descargados; None en caso contrario (sin mostrar error fatal).
    """
    estado = _estado_ollama()
    modelo = _elegir_modelo_ligero(estado["modelos"])
    if not modelo:
        return None
    aviso("Sin API key: usando modo OFFLINE con Ollama ('" + modelo + "').")
    return {"provider": "ollama", "model": modelo}


def _determinar_proveedor(args: argparse.Namespace) -> dict:
    """Resuelve proveedor y modelo con persistencia en la configuraciÛn.

    Devuelve un dict con las claves 'provider' y 'model'. Prioridad:
      1) --provider por CLI (y se guarda, salvo --no-persist).
      2) ConfiguraciÛn guardada en ~/.snapcontext/config.json.
      3) Env SNAPCONTEXT_PROVIDER (si no hay configuraciÛn guardada).
      4) Primer uso ‚Üí men˙ interactivo y preguntar si se guarda.
    """
    persistir = not getattr(args, "no_persist", False)
    proveedor_cli = getattr(args, "provider", None)
    modelo_cli = getattr(args, "modelo", None) or MODELO_DEFECTO

    # 1) Proveedor explÌcito en CLI: m·xima prioridad; adem·s se recuerda.
    if proveedor_cli:
        if persistir:
            guardar_configuracion(proveedor_cli, modelo_cli)
        return {"provider": proveedor_cli, "model": modelo_cli}

    # 2) Preferencia guardada (primer uso ‚Üí todavÌa no existe el archivo).
    if persistir:
        config = cargar_configuracion()
        if config.get("provider"):
            modelo = modelo_cli or config.get("model") or None
            return {"provider": config["provider"], "model": modelo}

        # 3) Variable de entorno como preferencia global.
        proveedor_env = os.environ.get("SNAPCONTEXT_PROVIDER")
        if proveedor_env:
            guardar_configuracion(proveedor_env, modelo_cli)
            return {"provider": proveedor_env, "model": modelo_cli}

    # 4) Primer uso sin configuraciÛn. v3.1.0: si no hay ninguna API key,
    #    se intenta Ollama autom·ticamente antes del men˙ interactivo.
    if not hay_api_key_configurada():
        offline = _proveedor_offline()
        if offline:
            if persistir and _preguntar_guardar_config():
                guardar_configuracion(offline["provider"], offline["model"])
            return offline
        raise RuntimeError(MENSAJE_SIN_CLAVE_NI_OLLAMA)

    proveedor, modelo = seleccionar_proveedor_interactivo()
    if persistir and _preguntar_guardar_config():
        guardar_configuracion(proveedor, modelo)
    return {"provider": proveedor, "model": modelo}


def seleccionar_archivos(consulta: str, archivos: List[str],
                         proveedor: str = PROVEEDOR_DEFECTO,
                         modelo: Optional[str] = None,
                         max_archivos: int = MAX_ARCHIVOS_DEFECTO) -> List[str]:
    """Despachador por proveedor. `modelo=None` usa el valor por defecto del
    proveedor (o el de SNAPCONTEXT_MODELO si est· definida)."""
    if proveedor not in PROVEEDORES:
        raise RuntimeError(
            f"Proveedor desconocido '{proveedor}'. "
            f"V·lidos: {', '.join(sorted(PROVEEDORES))}"
        )
    cfg = PROVEEDORES[proveedor]
    modelo = modelo or cfg["modelo_default"]

    if cfg["tipo"] == "gemini":
        return seleccionar_archivos_con_gemini(
            consulta, archivos, max_archivos=max_archivos, modelo=modelo,
        )
    if cfg["tipo"] == "openai":
        return seleccionar_archivos_con_openai(
            consulta, archivos, proveedor=proveedor, modelo=modelo,
            max_archivos=max_archivos,
        )
    if cfg["tipo"] == "anthropic":
        return seleccionar_archivos_con_anthropic(
            consulta, archivos, max_archivos=max_archivos, modelo=modelo,
        )
    raise RuntimeError(f"Tipo de proveedor no implementado: {cfg['tipo']}")

# ---------------------------------------------------------------------------
# EjecuciÛn de Aider y bucle de pruebas
# ---------------------------------------------------------------------------
def ejecutar_aider(archivos: List[str], consulta: str, directorio: str,
                   opciones_aider: str = "") -> bool:
    """Ejecuta Aider en `directorio` con los `archivos` aÒadidos y la consulta
    como mensaje. `--yes` evita confirmaciones manuales (auto-commit de git).

    El resto de la configuraciÛn de Aider (modelo, API key, etc.) se toma de
    las variables de entorno AIDER_* / .env, igual que en un uso normal.
    """
    if shutil.which("aider") is None:
        raise RuntimeError(MENSAJE_AIDER_FALTANTE)

    cmd = ["aider", "--yes"]
    # Rutas normalizadas y validadas: solo se pasan archivos que existan como
    # rutas limpias DENTRO del repo (bloquea '..' / relativas peligrosas).
    raiz_res = Path(directorio).resolve()
    for archivo in archivos:
        limpia = _normalizar_relativa(str(archivo))
        if not limpia:
            continue
        if not _esta_dentro(raiz_res, limpia):
            aviso(f"Ignorando archivo fuera del repositorio: {archivo}")
            continue
        cmd.extend(["--file", limpia])
    if opciones_aider.strip():
        cmd.extend(shlex.split(opciones_aider))
    cmd.extend(["--message", consulta])

    depurar("Comando: " + " ".join(cmd))
    info("Ejecutando Aider...")
    resultado = subprocess.run(cmd, cwd=directorio)

    if resultado.returncode == 0:
        exito("Aider terminÛ correctamente.")
        return True
    aviso(f"Aider terminÛ con cÛdigo {resultado.returncode}.")
    return False


def _editor_sobrescribir(archivo: str, contenido: str,
                          directorio: str = ".") -> bool:
    """Editor propio (Fase 1 ‚Äî Sobrescritura de archivos).

    Escribe `contenido` en `archivo` dentro de `directorio`.
    - Valida que la ruta estÈ dentro del repositorio.
    - Crea copia de seguridad en ~/.snapcontext/backups/ antes de sobrescribir.
    - Crea carpetas intermedias si no existen.
    - Devuelve True si tuvo Èxito, False en caso de error.
    """
    if not archivo or not str(archivo).strip():
        error("La ruta del archivo no puede estar vacÌa.")
        return False

    raw = str(archivo).replace("\\", "/").strip()
    partes_raw = raw.split("/")
    if ".." in partes_raw or raw.startswith("/"):
        error(f"Acceso denegado: el archivo '{archivo}' contiene referencias a directorios padre o raÌz.")
        return False

    raiz_res = Path(directorio).resolve()
    limpia = _normalizar_relativa(raw)
    if not limpia:
        error(f"Ruta no v·lida: {archivo}")
        return False

    destino = (raiz_res / limpia).resolve()
    try:
        destino.relative_to(raiz_res)
    except ValueError:
        error(f"Acceso denegado: el archivo '{archivo}' est· fuera del repositorio.")
        return False

    # Si el archivo ya existe, guardar backup (OBLIGATORIO desde v4.6.0).
    # Sin backup NO se escribe: se aborta la ediciÛn por seguridad.
    if destino.exists() and destino.is_file():
        try:
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            nombre_backup = f"{ts}_{destino.name}"
            backup_path = BACKUPS_DIR / nombre_backup
            shutil.copy2(destino, backup_path)
            depurar(f"[EditorPropio] Backup guardado en {backup_path}")
        except Exception as exc:
            error(f"[EditorPropio] Backup de {limpia} fallÛ ({exc}); "
                  "ediciÛn cancelada por seguridad.")
            return False

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8")
        exito(f"[EditorPropio] Archivo actualizado: {limpia}")
        return True
    except Exception as exc:
        error(f"[EditorPropio] Error al escribir {limpia}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Contexto inteligente para archivos grandes (v4.7.0)
# ---------------------------------------------------------------------------
def _extraer_bloques_ast(contenido: str,
                         archivo: Optional[str] = None) -> List[dict]:
    """Extrae los bloques de primer nivel (funciones/clases) de ``contenido``.

    - Python (o sin ``archivo``): usa el ``ast`` de la stdlib.
    - Otros lenguajes (con ``archivo``): usa tree-sitter vÌa
      ``parser_universal.extraer_bloques`` (v5.6.0).

    Devuelve una lista de dicts ``{"tipo", "nombre", "inicio", "fin"}`` con
    lÌneas 1-based inclusivas. Devuelve [] para lenguajes no soportados o
    sintaxis inv·lida.
    """
    if archivo and not _es_extension_python(archivo):
        try:
            import parser_universal as pu          # noqa: E402
            return pu.extraer_bloques(archivo, contenido)
        except Exception:                          # noqa: BLE001
            return []
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return []
    total = len(contenido.splitlines())
    bloques: List[dict] = []
    for nodo in arbol.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            inicio = nodo.lineno
            if getattr(nodo, "decorator_list", None):
                inicio = min(getattr(d, "lineno", inicio)
                             for d in nodo.decorator_list)
            fin = getattr(nodo, "end_lineno", nodo.lineno) or nodo.lineno
            bloques.append({"tipo": type(nodo).__name__,
                            "nombre": nodo.name,
                            "inicio": max(inicio, 1),
                            "fin": min(fin, total)})
    return bloques


def _extraer_contexto_selectivo(contenido: str, mensaje: str = "",
                                archivo: Optional[str] = None) -> str:
    """Construye contexto reducido para archivos grandes (> MAX_CONTEXT_LINES).

    Formato devuelto: ``[RESUMEN DEL ARCHIVO (AST)]`` (Python vÌa
    ``_resumen_ast_python``; otros lenguajes vÌa tree-sitter /
    ``parser_universal``, v5.6.0) + ``[C√ìDIGO RELEVANTE A EDITAR]``
    (bloques cuyo nombre aparece en ``mensaje``; si ninguno coincide, los
    primeros bloques hasta agotar el presupuesto ‚Äî b˙squeda por proximidad),
    cada uno con ¬±5 lÌneas de contexto adicional, m·s la ``[RESTRICCI√ìN]``
    final para que el modelo solo genere el diff del bloque mostrado.
    """
    es_python = (archivo is None) or _es_extension_python(archivo)
    if es_python:
        resumen = _resumen_ast_python(contenido)
    else:
        try:
            import parser_universal as pu          # noqa: E402
            resumen = pu.resumen_archivo(archivo, contenido)
        except Exception:                          # noqa: BLE001
            resumen = None
        if not resumen or not resumen.get("ok"):
            resumen = _resumen_ast(contenido, archivo or "")
    lineas = contenido.splitlines()
    total = len(lineas)

    partes = ["[RESUMEN DEL ARCHIVO (AST)]:",
              f"(lenguaje: {resumen.get('lenguaje') or '?'}, "
              f"motor: {resumen.get('motor') or '?'}, {total} lÌneas)"]
    for clave, titulo in (("imports", "Imports"), ("clases", "Clases"),
                          ("funciones", "Funciones")):
        items = resumen.get(clave) or []
        if items:
            nombres = ", ".join(
                (it.get("nombre") if isinstance(it, dict) else str(it))
                for it in items[:80])
            partes.append(f"{titulo}: {nombres}")

    bloques = _extraer_bloques_ast(contenido, archivo)
    tarea = (mensaje or "").lower()
    objetivo = [b for b in bloques if b["nombre"].lower() in tarea]
    if not objetivo:
        # Proximidad: nadie fue mencionado; se envÌan los primeros bloques
        # hasta agotar el presupuesto de lÌneas (reservando margen).
        presupuesto = max(MAX_CONTEXT_LINES - 80, 40)
        usadas = 0
        for b in bloques:
            tam = (b["fin"] - b["inicio"]) + 11       # + contexto ¬±5
            if objetivo and usadas + tam > presupuesto:
                break
            objetivo.append(b)
            usadas += tam

    partes.append("")
    partes.append("[C√ìDIGO RELEVANTE A EDITAR]:")
    rango_mostrado: List[tuple] = []
    for b in objetivo:
        ini = max(b["inicio"] - 5, 1)
        fin = min(b["fin"] + 5, total)
        if any(ini <= f and fin >= i for i, f in rango_mostrado):
            continue                                  # ya cubierto por otro
        rango_mostrado.append((ini, fin))
        partes.append(f"# ‚îÄ‚îÄ {b['tipo']} {b['nombre']} "
                      f"(lÌneas {ini}-{fin} de {total}) ‚îÄ‚îÄ")
        partes.extend(lineas[ini - 1:fin])
    if not objetivo:
        # √öltimo recurso (p. ej. script sin funciones/clases): cabecera.
        partes.append("# (sin funciones/clases detectadas; cabecera del archivo)")
        partes.extend(lineas[:min(MAX_CONTEXT_LINES, total)])

    partes.append("")
    partes.append("[RESTRICCI√ìN]: El resto del archivo no se muestra por "
                  "lÌmites de contexto. Genera el parche/diff solo para el "
                  "bloque mostrado. NO reescribas el archivo completo.")
    return "\n".join(partes)


def _splicear_bloque(contenido: str, bloque_viejo: str,
                     bloque_nuevo: str) -> Optional[str]:
    """Reemplaza ``bloque_viejo`` dentro de ``contenido`` por ``bloque_nuevo``.

    Localiza el bloque aunque haya pequeÒas diferencias (difflib sobre lÌneas
    sin espacios marginales). Devuelve el contenido resultante o ``None`` si
    no hay un emplazamiento con confianza suficiente (ratio medio ‚â• 0.80).
    """
    actuales = contenido.splitlines()
    viejas = (bloque_viejo or "").strip("\n").splitlines() or [""]
    nuevas = (bloque_nuevo or "").rstrip("\n").splitlines()
    n = len(viejas)
    if n == 0 or len(actuales) < n:
        return None
    stripped = [l.strip() for l in actuales]
    viejas_st = [l.strip() for l in viejas]
    mejor_pos, mejor_ratio = -1, 0.0
    for i in range(len(actuales) - n + 1):
        suma = sum(difflib.SequenceMatcher(
            None, viejas_st[j], stripped[i + j]).ratio() for j in range(n))
        promedio = suma / n
        if promedio > mejor_ratio:
            mejor_pos, mejor_ratio = i, promedio
    if mejor_pos < 0 or mejor_ratio < 0.80:
        return None
    resultado = actuales[:mejor_pos] + nuevas + actuales[mejor_pos + n:]
    sufijo = "\n" if (contenido.endswith("\n") or not resultado) else ""
    return "\n".join(resultado) + sufijo


def _comandos_validacion(lenguaje: str, archivo_tmp: str) -> List[List[str]]:
    """Comandos de validaciÛn sint·ctica para ``lenguaje``.

    Devuelve una lista de candidatos (cada uno un argv con ``archivo_tmp`` ya
    resuelto). Cuando un lenguaje admite un validador de reserva (p. ej.
    ``dart analyze`` ‚Üí ``dart format``), se incluyen en orden de preferencia.
    Devuelve ``[]`` si no existe validador para el lenguaje.
    """
    if not lenguaje:
        return []
    if lenguaje in ("python", "py", "python3"):
        return [[sys.executable, "-m", "py_compile", archivo_tmp]]
    if lenguaje in ("javascript", "typescript", "tsx", "js", "ts", "node",
                    "jsx", "mjs", "cjs", "mts", "cts"):
        return [["node", "--check", archivo_tmp]]
    if lenguaje == "dart":
        return [["dart", "analyze", archivo_tmp],
                ["dart", "format", "--output=none", archivo_tmp]]
    if lenguaje == "go":
        return [["go", "build", "-n", archivo_tmp],
                ["gofmt", "-e", archivo_tmp]]
    if lenguaje in ("rust", "rs"):
        return [["rustc", "--parse-only", archivo_tmp]]
    if lenguaje in ("java",):
        return [["javac", "-Xlint:none", archivo_tmp]]
    if lenguaje in ("c", "h"):
        return [["gcc", "-fsyntax-only", archivo_tmp],
                ["clang", "-fsyntax-only", archivo_tmp]]
    if lenguaje in ("cpp", "cc", "cxx", "hpp", "hh", "hxx", "c++"):
        return [["g++", "-fsyntax-only", archivo_tmp],
                ["clang++", "-fsyntax-only", archivo_tmp]]
    return []


def _validar_sintaxis(archivo: str, contenido: str,
                      directorio: str = ".") -> Tuple[bool, str]:
    """Valida la sintaxis de ``contenido`` como si fuese el de ``archivo``.

    Escribe el ``contenido`` en un archivo temporal (siempre conserva la
    extensiÛn del archivo original para que el parser/linter lo reconozca) y
    ejecuta el comando de validaciÛn correspondiente al lenguaje detectado con
    ``_lenguaje_archivo``. Nunca toca el archivo original.

    Devuelve ``(exito, mensaje_error)``:
      - ``(True, "")`` si la validaciÛn pasÛ, o si no hay validador / comando
        disponible (se omite la validaciÛn).
      - ``(False, mensaje)`` si el validador rechazÛ el contenido o hubo timeout.
    """
    lenguaje = _lenguaje_archivo(archivo, contenido) or ""
    if not lenguaje:
        depurar(
            f"[validar-sintaxis] Sin validador para '{archivo}' (lenguaje "
            f"desconocido); se omite la validaciÛn."
        )
        return True, ""

    suffix = Path(archivo).suffix or ".txt"
    archivo_tmp = ""
    try:
        fd, archivo_tmp = tempfile.mkstemp(
            suffix=suffix, prefix=".snapcontext_validacion_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(contenido or "")

        comandos = _comandos_validacion(lenguaje, archivo_tmp)
        if not comandos:
            depurar(
                f"[validar-sintaxis] Sin validador para '{lenguaje}'; "
                "se omite la validaciÛn."
            )
            return True, ""

        for cmd in comandos:
            binario = cmd[0]
            if shutil.which(binario) is None:
                depurar(
                    f"[validar-sintaxis] Comando '{binario}' no disponible; "
                    "se busca alternativa."
                )
                continue
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60,
                    cwd=directorio or ".",
                )
            except subprocess.TimeoutExpired:
                error(
                    f"[validar-sintaxis] El comando '{binario}' excediÛ el "
                    f"tiempo lÌmite al validar '{archivo}'."
                )
                return False, (
                    f"el comando '{binario}' excediÛ el tiempo lÌmite de validaciÛn"
                )
            except (OSError, ValueError):
                continue          # no se pudo lanzar ‚Üí probar candidato siguiente

            if proc.returncode == 0:
                return True, ""
            # CÛdigo de salida != 0 ‚áí error sint·ctico (o validador fallo).
            salida = (proc.stderr or "").strip()
            if not salida:
                salida = (proc.stdout or "").strip()
            mensaje = salida or (
                f"el validador '{binario}' fallÛ (cÛdigo {proc.returncode})"
            )
            return False, mensaje

        # Ning˙n candidato disponible ‚Üí se omite la validaciÛn.
        depurar(
            f"[validar-sintaxis] Ning˙n validador disponible para '{lenguaje}'; "
            "se omite la validaciÛn."
        )
        return True, ""
    finally:
        if archivo_tmp:
            try:
                os.unlink(archivo_tmp)
            except OSError:
                pass


def _generar_parche(original: str, nuevo: str, ruta_archivo: str) -> str:
    """Genera un parche unificado (unified diff) entre `original` y `nuevo`.

    El encabezado cumple con el est·ndar de `patch` y `git apply` (a/ruta b/ruta).
    """
    ruta_posix = str(ruta_archivo).replace("\\", "/").strip()
    if ruta_posix.startswith("./"):
        ruta_posix = ruta_posix[2:]

    lineas_orig = original.splitlines(keepends=True)
    lineas_nuevo = nuevo.splitlines(keepends=True)

    diff = difflib.unified_diff(
        lineas_orig,
        lineas_nuevo,
        fromfile=f"a/{ruta_posix}",
        tofile=f"b/{ruta_posix}",
    )
    return "".join(diff)


def _aplicar_parche(parche: str, directorio: str = ".") -> bool:
    """Aplica un parche unificado en `directorio` usando `git apply` o `patch`.

    1. Escribe el parche en un archivo temporal.
    2. Intenta aplicar con `git apply --whitespace=nowarn <temp_file>`.
    3. Si `git` falla o no est· disponible, intenta con `patch -p1 -i <temp_file>`.
    4. Devuelve True si se aplicÛ limpiamente, False si hubo error o conflicto.
    """
    if not parche or not parche.strip():
        aviso("[EditorPropio] Parche vacÌo; no se aplicaron cambios.")
        return False

    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", encoding="utf-8", delete=False) as f:
        f.write(parche)
        temp_path = f.name

    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        raiz_res = str(Path(directorio).resolve())

        # Intentar primero con git apply (muy est·ndar en repos de desarrollo)
        if shutil.which("git"):
            cmd_git = ["git", "apply", "--whitespace=nowarn", temp_path]
            res_git = subprocess.run(
                cmd_git, cwd=raiz_res, capture_output=True, text=True, creationflags=flags
            )
            if res_git.returncode == 0:
                exito("[EditorPropio] Parche unificado aplicado correctamente con git apply.")
                return True
            depurar(f"[EditorPropio] git apply fallÛ (cÛdigo {res_git.returncode}): {res_git.stderr}")

        # Fallback a patch
        if shutil.which("patch"):
            cmd_patch = ["patch", "-p1", "-i", temp_path]
            res_patch = subprocess.run(
                cmd_patch, cwd=raiz_res, capture_output=True, text=True, creationflags=flags
            )
            if res_patch.returncode == 0:
                exito("[EditorPropio] Parche unificado aplicado correctamente con patch.")
                return True
            depurar(f"[EditorPropio] patch fallÛ (cÛdigo {res_patch.returncode}): {res_patch.stderr}")

        aviso("[EditorPropio] No se pudo aplicar el parche autom·ticamente (ni git apply ni patch tuvieron Èxito).")
        return False
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Manejo de conflictos y aplicaciÛn incremental (v3.3.0)
# ---------------------------------------------------------------------------

# v6.3.0 ‚Äî Umbrales de similitud del emparejamiento difuso de parches:
#   - UMBRAL_DIFUSO_HUNKS: ratio MEDIO mÌnimo de las lÌneas de contexto de un
#     hunk para aceptar una posiciÛn candidata (b˙squeda global).
#   - UMBRAL_DIFUSO_LINEA: ratio mÌnimo por lÌnea individual de contexto.
#   - UMBRAL_DIFUSO_BLOQUE: ratio mÌnimo a nivel de bloque para la
#     resincronizaciÛn (ventana m·s parecida en todo el archivo).
UMBRAL_DIFUSO_HUNKS = 0.85
UMBRAL_DIFUSO_LINEA = 0.90
UMBRAL_DIFUSO_BLOQUE = 0.80
# v6.9.0: lÌmite de lÌneas de contexto usadas en el emparejamiento difuso
# (en lugar de recorrer TODO el archivo), para algoritmos O(n¬≤) ‚Üí O(n¬∑20) en
# archivos grandes. Se conserva el bloque completo para la aplicaciÛn final.
MAX_CONTEXTO_DIFUSO_LINEAS = 20


def _ruta_del_parche(parche: str) -> Optional[str]:
    """Extrae la ruta del archivo objetivo del encabezado del parche.

    Acepta encabezados ``--- a/ruta`` / ``+++ b/ruta`` y variantes sin
    prefijo. Devuelve None si no se encuentra.
    """
    for linea in (parche or "").splitlines():
        if linea.startswith("+++ "):
            ruta = linea[4:].strip().split("\t")[0]
            if ruta.startswith("b/"):
                ruta = ruta[2:]
            return ruta or None
        if linea.startswith("--- "):
            candidata = linea[4:].strip().split("\t")[0]
            if candidata.startswith("a/"):
                candidata = candidata[2:]
            if candidata and candidata not in ("/dev/null",):
                return candidata
    return None


def _validar_parche_previo(parche: str, directorio: str,
                           contenido_esperado: Optional[str]) -> tuple:
    """Verifica que el archivo coincide con lo usado para generar el parche.

    Evita conflictos por cambios concurrentes: si el contenido actual del
    archivo difiere del que se pasÛ al proveedor, aplicar a ciegas corromperÌa
    la ediciÛn. Devuelve ``(ok, detalle)``.
    """
    if contenido_esperado is None:
        return True, "sin validaciÛn (no hay contenido de referencia)"
    ruta = _ruta_del_parche(parche)
    if not ruta:
        return True, "parche sin encabezado reconocible; se omite la validaciÛn"
    destino = Path(directorio or ".").resolve() / ruta
    if not destino.is_file():
        return False, f"el archivo '{ruta}' ya no existe"
    try:
        actual = destino.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"no se pudo leer '{ruta}': {exc}"
    if actual != contenido_esperado:
        return False, (f"'{ruta}' cambiÛ desde que se generÛ el parche "
                       "(posible cambio concurrente)")
    return True, "el archivo coincide con la referencia"


def _parsear_hunks(parche: str) -> List[tuple]:
    """Divide un diff unificado en hunks ``(linea_inicio_original, cambios)``.

    ``cambios`` es una lista de ``(marca, texto)`` con marca ' ', '-' o '+'.
    Se omiten los hunks sin lÌneas modificadas. Devuelve [] si no hay ninguno.
    """
    hunks: List[tuple] = []
    hunk_actual: Optional[List[tuple]] = None
    inicio_orig = 0
    for linea in (parche or "").splitlines(keepends=True):
        texto = linea.rstrip("\r\n")
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", texto)
        if m:
            if hunk_actual:
                hunks.append((inicio_orig, hunk_actual))
            inicio_orig = int(m.group(1))
            hunk_actual = []
            continue
        if hunk_actual is None:
            continue                      # encabezados ---/+++/ruido
        if texto.startswith("+"):
            hunk_actual.append(("+", texto[1:]))
        elif texto.startswith("-"):
            hunk_actual.append(("-", texto[1:]))
        else:
            hunk_actual.append((" ", texto[1:] if texto else ""))
    if hunk_actual:
        hunks.append((inicio_orig, hunk_actual))
    return [(i, l) for i, l in hunks if any(marca != " " for marca, _ in l)]


def _quitar_comentario(linea: str) -> str:
    """Elimina de forma conservadora un comentario final ``#`` o ``//``.

    Solo se recorta si el marcador est· al inicio de la lÌnea o va precedido
    de un espacio (asÌ no se rompen URLs tipo ``https://‚Ä¶`` ni cadenas que
    contengan ``#``). Devuelve la lÌnea sin el comentario y sin espacios
    finales.
    """
    idx = linea.find("#")
    if idx == 0 or (idx > 0 and linea[idx - 1].isspace()):
        return linea[:idx].rstrip()
    idx = linea.find("//")
    while idx != -1:
        if idx == 0 or linea[idx - 1].isspace():
            return linea[:idx].rstrip()
        idx = linea.find("//", idx + 1)
    return linea


def _variantes_linea(linea: str) -> Tuple[str, str, str]:
    """Variantes progresivamente m·s laxas de una lÌnea (v6.3.0).

    1. La lÌnea tal cual (sin salto final).
    2. Con los espacios colapsados (tolera indentaciÛn/espacios extra).
    3. Adem·s, sin comentario final ``#``/``//`` (tolera comentarios
       aÒadidos o eliminados por el usuario o el formateador).

    Se usan en el emparejamiento por variantes del editor de parches; la
    variante 1 reproduce la comparaciÛn exacta histÛrica.
    """
    cruda = linea.rstrip("\r\n")
    normalizada = " ".join(cruda.split())
    return cruda, normalizada, _quitar_comentario(normalizada)


def _lineas_equivalentes(a: str, b: str) -> bool:
    """True si dos lÌneas coinciden en alguna de sus variantes (v6.3.0)."""
    va, vb = _variantes_linea(a), _variantes_linea(b)
    return va[0] == vb[0] or va[1] == vb[1] or va[2] == vb[2]


def _ratio_bloque(a: str, b: str) -> float:
    """Ratio de similitud de dos bloques de texto (v6.3.0).

    ``SequenceMatcher.real_quick_ratio`` y ``quick_ratio`` son cotas
    superiores del ratio final: se usan para descartar ventanas imposibles
    sin pagar el coste completo. Devuelve 0.0 si no supera
    ``UMBRAL_DIFUSO_BLOQUE``.
    """
    sm = difflib.SequenceMatcher(None, a, b)
    if sm.real_quick_ratio() < UMBRAL_DIFUSO_BLOQUE \
            or sm.quick_ratio() < UMBRAL_DIFUSO_BLOQUE:
        return 0.0
    return sm.ratio()


def _contar_cambios_parche(parche: str) -> Tuple[int, int]:
    """Cuenta ``(aÒadidas, eliminadas)`` en un diff unificado (v6.3.0)."""
    anadidas = eliminadas = 0
    en_hunk = False
    for linea in (parche or "").splitlines():
        if linea.startswith("@@"):
            en_hunk = True
            continue
        if not en_hunk:
            continue
        if linea.startswith("+"):
            anadidas += 1
        elif linea.startswith("-"):
            eliminadas += 1
    return anadidas, eliminadas


def _mostrar_diff_parche(parche: str, ruta: Optional[str] = None) -> None:
    """Muestra el diff propuesto coloreado (rich.syntax, 'diff') ‚Äî v6.3.0.

    Importa ``ui`` de forma tardÌa (patrÛn de ``_procesar_razonamiento``)
    para que tests y consumidores puedan sustituir ``ui.mostrar_diff``.
    Nunca lanza: un fallo de UI no debe impedir aplicar el parche.
    """
    try:
        import ui as _ui
        anadidas, eliminadas = _contar_cambios_parche(parche)
        # v6.12.0: envÌa el diff tambiÈn a la TUI (pestaÒa Diffs) si est·
        # activa; nunca lanza ni bloquea.
        _tui_log("info", f"?? Diff generado: {ruta or '(parche)'} "
                         f"(+{anadidas}/-{eliminadas})")
        try:
            global _TUI_HUB
            if _TUI_HUB is False:
                try:
                    import tui_hub as _hub_d
                    _TUI_HUB = _hub_d
                except Exception:                # noqa: BLE001
                    _TUI_HUB = None
            if _TUI_HUB and getattr(_TUI_HUB, "esta_activo", lambda: False)():
                _TUI_HUB.enviar_diff(ruta or "(parche)", parche)
        except Exception:                        # noqa: BLE001 ‚Äî blindaje TUI
            pass
        _ui.mostrar_diff(ruta or "(parche)", anadidas, eliminadas, parche)
    except Exception as exc:                   # noqa: BLE001 - blindaje UI
        depurar(f"[EditorPropio] No se pudo mostrar el diff: {exc}")


def _aplicar_hunks_incremental(parche: str, directorio: str,
                               mostrar_diff: bool = False) -> bool:
    """ResoluciÛn autom·tica de conflictos: aplica el parche lÌnea a lÌnea.

    Estrategia puramente Python (sin git/patch): para cada hunk busca el
    bloque original con tolerancia a desfases y aplica solo las lÌneas
    modificadas, siempre con copia de seguridad previa.

    v6.3.0 ‚Äî emparejamiento difuso por etapas (solo se prueba la etapa
    siguiente cuando la anterior no encuentra sitio, de modo que el caso
    exacto no paga el coste de ``SequenceMatcher``):
      1. Coincidencia exacta cerca de la posiciÛn declarada.
      2. Coincidencia por variantes (espacios colapsados, sin comentarios)
         cerca de la posiciÛn declarada.
      3. B˙squeda difusa global: mayor ratio medio de las lÌneas de contexto
         con ``difflib.SequenceMatcher`` (umbral ``UMBRAL_DIFUSO_HUNKS``).
      4. ResincronizaciÛn a nivel de bloque: si nada encaja, se busca en TODO
         el archivo la ventana m·s parecida al bloque original del hunk
         (umbral ``UMBRAL_DIFUSO_BLOQUE``) y se reemplaza conservando las
         lÌneas de contexto locales del usuario.

    Los hunks irresolubles abortan la operaciÛn (todo-o-nada desde v4.6.0)
    dejando el archivo intacto. Con ``mostrar_diff`` se muestra el diff
    propuesto antes de declarar el fallo.

    Devuelve True si se aplicÛ alg˙n cambio.
    """
    ruta = _ruta_del_parche(parche)
    if not ruta:
        aviso("[EditorPropio] No se pudo deducir el archivo del parche.")
        return False
    destino = Path(directorio or ".").resolve() / ruta
    if not destino.is_file():
        aviso(f"[EditorPropio] El archivo del parche no existe: {ruta}")
        return False
    try:
        original = destino.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        error(f"[EditorPropio] No se pudo leer '{ruta}': {exc}")
        return False

    resultado = original.splitlines()
    hunks = _parsear_hunks(parche)
    if not hunks:
        aviso("[EditorPropio] El parche no contiene hunks aplicables.")
        return False

    def _coincide(desde: int, bloque: List[tuple]) -> bool:
        """True si ``resultado[desde:]`` encaja con las lÌneas no-'+'."""
        idx = desde
        for marca, texto in bloque:
            if marca == "+":
                continue
            if idx >= len(resultado) or resultado[idx] != texto:
                return False
            idx += 1
        return True

    def _n_borrar(bloque: List[tuple]) -> int:
        return sum(1 for marca, _ in bloque if marca != "+")

    def _coincide_variantes(desde: int, bloque: List[tuple]) -> bool:
        """True si las lÌneas no-'+' encajan tolerando espacios/comentarios.

        v6.3.0: compara las variantes laxas (espacios colapsados y sin
        comentario final) lÌnea a lÌnea; TODAS las lÌneas no-'+' deben
        encajar en el mismo punto para evitar emparejamientos caprichosos.
        """
        idx = desde
        for marca, texto in bloque:
            if marca == "+":
                continue
            if idx >= len(resultado):
                return False
            if not _lineas_equivalentes(texto, resultado[idx]):
                return False
            idx += 1
        return True

    def _coincide_difusa(desde: int, bloque: List[tuple]) -> bool:
        """True si ``resultado[desde:]`` encaja de forma difusa con las lÌneas
        no-'+' del bloque (``difflib.SequenceMatcher``, ratio ‚â•
        ``UMBRAL_DIFUSO_LINEA`` por lÌnea de contexto). Tolera comentarios
        aÒadidos y espacios."""
        idx = desde
        for marca, texto in bloque:
            if marca == "+":
                continue
            if idx >= len(resultado):
                return False
            if resultado[idx] != texto:
                sm = difflib.SequenceMatcher(None, texto.strip(),
                                             resultado[idx].strip())
                if sm.ratio() < UMBRAL_DIFUSO_LINEA:
                    return False
            idx += 1
        return True

    desplazamiento = 0                     # acumulado por hunks previos
    hubo_resincronizacion = False          # v6.3.0: alg˙n hunk se recolocÛ
    lineas_norm: Optional[List[str]] = None  # perezoso (solo si hace falta)
    for inicio_orig, cambios in hunks:
        base = max(inicio_orig - 1 + desplazamiento, 0)
        n_borrados = _n_borrar(cambios)

        posicion = -1
        # 1) Coincidencia exacta cerca de la posiciÛn declarada.
        offsets = sorted({0, 1, -1, 2, -2, 3, -3, 5, -5, 10, -10, 20, -20})
        for delta in offsets:
            candidato = base + delta
            if 0 <= candidato <= len(resultado) and \
                    _coincide(candidato, cambios):
                posicion = candidato
                break
        # 2) v6.3.0: coincidencia por variantes cerca de la posiciÛn
        #    declarada (espacios colapsados, sin comentarios finales).
        if posicion < 0:
            for delta in offsets:
                candidato = base + delta
                if 0 <= candidato <= len(resultado) and \
                        _coincide_variantes(candidato, cambios):
                    posicion = candidato
                    break
        # 3) B˙squeda difusa real con difflib.SequenceMatcher (v4.6.0).
        #    Se busca el candidato cuyo conjunto de lÌneas de contexto tiene
        #    mayor ratio de similitud medio; se acepta solo por encima de
        #    UMBRAL_DIFUSO_HUNKS (0.85), tolerando comentarios/espacios
        #    cambiados.
        if posicion < 0:
            # v6.9.0: se limitan las lÌneas de contexto a 20 para reducir el
            # coste del barrido completo (O(n) candidatos √ó ctx lÌneas).
            contexto_idx = [i for i, (m, _) in enumerate(cambios)
                            if m == " " and _.strip()][:MAX_CONTEXTO_DIFUSO_LINEAS]
            if contexto_idx:
                lineas_stripped = [l.strip() for l in resultado]
                mejor_ratio, mejor_cand = 0.0, -1
                limite = max(1, len(resultado) - n_borrados + 1)
                textos_ctx = [(i, cambios[i][1].strip())
                              for i in contexto_idx]
                for candidato in range(limite):
                    ratio_total, n_ctx = 0.0, 0
                    for i_ctx, texto_ctx in textos_ctx:
                        idx = candidato + i_ctx
                        if idx >= len(lineas_stripped):
                            continue
                        sm = difflib.SequenceMatcher(
                            None, texto_ctx, lineas_stripped[idx])
                        ratio_total += sm.ratio()
                        n_ctx += 1
                    if n_ctx:
                        promedio = ratio_total / n_ctx
                        if promedio > mejor_ratio:
                            mejor_ratio, mejor_cand = promedio, candidato
                if mejor_cand >= 0 and mejor_ratio >= UMBRAL_DIFUSO_HUNKS \
                        and _coincide_difusa(mejor_cand, cambios):
                    posicion = mejor_cand
        # 4) v6.3.0 ‚Äî ResincronizaciÛn a nivel de bloque: si el hunk no encaja
        #    ni de forma exacta ni difusa, se busca en TODO el archivo la
        #    ventana del mismo tamaÒo m·s parecida al bloque original
        #    (contexto + lÌneas eliminadas) y se reemplaza. Como las lÌneas
        #    de contexto se conservan del archivo, el reemplazo respeta los
        #    cambios locales del usuario.
        resincronizado = False
        if posicion < 0:
            bloque_original = [texto for marca, texto in cambios
                               if marca in (" ", "-")]
            n_bloque = len(bloque_original)
            if n_bloque:
                texto_bloque = "\n".join(bloque_original)
                texto_bloque_norm = "\n".join(
                    " ".join(l.split()) for l in bloque_original)
                if lineas_norm is None:
                    lineas_norm = [" ".join(l.split()) for l in resultado]
                mejor_ratio, mejor_cand = 0.0, -1
                limite = max(0, len(resultado) - n_bloque + 1)
                # v6.9.0: fast path con difflib.get_close_matches para buscar
                # la ventana m·s parecida al bloque (evita el barrido manual
                # en el caso habitual; su coste es comparable pero centralizado
                # en una ˙nica llamada de la librerÌa est·ndar).
                ventanas_norm = [
                    "\n".join(lineas_norm[c:c + n_bloque])
                    for c in range(limite)]
                if ventanas_norm:
                    coincidencias = difflib.get_close_matches(
                        texto_bloque_norm, ventanas_norm, n=1,
                        cutoff=UMBRAL_DIFUSO_BLOQUE)
                    if coincidencias:
                        mejor_cand = ventanas_norm.index(coincidencias[0])
                        # get_close_matches garantiza ratio >= UMBRAL_DIFUSO_BLOQUE.
                        mejor_ratio = UMBRAL_DIFUSO_BLOQUE
                if mejor_cand < 0:
                    # Respaldo: barrido manual (comportamiento histÛrico).
                    for candidato in range(limite):
                        ratio = _ratio_bloque(
                            texto_bloque,
                            "\n".join(resultado[candidato:candidato + n_bloque]))
                        if ratio < UMBRAL_DIFUSO_BLOQUE:
                            ratio = max(ratio, _ratio_bloque(
                                texto_bloque_norm,
                                "\n".join(
                                    lineas_norm[candidato:candidato + n_bloque])))
                        if ratio > mejor_ratio:
                            mejor_ratio, mejor_cand = ratio, candidato
                if mejor_cand >= 0 and mejor_ratio >= UMBRAL_DIFUSO_BLOQUE:
                    posicion = mejor_cand
                    resincronizado = True
                    hubo_resincronizacion = True
        if posicion < 0 or not (
                resincronizado
                or _coincide(posicion, cambios)
                or _coincide_variantes(posicion, cambios)
                or _coincide_difusa(posicion, cambios)):
            # v4.6.0: antes se omitÌa el hunk y se escribÌa una aplicaciÛn
            # PARCIAL (estado mixto potencialmente inv·lido). Se aborta toda
            # la operaciÛn dejando el archivo intacto.
            # v6.3.0: mensaje claro con el proceso seguido, el umbral usado y
            # una sugerencia accionable; con --mostrar-diff se muestra antes
            # el diff propuesto para revisarlo o editarlo a mano.
            if mostrar_diff:
                _mostrar_diff_parche(parche, ruta)
            error("El parche no pudo aplicarse limpiamente.\n"
                  f"  Buscando coincidencia difusa... (umbral "
                  f"{UMBRAL_DIFUSO_HUNKS})\n"
                  "  No se encontrÛ una coincidencia suficiente.\n"
                  "  Sugerencia: Prueba con '--editor aider' o edita "
                  "manualmente.")
            return False

        # v4.6.0: las lÌneas de contexto (' ') se conservan tal cual est·n en
        # el archivo ‚Äî no se sobrescriben con el texto del parche ‚Äî para no
        # revertir cambios locales del usuario al aplicar de forma difusa.
        nuevo_bloque = []
        idx = posicion
        for marca, texto in cambios:
            if marca == "+":
                nuevo_bloque.append(texto)
            elif marca == " ":
                nuevo_bloque.append(resultado[idx])
                idx += 1
            else:                       # '-'
                idx += 1
        resultado[posicion:posicion + n_borrados] = nuevo_bloque
        desplazamiento += len(nuevo_bloque) - n_borrados

    if hubo_resincronizacion:
        exito(f"[EditorPropio] Parche aplicado con resoluciÛn incremental "
              f"(lÌnea a lÌnea, resincronizando bloques) sobre '{ruta}'.")
    else:
        exito(f"[EditorPropio] Parche aplicado con resoluciÛn incremental "
              f"(lÌnea a lÌnea) sobre '{ruta}'.")
    return _editor_sobrescribir(ruta, "\n".join(resultado) + "\n",
                                directorio=directorio)


def _aplicar_parche_con_resolucion(parche: str, directorio: str = ".",
                                   contenido_esperado: Optional[str] = None,
                                   mostrar_diff: bool = False,
                                   preguntar: Optional[Callable] = None) -> bool:
    """Aplica un parche con validaciÛn previa y resoluciÛn de conflictos.

    Flujo (v3.3.0):
      1. ValidaciÛn previa: si se pasa ``contenido_esperado`` (el contenido
         usado para generar el parche), comprueba que el archivo actual
         coincida para evitar conflictos concurrentes.
      2. Intento est·ndar: ``git apply`` ‚Üí ``patch -p1``.
      3. ResoluciÛn autom·tica: aplicaciÛn incremental lÌnea a lÌnea.
      4. Si todo falla, avisa para resoluciÛn manual (ya no sobrescribe a
         ciegas).

    v6.3.0: con ``mostrar_diff`` (flag ``--mostrar-diff``) muestra el diff
    propuesto y pregunta [a]plicar / [c]ancelar / [e]ditar manualmente ANTES
    de tocar nada. En modo ``--auto`` no se bloquea: se muestra el diff y se
    aplica. Sin el flag, comportamiento histÛrico (aplicar sin preguntar).
    ``preguntar`` permite inyectar la funciÛn de pregunta en tests; si es
    ``None`` se usa ``ui.preguntar_interactivo``.
    """
    ruta = _ruta_del_parche(parche)
    if mostrar_diff and (parche or "").strip():
        import ui as _ui
        _mostrar_diff_parche(parche, ruta)
        opciones = [
            ("a", "Aplicar el parche"),
            ("c", "Cancelar (no cambiar nada)"),
            ("e", "Editar manualmente"),
        ]
        mensaje = f"Diff propuesto para '{ruta or 'archivo'}' ‚Äî ¬øquÈ hacemos?"
        if preguntar is not None:
            eleccion = preguntar(opciones, mensaje, defecto="a")
        else:
            eleccion = _ui.preguntar_interactivo(
                opciones, mensaje, defecto="a")
        if eleccion == "c":
            info("[EditorPropio] Parche cancelado por el usuario; no se "
                 "realizÛ ning˙n cambio.")
            return False
        if eleccion == "e":
            aviso("[EditorPropio] EdiciÛn manual solicitada: el parche se "
                  f"descarta sin tocar '{ruta or 'el archivo'}'. Las copias "
                  "de seguridad previas est·n en ~/.snapcontext/backups/.")
            return False

    ok_validacion, detalle = _validar_parche_previo(
        parche, directorio, contenido_esperado)
    if not ok_validacion:
        aviso(f"[EditorPropio] ValidaciÛn previa fallida: {detalle}. "
              "Se intentar· la resoluciÛn autom·tica.")
    elif contenido_esperado is not None:
        depurar(f"[EditorPropio] ValidaciÛn previa OK: {detalle}")

    if _aplicar_parche(parche, directorio=directorio):
        return True
    info("[EditorPropio] Conflicto detectado; probando resoluciÛn "
         "incremental (lÌnea a lÌnea)...")
    return _aplicar_hunks_incremental(parche, directorio,
                                      mostrar_diff=mostrar_diff)



# ---------------------------------------------------------------------------
# Editor propio (Fase 3 ‚Äî EdiciÛn basada en AST)  ‚Äî v2.2.0
# ---------------------------------------------------------------------------
def _es_extension_python(ruta: str) -> bool:
    """True si ``ruta`` parece un archivo de Python editable con ``ast``."""
    return str(ruta).lower().endswith((".py", ".pyx", ".pxd"))


def _ast_disponible(ruta: str) -> bool:
    """True si se puede generar un AST para ``ruta`` (Python o tree-sitter).

    v5.6.0: para lenguajes no-Python se usa ``parser_universal`` (language
    pack) como detector principal; el backend cl·sico tree_sitter_languages
    queda como reserva.
    """
    if not ruta or not str(ruta).strip():
        return False
    if _es_extension_python(ruta):
        return True
    try:
        import parser_universal as pu          # noqa: E402
        if pu.detectar_lenguaje_por_extension(str(ruta)):
            return pu.backend_disponible()
    except Exception:                          # noqa: BLE001
        pass
    lenguaje = _lenguaje_tree_sitter(str(ruta))
    _importar_tree_sitter()
    return bool(tree_sitter is not None and _ts_lang is not None and lenguaje)


def _resumen_ast_python(contenido: str) -> dict:
    """Resumen del AST de un archivo Python (funciones, clases, variables, imports)."""
    resumen: dict = {
        "ok": False, "motor": "ast", "lenguaje": "python",
        "funciones": [], "clases": [], "variables": [], "imports": [],
        "error": None,
    }
    try:
        arbol = ast.parse(contenido)
    except SyntaxError as exc:
        resumen["error"] = f"sintaxis inv·lida: {exc}"
        return resumen
    resumen["ok"] = True
    variables: List[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            resumen["funciones"].append({
                "nombre": nodo.name,
                "linea": nodo.lineno,
                "argumentos": [a.arg for a in nodo.args.args],
                "es_metodo": bool(nodo.col_offset > 0),
            })
        elif isinstance(nodo, ast.ClassDef):
            metodos = [n.name for n in nodo.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            resumen["clases"].append(
                {"nombre": nodo.name, "linea": nodo.lineno, "metodos": metodos})
        elif isinstance(nodo, ast.Name) and isinstance(nodo.ctx, ast.Store):
            if nodo.id not in variables:
                variables.append(nodo.id)
                resumen["variables"].append({"nombre": nodo.id, "linea": nodo.lineno})
        elif isinstance(nodo, ast.Import):
            for alias in nodo.names:
                resumen["imports"].append({"tipo": "import", "nombre": alias.name,
                                           "linea": nodo.lineno})
        elif isinstance(nodo, ast.ImportFrom):
            modulo = nodo.module or ""
            for alias in nodo.names:
                resumen["imports"].append(
                    {"tipo": "from", "modulo": modulo, "nombre": alias.name,
                     "linea": nodo.lineno})
    return resumen


def _resumen_ast(contenido: str, ruta: str) -> dict:
    """Genera un resumen del AST de ``ruta`` para pas·rselo al proveedor de IA.

    - Python: usa el mÛdulo ``ast`` de la stdlib.
    - Otros lenguajes: usa ``tree_sitter`` si est· instalado.

    Devuelve un dict con ``ok``, ``motor``, ``lenguaje`` y una proyecciÛn simple
    (funciones/clases/imports/variables/llamadas). Nunca lanza excepciones.
    """
    lenguaje = _lenguaje_archivo(ruta, contenido) or ""
    if _es_extension_python(ruta) or lenguaje == "python":
        return _resumen_ast_python(contenido)
    # v5.6.0: parser_universal (tree-sitter language pack) como motor
    # principal; el backend cl·sico tree_sitter_languages queda de reserva.
    try:
        import parser_universal as pu              # noqa: E402
        resumen_pu = pu.resumen_archivo(ruta, contenido)
        if resumen_pu and resumen_pu.get("ok"):
            resumen_pu["llamadas"] = []
            return resumen_pu
    except Exception:                              # noqa: BLE001
        pass
    _importar_tree_sitter()
    if tree_sitter is not None and _ts_lang is not None and lenguaje:
        try:
            idioma = _ts_lang.get_language(lenguaje)
            parser = tree_sitter.Parser()
            try:
                parser.set_language(idioma)          # API antigua (<0.22)
            except (AttributeError, TypeError):
                parser.language = idioma             # API nueva (>=0.22)
            arbol = parser.parse(contenido.encode("utf-8"))
            simbolos = _extraer_simbolos_ts(arbol, lenguaje)
            return {
                "ok": True, "motor": "tree-sitter", "lenguaje": lenguaje,
                "funciones": simbolos.get("funciones", []),
                "clases": simbolos.get("clases", []),
                "imports": simbolos.get("imports", []),
                "llamadas": simbolos.get("llamadas", []),
                "variables": [], "error": None,
            }
        except Exception as exc:                     # gram·tica ausente, API distinta‚Ä¶
            return {"ok": False, "motor": "tree-sitter", "lenguaje": lenguaje,
                    "error": f"tree-sitter fallÛ para {lenguaje}: {exc}"}
    return {"ok": False, "motor": None, "lenguaje": lenguaje,
            "error": "sin analizador AST disponible para este lenguaje "
                     "(usa .py o instala tree-sitter)"}

def _formatear_resumen_ast(resumen: dict, ruta: str) -> str:
    """Devuelve una representaciÛn textual compacta del resumen para el prompt."""
    lineas = [
        f"Lenguaje: {resumen.get('lenguaje') or '?'}  "
        f"(motor: {resumen.get('motor') or 'ninguno'})",
        "",
    ]
    for clave, titulo in (("imports", "Imports"), ("clases", "Clases"),
                          ("funciones", "Funciones"), ("variables", "Variables"),
                          ("llamadas", "Llamadas")):
        items = resumen.get(clave) or []
        if not items:
            continue
        lineas.append(f"  {titulo}:")
        for it in items:
            if isinstance(it, dict):
                nombre = it.get("nombre") or it.get("atributo") or ""
                linea_n = it.get("linea")
                extraa = it.get("argumentos") or it.get("metodos")
                sufijo = f" (lÌnea {linea_n})" if linea_n else ""
                if extraa:
                    sufijo += f" {extraa}"
                lineas.append(f"    - {nombre}{sufijo}")
            else:
                lineas.append(f"    - {it}")
        lineas.append("")
    return "\n".join(lineas)


def _limpiar_fenced_codigo(texto: str) -> str:
    """Quita los delimitadores ``` ... ``` (y la etiqueta de lenguaje) de un bloque."""
    if not texto:
        return ""
    texto = texto.strip()
    if not texto.startswith("```"):
        return texto
    lineas = texto.splitlines()
    if lineas and lineas[0].strip().startswith("```"):
        lineas = lineas[1:]
    if lineas and lineas[-1].strip().startswith("```"):
        lineas = lineas[:-1]
    linea0 = lineas[0].strip() if lineas else ""
    if linea0 and re.match(r"^[a-zA-Z][\w+-]*$", linea0) and len(linea0) <= 16:
        lineas = lineas[1:]
    return "\n".join(lineas).strip()


def _interpretar_operaciones_ast(respuesta: str) -> Optional[List[dict]]:
    """Interpreta la respuesta del proveedor como operaciones AST.

    Prefiere una lista JSON de operaciones; si no es JSON v·lido, la trata como
    el cÛdigo completo resultante (envuelto en una operaciÛn ``completo``).
    Devuelve ``None`` si no se pudo interpretar nada.
    """
    limpio = _limpiar_fenced_codigo(respuesta or "")
    if not limpio:
        return None
    inicio = limpio.find("[")
    fin = limpio.rfind("]")
    if inicio != -1 and fin > inicio:
        try:
            dato = json.loads(limpio[inicio:fin + 1])
            if isinstance(dato, list) and dato:
                return dato
        except (json.JSONDecodeError, ValueError):
            pass
    return [{"tipo": "completo", "codigo": limpio}]


def _offset_caracteres(contenido: str, fila: int, col: int) -> Optional[int]:
    """Convierte (fila 1-based, col 0-based) a Ìndice de caracteres del cÛdigo."""
    if fila < 1 or col < 0:
        return None
    pos = 0
    fila_actual = 1
    for linea in contenido.splitlines(keepends=True):
        if fila_actual == fila:
            return pos + col
        pos += len(linea)
        fila_actual += 1
    return None

def _renombrar_identificador(contenido: str, viejo: str, nuevo: str) -> str:
    """Renombra un identificador (variable, funciÛn, clase, par·metro) en ``contenido``.

    Usa ``tokenize`` para no tocar cadenas ni comentarios y preservar el formateo.
    """
    if not viejo or not nuevo or viejo == nuevo:
        return contenido
    try:
        import io as _io
        from tokenize import NAME, generate_tokens

        tokens = list(generate_tokens(_io.StringIO(contenido).readline))
    except Exception:
        return contenido
    cambios: List[tuple] = []
    for tok in tokens:
        if tok.type == NAME and tok.string == viejo:
            inicio = _offset_caracteres(contenido, tok.start[0], tok.start[1])
            fin = _offset_caracteres(contenido, tok.end[0], tok.end[1])
            if inicio is not None and fin is not None and fin > inicio:
                cambios.append((inicio, fin, nuevo))
    for s, e, n in sorted(cambios, key=lambda t: t[0], reverse=True):
        contenido = contenido[:s] + n + contenido[e:]
    return contenido


def _insertar_import(contenido: str, importacion: str) -> str:
    """Inserta ``importacion`` tras los imports de la cabecera si a˙n no existe."""
    imp = (importacion or "").strip()
    if not imp:
        return contenido
    lineas = contenido.split("\n")
    if any(l.strip() == imp for l in lineas):
        return contenido
    idx = 0
    while idx < len(lineas):
        e = lineas[idx].lstrip()
        if e.startswith(("import ", "from ")) or not e.strip():
            idx += 1
        else:
            break
    lineas.insert(idx, imp)
    return "\n".join(lineas)


def _aplicar_operaciones_ast(contenido: str, operaciones: List[dict]) -> Optional[str]:
    """Aplica una lista de operaciones AST al cÛdigo.

    Soporta al menos:
      - ``{"tipo": "completo", "codigo": "..."}`` ‚Üí reemplaza todo el archivo.
      - ``{"tipo": "renombrar", "nombre": "x", "nuevo": "y"}`` ‚Üí renombra sÌmbolo.
      - ``{"tipo": "insertar_import", "codigo": "import os"}`` ‚Üí aÒade import.

    Devuelve el cÛdigo resultante o ``None`` si no hubo cambio aplicable.
    """
    if not operaciones:
        return None
    for op in operaciones:
        if op.get("tipo") == "completo" and op.get("codigo"):
            return _limpiar_fenced_codigo(op["codigo"])

    resultado = contenido
    for op in operaciones:
        tipo = op.get("tipo")
        if tipo == "renombrar":
            viejo = op.get("nombre") or op.get("antiguo")
            nuevo = op.get("nuevo")
            if viejo and nuevo:
                resultado = _renombrar_identificador(resultado, viejo, nuevo)
        elif tipo == "insertar_import":
            resultado = _insertar_import(
                resultado, op.get("importacion") or op.get("codigo") or "")
    return resultado if resultado != contenido else None

def _editor_ast(archivo: str, tarea: str, directorio: str = ".",
                proveedor: Optional[str] = None,
                modelo: Optional[str] = None,
                conciso: bool = False,
                max_context_tokens: Optional[int] = None,
                mostrar_razonamiento: bool = False) -> bool:
    """Editor propio (Fase 3 ‚Äî EdiciÛn basada en AST).

    1) Lee el contenido del archivo.
    2) Genera el AST (Python con ``ast``; otros lenguajes con ``tree-sitter``).
    3) Pasa un resumen del AST + la tarea al proveedor de IA.
    4) El proveedor devuelve un *parche AST* (instrucciones de modificaciÛn del
       ·rbol) o el cÛdigo nuevo completo.
    5) Aplica los cambios y guarda el archivo modificado (con copia de seguridad).

    Con ``conciso=True`` (v4.1.0) se usa un prompt reducido para modelos
    ligeros (Ollama local o ``--modelo-ligero``).

    Devuelve ``True`` si tuvo Èxito; ``False`` si no se pudo editar (para que el
    agente haga fallback a parche o sobrescritura).

    v6.1.0: con ``max_context_tokens`` los archivos grandes se envÌan con
    contexto selectivo (:func:`context_utils.seleccionar_contexto`); las
    operaciones AST se aplican igualmente sobre el contenido completo, por lo
    que en ese caso se descarta la op ``"completo"`` (reescribirÌa el archivo
    entero solo con el fragmento mostrado).
    """
    if not archivo or not str(archivo).strip():
        error("La ruta del archivo no puede estar vacÌa.")
        return False
    ruta_posix = _normalizar_relativa(str(archivo).replace("\\", "/").strip())
    if not ruta_posix:
        return False
    raiz = Path(directorio or ".").resolve()
    destino = (raiz / ruta_posix).resolve()
    if not destino.is_file():
        aviso(f"[EditorAST] No existe el archivo: {ruta_posix}")
        return False
    contenido = _leer_archivo(destino)
    if contenido is None:
        return False
    if not _ast_disponible(ruta_posix):
        aviso(f"[EditorAST] Sin analizador AST para '{ruta_posix}'; se delega el cambio.")
        return False
    resumen = _resumen_ast(contenido, ruta_posix)
    if not resumen.get("ok"):
        depurar(f"[EditorAST] No se pudo generar AST de '{ruta_posix}': "
                f"{resumen.get('error')}")
        return False

    proveedor = proveedor or cargar_configuracion().get("provider") or PROVEEDOR_DEFECTO
    lenguaje = resumen.get("lenguaje") or _lenguaje_archivo(ruta_posix,
                                                            contenido) or "?"
    # v6.1.0 ‚Äî Contexto selectivo: si el archivo supera el presupuesto de
    # tokens se envÌa resumen AST + bloque objetivo + bloques relevantes.
    # Las operaciones siguen aplic·ndose sobre `contenido` (el archivo entero).
    try:
        import context_utils as _ctx
        _limite = (max_context_tokens if max_context_tokens is not None
                   else MAX_CONTEXT_TOKENS)
        _objetivo = _ctx.objetivo_en_mensaje(contenido, lenguaje, tarea)
        contenido_envio = _ctx.seleccionar_contexto(
            contenido, lenguaje, objetivo=_objetivo, max_tokens=int(_limite))
        truncado = contenido_envio != contenido
        if truncado:
            if _objetivo:
                info(f"‚Ñπ Archivo grande ({_ctx.estimar_tokens(contenido)} "
                     f"tokens). Usando contexto selectivo (bloque: "
                     f"'{_objetivo}')...")
            else:
                info(f"‚Ñπ Archivo grande ({_ctx.estimar_tokens(contenido)} "
                     "tokens). Usando contexto selectivo...")
    except Exception as _exc:          # noqa: BLE001 ‚Äî nunca romper el modo AST
        depurar(f"[EditorAST] contexto selectivo fallÛ: {_exc}")
        contenido_envio, truncado = contenido, False
    num_lineas = contenido.count("\n") + 1
    if conciso:
        prompt = (
            f"Tarea: {tarea}\nArchivo: {ruta_posix} ({lenguaje})\n"
            f"SÌmbolos: {_formatear_resumen_ast(resumen, ruta_posix)}\n\n"
            f"```\n{contenido_envio}\n```\n\n"
            f"Responde SOLO una lista JSON de operaciones "
            f'[{{"tipo": "renombrar", "nombre": "x", "nuevo": "y"}}] '
            f'o [{{"tipo": "completo", "codigo": "..."}}]. Sin texto extra.'
        )
    else:
        prompt = (
            f"Vas a modificar un archivo comprendiendo su estructura sint·ctica "
            f"(AST). Objetivo: precisiÛn m·xima y cambios mÌnimos.\n\n"
            f"Tarea: {tarea}\n"
            f"Archivo: {ruta_posix}  (lenguaje: {lenguaje}, {num_lineas} lÌneas)\n\n"
            f"Resumen del AST (sÌmbolos disponibles y sus posiciones):\n"
            f"{_formatear_resumen_ast(resumen, ruta_posix)}\n\n"
            f"Contenido actual completo:\n```\n{contenido_envio}\n```\n\n"
            f"Reglas de ediciÛn:\n"
            f"- Conserva el estilo existente (indentaciÛn, comillas, convenciones).\n"
            f"- Modifica SOLO lo necesario para la tarea; no reorganices el resto.\n"
            f"- Usa los sÌmbolos del resumen del AST para anclar tus cambios.\n\n"
            f"Responde √öNICAMENTE con una lista JSON de operaciones de ediciÛn, por ejemplo:\n"
            f'[{{"tipo": "renombrar", "nombre": "viejo", "nuevo": "nuevo"}}]\n'
            f'O, si prefieres devolver el cÛdigo completo resultante:\n'
            f'[{{"tipo": "completo", "codigo": "def fn(): ...\\n..."}}]\n'
            f"Sin explicaciones ni markdown fuera del JSON."
        )
        if truncado:
            prompt += (
                "\nNOTA: solo se muestra parte del archivo por lÌmites de "
                "contexto. Responde SOLO con operaciones (\"renombrar\" / "
                "\"insertar_import\"); NO uses \"completo\".")
    try:
        respuesta = _enviar_al_proveedor(
            proveedor, modelo, [{"role": "user", "content": prompt}])
    except Exception as exc:
        error(f"[EditorAST] Error generando cambios para {ruta_posix}: {exc}")
        return False

    respuesta, _raz_ast = _procesar_razonamiento(
        respuesta, activo=mostrar_razonamiento)
    opos = _interpretar_operaciones_ast(respuesta)
    if truncado:
        # v6.1.0: con contexto selectivo una op "completo" reemplazarÌa TODO
        # el archivo solo con el fragmento mostrado ‚Üí se descarta y quedan las
        # operaciones seguras (renombrar/insertar_import), que se aplican sobre
        # el contenido completo. Si no queda ninguna, el modo AST falla y la
        # cadena sigue con parche/sobrescribir (que sÌ manejan el recorte).
        opos = [op for op in opos if op.get("tipo") != "completo"]
    if not opos:
        depurar(f"[EditorAST] El proveedor no devolviÛ operaciones AST para '{ruta_posix}'.")
        return False
    nuevo_contenido = _aplicar_operaciones_ast(contenido, opos)
    if not nuevo_contenido or nuevo_contenido == contenido:
        aviso(f"[EditorAST] No hubo cambio neto aplicable en '{ruta_posix}'.")
        return False
    exito(f"[EditorAST] EdiciÛn AST aplicada sobre {ruta_posix} (mÈtodo AST).")
    return _editor_sobrescribir(ruta_posix, nuevo_contenido, directorio=directorio)


def _extraer_error(resultado: "subprocess.CompletedProcess") -> str:
    """Une stdout+stderr, limpia cÛdigos ANSI y limita el tamaÒo del error
    que se mostrar· a Aider (evita llenar el contexto)."""
    salida = (resultado.stdout or "") + "\n" + (resultado.stderr or "")
    salida = re.sub(r"\x1b\[[0-9;]*m", "", salida)  # quitar colores ANSI
    salida = salida.strip() or "(el comando de prueba no devolviÛ salida)"
    if len(salida) > MAX_ERROR_SALIDA:
        salida = "\n... (salida recortada) ...\n" + salida[-MAX_ERROR_SALIDA:]
    return salida


def _resolver_comando_test(directorio: str,
                           comando_explicito: Optional[List[str]] = None
                           ) -> List[str]:
    """Resuelve el comando de pruebas del bucle (v5.3.0).

    Prioridad:
      1. ``comando_explicito`` (el usuario pasÛ ``--comando-test``);
      2. detecciÛn autom·tica con ``detector_tests``;
      3. ``COMANDO_TEST_DEFECTO`` (compatibilidad hacia atr·s).

    AsÌ, si el usuario no configura nada, el agente detecta el lenguaje del
    proyecto y ejecuta el comando adecuado sin intervenciÛn.
    """
    if comando_explicito:
        return list(comando_explicito)
    try:
        import detector_tests as _det
        det = _det.detectar_automaticamente(str(directorio))
        if det["detectado"] and det["comando"]:
            return shlex.split(det["comando"])
    except Exception:                       # noqa: BLE001 ‚Äî nunca romper el flujo
        pass
    return shlex.split(COMANDO_TEST_DEFECTO)


def ejecutar_bucle_test(consulta: str, archivos: List[str], directorio: str,
                        opciones_aider: str, comando_test: List[str],
                        max_iteraciones: int = MAX_ITERACIONES_TEST_DEFECTO) -> bool:
    """Bucle agÈntico b·sico: Aider ‚Üí pruebas ‚Üí si fallan, Aider las arregla.

    Este es el punto natural de extensiÛn: aquÌ puedes aÒadir m·s herramientas
    al bucle (p. ej. linters, analysizer de Flutter, generaciÛn de tests...).

    v5.3.0: si ``comando_test`` viene vacÌo se resuelve autom·ticamente con
    ``detector_tests`` (detecciÛn del lenguaje del proyecto).
    """
    comando_test = _resolver_comando_test(directorio, comando_test)
    if not comando_test:
        raise RuntimeError("El comando de pruebas est· vacÌo (--comando-test).")
    # v4.3.0: en sandbox el binario vive dentro del contenedor; la comprobaciÛn
    # de PATH del host no aplica.
    if not _SANDBOX_ACTIVO and shutil.which(comando_test[0]) is None:
        raise RuntimeError(
            f"No se encontrÛ el comando de pruebas '{comando_test[0]}'. "
            "Ajusta --comando-test."
        )

    ultimo_error = ""
    for iteracion in range(1, max_iteraciones + 1):
        info(f"IteraciÛn {iteracion} de {max_iteraciones} ‚Äî Aider...")
        if iteracion == 1 or not ultimo_error:
            mensaje = consulta
        else:
            # Devolvemos el error real de la iteraciÛn anterior para que Aider
            # repare el cÛdigo sin perder de vista la tarea original.
            mensaje = (
                f"La tarea original era:\n{consulta}\n\n"
                f"El comando de prueba fallÛ en la iteraciÛn {iteracion - 1} con:\n"
                f"```\n{ultimo_error}\n```\n"
                "Corrige esos errores sin cambiar el alcance de la tarea original."
            )
        ejecutar_aider(archivos, mensaje, directorio, opciones_aider)

        solicitud = " ".join(comando_test)
        info(f"Ejecutando pruebas: {solicitud}")
        # v4.3.0: con --sandbox las pruebas corren dentro del contenedor.
        if _SANDBOX_ACTIVO:
            codigo, stdout, stderr = _ejecutar_pruebas_argv(
                comando_test, directorio)
            resultado = subprocess.CompletedProcess(
                comando_test, codigo, stdout=stdout, stderr=stderr)
        else:
            resultado = subprocess.run(
                comando_test, cwd=directorio, capture_output=True, text=True
            )
        if resultado.returncode == 0:
            exito(f"¬°Pruebas superadas en la iteraciÛn {iteracion}!")
            return True
        ultimo_error = _extraer_error(resultado)
        aviso(
            f"Pruebas fallidas (cÛdigo {resultado.returncode}). "
            "Se envÌa el error a Aider para que lo corrija..."
        )

    error(f"No se consiguiÛ que las pruebas pasaran tras {max_iteraciones} iteraciones.")
    return False


# ---------------------------------------------------------------------------
# Bucle agÈntico con servidor Flutter (--server-loop / --manual-loop)
# ---------------------------------------------------------------------------
# Patrones que suelen anunciar que Flutter terminÛ de compilar y sirve.
_PATRONES_SERVIDOR = re.compile(
    r"Running on|Synced|is being served at|served at|available at|VM Service"
)
_RE_URL = re.compile(r"https?://[^\s\"'()<>]+")


def lanzar_servidor(directorio: str = ".",
                    dispositivo: str = "web-server",
                    puerto: int = 5000) -> subprocess.Popen:
    """Lanza `flutter run` en segundo plano y devuelve el subproceso.

    Se fusiona stderr en stdout (asÌ es m·s f·cil analizar la salida y, para
    dispositivos web, se fija el puerto para que coincida con --url-defecto).
    """
    if shutil.which("flutter") is None:
        raise RuntimeError(
            "No se encontrÛ 'flutter' en el PATH. Instala Flutter o revisa tu "
            "configuraciÛn (https://flutter.dev)."
        )
    cmd = ["flutter", "run", "-d", dispositivo]
    if dispositivo.startswith("web") or dispositivo in ("chrome", "edge"):
        cmd.extend(["--web-port", str(puerto)])
    depurar("Comando del servidor: " + " ".join(cmd))

    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proceso = subprocess.Popen(
        cmd,
        cwd=directorio,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # juntamos stdout y stderr
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,                  # lectura por lÌneas
        creationflags=flags,
    )
    _PROCESOS_ACTIVOS.add(proceso)  # para poder cerrarlo desde la seÒal SIGINT/SIGTERM
    return proceso


def _iniciar_lector_salida(proceso: subprocess.Popen) -> List[str]:
    """Hilo lector que acumula la salida de `proceso` en una lista.

    Esto permite a `esperar_servidor` revisar la salida sin bloquearse en un
    readline() (truco cross-platform, sin depender de select/fcntl) y deja la
    salida disponible tambiÈn para `obtener_error`.
    """
    buffer: List[str] = []
    finalizado = threading.Event()

    def _leer() -> None:
        try:
            for linea in proceso.stdout:
                buffer.append(linea.rstrip())
        except (AttributeError, ValueError, OSError):
            pass
        finally:
            finalizado.set()

    hilo = threading.Thread(target=_leer, daemon=True)
    hilo.start()
    # Se guardan en el propio objeto proceso para compartirlos con otras funciones.
    proceso.snapctx_buffer = buffer           # type: ignore[attr-defined]
    proceso.snapctx_lector_fin = finalizado   # type: ignore[attr-defined]
    return buffer


def esperar_servidor(proceso,
                     url_defecto: str = "http://localhost:5000",
                     timeout: int = 60) -> Optional[str]:
    """Espera a que el servidor Flutter estÈ listo leyendo su salida en vivo.

    Busca patrones tÌpicos de arranque ("Running on", "Synced", "served at",
    "available at", "VM Service") y, si aparece, extrae la URL real.

    Devuelve:
      - URL real o `url_defecto` si el servidor est· en marcha.
      - None si el proceso muriÛ sin arrancar (la salida queda disponible
        para `obtener_error`).
    """
    buffer = getattr(proceso, "snapctx_buffer", None)
    if buffer is None:
        buffer = _iniciar_lector_salida(proceso)

    consumidas = 0
    inicio = time.time()
    while time.time() - inicio < timeout:
        while consumidas < len(buffer):
            linea = buffer[consumidas]
            consumidas += 1
            _emitir(sys.stdout, linea)  # mostramos la salida de Flutter en vivo
            if _PATRONES_SERVIDOR.search(linea):
                coincidencia = _RE_URL.search(linea)
                if coincidencia:
                    return coincidencia.group(0).rstrip("\"'.,;,)")
                return url_defecto
        if proceso.poll() is not None:
            break  # el proceso terminÛ antes de arrancar el servidor
        time.sleep(0.3)

    # Se agotÛ el tiempo sin patrÛn conocido: si sigue vivo, asumimos que sirve.
    if proceso.poll() is None:
        aviso("No se detectÛ el patrÛn de arranque esperado; se asume que el "
              "servidor responde en " + url_defecto)
        return url_defecto
    return None


def _detener_servidor(proceso) -> None:
    """Termina el proceso del servidor (terminate ‚Üí kill) sin dejar huÈrfanos."""
    if proceso is None:
        return
    _PROCESOS_ACTIVOS.discard(proceso)
    try:
        if proceso.poll() is None:
            proceso.terminate()
            try:
                proceso.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proceso.kill()
    except (AttributeError, OSError, ValueError):
        pass

def obtener_error(proceso, max_caracteres: int = 4000) -> str:
    """Extrae y recorta el error del proceso del servidor (para pasarlo a Aider)."""
    lineas: List[str] = []
    buffer = getattr(proceso, "snapctx_buffer", None)
    if buffer:
        lineas = list(buffer)
    try:
        # Si quedaron lÌneas sin leer las drenamos (el proceso ya est· parado).
        if proceso.stdout:
            lineas.extend(proceso.stdout.read().splitlines())
    except (AttributeError, ValueError, OSError):
        pass

    texto = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lineas))  # quitar ANSI
    texto = texto.strip() or "(el servidor no devolviÛ salida)"
    if len(texto) > max_caracteres:
        # Nos quedamos con el final: ahÌ suele estar el error real.
        texto = "\n... (salida recortada) ...\n" + texto[-max_caracteres:]
    return texto


def _recortar(texto: str, longitud: int) -> str:
    texto = texto.strip()
    if len(texto) > longitud:
        return texto[:longitud] + "\n... (salida recortada) ..."
    return texto


def abrir_navegador(url: str) -> bool:
    """Abre la URL en el navegador del sistema (multiplataforma).

    Intenta primero `webbrowser.open` (port·til). Si no lo consigue (devuelve
    False o lanza), usa el comando nativo del sistema con `subprocess.run`
    (sin shell): `open` en macOS y `xdg-open` en Linux.
    """
    info(f"Abriendo el navegador en {url}...")
    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass

    if sys.platform == "darwin":
        comando = ["open", url]
    elif sys.platform.startswith("linux"):
        comando = ["xdg-open", url]
    else:
        comando = None

    if comando:
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.run(comando, shell=False, check=False, creationflags=flags)
            return True
        except (OSError, ValueError):
            aviso("No se pudo abrir el navegador. Abre "
                  + url + " manualmente.")
            return False

    aviso("No se pudo abrir el navegador autom·ticamente. Abre "
          + url + " manualmente.")
    return False


def _menu_conflicto_parche() -> str:
    """Men˙ interactivo cuando un parche no se aplica limpiamente (v4.1.0).

    Opciones: [a]plicar de todas formas ¬∑ [v]er diff ¬∑ [r]eintentar con IA
    ¬∑ [c]ancelar. Devuelve la letra elegida ('a' | 'v' | 'r' | 'c').
    """
    while True:
        try:
            print("‚ö† El parche no se aplicÛ limpiamente. ¬øQuÈ quieres hacer?")
            print("  [a] Aplicar de todas formas (sobrescribir el archivo)")
            print("  [v] Ver el diff manualmente")
            print("  [r] Reintentar con el proveedor de IA")
            print("  [c] Cancelar y conservar la versiÛn original")
            eleccion = input("(a/v/r/c): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "c"
        if eleccion in ("a", "v", "r", "c"):
            return eleccion


def _registrar_fallo_editor(archivo: str, tarea: str,
                            estrategias: list, motivo: str) -> None:
    """Registra un fallo del editor propio en ~/.snapcontext/logs/ (v4.1.0).

    Nunca lanza excepciones: es telemetrÌa local opcional para depuraciÛn.
    """
    try:
        carpeta = CONFIG_DIR / "logs"
        carpeta.mkdir(parents=True, exist_ok=True)
        sello = time.strftime("%Y-%m-%d %H:%M:%S")
        linea = (f"[{sello}] archivo={archivo} estrategias="
                 f"{' ‚Üí '.join(estrategias)} motivo={motivo or 'desconocido'} "
                 f"tarea={tarea[:200]}\n")
        with open(carpeta / "editor_fallos.log", "a", encoding="utf-8") as fh:
            fh.write(linea)
    except Exception:
        pass


def _preguntar_si(pregunta: str) -> bool:
    """Pregunta s/n (acepta si/sÌ) hasta obtener una respuesta v·lida."""
    while True:
        try:
            respuesta = input(_pintar(pregunta, _CYAN)).strip().lower()
        except EOFError:  # entrada no interactiva ‚Üí se asume 'n'
            return False
        if respuesta in ("s", "si", "sÌ"):
            return True
        if respuesta in ("n", "no"):
            return False
        aviso("Responde 's' o 'n'.")


def _pedir_detalle_error(error_servidor: str) -> str:
    """Pide al usuario (modo manual) que describa el error para d·rselo a Aider."""
    aviso("El usuario reportar· el problema y Aider lo corregir·.")
    try:
        descripcion = input(_pintar("Describe el error (o pega el mensaje): ",
                                    _CYAN)).strip()
    except EOFError:
        descripcion = ""
    if descripcion:
        return f"Arregla este error: {descripcion}"
    return f"Arregla este error: {error_servidor}"


def _puerto_de(url: str) -> int:
    """Extrae el puerto de una URL (5000 por defecto)."""
    try:
        return urlparse(url).port or 5000
    except (ValueError, TypeError):
        return 5000

def ejecutar_bucle_agente(consulta: str, archivos: List[str],
                          modo: str = "auto", max_intentos: int = 3,
                          directorio: str = ".",
                          opciones_aider: str = "",
                          dispositivo: str = "web-server",
                          url_defecto: str = "http://localhost:5000") -> bool:
    """Bucle agÈntico con servidor Flutter (flutter run en segundo plano).

    --server-loop (modo="auto"):
        Reintenta hasta `max_intentos`. Si el servidor arranca, pregunta si se
        quiere probar la app (abre el navegador con la URL real y espera Enter).
        Si se agotan los intentos, ofrece cambiar a modo manual.
    --manual-loop (modo="manual"):
        Tras cada intento pregunta siempre "¬øLa app funciona correctamente?",
        el usuario describe el error y Aider lo corrige (repite el ciclo).

    En los reintentos y cambios de modo, Aider recibe "Arregla este error: ...".
    """
    if modo not in ("auto", "manual"):
        raise RuntimeError(f"Modo de bucle agente desconocido: {modo}")
    max_intentos = max(1, max_intentos)

    error_ultimo = ""      # mensaje para Aider ("Arregla este error: ...")
    proceso_actual = None
    intento = 0
    try:
        while True:
            intento += 1
            info(f"Intento {intento} ‚Äî Aider...")
            if intento == 1 or not error_ultimo:
                mensaje = consulta
            else:
                mensaje = consulta + "\n\n" + error_ultimo
            ejecutar_aider(archivos, mensaje, directorio, opciones_aider)

            # ---- Lanzar flutter run en segundo plano -------------------------
            info("Lanzando Flutter en segundo plano (flutter run)...")
            proceso_actual = lanzar_servidor(
                directorio, dispositivo, puerto=_puerto_de(url_defecto)
            )
            url = None
            try:
                url = esperar_servidor(proceso_actual, url_defecto=url_defecto)
            finally:
                if url is None:
                    _detener_servidor(proceso_actual)

            if url is not None:
                exito(f"El servidor arrancÛ en {url}.")
                if modo == "auto":
                    # --- modo autom·tico --------------------------------------
                    if _preguntar_si(
                        "‚úÖ El servidor arrancÛ correctamente. "
                        "¬øQuieres probar la app manualmente? (s/n): "
                    ):
                        abrir_navegador(url)
                        try:
                            input(_pintar("Pulsa Enter cuando hayas terminado de "
                                          "probar la app...", _CYAN))
                        except EOFError:
                            pass
                    _detener_servidor(proceso_actual)
                    proceso_actual = None
                    exito("Bucle agÈntico completado (el servidor respondiÛ).")
                    return True

                # --- modo manual: el usuario decide ---------------------------
                if _preguntar_si("¬øLa app funciona correctamente? (s/n): "):
                    _detener_servidor(proceso_actual)
                    proceso_actual = None
                    exito("¬°Bucle agÈntico completado!")
                    return True
                _detener_servidor(proceso_actual)
                proceso_actual = None
                error_ultimo = _pedir_detalle_error(error_ultimo)
                continue

            # ---- el servidor no arrancÛ --------------------------------------
            salida_error = obtener_error(proceso_actual)
            error_ultimo = f"Arregla este error: {salida_error}"
            proceso_actual = None
            error(f"El servidor no arrancÛ en el intento {intento}.")
            _emitir(sys.stdout, _pintar("Salida del servidor:", _GRIS))
            _emitir(sys.stdout, _recortar(salida_error, 500))

            if modo == "manual":
                if _preguntar_si("¬øLa app funciona correctamente? (s/n): "):
                    exito("¬°Bucle agÈntico completado!")
                    return True
                error_ultimo = _pedir_detalle_error(error_ultimo)
                continue

            # ---- modo autom·tico: decidir reintentar o cambiar a manual -------
            if intento < max_intentos:
                aviso(
                    f"Intento {intento}/{max_intentos} fallido. Aider corregir· "
                    "y se reintentar· autom·ticamente..."
                )
                continue
            if _preguntar_si(
                f"‚ùå El bucle autom·tico fallÛ despuÈs de {max_intentos} intentos. "
                "¬øQuieres cambiar a modo manual? (s/n): "
            ):
                info("Cambiando a modo manual...")
                return ejecutar_bucle_agente(
                    consulta + "\n\n" + error_ultimo,
                    archivos, modo="manual", max_intentos=max_intentos,
                    directorio=directorio, opciones_aider=opciones_aider,
                    dispositivo=dispositivo, url_defecto=url_defecto,
                )
            error("Finalizado: el usuario decidiÛ no continuar en modo manual.")
            return False
    finally:
        # Garantiza que, aunque haya excepciÛn o Ctrl+C, no queden servidores sueltos.
        _detener_servidor(proceso_actual)


# ---------------------------------------------------------------------------
# Modo experto (--experto): revisar/editar la selecciÛn antes de Aider
# ---------------------------------------------------------------------------
def _normalizar_ruta_manual(raiz: Path, ruta: str) -> Optional[str]:
    """Normaliza una ruta tecleada por el usuario a formato POSIX relativo.

    Acepta "lib/a.dart", "./lib/a.dart" y rutas absolutas dentro del repo.
    Devuelve None si la ruta queda fuera del repositorio.
    """
    ruta = ruta.strip().strip('"').strip("'")
    if not ruta:
        return None
    candidata = Path(ruta)
    if candidata.is_absolute():
        try:
            return candidata.resolve().relative_to(raiz.resolve()).as_posix()
        except ValueError:
            return None  # ruta absoluta fuera del repo
    limpia = ruta.replace("\\", "/")
    if limpia.startswith("./"):
        limpia = limpia[2:]
    return limpia.strip("/") or None


def _pedir_archivo_para_agregar(raiz: Path, seleccion: List[str]) -> Optional[str]:
    """Pide una ruta, la valida (existe, dentro del repo, sin duplicados)."""
    try:
        ruta = input(_pintar("Ruta del archivo a aÒadir (relativa al repo): ",
                             _CYAN)).strip()
    except EOFError:
        return None

    normalizada = _normalizar_ruta_manual(raiz, ruta)
    if not normalizada:
        aviso("Ruta no v·lida.")
        return None

    # ComprobaciÛn de que el archivo existe y NO sale del repo (evita "..").
    candidata = (raiz / normalizada).resolve()
    try:
        candidata.relative_to(raiz.resolve())
    except ValueError:
        aviso(f"'{normalizada}' est· fuera del repositorio.")
        return None
    if not candidata.is_file():
        aviso(f"No existe el archivo: {normalizada}")
        return None
    if normalizada in seleccion:
        aviso(f"'{normalizada}' ya est· en la lista.")
        return None
    return normalizada


def _eliminar_por_indice(seleccion: List[str]) -> List[str]:
    """Pide un Ìndice y elimina ese archivo (valida que estÈ en rango)."""
    try:
        entrada = input(_pintar("√çndice a eliminar: ", _CYAN)).strip()
        indice = int(entrada)
    except (ValueError, EOFError):
        aviso("√çndice no v·lido.")
        return seleccion
    if not (1 <= indice <= len(seleccion)):
        aviso(f"√çndice fuera de rango (la lista tiene {len(seleccion)} archivo(s)).")
        return seleccion
    eliminado = seleccion.pop(indice - 1)
    exito(f"Eliminado: {eliminado}")
    return seleccion


def modo_experto(seleccion: List[str], raiz: Path) -> List[str]:
    """Modo experto: revisar/aÒadir/eliminar/limpiar archivos de la selecciÛn.

    Opciones del men˙:
      [a]gregar   ‚Üí pide una ruta (debe existir y estar dentro del repo).
      [e]liminar  ‚Üí pide un Ìndice (fuera de rango se rechaza).
      [l]impiar   ‚Üí vacÌa la lista (con confirmaciÛn).
      [c]ontinuar ‚Üí devuelve la lista final que usar· Aider.

    Devuelve la lista final (rutas POSIX relativas al repositorio).
    """
    while True:
        _emitir(sys.stdout, _pintar("‚îÄ‚îÄ Modo experto ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ", _CYAN))
        if not seleccion:
            aviso("La lista de archivos est· vacÌa.")
        for i, archivo in enumerate(seleccion, start=1):
            _emitir(sys.stdout, f"  [{i}] {archivo}")
        _emitir(sys.stdout, _pintar(
            "Opciones: [a]gregar   [e]liminar   [l]impiar   [c]ontinuar", _CYAN))

        try:
            opcion = input(_pintar("Elige (a/e/l/c): ", _CYAN)).strip().lower()
        except EOFError:
            opcion = "c"

        if opcion in ("c", "continuar", ""):
            if not seleccion:
                aviso("No se puede continuar con la lista vacÌa: aÒade archivos "
                      "con [a] o sal con Ctrl+C.")
                continue
            return seleccion

        if opcion in ("a", "agregar", "add"):
            ruta = _pedir_archivo_para_agregar(raiz, seleccion)
            if ruta:
                seleccion.append(ruta)
                exito(f"AÒadido: {ruta}")
            continue

        if opcion in ("e", "eliminar", "remove"):
            seleccion = _eliminar_por_indice(seleccion)
            continue

        if opcion in ("l", "limpiar", "clear"):
            if _preguntar_si("¬øVaciar toda la lista? (s/n): "):
                seleccion = []
                aviso("Lista vaciada.")
            continue

        aviso(f"OpciÛn no v·lida: '{opcion}'. Usa a, e, l o c.")

# ---------------------------------------------------------------------------
# Interfaz CLI
# ---------------------------------------------------------------------------
class _VersionAction(argparse.Action):
    """AcciÛn personalizada para --version: muestra el logo grande y sale."""
    def __init__(self, option_strings, dest, nargs=0, **kwargs):
        super().__init__(option_strings, dest, nargs=nargs, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        # v4.8.0: banner profesional con Rich (tabla de comandos incluida).
        _ui_mostrar_banner(VERSION)
        parser.exit()


# ---------------------------------------------------------------------------
# Memoria persistente (~/.snapcontext/historial.json) ‚Äî v0.10.0
# ---------------------------------------------------------------------------
def _cargar_historial() -> List[dict]:
    """Devuelve la lista de tareas guardadas en ~/.snapcontext/historial.json.

    Si el archivo no existe o est· corrupto se devuelve [] (sin lanzar error),
    para que el historial nunca rompa el flujo principal.
    """
    try:
        if HISTORIAL_PATH.is_file():
            datos = json.loads(HISTORIAL_PATH.read_text(encoding="utf-8"))
            if isinstance(datos, list):
                return [e for e in datos if isinstance(e, dict)]
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"No se pudo leer el historial ({HISTORIAL_PATH}): {exc}")
    return []


def _guardar_historial(entrada: dict) -> bool:
    """AÒade ``entrada`` al historial persistente y lo recorta si crece mucho.

    ``entrada`` tÌpico::

        {"fecha": "2026-08-21T12:00:00", "consulta": "...",
         "archivos": ["..."], "resultado": "Èxito"/"fallo",
         "duracion": 12.5}

    Devuelve True si se escribiÛ correctamente. Los errores solo avisan: la
    memoria es un extra y no debe interrumpir una tarea que sÌ funcionÛ.
    """
    try:
        historial = _cargar_historial()
        historial.append(entrada)
        historial = historial[-MAX_HISTORIAL_ENTRADAS:]
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        HISTORIAL_PATH.write_text(
            json.dumps(historial, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError as exc:
        aviso(f"No se pudo guardar en el historial: {exc}")
        return False


def _mostrar_historial(ultimas: int = 20) -> int:
    """Muestra las ``ultimas`` entradas m·s recientes del historial.

    Devuelve el n˙mero de entradas mostradas.
    """
    historial = _cargar_historial()
    if not historial:
        info("Historial vacÌo: a˙n no hay tareas guardadas.")
        return 0
    recientes = historial[-ultimas:]
    exito(f"√öltimas {len(recientes)} tarea(s) guardada(s) "
          f"({HISTORIAL_PATH}):")
    for entrada in reversed(recientes):     # la m·s reciente primero
        fecha = str(entrada.get("fecha", "?"))
        consulta = str(entrada.get("consulta", "?"))[:70]
        resultado = str(entrada.get("resultado", "?"))
        duracion = entrada.get("duracion")
        duracion_txt = f"{duracion:.1f}s" if isinstance(duracion, (int, float)) else "?"
        archivos = entrada.get("archivos") or []
        _emitir(sys.stdout, f"  [{fecha}] {resultado} ¬∑ {duracion_txt}")
        _emitir(sys.stdout, f"      consulta : {consulta}")
        if archivos:
            _emitir(sys.stdout, f"      archivos : {', '.join(map(str, archivos[:5]))}"
                    + ("‚Ä¶" if len(archivos) > 5 else ""))
    return len(recientes)


def _limpiar_historial() -> bool:
    """Borra ~/.snapcontext/historial.json. Devuelve True si se eliminÛ."""
    try:
        if HISTORIAL_PATH.exists():
            HISTORIAL_PATH.unlink()
            exito(f"Historial borrado ({HISTORIAL_PATH}).")
        else:
            info("No hay historial que borrar.")
        return True
    except OSError as exc:
        error(f"No se pudo borrar el historial: {exc}")
        return False


# ---------------------------------------------------------------------------
# Utilidades genÈricas para el agente autÛnomo ‚Äî v0.10.0
# ---------------------------------------------------------------------------
def _leer_archivo(ruta: Union[str, Path]) -> Optional[str]:
    """Lee un archivo (ruta relativa o absoluta) y devuelve su contenido.

    Devuelve ``None`` si no existe, es un directorio o falla la lectura
    (el error se registra con ``aviso``). Pensado para ser usado por el chat,
    el orquestador y futuros planificadores autÛnomos.
    """
    try:
        camino = Path(ruta).expanduser()
        if not camino.is_absolute():
            camino = Path.cwd() / camino
        camino = camino.resolve()
        if not camino.is_file():
            aviso(f"_leer_archivo: no existe o no es archivo: {camino}")
            return None
        return camino.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        aviso(f"_leer_archivo: error leyendo '{ruta}': {exc}")
        return None


def _ejecutar_comando(comando: str, directorio: str = ".",
                      timeout: int = 120, capture_output: bool = True) -> tuple:
    """Ejecuta ``comando`` (str de shell) en ``directorio``.

    Devuelve ``(codigo_retorno, stdout, stderr)``. Usa ``shell=True`` en todas
    las plataformas (cmd.exe en Windows, sh en Linux/macOS). Errores comunes
    (timeout, directorio inv·lido) devuelven ``(-1, "", mensaje_de_error)``
    sin lanzar excepciones.

    Con ``capture_output=False`` la salida se muestra en tiempo real en la
    consola (no se captura), y ``stdout``/``stderr`` devueltos ser·n vacÌos.
    """
    raiz = Path(directorio).expanduser()
    if not raiz.is_dir():
        return (-1, "", f"El directorio no existe: {raiz}")
    # v5.4.0: sandboxing inteligente. Decide por comando si se ejecuta dentro
    # del contenedor Docker, sin forzarlo para todos los comandos.
    decision = _decidir_ejecucion_sandbox(comando, str(raiz))
    if decision == _SANDBOX_ABORTAR:
        return (-1, "", "Comando peligroso abortado (no hay sandbox Docker disponible).")
    if decision == _SANDBOX_CONTENEDOR:
        # v6.4.0: con --sandbox-session se reutiliza una sesiÛn Docker en toda
        # la tarea; si est· solicitada, se ejecuta con `docker exec` del mismo
        # contenedor (se crea de forma perezosa en el primer comando).
        if _SESION_DOCKER_SOLICITADA:
            import sandbox_session as ss                           # noqa: E402
            if _asegurar_sesion_docker(str(raiz)):
                info(f"?? Ejecutando en sesiÛn Docker: {comando}")
                comando = ss.comando_en_sesion(comando)
                raiz = Path.cwd()  # docker se lanza desde el host
            else:
                if _SANDBOX_ACTIVO:
                    info(f"[sandbox] Ejecutando en contenedor: {comando}")
                comando = _envolver_sandbox(comando, str(raiz))
                raiz = Path.cwd()
        else:
            if _SANDBOX_ACTIVO:
                info(f"[sandbox] Ejecutando en contenedor: {comando}")
            comando = _envolver_sandbox(comando, str(raiz))
            raiz = Path.cwd()  # docker se lanza desde el host; el mount ya es absoluto
    try:
        # seguridad: si el comando va a ejecutarse directo (sin
        # contenedor, p. ej. con --no-sandbox) y es potencialmente peligroso,
        # pedir confirmaciÛn y abortar en modo --auto/no interactivo.
        if decision == _SANDBOX_DIRECTO and _es_comando_peligroso(comando):
            if _ui_es_auto() or not _entrada_interactiva():
                return (-1, "", "Comando peligroso abortado (modo --auto / no interactivo).")
            if not _preguntar_si(
                    f"Comando potencialmente peligroso: {comando}\n"
                    "øEjecutar igualmente? (s/n): "):
                return (-1, "", "Comando peligroso rechazado por el usuario.")
        # seguridad: helper seguro. Comandos SIN sintaxis de shell
        # (pipes/redirecciones/glÛbulos) se dividen con shlex.split y se
        # ejecutan con shell=False; los que la usan mantienen shell=True tras
        # la validaciÛn de peligro (realizada arriba / en el sandbox).
        proc = _ejecutar_con_politica(
            comando, cwd=str(raiz), timeout=timeout,
            capturar_salida=capture_output)
        if not capture_output:
            return (proc.returncode, "", "")
        return (proc.returncode, proc.stdout or "", proc.stderr or "")
    except RuntimeError as exc:
        return (-1, "", f"Comando bloqueado por seguridad: {exc}")
    except subprocess.TimeoutExpired:
        return (-1, "", f"El comando tardÛ demasiado (timeout={timeout}s)")
    except OSError as exc:
        return (-1, "", f"Error ejecutando '{comando}': {exc}")


# ---------------------------------------------------------------------------
# ?? Sandboxing con Docker (v4.3.0)
# ---------------------------------------------------------------------------
# Imagen por defecto del sandbox (ligera, con Python y herramientas comunes).
# Puede sobrescribirse con --sandbox-imagen o SNAPCONTEXT_SANDBOX_IMAGE.
SANDBOX_IMAGEN_DEFECTO = "python:3.11-slim"
SANDBOX_DIR_TRABAJO = "/workspace"
# Nombres/patrones de variables de entorno que se pasan al contenedor.
_SANDBOX_VARS_CLAVE = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                       "DEEPSEEK_API_KEY", "GROQ_API_KEY", "OLLAMA_URL",
                       "SNAPCONTEXT_SANDBOX_IMAGE")

# Estado global del sandbox (lo activa _activar_sandbox desde main()).
_SANDBOX_ACTIVO: bool = False
_SANDBOX_IMAGEN: str = SANDBOX_IMAGEN_DEFECTO
_SANDBOX_COMANDO_PREP: Optional[str] = None
# v6.4.0: persistencia Docker por sesiÛn (--sandbox-session). Cuando est·
# activa, los comandos se ejecutan en un ˙nico contenedor reutilizado en toda
# la tarea en lugar de `docker run --rm` por comando. Se solicita en main().
_SESION_DOCKER_SOLICITADA: bool = False

# ‚îÄ‚îÄ Sandboxing inteligente (v5.4.0) ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
# PolÌtica por comando (adem·s del sandbox forzado de --sandbox):
#   _NO_SANDBOX   : --no-sandbox o SNAPCONTEXT_SANDBOX=0. Desactiva TODO el
#                   sandbox (incluidos los comandos peligrosos).
#   _SANDBOX_SMART: habilita la detecciÛn autom·tica de comandos peligrosos.
_NO_SANDBOX: bool = False
_SANDBOX_SMART: bool = True
# _SNAPCONTEXT_SANDBOX_ALWAYS queda implÌcito leyendo el entorno al decidir;
# se activa en main() llamando a _activar_sandbox cuando la variable es "1".


def _deberia_usar_sandbox(comando: Optional[str],
                          args: Optional[argparse.Namespace] = None) -> bool:
    """Decide si ``comando`` debe ejecutarse dentro del sandbox Docker (v5.4.0).

    Orden de prioridad:

    1. ``--no-sandbox`` (flag o ``SNAPCONTEXT_SANDBOX=0``) ‚Üí ``False``.
    2. ``--sandbox`` explÌcito ‚Üí ``True`` (m·xima prioridad activa).
    3. ``SNAPCONTEXT_SANDBOX=1`` ‚Üí ``True`` (siempre activo).
    4. El comando es peligroso (``sandbox_utils.es_comando_peligroso``) ‚Üí ``True``.
    5. Cualquier otro caso ‚Üí ``False``.

    Cuando ``args`` se omite (p. ej. dentro de ``_ejecutar_comando``) se usa la
    polÌtica global fijada en ``main()`` (``_NO_SANDBOX`` / ``_SANDBOX_ACTIVO``).
    """
    # 1. Opt-out explÌcito: flag --no-sandbox o entorno SNAPCONTEXT_SANDBOX=0.
    no_sandbox = _NO_SANDBOX
    if args is not None and getattr(args, "no_sandbox", False):
        no_sandbox = True
    if no_sandbox or os.environ.get("SNAPCONTEXT_SANDBOX") == "0":
        return False
    # 2. Sandbox forzado justificaciÛn para todo.
    if args is not None and getattr(args, "sandbox", False):
        return True
    # 2b. v4.3.0: sandbox global activo (--sandbox en main()) ‚Üí todo al
    # contenedor, como siempre (compatibilidad hacia atr·s).
    if _SANDBOX_ACTIVO:
        return True
    # 3. Siempre activo por entorno.
    if os.environ.get("SNAPCONTEXT_SANDBOX") == "1":
        return True
    # 4. Comando peligroso ‚Üí sandbox autom·ticamente.
    if _SANDBOX_SMART and es_comando_peligroso(comando):
        return True
    # 5. Resto (seguro) ‚Üí sin sandbox.
    return False


def _configurar_no_sandbox(activo: bool) -> None:
    """Fija la polÌtica global ``--no-sandbox`` (para tests y CLI)."""
    global _NO_SANDBOX
    _NO_SANDBOX = bool(activo)


# v6.24.0: orquestaciÛn inteligente de modelos (model_router.py).
_MODEL_ROUTING_ACTIVO = True     # flag --model-routing (por defecto activado)
_MODELO_EXPLICITO = False        # --model/--provider explÌcitos ‚Üí sin enrutado


# v6.30.0: enrutamiento hÌbrido Local-Nube (fallback entre modelos).
_MODEL_FALLBACK_ACTIVO = True              # --model-fallback (por defecto: sÌ)
_UMBRAL_COMPLEJIDAD_CLI: Optional[int] = None      # --complejidad-umbral N
_PRIORIDAD_LOCAL_CLI: Optional[List[str]] = None   # --model-prioridad-local
_PRIORIDAD_NUBE_CLI: Optional[List[str]] = None    # --model-prioridad-nube

# Marcas de error de AUTENTICACI”N (v6.30.0): NO disparan el fallback porque
# reintentar con otro modelo no arregla una clave ausente o inv·lida; se
# exige acciÛn del usuario (exportar la variable de entorno correcta).
_MARCAS_ERROR_AUTH: Tuple[str, ...] = (
    "api key", "apikey", "api_key", "clave", "variable de entorno",
    "unauthorized", "authentication", "forbidden", "permission denied",
    "invalid_api_key", "401", "403",
)


def _configurar_model_fallback(activo: bool) -> None:
    """Fija el estado global del fallback entre modelos (v6.30.0).

    - ``True`` (defecto): ante fallo de API/timeout se prueba el siguiente
      modelo de la cadena de prioridad local?nube (:mod:`model_router`).
    - ``False`` (``--no-model-fallback``): se usa solo el modelo configurado
      (comportamiento idÈntico a v6.24.0).
    """
    global _MODEL_FALLBACK_ACTIVO
    _MODEL_FALLBACK_ACTIVO = bool(activo)


def _configurar_hibrido_cli(umbral: Optional[int] = None,
                            prioridad_local: Optional[List[str]] = None,
                            prioridad_nube: Optional[List[str]] = None) -> None:
    """Fija los overrides CLI del enrutamiento hÌbrido (v6.30.0).

    - ``umbral``         : ``--complejidad-umbral N`` (longitud_consulta).
    - ``prioridad_local``: ``--model-prioridad-local prov/model ...``.
    - ``prioridad_nube`` : ``--model-prioridad-nube prov/model ...``.

    Los flags CLI tienen prioridad sobre ``config.json``; ``None`` significa
    "sin override" (se usa la configuraciÛn persistida).
    """
    global _UMBRAL_COMPLEJIDAD_CLI, _PRIORIDAD_LOCAL_CLI, _PRIORIDAD_NUBE_CLI
    _UMBRAL_COMPLEJIDAD_CLI = int(umbral) if umbral is not None else None
    _PRIORIDAD_LOCAL_CLI = list(prioridad_local) if prioridad_local else None
    _PRIORIDAD_NUBE_CLI = list(prioridad_nube) if prioridad_nube else None


def _configurar_model_routing(activo: bool, explicito: bool = False) -> None:
    """Fija el estado global del enrutamiento de modelos (v6.24.0).

    - ``activo``   : flag ``--model-routing`` (True) / ``--no-model-routing``.
    - ``explicito``: True si el usuario pasÛ ``--model`` o ``--provider``;
      en ese caso el enrutamiento se ignora (prioridad m·xima de los flags).
    """
    global _MODEL_ROUTING_ACTIVO, _MODELO_EXPLICITO
    _MODEL_ROUTING_ACTIVO = bool(activo)
    _MODELO_EXPLICITO = bool(explicito)


def _cargar_configuracion_routing() -> dict:
    """Lee la secciÛn ``model_routing`` de ``~/.snapcontext/config.json``.

    Devuelve un dict ``{categoria: {"provider": ..., "model": ...}}``; vacÌo
    (o {} si la secciÛn no existe/corrupta) ‚Üí se usa el modelo por defecto.
    """
    try:
        seccion = cargar_configuracion().get("model_routing")
    except Exception:                                    # noqa: BLE001
        return {}
    return seccion if isinstance(seccion, dict) else {}


def _configuracion_routing_efectiva() -> dict:
    """SecciÛn ``model_routing`` con los overrides CLI aplicados (v6.30.0).

    Combina la configuraciÛn persistida en ``~/.snapcontext/config.json`` con
    los flags ``--complejidad-umbral`` y ``--model-prioridad-local/nube`` (los
    flags ganan). Nunca lanza; sin configuraciÛn devuelve ``{}``.
    """
    try:
        seccion = dict(_cargar_configuracion_routing())
    except Exception:                                    # noqa: BLE001
        seccion = {}
    if _UMBRAL_COMPLEJIDAD_CLI is not None:
        previos = seccion.get("umbral_complejidad")
        umbrales = dict(previos) if isinstance(previos, dict) else {}
        umbrales["longitud_consulta"] = _UMBRAL_COMPLEJIDAD_CLI
        seccion["umbral_complejidad"] = umbrales
    if _PRIORIDAD_LOCAL_CLI:
        seccion["prioridad_local"] = list(_PRIORIDAD_LOCAL_CLI)
    if _PRIORIDAD_NUBE_CLI:
        seccion["prioridad_nube"] = list(_PRIORIDAD_NUBE_CLI)
    return seccion


def _extraer_consulta_mensajes(mensajes: List[dict]) -> str:
    """⁄ltimo mensaje del usuario (heurÌstica de complejidad, v6.30.0).

    ``_enviar_al_proveedor`` no recibe la consulta original; para las
    heurÌsticas de :mod:`model_router` se usa el ˙ltimo mensaje ``user``
    (truncado a 100.000 caracteres por rendimiento). Nunca lanza.
    """
    try:
        for mensaje in reversed(mensajes):
            if isinstance(mensaje, dict) and mensaje.get("role") == "user":
                return str(mensaje.get("content") or "")[:100000]
    except Exception:                                    # noqa: BLE001
        pass
    return ""


def _es_error_autenticacion(exc: BaseException) -> bool:
    """øIndica ``exc`` un problema de autenticaciÛn/permisos? (v6.30.0).

    RestricciÛn del fallback hÌbrido: los errores de autenticaciÛn (clave
    ausente/inv·lida, 401/403) NO se reintentan con otro modelo ó el
    siguiente fallarÌa igual ó; se aborta con el error original.
    """
    codigo = getattr(exc, "status_code", None)
    if codigo in (401, 403):
        return True
    try:
        texto = f"{type(exc).__name__}: {exc}".lower()
    except Exception:                                    # noqa: BLE001
        return False
    return any(marca in texto for marca in _MARCAS_ERROR_AUTH)


def _es_comando_peligroso(comando: str) -> bool:
    """Alias de detecciÛn (delegado a :mod:`sandbox_utils`)."""
    return es_comando_peligroso(comando)


# CÛdigos de decisiÛn de ejecuciÛn respecto al sandbox.
_SANDBOX_ABORTAR = -1    # no ejecutar (comando peligroso sin Docker disponible)
_SANDBOX_DIRECTO = 0     # ejecutar directamente (comando seguro / opt-out)
_SANDBOX_CONTENEDOR = 1  # ejecutar dentro del contenedor Docker


def _decidir_ejecucion_sandbox(comando: str, directorio: str) -> int:
    """Resuelve cÛmo ejecutar ``comando`` y gestiona el aviso de peligro.

    Devuelve uno de :data:`_SANDBOX_CONTENEDOR`, :data:`_SANDBOX_DIRECTO` o
    :data:`_SANDBOX_ABORTAR`.

    - Si el comando es peligroso y el sandbox Docker **no** est· disponible:
      avisa y (modo interactivo) pregunta si continuar sin sandbox; en modo
      ``--auto`` (o stdin no interactivo) **aborta**.
    - Si es peligroso y hay Docker: avisa con el candado y lo encapsula.
    """
    if not _deberia_usar_sandbox(comando):
        return _SANDBOX_DIRECTO

    peligroso = _es_comando_peligroso(comando)

    # Sandbox ya forzado / activo globalmente (--sandbox o env=1 en main()).
    if _SANDBOX_ACTIVO:
        return _SANDBOX_CONTENEDOR

    # Sandbox detectado por peligro (o env=1) ‚Üí comprobar disponibilidad.
    if _docker_disponible():
        if peligroso:
            info("?? Comando potencialmente peligroso detectado. "
                 "Ejecutando en sandbox Docker.")
        else:
            depurar("[sandbox] SNAPCONTEXT_SANDBOX=1 ‚Üí Ejecutando en contenedor.")
        return _SANDBOX_CONTENEDOR

    # Solicitado pero sin Docker disponible.
    if peligroso:
        aviso("‚ö†Ô∏è Comando peligroso detectado. No se puede usar sandbox "
              "(Docker no instalado).")
        if _ui_es_auto() or not _entrada_interactiva():
            aviso("  ‚Üí Modo --auto: se aborta la ejecuciÛn del comando.")
            return _SANDBOX_ABORTAR
        if not _preguntar_si("¬øContinuar sin sandbox? (s/n): "):
            aviso("  ‚Üí EjecuciÛn rechazada por el usuario.")
            return _SANDBOX_ABORTAR
        return _SANDBOX_DIRECTO

    # Sandbox solicitado (env=1) pero sin Docker y comando no peligroso:
    # mejor esfuerzo ‚Üí seguir directo (no es destructivo).
    depurar("[sandbox] Solicitado pero Docker no disponible; se contin˙a directo.")
    return _SANDBOX_DIRECTO


def _docker_disponible() -> bool:
    """Comprueba que Docker est· instalado Y que el daemon est· en ejecuciÛn.

    - ``docker --version`` existe en el PATH.
    - ``docker info`` responde sin error (daemon activo).

    Nunca lanza excepciones; devuelve ``False`` ante cualquier problema.
    """
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, text=True,
            timeout=30, creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _sandbox_imagen_resuelta(explicita: Optional[str] = None) -> str:
    """Resuelve la imagen del sandbox: flag > variable de entorno > defecto."""
    return (explicita or os.environ.get("SNAPCONTEXT_SANDBOX_IMAGE", "").strip()
            or SANDBOX_IMAGEN_DEFECTO)


def _activar_sandbox(imagen: Optional[str] = None,
                     comando_prep: Optional[str] = None,
                     estricto: bool = True) -> bool:
    """Activa el sandbox global si Docker est· disponible.

    Args:
        imagen: imagen Docker (--sandbox-imagen o env por defecto).
        comando_prep: comando de preparaciÛn previo (--sandbox-comando).
        estricto: si es ``True`` (``--sandbox`` explÌcito) y Docker no est·
            disponible lanza ``RuntimeError``; si es ``False`` solo avisa y
            contin˙a sin sandbox.

    Devuelve ``True`` si el sandbox quedÛ activo.
    """
    global _SANDBOX_ACTIVO, _SANDBOX_IMAGEN, _SANDBOX_COMANDO_PREP
    if not _docker_disponible():
        mensaje = (
            "--sandbox solicitado pero Docker no est· disponible "
            "(¬øinstalado? ¬øel daemon est· en ejecuciÛn?). "
            "Instala Docker Desktop o inicia el servicio 'docker'."
        )
        _SANDBOX_ACTIVO = False
        if estricto:
            raise RuntimeError(mensaje)
        aviso(mensaje + "\n  ‚Üí Se contin˙a SIN sandbox.")
        return False
    _SANDBOX_ACTIVO = True
    _SANDBOX_IMAGEN = _sandbox_imagen_resuelta(imagen)
    _SANDBOX_COMANDO_PREP = (comando_prep or "").strip() or None
    exito(f"?? Sandbox activo (imagen: {_SANDBOX_IMAGEN}, "
          f"directorio montado en {SANDBOX_DIR_TRABAJO}).")
    return True


def _desactivar_sandbox() -> None:
    """Desactiva el sandbox global (vuelve al comportamiento normal)."""
    global _SANDBOX_ACTIVO, _SANDBOX_COMANDO_PREP
    _SANDBOX_ACTIVO = False
    _SANDBOX_COMANDO_PREP = None


def sandbox_activo() -> bool:
    """Indica si el sandbox Docker est· activo."""
    return _SANDBOX_ACTIVO


def _envolver_sandbox(comando: str, directorio: str = ".") -> str:
    """Envuelve ``comando`` en un ``docker run`` dentro del sandbox.

    Genera algo como::

        docker run --rm -v "<dir>:/workspace" -w /workspace \
                   -e GEMINI_API_KEY ... <imagen> sh -c "<comando>"

    Si hay comando de preparaciÛn (--sandbox-comando), se antepone con
    ``&&``. Sin sandbox activo devuelve ``comando`` tal cual.
    """
    if not _SANDBOX_ACTIVO:
        return comando
    raiz = Path(directorio).expanduser().resolve()
    partes = ["docker", "run", "--rm",
              "-v", f"{raiz}:{SANDBOX_DIR_TRABAJO}",
              "-w", SANDBOX_DIR_TRABAJO]
    # Pasar las variables de entorno relevantes del host al contenedor.
    vistas = set()
    for nombre in list(os.environ):
        incluir = nombre in _SANDBOX_VARS_CLAVE or nombre.endswith("_API_KEY")
        if incluir and nombre not in vistas:
            vistas.add(nombre)
            partes.extend(["-e", nombre])
    partes.append(_SANDBOX_IMAGEN)
    if _SANDBOX_COMANDO_PREP:
        comando = f"{_SANDBOX_COMANDO_PREP} && ({comando})"
    partes.extend(["sh", "-c", comando])
    return shlex.join(partes)
# --- Persistencia Docker por sesiÛn (v6.4.0) ---------------------------------
def _configurar_sesion_docker(solicitada: bool) -> None:
    """Fija el estado global de sesiÛn persistente (para CLI y tests)."""
    global _SESION_DOCKER_SOLICITADA
    _SESION_DOCKER_SOLICITADA = bool(solicitada)


def _asegurar_sesion_docker(directorio: str) -> Optional[str]:
    """Devuelve el contenedor de sesiÛn activo, cre·ndolo si hace falta.

    La sesiÛn se crea de forma perezosa en el primer comando de la tarea
    (``--sandbox-session``). Si ya existe en memoria la reutiliza (sin volver
    a lanzar ``docker run``). Devuelve el nombre del contenedor o ``None``.
    """
    import sandbox_session as ss                                   # noqa: E402
    if ss.sesion_nombre() is not None:
        return ss.sesion_nombre()
    nombre = ss.crear_sesion(directorio, _SANDBOX_IMAGEN,
                             _SANDBOX_COMANDO_PREP,
                             vars_entorno=_SANDBOX_VARS_CLAVE)
    return nombre


def _destruir_sesion_si_aplica() -> None:
    """Destruye la sesiÛn Docker si la tarea la solicitÛ (v6.4.0).

    Se llama al finalizar el plan o el bucle ReAct y desde el manejador de
    seÒales, de modo que no queden contenedores huÈrfanos. Nunca lanza.
    """
    if not _SESION_DOCKER_SOLICITADA:
        return
    try:
        import sandbox_session as ss                               # noqa: E402
        ss.destruir_sesion()
    except Exception as exc:                                       # noqa: BLE001
        aviso(f"[salida] No se pudo destruir la sesiÛn Docker ({exc}).")


def _limpiar_sesiones_huÈrfanas(auto: bool = False) -> int:
    """Elimina contenedores ``snap-session-*`` sobrantes (--sandbox-session-clean)."""
    import sandbox_session as ss                                   # noqa: E402
    return ss.limpiar_huÈrfanos(auto=auto)


def _ejecutar_pruebas_argv(comando: List[str], directorio: str) -> tuple:
    """Ejecuta un comando de pruebas (lista argv) respetando el sandbox.

    Devuelve ``(codigo_retorno, stdout, stderr)`` como :func:`_ejecutar_comando`.
    """
    if not comando:
        return (-1, "", "El comando de pruebas est· vacÌo.")
    return _ejecutar_comando(" ".join(comando), directorio, timeout=1800)


@contextlib.contextmanager
def _sandbox_pausado():
    """Desactiva temporalmente el sandbox (herramientas de solo lectura).

    Las herramientas MCP que no modifican el sistema (grep, git_status,
    git_diff...) se ejecutan fuera del contenedor para mayor velocidad.
    """
    global _SANDBOX_ACTIVO
    previo = _SANDBOX_ACTIVO
    _SANDBOX_ACTIVO = False
    try:
        yield
    finally:
        _SANDBOX_ACTIVO = previo


# --- Procesos en segundo plano para execute_command (v2.3.0) -----------------
_PROCESOS_FONDO: dict = {}   # pid ‚Üí estado (para ejecuciÛn en background)


def _lanzar_proceso_fondo(comando: str, directorio: str = ".",
                          capture_output: bool = True) -> dict:
    """Lanza ``comando`` en segundo plano (Popen). Devuelve un registro con el PID.

    El proceso queda registrado en ``_PROCESOS_FONDO`` para poder consultarlo
    despuÈs con :func:`_estado_proceso_fondo`. Nunca lanza excepciones.
    """
    raiz = Path(directorio).expanduser()
    if not raiz.is_dir():
        return {"ok": False, "error": f"El directorio no existe: {raiz}"}
    # v4.3.0: los procesos en segundo plano tambiÈn respetan --sandbox.
    if _SANDBOX_ACTIVO:
        # v6.4.0: con --sandbox-session se lanzan dentro del contenedor de sesiÛn.
        if _SESION_DOCKER_SOLICITADA:
            import sandbox_session as ss                           # noqa: E402
            if _asegurar_sesion_docker(str(raiz)):
                info(f"?? Ejecutando en sesiÛn Docker (background): {comando}")
                comando = ss.comando_en_sesion(comando)
                raiz = Path.cwd()
            else:
                info(f"[sandbox] Ejecutando en contenedor (background): {comando}")
                comando = _envolver_sandbox(comando, str(raiz))
                raiz = Path.cwd()
        else:
            info(f"[sandbox] Ejecutando en contenedor (background): {comando}")
            comando = _envolver_sandbox(comando, str(raiz))
            raiz = Path.cwd()
    # seguridad: en background no hay confirmaciÛn interactiva ˙til;
    # si no hay sandbox y el comando es peligroso, se rechaza sin lanzarlo.
    if not _SANDBOX_ACTIVO and _es_comando_peligroso(comando):
        return {"ok": False,
                "error": f"Comando peligroso rechazado (sin sandbox): {comando}"}
    try:
        if capture_output:
            proc = subprocess.Popen(
                comando, cwd=str(raiz), shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace")
        else:
            proc = subprocess.Popen(comando, cwd=str(raiz), shell=True)
        registro = {"ok": True, "pid": proc.pid,
                    "proceso": proc, "estado": "ejecutando",
                    "comando": comando, "codigo_retorno": None,
                    "stdout": "", "stderr": ""}
        _PROCESOS_FONDO[proc.pid] = registro
        return {"ok": True, "pid": proc.pid, "comando": comando}
    except OSError as exc:
        return {"ok": False, "error": f"Error lanzando '{comando}': {exc}"}


def _estado_proceso_fondo(pid: int) -> dict:
    """Consulta el estado de un proceso lanzado en segundo plano.

    Si ya terminÛ, captura su stdout/stderr (si se pidiÛ captura) y lo marca
    como finalizado. Devuelve un dict con ``estado``, ``pid`` y (si terminÛ)
    ``codigo_retorno``, ``stdout`` y ``stderr``.
    """
    registro = _PROCESOS_FONDO.get(pid)
    if registro is None:
        return {"ok": False, "estado": "desconocido", "pid": pid,
                "error": f"no hay proceso en segundo plano con pid {pid}"}
    proc = registro.get("proceso")
    if proc is None:
        return {"ok": True, "estado": registro.get("estado", "desconocido"),
                "pid": pid}
    if proc.poll() is None:
        registro["estado"] = "ejecutando"
        return {"ok": True, "estado": "ejecutando", "pid": pid}
    # Ya terminÛ: capturar salida si se pidiÛ.
    if proc.stdout is not None:
        try:
            registro["stdout"] = (proc.stdout.read() or "") if proc.stdout else ""
        except Exception:
            registro["stdout"] = ""
    if proc.stderr is not None:
        try:
            registro["stderr"] = (proc.stderr.read() or "") if proc.stderr else ""
        except Exception:
            registro["stderr"] = ""
    registro["codigo_retorno"] = proc.returncode
    registro["estado"] = "finalizado"
    return {"ok": True, "estado": "finalizado", "pid": pid,
            "codigo_retorno": proc.returncode,
            "stdout": registro["stdout"], "stderr": registro["stderr"]}

# ---------------------------------------------------------------------------
# Modo chat interactivo (--chat) ‚Äî v0.10.0
# ---------------------------------------------------------------------------
AYUDA_CHAT = """Comandos disponibles:
  /salir                 ‚Üí salir del chat
  /archivos              ‚Üí mostrar los archivos del contexto actual
  /context               ‚Üí alias de /archivos (contexto de la conversaciÛn)
  /limpiar               ‚Üí limpiar el historial de conversaciÛn
  /seleccion <consulta>  ‚Üí seleccionar archivos relevantes con el proveedor actual
  /provider <proveedor>  ‚Üí cambiar proveedor (gemini | anthropic | ollama | deepseek | groq)
  /historial             ‚Üí mostrar las ˙ltimas tareas guardadas
  /run <comando>         ‚Üí ejecutar un comando de shell y mostrar su salida
                           (pide permiso salvo con --no-confirmar)
  /read <archivo>        ‚Üí mostrar el contenido de un archivo
  /explore <tema>        ‚Üí buscar un tema en el cÛdigo (rg/grep/findstr, sin permiso)
  /fix <mensaje>         ‚Üí ejecutar el alias fix (bucle de pruebas)
  /review <mensaje>      ‚Üí ejecutar el alias review (vista previa + experto)
  /server <mensaje>      ‚Üí ejecutar el alias server (bucle con servidor)
  /edit <archivo>        ‚Üí abrir el archivo en el editor (VSCode/nano/notepad;
                           pide permiso salvo con --no-confirmar)
  /save                  ‚Üí guardar la sesiÛn actual en historial.json
  /tools                 ‚Üí listar las herramientas MCP disponibles
  /tool <nombre> <args>  ‚Üí ejecutar una herramienta MCP
                           (p. ej.: /tool grep login ¬∑ /tool read_file a.py)
                           args en JSON tambiÈn v·lidos: /tool read_file {"ruta": "a.py", "linea_inicio": 10}
  /search <consulta>     ‚Üí b˙squeda sem·ntica de archivos (embeddings; requiere
                           pip install snapcontext[embeddings])
  /buscar <consulta>     ‚Üí alias de /search (v1.4.0)
  /grafo                 ‚Üí grafo de dependencias del proyecto en texto ASCII
  /dependencias <archivo> ‚Üí imports y dependencias inversas de un archivo
  /claude                ‚Üí mostrar la memoria del proyecto (CLAUDE.md)
  /context               ‚Üí mostrar memoria del proyecto y archivos en contexto
  /asesor                ‚Üí an·lisis proactivo: sugerencias de mejora del cÛdigo
  /seguridad             ‚Üí an·lisis de vulnerabilidades ?? del proyecto
  /rendimiento           ‚Üí an·lisis de rendimiento ‚ö° del proyecto
  /plugin [p.h args]     ‚Üí lista plugins o ejecuta plugin.herramienta (args JSON)
  /ayuda                 ‚Üí mostrar esta ayuda
Cualquier otro texto se envÌa como mensaje al proveedor de IA; si parece una
pregunta de exploraciÛn, SnapContext puede usar herramientas MCP de solo
lectura autom·ticamente y aÒadir el resultado como contexto.
Los comandos /run, /explore, /fix, /review y /server se ejecutan en un hilo
separado para no bloquear el chat."""


# ---------------------------------------------------------------------------
# Razonamiento del modelo (chain-of-thought) ‚Äî v6.2.0
# ---------------------------------------------------------------------------
_CLAVES_RAZONAMIENTO = ("reasoning", "reasoning_content", "thinking",
                        "chain_of_thought", "thoughts", "razonamiento",
                        "thought")
_RE_THINK = re.compile(r"<think>(.*?)</think>", re.S | re.I)
_RE_THINK_ABIERTO = re.compile(r"</?think>", re.I)

# Estado de sesiÛn del modo razonamiento (mutable, sin globals).
_RAZONAMIENTO_ESTADO = {"banner": False, "aviso_dos_pasos": False}


def _extraer_razonamiento(respuesta) -> Optional[str]:
    """Extrae el razonamiento (chain-of-thought) de una respuesta del modelo.

    Acepta un dict (campos ``reasoning``/``thinking``/``chain_of_thought``/
    ``reasoning_content``/``thoughts`` en el nivel superior o anidados como
    ``message`` de Ollama o ``choices[0].message`` de OpenAI) o un str con
    bloques ``<think>‚Ä¶</think>`` (DeepSeek-R1 y otros modelos locales).
    Devuelve el texto del razonamiento o ``None`` si no hay.
    """
    if respuesta is None:
        return None
    if isinstance(respuesta, dict):
        for clave in _CLAVES_RAZONAMIENTO:
            valor = respuesta.get(clave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        for anidado in (respuesta.get("message"),
                        respuesta.get("delta")):
            if isinstance(anidado, dict):
                encontrado = _extraer_razonamiento(anidado)
                if encontrado:
                    return encontrado
        for choice in respuesta.get("choices") or []:
            if isinstance(choice, dict):
                encontrado = _extraer_razonamiento(choice.get("message"))
                if encontrado:
                    return encontrado
        return None
    texto = str(respuesta)
    if not texto:
        return None
    bloques = _RE_THINK.findall(texto)
    if bloques:
        return "\n".join(b.strip() for b in bloques if b.strip()) or None
    return None


def _quitar_razonamiento(texto: str) -> str:
    """Elimina los bloques ``<think>‚Ä¶</think>`` de un texto plano.

    Los modelos que los emiten romperÌan el parseo de JSON del planificador
    y los parches del editor si se dejaran en el texto ˙til. Si el texto no
    contiene razonamiento se devuelve **tal cual** (sin ``strip()``), para no
    alterar el whitespace de las respuestas del proveedor (compatibilidad).
    """
    if not texto:
        return texto or ""
    limpio = _RE_THINK.sub("", texto)
    limpio = _RE_THINK_ABIERTO.sub("", limpio)
    if limpio == texto:
        # Sin razonamiento: respetar el texto original byte a byte.
        return texto
    return limpio.strip()


def _razonamiento_activo(args=None) -> bool:
    """¬øMostrar el razonamiento? Flag ``--mostrar-razonamiento`` o variable
    de entorno ``SNAPCONTEXT_MOSTRAR_RAZONAMIENTO`` (``1``/``true``/``yes``).
    """
    bruto = (os.environ.get("SNAPCONTEXT_MOSTRAR_RAZONAMIENTO")
             or "").strip().lower()
    if bruto in ("1", "true", "yes", "si", "sÌ", "on"):
        return True
    return bool(getattr(args, "mostrar_razonamiento", False))


def _procesar_razonamiento(respuesta, activo: bool = False,
                           avisar: bool = True,
                           titulo: str = "?? Razonamiento del modelo") -> tuple:
    """Muestra (si ``activo``) el razonamiento y devuelve ``(limpio, raz)``.

    Siempre elimina los bloques ``<think>‚Ä¶</think>`` del texto ˙til. Con
    ``avisar=False`` no muestra el mensaje de "sin razonamiento explÌcito"
    (˙til cuando el llamador gestiona el modo de dos pasos).
    """
    raz = _extraer_razonamiento(respuesta)
    limpio = (_quitar_razonamiento(respuesta)
              if isinstance(respuesta, str) else respuesta)
    if activo:
        if raz:
            import ui as _ui
            _ui.mostrar_razonamiento(raz, titulo=titulo)
        elif avisar:
            info("‚Ñπ El modelo no proporcionÛ razonamiento explÌcito.")
    return limpio, raz


def _razonamiento_dos_pasos(tarea: str, proveedor: Optional[str],
                            modelo: Optional[str] = None) -> Optional[str]:
    """Modo de dos pasos: pide primero SOLO el razonamiento de ``tarea``.

    Se usa cuando el modelo no devuelve razonamiento explÌcito y el usuario
    activÛ ``--mostrar-razonamiento``. Devuelve el texto del razonamiento o
    ``None`` si la llamada falla (nunca rompe el flujo principal).
    """
    prompt = ("Por favor, genera tu razonamiento paso a paso para la "
              "siguiente tarea, sin ejecutar ninguna acciÛn.\n\nTarea: "
              + (tarea or "").strip())
    try:
        respuesta = _enviar_al_proveedor(
            proveedor, modelo, [{"role": "user", "content": prompt}])
    except Exception:                   # noqa: BLE001 ‚Äî nunca romper el flujo
        return None
    raz = _extraer_razonamiento(respuesta)
    return raz or (str(respuesta).strip() or None)


# ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
# v6.11.0 ‚Äî PROMPT CACHING
# Mantiene en cachÈ los mensajes del sistema, las herramientas MCP y la memoria
# del proyecto (CLAUDE.md) para los proveedores compatibles (Anthropic/DeepSeek)
# mediante la marca `cache_control: {"type": "ephemeral"}` que entienden sus API.
# Reduce coste y latencia en sesiones largas. No tiene efecto en Gemini, Groq u
# Ollama (se envÌan los mensajes tal cual). Activado por defecto; se desactiva
# con `--no-prompt-caching`, `SNAPCONTEXT_PROMPT_CACHING=0` o
# `prompt_caching: false` en ~/.snapcontext/config.json.
#
# v6.16.0 ‚Äî MÈtricas de cachÈ: en modo `--depurar` se emiten logs con la
# estimaciÛn de tokens cacheados por categorÌa (sistema, herramientas,
# CLAUDE.md/SNAPCONTEXT.md) y el total no cacheado.
# ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
PROMPT_CACHING_DEFECTO = True
ENV_PROMPT_CACHING = "SNAPCONTEXT_PROMPT_CACHING"
# HeurÌstica ligera (no afecta al contenido) para detectar si un mensaje lleva
# definiciones de herramientas MCP o la memoria del proyecto y marcarlo cacheable.
_MARCADORES_CACHE_HERRAMIENTAS = (
    "HERRAMIENTAS", "herramienta", "MCP", "editar_archivo", "ejecutar_comando")
_MARCADORES_CACHE_MEMORIA = ("CLAUDE.md", "SNAPCONTEXT.md")


def _soporta_prompt_caching(proveedor: str) -> bool:
    """¬øEl proveedor soporta marcas ``cache_control`` (v6.11.0)?"""
    return bool(PROVEEDORES.get(proveedor, {}).get("soporta_caching", False))


def _resolver_prompt_caching(explicito: Optional[bool] = None) -> bool:
    """Resuelve si el prompt caching est· activado (v6.11.0).

    Prioridad: flag ``--prompt-caching`` (``explicito``) > entorno
    ``SNAPCONTEXT_PROMPT_CACHING`` > ``config.json -> prompt_caching`` >
    valor por defecto (``PROMPT_CACHING_DEFECTO``, activado). Nunca lanza.
    """
    if explicito is not None:
        return bool(explicito)
    bruto = os.environ.get(ENV_PROMPT_CACHING, "").strip()
    if bruto:
        return bruto.lower() not in ("0", "false", "no", "off")
    try:
        cfg = cargar_configuracion()
        if cfg and "prompt_caching" in cfg:
            valor = cfg["prompt_caching"]
            # v6.31.0: bool (v6.16.0) o seccion dict {"activo": ...}.
            if isinstance(valor, dict):
                return bool(valor.get("activo", True))
            return bool(valor)
    except Exception:                        # noqa: BLE001 ‚Äî nunca romper flujo
        pass
    return PROMPT_CACHING_DEFECTO


# -----------------------------------------------------------------------------
# v6.31.0 ó PROMPT CACHING POR CAPAS (est·tica + semi-est·tica + vol·til)
#
# Con el caching b·sico (v6.16.0) las marcas se colocan sobre mensajes sueltos;
# con las capas, el prompt se estructura en orden estricto (est·tica ?
# semi-est·tica ? vol·til) vÌa :mod:`prompt_cache` para maximizar el prefijo
# idÈntico entre peticiones. Estado global ``_PROMPT_CACHING_CAPAS``: None ?
# se resuelve por flag > entorno > config.json > defecto (activado).
# -----------------------------------------------------------------------------
ENV_PROMPT_CACHING_CAPAS = "SNAPCONTEXT_PROMPT_CACHING_CAPAS"
_PROMPT_CACHING_CAPAS: Optional[bool] = None   # --prompt-caching-capas


def _configurar_prompt_caching_capas(activo: Optional[bool]) -> None:
    """Fija el estado global del Prompt Caching por Capas (v6.31.0).

    ``None`` significa "sin override": se resuelve din·micamente por
    ``_resolver_prompt_caching_capas`` (entorno/config/defecto activado).
    """
    global _PROMPT_CACHING_CAPAS
    _PROMPT_CACHING_CAPAS = None if activo is None else bool(activo)


def _resolver_prompt_caching_capas(explicito: Optional[bool] = None) -> bool:
    """Resuelve si el caching por Capas est· activado (v6.31.0).

    Prioridad: flag ``--prompt-caching-capas`` (``explicito``) > entorno
    ``SNAPCONTEXT_PROMPT_CACHING_CAPAS`` > ``config.json``
    (``prompt_caching.capas_activo``; defecto activado). Nunca lanza.
    """
    if explicito is not None:
        return bool(explicito)
    bruto = os.environ.get(ENV_PROMPT_CACHING_CAPAS, "").strip()
    if bruto:
        return bruto.lower() not in ("0", "false", "no", "off")
    try:
        cfg = cargar_configuracion()
        if cfg:
            seccion = cfg.get("prompt_caching")
            if isinstance(seccion, dict):
                return bool(seccion.get("capas_activo", True))
            if "prompt_caching_capas" in cfg:
                return bool(cfg["prompt_caching_capas"])
    except Exception:                        # noqa: BLE001 ó nunca romper flujo
        pass
    return True


def _capas_caching_activo(explicito: Optional[bool] = None) -> bool:
    """øEst· activo el caching por Capas? (estado global > resoluciÛn)."""
    if _PROMPT_CACHING_CAPAS is not None:
        return _PROMPT_CACHING_CAPAS
    return _resolver_prompt_caching_capas(explicito)


# -----------------------------------------------------------------------------
# v6.32.0 ó Pruning proactivo de contexto (ediciÛn quir˙rgica del historial)
# Podado de resultados extensos de herramientas (logs, salidas, diffs) con
# resumen de una lÌnea (LLM si est· disponible, si no heurÌstica simple).
# Estado global _PRUNING_ACTIVO: None ? se resuelve por flag > config > defecto.
# -----------------------------------------------------------------------------
ENV_PRUNING = "SNAPCONTEXT_PRUNING"
_PRUNING_ACTIVO: Optional[bool] = None   # --prune-context
_PRUNING_UMBRAL: Optional[int] = None    # --prune-umbral


def _configurar_pruning(activo: Optional[bool]) -> None:
    """Fija el estado global del pruning proactivo (v6.32.0).

    ``None`` significa \"sin override\": se resuelve din·micamente por
    ``_resolver_pruning`` (config.json > defecto activado).
    """
    global _PRUNING_ACTIVO
    _PRUNING_ACTIVO = None if activo is None else bool(activo)


def _configurar_umbral_pruning(umbral: Optional[int]) -> None:
    """Fija el umbral global de lÌneas para el pruning (v6.32.0)."""
    global _PRUNING_UMBRAL
    if umbral is None:
        _PRUNING_UMBRAL = None
        return
    try:
        _PRUNING_UMBRAL = max(1, int(umbral))
    except (TypeError, ValueError):
        _PRUNING_UMBRAL = None


def _resolver_pruning(explicito: Optional[bool] = None) -> bool:
    """Resuelve si el pruning proactivo est· activado (v6.32.0).

    Prioridad: flag ``--prune-context`` (``explicito``) > entorno
    ``SNAPCONTEXT_PRUNING`` > ``config.json`` (``pruning.activo``;
    defecto activado). Nunca lanza.
    """
    if explicito is not None:
        return bool(explicito)
    bruto = os.environ.get(ENV_PRUNING, "").strip()
    if bruto:
        return bruto.lower() not in ("0", "false", "no", "off")
    try:
        cfg = cargar_configuracion()
        if cfg:
            seccion = cfg.get("pruning")
            if isinstance(seccion, dict):
                return bool(seccion.get("activo", True))
            if "pruning_activo" in cfg:
                return bool(cfg["pruning_activo"])
    except Exception:
        pass
    return True


def _pruning_activo(explicito: Optional[bool] = None) -> bool:
    """øEst· activo el pruning? (estado global > resoluciÛn)."""
    if _PRUNING_ACTIVO is not None:
        return _PRUNING_ACTIVO
    return _resolver_pruning(explicito)


def _umbral_pruning() -> int:
    """Devuelve el umbral efectivo de lÌneas (flag > config > defecto 10)."""
    if _PRUNING_UMBRAL is not None:
        return _PRUNING_UMBRAL
    try:
        cfg = cargar_configuracion()
        if cfg:
            seccion = cfg.get("pruning")
            if isinstance(seccion, dict):
                umbral = seccion.get("umbral_lineas")
                if umbral is not None:
                    return max(1, int(umbral))
    except Exception:
        pass
    return 10


def podar_si_extenso(
        resultado: dict,
        tipo_herramienta: str = "",
        umbral_lineas: Optional[int] = None) -> dict:
    """Poda un resultado de herramienta si es extenso (v6.32.0).

    Wrapper seguro sobre ``context_pruner.prune_resultado``. Si el mÛdulo
    falta, el pruning est· desactivado o el resultado no es extenso, devuelve
    el resultado original sin cambios. Nunca lanza.
    """
    try:
        import context_pruner as _cp
    except Exception:
        return resultado
    if not _pruning_activo():
        return resultado
    if not isinstance(resultado, dict):
        return resultado
    umbral = umbral_lineas if umbral_lineas is not None else _umbral_pruning()
    try:
        return _cp.prune_resultado(
            resultado, tipo_herramienta=tipo_herramienta,
            umbral_lineas=umbral)
    except Exception:
        return resultado


def _podar_resultados_extensos(mensajes: List[dict]) -> List[dict]:
    """Poda los resultados extensos de herramientas en una lista de mensajes.

    Revisa mensajes de rol ``"tool"`` y ``"assistant"`` que contengan resultados
    extensos (stdout, stderr, contenido, diff) y los reemplaza por un resumen
    de una lÌnea. Devuelve la lista podada.
    """
    try:
        import context_pruner as _cp
    except Exception:
        return mensajes
    if not _pruning_activo():
        return mensajes
    umbral = _umbral_pruning()
    podados: List[dict] = []
    for msg in mensajes:
        if not isinstance(msg, dict):
            podados.append(msg)
            continue
        rol = msg.get("role", "")
        if rol in ("tool", "assistant"):
            contenido = msg.get("content", "")
            if isinstance(contenido, str) and _cp.es_resultado_extenso(
                    {"contenido": contenido}, umbral_lineas=umbral):
                resumen = _cp.resumir_linea(contenido)
                msg_nuevo = dict(msg)
                msg_nuevo["content"] = resumen if resumen else contenido[:200]
                msg_nuevo["_pruned"] = True
                podados.append(msg_nuevo)
                continue
        podados.append(msg)
    return podados


def _estructura_mensajes_por_capas(
        mensajes: List[dict],
        config: Optional[dict] = None) -> List[dict]:
    """Estructura ``mensajes`` en capas inmutables (v6.31.0).

    Clasifica la lista plana (est·tica/semi-est·tica/vol·til) y la ensambla en
    orden estricto con marcas ``cache_control`` en las capas inmutables, vÌa
    :mod:`prompt_cache`. Si el mÛdulo falta o falla, degrada al caching b·sico
    de v6.16.0 (``_aplicar_cache_control``) sin romper el flujo.
    """
    try:
        import prompt_cache as _pc              # noqa: E402
        _capas = _pc.clasificar_mensajes(mensajes, config)
        return _pc.ensamblar_prompt_estructurado(
            None, _capas["estatica"], _capas["semi_estatica"],
            _capas["volatil"], config)
    except Exception:                        # noqa: BLE001 ó degradaciÛn graceful
        return _aplicar_cache_control(mensajes)


def _aplicar_cache_control(mensajes: List[dict]) -> List[dict]:
    """Devuelve una copia de ``mensajes`` con la marca ``cache_control``.

    Solo debe llamarse para proveedores con ``soporta_caching`` (v6.11.0).
    NO muta la lista original ni el contenido de los mensajes: aÒade la marca
    ``cache_control`` a:
      - el mensaje del sistema (el primero de la lista);
      - los mensajes con definiciones de herramientas MCP;
      - los mensajes con la memoria del proyecto (CLAUDE.md / SNAPCONTEXT.md).
    """
    if not mensajes:
        return list(mensajes)
    salida: List[dict] = []
    for i, m in enumerate(mensajes):
        copia = dict(m)
        contenido = str(m.get("content", ""))
        es_sistema = bool(m.get("role") == "system") or i == 0
        tiene_herramientas = any(
            p in contenido for p in _MARCADORES_CACHE_HERRAMIENTAS)
        tiene_memoria = any(
            p in contenido for p in _MARCADORES_CACHE_MEMORIA)
        if es_sistema or tiene_herramientas or tiene_memoria:
            copia["cache_control"] = {"type": "ephemeral"}
        salida.append(copia)
    return salida


# v6.16.0 ‚Äî MÈtricas de Prompt Caching (modo --depurar)
def _contar_tokens(texto: str) -> int:
    """EstimaciÛn aproximada de tokens (v6.16.0).

    Usa la heurÌstica est·ndar 1 token ‚âà 4 caracteres. Suficiente para
    mÈtricas de depuraciÛn; no se usa para limitar el contexto del modelo.
    """
    return max(len(texto) // 4, 0)


def _calcular_metricas_caching(mensajes: List[dict]) -> dict:
    """Calcula mÈtricas de Prompt Caching (v6.16.0).

    Categoriza los mensajes cacheables (sistema, herramientas, memoria) y
    suma tokens estimados por categorÌa. La prioridad de categorÌa es:

      1. ``sistema``  (rol ``system`` o primer mensaje)
      2. ``CLAUDE.md`` / ``SNAPCONTEXT.md`` (contiene la memoria)
      3. ``herramientas`` (contiene definiciones MCP)

    Esto evita duplicar el recuento cuando un mensaje del sistema tambiÈn
    define herramientas.

    Devuelve::

        {"categorias": {cat: tokens, ...},
         "tokens_cacheados": int, "tokens_no_cacheados": int}
    """
    categorias: Dict[str, int] = {}
    tokens_cacheados = 0
    tokens_no_cacheados = 0
    for i, m in enumerate(mensajes):
        contenido = str(m.get("content", ""))
        tokens = _contar_tokens(contenido)
        es_sistema = bool(m.get("role") == "system") or i == 0
        tiene_memoria = any(
            p in contenido for p in _MARCADORES_CACHE_MEMORIA)
        if es_sistema:
            categoria = "sistema"
        elif tiene_memoria:
            categoria = ("CLAUDE.md" if "CLAUDE.md" in contenido
                         else "SNAPCONTEXT.md")
        elif any(p in contenido for p in _MARCADORES_CACHE_HERRAMIENTAS):
            categoria = "herramientas"
        else:
            categoria = None
        if categoria:
            categorias[categoria] = categorias.get(categoria, 0) + tokens
            tokens_cacheados += tokens
        else:
            tokens_no_cacheados += tokens
    return {
        "categorias": categorias,
        "tokens_cacheados": tokens_cacheados,
        "tokens_no_cacheados": tokens_no_cacheados,
    }


def _mensaje_caching_inicio(proveedor: str) -> Optional[str]:
    """Mensaje de usuario al inicio de sesiÛn sobre prompt caching (v6.11.0).

    Devuelve None si el proveedor lo soporta pero el caching est· desactivado
    (no se muestra ning˙n aviso). Nunca lanza.
    """
    try:
        if _soporta_prompt_caching(proveedor):
            if _resolver_prompt_caching(None):
                return f"?? Prompt Caching activado para {proveedor}"
            return None
    except Exception:                        # noqa: BLE001
        return None
    return f"?? Prompt Caching no soportado para {proveedor}"


def _mensaje_capas_caching_inicio(proveedor: str) -> Optional[str]:
    """Mensaje de usuario sobre Prompt Caching por Capas (v6.31.0).

    Devuelve el texto solo si el proveedor soporta caching, el caching b·sico
    est· activado y las Capas tambiÈn; ``None`` en caso contrario (no se
    muestra nada). Nunca lanza. Se muestra COMO LÕNEA ADICIONAL tras el
    mensaje b·sico de v6.16.0 (que no cambia, compatibilidad total).
    """
    try:
        if (_soporta_prompt_caching(proveedor)
                and _resolver_prompt_caching(None)
                and _capas_caching_activo()):
            return ("?? Prompt Caching por Capas activado "
                    "(est·tica + semi-est·tica + vol·til).")
    except Exception:                        # noqa: BLE001
        return None
    return None


def _enviar_al_proveedor(proveedor: str, modelo: Optional[str],
                         mensajes: List[dict],
                         prompt_caching: Optional[bool] = None,
                         categoria: Optional[str] = None) -> str:
    """EnvÌa ``mensajes`` al proveedor, con enrutamiento y fallback (v6.30.0).

    Soporta todos los tipos registrados en PROVEEDORES (gemini, openai-compatible
    y anthropic). Devuelve el texto de respuesta o lanza RuntimeError.

    v6.24.0: si ``categoria`` se indica y el enrutamiento de modelos est·
    activo (``--model-routing``) y el usuario no pasÛ ``--model``/``--provider``
    explÌcitos, se consulta a :mod:`model_router` y la peticiÛn se envÌa al
    modelo configurado para esa categorÌa. Sin configuraciÛn especÌfica en
    ``config.json`` (``model_routing``) no se reenruta nada (compatibilidad).

    v6.30.0 (hÌbrido Local-Nube): con ``--model-fallback`` activo (por
    defecto) y prioridades configuradas (``prioridad_local``/``prioridad_nube``
    de config.json o flags ``--model-prioridad-*``), la complejidad de la
    tarea se detecta con heurÌsticas r·pidas (sin IA) y la peticiÛn recorre
    la cadena local?nube: un fallo de API/timeout pasa al siguiente modelo
    (aviso ``?? Fallo en ...``); los errores de autenticaciÛn abortan de
    inmediato. Si TODOS los modelos fallan se lanza un RuntimeError claro.
    Sin prioridades configuradas el comportamiento es idÈntico a v6.24.0.
    """
    candidatos: List[Tuple[str, Optional[str]]] = [(proveedor, modelo)]
    if categoria and _MODEL_ROUTING_ACTIVO and not _MODELO_EXPLICITO:
        try:
            import model_router as _mr              # noqa: E402
            _cfg = {"model_routing": _configuracion_routing_efectiva()}
            _hibrido = False
            if _MODEL_FALLBACK_ACTIVO:
                _compleja = _mr.es_tarea_compleja(
                    _extraer_consulta_mensajes(mensajes),
                    {"categoria": categoria}, _cfg)
                _orden = [_par for _par in _mr.obtener_orden_prioridad(
                    _compleja, _cfg) if _par[0] in PROVEEDORES]
                if _orden:
                    _p0, _m0 = _orden[0]
                    _m0_ef = _m0 or PROVEEDORES[_p0]["modelo_default"]
                    if _mr.es_proveedor_local(_p0, _cfg):
                        info(f"?? Tarea simple. Usando modelo local: "
                             f"{_p0}/{_m0_ef}")
                    else:
                        info(f"?? Tarea compleja detectada. Usando modelo "
                             f"cloud: {_p0}/{_m0_ef}")
                    candidatos = _orden
                    _hibrido = True
            if not _hibrido:
                # Sin cadena de prioridades ? enrutado por categorÌa (v6.24.0).
                _p2, _m2 = _mr.seleccionar_modelo(
                    categoria,
                    {"model_routing": _cargar_configuracion_routing()})
                if _p2 and _p2 in PROVEEDORES:
                    _m_efectivo = _m2 or PROVEEDORES[_p2]["modelo_default"]
                    info(f"?? Modelo enrutado: {categoria} ? {_p2}/{_m_efectivo}")
                    candidatos = [(_p2, _m2)]
                elif _p2:
                    depurar(f"[model-routing] proveedor desconocido '{_p2}'; "
                            f"se mantiene {proveedor}.")
        except Exception as exc:                     # noqa: BLE001 ó nunca romper
            depurar(f"[model-routing] no se pudo enrutar ({categoria}): {exc}")

    if len(candidatos) <= 1 or not _MODEL_FALLBACK_ACTIVO:
        _p_unico, _m_unico = candidatos[0]
        return _enviar_al_proveedor_unico(_p_unico, _m_unico, mensajes,
                                          prompt_caching)

    # v6.30.0: cadena de fallback entre modelos (local ? nube).
    _intentados: List[str] = []
    _ultimo_error: Optional[BaseException] = None
    for _pos, (_p, _m) in enumerate(candidatos):
        _m_ef = _m or (PROVEEDORES[_p]["modelo_default"]
                       if _p in PROVEEDORES else None)
        try:
            return _enviar_al_proveedor_unico(_p, _m, mensajes, prompt_caching)
        except Exception as exc:                     # noqa: BLE001
            if _es_error_autenticacion(exc):
                raise      # la clave no se arregla cambiando de modelo
            _intentados.append(f"{_p}/{_m_ef or '?'}")
            _ultimo_error = exc
            if _pos + 1 < len(candidatos):
                _p2, _m2 = candidatos[_pos + 1]
                _m2_ef = _m2 or (PROVEEDORES[_p2]["modelo_default"]
                                 if _p2 in PROVEEDORES else None)
                aviso(f"?? Fallo en {_p}/{_m_ef or '?'}. Reintentando con "
                      f"{_p2}/{_m2_ef or '?'}.")
    raise RuntimeError(
        "Todos los modelos de la cadena de fallback fallaron ("
        + ", ".join(_intentados) + ")"
        + (f"; ˙ltimo error: {_ultimo_error}" if _ultimo_error else "")
        + ". Revisa la configuraciÛn (o usa --no-model-fallback para "
          "usar solo el modelo principal)."
    )


def _enviar_al_proveedor_unico(proveedor: str, modelo: Optional[str],
                               mensajes: List[dict],
                               prompt_caching: Optional[bool] = None) -> str:
    """EnvÌa ``mensajes`` ([{"role": ..., "content": ...}, ...]) a UN proveedor.

    Soporta todos los tipos registrados en PROVEEDORES (gemini, openai-compatible
    y anthropic). Devuelve el texto de respuesta o lanza RuntimeError.

    v6.30.0: n˙cleo de un solo intento, extraÌdo de ``_enviar_al_proveedor``
    (que aÒade el enrutamiento por categorÌa y la cadena de fallback hÌbrido
    Local-Nube). Sin enrutamiento ni reintentos: los errores se propagan.
    """
    if proveedor not in PROVEEDORES:
        raise RuntimeError(
            f"Proveedor desconocido '{proveedor}'. "
            f"V·lidos: {', '.join(sorted(PROVEEDORES))}"
        )
    cfg = PROVEEDORES[proveedor]
    modelo = modelo or cfg["modelo_default"]
    tipo = cfg["tipo"]

    # v6.11.0: Prompt Caching. Solo aplica a proveedores con `soporta_caching`
    # (Anthropic, DeepSeek) y cuando est· activado. El resto recibe los mensajes
    # tal cual (sin marcas), manteniendo la compatibilidad total.
    mensajes_finales = mensajes
    if (_soporta_prompt_caching(proveedor)
            and _resolver_prompt_caching(prompt_caching)):
        # v6.31.0: Prompt Caching por Capas (orden estricto estatica ->
        # semi-estatica -> volatil). Con --no-prompt-caching-capas se usa
        # el caching basico de v6.16.0 (marcas sin reestructurar).
        _por_capas = _capas_caching_activo()
        if _por_capas:
            mensajes_finales = _estructura_mensajes_por_capas(mensajes)
        else:
            mensajes_finales = _aplicar_cache_control(mensajes)
        # v6.16.0: mÈtricas de cachÈ en modo --depurar
        if DEPURAR:
            _metricas = _calcular_metricas_caching(mensajes)
            _cats = ", ".join(
                f"{k} ({v} tokens)"
                for k, v in _metricas["categorias"].items())
            depurar(f"‚Ñπ Prompt Caching activado ({proveedor}): {_cats}")
            if _por_capas:
                # v6.31.0: metricas por capa (estatica/semi/volatil).
                try:
                    import prompt_cache as _pc          # noqa: E402
                    _tokens_capa = _pc.metricas_capas(mensajes_finales)
                    depurar(
                        "?? Capa est·tica: "
                        f"{_tokens_capa['estatica']} tokens, semi-est·tica: "
                        f"{_tokens_capa['semi_estatica']} tokens, vol·til: "
                        f"{_tokens_capa['volatil']} tokens.")
                except Exception:            # noqa: BLE001
                    pass
    # v6.32.0: Pruning proactivo de contexto ó podar resultados extensos de
    # herramientas (logs, salidas, diffs) con resumen de una lÌnea. Se aplica
    # despuÈs del caching para no interferir con las marcas cache_control.
    if _pruning_activo():
        try:
            _msg_antes = str(mensajes_finales)
            mensajes_finales = _podar_resultados_extensos(mensajes_finales)
            if DEPURAR and _msg_antes != str(mensajes_finales):
                depurar("?? Podando contexto (resultados extensos ? resumen 1 lÌnea)")
        except Exception:
            pass
    if tipo == "gemini":
        if _importar_genai() is None:
            raise RuntimeError(MENSAJE_GENAI_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(MENSAJE_API_KEY)
        genai.configure(api_key=api_key)
        generador = genai.GenerativeModel(model_name=modelo)
        # Gemini distingue user/model; convertimos "assistant" ‚Üí "model".
        contenidos = [
            {"role": "user" if m["role"] != "assistant" else "model",
             "parts": [m["content"]]}
            for m in mensajes
        ]
        respuesta = generador.generate_content(contenidos)
        return respuesta.text or ""

    if tipo == "anthropic":
        if _importar_anthropic() is None:
            raise RuntimeError(MENSAJE_ANTHROPIC_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))
        cliente = anthropic.Anthropic(api_key=api_key)
        respuesta = cliente.messages.create(
            model=modelo, max_tokens=2048, messages=mensajes_finales,
        )
        return "".join(
            bloque.text for bloque in respuesta.content
            if getattr(bloque, "type", None) == "text"
        )

    # v6.34.0: tipo "xpu" ó inferencia local en GPUs Intel Arc vÌa IPEX.
    if tipo == "xpu":
        try:
            import backend_xpu as _xpu
        except ImportError as exc:
            raise RuntimeError(
                f"Para usar XPU se necesita backend_xpu y sus dependencias: {exc}"
            ) from exc
        if not _xpu.xpu_disponible():
            raise RuntimeError(
                "Intel XPU no detectado. Revisa la instalaciÛn de IPEX y drivers."
            )
        _modelo_xpu = modelo or PROVEEDORES["xpu"]["modelo_default"]
        _cfg_xpu = cargar_configuracion().get("xpu", {})
        _max_tokens = int(_cfg_xpu.get("max_tokens", 500))
        _temperature = float(_cfg_xpu.get("temperature", 0.7))
        # Los flags CLI tienen prioridad sobre la configuraciÛn.
        if getattr(args, "xpu_model", None):
            _modelo_xpu = args.xpu_model
        if getattr(args, "xpu_max_tokens", None) is not None:
            _max_tokens = args.xpu_max_tokens
        if getattr(args, "xpu_temperature", None) is not None:
            _temperature = args.xpu_temperature
        _motor = _xpu.cargar_modelo_xpu(
            _modelo_xpu,
            config={"xpu": {"max_tokens": _max_tokens, "temperature": _temperature}},
        )
        # Concatenar los mensajes en un solo prompt para el modelo local.
        _prompt_xpu = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in mensajes_finales
        )
        return _motor.generate(_prompt_xpu)

    # Tipo "openai": Groq, DeepSeek y Ollama (API compatible).
    if _importar_openai() is None:
        raise RuntimeError(MENSAJE_OPENAI_FALTANTE)
    api_key = os.environ.get(cfg["clave_env"], "").strip()
    if cfg["requiere_clave"] and not api_key:
        raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))
    cliente = openai.OpenAI(
        api_key=api_key or "ollama-local",
        base_url=_resolver_url_openai(cfg), timeout=120,
    )
    respuesta = cliente.chat.completions.create(
        model=modelo, messages=mensajes_finales, temperature=0.4,
    )
    return respuesta.choices[0].message.content or ""


def _ejecutar_chat(proveedor: Optional[str] = None,
                   modelo: Optional[str] = None,
                   prompt_caching: Optional[bool] = None) -> int:
    """REPL interactivo (`snapcontext --chat`). Devuelve cÛdigo de salida.

    Mantiene la conversaciÛn en memoria (`historial_chat`) y da acceso a los
    comandos /salir, /archivos, /limpiar, /seleccion, /provider, /historial y
    /ayuda. Cualquier otro texto se envÌa al proveedor actual.
    """
    preferencias = cargar_configuracion()
    # v5.4.1: resoluciÛn con prioridad clara.
    #   1) Flags CLI (--provider / --model) ‚Äî el flag --model ya incorpora
    #      SNAPCONTEXT_MODELO como valor por defecto (MODELO_DEFECTO).
    #   2) Variables de entorno SNAPCONTEXT_PROVIDER / SNAPCONTEXT_MODELO.
    #   3) ConfiguraciÛn guardada en ~/.snapcontext/config.json.
    #   4) Fallback final (con aviso).
    # Antes se ignoraba tanto el modelo guardado en config.json como los
    # flags, por lo que Ollama caÌa siempre a 'llama3.2' (404 si el usuario
    # tenÌa otro modelo descargado, p. ej. qwen3.5:9b).
    proveedor_flag = proveedor or os.environ.get("SNAPCONTEXT_PROVIDER") or None
    modelo_flag = modelo or os.environ.get("SNAPCONTEXT_MODELO") or None
    proveedor = (proveedor_flag
                 or preferencias.get("provider")
                 or PROVEEDOR_DEFECTO)
    # El modelo guardado en config.json solo aplica si el proveedor tambiÈn
    # viene de la configuraciÛn (evita mezclar modelos entre proveedores).
    modelo = (modelo_flag
              or (None if proveedor_flag else preferencias.get("model"))
              or None)
    if not proveedor_flag and not preferencias.get("provider"):
        aviso("No hay proveedor configurado (flags, entorno ni config.json); "
              f"usando el fallback '{PROVEEDOR_DEFECTO}' "
              f"({PROVEEDORES[PROVEEDOR_DEFECTO]['modelo_default']}). "
              "Config˙ralo con 'snapcontext --init'.")

    _emitir(sys.stdout, _pintar(
        f"üí¨ SnapContext Chat (v{VERSION}) ‚Äî Escribe tu tarea, "
        "/salir para terminar", _CYAN))
    info(f"Proveedor actual: {proveedor} "
         f"({modelo or PROVEEDORES[proveedor]['modelo_default']}). "
         "Escribe /ayuda para ver los comandos.")
    # v6.11.0: informa del estado del Prompt Caching al inicio de la sesiÛn.
    _mensaje_caching = _mensaje_caching_inicio(proveedor)
    if _mensaje_caching:
        info(_mensaje_caching)
    # v6.31.0: informa del Prompt Caching por Capas al inicio de la sesiÛn.
    _mensaje_capas = _mensaje_capas_caching_inicio(proveedor)
    if _mensaje_capas:
        info(_mensaje_capas)

    historial_chat: List[dict] = []       # conversaciÛn de esta sesiÛn
    contexto_archivos: List[str] = []     # selecciÛn actual (/seleccion)
    hilos: List[threading.Thread] = []    # comandos de agente en 2¬∫ plano

    def _esperar_hilos(limite: float = 120.0) -> None:
        """Espera (con tope) a que terminen los comandos lanzados en hilos."""
        for h in hilos:
            h.join(timeout=limite)
        hilos.clear()

    while True:
        try:
            linea = input(_pintar("> ", _CYAN)).strip()
        except (EOFError, KeyboardInterrupt):
            _emitir(sys.stdout, "")
            info("Esperando comandos en curso (Ctrl+C para forzar)...")
            _esperar_hilos()
            info("Chat terminado.")
            return 0

        # ---- comandos internos -------------------------------------------
        if linea in ("/salir", "/exit", "/quit"):
            info("Esperando comandos en curso...")
            _esperar_hilos()
            info("Chat terminado.")
            return 0
        if not linea:
            continue

        if linea == "/ayuda":
            _emitir(sys.stdout, AYUDA_CHAT)
            continue

        # ---- asesor proactivo (v3.5.0) ------------------------------------
        if linea in ("/asesor", "/sugerir"):
            info("?? Analizando el proyecto...")
            sugerencias_chat = _asesor_analizar(".")
            _asesor_mostrar(sugerencias_chat)
            continue

        # ---- seguridad / rendimiento (v4.2.0) ------------------------------
        if linea == "/seguridad":
            info("?? Analizando vulnerabilidades del proyecto...")
            _asesor_mostrar(_analizar_seguridad("."))
            continue
        if linea == "/rendimiento":
            info("‚ö° Analizando rendimiento del proyecto...")
            _asesor_mostrar(_analizar_rendimiento("."))
            continue

        # ---- plugins (v4.0.0) ----------------------------------------------
        if linea == "/plugin" or linea.startswith("/plugin "):
            argumento = linea[len("/plugin"):].strip()
            if not argumento:
                _plugin_mostrar()
                continue
            partes = shlex.split(argumento)
            objetivo = partes[0]
            if "." not in objetivo:
                aviso("Uso: /plugin <plugin>.<herramienta> ['{args json}'] "
                      "‚Äî o /plugin a secas para listar.")
                continue
            nombre_plugin, nombre_herramienta = objetivo.split(".", 1)
            instalados_chat = _plugins_instalados()
            if nombre_plugin not in instalados_chat:
                aviso(f"Plugin '{nombre_plugin}' no encontrado.")
                continue
            if not any(
                    (h.get("nombre") or "").strip() == nombre_herramienta
                    for h in instalados_chat[nombre_plugin].get(
                        "herramientas", [])):
                aviso(f"El plugin '{nombre_plugin}' no expone la herramienta "
                      f"'{nombre_herramienta}'.")
                continue
            argumentos_chat: dict = {}
            if len(partes) > 1:
                try:
                    cargado = json.loads(" ".join(partes[1:]))
                    if isinstance(cargado, dict):
                        argumentos_chat = cargado
                except (ValueError, json.JSONDecodeError):
                    argumentos_chat = {"consulta": " ".join(partes[1:])}
            llamada_chat = _ejecutar_herramienta_mcp(nombre_herramienta,
                                                     argumentos_chat)
            _emitir(sys.stdout, _formatear_resultado_mcp(llamada_chat))
            continue

        if linea == "/limpiar":
            historial_chat = []
            exito("Historial de conversaciÛn limpiado.")
            continue

        if linea == "/archivos" or linea == "/context":
            if MEMORIA_PROYECTO:
                exito("‚îÄ‚îÄ Memoria del proyecto (CLAUDE.md) ‚îÄ‚îÄ")
                for linea_memoria in MEMORIA_PROYECTO.splitlines()[:60]:
                    _emitir(sys.stdout, "  " + linea_memoria)
            if not contexto_archivos and not MEMORIA_PROYECTO:
                aviso("Sin memoria de proyecto ni archivos en contexto "
                      "(usa --init-claude o /seleccion).")
            elif contexto_archivos:
                exito(f"Archivos en contexto ({len(contexto_archivos)}):")
                for archivo in contexto_archivos:
                    _emitir(sys.stdout, "   ‚Ä¢ " + archivo)
            continue

        # ---- memoria del proyecto (v0.15.0) -------------------------------
        if linea == "/claude":
            if not MEMORIA_PROYECTO:
                aviso("No hay CLAUDE.md ni SNAPCONTEXT.md en este proyecto. "
                      "CrÈalos con: snapcontext --init-claude")
            else:
                exito(f"‚îÄ‚îÄ {_buscar_claude_md().name} ‚îÄ‚îÄ")
                _emitir(sys.stdout, MEMORIA_PROYECTO)
            continue

        if linea == "/historial":
            _mostrar_historial()
            continue

        if linea == "/save":
            _cmd_chat_save(historial_chat)
            continue

        # ---- b˙squeda sem·ntica (v1.1.0; alias /buscar desde v1.4.0) -------
        if linea.startswith("/search ") or linea.startswith("/buscar"):
            prefijo_busqueda = ("/search" if linea.startswith("/search")
                                else "/buscar")
            consulta_busqueda = linea[len(prefijo_busqueda):].strip()
            if not consulta_busqueda:
                aviso("Uso: /search <consulta>")
                continue
            try:
                resultados = _buscar_semanticamente(consulta_busqueda,
                                                    directorio=".")
            except RuntimeError as exc:
                error(str(exc))
                continue
            if not resultados:
                aviso("Sin resultados sem·nticos.")
                continue
            exito(f"Resultados sem·nticos para '{consulta_busqueda}':")
            for resultado in resultados[:10]:
                _emitir(sys.stdout, _pintar(
                    f"   ‚Ä¢ {resultado['archivo']}:{resultado['linea_inicio']} "
                    f"(similitud {resultado['similitud']})", _VERDE))
            continue

        # ---- grafo y dependencias (v1.4.0) --------------------------------
        if linea == "/grafo":
            grafo_chat = _grafo_dependencias(".")
            nodos_chat = grafo_chat.get("nodos", [])
            enlaces_chat = grafo_chat.get("enlaces", [])
            if not nodos_chat:
                aviso("Sin archivos de cÛdigo detectados para construir el grafo.")
                continue
            exito(f"Grafo de dependencias ({len(nodos_chat)} nodo(s), "
                  f"{len(enlaces_chat)} enlace(s)):")
            salientes: dict = {}
            for enlace in enlaces_chat:
                salientes.setdefault(enlace["origen"], []).append(
                    enlace["destino"])
            for nodo in nodos_chat:
                nodo_id = nodo["id"]
                _emitir(sys.stdout, _pintar(f"  ‚ñ∏ {nodo_id}", _CYAN))
                for destino in salientes.get(nodo_id, []):
                    _emitir(sys.stdout, f"      ‚îÄ‚îÄ‚ñ∂ {destino}")
                if nodo_id not in salientes:
                    _emitir(sys.stdout, "      (sin dependencias locales)")
            continue

        if linea.startswith("/dependencias"):
            partes_dep = linea.split(maxsplit=1)
            archivo_objetivo = partes_dep[1].strip() if len(partes_dep) > 1 else ""
            if not archivo_objetivo:
                aviso("Uso: /dependencias <archivo>")
                continue
            grafo_dep = _grafo_dependencias(".")
            directas, inversas = [], []
            for enlace in grafo_dep.get("enlaces", []):
                if enlace["origen"] == archivo_objetivo:
                    directas.append(enlace["destino"])
                if enlace["destino"] == archivo_objetivo:
                    inversas.append(enlace["origen"])
            if not directas and not inversas:
                aviso(f"Sin dependencias detectadas para '{archivo_objetivo}' "
                      f"(¬øexiste el archivo y tiene imports?).")
                continue
            exito(f"Dependencias de {archivo_objetivo}:")
            _emitir(sys.stdout, f"  Importa de ({len(directas)}):")
            for destino in directas:
                _emitir(sys.stdout, _pintar(f"    ‚îÄ‚îÄ‚ñ∂ {destino}", _VERDE))
            if not directas:
                _emitir(sys.stdout, "    (ninguna)")
            _emitir(sys.stdout, f"  Importado por ({len(inversas)}):")
            for origen in inversas:
                _emitir(sys.stdout, _pintar(f"    ‚óÄ‚îÄ‚îÄ {origen}", _AMARILLO))
            if not inversas:
                _emitir(sys.stdout, "    (ninguno)")
            continue

        # ---- herramientas MCP (v0.14.0) -----------------------------------
        if linea == "/tools":
            herramientas = _cargar_herramientas_mcp()
            exito(f"Herramientas MCP disponibles ({len(herramientas)}):")
            for nombre in sorted(herramientas):
                cfg = herramientas[nombre]
                permiso = "?? requiere permiso" if cfg.get("requiere_permiso") \
                    else "lectura"
                _emitir(sys.stdout,
                        f"   ‚Ä¢ {nombre} ‚Äî {cfg['descripcion']} [{permiso}]")
            continue

        if linea.startswith("/tool "):
            resto = linea[len("/tool "):].strip()
            partes = resto.split(maxsplit=1)
            nombre = partes[0]
            argumentos: dict = {}
            if len(partes) > 1:
                bruto = partes[1].strip()
                try:
                    cargado = json.loads(bruto)
                    if not isinstance(cargado, dict):
                        raise ValueError
                    argumentos = cargado
                except (ValueError, json.JSONDecodeError):
                    # Formato posicional simple por herramienta.
                    if nombre == "grep":
                        argumentos = {"patron": bruto.strip('"').strip("'")}
                    elif nombre == "read_file":
                        trozos = shlex.split(bruto)
                        argumentos = {"ruta": trozos[0] if trozos else ""}
                        if len(trozos) > 1:
                            argumentos["linea_inicio"] = trozos[1]
                        if len(trozos) > 2:
                            argumentos["linea_fin"] = trozos[2]
                    elif nombre == "list_files":
                        argumentos = {"directorio": bruto.strip('"').strip("'")}
                    elif nombre == "ast":
                        argumentos = {"ruta": bruto.strip('"').strip("'")}
                    elif nombre == "git_diff":
                        argumentos = {"archivo": bruto.strip('"').strip("'")}
                    elif nombre == "execute_command":
                        argumentos = {"comando": bruto}
                    else:
                        argumentos = {"comando": bruto}
            info(f"?? Ejecutando herramienta MCP '{nombre}'...")
            llamada = _ejecutar_herramienta_mcp(nombre, argumentos,
                                                confirmar=CONFIRMAR_ACCIONES)
            texto = _formatear_resultado_mcp(llamada)
            _emitir(sys.stdout, _pintar(texto, _VERDE if llamada["ok"]
                                        else _AMARILLO))
            if llamada["ok"]:
                exito("Herramienta completada.")
            else:
                error("La herramienta devolviÛ un error.")
            # El resultado queda en el contexto de la conversaciÛn.
            historial_chat.append({
                "role": "user",
                "content": f"[herramienta {nombre}] "
                           + _formatear_resultado_mcp(llamada)[:2000]})
            continue

        # ---- comandos de agente ------------------------------------------
        if linea.startswith("/run "):
            hilos.append(_lanzar_en_hilo(
                _cmd_chat_run, linea[len("/run "):].strip()))
            continue

        if linea.startswith("/read "):
            _cmd_chat_read(linea[len("/read "):].strip().strip('"').strip("'"))
            continue

        if linea.startswith("/explore "):
            hilos.append(_lanzar_en_hilo(
                _cmd_chat_explore, linea[len("/explore "):].strip()))
            continue

        if linea.startswith("/edit "):
            _cmd_chat_edit(linea[len("/edit "):].strip().strip('"').strip("'"),
                           confirmar=CONFIRMAR_ACCIONES)
            continue

        if linea == "/fix" or linea.startswith("/fix ") \
                or linea == "/review" or linea.startswith("/review ") \
                or linea == "/server" or linea.startswith("/server "):
            _alias = linea[1:].split(maxsplit=1)[0]
            hilos.append(_lanzar_en_hilo(
                _cmd_chat_alias, _alias,
                linea[len(f"/{_alias}"):].strip()))
            continue

        if linea.startswith("/provider"):
            partes = linea.split(maxsplit=1)
            nuevo = partes[1].strip().lower() if len(partes) > 1 else ""
            if nuevo not in PROVEEDORES:
                aviso(f"Proveedores v·lidos: {', '.join(sorted(PROVEEDORES))}")
                continue
            proveedor = nuevo
            modelo = None                       # vuelve al modelo por defecto
            exito(f"Proveedor cambiado a {PROVEEDORES[proveedor]['nombre']} "
                  f"({PROVEEDORES[proveedor]['modelo_default']}).")
            continue

        if linea.startswith("/seleccion"):
            consulta = linea[len("/seleccion"):].strip()
            if not consulta:
                aviso("Uso: /seleccion <consulta>")
                continue
            try:
                candidatos = escanear_repositorio(
                    consulta, directorio=".", carpetas=list(CARPETAS_DEFECTO))
                if not candidatos:
                    aviso("No se encontraron candidatos en este repositorio.")
                    continue
                contexto_archivos = seleccionar_archivos(
                    consulta, candidatos,
                    proveedor=proveedor, modelo=modelo,
                )
                if contexto_archivos:
                    exito(f"SelecciÛn ({len(contexto_archivos)}):")
                    for archivo in contexto_archivos:
                        _emitir(sys.stdout, "   ‚Ä¢ " + archivo)
                else:
                    aviso("El proveedor no devolviÛ archivos.")
            except RuntimeError as exc:
                error(str(exc))
            continue

        # ---- mensaje normal ‚Üí proveedor de IA ----------------------------
        historial_chat.append({"role": "user", "content": linea})
        try:
            # MCP autom·tico (v0.14.0): si el mensaje parece una pregunta de
            # exploraciÛn, se recopila contexto con herramientas de solo
            # lectura y se aÒade al turno del usuario.
            contexto_mcp = _contexto_automatico_mcp(linea)
        except Exception as exc:                # nunca romper el chat
            depurar(f"[mcp] contexto autom·tico fallÛ: {exc}")
            contexto_mcp = ""
        if contexto_mcp:
            info("?? Contexto MCP aÒadido a la consulta "
                 "(herramientas de solo lectura).")
            historial_chat[-1]["content"] += (
                "\n\n[Contexto obtenido con herramientas MCP]\n"
                + contexto_mcp[:3000])
        if MEMORIA_PROYECTO:
            # Memoria del proyecto (v0.15.0): contexto persistente CLAUDE.md.
            historial_chat[-1]["content"] = (
                "[Memoria del proyecto]\n" + MEMORIA_PROYECTO[:2000]
                + "\n\n" + historial_chat[-1]["content"])
        try:
            # Se envÌan solo los ˙ltimos 20 turnos para no crecer sin lÌmite.
            respuesta = _enviar_al_proveedor(
                proveedor, modelo, historial_chat[-20:],
                prompt_caching=prompt_caching,
            )
        except RuntimeError as exc:
            error(str(exc))
            historial_chat.pop()            # no conservar el turno fallido
            continue
        except Exception as exc:            # errores de red/API no controlados
            error(f"Error hablando con {PROVEEDORES[proveedor]['nombre']}: {exc}")
            historial_chat.pop()
            continue
        _raz_activo = _razonamiento_activo()
        if _raz_activo and not _RAZONAMIENTO_ESTADO.get("banner"):
            _RAZONAMIENTO_ESTADO["banner"] = True
            info("?? Mostrando razonamiento del modelo "
                 "(--mostrar-razonamiento)")
        respuesta_limpia, raz = _procesar_razonamiento(respuesta,
                                                       activo=False)
        if _raz_activo and raz:
            import ui as _ui
            _ui.mostrar_razonamiento(raz)
        elif _raz_activo:
            # Modo de dos pasos (v6.2.0): el modelo no etiqueta su
            # razonamiento ‚Üí se pide explÌcitamente antes de la respuesta.
            if not _RAZONAMIENTO_ESTADO.get("aviso_dos_pasos"):
                _RAZONAMIENTO_ESTADO["aviso_dos_pasos"] = True
                aviso("‚ö† El modelo no devuelve razonamiento explÌcito: se "
                      "usar· el modo de dos pasos (duplica las llamadas y "
                      "puede ralentizar modelos lentos).")
            raz2 = _razonamiento_dos_pasos(linea, proveedor, modelo)
            if raz2:
                import ui as _ui
                _ui.mostrar_razonamiento(raz2)
            else:
                info("‚Ñπ El modelo no proporcionÛ razonamiento explÌcito.")
        historial_chat.append({"role": "assistant",
                               "content": respuesta_limpia})
        _emitir(sys.stdout, _pintar(respuesta_limpia, _VERDE))
        _emitir(sys.stdout, "")


# ---------------------------------------------------------------------------
# Comandos de agente del chat (--chat) ‚Äî v0.10.x
# ---------------------------------------------------------------------------
def _lanzar_en_hilo(fn, *args) -> threading.Thread:
    """Ejecuta ``fn(*args)`` en un hilo daemon para no bloquear el REPL."""
    hilo = threading.Thread(target=fn, args=args, daemon=True)
    hilo.start()
    return hilo


def _cmd_chat_run(comando: str, directorio: str = ".",
                  confirmar: Optional[bool] = None) -> None:
    """`/run <comando>`: ejecuta un comando de shell y muestra su salida."""
    if not comando:
        aviso("Uso: /run <comando>")
        return
    if not _confirmar_accion(comando, tipo="ejecutar",
                             detalles=f"directorio: {directorio}",
                             confirmar=confirmar):
        return
    info(f"$ {comando}")
    codigo, stdout, stderr = _ejecutar_comando(comando, directorio)
    if stdout.strip():
        _emitir(sys.stdout, _pintar(stdout.rstrip(), _VERDE))
    if stderr.strip():
        _emitir(sys.stdout, _pintar(stderr.rstrip(), _AMARILLO))
    if codigo == 0:
        exito("Comando terminado correctamente.")
    else:
        error(f"Comando terminado con cÛdigo {codigo}.")


def _cmd_chat_read(archivo: str) -> None:
    """`/read <archivo>`: muestra el contenido de un archivo en el chat."""
    if not archivo:
        aviso("Uso: /read <archivo>")
        return
    contenido = _leer_archivo(archivo)
    if contenido is None:
        error(f"No se pudo leer '{archivo}'.")
        return
    lineas = contenido.splitlines()
    exito(f"‚îÄ‚îÄ {archivo} ({len(lineas)} lÌnea(s)) " + "‚îÄ" * 20)
    # Se muestran como m·ximo 400 lÌneas para no saturar la consola.
    for linea in lineas[:400]:
        _emitir(sys.stdout, "  " + linea)
    if len(lineas) > 400:
        aviso(f"(salida recortada: {len(lineas) - 400} lÌnea(s) m·s)")


def _herramienta_busqueda() -> Optional[str]:
    """Devuelve el buscador disponible: ripgrep ('rg'), 'grep' o 'findstr'."""
    for herramienta in ("rg", "grep", "findstr"):
        if shutil.which(herramienta):
            return herramienta
    return None


def _cmd_chat_explore(tema: str, directorio: str = ".") -> None:
    """`/explore <tema>`: busca ``tema`` en el cÛdigo del repositorio.

    Usa ripgrep si est· instalado; si no, `grep` en Linux/macOS o `findstr`
    en Windows. Recursivo e insensible a may˙sculas.
    """
    if not tema:
        aviso("Uso: /explore <tema>")
        return
    herramienta = _herramienta_busqueda()
    if herramienta is None:
        error("No se encontrÛ ning˙n buscador (rg, grep ni findstr) en el PATH.")
        return
    info(f"Explorando '{tema}' con {herramienta}...")
    if herramienta == "rg":
        comando = f'rg -n -i --max-count 5 "{tema}"'
    elif herramienta == "grep":
        comando = f'grep -rn -i -m 5 "{tema}" .'
    else:  # findstr (Windows): /s recursivo, /i sin may˙sculas
        comando = f'findstr /s /n /i "{tema}" *.py *.dart *.js *.ts *.go *.rs'
    codigo, stdout, stderr = _ejecutar_comando(comando, directorio, timeout=60)
    salida = (stdout or "").strip()
    if salida:
        lineas = salida.splitlines()
        exito(f"{len(lineas)} coincidencia(s):")
        for linea in lineas[:50]:
            _emitir(sys.stdout, "  " + linea)
        if len(lineas) > 50:
            aviso(f"(mostradas 50 de {len(lineas)} coincidencias)")
    elif codigo == 0:
        aviso("Sin coincidencias.")
    else:
        error(f"La b˙squeda fallÛ (cÛdigo {codigo}): "
              f"{stderr.strip() or 'sin detalle'}")


def _cmd_chat_alias(alias: str, mensaje: str) -> int:
    """Ejecuta los alias fix/review/server desde el chat.

    Reutiliza exactamente la lÛgica existente: convierte el alias con
    ``_preparar_argv_aliases``, parsea los argumentos con ``crear_parser`` y
    llama a ``flujo_principal`` (que adem·s registra la tarea en el historial).
    Devuelve el cÛdigo de salida del pipeline.
    """
    if not mensaje:
        aviso(f"Uso: /{alias} <mensaje>")
        return 1
    argv = _preparar_argv_aliases([alias] + shlex.split(mensaje))
    args = crear_parser().parse_args(argv)
    args.depurar = DEPURAR
    info(f"Ejecutando alias '{alias}' con: {mensaje}")
    return flujo_principal(args)


def _cmd_chat_edit(archivo: str, confirmar: Optional[bool] = None) -> None:
    """`/edit <archivo>`: abre el archivo en VSCode, $EDITOR o nano/notepad."""
    if not archivo:
        aviso("Uso: /edit <archivo>")
        return
    camino = Path(archivo).expanduser()
    if not camino.is_absolute():
        camino = Path.cwd() / camino
    if not camino.is_file():
        error(f"El archivo no existe: {camino}")
        return
    # Solo lectura en el chat, pero se abre un programa externo: se confirma.
    if not _confirmar_accion(f"abrir '{camino}' en el editor", tipo="editar",
                             confirmar=confirmar):
        return
    editor_cmd = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
    candidatos = ([shlex.split(editor_cmd)] if editor_cmd else [])
    candidatos += [["code"], ["nano"] if os.name != "nt" else ["notepad"]]
    for cmd in candidatos:
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd + [str(camino)])
                exito(f"Abriendo '{camino}' con {cmd[0]}...")
            except OSError as exc:
                error(f"No se pudo abrir el editor: {exc}")
            return
    error("No se encontrÛ ning˙n editor (code/nano/notepad/$EDITOR).")


def _cmd_chat_save(historial_chat: List[dict]) -> None:
    """`/save`: guarda un resumen de la sesiÛn actual en historial.json."""
    if not historial_chat:
        aviso("No hay conversaciÛn que guardar.")
        return
    turnos_usuario = [m["content"] for m in historial_chat
                      if m.get("role") == "user"]
    entrada = {
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "consulta": " | ".join(t[:120] for t in turnos_usuario),
        "archivos": [],
        "resultado": "Èxito",
        "duracion": round(len(turnos_usuario), 2),   # n¬∫ de turnos del usuario
        "tipo": "sesion-chat",
        "mensajes": len(historial_chat),
    }
    if _guardar_historial(entrada):
        exito(f"SesiÛn guardada en {HISTORIAL_PATH} "
              f"({entrada['mensajes']} mensajes).")


# ---------------------------------------------------------------------------
# Planificador de tareas (--plan) ‚Äî v0.12.0
# ---------------------------------------------------------------------------
PROMPT_PLAN = (
    "Eres un planificador de tareas de desarrollo. DescompÛn la siguiente "
    "tarea en pasos CONCRETOS y AT√ìMICOS (m·ximo 8).\n\n"
    "TAREA: {consulta}\n\n"
    "Devuelve SOLO un objeto JSON con esta forma exacta (sin explicaciones):\n"
    '{{"pasos": [{{\n'
    '  "descripcion": "quÈ hace este paso",\n'
    '  "accion": "editar" | "ejecutar" | "consultar" | "mcp" | "asesor",\n'
    '  "archivos": ["ruta/relativa.py"],   // solo para accion "editar"\n'
    '  "comando": "comando shell",         // solo para accion "ejecutar"\n'
    '  "herramienta": "grep|read_file|list_files|ast|git_status|git_diff|\n'
    '                 "execute_command|...",   // solo para accion "mcp"\n'
    '  "args": {{"patron": "..."}},        // solo para accion "mcp"\n'
    '  "variable": "mi_resultado",        // opcional (mcp): nombre del resultado\n'
    "}}]}}\n\n"
    "Significado de las acciones:\n"
    ' - "editar": modificar cÛdigo (Aider). Indica los archivos implicados.\n'
    ' - "ejecutar": lanzar un comando (tests, build, migraciones...).\n'
    ' - "consultar": aclarar una duda sobre el proyecto sin cambiar nada.\n'
    ' - "mcp": ejecutar una herramienta MCP (campos "herramienta" y "args") y\n'
    '   usar su resultado en pasos posteriores con {{{{resultado}}}} o {{{{mi_variable}}}}.\n'
    "Condiciones admitidas (el paso se salta si son falsas):\n"
    ' - archivo_existe(ruta), archivo_contiene(ruta, texto), comando_exito(cmd),\n'
    "   variable_existe(nombre) o comparaciones como\n"
    "   \"pasos[0].resultado == 'ok'\"  ¬∑  \"resultados.mi_variable != ''\".\n"
)

ACCIONES_VALIDAS = {"editar", "ejecutar", "consultar", "mcp", "asesor",
                    "seguridad", "rendimiento"}


def _normalizar_pasos(datos) -> List[dict]:
    """Normaliza la respuesta del proveedor a una lista de pasos v·lidos.

    Acepta ``{"pasos": [...]}``, una lista directa o un ˙nico paso suelto.
    Descarta pasos mal formados (sin descripciÛn o con acciÛn desconocida).
    """
    if isinstance(datos, dict):
        datos = datos.get("pasos", [])
    if isinstance(datos, dict):
        datos = [datos]
    if not isinstance(datos, list):
        return []
    pasos: List[dict] = []
    for crudo in datos:
        if not isinstance(crudo, dict):
            continue
        descripcion = str(crudo.get("descripcion") or "").strip()
        accion = str(crudo.get("accion") or "").strip().lower()
        if not descripcion or accion not in ACCIONES_VALIDAS:
            continue
        archivos = crudo.get("archivos") or []
        if not isinstance(archivos, list):
            archivos = []
        paso = {
            "descripcion": descripcion,
            "accion": accion,
            "archivos": [str(a) for a in archivos if str(a).strip()],
            "comando": str(crudo.get("comando") or "").strip(),
            # v1.3.0: dependencias entre pasos y ejecuciÛn condicional.
            "dependencias": _normalizar_dependencias(crudo.get("dependencias")),
            "condicion": str(crudo.get("condicion") or "").strip(),
            # v2.3.0: pasos de tipo "mcp".
            "herramienta": str(crudo.get("herramienta") or "").strip(),
            "args": crudo.get("args") if isinstance(
                crudo.get("args"), dict) else {},
            "variable": str(crudo.get("variable") or "").strip(),
        }
        pasos.append(paso)
    return pasos


def _normalizar_dependencias(valor) -> List[int]:
    """Convierte el campo ``dependencias`` de un paso en una lista de Ìndices.

    Acepta lista de enteros/strings numÈricos o un ˙nico valor. Se descartan
    los Ìndices no v·lidos (negativos o fuera de rango se validan en la
    ejecuciÛn, aquÌ solo se normaliza el tipo).
    """
    if valor is None or valor == "":
        return []
    if not isinstance(valor, list):
        valor = [valor]
    indices: List[int] = []
    for item in valor:
        try:
            indice = int(item)
        except (TypeError, ValueError):
            continue
        if indice < 0:
            continue
        indices.append(indice)
    return sorted(set(indices))


def _generar_plan(consulta: str, proveedor: Optional[str] = None,
                  modelo: Optional[str] = None) -> List[dict]:
    """Pide al proveedor de IA un plan en JSON para la ``consulta``.

    Devuelve la lista de pasos normalizada (vacÌa si el proveedor no devolviÛ
    nada utilizable). Lanza RuntimeError ante fallos de configuraciÛn/API.
    """
    preferencias = cargar_configuracion()
    proveedor = proveedor or preferencias.get("provider") or PROVEEDOR_DEFECTO
    cfg = PROVEEDORES[proveedor]
    modelo = modelo or cfg["modelo_default"]
    prompt = PROMPT_PLAN.format(consulta=consulta)
    info(f"Generando plan con {cfg['nombre']} ({modelo})...")

    # MCP (v0.14.0): explora el proyecto con herramientas de solo lectura para
    # generar pasos m·s precisos (best-effort: nunca rompe la planificaciÛn).
    try:
        contexto_proyecto: List[str] = []
        estado = _ejecutar_herramienta_mcp("git_status", {},
                                           confirmar=False)
        if estado.get("ok"):
            res = estado["resultado"]
            contexto_proyecto.append(
                f"Rama git: {res.get('rama')} ¬∑ cambios sin commitear: "
                f"{res.get('total_cambios')}")
        listado = _ejecutar_herramienta_mcp(
            "list_files", {"max_archivos": 30}, confirmar=False)
        if listado.get("ok"):
            contexto_proyecto.append(
                "Archivos del proyecto (muestra): "
                + ", ".join(listado["resultado"]["archivos"][:30]))
        if contexto_proyecto:
            prompt += "\n\nCONTEXTO DEL PROYECTO (obtenido con herramientas " \
                      "MCP):\n" + "\n".join(contexto_proyecto)
            info("?? Contexto MCP del proyecto aÒadido al planificador.")
    except Exception as exc:
        depurar(f"[mcp] contexto de planificaciÛn fallÛ: {exc}")

    # Memoria de proyecto (v0.15.0): CLAUDE.md como contexto persistente.
    if MEMORIA_PROYECTO:
        # v6.1.0: el contenido del archivo CLAUDE.md se limita por tokens con
        # contexto selectivo (antes: recorte bruto a 3000 caracteres).
        try:
            import context_utils as _ctxm
            memoria_ctx = _ctxm.seleccionar_contexto(
                MEMORIA_PROYECTO, "markdown", max_tokens=750)
        except Exception as _exc:       # noqa: BLE001 ‚Äî best-effort
            depurar(f"[plan] contexto selectivo de CLAUDE.md fallÛ: {_exc}")
            memoria_ctx = MEMORIA_PROYECTO[:3000]
        prompt += ("\n\nMEMORIA DEL PROYECTO (CLAUDE.md, respeta sus "
                   "convenciones al proponer pasos):\n" + memoria_ctx)
        info("?? Memoria del proyecto (CLAUDE.md) incluida en la planificaciÛn.")

    # Skills din·micos (v6.6.0): reglas abstractas aprendidas de planes
    # exitosos enriquecen el prompt (m·x. 3, priorizadas por confianza).
    prompt = _enriquecer_prompt_con_reglas(prompt, consulta)


    tipo = cfg["tipo"]
    if tipo == "gemini":
        if _importar_genai() is None:
            raise RuntimeError(MENSAJE_GENAI_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(MENSAJE_API_KEY)
        genai.configure(api_key=api_key)
        generador = genai.GenerativeModel(model_name=modelo)
        config = genai.types.GenerationConfig(
            temperature=0.2, response_mime_type="application/json")
        try:
            respuesta = generador.generate_content(prompt, generation_config=config)
            texto = respuesta.text or ""
        except Exception as exc:
            raise RuntimeError(f"Error al generar el plan con Gemini: {exc}") from exc

    elif tipo == "anthropic":
        if _importar_anthropic() is None:
            raise RuntimeError(MENSAJE_ANTHROPIC_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))
        cliente = anthropic.Anthropic(api_key=api_key)
        try:
            respuesta = cliente.messages.create(
                model=modelo, max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            texto = "".join(
                bloque.text for bloque in respuesta.content
                if getattr(bloque, "type", None) == "text")
        except Exception as exc:
            raise RuntimeError(
                f"Error al generar el plan con Claude: {exc}") from exc

    else:  # tipo "openai"
        if _importar_openai() is None:
            raise RuntimeError(MENSAJE_OPENAI_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if cfg["requiere_clave"] and not api_key:
            raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))
        cliente = openai.OpenAI(
            api_key=api_key or "ollama-local",
            base_url=_resolver_url_openai(cfg), timeout=120)
        mensajes = [{"role": "user", "content": prompt}]
        try:
            try:
                respuesta = cliente.chat.completions.create(
                    model=modelo, messages=mensajes, temperature=0.2,
                    response_format={"type": "json_object"})
            except Exception:
                respuesta = cliente.chat.completions.create(
                    model=modelo, messages=mensajes, temperature=0.2)
            texto = respuesta.choices[0].message.content or ""
        except Exception as exc:
            raise RuntimeError(
                f"Error al generar el plan con {cfg['nombre']}: {exc}") from exc

    # v6.2.0: muestra el razonamiento (chain-of-thought) si est· activado y
    # limpia los bloques <think> antes de parsear el JSON del plan.
    texto, _raz_plan = _procesar_razonamiento(texto,
                                              activo=_razonamiento_activo())
    depurar(f"Plan recibido ({len(texto)} caracteres): {texto[:200]}")
    return _normalizar_pasos(parsear_json(texto))


# --- Git explÌcito para el planificador ------------------------------------
def _es_repo_git(directorio: str) -> bool:
    """True si ``directorio`` est· dentro de un repositorio git."""
    codigo, _, _ = _ejecutar_comando("git rev-parse --is-inside-work-tree",
                                     directorio, timeout=15)
    return codigo == 0


def _git_crear_rama(nombre: str, directorio: str = ".") -> bool:
    """Crea y cambia a la rama ``nombre`` (git checkout -b). True si ok."""
    if not nombre or not nombre.strip():
        error("--branch requiere un nombre de rama.")
        return False
    if not _es_repo_git(directorio):
        error(f"'{directorio}' no es un repositorio git; no se puede crear "
              f"la rama '{nombre}'.")
        return False
    codigo, _, stderr = _ejecutar_comando(
        f'git checkout -b "{nombre.strip()}"', directorio, timeout=30)
    if codigo == 0:
        exito(f"Rama creada y activada: {nombre.strip()}")
        return True
    # La rama puede existir ya; intentar solo cambiar a ella.
    codigo2, _, _ = _ejecutar_comando(
        f'git checkout "{nombre.strip()}"', directorio, timeout=30)
    if codigo2 == 0:
        aviso(f"La rama '{nombre.strip()}' ya existÌa; se ha cambiado a ella.")
        return True
    error(f"No se pudo crear/cambiar a la rama '{nombre.strip()}': "
          f"{stderr.strip()}")
    return False


def _git_commit_paso(descripcion: str, directorio: str = ".") -> bool:
    """`git add .` + `git commit -m "paso: <descripcion>"`. True si ok.

    Si no hay cambios que commitear se considera Èxito silencioso.
    """
    if not _es_repo_git(directorio):
        depurar("[plan] No es repo git; se omite el commit del paso.")
        return True
    _ejecutar_comando("git add .", directorio, timeout=60)
    mensaje = f"paso: {descripcion}".replace('"', "'")
    codigo, _, stderr = _ejecutar_comando(
        f'git commit -m "{mensaje}"', directorio, timeout=60)
    if codigo == 0:
        exito(f"Commit creado: {mensaje}")
        return True
    texto = (stderr or "").lower()
    if "nothing to commit" in texto or "no changes added" in texto:
        depurar("[plan] Sin cambios que commitear en este paso.")
        return True
    aviso(f"El commit del paso fallÛ: {(stderr or '').strip()}")
    return False


# ----------------------------------------------------------------------
# v6.20.0 ‚Äî Git profundo: mensajes de commit con IA, commits atÛmicos
# por paso (con hash en la BD) y revert nativo (`snapcontext revert N`).
# ----------------------------------------------------------------------

_PATRON_SECRETOS = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}"                            # claves OpenAI-like
    r"|[A-Za-z0-9_\-]{32,}"                              # tokens genÈricos largos
    r"|(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


def _sanear_mensaje_commit(texto: str) -> str:
    """Elimina posibles secretos del mensaje de commit (restricciÛn v6.20.0)."""
    return _PATRON_SECRETOS.sub("[REDACTADO]", texto or "")


def _generar_mensaje_commit(diff: str, tarea: str) -> str:
    """Genera un mensaje de commit (Conventional Commits) con el proveedor.

    Si el proveedor no est· disponible o falla, devuelve el mensaje genÈrico
    ``"paso: {tarea}"``. Nunca lanza y nunca incluye secretos en el mensaje.
    """
    fallback = f"paso: {tarea}".replace('"', "'").strip() or "paso: cambio"
    try:
        cfg = cargar_configuracion()
        proveedor = cfg.get("provider") or "ollama"
        modelo = cfg.get("model") or None
        diff_recortado = (diff or "")[:4000]
        prompt = (
            "Escribe UNA sola lÌnea de mensaje de commit en formato "
            "Conventional Commits (ej: 'feat: aÒadir funciÛn de "
            "autenticaciÛn') en espaÒol, describiendo este cambio.\n\n"
            f"Tarea: {tarea}\n\nDiff:\n{diff_recortado}\n\n"
            "Responde SOLO con la lÌnea del mensaje, sin comillas."
        )
        respuesta = _enviar_al_proveedor(proveedor, modelo, [
            {"role": "user", "content": prompt},
        ])
        mensaje = _sanear_mensaje_commit(
            (respuesta or "").strip().splitlines()[0].strip().strip('"'))
        return mensaje or fallback
    except Exception:                                    # noqa: BLE001
        return fallback


def _commit_paso(paso: dict, args: argparse.Namespace,
                 directorio: str = ".") -> Optional[str]:
    """Commit atÛmico de un paso (v6.20.0). Devuelve el hash o ``None``.

    - Inicializa el repo con ``git init`` si el directorio no es repo git.
    - Si no hay cambios, no commitea y devuelve ``None`` (idempotente).
    - Usa ``--git-mensaje`` si el usuario lo indicÛ; si no, genera el mensaje
      con IA vÌa :func:`_generar_mensaje_commit`.
    - Guarda el hash en la tabla ``pasos`` de la BD y muestra
      ``?? Commit autom·tico: {mensaje} ({hash})``.
    Nunca lanza: un fallo de git no debe bloquear el plan.
    """
    try:
        descripcion = str(paso.get("descripcion") or "cambio")
        if not _es_repo_git(directorio):
            _ejecutar_comando("git init", directorio, timeout=30)
        codigo_estado, salida_estado, _ = _ejecutar_comando(
            "git status --porcelain", directorio, timeout=30)
        if codigo_estado != 0 or not (salida_estado or "").strip():
            depurar("[git-profundo] Sin cambios; no se commitea.")
            return None
        _ejecutar_comando("git add .", directorio, timeout=60)
        if getattr(args, "git_mensaje", None):
            mensaje = str(args.git_mensaje).replace('"', "'")
        else:
            mensaje = _generar_mensaje_commit(salida_estado, descripcion)
        mensaje = _sanear_mensaje_commit(mensaje)
        codigo, _, stderr = _ejecutar_comando(
            f'git commit -m "{mensaje}"', directorio, timeout=60)
        if codigo != 0:
            aviso(f"El commit del paso fallÛ: {(stderr or '').strip()}")
            return None
        _, salida_hash, _ = _ejecutar_comando(
            "git rev-parse HEAD", directorio, timeout=30)
        commit_hash = (salida_hash or "").strip() or None
        _db_registrar_paso(descripcion, commit_hash,
                           tipo=str(paso.get("accion") or "paso"))
        _emitir(sys.stdout, _pintar(
            f"?? Commit autom·tico: {mensaje} ({commit_hash})", _VERDE))
        return commit_hash
    except Exception as exc:                             # noqa: BLE001
        aviso(f"[git-profundo] No se pudo commitear el paso: {exc}")
        return None


def _db_registrar_paso(descripcion: str, commit_hash: Optional[str],
                       tipo: str = "paso",
                       tarea_id: Optional[int] = None) -> Optional[int]:
    """Inserta una fila en la tabla ``pasos`` y devuelve su id (o ``None``)."""
    try:
        _db_ejecutar(
            "INSERT INTO pasos (tarea_id, tipo, descripcion, commit_hash)"
            " VALUES (?, ?, ?, ?)",
            (tarea_id, tipo, descripcion, commit_hash))
        filas = _db_query("SELECT last_insert_rowid() AS id")
        return int(filas[0]["id"]) if filas else None
    except Exception:                                    # noqa: BLE001
        return None


def _db_migrar_pasos() -> None:
    """MigraciÛn v6.20.0: crea la tabla ``pasos`` (commits atÛmicos) si falta.

    Idempotente: usa ``CREATE TABLE IF NOT EXISTS``, de modo que las bases
    creadas antes de v6.20.0 se actualizan sin perder datos.
    """
    _db_ejecutar(
        "CREATE TABLE IF NOT EXISTS pasos ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "tarea_id INTEGER, "
        "tipo TEXT NOT NULL, "
        "descripcion TEXT, "
        "commit_hash TEXT, "
        "creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    _db_ejecutar(
        "CREATE INDEX IF NOT EXISTS idx_pasos_tarea ON pasos(tarea_id)")


def _revertir_paso(step_id: int) -> bool:
    """Revierte el commit del paso ``step_id`` (v6.20.0). True si ok.

    Ejecuta ``git revert --no-commit <hash>`` + ``git commit -m
    "revert: paso {id}"``. Si hay conflictos, sugiere ``git mergetool``.
    """
    filas = _db_query(
        "SELECT * FROM pasos WHERE id = ? ORDER BY id DESC LIMIT 1", (step_id,))
    if not filas:
        error(f"No existe el paso {step_id} en la base de datos.")
        return False
    paso = filas[0]
    commit_hash = paso["commit_hash"]
    mensaje = paso["descripcion"] or ""
    if not commit_hash:
        aviso(f"El paso {step_id} no tiene commit asociado.")
        return False
    if not _es_repo_git("."):
        error("El directorio actual no es un repositorio git.")
        return False
    codigo, _, stderr = _ejecutar_comando(
        f"git revert --no-commit {commit_hash}", ".", timeout=120)
    texto_err = (stderr or "")
    if codigo != 0:
        if "conflict" in texto_err.lower():
            error(f"Conflicto al revertir el paso {step_id}:\n"
                  f"{texto_err.strip()}\n"
                  "ResuÈlvelo con 'git mergetool' y finaliza con "
                  "'git commit'.")
        else:
            error(f"No se pudo revertir el paso {step_id}: "
                  f"{texto_err.strip()}")
        return False
    codigo, _, stderr = _ejecutar_comando(
        f'git commit -m "revert: paso {step_id}"', ".", timeout=60)
    if codigo != 0 and "nothing to commit" not in (stderr or "").lower():
        error(f"No se pudo cerrar el revert del paso {step_id}: "
              f"{(stderr or '').strip()}")
        return False
    _db_registrar_paso(f"revert: paso {step_id}", None, tipo="revert")
    _emitir(sys.stdout, _pintar(
        f"‚Ü©Ô∏è Revertido paso {step_id}: {mensaje}", _AMARILLO))
    return True


def _ejecutar_revert(step: Optional[str] = None) -> int:
    """Comando ``snapcontext revert <step>`` (v6.20.0).

    ``step`` puede ser un id numÈrico; si se omite, se revierte el ˙ltimo
    paso commiteado. Devuelve el cÛdigo de salida (0 = Èxito).
    """
    try:
        if step is None or not str(step).strip():
            filas = _db_query(
                "SELECT id FROM pasos WHERE tipo != 'revert' "
                "AND commit_hash IS NOT NULL ORDER BY id DESC LIMIT 1")
            if not filas:
                error("No hay pasos con commit para revertir.")
                return 1
            step_id = int(filas[0]["id"])
        else:
            step_id = int(str(step).strip())
    except (TypeError, ValueError):
        error(f"Paso inv·lido: {step!r}. Usa 'snapcontext revert <N>'.")
        return 1
    filas = _db_query("SELECT id FROM pasos WHERE id = ?", (step_id,))
    if not filas:
        error(f"El paso {step_id} no existe en la base de datos.")
        return 1
    return 0 if _revertir_paso(step_id) else 1

# v6.22.0: gestor global de hooks (lifecycle events). Import perezoso y
# tolerante: sin `hooks.py` disponible, los call-sites no rompen el flujo.
try:
    import hooks as _hooks                      # type: ignore[import]
except Exception:                              # pragma: no cover
    _hooks = None                               # type: ignore[assignment]


def _hooks_inicializar() -> None:
    """Carga perezosa de hooks desde plugins y ~/.snapcontext/hooks/ (v6.22.0).

    Solo se ejecuta una vez por proceso y solo si el sistema est· activo
    (`--no-hooks` lo impide). Sin hooks instalados no tiene coste apreciable.
    """
    if _hooks is None:
        return
    try:
        if not _hooks._CARGADO:
            _hooks.cargar_todos_los_hooks()
            _hooks._CARGADO = True
    except Exception as exc:                     # noqa: BLE001
        depurar(f"[hooks] No se pudieron cargar los hooks: {exc}")


def _hooks_ejecutar(evento: str, contexto: Optional[dict] = None) -> tuple:
    """Wrapper seguro de ``hooks.ejecutar_hook`` (v6.22.0).

    Devuelve ``(abortado, contexto)``; si el mÛdulo no est· disponible o el
    sistema est· desactivado contin˙a sin abortar (compatibilidad total).
    """
    if _hooks is None:
        return False, (contexto if isinstance(contexto, dict) else {})
    try:
        return _hooks.ejecutar_hook(evento, contexto)
    except Exception as exc:                     # noqa: BLE001
        depurar(f"[hooks] Error ejecutando '{evento}': {exc}")
        return False, (contexto if isinstance(contexto, dict) else {})


def _ejecutar_paso_plan(paso: dict, args: argparse.Namespace,
                        raiz: str) -> tuple:
    """Ejecuta un paso del plan. Devuelve (ok: bool, detalle: str).

    - "editar": usa el orquestador actual ‚Äî ``_planificar`` para elegir los
      archivos y ``_bucle_test``/AgenteEditor para aplicar la descripciÛn.
    - "ejecutar": lanza ``paso["comando"]`` con ``_ejecutar_comando``.
    - "consultar": pregunta al proveedor y muestra su respuesta.

    v6.22.0: ejecuta los hooks ``before_plan_step`` (puede abortar el paso)
    y ``after_plan_step`` al finalizar.
    """
    import snapcontext as sc
    from orquestador import Orquestador

    # v6.22.0: hook before_plan_step ‚Äî puede modificar el paso o abortarlo.
    _ctx_hook = {"paso": paso, "accion": paso.get("accion"),
                 "descripcion": paso.get("descripcion")}
    _abortado, _ctx_hook = _hooks.ejecutar_hook("before_plan_step", _ctx_hook)
    if _abortado:
        aviso(f"Paso abortado por hook: {paso.get('descripcion', '')!s:.60}")
        return (False, "abortado por hook before_plan_step")
    paso = _ctx_hook.get("paso") or paso

    accion = paso["accion"]
    descripcion = paso["descripcion"]
    # v2.3.0: sustituciÛn de marcadores {{variable}} / {{resultado}} en los
    # campos del paso usando el contexto din·mico del plan.
    descripcion = _resolver_marcadores(descripcion)
    for _clave in ("comando", "herramienta", "contenido"):
        _valor = paso.get(_clave)
        if isinstance(_valor, str) and "{{" in _valor:
            paso[_clave] = _resolver_marcadores(_valor)
    if isinstance(paso.get("archivos"), list):
        paso["archivos"] = [_resolver_marcadores(a) for a in paso["archivos"]]
    if isinstance(paso.get("args"), dict) and paso["args"]:
        paso["args"] = _resolver_marcadores_args(paso["args"])

    # ConfirmaciÛn de permisos (v0.13.0) antes de cualquier acciÛn.
    # En modo autÛnomo (--auto, v0.17.0) no se pregunta: solo se respetan las
    # preferencias ya guardadas en permisos.json (nunca ‚Üí denegado).
    if accion == "ejecutar":
        detalles_paso = paso.get("comando") or None
    elif accion == "editar":
        detalles_paso = "\n".join(paso.get("archivos", [])) or None
    elif accion == "mcp":
        detalles_paso = str(paso.get("herramienta") or "")
    else:
        detalles_paso = None
    if getattr(args, "auto", False):
        if _permiso_recordado(accion) is False:
            aviso(f"[auto] Paso '{accion}' denegado por permisos guardados "
                  f"(permisos.json).")
            return (False, "denegado por permisos guardados")
    elif not _confirmar_accion(
            descripcion, tipo=accion, detalles=detalles_paso,
            confirmar=getattr(args, "confirmar", True)):
        return (False, "denegado por el usuario")

    if accion == "ejecutar":
        if not paso.get("comando"):
            return (False, 'el paso no indica "comando"')
        info(f'$ {paso["comando"]}')
        codigo, stdout, stderr = _ejecutar_comando(paso["comando"], raiz)
        if stdout.strip():
            _emitir(sys.stdout, _pintar(stdout.rstrip(), _VERDE))
        if stderr.strip():
            _emitir(sys.stdout, _pintar(stderr.rstrip(), _AMARILLO))
        return (codigo == 0, f"cÛdigo {codigo}")

    # accion == "mcp" (v2.3.0): ejecuta una herramienta MCP y deja su
    # resultado en el contexto del plan para los pasos siguientes.
    if accion == "mcp":
        herramienta = paso.get("herramienta")
        if not herramienta:
            return (False, 'el paso no indica "herramienta"')
        argumentos = _resolver_marcadores_args(paso.get("args") or {})
        info(("[mcp] " + herramienta + " " + str(argumentos)).rstrip())
        llamada = _ejecutar_herramienta_mcp(herramienta, argumentos)
        res = llamada.get("resultado", {})
        try:
            muestra = json.dumps(res, ensure_ascii=False)
        except Exception:
            muestra = str(res)
        if len(muestra) > 400:
            muestra = muestra[:400] + "‚Ä¶"
        if llamada.get("ok"):
            exito("[mcp] resultado: " + muestra)
            _contexto_plan_variable(str(paso.get("variable") or herramienta),
                                    res)
            return (True, herramienta + ": ok")
        error("[mcp] fallÛ: " + muestra)
        return (False, herramienta + ": "
                + str(res.get("error", "fallo")))

    if accion == "consultar":
        preferencias = cargar_configuracion()
        proveedor = preferencias.get("provider") or PROVEEDOR_DEFECTO
        try:
            respuesta = _enviar_al_proveedor(
                proveedor, getattr(args, "modelo", None),
                [{"role": "user",
                  "content": f"Tarea general: {getattr(args, 'consulta', '')}\n"
                             f"Paso a aclarar: {descripcion}\n"
                             "Responde de forma breve y ˙til."}],
            )
            respuesta, _raz = _procesar_razonamiento(
                respuesta, activo=_razonamiento_activo(args))
            _emitir(sys.stdout, _pintar(respuesta, _VERDE))
            return (True, "respuesta mostrada")
        except RuntimeError as exc:
            error(str(exc))
            return (False, str(exc))

    # accion == "seguridad" / "rendimiento" (v4.2.0): an·lisis enfocado;
    # en --auto se ejecutan solos y las sugerencias solo se muestran.
    if accion in ("seguridad", "rendimiento"):
        tipos = ("vulnerabilidad",) if accion == "seguridad" \
            else ("rendimiento",)
        encontradas = _asesor_analizar_por_tipo(raiz, tipos)
        if not encontradas:
            exito(f"[{accion}] Sin hallazgos: sin problemas detectados.")
            return (True, "sin hallazgos")
        for sugg in encontradas:
            texto = (f"[{accion}] {sugg['descripcion']} "
                     f"({sugg['archivo']}:{sugg['linea']})")
            if getattr(args, "auto", False):
                aviso(texto + f" ‚Üí {sugg['solucion']}")
                continue
            if _confirmar_accion(texto, tipo=accion,
                                 detalles=sugg.get("solucion"),
                                 confirmar=getattr(args, "confirmar", True)):
                exito(f"Anotada: {sugg['solucion']}")
        return (True, f"{len(encontradas)} hallazgo(s) de {accion}")

    # accion == "asesor" (v3.5.0): an·lisis est·tico del proyecto; cada
    # sugerencia se presenta al usuario para aceptarla o rechazarla. En modo
    # --auto solo se informan (nunca se aplica cÛdigo sin confirmaciÛn).
    if accion == "asesor":
        sugerencias_paso = _asesor_analizar(raiz)
        if not sugerencias_paso:
            exito("[asesor] Sin sugerencias: el cÛdigo est· limpio.")
            return (True, "sin sugerencias")
        aceptadas = 0
        for sugg in sugerencias_paso:
            texto = (f"[asesor] {sugg['descripcion']} "
                     f"({sugg['archivo']}:{sugg['linea']})")
            if getattr(args, "auto", False):
                aviso(texto + f" ‚Üí {sugg['solucion']}")
                continue
            if _confirmar_accion(texto, tipo="asesor",
                                 detalles=sugg.get("solucion"),
                                 confirmar=getattr(args, "confirmar", True)):
                aceptadas += 1
                exito(f"Sugerencia aceptada: {sugg['solucion']}")
            else:
                info("Sugerencia descartada.")
        return (True, f"{len(sugerencias_paso)} sugerencia(s), "
                      f"{aceptadas} aceptada(s)")

    # accion == "editar": reutiliza el pipeline existente o usa el editor propio
    editor_elegido = getattr(args, "editor", "aider") or "aider"
    if editor_elegido == "propio":
        archivos_paso = paso.get("archivos", [])
        contenido_paso = paso.get("contenido")
        if archivos_paso and contenido_paso is not None:
            # Si el paso trae archivo y contenido explÌcito
            todo_ok = True
            for arch in archivos_paso:
                if not _editor_sobrescribir(arch, contenido_paso, raiz):
                    todo_ok = False
            return (todo_ok, f"EditorPropio sobre {len(archivos_paso)} archivo(s)")

    paso_args = argparse.Namespace(**vars(args))
    paso_args.consulta = descripcion
    orch = Orquestador()
    plan = orch._planificar(paso_args, sc)
    if plan is None:
        return (False, "no se pudo planificar la ediciÛn (sin candidatos)")
    _, ruta_raiz, _, seleccion = plan

    if editor_elegido == "propio":
        modo_ed = getattr(args, "modo_edicion", "auto") or "auto"
        todo_ok = orch.agente_editor_propio.ejecutar(
            seleccion,
            descripcion,
            directorio=str(ruta_raiz),
            modo_edicion=modo_ed,
            modelo=getattr(args, "modelo", None),
            validar=getattr(args, "validar", True),
            max_intentos_validacion=getattr(
                args, "max_intentos_validacion", MAX_INTENTOS_VALIDACION),
            proveedor=getattr(args, "provider", None),
            modelo_ligero=getattr(args, "modelo_ligero", False),
            auto=getattr(args, "auto", False),
            max_context_tokens=getattr(args, "max_context_tokens", None),
            editor_fallback=getattr(args, "editor_fallback", False),
            mostrar_diff=getattr(args, "mostrar_diff", False),
        )
        return (todo_ok, f"EditorPropio sobre {len(seleccion)} archivo(s)")

    if getattr(args, "test_loop", False):
        _comando_test = None
        if getattr(args, "comando_test", None):
            _comando_test = shlex.split(args.comando_test)
        ok = orch._bucle_test(
            descripcion, seleccion, str(ruta_raiz),
            opciones_aider=getattr(args, "aider_opciones", ""),
            comando_test=_comando_test,
            max_iteraciones=max(getattr(args, "max_iteraciones", 1), 1),
        )
        return (ok, "bucle de pruebas")
    ok = orch.agente_editor.ejecutar_aider(
        seleccion, descripcion, str(ruta_raiz),
        opciones_aider=getattr(args, "aider_opciones", ""),
    )
    # v6.22.0: hook `after_plan_step` ‚Äî observabilidad post-ejecuciÛn del paso.
    try:
        _hooks.ejecutar_hook("after_plan_step", {
            "paso": paso, "ok": ok, "detalle": f"Aider sobre {len(seleccion)} archivo(s)"})
    except Exception:                                # noqa: BLE001 ‚Äî nunca romper
        pass
    return (ok, f"Aider sobre {len(seleccion)} archivo(s)")


# --- Condiciones y paralelismo del planificador (v1.4.0) --------------------
def _evaluar_condicion(condicion: str, raiz: str = ".",
                      contexto: Optional[dict] = None) -> bool:
    """Eval˙a la condiciÛn de un paso del plan. Devuelve True si se cumple.

    Formatos soportados:

      Funciones (v1.4.0):
        archivo_existe('src/main.py')
        archivo_contiene('src/main.py', 'def main')
        comando_exito('flutter test')
        variable_existe('mi_variable')            # v2.3.0

      Comparaciones din·micas (v2.3.0), con resultados de pasos previos o
      variables dejadas en el contexto (p. ej. por pasos "mcp"):
        pasos[0].resultado == 'ok'
        pasos[2].resultado != 'fallo'
        resultados.mi_variable == 'listo'
        mi_variable != ''                         # forma abreviada

    Las cadenas pueden ir con comillas simples o dobles. Cualquier condiciÛn
    mal formada o desconocida devuelve False con un aviso (fallo elegante:
    el paso se salta, nunca se aborta el plan).
    """
    if contexto is None:
        contexto = _CONTEXTO_PLAN
    condicion = (condicion or "").strip()
    if not condicion:
        return True

    # 1) Comparaciones din·micas (== / !=).
    comparacion = re.match(r"^(.+?)\s*(==|!=)\s*(.+)$", condicion, re.S)
    if comparacion and "(" not in condicion.split("==")[0].split("!=")[0]:
        izquierdo = _resolver_operando_condicion(
            comparacion.group(1).strip(), contexto)
        derecho = _resolver_operando_condicion(
            comparacion.group(3).strip(), contexto)
        if izquierdo is _DESCONOCIDO or derecho is _DESCONOCIDO:
            aviso(f"CondiciÛn con referencia desconocida: '{condicion}'.")
            return False
        iguales = (_normalizar_comparacion(izquierdo)
                   == _normalizar_comparacion(derecho))
        return iguales if comparacion.group(2) == "==" else not iguales

    # 2) Formas funcionales cl·sicas.
    coincidencia = re.match(r"^([a-zA-Z_]\w*)\s*\((.*)\)\s*$",
                            condicion, re.S)
    if not coincidencia:
        aviso(f"CondiciÛn de paso mal formada: '{condicion}'. Se interpreta "
              f"como no cumplida.")
        return False
    funcion, crudo_args = coincidencia.group(1), coincidencia.group(2)
    try:
        argumentos = [a.strip()
                      for a in _partir_argumentos(crudo_args)]
    except ValueError as exc:
        aviso(f"CondiciÛn inv·lida '{condicion}': {exc}")
        return False

    if funcion == "archivo_existe":
        return len(argumentos) == 1 and (Path(raiz) / argumentos[0]).exists()
    if funcion == "archivo_contiene":
        if len(argumentos) != 2:
            return False
        contenido = _leer_archivo(Path(raiz) / argumentos[0])
        return contenido is not None and argumentos[1] in contenido
    if funcion == "comando_exito":
        if not argumentos or not argumentos[0]:
            return False
        codigo, _, _ = _ejecutar_comando(argumentos[0], raiz, timeout=300)
        return codigo == 0
    if funcion == "variable_existe":
        with _CANDADO_CONTEXTO_PLAN:
            variables = dict(contexto.get("variables", {}))
        return bool(argumentos) and argumentos[0] in variables

    aviso(f"FunciÛn de condiciÛn desconocida: '{funcion}'. Soportadas: "
          f"archivo_existe, archivo_contiene, comando_exito, "
          f"variable_existe.")
    return False


# Resultados desconocidos para condiciones dinamicas.
_DESCONOCIDO = object()


def _resolver_operando_condicion(operando: str, contexto: dict):
    """Convierte un operando de condiciÛn en un valor Python concreto.

    Acepta literales ('texto', n˙meros, true/false/null) y referencias al
    contexto: pasos[N].campo, resultados.nombre o un identificador simple.
    Devuelve _DESCONOCIDO si no se puede resolver.
    """
    operando = operando.strip()
    if len(operando) >= 2 and operando[0] in "'\"" \
            and operando[-1] == operando[0]:
        return operando[1:-1]
    if operando.lower() in ("true", "verdad"):
        return True
    if operando.lower() in ("false", "falso"):
        return False
    if operando.lower() in ("none", "null", "nulo"):
        return None
    try:
        return int(operando)
    except ValueError:
        pass
    try:
        return float(operando)
    except ValueError:
        pass

    m = re.match(r"^pasos\[(\d+)\]\.(\w+)$", operando)
    if m:
        numero, campo = int(m.group(1)), m.group(2)
        with _CANDADO_CONTEXTO_PLAN:
            paso_ctx = contexto.get("pasos", {}).get(str(numero))
        if not isinstance(paso_ctx, dict) or campo not in paso_ctx:
            return _DESCONOCIDO
        return paso_ctx[campo]

    m = re.match(r"^resultados?\.(\w+)$", operando)
    if m:
        with _CANDADO_CONTEXTO_PLAN:
            variables = contexto.get("variables", {})
        return variables.get(m.group(1), _DESCONOCIDO)

    if re.match(r"^[a-z_][\w]*$", operando):
        with _CANDADO_CONTEXTO_PLAN:
            variables = contexto.get("variables", {})
        return variables.get(operando, _DESCONOCIDO)

    return _DESCONOCIDO


def _normalizar_comparacion(valor):
    """Normaliza valores para poder compararlos entre sÌ."""
    if isinstance(valor, bool):
        return "ok" if valor else "fallo"
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, (dict, list)):
        try:
            import json as _json
            return _json.dumps(valor, sort_keys=True, ensure_ascii=False)
        except Exception:
            return str(valor)
    return str(valor)


def _partir_argumentos(texto: str) -> List[str]:
    """Separa los argumentos de una condiciÛn respetando comillas."""
    partes, actual, comilla = [], "", None
    for caracter in texto:
        if comilla:
            if caracter == comilla:
                comilla = None
            else:
                actual += caracter
            continue
        if caracter in ("'", '"'):
            comilla = caracter
            continue
        if caracter == ",":
            partes.append(actual)
            actual = ""
            continue
        actual += caracter
    if comilla:
        raise ValueError("comillas sin cerrar")
    partes.append(actual)
    return [p for p in (p.strip() for p in partes)]


# --- Contexto din·mico del plan (v2.3.0) ------------------------------------
# Los pasos pueden dejar resultados (p. ej. herramientas MCP) en este contexto
# y los pasos posteriores los consumen con {{resultado}}, {{mi_variable}} o
# condiciones como "pasos[0].resultado == 'ok'" / "resultados.mi_var == 'x'".
_CONTEXTO_PLAN = {"variables": {}, "pasos": {}}
_CANDADO_CONTEXTO_PLAN = threading.Lock()


def _contexto_plan_reiniciar() -> None:
    """Limpia el contexto din·mico al empezar cada ejecuciÛn del plan."""
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["variables"].clear()
        _CONTEXTO_PLAN["pasos"].clear()


def _contexto_plan_variable(nombre: str, valor) -> None:
    """Guarda ``valor`` bajo ``nombre`` (y como ˙ltimo ``resultado``)."""
    if not nombre:
        return
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["variables"][nombre] = valor
        _CONTEXTO_PLAN["variables"]["resultado"] = valor


def _registrar_resultado_plan(numero: int, ok: bool, detalle: str,
                              estado: str = "") -> None:
    """Registra el resultado de un paso (base 1) para condiciones din·micas."""
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["pasos"][str(numero)] = {
            "resultado": estado or ("ok" if ok else "fallo"),
            "ok": ok, "detalle": detalle}


def _resolver_marcadores(texto: str):
    """Sustituye la marca de doble llave {{clave}} por el valor que
    tenga esa clave en el contexto din·mico del plan. Si la clave
    no existe o el texto no es una cadena, se devuelve sin cambios.

    Si ``texto`` no es una cadena se devuelve tal cual. Las claves desconocidas
    se dejan sin sustituir (fallo elegante).
    """
    if not isinstance(texto, str) or "{{" not in texto:
        return texto
    import json as _json
    with _CANDADO_CONTEXTO_PLAN:
        variables = dict(_CONTEXTO_PLAN["variables"])

    def _sustituir(coincidencia):
        clave = coincidencia.group(1).strip()
        if clave not in variables:
            return coincidencia.group(0)
        valor = variables[clave]
        if isinstance(valor, str):
            return valor
        try:
            return _json.dumps(valor, ensure_ascii=False)
        except Exception:
            return str(valor)

    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", _sustituir, texto)


def _refs_de_condicion(condicion: str) -> tuple:
    """Extrae los Ìndices de pasos y nombres de variables que usa una condiciÛn."""
    condicion = condicion or ""
    indices = set()
    for m in re.findall(r"pasos\[(\d+)\]", condicion):
        try:
            indices.add(int(m) - 1)
        except ValueError:
            continue
    nombres = set(re.findall(r"resultados?\.(\w+)", condicion))
    for m in re.findall(r"(?:^|\(|&&|\|)\s*([a-z_][\w]*)"
                        r"\s*(?:==|!=)", condicion):
        nombre = m[1] if isinstance(m, tuple) else m
        if nombre not in ("true", "false", "none", "ok"):
            nombres.add(nombre)
    return indices, nombres


def _resolver_marcadores_args(argumentos: dict) -> dict:
    """Aplica la sustituciÛn de marcadores a los valores string de un dict."""
    resuelto = {}
    for clave, valor in (argumentos or {}).items():
        if isinstance(valor, str):
            resuelto[clave] = _resolver_marcadores(valor)
        elif isinstance(valor, list):
            resuelto[clave] = [_resolver_marcadores(v) for v in valor]
        else:
            resuelto[clave] = valor
    return resuelto


_CANDADO_GIT_PLAN = threading.Lock()   # serializa commits en modo --paralelo


def _ejecutar_paso_paralelo(paso: dict, args: argparse.Namespace,
                            raiz: str, numero: int) -> dict:
    """Ejecuta un paso en modo --paralelo (hilo secundario). Devuelve registro."""
    prefijo = f"[paso {numero}]"
    exito(f"{prefijo} [{paso['accion']}]: {paso['descripcion']}")

    condicion = paso.get("condicion")
    if condicion and not _evaluar_condicion(condicion, raiz):
        aviso(f"{prefijo} condiciÛn no cumplida ({condicion}); se salta.")
        return {"paso": numero, "descripcion": paso["descripcion"],
                "accion": paso["accion"], "resultado": "saltado",
                "detalle": f"condiciÛn no cumplida: {condicion}", "intentos": 0}
    try:
        ok, detalle = _ejecutar_paso_plan(paso, args, raiz)
    except Exception as exc:                     # blindaje del hilo
        ok, detalle = False, f"excepciÛn: {exc}"
    _registrar_resultado_plan(numero, ok, detalle)
    marca = "‚úî" if ok else "‚úñ"
    _emitir(sys.stdout, f"  {marca} {prefijo} terminado ({detalle})")
    if ok and getattr(args, "git_commit", True):
        with _CANDADO_GIT_PLAN:
            _commit_paso(paso, args, raiz)
    return {"paso": numero, "descripcion": paso["descripcion"],
            "accion": paso["accion"], "resultado": "Èxito" if ok else "fallo",
            "detalle": detalle, "intentos": 1}


def _ejecutar_plan_en_paralelo(pasos: List[dict], args: argparse.Namespace,
                               raiz: str, max_hilos: int) -> List[dict]:
    """Ejecuta el plan con ``--paralelo N`` (modo --auto).

    Rondas de ejecuciÛn: en cada ronda se lanzan todos los pasos cuyas
    dependencias ya tuvieron Èxito (ThreadPoolExecutor limita la concurrencia
    a ``max_hilos``); los pasos con dependencias fallidas o saltadas se marcan
    como saltados. Los logs llevan el identificador ``[paso N]``.
    """
    estado: dict = {}                            # Ìndice ‚Üí resultado terminal
    resultados: List[dict] = []
    pendientes = set(range(len(pasos)))
    MALOS_TERMINALES = ("fallo", "saltado")

    with ThreadPoolExecutor(max_workers=max(1, max_hilos)) as pool:
        while pendientes:
            # 'dependencias' guarda n˙meros de paso (base 1): convertimos.
            for i in sorted(pendientes):
                deps = [d - 1 for d in (pasos[i].get("dependencias") or [])]
                if any(estado.get(d) in MALOS_TERMINALES for d in deps):
                    numero = i + 1
                    aviso(f"[paso {numero}] saltado: dependencia(s) sin Èxito "
                          f"({[d + 1 for d in deps]}).")
                    estado[i] = "saltado"
                    resultados.append(
                        {"paso": numero, "descripcion": pasos[i]["descripcion"],
                         "accion": pasos[i]["accion"], "resultado": "saltado",
                         "detalle": "dependencia sin Èxito", "intentos": 0})
                    pendientes.discard(i)

            # v2.3.0: adem·s de las dependencias explÌcitas, un paso queda
            # bloqueado mientras su condiciÛn referencie variables que alg˙n
            # paso pendiente a˙n puede producir (p. ej. un paso "mcp").
            producibles = set()
            for j in pendientes:
                _pj = pasos[j]
                _ri, _rv = _refs_de_condicion(_pj.get("condicion") or "")
                producibles |= _rv
                if _pj.get("accion") == "mcp":
                    producibles.add(str(_pj.get("variable")
                                        or _pj.get("herramienta") or ""))
                    producibles.add("resultado")

            def _listo(i):
                deps = [d - 1 for d in (pasos[i].get("dependencias") or [])]
                if any(estado.get(d) != "Èxito" for d in deps):
                    return False
                ref_i, ref_v = _refs_de_condicion(
                    pasos[i].get("condicion") or "")
                if any(estado.get(d) != "Èxito" for d in ref_i):
                    return False
                with _CANDADO_CONTEXTO_PLAN:
                    disponibles = set(_CONTEXTO_PLAN["variables"])
                    registrados = set(_CONTEXTO_PLAN["pasos"])
                for v in ref_v:
                    if v not in disponibles and v in producibles:
                        return False       # esperar a que se produzca
                for d in ref_i:
                    if str(d + 1) not in registrados:
                        return False
                return True

            lanzables = [i for i in sorted(pendientes) if _listo(i)]
            if not lanzables:
                if pendientes:                   # nada ejecutable ‚Üí evitar bloqueo
                    for i in sorted(pendientes):
                        estado[i] = "saltado"
                        resultados.append(
                            {"paso": i + 1,
                             "descripcion": pasos[i]["descripcion"],
                             "accion": pasos[i]["accion"],
                             "resultado": "saltado",
                             "detalle": "dependencias insatisfechas",
                             "intentos": 0})
                    pendientes.clear()
                continue

            futuros = {pool.submit(_ejecutar_paso_paralelo, pasos[i], args,
                                   raiz, i + 1): i for i in lanzables}
            for i in lanzables:
                pendientes.discard(i)
            for futuro in concurrent.futures.as_completed(futuros):
                i = futuros[futuro]
                registro = futuro.result()
                estado[i] = registro["resultado"]
                resultados.append(registro)

    resultados.sort(key=lambda r: r["paso"])
    return resultados


def _graph_rag_activo(args: argparse.Namespace) -> bool:
    """v5.5.0: True si el Grafo de Conocimiento est· activado.

    Prioridad: flag ``--graph-rag`` > env ``SNAPCONTEXT_GRAPH_RAG=1``.
    Nunca lanza excepciones (si graph_rag no est· disponible ‚Üí False).
    """
    if getattr(args, "graph_rag", False):
        return True
    try:
        import graph_rag as gr                   # noqa: E402
        return gr.graph_rag_activo(None)
    except Exception:                            # noqa: BLE001
        return False

    return False


def _graph_lsp_activo(args: argparse.Namespace) -> bool:
    """v6.33.0: True si la integracion Graph RAG + LSP esta activada.

    Requiere que ``--graph-rag`` Y ``--lsp`` esten activos ademas del flag
    ``--graph-rag-lsp``. Prioridad: flag > env ``SNAPCONTEXT_GRAPH_RAG_LSP=1``.
    """
    if not _graph_rag_activo(args):
        return False
    if not getattr(args, "lsp", False):
        return False
    if getattr(args, "graph_rag_lsp", False):
        return True
    return os.environ.get("SNAPCONTEXT_GRAPH_RAG_LSP", "").strip() == "1"


# Alias para compatibilidad con tests que usan el nombre antiguo.
_graph_rag_lsp_activo = _graph_lsp_activo


def _configurar_graph_lsp(args: argparse.Namespace) -> Dict[str, Any]:
    """v6.33.0: Devuelve la configuracion efectiva de Graph RAG + LSP."""
    return {
        "activo": _graph_lsp_activo(args),
        "profundidad": getattr(args, "lsp_profundidad", None) or 2,
        "simbolos_max": getattr(args, "lsp_simbolos_max", None) or 10,
    }


def _obtener_contexto_graph_lsp(
        archivo: str,
        linea: Optional[int] = None,
        grafo: Optional[Dict] = None,
        config: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """v6.33.0: Obtiene simbolos precisos via Graph RAG + LSP.

    Funcion de conveniencia que delega en ``graph_lsp_integrator``.
    Nunca lanza; devuelve ``[]`` si el modulo no esta disponible.
    """
    try:
        import graph_lsp_integrator as gli
        return gli.obtener_contexto_preciso(
            archivo, linea, "funcion", grafo,
            config=config, proveedor_lsp=None)
    except Exception:
        return []


def obtener_simbolos_lsp(
        archivo: str,
        linea: Optional[int] = None,
        tipo: str = "funcion",
        grafo: Optional[Dict] = None,
        config: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """v6.33.0: Obtiene sÌmbolos LSP de un archivo (p˙blico).

    FunciÛn de conveniencia que delega en ``graph_lsp_integrator``.
    Nunca lanza; devuelve ``[]`` si el mÛdulo no est· disponible.
    """
    try:
        import graph_lsp_integrator as gli
        return gli.obtener_contexto_preciso(
            archivo, linea, tipo, grafo,
            config=config, proveedor_lsp=None)
    except Exception:
        return []


def _multi_agent_activo(flag: Optional[bool] = None) -> bool:
    """v6.0.0: True si el modo multi-agente est· activado.

    Prioridad: flag ``--multi-agent`` > env ``SNAPCONTEXT_MULTI_AGENT=1``.
    """
    if flag is not None:
        return bool(flag)
    return os.environ.get("SNAPCONTEXT_MULTI_AGENT", "").strip() == "1"


def _ejecutar_multi_agent(args: argparse.Namespace) -> int:
    """Ejecuta el sistema multi-agente (`snapcontext --multi-agent "tarea"`).

    Instancia el ``Supervisor`` de ``multi_agent.py`` y ejecuta el pipeline
    Arquitecto ‚Üí Programador ‚Üí Tester. Devuelve 0/1. Es opcional y no altera
    el resto de modos (``--plan``, ReAct).
    """
    consulta = getattr(args, "consulta", None)
    if not consulta:
        error("El modo --multi-agent necesita una consulta. Uso:\n"
              '  snapcontext --multi-agent "aÒadir un endpoint de login"')
        return 1
    try:
        import multi_agent as ma                       # noqa: E402
    except Exception as exc:                           # noqa: BLE001
        error(f"No se pudo cargar el mÛdulo multi_agent: {exc}")
        return 1
    directorio = getattr(args, "directorio", ".") or "."
    raiz = str(resolver_raiz(directorio))
    supervisor = ma.Supervisor(
        directorio=raiz,
        tarea=consulta,
        auto=bool(getattr(args, "auto", False)),
        proveedor=getattr(args, "provider", None),
        modelo=getattr(args, "modelo", None),
        max_reintentos=max(1, int(getattr(args, "max_reintentos", 3) or 3)),
        comando_test=getattr(args, "comando_test", None),
        sub_agents=bool(getattr(args, "sub_agents", False)),
        max_parallel=int(getattr(args, "max_parallel", 3) or 3),
        lsp=bool(getattr(args, "lsp", False)),
        qa_tester_activo=bool(getattr(args, "qa_tester", True)),
        qa_iteraciones_max=int(getattr(args, "qa_iteraciones", 2) or 2),
        qa_severidad=getattr(args, "qa_severidad", "media") or "media",
    )
    resultado = supervisor.ejecutar()
    if resultado.get("ok"):
        exito("?? Multi-agente: la tarea se completÛ.")
        return 0
    error("Multi-agente: " + str(resultado.get("error")
          or "la tarea no se completÛ."))
    return 1


# v6.20.0: gestiÛn de sub-agentes din·micos desde la CLI (independiente).
def _ejecutar_listar_sub_agentes() -> int:
    """``snapcontext --sub-agente-listar``: lista los sub-agentes registrados."""
    try:
        import sub_agent as sa                              # noqa: E402
    except Exception as exc:                                 # noqa: BLE001
        error(f"No se pudo cargar el mÛdulo de sub-agentes: {exc}")
        return 1
    nombres = sa.REGISTRO_SUB_AGENTES.listar()
    info(f"?? Sub-agentes din·micos registrados ({len(nombres)}):")
    for n in nombres:
        cfg = sa.REGISTRO_SUB_AGENTES.obtener(n)
        desc = str(cfg.get("descripcion") or "")
        herramientas = ", ".join(cfg.get("herramientas") or [])
        info(f"  - {n}: {desc}")
        info(f"      herramientas: {herramientas} | "
             f"max_iter: {cfg.get('max_iter')}")
    return 0


def _registrar_sub_agente_cli(nombre: str, descripcion: str) -> int:
    """``snapcontext --sub-agente-nuevo <nombre> <descripcion>``.

    Registra un sub-agente din·mico nuevo en el registro por defecto (˙til
    para plugins: rol bajo demanda con herramientas de solo lectura). El
    nombre queda disponible para ``--sub-agente-listar``, el Supervisor y la
    herramienta ReAct ``invocar_sub_agente``.
    """
    try:
        import sub_agent as sa                              # noqa: E402
        from sub_agent_prompts import PROMPTS as _P         # noqa: E402
    except Exception as exc:                                 # noqa: BLE001
        error(f"No se pudo cargar el mÛdulo de sub-agentes: {exc}")
        return 1
    nombre = str(nombre or "").strip()
    if not nombre:
        error("--sub-agente-nuevo necesita un <nombre>.")
        return 1
    cfg = {
        "descripcion": str(descripcion or ""),
        # Si el nombre coincide con un prompt canÛnico se usa ese; si no, un
        # prompt genÈrico con herramientas de solo lectura (mÌnimo privilegio).
        "prompt": _P.get(nombre.lower(),
                         f"Eres {nombre}, un sub-agente especializado. "
                         "Lee e investiga y devuelve un resumen conciso."),
        "herramientas": ["leer_archivo", "buscar_codigo", "finalizar"],
        "max_iter": 8,
    }
    sa.REGISTRO_SUB_AGENTES.registrar(nombre, cfg)
    exito(f"‚úÖ Sub-agente registrado: {nombre} ‚Äî {descripcion}")
    return 0


def _ejecutar_react(args: argparse.Namespace) -> int:
    """Ejecuta el motor ReAct (`snapcontext [--react] "tarea"`). 0/1.

    Desde v5.2.0 es el **modo por defecto** para cualquier consulta sin
    ``--plan``; el flag ``--react`` se acepta por compatibilidad aunque sea
    redundante. Instancia el `ReactAgent` de `react_agent.py` y ejecuta el
    bucle din·mico pensamiento ‚Üí acciÛn ‚Üí observaciÛn hasta que el agente
    decida finalizar, se alcance el tope de iteraciones o el usuario aborte.
    """
    if not getattr(args, "consulta", None):
        error("El modo ReAct necesita una consulta. Uso:\n"
              '  snapcontext "aÒadir login con Google"\n'
              '  snapcontext --react "aÒadir login con Google"   # equivalente')
        return 1
    try:
        import react_agent as ra                     # noqa: E402
    except Exception as exc:                         # pragma: no cover
        error(f"No se pudo importar react_agent: {exc}")
        return 1
    agente = ra.ReactAgent(
        directorio=os.getcwd(),
        auto=bool(getattr(args, "auto", False)),
        max_iter=int(getattr(args, "react_max_iter", 15) or 15),
        graph_rag=_graph_rag_activo(args),
        mostrar_razonamiento=bool(getattr(args, "mostrar_razonamiento", False)),
        sesion_docker=bool(getattr(args, "sandbox_session", False)),
        web_interactive=bool(getattr(args, "web_interactive", False)),
        browser=bool(getattr(args, "browser", False)),
        prompt_caching=getattr(args, "prompt_caching",
                               PROMPT_CACHING_DEFECTO),
        lsp=bool(getattr(args, "lsp", False)),
        # v6.20.0: commits autom·ticos por acciÛn (git profundo).
        git_commit=bool(getattr(args, "git_commit", True)),
        git_mensaje=getattr(args, "git_mensaje", None),
    )
    # v6.10.0: activar el modo navegador si se pidiÛ --browser. La sesiÛn
    # (navegador headless persistente) se cierra al terminar la tarea.
    if bool(getattr(args, "browser", False)):
        try:
            import mcp_tools_browser as btool
            btool.browser_activar(
                headless=not bool(getattr(args, "browser_headed", False)))
            info("?? Modo navegador activado (--browser).")
        except Exception as exc:                         # noqa: BLE001
            aviso(f"‚ö†Ô∏è No se pudo activar el modo navegador: {exc}")
    # v6.4.0: la sesiÛn Docker se crea de forma perezosa y se destruye con
    # total garantÌa al terminar el bucle ReAct (Èxito, aborto o excepciÛn).
    try:
        resultado = agente.ejecutar(args.consulta)
    finally:
        _destruir_sesion_si_aplica()
        # v6.10.0: liberar el navegador al terminar (Èxito, aborto o error).
        try:
            import mcp_tools_browser as _btool
            _btool.browser_cerrar()
        except Exception:                                # noqa: BLE001
            pass
    return 0 if resultado.get("ok") else 1


# ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
# v6.23.0 ‚Äî MODO INTELIGENTE POR DEFECTO (sin flags)
# ---------------------------------------------------------------------------
# Capa de "entrada": detecta la complejidad de la tarea y aplica defaults.
# NO toca la lÛgica del editor, planificador ni agente ReAct (solo flags).
# ---------------------------------------------------------------------------

# Flags que indican que el usuario ya eligiÛ un pipeline concreto. Ante
# cualquiera de ellos se respeta su elecciÛn (compatibilidad total) y NO se
# activa la detecciÛn autom·tica ni se sobrescriben defaults.
_FLAGS_MODO_EXPLICITO: Tuple[str, ...] = (
    "plan", "react", "auto",
    "multi_agent", "sub_agents",
    "test_loop", "server_loop", "manual_loop",
    "vista_previa", "experto",
    "asesor", "asesor_auto", "asesor_profundo",
    "iniciar_proyecto",
)

# RaÌces verbales para la detecciÛn heurÌstica de intenciÛn (cubren la
# conjugaciÛn: "arreglar/arregla/arreglo", "aÒadir/aÒade", ...).
_PALABRAS_EDITAR: Tuple[str, ...] = (
    "arregl", "corrig", "correg", "refactoriz",
    "aÒad", "anad", "cambi", "elimin",
)
_PALABRAS_LEER: Tuple[str, ...] = (
    "analiz", "revis", "leer", "audit", "explor",
)
_PALABRAS_MULTIPASO: Tuple[str, ...] = (
    "luego", "despuÈs", "despues", "entonces",
    "primero", "finalmente", "pasos", "posteriormente",
)
def _detectar_modo_operacion(consulta: Optional[str],
                             args: argparse.Namespace) -> dict:
    """Detecta la complejidad de la tarea y elige el modo (v6.23.0).

    Devuelve un dict ``{"modo", "razon", "flags_extra"}``:

      - ``modo``        ‚Üí ``"chat"`` | ``"plan"`` | ``"react"`` |
                          ``"react_paralelo"`` | ``None``.
      - ``razon``       ‚Üí explicaciÛn legible de la decisiÛn (mensaje ``??``).
      - ``flags_extra`` ‚Üí flags sugeridos (p. ej. ``{"paralelo": 3}``).

    Prioridades (compatibilidad):
      * Si el usuario ya usÛ flags de pipeline explÌcitos
        (``--plan``/``--react``/``--auto``/...) devuelve ``modo=None`` y no se
        sobrescribe nada.
      * Consulta que pide editar (arreglar/corregir/... ‚Üí plan).
      * Consulta que pide analizar/revisar/leer ‚Üí react (herramientas lectura).
      * Consulta larga (>50 palabras) o multi-paso ‚Üí react_paralelo.
      * Consulta corta (<20 palabras) sin ediciÛn ‚Üí chat (ReAct simple).
    """
    if consulta is None:
        consulta = ""
    consulta = str(consulta).strip()
    for flag in _FLAGS_MODO_EXPLICITO:
        if bool(getattr(args, flag, False)):
            return {"modo": None,
                    "razon": ("Se detectaron flags explÌcitos; se respeta la "
                              "elecciÛn del usuario."),
                    "flags_extra": {}}
    if not consulta:
        return {"modo": None,
                "razon": "Sin consulta; no se aplica detecciÛn.",
                "flags_extra": {}}
    baja = consulta.lower()
    num_palabras = len(consulta.split())

    # 1) EdiciÛn de archivos ‚Üí planificador.
    if any(p in baja for p in _PALABRAS_EDITAR):
        return {"modo": "plan",
                "razon": "La consulta pide modificar/editar el cÛdigo.",
                "flags_extra": {"plan": True, "auto": True}}

    # 2) Lectura / an·lisis ‚Üí ReAct con herramientas de lectura.
    if any(p in baja for p in _PALABRAS_LEER):
        return {"modo": "react",
                "razon": "La consulta pide analizar/revisar (modo lectura).",
                "flags_extra": {}}

    # 3) Larga (>50 palabras) o con varios pasos ‚Üí paralelismo.
    if num_palabras > 50 or any(p in baja for p in _PALABRAS_MULTIPASO):
        return {"modo": "react_paralelo",
                "razon": ("Tarea extensa o con varios pasos; se usar· "
                          "paralelismo (--paralelo 3)."),
                "flags_extra": {"paralelo": 3}}

    # 4) Corta (<20 palabras) y sin ediciÛn ‚Üí chat simple (ReAct).
    if num_palabras < 20:
        return {"modo": "chat",
                "razon": "Consulta corta; conversaciÛn/ReAct simple.",
                "flags_extra": {}}

    return {"modo": "react",
            "razon": "Consulta de complejidad media; ReAct est·ndar.",
            "flags_extra": {}}
def _configurar_comportamiento_por_defecto(
        args: argparse.Namespace) -> argparse.Namespace:
    """Aplica defaults inteligentes sobre ``args`` (v6.23.0).

    - ``--local``                 si no hay API key configurada.
    - ``--mostrar-razonamiento``  por defecto (para usuarios nuevos).
    - ``--auto`` (+``--plan``)    si el modo detectado es ``"plan"``.
    - ``--paralelo 3``            si el modo detectado es ``"react_paralelo"``.

    Respeta los flags explÌcitos: si ``_detectar_modo_operacion`` devuelve
    ``modo=None`` devuelve ``args`` intacto (compatibilidad total).
    """
    det = _detectar_modo_operacion(getattr(args, "consulta", None), args)
    modo = det.get("modo")
    if not modo:
        return args
    extra = det.get("flags_extra") or {}
    hay_api = hay_api_key_configurada()

    # 1) --local si no hay clave de API.
    if not hay_api and not bool(getattr(args, "local", False)):
        args.local = True
        extra["local"] = True

    # 2) --mostrar-razonamiento por defecto (salvo que el usuario lo active).
    if not bool(getattr(args, "mostrar_razonamiento", False)):
        args.mostrar_razonamiento = True
        extra["mostrar_razonamiento"] = True

    # 3) modo plan ‚Üí enrutar al planificador y ejecutar sin confirmar por paso.
    if modo == "plan":
        if not bool(getattr(args, "plan", False)):
            args.plan = True
            extra.setdefault("plan", True)
        if not bool(getattr(args, "auto", False)):
            args.auto = True
            extra.setdefault("auto", True)

    # 4) modo react_paralelo ‚Üí --paralelo 3.
    if modo == "react_paralelo":
        actual = getattr(args, "paralelo", None)
        if actual is None or int(actual) <= 1:
            args.paralelo = 3
            extra.setdefault("paralelo", 3)

    # Marcadores internos (consumidos por la capa de presentaciÛn y por el
    # planificador para mostrar el resumen condensado sin cambiar su lÛgica).
    args._modo_inteligente = True
    args._modo_detectado = modo
    args._modo_razon = det.get("razon", "")
    args._flags_extra = extra
    return args


def _mostrar_plan_resumido(plan: Optional[list]) -> str:
    """Devuelve un resumen legible del plan en 3-5 lÌneas (v6.23.0).

    En lugar de listar el plan completo, genera una frase compacta
    ``"Voy a: 1) leer el login, 2) corregir el error, ..."``; si hay m·s de 5
    pasos aÒade ``"y N m·s"``. Devuelve ``""`` si el plan est· vacÌo.
    """
    if not plan:
        return ""
    pasos = list(plan)[:5]
    trozos: List[str] = []
    for i, paso in enumerate(pasos, start=1):
        if isinstance(paso, dict):
            desc = paso.get("descripcion") or paso.get("comando") or ""
            desc = str(desc).strip()
        else:
            desc = str(paso).strip()
        trozos.append(f"{i}) {desc}".strip())
    resumen = ", ".join(t for t in trozos if t)
    resto = len(list(plan)) - len(pasos)
    if resto > 0:
        resumen += f" y {resto} m·s"
    return f"Voy a: {resumen}"


def _aplicar_modo_inteligente(args: argparse.Namespace) -> argparse.Namespace:
    """v6.23.0: punto de entrada del modo inteligente (capa de "entrada").

    Solo act˙a cuando ``SNAPCONTEXT_MODO_DEFAULT`` ‚â† ``manual`` y el usuario
    no eligiÛ un pipeline explÌcito (flag de ``_FLAGS_MODO_EXPLICITO``).
    Aplica los defaults inteligentes y muestra los mensajes descriptivos.
    """
    modo_env = (os.environ.get("SNAPCONTEXT_MODO_DEFAULT", "inteligente")
                or "inteligente").strip().lower()
    if modo_env == "manual":
        return args
    # Flags de pipeline explÌcitos ‚Üí compatibilidad total (sin cambios).
    for flag in _FLAGS_MODO_EXPLICITO:
        if bool(getattr(args, flag, False)):
            return args
    if not getattr(args, "consulta", None):
        return args
    args = _configurar_comportamiento_por_defecto(args)
    if getattr(args, "_modo_inteligente", False):
        info(f"?? Modo inteligente activado (detectado: "
             f"{args._modo_detectado}). Escribe /ayuda para ver comandos.")
        if getattr(args, "_modo_razon", ""):
            info(f"?? {args._modo_razon}")
    return args
def _ejecutar_modo_tarea(args: argparse.Namespace) -> int:
    """Resuelve el modo de ejecuciÛn de la tarea (v5.2.0).

    - ``--plan``     ‚Üí planificador est·tico (**modo legacy**, mantenido para
      compatibilidad con scripts existentes).
    - Por defecto    ‚Üí motor ReAct (razonamiento din·mico). El flag
      ``--react`` sigue acept·ndose pero ya es redundante.
    - Sin consulta   ‚Üí flujo cl·sico (`flujo_principal`), que valida la
      entrada y muestra la ayuda amigable si falta la consulta.
    """
    if bool(getattr(args, "plan", False)):
        return _ejecutar_planificador(args)          # legacy explÌcito
    # v6.0.0: multi-agente (--multi-agent o SNAPCONTEXT_MULTI_AGENT=1) gana
    # sobre ReAct, que sigue siendo el modo por defecto para el resto.
    if _multi_agent_activo(getattr(args, "multi_agent", None) or None):
        return _ejecutar_multi_agent(args)
    # v5.2.0: ReAct es el modo por defecto (--react es redundante aquÌ).
    if getattr(args, "react", False) or getattr(args, "consulta", None):
        return _ejecutar_react(args)
    return flujo_principal(args)


def _aprender_regla_en_fondo(consulta: str, resultados: list,
                             raiz: str = ".") -> Optional[threading.Thread]:
    """Extrae (en un hilo demonio) una regla abstracta de un plan exitoso.

    v6.6.0: usa ``skill_abstraction.extraer_regla`` (LLM con fallback
    heurÌstico), la guarda en la tabla ``reglas`` y, si supera el umbral de
    confianza, la inyecta en CLAUDE.md. Nunca lanza ni bloquea.

    Se omite si ``SKILLS_DINAMICOS`` est· desactivado o si se corre bajo un
    test runner (evita hilos con sqlite/imports nativos al cerrar el proceso).
    """
    if not SKILLS_DINAMICOS:
        return None
    _argv0 = (sys.argv[0] or "").lower()
    if any(x in _argv0 for x in ("unittest", "pytest", "py.test")):
        return None

    def _trabajo():
        try:
            import skill_abstraction as _sa
            info("?? Extrayendo regla abstracta del plan exitoso...")
            plan = {"tarea": consulta, "pasos": resultados}
            regla = _sa.extraer_regla(plan, {"directorio": raiz})
            regla = _sa.guardar_regla(regla, directorio=raiz)
            if regla:
                info("?? Nueva regla aprendida: "
                     f"{regla.get('patron', '')} "
                     f"(confianza: {regla.get('confianza', 1.0):.2f})")
                if float(regla.get("confianza", 0)) > \
                        _sa.UMBRAL_CONFIANZA_INYECCION:
                    if _sa.inyectar_en_claudemd(regla, raiz):
                        info("?? Regla inyectada en CLAUDE.md")
        except Exception as exc:         # noqa: BLE001 ‚Äî nunca romper
            depurar(f"[skills-dinamicos] extracciÛn fallÛ: {exc}")

    hilo = threading.Thread(target=_trabajo, daemon=True,
                            name="snap-skills-dinamicos")
    hilo.start()
    return hilo


def _ejecutar_planificador(args: argparse.Namespace) -> int:
    """Modo planificador (`snapcontext --plan "tarea"`, legacy desde v5.2.0).

    Flujo: generar plan con IA ‚Üí confirmaciÛn ‚Üí ejecuciÛn secuencial con men˙
    continuar/reintentar/saltar tras cada paso ‚Üí resumen final. Con
    ``--branch`` crea una rama antes de empezar y con ``--git-commit``
    (por defecto) commitea `paso: <descripciÛn>` tras cada paso exitoso.
    """
    global DEPURAR
    DEPURAR = getattr(args, "depurar", False)
    consulta = getattr(args, "consulta", None)
    if not consulta:
        error("El modo --plan necesita una consulta. Uso:\n"
              '  snapcontext --plan "aÒadir login con Google"')
        return 1

    directorio = getattr(args, "directorio", ".") or "."
    raiz = str(resolver_raiz(directorio))

    # Rama git opcional antes de empezar.
    rama = getattr(args, "branch", None)
    if rama and not _git_crear_rama(rama, raiz):
        return 1

    # 1) GeneraciÛn del plan (con un reintento si viene vacÌo/mal formado).
    pasos: List[dict] = []
    for _intento in range(2):
        try:
            pasos = _generar_plan(consulta,
                                  getattr(args, "provider", None),
                                  getattr(args, "modelo", None))
        except RuntimeError as exc:
            error(str(exc))
            return 1
        if pasos:
            break
        aviso("El plan vino vacÌo o mal formado; reintentando...")
    if not pasos:
        error("No se pudo obtener un plan v·lido del proveedor.")
        return 1

    # Modo autÛnomo (v0.17.0): sin confirmaciÛn inicial ni men˙ por paso;
    # reintentos autom·ticos de pasos fallidos.
    auto = bool(getattr(args, "auto", False))
    MAX_REINTENTOS_AUTO = 3
    if auto:
        exito(f"Modo autÛnomo (--auto): {len(pasos)} paso(s) se ejecutar·n "
              f"sin confirmaciones, con hasta {MAX_REINTENTOS_AUTO} "
              f"reintentos por paso. Permisos guardados en permisos.json "
              f"siguen aplic·ndose.")
        # v6.23.0: en modo inteligente se muestra un resumen condensado del
        # plan (??) en lugar de la lista completa, antes de ejecutarlo.
        if getattr(args, "_modo_inteligente", False):
            info(f"?? Plan: {_mostrar_plan_resumido(pasos)}")
    else:
        # 2) Mostrar el plan y pedir confirmaciÛn.
        exito(f"Plan generado ({len(pasos)} paso(s)):")
        for numero, paso in enumerate(pasos, start=1):
            extra = paso.get("comando") or ", ".join(paso.get("archivos", []))
            sufijo = f" ‚Üí {extra}" if extra else ""
            _emitir(sys.stdout, f"  {numero}. [{paso['accion']}] "
                                f"{paso['descripcion']}{sufijo}")
        if not _preguntar_si("\n¬øQuieres ejecutar estos pasos? (s/n): "):
            aviso("Plan cancelado por el usuario.")
            _destruir_sesion_si_aplica()
            return 0

    # 3) EjecuciÛn (v1.4.0): con --paralelo N (y --auto) se lanzan varios pasos
    # sin dependencias mutuas a la vez; en caso contrario, secuencial.
    _contexto_plan_reiniciar()   # v2.3.0: contexto din·mico por plan
    # v6.22.0: hook `session_start` ‚Äî inicio de sesiÛn del planificador.
    try:
        _hooks.ejecutar_hook("session_start", {
            "modo": "planificador", "consulta": consulta,
            "pasos": len(pasos), "directorio": raiz})
    except Exception:                                # noqa: BLE001 ‚Äî nunca romper
        pass
    # v6.20.0: `--paralelo 0` = n¬∫ de n˙cleos de CPU (ParallelExecutor).
    _paralelo = getattr(args, "paralelo", 1)
    _paralelo = 1 if _paralelo is None else int(_paralelo)
    try:
        from parallel_executor import resolver_workers  # noqa: E402
        max_hilos = resolver_workers(_paralelo)
    except Exception:                                    # noqa: BLE001
        max_hilos = max(1, _paralelo)
    resultados: List[dict] = []
    abortar = False
    if auto and max_hilos > 1:
        exito(f"Modo --paralelo: hasta {max_hilos} paso(s) simult·neo(s).")
        resultados = _ejecutar_plan_en_paralelo(pasos, args, raiz, max_hilos)
    else:
        indice = 0
        abortar = False
        estado_seq: dict = {}   # Ìndice ‚Üí "Èxito"|"fallo"|"saltado"
        while indice < len(pasos) and not abortar:
            paso = pasos[indice]
            numero = indice + 1

            # v1.4.0: un paso solo se ejecuta si sus dependencias tuvieron Èxito.
            # 'dependencias' guarda n˙meros de paso (base 1); convertimos.
            deps_paso = [d - 1 for d in (paso.get("dependencias") or [])]
            fallidas = [d + 1 for d in deps_paso
                        if estado_seq.get(d) != "Èxito"]
            if fallidas:
                aviso(f"Paso {numero} saltado: dependencia(s) sin Èxito "
                      f"{fallidas}.")
                resultados.append(
                    {"paso": numero, "descripcion": paso["descripcion"],
                     "accion": paso["accion"], "resultado": "saltado",
                     "detalle": f"dependencia(s) sin Èxito: {fallidas}",
                     "intentos": 0})
                estado_seq[indice] = "saltado"
                indice += 1
                continue

            # v1.4.0: ejecuciÛn condicional del paso.
            condicion = paso.get("condicion")
            if condicion and not _evaluar_condicion(condicion, raiz):
                aviso(f"Paso {numero} saltado: condiciÛn no cumplida "
                      f"({condicion}).")
                resultados.append(
                    {"paso": numero, "descripcion": paso["descripcion"],
                     "accion": paso["accion"], "resultado": "saltado",
                     "detalle": f"condiciÛn no cumplida: {condicion}",
                     "intentos": 0})
                estado_seq[indice] = "saltado"
                indice += 1
                continue

            _emitir(sys.stdout, "")
            exito(f"Paso {numero}/{len(pasos)} [{paso['accion']}]: "
                  f"{paso['descripcion']}")
            intentos = 0
            while True:
                intentos += 1
                try:
                    ok, detalle = _ejecutar_paso_plan(paso, args, raiz)
                except Exception as exc:        # blindaje del bucle interactivo
                    ok, detalle = False, f"excepciÛn: {exc}"
                    error(f"El paso lanzÛ una excepciÛn: {exc}")
                if ok or not auto:
                    break
                if intentos < MAX_REINTENTOS_AUTO:
                    aviso(f"Paso {numero} fallÛ (intento {intentos}/"
                          f"{MAX_REINTENTOS_AUTO}); reintentando autom·ticamente‚Ä¶")
                else:
                    aviso(f"Paso {numero} agotÛ sus {MAX_REINTENTOS_AUTO} "
                          f"intentos; se contin˙a con el siguiente paso.")
                    break

            _registrar_resultado_plan(numero, ok, detalle)
            estado_seq[indice] = "Èxito" if ok else "fallo"
            if ok and getattr(args, "git_commit", True):
                _commit_paso(paso, args, raiz)

            if auto:
                # AutÛnomo: cada paso se registra una ˙nica vez (˙ltimo intento).
                resultados.append({"paso": numero,
                                   "descripcion": paso["descripcion"],
                                   "accion": paso["accion"],
                                   "resultado": "Èxito" if ok else "fallo",
                                   "detalle": detalle, "intentos": intentos})
                indice += 1
                continue

            # Interactivo: men˙ post-paso y registro ˙nico al abandonar el paso.
            while True:
                try:
                    eleccion = input(_pintar(
                        "[c]ontinuar ¬∑ [r]eintentar ¬∑ [s]altar ¬∑ [x]abortar "
                        "(c/r/s/x): ", _CYAN)).strip().lower()
                except EOFError:
                    eleccion = "c"
                if eleccion in ("", "c", "continuar"):
                    resultados.append(
                        {"paso": numero, "descripcion": paso["descripcion"],
                         "accion": paso["accion"],
                         "resultado": "Èxito" if ok else "fallo",
                         "detalle": detalle, "intentos": intentos})
                    indice += 1
                    break
                if eleccion in ("r", "reintentar"):
                    break                        # mismo Ìndice: repetir el paso
                if eleccion in ("s", "saltar"):
                    aviso(f"Paso {numero} saltado.")
                    estado_seq[indice] = "saltado"
                    resultados.append(
                        {"paso": numero, "descripcion": paso["descripcion"],
                         "accion": paso["accion"], "resultado": "fallo",
                         "detalle": "saltado por el usuario", "intentos": intentos})
                    indice += 1
                    break
                if eleccion in ("x", "abortar", "salir"):
                    aviso("Plan abortado por el usuario.")
                    abortar = True
                    break
                aviso("OpciÛn no v·lida; usa c, r, s o x.")

    # 4) Resumen final + memoria persistente.
    _emitir(sys.stdout, "")
    exito("‚îÄ‚îÄ Resumen del plan " + "‚îÄ" * 30)
    for r in resultados:
        marca = "‚úî" if r["resultado"] == "Èxito" else "‚úñ"
        reintentos = (f", {r['intentos']} intento(s)"
                      if r.get("intentos", 1) > 1 else "")
        _emitir(sys.stdout,
                f"  {marca} Paso {r['paso']} [{r['accion']}] "
                f"{r['descripcion']} ({r['resultado']}: {r['detalle']}"
                f"{reintentos})")
    saltados = len(pasos) - len(resultados)
    if saltados > 0:
        aviso(f"{saltados} paso(s) sin ejecutar (saltados o abortados).")
    exitos = sum(1 for r in resultados if r["resultado"] == "Èxito")
    exito(f"Resultado: {exitos}/{len(resultados)} paso(s) exitoso(s).")

    todo_ok = bool(resultados) and exitos == len(resultados) and saltados == 0
    _guardar_historial({
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "consulta": consulta,
        "archivos": [],
        "resultado": "Èxito" if todo_ok else ("fallo" if exitos == 0 else "parcial"),
        "duracion": round(len(resultados), 2),
        "tipo": "plan",
        "pasos": resultados,
    })

    # Aprendizaje continuo (v3.0.0): registrar la tarea y generar/reforzar
    # skills. Desactivable con --sin-aprendizaje. Nunca rompe el planificador.
    if not getattr(args, "sin_aprendizaje", False):
        try:
            _aprender_de_tarea(
                consulta, todo_ok, resultados, raiz=str(raiz),
                detalle=("plan: " + str(exitos) + "/"
                         + str(len(resultados)) + " pasos"))
        except Exception as exc:
            aviso(f"[aprendizaje] No se pudo registrar la tarea ({exc})")

    # Skills din·micos (v6.6.0): si el plan fue todo exitoso, extraer una
    # regla abstracta en segundo plano (nunca bloquea al usuario).
    if todo_ok and SKILLS_DINAMICOS and not getattr(
            args, "sin_aprendizaje", False):
        _aprender_regla_en_fondo(consulta, resultados, str(raiz))

    # Memoria de proyecto (v0.15.0): tras un plan exitoso se propone (con
    # confirmaciÛn) actualizar CLAUDE.md con lo aprendido.
    if todo_ok and MEMORIA_PROYECTO:
        resumen = "; ".join(
            f"{r['descripcion']} [{r['accion']}] ({r['resultado']})"
            for r in resultados)
        _actualizar_claude_md_automatico(resumen, raiz)
    # v6.4.0: al terminar el plan (Èxito, aborto o error) se destruye la sesiÛn
    # Docker persistente para no dejar contenedores huÈrfanos.
    # v6.22.0: hook `session_end` ‚Äî cierre de sesiÛn del planificador.
    try:
        _hooks.ejecutar_hook("session_end", {
            "modo": "planificador", "consulta": consulta,
            "resultado": "Èxito" if todo_ok else ("abortado" if abortar else "fallo"),
            "exitos": exitos, "total": len(resultados)})
    except Exception:                                # noqa: BLE001 ‚Äî nunca romper
        pass
    _destruir_sesion_si_aplica()
    return 0 if todo_ok or abortar else 1


# ---------------------------------------------------------------------------
# Permisos y confirmaciones (--confirmar / --no-confirmar) ‚Äî v0.13.0
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Memoria persistente avanzada (SQLite) y aprendizaje autÛnomo ‚Äî v3.0.0
# ---------------------------------------------------------------------------
# Sustituye/complementa el historial JSON con una base de datos robusta en
# ~/.snapcontext/memoria.db. sqlite3 forma parte de la stdlib: sin dependencias.
import sqlite3
import datetime

DB_PATH = CONFIG_DIR / "memoria.db"
_DB_CONEXION = None                    # conexiÛn singleton (check_same_thread=False)
_CANDADO_DB = threading.RLock()        # reentrante: _db_insert ‚Üí _db ‚Üí _db_init


def _db_init() -> str:
    """Crea la base de datos y sus tablas si no existen. Devuelve la ruta.

    Tablas:
      skills               ‚Üí procedimientos reutilizables aprendidos.
      historial_aprendizaje ‚Üí tareas completadas (Èxito/fallo/correcciones).
      contexto_kv          ‚Üí preferencias del usuario y metadatos (clave/valor).
      cola                 ‚Üí skills pendientes de ejecutar por el daemon.
    """
    ruta = Path(DB_PATH)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with _CANDADO_DB:
        global _DB_CONEXION
        if _DB_CONEXION is None:
            _DB_CONEXION = sqlite3.connect(
                str(ruta), check_same_thread=False)
            _DB_CONEXION.row_factory = sqlite3.Row
            _DB_CONEXION.execute("PRAGMA journal_mode=WAL")
        _DB_CONEXION.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre         TEXT UNIQUE NOT NULL,
                consulta       TEXT NOT NULL,
                descripcion    TEXT DEFAULT '',
                pasos_json     TEXT DEFAULT '[]',
                contexto_json  TEXT DEFAULT '{}',
                creado         TEXT NOT NULL,
                ultimo_exito   TEXT DEFAULT '',
                usos           INTEGER DEFAULT 0,
                exitos         INTEGER DEFAULT 0,
                fallos         INTEGER DEFAULT 0,
                tokens_promedio  INTEGER DEFAULT 0,
                tiempo_promedio_ms INTEGER DEFAULT 0,
                ultimo_uso     TEXT DEFAULT '',
                version        INTEGER DEFAULT 1,
                activo         INTEGER DEFAULT 1,
                confiabilidad  REAL DEFAULT 0.5,
                archivado      INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS historial_skills (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id   INTEGER NOT NULL,
                version    INTEGER DEFAULT 1,
                prompt     TEXT DEFAULT '',
                motivo     TEXT DEFAULT 'refactorizado',
                fecha      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS historial_aprendizaje (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                consulta TEXT NOT NULL,
                exito    INTEGER NOT NULL,
                detalle  TEXT DEFAULT '',
                fecha    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contexto_kv (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cola (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id INTEGER NOT NULL,
                estado   TEXT DEFAULT 'pendiente',
                fecha    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reglas (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                patron             TEXT NOT NULL,
                accion             TEXT NOT NULL DEFAULT '',
                archivos_afectados TEXT,
                dependencias       TEXT,
                confianza          REAL DEFAULT 1.0,
                usos               INTEGER DEFAULT 0,
                creado             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        _DB_CONEXION.commit()
        _db_migrar_curador()
        _db_migrar_reglas()
        _db_migrar_tareas()
        _db_migrar_pasos()
    return str(ruta)


def _db_migrar_tareas() -> None:
    """MigraciÛn v6.8.0: crea la tabla ``tareas`` (cola de tareas asÌncronas) si falta.

    Idempotente: usa ``CREATE TABLE IF NOT EXISTS``, permitiendo que tareas de
    GitHub/Telegram/Discord se encolen y procesen en segundo plano.
    """
    _db_ejecutar(
        "CREATE TABLE IF NOT EXISTS tareas ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "tipo TEXT NOT NULL, "
        "estado TEXT NOT NULL, "
        "datos TEXT NOT NULL, "
        "resultado TEXT, "
        "chat_id TEXT, "
        "canal TEXT, "
        "creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "actualizado TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    _db_ejecutar(
        "CREATE INDEX IF NOT EXISTS idx_tareas_estado ON tareas(estado)")


def _db_migrar_reglas() -> None:
    """MigraciÛn v6.6.0: crea la tabla ``reglas`` (skills din·micos) si falta.

    Idempotente: usa ``CREATE TABLE IF NOT EXISTS``, de modo que las bases
    creadas antes de v6.6.0 se actualizan sin perder datos.
    """
    _db_ejecutar(
        "CREATE TABLE IF NOT EXISTS reglas ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "patron TEXT NOT NULL, "
        "accion TEXT NOT NULL DEFAULT '', "
        "archivos_afectados TEXT, "
        "dependencias TEXT, "
        "confianza REAL DEFAULT 1.0, "
        "usos INTEGER DEFAULT 0, "
        "creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")


def _db_migrar_curador() -> None:
    """MigraciÛn v5.0.0: aÒade las columnas de mÈtricas del curador proactivo.

    Las bases creadas antes de v5.0.0 no tienen `exitos`, `tokens_promedio`,
    `tiempo_promedio_ms`, `ultimo_uso`, `version` ni `activo`. Esta funciÛn
    aÒade SOLO las que falten con ``ALTER TABLE ... ADD COLUMN`` (idempotente).
    TambiÈn crea la tabla `historial_skills` para registrar el prompt previo
    cuando un skill se refactoriza (desactivando la versiÛn anterior).
    """
    _COLUMNAS_NUEVAS = {
        "exitos": "INTEGER DEFAULT 0",
        "tokens_promedio": "INTEGER DEFAULT 0",
        "tiempo_promedio_ms": "INTEGER DEFAULT 0",
        "ultimo_uso": "TEXT DEFAULT ''",
        "version": "INTEGER DEFAULT 1",
        "activo": "INTEGER DEFAULT 1",
    }
    existen = {fila["name"] for fila in
               _db_query("PRAGMA table_info(skills)")}
    for columna, definicion in _COLUMNAS_NUEVAS.items():
        if columna not in existen:
            _db_ejecutar(
                f"ALTER TABLE skills ADD COLUMN {columna} {definicion}")
    _db_ejecutar(
        "CREATE TABLE IF NOT EXISTS historial_skills ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "skill_id INTEGER NOT NULL, "
        "version INTEGER DEFAULT 1, "
        "prompt TEXT DEFAULT '', "
        "motivo TEXT DEFAULT 'refactorizado', "
        "fecha TEXT NOT NULL)")


def _db():
    """Devuelve la conexiÛn activa, inicializando la base si hace falta."""
    if _DB_CONEXION is None:
        _db_init()
    return _DB_CONEXION


def _db_cerrar() -> None:
    """Cierra la conexiÛn (˙til en tests tras re-apuntar DB_PATH)."""
    global _DB_CONEXION
    with _CANDADO_DB:
        if _DB_CONEXION is not None:
            try:
                _DB_CONEXION.close()
            except Exception:
                pass
            _DB_CONEXION = None


def _db_query(sql: str, params: tuple = ()) -> List[dict]:
    """Ejecuta un SELECT y devuelve las filas como lista de diccionarios."""
    filas = _db().execute(sql, params).fetchall()
    return [dict(f) for f in filas]


def _db_insert(sql: str, params: tuple = ()) -> int:
    """Ejecuta un INSERT y devuelve el rowid generado."""
    with _CANDADO_DB:
        cursor = _db().execute(sql, params)
        _db().commit()
        return int(cursor.lastrowid)


def _db_ejecutar(sql: str, params: tuple = ()) -> int:
    """Ejecuta UPDATE/DELETE y devuelve el n˙mero de filas afectadas."""
    with _CANDADO_DB:
        cursor = _db().execute(sql, params)
        _db().commit()
        return cursor.rowcount


def _kv_obtener(clave: str, defecto: str = "") -> str:
    """Lee un valor del contexto persistente (tabla contexto_kv)."""
    filas = _db_query("SELECT valor FROM contexto_kv WHERE clave = ?",
                      (clave,))
    return filas[0]["valor"] if filas else defecto


def _kv_fijar(clave: str, valor: str) -> None:
    """Guarda (o actualiza) un valor del contexto persistente."""
    _db_ejecutar(
        "INSERT INTO contexto_kv (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, valor))


# ‚îÄ‚îÄ‚îÄ Skills: procedimientos reutilizables ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
def _skill_normalizar_nombre(consulta: str, max_len: int = 60) -> str:
    """Genera un nombre estable a partir de la consulta del usuario.

    Translitera acentos y eÒes (·‚Üía, Ò‚Üín) para que el nombre sea estable
    e independiente del teclado del usuario.
    """
    texto = unicodedata.normalize("NFD", consulta.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", "-", texto).strip("-")
    return (texto[:max_len].rstrip("-")) or "skill-sin-nombre"


def _skill_guardar(nombre: str, consulta: str, pasos: List[dict],
                   contexto: Optional[dict] = None,
                   descripcion: str = "") -> int:
    """Inserta (o actualiza) un skill y devuelve su id.

    Si ya existe un skill con el mismo nombre se actualiza en lugar de
    duplicarlo (idempotencia).
    """
    ahora = time.strftime("%Y-%m-%dT%H:%M:%S")
    existente = _db_query(
        "SELECT id FROM skills WHERE nombre = ?", (nombre,))
    pasos_json = json.dumps(pasos, ensure_ascii=False)
    contexto_json = json.dumps(contexto or {}, ensure_ascii=False)
    if existente:
        sid = int(existente[0]["id"])
        _db_ejecutar(
            "UPDATE skills SET consulta = ?, descripcion = ?, "
            "pasos_json = ?, contexto_json = ? WHERE id = ?",
            (consulta, descripcion, pasos_json, contexto_json, sid))
        return sid
    return _db_insert(
        "INSERT INTO skills (nombre, consulta, descripcion, pasos_json, "
        "contexto_json, creado, ultimo_exito, usos, fallos, confiabilidad, "
        "archivado) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0.5, 0)",
        (nombre, consulta, descripcion, pasos_json, contexto_json,
         ahora, ahora))


def _skill_obtener(skill_id: int) -> Optional[dict]:
    """Devuelve un skill como diccionario (con pasos/contexto parseados)."""
    filas = _db_query("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if not filas:
        return None
    skill = dict(filas[0])
    try:
        skill["pasos"] = json.loads(skill.get("pasos_json") or "[]")
    except json.JSONDecodeError:
        skill["pasos"] = []
    try:
        skill["contexto"] = json.loads(skill.get("contexto_json") or "{}")
    except json.JSONDecodeError:
        skill["contexto"] = {}
    return skill


# ‚îÄ‚îÄ‚îÄ Skills del editor propio: patrones de ediciÛn (v3.3.0) ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
_EDITOR_PATRONES = (
    ("renombrar", re.compile(
        r"\b(renombrar|rename|cambia(r| el nombre)( de| la)? (la )?(funciÛn|"
        r"funcion|variable|clase|mÈtodo|metodo))\b", re.IGNORECASE)),
    ("aÒadir_import", re.compile(
        r"\b(a[nÒ]ad(i|ir)|importar|import|agregar)\s+(el\s+|la\s+)?"
        r"(import|mÛdulo|modulo|librerÌa|libreria|paquete)\b", re.IGNORECASE)),
    ("refactorizar_clase", re.compile(
        r"\b(refactoriza(r)?|reestructura|rdivide|extraer\s+clase|"
        r"reorganiza(r)?)\b.*\b(clase|class|mÛdulo|modulo)\b", re.IGNORECASE)),
    ("aÒadir_funcion", re.compile(
        r"\b(a[nÒ]ade?|a[nÒ]adir|crear?|agrega(r)?)\s+(una?\s+)?"
        r"(funciÛn|funcion|funciÛn nueva|nueva funci|nuevo m[Èe]todo|"
        r"m[Èe]todo)\b", re.IGNORECASE)),
    ("corregir_error", re.compile(
        r"\b(arregla|r|corrige|fix|bug|error|fallo|excepci[oÛ]n)\b",
        re.IGNORECASE)),
)


def _editor_clasificar_tarea(tarea: str) -> str:
    """Clasifica una tarea de ediciÛn en un patrÛn conocido (v3.3.0).

    Devuelve uno de: 'renombrar', 'aÒadir_import', 'refactorizar_clase',
    'aÒadir_funcion', 'corregir_error' o 'general'.
    """
    texto = (tarea or "").strip()
    if not texto:
        return "general"
    for patron, regex in _EDITOR_PATRONES:
        if regex.search(texto):
            return patron
    return "general"


def _skill_editor_guardar(tarea: str, archivo: str, patron: str,
                          estrategia: str = "parche") -> Optional[int]:
    """Guarda/actualiza un skill con el patrÛn de ediciÛn exitoso (v3.3.0).

    Idempotente por nombre (`editor-<patrÛn>`): si ya existe se actualiza.
    Nunca lanza excepciones (los errores de memoria solo avisan).
    """
    try:
        return _skill_guardar(
            nombre=f"editor-{patron}",
            consulta=tarea or f"editar {archivo}",
            pasos=[{
                "descripcion": (f"EdiciÛn '{patron}' aplicada con Èxito "
                                f"sobre {archivo}"),
                "accion": "editor_propio",
                "estrategia": estrategia,
            }],
            contexto={"archivo": archivo, "patron": patron,
                      "estrategia": estrategia},
            descripcion=f"PatrÛn de ediciÛn del editor propio: {patron}")
    except Exception as exc:                   # pragma: no cover
        depurar(f"[skills-editor] No se pudo guardar el skill: {exc}")
        return None


def _skill_editor_estrategia(tarea: str, umbral: float = 0.6) -> Optional[str]:
    """Busca un skill de ediciÛn previo y devuelve su estrategia (v3.3.0).

    Permite que el editor propio aplique directamente la estrategia que ya
    funcionÛ para tareas similares, sin pasar por el proveedor de IA.
    Solo se aceptan skills de editor no archivados y con confiabilidad >= 0.6.
    """
    try:
        skill = _skill_buscar(f"editor {(tarea or '').strip()}", umbral=umbral)
    except Exception as exc:
        depurar(f"[skills-editor] B˙squeda fallÛ: {exc}")
        return None
    if not skill or not str(skill.get("nombre", "")).startswith("editor-"):
        return None
    if float(skill.get("confiabilidad") or 0) < 0.6:
        return None
    for paso in skill.get("pasos") or []:
        estrategia = paso.get("estrategia")
        if estrategia in ("parche", "sobrescribir", "ast"):
            depurar(f"[skills-editor] Reutilizando estrategia "
                    f"'{estrategia}' del skill #{skill.get('id')}.")
            return estrategia
    return None


def _skill_listar(incluir_archivados: bool = False,
                  solo_confiables: bool = False) -> List[dict]:
    """Lista skills ordenados por confiabilidad descendente."""
    sql = ("SELECT id, nombre, consulta, descripcion, creado, ultimo_exito, "
           "usos, fallos, confiabilidad, archivado FROM skills")
    condiciones = []
    if not incluir_archivados:
        condiciones.append("archivado = 0")
    if solo_confiables:
        condiciones.append("confiabilidad >= 0.9 AND usos >= 3")
    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)
    sql += " ORDER BY confiabilidad DESC, usos DESC, id DESC"
    return _db_query(sql)


def _skill_registrar_exito(skill_id: int, tokens: int = 0,
                           tiempo_ms: int = 0) -> float:
    """Refuerza un skill tras un uso exitoso. Devuelve la nueva confiabilidad.

    Con 3+ usos sin fallos el skill se considera 'confiable' (confiabilidad
    1.0) y el planificador lo prioriza. v5.0.0: tambiÈn actualiza las mÈtricas
    del curador proactivo (`exitos`, `tokens_promedio`, `tiempo_promedio_ms`,
    `ultimo_uso`).
    """
    ahora = time.strftime("%Y-%m-%dT%H:%M:%S")
    _db_ejecutar(
        "UPDATE skills SET usos = usos + 1, exitos = exitos + 1, "
        "ultimo_exito = ?, ultimo_uso = ?, "
        "confiabilidad = MIN(1.0, confiabilidad + 0.15), "
        "tokens_promedio = CASE WHEN usos = 0 THEN ? "
        "ELSE (tokens_promedio * usos + ?) / (usos + 1) END, "
        "tiempo_promedio_ms = CASE WHEN usos = 0 THEN ? "
        "ELSE (tiempo_promedio_ms * usos + ?) / (usos + 1) END "
        "WHERE id = ?",
        (ahora, ahora, tokens, tokens, tiempo_ms, tiempo_ms, skill_id))
    _db_ejecutar(
        "UPDATE skills SET confiabilidad = 1.0 "
        "WHERE id = ? AND usos >= 3 AND fallos = 0", (skill_id,))
    filas = _db_query("SELECT confiabilidad FROM skills WHERE id = ?",
                      (skill_id,))
    return float(filas[0]["confiabilidad"]) if filas else 0.5


def _skill_registrar_fallo(skill_id: int, tokens: int = 0,
                           tiempo_ms: int = 0) -> float:
    """Penaliza un skill tras un fallo. Devuelve la nueva confiabilidad.

    A partir de 2 fallos la confiabilidad cae por debajo de 0.4 y el skill
    queda marcado para revisiÛn por el curador/agente. v5.0.0: tambiÈn
    actualiza las mÈtricas del curador proactivo.
    """
    ahora = time.strftime("%Y-%m-%dT%H:%M:%S")
    _db_ejecutar(
        "UPDATE skills SET usos = usos + 1, fallos = fallos + 1, "
        "ultimo_uso = ?, "
        "confiabilidad = MAX(0.0, confiabilidad - 0.25), "
        "tokens_promedio = CASE WHEN usos = 0 THEN ? "
        "ELSE (tokens_promedio * usos + ?) / (usos + 1) END, "
        "tiempo_promedio_ms = CASE WHEN usos = 0 THEN ? "
        "ELSE (tiempo_promedio_ms * usos + ?) / (usos + 1) END "
        "WHERE id = ?",
        (ahora, tokens, tokens, tiempo_ms, tiempo_ms, skill_id))
    filas = _db_query("SELECT confiabilidad FROM skills WHERE id = ?",
                      (skill_id,))
    return float(filas[0]["confiabilidad"]) if filas else 0.5


def _skill_similitud(texto_a: str, texto_b: str) -> float:
    """Similitud [0..1] entre dos textos.

    Usa embeddings (coseno) si sentence-transformers est· disponible; si no,
    cae a similitud Jaccard de palabras (fallo elegante, cero dependencias).
    """
    modelo = _modelo_embeddings()
    if modelo is not None:
        try:
            import numpy as _np  # type: ignore
            vectores = modelo.encode([texto_a.lower(), texto_b.lower()])
            va = _np.asarray(vectores[0])
            vb = _np.asarray(vectores[1])
            denom = float(_np.linalg.norm(va) * _np.linalg.norm(vb))
            if denom == 0:
                return 0.0
            return max(0.0, min(1.0, float(_np.dot(va, vb)) / denom))
        except Exception:
            pass
    palabras_a = set(re.findall(r"\w+", texto_a.lower()))
    palabras_b = set(re.findall(r"\w+", texto_b.lower()))
    if not palabras_a or not palabras_b:
        return 0.0
    interseccion = len(palabras_a & palabras_b)
    union = len(palabras_a | palabras_b)
    jaccard = interseccion / union if union else 0.0
    # ContenciÛn: captura frases donde una contiene a la otra ("... ya",
    # "... ahora"), que Jaccard penaliza en exceso. Exige >= 2 palabras en
    # com˙n para evitar falsos positivos con consultas muy cortas.
    contencion = (interseccion / min(len(palabras_a), len(palabras_b))
                  if interseccion >= 2 else 0.0)
    return min(1.0, max(jaccard, contencion))


def _skill_buscar(consulta: str, umbral: float = 0.75) -> Optional[dict]:
    """Busca el skill activo m·s similar a ``consulta``.

    Compara con similitud sem·ntica (embeddings o Jaccard como fallback)
    contra las consultas de los skills no archivados. Devuelve el skill
    completo con su campo extra 'similitud', o None si ninguno supera el
    umbral.
    """
    candidatos = _db_query(
        "SELECT * FROM skills WHERE archivado = 0 ORDER BY usos DESC")
    mejor = None
    for fila in candidatos:
        sim = _skill_similitud(consulta, fila["consulta"])
        if mejor is None or sim > mejor["similitud"]:
            skill = dict(fila)
            skill["similitud"] = sim
            mejor = skill
    if mejor is None or mejor["similitud"] < umbral:
        return None
    completo = _skill_obtener(int(mejor["id"]))
    if completo is not None:
        completo["similitud"] = mejor["similitud"]
    return completo


def _skill_generar(consulta: str, resultados: List[dict],
                   raiz: str = ".") -> Optional[int]:
    """Genera un skill a partir de una tarea completada con Èxito.

    Extrae los pasos clave de ``resultados`` (del planificador). Si hay
    proveedor de IA disponible, pide una descripciÛn breve; si no, construye
    el skill directamente de los resultados (modo local, sin red).
    Devuelve el id del skill o None si no hay material suficiente.
    """
    pasos_utiles = []
    for r in resultados or []:
        paso = {"descripcion": r.get("descripcion") or "",
                "accion": r.get("accion") or ""}
        if r.get("comando"):
            paso["comando"] = r["comando"]
        if r.get("archivos"):
            paso["archivos"] = r["archivos"]
        if paso["descripcion"] and paso["accion"]:
            pasos_utiles.append(paso)
    if not pasos_utiles:
        return None

    descripcion = ""
    try:
        resumen = "; ".join(
            f"{r.get('descripcion')} [{r.get('accion')}]"
            for r in resultados or [])
        respuesta = _enviar_al_proveedor(
            "Resume en UNA frase corta que hace este procedimiento de "
            "desarrollo (sin detalles de archivos): " + resumen,
            args=None)
        if respuesta and respuesta.strip():
            descripcion = respuesta.strip().splitlines()[0][:300]
    except Exception:
        descripcion = ""          # modo local / sin red: seguimos sin resumen

    nombre = _skill_normalizar_nombre(consulta)
    contexto = {"raiz": str(raiz),
                "fecha": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return _skill_guardar(nombre, consulta, pasos_utiles, contexto,
                          descripcion)


def _aprender_de_tarea(consulta: str, todo_ok: bool,
                       resultados: List[dict], raiz: str = ".",
                       detalle: str = "") -> Optional[int]:
    """Gancho central de aprendizaje continuo (v3.0.0).

    Registra la tarea en historial_aprendizaje y:
      - exito ‚Üí refuerza el skill similar existente o genera uno nuevo.
      - fallo ‚Üí penaliza el skill similar (queda marcado para revisiÛn).
    Devuelve el id del skill afectado/generado, o None.
    """
    _db_init()
    _db_insert(
        "INSERT INTO historial_aprendizaje (consulta, exito, detalle, fecha) "
        "VALUES (?, ?, ?, ?)",
        (consulta, 1 if todo_ok else 0, detalle,
         time.strftime("%Y-%m-%dT%H:%M:%S")))
    try:
        skill_previo = _skill_buscar(consulta, umbral=0.75)
    except Exception as exc:
        aviso(f"[aprendizaje] No se pudo buscar skills ({exc})")
        return None

    if todo_ok:
        if skill_previo is not None:
            conf = _skill_registrar_exito(int(skill_previo["id"]))
            depurar(f"[aprendizaje] Skill #{skill_previo['id']} reforzado "
                    f"(confiabilidad {conf:.2f})")
            id_skill = int(skill_previo["id"])
        else:
            id_skill = _skill_generar(consulta, resultados, raiz)
            if id_skill is not None:
                info(f"[aprendizaje] Nuevo skill guardado: "
                     f"{_skill_normalizar_nombre(consulta)} (#{id_skill})")
        _aprender_regla_en_fondo(consulta, resultados, raiz)
        return id_skill

    if skill_previo is not None:
        conf = _skill_registrar_fallo(int(skill_previo["id"]))
        aviso(f"[aprendizaje] Skill #{skill_previo['id']} marcado para "
              f"revision (confiabilidad {conf:.2f})")
        return int(skill_previo["id"])
    return None


def _cola_encolar(skill_id: int) -> int:
    """Encola un skill para ejecuciÛn en segundo plano por el daemon."""
    return _db_insert(
        "INSERT INTO cola (skill_id, estado, fecha) VALUES (?, 'pendiente', ?)",
        (skill_id, time.strftime("%Y-%m-%dT%H:%M:%S")))


# ‚îÄ‚îÄ‚îÄ Curador autÛnomo ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
CURADOR_DIAS_SIN_USO = 30          # skills sin uso > 30 dÌas ‚Üí archivados
CURADOR_UMBRAL_FUSION = 0.90       # similitud mÌnima para fusionar skills
CLAVE_CURADOR_ULTIMA = "curador_ultima_ejecucion"


def _curador_ejecutar(dias_sin_uso: int = CURADOR_DIAS_SIN_USO,
                      umbral_fusion: float = CURADOR_UMBRAL_FUSION) -> dict:
    """Ejecuta una pasada del curador. Devuelve un resumen de acciones.

    Acciones:
      - Archiva skills activos cuyo ultimo_exito (o creado) sea anterior a
        ``dias_sin_uso`` dÌas.
      - Fusiona pares de skills muy similares (sim >= ``umbral_fusion``):
        conserva el m·s usado sumando usos/fallos y archiva el otro.
      - Notifica por la CLI los skills con baja confiabilidad (revisiÛn).
    """
    _db_init()
    acciones = {"archivados": [], "fusiones": [], "revision": []}
    ahora = datetime.datetime.now()

    def _antiguedad_dias(valor):
        if not valor:
            return dias_sin_uso + 1.0     # nunca usado ‚Üí candidato
        try:
            fecha = datetime.datetime.fromisoformat(valor)
            return (ahora - fecha).total_seconds() / 86400.0
        except ValueError:
            return dias_sin_uso + 1.0

    # 1) Archivar skills sin uso reciente.
    for fila in _skill_listar():
        referencia = fila["ultimo_exito"] or fila["creado"]
        if _antiguedad_dias(referencia) > dias_sin_uso:
            _db_ejecutar("UPDATE skills SET archivado = 1 WHERE id = ?",
                         (fila["id"],))
            acciones["archivados"].append(
                {"id": fila["id"], "nombre": fila["nombre"]})

    # 2) Fusionar skills muy similares entre sÌ.
    activos = _skill_listar()
    vistos = set()
    for i, a in enumerate(activos):
        if a["id"] in vistos:
            continue
        for b in activos[i + 1:]:
            if b["id"] in vistos:
                continue
            try:
                sim = _skill_similitud(a["consulta"], b["consulta"])
            except Exception:
                continue
            if sim < umbral_fusion:
                continue
            conservar, fusionar = (
                (a, b) if a["usos"] >= b["usos"] else (b, a))
            _db_ejecutar(
                "UPDATE skills SET usos = usos + ?, fallos = fallos + ?, "
                "confiabilidad = MAX(confiabilidad, ?), "
                "archivado = 0 WHERE id = ?",
                (fusionar["usos"], fusionar["fallos"],
                 fusionar["confiabilidad"], conservar["id"]))
            _db_ejecutar(
                "UPDATE skills SET archivado = 1 WHERE id = ?",
                (fusionar["id"],))
            vistos.add(fusionar["id"])
            acciones["fusiones"].append({
                "conservado": conservar["id"],
                "archivado": fusionar["id"],
                "similitud": round(sim, 3)})

    # 3) Notificar skills marcados para revisiÛn (muchos fallos).
    for fila in _db_query(
            "SELECT id, nombre FROM skills "
            "WHERE archivado = 0 AND confiabilidad < 0.4"):
        acciones["revision"].append({"id": fila["id"],
                                     "nombre": fila["nombre"]})
    for r in acciones["revision"]:
        aviso("[curador] Skill #" + str(r["id"]) + " '" + r["nombre"]
              + "' tiene baja confiabilidad; revisar o regenerar.")

    _kv_fijar(CLAVE_CURADOR_ULTIMA, time.strftime("%Y-%m-%dT%H:%M:%S"))
    total = len(acciones["archivados"]) + len(acciones["fusiones"])
    if total:
        exito("[curador] " + str(len(acciones["archivados"]))
              + " skill(s) archivado(s), "
              + str(len(acciones["fusiones"])) + " fusion/fusiones.")
    else:
        depurar("[curador] Sin acciones necesarias.")
    return acciones


# ‚îÄ‚îÄ‚îÄ Daemon: proceso en segundo plano (--daemon) ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
DAEMON_INTERVALO_HORAS_DEFECTO = 168     # curador cada 7 dÌas
DAEMON_PAUSA_SEGUNDOS = 60               # frecuencia de sondeo del bucle


def _daemon_tick(intervalo_horas: int = DAEMON_INTERVALO_HORAS_DEFECTO,
                 ahora=None) -> dict:
    """Una iteraciÛn del daemon (funciÛn aislada para facilitar los tests).

    Ejecuta el curador si ha pasado ``intervalo_horas`` desde su ˙ltima
    pasada (registrada en contexto_kv) y procesa la cola de skills
    pendientes marc·ndolos como 'hecho' (o 'descartado').
    """
    _db_init()
    resultado = {"curador": False, "procesados": []}
    ultima = _kv_obtener(CLAVE_CURADOR_ULTIMA, "")
    vencido = True
    if ultima:
        try:
            fecha = datetime.datetime.fromisoformat(ultima)
            referencia = ahora or datetime.datetime.now()
            vencido = ((referencia - fecha).total_seconds()
                       >= intervalo_horas * 3600)
        except ValueError:
            vencido = True
    if vencido:
        info("[daemon] Ejecutando curador programado...")
        _curador_ejecutar()
        resultado["curador"] = True

    pendientes = _db_query(
        "SELECT id, skill_id FROM cola WHERE estado = 'pendiente' "
        "ORDER BY id LIMIT 10")
    for tarea in pendientes:
        skill = _skill_obtener(int(tarea["skill_id"]))
        if skill is None or skill.get("archivado"):
            _db_ejecutar(
                "UPDATE cola SET estado = 'descartado' WHERE id = ?",
                (tarea["id"],))
            continue
        _db_ejecutar("UPDATE cola SET estado = 'ejecutando' WHERE id = ?",
                     (tarea["id"],))
        depurar("[daemon] Skill #" + str(skill["id"]) + " '"
                + skill["nombre"] + "' listo para ejecuciÛn en segundo "
                "plano (" + str(len(skill.get("pasos") or [])) + " pasos)")
        _db_ejecutar("UPDATE cola SET estado = 'hecho' WHERE id = ?",
                     (tarea["id"],))
        resultado["procesados"].append(skill["id"])

    # v6.8.0: procesa tareas asÌncronas encoladas (GitHub/Telegram/Discord)
    try:
        import task_queue as _tq
        while True:
            t_res = _tq.procesar_siguiente_tarea()
            if not t_res:
                break
            resultado.setdefault("tareas_asincronas", []).append(t_res["id"])
    except Exception as exc:
        depurar(f"[daemon] Error procesando cola de tareas: {exc}")

    return resultado


def _daemon_bucle(intervalo_horas: int = DAEMON_INTERVALO_HORAS_DEFECTO,
                  pausa_segundos: int = DAEMON_PAUSA_SEGUNDOS) -> None:
    """Bucle principal del daemon (`snapcontext --daemon`)."""
    exito("Daemon iniciado (curador cada " + str(intervalo_horas)
       + " h, sondeo cada " + str(pausa_segundos) + " s). Ctrl+C para salir.")
    while True:
        try:
            _daemon_tick(intervalo_horas=intervalo_horas)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            aviso("[daemon] Error en tick (" + str(exc) + "); se reintenta.")
        time.sleep(pausa_segundos)


PERMISOS_PATH = CONFIG_DIR / "permisos.json"

# Interruptor global: main() lo sincroniza con args.confirmar (por defecto
# True). Con --no-confirmar todas las preguntas se omiten (modo autom·tico).
CONFIRMAR_ACCIONES = True


def _cargar_permisos() -> dict:
    """Devuelve las preferencias guardadas en ~/.snapcontext/permisos.json.

    Formato: {"<tipo>": "siempre" | "nunca"} para cada tipo de acciÛn
    ("editar", "ejecutar", "consultar", ...). Archivo corrupto ‚Üí {}.
    """
    try:
        if PERMISOS_PATH.is_file():
            datos = json.loads(PERMISOS_PATH.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                return {str(k): str(v) for k, v in datos.items()}
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"No se pudieron leer los permisos ({PERMISOS_PATH}): {exc}")
    return {}


def _guardar_permiso(tipo: str, valor: str) -> bool:
    """Guarda ``{"<tipo>": valor}`` en permisos.json (valor: siempre/nunca)."""
    try:
        permisos = _cargar_permisos()
        permisos[tipo] = valor
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PERMISOS_PATH.write_text(
            json.dumps(permisos, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        aviso(f"No se pudo guardar el permiso ({PERMISOS_PATH}): {exc}")
        return False


def _permiso_recordado(tipo: str) -> Optional[bool]:
    """Devuelve la preferencia guardada para ``tipo`` sin preguntar.

    True ‚Üí "siempre" permitido ¬∑ False ‚Üí "nunca" ¬∑ None ‚Üí sin preferencia.
    Lo usa el modo autÛnomo (--auto), que no puede preguntar pero sÌ debe
    respetar las decisiones previas del usuario en permisos.json.
    """
    recordado = _cargar_permisos().get(tipo)
    if recordado == "siempre":
        return True
    if recordado == "nunca":
        return False
    return None


def _limpiar_permisos() -> bool:
    """Borra ~/.snapcontext/permisos.json (todas las preferencias 't'/'a')."""
    try:
        if PERMISOS_PATH.exists():
            PERMISOS_PATH.unlink()
            exito(f"Permisos restablecidos ({PERMISOS_PATH} borrado).")
        else:
            info("No hay preferencias de permisos guardadas.")
        return True
    except OSError as exc:
        error(f"No se pudieron borrar los permisos: {exc}")
        return False


def _confirmar_accion(descripcion: str, tipo: str = "editar",
                      detalles: Optional[str] = None,
                      confirmar: Optional[bool] = None) -> bool:
    """Pide permiso al usuario antes de una acciÛn sensible.

    - Muestra un resumen (tipo, descripciÛn y detalles opcionales).
    - Respeta las preferencias guardadas en permisos.json:
      "siempre" ‚Üí permite sin preguntar; "nunca" ‚Üí deniega sin preguntar.
    - Pregunta ``¬øPermitir esta acciÛn? (s/n/t/a)`` donde:
        s ‚Üí permitir solo esta vez ¬∑ n ‚Üí saltar esta vez
        t ‚Üí permitir TODAS las de este tipo (se guarda)
        a ‚Üí no permitir NINGUNA de este tipo (se guarda)

    Devuelve True si la acciÛn est· permitida. Con confirmaciones desactivadas
    (``--no-confirmar`` o ``confirmar=False``) devuelve True siempre.
    """
    activo = CONFIRMAR_ACCIONES if confirmar is None else confirmar
    if not activo:
        return True

    permisos = _cargar_permisos()
    recordado = permisos.get(tipo)
    if recordado == "siempre":
        depurar(f"[permisos] '{tipo}' recordada como SIEMPRE permitida.")
        return True
    if recordado == "nunca":
        depurar(f"[permisos] '{tipo}' recordada como NUNCA permitida.")
        return False

    exito("‚îÄ‚îÄ Permiso requerido " + "‚îÄ" * 30)
    _emitir(sys.stdout, f"  tipo        : {tipo}")
    _emitir(sys.stdout, f"  acciÛn      : {descripcion}")
    if detalles:
        for linea in str(detalles).splitlines()[:6]:
            _emitir(sys.stdout, f"  detalle     : {linea}")
    while True:
        try:
            eleccion = input(_pintar(
                "¬øPermitir esta acciÛn? "
                "[s]Ì ¬∑ [n]o ¬∑ [t]odos este tipo ¬∑ [a]nular todas (s/n/t/a): ",
                _AMARILLO)).strip().lower()
        except EOFError:
            aviso("Sin entrada disponible; acciÛn denegada por seguridad.")
            return False
        if eleccion in ("s", "si", "sÌ", "y", "yes"):
            return True
        if eleccion in ("n", "no"):
            aviso("AcciÛn denegada por el usuario.")
            return False
        if eleccion in ("t", "todos", "todo"):
            _guardar_permiso(tipo, "siempre")
            exito(f"Se recordar·: '{tipo}' siempre permitido "
                  f"({PERMISOS_PATH}). Usa --init o borra el archivo para "
                  "restaurar las preguntas.")
            return True
        if eleccion in ("a", "anular", "nunca"):
            _guardar_permiso(tipo, "nunca")
            aviso(f"Se recordar·: '{tipo}' nunca permitido ({PERMISOS_PATH}).")
            return False
        aviso("OpciÛn no v·lida; responde s, n, t o a.")


# ---------------------------------------------------------------------------
# MCP (Model Context Protocol): herramientas para el agente ‚Äî v0.14.0
# ---------------------------------------------------------------------------
MCP_TOOLS_PATH = CONFIG_DIR / "mcp_tools.json"
# Ecosistema de plugins (v4.0.0): ~/.snapcontext/plugins/<nombre>/plugin.json
PLUGINS_DIR = CONFIG_DIR / "plugins"
# Repositorio de comunidad donde `plugin install <nombre>` busca plugins.
REPOSITORIO_PLUGINS = "https://github.com/NicolasBruna24/snapcontext-plugins"

# Registro de herramientas predefinidas. Cada entrada describe la herramienta
# (para que el agente/usuario sepa cÛmo usarla) y si requiere permiso.
HERRAMIENTAS_PREDEFINIDAS = {
    "grep": {
        "descripcion": "Busca un patrÛn en el cÛdigo (rg/grep/findstr).",
        "parametros": {"patron": "str", "directorio": "str='.'"},
        "requiere_permiso": False,          # solo lectura
    },
    "read_file": {
        "descripcion": "Lee un archivo completo o un rango de lÌneas.",
        "parametros": {"ruta": "str", "linea_inicio": "int?", "linea_fin": "int?"},
        "requiere_permiso": False,          # solo lectura
    },
    "list_files": {
        "descripcion": "Lista archivos de una carpeta, con filtro de extensiÛn.",
        "parametros": {"directorio": "str='.'", "extensiones": "list?",
                       "max_archivos": "int=200"},
        "requiere_permiso": False,          # solo lectura
    },
    "ast": {
        "descripcion": "Analiza un .py y extrae imports, clases y funciones.",
        "parametros": {"ruta": "str"},
        "requiere_permiso": False,          # solo lectura
    },
    # v1.4.0: an·lisis sint·ctico multi-lenguaje (tree-sitter) y b˙squeda
    # sem·ntica integrada en el sistema de herramientas MCP.
    "ast_avanzado": {
        "descripcion": "An·lisis sint·ctico multi-lenguaje con tree-sitter "
                       "(funciones, clases, imports y llamadas); sin "
                       "tree-sitter usa ast de Python.",
        "parametros": {"ruta": "str"},
        "requiere_permiso": False,          # solo lectura
    },
    "semantic_search": {
        "descripcion": "B˙squeda sem·ntica por embeddings; devuelve los "
                       "fragmentos/archivos m·s relevantes para una consulta.",
        "parametros": {"consulta": "str", "directorio": "str='.'",
                       "max_resultados": "int=10"},
        "requiere_permiso": False,          # solo lectura
    },
    "git_status": {
        "descripcion": "Estado de Git (cambios sin commitear, rama actual).",
        "parametros": {"directorio": "str='.'"},
        "requiere_permiso": False,          # solo lectura
    },
    "git_diff": {
        "descripcion": "Muestra el diff (opcionalmente de un archivo).",
        "parametros": {"directorio": "str='.'", "archivo": "str?"},
        "requiere_permiso": False,          # solo lectura
    },
    "execute_command": {
        "descripcion": "Ejecuta cualquier comando shell (confirmaciÛn estricta).",
        "parametros": {"comando": "str", "directorio": "str='.'",
                       "background": "bool=False",
                       "capture_output": "bool=True"},
        "requiere_permiso": True,
    },
    "execute_command_status": {
        "descripcion": "Consulta el estado de un comando lanzado en segundo plano "
                       "(devuelve stdout/stderr/cÛdigo si terminÛ).",
        "parametros": {"pid": "int"},
        "requiere_permiso": False,
    },
    # v6.7.0: expansiÛn MCP ‚Äî bases de datos (solo lectura) y APIs externas.
    "db_query": {
        "descripcion": "Ejecuta una consulta SQL de SOLO LECTURA (SELECT, SHOW, "
                       "DESCRIBE, EXPLAIN) sobre la base de datos conectada "
                       "(conectar antes con --db-url o db_connect). Requiere "
                       "confirmaciÛn del usuario en modo interactivo.",
        "parametros": {"consulta": "str", "auto": "bool=False"},
        "requiere_permiso": False,   # la validaciÛn/confirmaciÛn es interna
    },
    "db_schema": {
        "descripcion": "Devuelve el esquema de la base de datos conectada "
                       "(tablas, columnas, tipos, claves).",
        "parametros": {},
        "requiere_permiso": False,          # solo lectura
    },
    "api_request": {
        "descripcion": "Hace una peticiÛn HTTP (GET/POST/PUT/PATCH/DELETE/HEAD) "
                       "a una URL externa y devuelve status, cabeceras y cuerpo "
                       "(JSON parseado si aplica).",
        "parametros": {"url": "str", "metodo": "str='GET'",
                       "headers": "dict={}", "body": "str=''",
                       "timeout": "float=15"},
        "requiere_permiso": True,
    },
    "api_inspect": {
        "descripcion": "Inspecciona una URL con GET: status, tiempo de "
                       "respuesta, tamaÒo y tipo de contenido.",
        "parametros": {"url": "str", "timeout": "float=15"},
        "requiere_permiso": False,          # solo lectura (GET)
    },
    # v6.10.0: herramientas de navegador (Playwright) para depuraciÛn visual.
    "browser_abrir": {
        "descripcion": "Abre una URL en el navegador headless (Playwright); "
                       "espera opcionalmente a que aparezca un selector.",
        "parametros": {"url": "str", "wait_for": "str?", "timeout": "int=30"},
        "requiere_permiso": False,
    },
    "browser_screenshot": {
        "descripcion": "Captura de pantalla (base64 PNG) de la p·gina actual "
                       "o de una URL; p·gina completa o un selector concreto.",
        "parametros": {"url": "str?", "full_page": "bool=False",
                       "selector": "str?"},
        "requiere_permiso": False,
    },
    "browser_click": {
        "descripcion": "Hace clic en un elemento de la p·gina actual.",
        "parametros": {"selector": "str"},
        "requiere_permiso": True,
    },
    "browser_type": {
        "descripcion": "Escribe texto en un campo de entrada de la p·gina "
                       "actual.",
        "parametros": {"selector": "str", "texto": "str"},
        "requiere_permiso": True,
    },
    "browser_get_text": {
        "descripcion": "Extrae el texto de un elemento de la p·gina actual.",
        "parametros": {"selector": "str"},
        "requiere_permiso": False,
    },
    "browser_analizar_imagen": {
        "descripcion": "Analiza una captura (base64) con un modelo de visiÛn "
                       "(Gemini 2.5 Pro / Claude 3.7 Sonnet) para detectar "
                       "errores visuales.",
        "parametros": {"imagen_base64": "str", "pregunta": "str"},
        "requiere_permiso": False,
    },
    "browser_cerrar": {
        "descripcion": "Cierra el navegador y libera recursos.",
        "parametros": {},
        "requiere_permiso": False,
    },
}


def _cargar_herramientas_mcp() -> dict:
    """Devuelve las herramientas disponibles: predefinidas + las del usuario.

    Las definidas por el usuario viven en ~/.snapcontext/mcp_tools.json con
    formato::

        {"tools": [{"nombre": "build", "descripcion": "...",
                    "comando": "npm run build", "requiere_permiso": true}]}

    Cada herramienta de usuario se ejecuta como comando shell. Archivo
    corrupto o entradas inv·lidas se ignoran con aviso (sin romper nada).
    """
    herramientas = {nombre: dict(cfg)
                    for nombre, cfg in HERRAMIENTAS_PREDEFINIDAS.items()}
    # v6.10.0: herramientas de navegador (Playwright), solo si Playwright
    # est· instalado (import perezoso; si falta no se ofrecen).
    try:
        import mcp_tools_browser as _btool
        if _btool._importar_playwright():
            _btool.registrar_en(herramientas)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        if MCP_TOOLS_PATH.is_file():
            datos = json.loads(MCP_TOOLS_PATH.read_text(encoding="utf-8"))
            for cruda in datos.get("tools", []) if isinstance(datos, dict) else []:
                nombre = str(cruda.get("nombre") or "").strip()
                comando = str(cruda.get("comando") or "").strip()
                if not nombre or not comando:
                    aviso(f"[mcp] Herramienta de usuario inv·lida ignorada: "
                          f"{cruda}")
                    continue
                herramientas[nombre] = {
                    "descripcion": str(cruda.get("descripcion")
                                       or f"Comando: {comando}"),
                    "parametros": {},
                    "requiere_permiso": bool(cruda.get("requiere_permiso", True)),
                    "comando": comando,
                }
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"No se pudieron leer las herramientas MCP "
              f"({MCP_TOOLS_PATH}): {exc}")
    # v4.0.0: herramientas expuestas por los plugins instalados y habilitados.
    for nombre, cfg in _plugins_herramientas().items():
        herramientas.setdefault(nombre, cfg)
    return herramientas


# ---------------------------------------------------------------------------
# Ecosistema de plugins (v4.0.0)
# ---------------------------------------------------------------------------
# Cada plugin vive en ~/.snapcontext/plugins/<nombre>/ con un ``plugin.json``::
#
#     {"nombre": "saludos", "version": "1.0.0", "autor": "alguien",
#      "descripcion": "...", "permisos": ["archivos"],
#      "herramientas": [{"nombre": "hola", "descripcion": "...",
#                        "comando": "python saluda.py"}]}
#
# Las herramientas se registran en el sistema MCP y se ejecutan por
# subproceso (como las herramientas de usuario de mcp_tools.json).

PERMISOS_PLUGIN_VALIDOS = ("archivos", "red", "red_escrita", "ejecucion",
                           "entorno")


def _plugins_directorio() -> Path:
    """Devuelve ~/.snapcontext/plugins cre·ndolo si no existe."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    return PLUGINS_DIR


def _plugin_leer_manifest(ruta_plugin: Path) -> Optional[dict]:
    """Lee y valida el ``plugin.json`` de un plugin. None si es inv·lido."""
    manifest = ruta_plugin / "plugin.json"
    if not manifest.is_file():
        return None
    try:
        datos = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        aviso(f"[plugin] plugin.json inv·lido o ilegible: {ruta_plugin.name}")
        return None
    if not isinstance(datos, dict):
        return None
    nombre = str(datos.get("nombre") or "").strip()
    herramientas = datos.get("herramientas")
    if not nombre or not isinstance(herramientas, list) or not herramientas:
        aviso(f"[plugin] Manifest sin 'nombre' o sin 'herramientas': "
              f"{ruta_plugin.name}")
        return None
    datos["nombre"] = nombre
    datos["ruta"] = str(ruta_plugin)
    datos.setdefault("version", "0.0.0")
    datos.setdefault("autor", "desconocido")
    datos.setdefault("descripcion", "")
    datos.setdefault("permisos", [])
    datos["habilitado"] = bool(datos.get("habilitado", True))
    return datos


def _plugins_instalados() -> dict:
    """Escanea el directorio de plugins y devuelve {nombre: manifest}.

    Los plugins inv·lidos (sin plugin.json o corruptos) se ignoran con un
    aviso; nunca rompen el arranque de SnapContext.
    """
    raiz = _plugins_directorio()
    instalados: dict = {}
    for carpeta in sorted(raiz.iterdir()):
        if not carpeta.is_dir():
            continue
        manifest = _plugin_leer_manifest(carpeta)
        if manifest is not None and manifest["nombre"] not in instalados:
            instalados[manifest["nombre"]] = manifest
    return instalados


def _plugins_herramientas() -> dict:
    """Herramientas MCP aportadas por los plugins habilitados.

    Formato idÈntico al de las herramientas de usuario (``comando``), m·s
    metadatos propios (``plugin``, ``permisos``) para trazabilidad.
    """
    resultado: dict = {}
    for nombre_plugin, manifest in _plugins_instalados().items():
        if not manifest.get("habilitado"):
            continue
        base = Path(manifest["ruta"])
        for herramienta in manifest.get("herramientas", []):
            if not isinstance(herramienta, dict):
                continue
            nombre = str(herramienta.get("nombre") or "").strip()
            comando = str(herramienta.get("comando") or "").strip()
            script = str(herramienta.get("script") or "").strip()
            if not nombre:
                continue
            if not comando and script:
                # Script relativo a la carpeta del plugin.
                comando = f'"{sys.executable}" "{(base / script)}"'
            if not comando:
                continue
            resultado[nombre] = {
                "descripcion": str(herramienta.get("descripcion")
                                   or f"Herramienta del plugin "
                                      f"'{nombre_plugin}'."),
                "parametros": herramienta.get("parametros") or {},
                "requiere_permiso": bool(
                    herramienta.get("requiere_permiso", True)),
                "comando": comando,
                "plugin": nombre_plugin,
                "permisos": list(manifest.get("permisos") or []),
            }
    return resultado


def _plugin_guardar_manifest(manifest: dict) -> bool:
    """Reescribe el plugin.json de un plugin (para habilitar/deshabilitar)."""
    try:
        datos = {k: v for k, v in manifest.items() if k != "ruta"}
        (Path(manifest["ruta"]) / "plugin.json").write_text(
            json.dumps(datos, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return True
    except OSError:
        return False


def _plugin_descargar_zip(origen: str, destino_tmp: Path) -> Optional[Path]:
    """Descarga el ZIP de un plugin desde GitHub y lo extrae en ``destino_tmp``.

    ``origen`` acepta:
      - URL de codeload/GitHub directa al zip.
      - Slug ``usuario/repositorio`` ‚Üí codeload con la rama ``main``.
    Devuelve la carpeta extraÌda que contiene ``plugin.json`` o None.
    """
    import urllib.request
    import zipfile

    if origen.startswith("http"):
        url_zip = origen
    else:
        url_zip = (f"https://codeload.github.com/{origen}/zip/refs/heads/main")
    zip_path = destino_tmp / "plugin.zip"
    try:
        with urllib.request.urlopen(url_zip, timeout=60) as respuesta:
            zip_path.write_bytes(respuesta.read())
        with zipfile.ZipFile(zip_path) as comprimido:
            comprimido.extractall(destino_tmp)
    except Exception as exc:   # noqa: BLE001 ‚Äî se reporta al llamador
        error(f"No se pudo descargar el plugin desde '{origen}': {exc}")
        return None
    # Busca el primer directorio extraÌdo que contenga plugin.json.
    for candidato in sorted(destino_tmp.rglob("plugin.json")):
        return candidato.parent
    aviso("El archivo descargado no contiene un plugin.json.")
    return None


def _plugin_instalar(origen: str, confirmar: bool = True,
                     auto: bool = False) -> int:
    """Instala un plugin desde un repositorio o carpeta local. ‚Üí cÛdigo salida.

    - Origen local: ruta a una carpeta con ``plugin.json`` (o su padre).
    - Origen remoto: slug GitHub (``usuario/repo``) o URL del zip.
    Siempre pide confirmaciÛn para fuentes externas salvo ``auto=True``.
    """
    raiz = _plugins_directorio()
    candidata = Path(origen).expanduser()
    externa = not candidata.is_dir()
    manifest = _plugin_leer_manifest(candidata) if candidata.is_dir() else None
    if manifest is not None:
        carpeta_origen = candidata
    elif externa:
        info(f"Descargando plugin desde '{origen}'...")
        tmp = Path(tempfile.mkdtemp(prefix="snapcontext_plugin_"))
        carpeta_origen = _plugin_descargar_zip(origen, tmp)
        if carpeta_origen is None:
            return 1
        manifest = _plugin_leer_manifest(carpeta_origen)
        if manifest is None:
            return 1
    else:
        error(f"'{origen}' no es una carpeta de plugin v·lida "
              f"(falta plugin.json).")
        return 1

    nombre = manifest["nombre"]
    permisos = ", ".join(manifest.get("permisos") or []) or "ninguno"
    if externa and not auto:
        if not _confirmar_accion(
                f"instalar el plugin externo '{nombre}' v{manifest['version']} "
                f"de '{manifest.get('autor', '?')}'",
                tipo="plugin",
                detalles=f"permisos declarados: {permisos}",
                confirmar=confirmar):
            aviso("InstalaciÛn cancelada.")
            return 1
    destino = raiz / nombre
    if destino.exists():
        aviso(f"El plugin '{nombre}' ya estaba instalado; se sobrescribe.")
        shutil.rmtree(destino, ignore_errors=True)
    try:
        shutil.copytree(carpeta_origen, destino,
                        ignore=shutil.ignore_patterns(
                            ".git", "__pycache__", "*.zip"))
    except OSError as exc:
        error(f"No se pudo instalar el plugin: {exc}")
        return 1
    if externa:
        # Guarda el origen para `plugin update`.
        instalado = _plugin_leer_manifest(destino)
        if instalado is not None:
            instalado["origen"] = origen
            _plugin_guardar_manifest(instalado)
    herramientas = ", ".join(
        h.get("nombre", "?") for h in manifest.get("herramientas", []))
    exito(f"Plugin '{nombre}' v{manifest['version']} instalado. "
          f"Herramientas: {herramientas}")
    return 0


def _plugin_remove(nombre: str, confirmar: bool = True) -> int:
    """Desinstala un plugin borrando su carpeta (con confirmaciÛn)."""
    instalados = _plugins_instalados()
    if nombre not in instalados:
        error(f"Plugin '{nombre}' no encontrado. Instalados: "
              f"{', '.join(instalados) or '(ninguno)'}")
        return 1
    if confirmar and not _confirmar_accion(f"desinstalar el plugin '{nombre}'",
                                           tipo="plugin"):
        aviso("DesinstalaciÛn cancelada.")
        return 1
    shutil.rmtree(Path(instalados[nombre]["ruta"]), ignore_errors=True)
    exito(f"Plugin '{nombre}' desinstalado.")
    return 0


def _plugin_create(nombre: str = None) -> int:
    """Asistente que genera la estructura b·sica de un plugin nuevo."""
    nombre = (nombre or "").strip()
    if not nombre or not re.fullmatch(r"[a-zA-Z0-9_\-]+", nombre):
        nombre = ""
        while not nombre or not re.fullmatch(r"[a-zA-Z0-9_\-]+", nombre):
            try:
                nombre = input("Nombre del plugin (letras, n˙meros, - _): "
                               ).strip()
            except EOFError:
                error("Nombre requerido.")
                return 1
    destino = _plugins_directorio() / nombre
    if destino.exists():
        error(f"Ya existe un plugin llamado '{nombre}'.")
        return 1
    destino.mkdir(parents=True)
    manifest = {
        "nombre": nombre, "version": "0.1.0", "autor": "",
        "descripcion": f"Plugin {nombre} para SnapContext.",
        "permisos": [], "habilitado": True,
        "herramientas": [{
            "nombre": f"{nombre}_saludar",
            "descripcion": "Herramienta de ejemplo: imprime un saludo.",
            "script": "saluda.py",
            "requiere_permiso": False,
            "parametros": {"nombre": "str"},
        }],
    }
    (destino / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (destino / "saluda.py").write_text(
        '#!/usr/bin/env python3\n'
        '"""Herramienta de ejemplo del plugin. Recibe argumentos JSON por\n'
        'stdin y responde un JSON con {"ok": true|false, ...}."""\n'
        "import json\n"
        "import sys\n\n"
        "datos = json.loads(sys.stdin.read() or '{}')\n"
        "quien = datos.get('nombre', 'mundo')\n"
        "print(json.dumps({'ok': True, 'saludo': f'Hola, {quien}!'}))\n",
        encoding="utf-8")
    (destino / "README.md").write_text(
        f"# Plugin {nombre}\n\nGenerado por `snapcontext plugin create`.\n\n"
        "Edita `plugin.json` para aÒadir m·s herramientas.\n",
        encoding="utf-8")
    exito(f"Plugin '{nombre}' creado en {destino}.")
    info("PruÈbalo con: snapcontext plugin list")
    return 0


def _plugin_update(nombre: str) -> int:
    """Reinstala un plugin desde su origen registrado (plugin update)."""
    instalados = _plugins_instalados()
    if nombre not in instalados:
        error(f"Plugin '{nombre}' no encontrado.")
        return 1
    origen = instalados[nombre].get("origen")
    if not origen:
        aviso(f"El plugin '{nombre}' no registra origen remoto; "
              "reinst·lalo manualmente.")
        return 1
    info(f"Actualizando '{nombre}' desde {origen}...")
    return _plugin_instalar(origen, auto=True)


def _plugin_cambiar_estado(nombre: str, habilitar: bool) -> int:
    """Habilita o deshabilita un plugin individualmente."""
    instalados = _plugins_instalados()
    if nombre not in instalados:
        error(f"Plugin '{nombre}' no encontrado.")
        return 1
    manifest = instalados[nombre]
    manifest["habilitado"] = habilitar
    if _plugin_guardar_manifest(manifest):
        estado = "habilitado" if habilitar else "deshabilitado"
        exito(f"Plugin '{nombre}' {estado}.")
        return 0
    error(f"No se pudo escribir el manifest de '{nombre}'.")
    return 1


def _plugin_mostrar() -> None:
    """Lista los plugins instalados y sus herramientas en la CLI."""
    instalados = _plugins_instalados()
    if not instalados:
        info("No hay plugins instalados en ~/.snapcontext/plugins.")
        info("Crea uno con: snapcontext plugin create <nombre>")
        return
    exito(f"Plugins instalados ({len(instalados)}):")
    for nombre, manifest in instalados.items():
        estado = "habilitado" if manifest.get("habilitado") else \
                 "DESHABILITADO"
        color = _VERDE if manifest.get("habilitado") else _AMARILLO
        _emitir(sys.stdout, _pintar(
            f"  ‚óè {nombre} v{manifest['version']} [{estado}] ‚Äî "
            f"{manifest.get('descripcion', '')}", color))
        for herramienta in manifest.get("herramientas", []):
            if isinstance(herramienta, dict) and herramienta.get("nombre"):
                _emitir(sys.stdout, _pintar(
                    f"      ¬∑ herramienta '{herramienta['nombre']}': "
                    f"{herramienta.get('descripcion', '')}", _CYAN))


def _ejecutar_comando_discord(subargv: List[str]) -> int:
    """Despacha el subcomando ``snapcontext discord <accion> [...]``."""
    import argparse as _ap

    try:
        import discord_gateway as dg
    except ImportError as exc:
        error(f"El gateway de Discord necesita httpx y cryptography: "
              f"pip install httpx cryptography (error: {exc})")
        return 1

    parser = _ap.ArgumentParser(prog="snapcontext discord", add_help=False)
    sub = parser.add_subparsers(dest="accion")

    p_setup = sub.add_parser("setup", help="Guarda las credenciales de la app.")
    p_setup.add_argument("--public-key", dest="public_key", default=None,
                         help="Clave p˙blica Ed25519 de la aplicaciÛn.")
    p_setup.add_argument("--app-id", dest="app_id", default=None,
                         help="ID de la aplicaciÛn (Application ID).")
    p_setup.add_argument("--token", default=None,
                         help="Token del bot (Bot Token).")
    p_setup.add_argument("--webhook-url", dest="webhook_url", default=None,
                         help="Webhook est·ndar de un canal (alternativa).")
    sub.add_parser("estado", help="Muestra la configuraciÛn actual.")

    if not subargv or subargv[0] in ("-h", "--help", "help"):
        info(
            "Uso: snapcontext discord <setup|estado> [...]\n"
            "  setup --public-key <KEY> --app-id <ID> --token <BOT_TOKEN> "
            "[--webhook-url <URL>]\n\n"
            "ConfiguraciÛn del webhook en el portal (self-hosted):\n"
            "  1. https://discord.com/developers/applications ‚Üí tu app ‚Üí\n"
            "     'General Information': copia PUBLIC KEY y APPLICATION ID.\n"
            "  2. 'Bot': crea el bot y copia el TOKEN.\n"
            "  3. ExpÛn este servidor con ngrok o un VPS:\n"
            "         ngrok http 8001        (si usas `snapcontext --api`)\n"
            "  4. En 'General Information' ‚Üí INTERACTIONS ENDPOINT URL pon:\n"
            "         https://<tu-dominio>/webhook/discord\n"
            "     Discord lo verificar· con un PING; nuestro endpoint\n"
            "     responde {\"type\": 1} autom·ticamente.\n"
            "  5. 'Bot' ‚Üí activa los permisos que necesites e invita el bot\n"
            "     a tu servidor (OAuth2 ‚Üí URL Generator, scope 'applications.commands bot')."
        )
        return 0
    try:
        args = parser.parse_args(subargv)
    except SystemExit:
        return 1

    def _oculto(valor: Optional[str]) -> str:
        return f"configurado (***{valor[-4:]})" if valor else "(sin definir)"

    if args.accion == "setup":
        guardado = dg.guardar_configuracion_discord(
            args.public_key, args.app_id, args.token, args.webhook_url)
        exito("Credenciales de Discord guardadas en ~/.snapcontext/"
              "config.json ('discord').")
        info(f"  public_key     : {_oculto(guardado.get('public_key'))}")
        info(f"  application_id : {guardado.get('application_id') or '(sin definir)'}")
        info(f"  bot_token      : {_oculto(guardado.get('bot_token'))}")
        info(f"  webhook_url    : {guardado.get('webhook_url') or '(sin definir)'}")
        aviso(
            "Siguiente paso (portal de Discord Developers):\n"
            "  https://discord.com/developers/applications ‚Üí tu app ‚Üí\n"
            "  General Information ‚Üí INTERACTIONS ENDPOINT URL:\n"
            "      https://<tu-dominio>/webhook/discord\n"
            "  (expÛn el puerto con `ngrok http 8001` si desarrollas en local;\n"
            "   Discord lo verifica con un PING que respondemos autom·ticamente).")
        return 0

    if args.accion == "estado":
        public_key = dg.obtener_public_key()
        exito("Estado del gateway de Discord:")
        info(f"  public_key     : {_oculto(public_key)}")
        info(f"  application_id : {dg.obtener_application_id() or '(sin definir)'}")
        info(f"  bot_token      : {_oculto(dg.obtener_bot_token())}")
        info(f"  webhook_url    : {dg.obtener_webhook_url() or '(sin definir)'}")
        return 0 if public_key else 1

    parser.print_help()
    return 1


def _ejecutar_comando_telegram(subargv: List[str]) -> int:
    """Despacha el subcomando ``snapcontext telegram <accion> [...]``."""
    import argparse as _ap

    try:
        import telegram_gateway as tg
    except ImportError as exc:
        error(f"El gateway de Telegram necesita httpx: pip install httpx "
              f"(error: {exc})")
        return 1

    parser = _ap.ArgumentParser(prog="snapcontext telegram", add_help=False)
    sub = parser.add_subparsers(dest="accion")

    p_setup = sub.add_parser("setup", help="Guarda token y URL del webhook.")
    p_setup.add_argument("--token", default=None,
                         help="Token del bot (de @BotFather).")
    p_setup.add_argument("--webhook-url", dest="webhook_url", default=None,
                         help="URL p˙blica (ngrok/dominio); el webhook queda "
                              "en <url>/webhook/telegram.")
    sub.add_parser("estado", help="Muestra la configuraciÛn actual.")
    sub.add_parser("webhook-registrar",
                   help="Llama a setWebhook con la URL configurada.")

    if not subargv or subargv[0] in ("-h", "--help", "help"):
        info("Uso: snapcontext telegram <setup|estado|webhook-registrar> [...]\n"
             "  setup --token <TOKEN> [--webhook-url <URL>]")
        return 0
    try:
        args = parser.parse_args(subargv)
    except SystemExit:
        return 1

    if args.accion == "setup":
        guardado = tg.guardar_configuracion_telegram(args.token,
                                                     args.webhook_url)
        exito("ConfiguraciÛn de Telegram guardada en ~/.snapcontext/"
              "config.json ('telegram').")
        info(f"  webhook_url : {guardado.get('webhook_url') or '(sin definir)'}")
        info(f"  bot_token   : {'***' + guardado.get('bot_token', '')[-4:]}"
             if guardado.get("bot_token") else "  bot_token   : (sin definir)")
        if guardado.get("bot_token") and guardado.get("webhook_url"):
            ok, detalle = tg.registrar_webhook()
            (exito if ok else aviso)(f"setWebhook: {detalle}")
        elif not guardado.get("webhook_url"):
            aviso("Sin --webhook-url no se registrÛ el webhook; ll·malo con\n"
                  "  snapcontext telegram webhook-registrar")
        return 0

    if args.accion == "estado":
        token = tg.obtener_token()
        url = tg.obtener_webhook_url()
        exito("Estado del gateway de Telegram:")
        info(f"  bot_token   : {'configurado (***' + token[-4:] + ')'}"
             if token else "  bot_token   : NO configurado")
        info(f"  webhook_url : {url or '(no definida)'}")
        return 0 if token else 1

    if args.accion == "webhook-registrar":
        ok, detalle = tg.registrar_webhook()
        (exito if ok else error)(f"setWebhook: {detalle}")
        return 0 if ok else 1

    parser.print_help()
    return 1


def _ejecutar_comando_github(subargv: List[str]) -> int:
    """Despacha el subcomando ``snapcontext github <accion> [...]`` (v6.8.0)."""
    import argparse as _ap

    try:
        import github_gateway as gh
    except ImportError as exc:
        error(f"El gateway de GitHub no se pudo cargar: {exc}")
        return 1

    parser = _ap.ArgumentParser(prog="snapcontext github", add_help=False)
    sub = parser.add_subparsers(dest="accion")

    p_setup = sub.add_parser("setup", help="Guarda credenciales y webhook de GitHub.")
    p_setup.add_argument("--token", default=None, help="Personal Access Token de GitHub.")
    p_setup.add_argument("--secret", "--webhook-secret", dest="secret", default=None,
                         help="Secreto para validar la firma HMAC del webhook.")
    p_setup.add_argument("--webhook-url", dest="webhook_url", default=None,
                         help="URL p˙blica (ngrok/dominio); el webhook queda en <url>/webhook/github.")

    sub.add_parser("estado", help="Muestra la configuraciÛn actual de GitHub.")

    p_hook = sub.add_parser("webhook-registrar", help="Registra el webhook en un repositorio de GitHub.")
    p_hook.add_argument("--repo", required=True, help="Repositorio en GitHub (ej: owner/repo).")
    p_hook.add_argument("--webhook-url", dest="webhook_url", default=None, help="URL p˙blica del webhook.")
    p_hook.add_argument("--secret", dest="secret", default=None, help="Secreto HMAC del webhook.")

    if not subargv or subargv[0] in ("-h", "--help", "help"):
        info("Uso: snapcontext github <setup|estado|webhook-registrar> [...]\n"
             "  setup [--token <TOKEN>] [--secret <SECRET>] [--webhook-url <URL>]\n"
             "  webhook-registrar --repo <owner/repo> [--webhook-url <URL>] [--secret <SECRET>]")
        return 0
    try:
        args = parser.parse_args(subargv)
    except SystemExit:
        return 1

    if args.accion == "setup":
        guardado = gh.guardar_configuracion_github(
            webhook_secret=getattr(args, "secret", None),
            token=getattr(args, "token", None),
            webhook_url=getattr(args, "webhook_url", None),
        )
        exito("ConfiguraciÛn de GitHub guardada en ~/.snapcontext/config.json ('github').")
        info(f"  webhook_url    : {guardado.get('webhook_url') or '(sin definir)'}")
        info(f"  token          : {_oculto(guardado.get('token'))}")
        info(f"  webhook_secret : {_oculto(guardado.get('webhook_secret'))}")
        return 0

    if args.accion == "estado":
        token = gh.obtener_github_token()
        secreto = gh.obtener_webhook_secreto()
        url = gh.obtener_webhook_url()
        exito("Estado del gateway de GitHub:")
        info(f"  token          : {_oculto(token)}")
        info(f"  webhook_secret : {_oculto(secreto)}")
        info(f"  webhook_url    : {url or '(no definida)'}")
        return 0 if (token or secreto) else 1

    if args.accion == "webhook-registrar":
        url = getattr(args, "webhook_url", None) or gh.obtener_webhook_url() or ""
        secreto = getattr(args, "secret", None) or gh.obtener_webhook_secreto() or ""
        ok, detalle = gh.configurar_webhook(url=url, secreto=secreto, repo=args.repo)
        (exito if ok else error)(f"GitHub Webhook: {detalle}")
        return 0 if ok else 1

    parser.print_help()
    return 1


def _ejecutar_comando_curador(subargv: List[str]) -> int:
    """Despacha el subcomando ``snapcontext curador <accion> [...]`` (v5.0.0).

    Acciones:
      estado      ‚Üí muestra estadÌsticas agregadas de skills.
      ejecutar    ‚Üí corre el motor de refactorizaciÛn proactivo manualmente.
      activar     ‚Üí reactiva el curador proactivo (persistente).
      desactivar  ‚Üí lo desactiva.
    """
    import argparse as _ap

    try:
        import curador_proactivo as cp
    except ImportError as exc:
        error(f"No se pudo cargar el curador proactivo: {exc}")
        return 1

    accion = (subargv[0] if subargv else "estado").strip().lower()
    # Soporte para '-h/--help/help'.
    if accion in ("-h", "--help", "help"):
        info("Uso: snapcontext curador <estado|ejecutar|activar|desactivar>")
        info("  estado      ‚Üí mÈtricas y estado del motor")
        info("  ejecutar    ‚Üí refactoriza los skills candidatos ahora")
        info("  activar     ‚Üí reactiva el curador proactivo persistente")
        info("  desactivar  ‚Üí desactiva el curador proactivo")
        return 0

    if accion == "estado":
        resumen = cp.estado_curador()
        exito("Estado del curador proactivo:")
        info(f"  activo            : {'sÌ' if resumen['activo'] else 'no'}")
        info(f"  intervalo (horas) : {resumen['intervalo_horas']}")
        info(f"  skills            : {resumen['total_skills']} "
             f"(activos {resumen['activos']})")
        info(f"  candidatos        : {resumen['candidatos']}")
        info(f"  ˙ltima pasada     : {resumen['ultima_pasada'] or 'nunca'}")
        for fila in resumen.get("reinado_lista", [])[:20]:
            info(f"    #{fila['id']} {fila['nombre']} "
                 f"(usos {fila['usos']}, fallos {fila['fallos']}, "
                 f"tokens~ {fila['tokens_promedio']})")
        return 0

    if accion == "ejecutar":
        resultados = cp.ejecutar_curador()
        if resultados is None:
            aviso("Curador desactivado. ActÌvalo: snapcontext curador activar")
            return 0
        mejorados = [r for r in resultados if r.get("mejorado")]
        info(f"Curador: {len(resultados)} skill(s) candidato(s), "
             f"{len(mejorados)} mejorado(s).")
        return 0

    if accion == "activar":
        cp.activar_curador()
        exito("Curador proactivo activado (persistente).")
        return 0
    if accion == "desactivar":
        cp.desactivar_curador()
        exito("Curador proactivo desactivado.")
        return 0

    return 0


def _ejecutar_comando_plugin(subargv: List[str]) -> int:
    """Despacha el subcomando ``snapcontext plugin <accion> [...]``."""
    global DEPURAR
    if not subargv:
        _plugin_mostrar()
        return 0
    accion = subargv[0].lower()
    resto = subargv[1:]
    if accion == "list":
        _plugin_mostrar()
        return 0
    if accion == "install":
        if not resto:
            error("Uso: snapcontext plugin install <nombre | usuario/repo "
                  "| url | ruta_local>")
            return 1
        # v6.22.0: nombres simples se resuelven contra el marketplace.
        try:
            import marketplace
            return marketplace.instalar_plugin(resto[0])
        except Exception as exc:                         # noqa: BLE001
            error(f"Error en la instalaciÛn: {exc}")
            return 1
    if accion in ("remove", "uninstall"):                # v6.22.0: uninstall
        if not resto:
            error("Uso: snapcontext plugin remove <nombre>")
            return 1
        return _plugin_remove(resto[0])
    if accion == "search":                               # v6.22.0: marketplace
        if not resto:
            error("Uso: snapcontext plugin search <termino>")
            return 1
        return _plugin_search(resto[0])
    if accion == "create":
        return _plugin_create(resto[0] if resto else None)
    if accion == "update":
        if not resto:
            error("Uso: snapcontext plugin update <nombre>")
            return 1
        return _plugin_update(resto[0])
    if accion in ("enable", "disable"):
        if not resto:
            error(f"Uso: snapcontext plugin {accion} <nombre>")
            return 1
        return _plugin_cambiar_estado(resto[0], habilitar=accion == "enable")
    error(f"AcciÛn de plugin desconocida: '{accion}'. Usa search/list/install/"
          "remove/create/update/enable/disable.")
    return 1


def _plugin_search(termino: str) -> int:
    """Busca plugins en el marketplace central (v6.22.0)."""
    try:
        import marketplace
    except Exception as exc:                             # noqa: BLE001
        error(f"No se pudo importar marketplace: {exc}")
        return 1
    try:
        resultados = marketplace.buscar_plugins(termino)
    except Exception as exc:                             # noqa: BLE001
        error(f"Error buscando en el marketplace: {exc}")
        return 1
    if not resultados:
        aviso(f"Sin resultados para '{termino}'.")
        return 0
    info(f"?? {len(resultados)} plugin(s) encontrados para '{termino}':")
    for entrada in resultados:
        nombre = entrada.get("nombre") or entrada.get("name") or "?"
        desc = (entrada.get("descripcion") or entrada.get("description")
                or "").strip()
        autor = entrada.get("autor") or entrada.get("author") or ""
        exito(f"  ‚Ä¢ {nombre}" + (f" ‚Äî {desc}" if desc else "")
              + (f" (por {autor})" if autor else ""))
    return 0


# --- Implementaciones de las herramientas (resultados estructurados) -------
def _tool_grep(patron: str, directorio: str = ".",
               max_resultados: int = 50) -> dict:
    """Herramienta `grep`: busca un patrÛn en el cÛdigo del proyecto."""
    if not patron:
        return {"ok": False, "error": "falta el patrÛn de b˙squeda"}
    herramienta = _herramienta_busqueda()
    if herramienta is None:
        return {"ok": False, "error": "sin buscador disponible (rg/grep/findstr)"}
    if herramienta == "rg":
        comando = f'rg -n -i --max-count 5 "{patron}"'
    elif herramienta == "grep":
        comando = f'grep -rn -i -m 5 "{patron}" .'
    else:
        comando = f'findstr /s /n /i "{patron}" *.py *.dart *.js *.ts *.go *.rs'
    codigo, stdout, stderr = (None, None, None)
    # v4.3.0: grep es de solo lectura ‚Üí corre fuera del sandbox.
    with _sandbox_pausado():
        codigo, stdout, stderr = _ejecutar_comando(comando, directorio,
                                                   timeout=60)
    lineas = [l for l in (stdout or "").splitlines() if l.strip()]
    return {"ok": codigo == 0 or bool(lineas),
            "buscador": herramienta, "total": len(lineas),
            "coincidencias": lineas[:max_resultados],
            "error": None if (codigo == 0 or lineas) else stderr.strip()}


def _tool_read_file(ruta: str, linea_inicio: Optional[int] = None,
                    linea_fin: Optional[int] = None) -> dict:
    """Herramienta `read_file`: lee un archivo completo o un rango de lÌneas."""
    contenido = _leer_archivo(ruta)
    if contenido is None:
        return {"ok": False, "ruta": ruta, "error": "no se pudo leer"}
    lineas = contenido.splitlines()
    ini = max((linea_inicio or 1), 1)
    fin = min(linea_fin or len(lineas), len(lineas))
    fragmento = lineas[ini - 1:fin]
    return {"ok": True, "ruta": ruta, "total_lineas": len(lineas),
            "linea_inicio": ini, "linea_fin": fin,
            "contenido": "\n".join(fragmento)}


def _tool_list_files(directorio: str = ".",
                     extensiones: Optional[List[str]] = None,
                     max_archivos: int = 200) -> dict:
    """Herramienta `list_files`: lista archivos con filtros opcionales."""
    raiz = Path(directorio).expanduser()
    if not raiz.is_dir():
        return {"ok": False, "directorio": directorio,
                "error": f"el directorio no existe: {raiz}"}
    extensiones = [e.lower() if e.startswith(".") else f".{e.lower()}"
                   for e in (extensiones or [])]
    encontrados: List[str] = []
    for camino in sorted(raiz.rglob("*")):
        if not camino.is_file():
            continue
        if any(parte in (".git", "__pycache__", "node_modules")
               for parte in camino.parts):
            continue
        if extensiones and camino.suffix.lower() not in extensiones:
            continue
        encontrados.append(str(camino.relative_to(raiz)))
        if len(encontrados) >= max_archivos:
            break
    return {"ok": True, "directorio": str(raiz), "total": len(encontrados),
            "archivos": encontrados}


def _tool_ast(ruta: str) -> dict:
    """Herramienta `ast`: extrae imports, clases y funciones de un .py."""
    contenido = _leer_archivo(ruta)
    if contenido is None:
        return {"ok": False, "ruta": ruta, "error": "no se pudo leer"}
    try:
        arbol = ast.parse(contenido)
    except SyntaxError as exc:
        return {"ok": False, "ruta": ruta,
                "error": f"sintaxis inv·lida: {exc}"}
    imports: List[str] = []
    clases: List[dict] = []
    funciones: List[dict] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                imports.append(alias.name)
        elif isinstance(nodo, ast.ImportFrom):
            modulo = nodo.module or ""
            nombres = ", ".join(a.name for a in nodo.names)
            imports.append(f"from {modulo} import {nombres}")
        elif isinstance(nodo, ast.ClassDef):
            metodos = [n.name for n in nodo.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            clases.append({"nombre": nodo.name, "linea": nodo.lineno,
                           "metodos": metodos})
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            argumentos = [a.arg for a in nodo.args.args]
            funciones.append({"nombre": nodo.name, "linea": nodo.lineno,
                              "argumentos": argumentos})
    return {"ok": True, "ruta": ruta, "imports": imports, "clases": clases,
            "funciones": funciones}


def _tool_git_status(directorio: str = ".") -> dict:
    """Herramienta `git_status`: rama actual y cambios sin commitear."""
    if not _es_repo_git(directorio):
        return {"ok": False, "error": f"'{directorio}' no es un repositorio git"}
    _, rama, _ = _ejecutar_comando("git rev-parse --abbrev-ref HEAD",
                                   directorio, timeout=15)
    with _sandbox_pausado():  # v4.3.0: solo lectura ‚Üí fuera del sandbox
        codigo, salida, _ = _ejecutar_comando("git status --porcelain",
                                              directorio, timeout=30)
    modificados = [l.strip() for l in (salida or "").splitlines() if l.strip()]
    return {"ok": codigo == 0, "rama": (rama or "").strip(),
            "cambios": modificados, "total_cambios": len(modificados)}


def _tool_git_diff(directorio: str = ".", archivo: Optional[str] = None,
                   max_lineas: int = 200) -> dict:
    """Herramienta `git_diff`: diferencias sin commitear (staged + unstaged)."""
    if not _es_repo_git(directorio):
        return {"ok": False, "error": f"'{directorio}' no es un repositorio git"}
    comando = "git diff HEAD"
    if archivo:
        comando += f' -- "{archivo}"'
    with _sandbox_pausado():  # v4.3.0: solo lectura ‚Üí fuera del sandbox
        codigo, salida, stderr = _ejecutar_comando(comando, directorio,
                                                   timeout=60)
    lineas = (salida or "").splitlines()
    return {"ok": codigo == 0, "archivo": archivo,
            "total_lineas": len(lineas),
            "diff": "\n".join(lineas[:max_lineas]),
            "recortado": len(lineas) > max_lineas,
            "error": None if codigo == 0 else stderr.strip()}


def _tool_execute_command(comando: str, directorio: str = ".",
                          background: bool = False,
                          capture_output: bool = True) -> dict:
    """Herramienta `execute_command`: ejecuta un comando shell arbitrario.

    - ``background=True`` lanza el proceso en segundo plano y devuelve un
      ``pid`` para consultarlo despuÈs.
    - ``capture_output=False`` muestra la salida en tiempo real (en su lugar
      ``stdout``/``stderr`` quedan vacÌos).

    Requiere confirmaciÛn estricta (se valida en el dispatcher).
    """
    if not comando:
        return {"ok": False, "error": "falta el comando a ejecutar"}
    if background:
        res = _lanzar_proceso_fondo(comando, directorio,
                                    capture_output=capture_output)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "no se pudo lanzar")}
        return {"ok": True, "pid": res["pid"], "comando": comando,
                "estado": "ejecutando"}
    codigo, stdout, stderr = _ejecutar_comando(
        comando, directorio, capture_output=capture_output)
    return {"ok": codigo == 0, "codigo_retorno": codigo,
            "stdout": stdout.strip() if stdout else "",
            "stderr": stderr.strip() if stderr else ""}


# --- Herramientas avanzadas (v1.4.0): tree-sitter + b˙squeda sem·ntica ------
# Tipos de nodo tree-sitter por categorÌa (nombres comunes entre gram·ticas).
_TS_NODOS_FUNCION = frozenset((
    "function_definition", "function_declaration", "function_item",
    "function_signature", "method_definition", "method_declaration",
))
_TS_NODOS_CLASE = frozenset((
    "class_definition", "class_declaration", "class_specifier",
    "struct_item", "interface_declaration",
))
_TS_NODOS_IMPORT = frozenset((
    "import_statement", "import_from_statement", "import_specifier",
    "import_declaration", "use_declaration", "package_clause",
    "preproc_include", "import_directive",
))
_TS_NODOS_LLAMADA = frozenset(("call_expression", "call"))


def _lenguaje_tree_sitter(ruta: str) -> Optional[str]:
    """Adivina el nombre de gram·tica tree-sitter para ``ruta``."""
    extension = Path(ruta).suffix.lower().lstrip(".")
    mapa = {
        "py": "python", "pyi": "python", "js": "javascript", "jsx": "javascript",
        "mjs": "javascript", "cjs": "javascript", "ts": "typescript",
        "mts": "typescript", "cts": "typescript", "tsx": "tsx",
        "dart": "dart", "go": "go", "rs": "rust", "java": "java",
        "kt": "kotlin", "kts": "kotlin", "swift": "swift", "c": "c",
        "h": "c", "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp",
        "hh": "cpp", "hxx": "cpp", "cs": "c_sharp", "rb": "ruby",
        "php": "php", "sh": "bash", "bash": "bash", "zsh": "bash",
        "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
        "html": "html", "htm": "html", "css": "css", "scss": "css",
        "md": "markdown", "scala": "scala", "lua": "lua", "sql": "sql",
        "ex": "elixir", "exs": "elixir", "zig": "zig", "hs": "haskell",
        "r": "r", "vue": "vue", "svelte": "svelte",
    }
    return mapa.get(extension)


# ‚îÄ‚îÄ‚îÄ DetecciÛn de lenguaje por contenido (v3.3.0) ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ‚îÄ
_PATRONES_LENGUAJE_CONTENIDO = (
    (re.compile(r"^#!.*\bpython\S*", re.MULTILINE), "python"),
    (re.compile(r"^#!.*\b(bash|sh|zsh)\b", re.MULTILINE), "bash"),
    (re.compile(r"^#!.*\bnode\b", re.MULTILINE), "javascript"),
    (re.compile(r"^package\s+\w+", re.MULTILINE), "go"),
    (re.compile(r"<\?php", re.IGNORECASE), "php"),
    (re.compile(r"^\s*def\s+\w+\s*\(.*\)\s*:", re.MULTILINE), "python"),
    (re.compile(r"\bfunc(?:tion)?\s+\w*\s*\(", re.MULTILINE), "javascript"),
    (re.compile(r"^\s*(public|private)?\s*class\s+\w+", re.MULTILINE), "java"),
    (re.compile(r"\bfn\s+\w+\s*\(", re.MULTILINE), "rust"),
)


def _detectar_lenguaje_contenido(contenido: str) -> Optional[str]:
    """Intenta adivinar el lenguaje a partir del contenido del archivo.

    Se usa como refuerzo cuando la extensiÛn es ambigua o desconocida
    (p. ej. scripts sin extensiÛn, archivos generados, proyectos mixtos).
    """
    if not contenido:
        return None
    muestra = contenido[:4096]
    for patron, lenguaje in _PATRONES_LENGUAJE_CONTENIDO:
        if patron.search(muestra):
            return lenguaje
    return None


def _lenguaje_archivo(ruta: str,
                      contenido: Optional[str] = None) -> Optional[str]:
    """Detecta el lenguaje de ``ruta`` combinando extensiÛn y contenido.

    Prioridad: extensiÛn conocida ‚Üí heurÌstica de contenido ‚Üí None.
    M·s robusto que la detecciÛn solo por extensiÛn en proyectos mixtos.
    """
    por_extension = _lenguaje_tree_sitter(ruta)
    if por_extension:
        return por_extension
    if contenido is None:
        leido = _leer_archivo(ruta)
        contenido = leido or ""
    return _detectar_lenguaje_contenido(contenido)



def _extraer_simbolos_ts(arbol, lenguaje: str) -> dict:
    """Recorre el ·rbol tree-sitter y extrae funciones/clases/imports/llamadas."""
    funciones: List[dict] = []
    clases: List[dict] = []
    imports: List[str] = []
    llamadas: List[str] = []

    def _texto(nodo) -> str:
        return nodo.text.decode("utf-8", errors="replace") if nodo.text else ""

    pila = [arbol.root_node]
    while pila:
        nodo = pila.pop()
        tipo = nodo.type
        if tipo in _TS_NODOS_FUNCION or tipo in _TS_NODOS_CLASE:
            nombre = ""
            for hijo in nodo.children:
                if getattr(hijo, "type", "") in (
                        "identifier", "name", "property_identifier",
                        "type_identifier"):
                    nombre = _texto(hijo)
                    break
            entrada = {"nombre": nombre or f"({tipo})",
                       "linea": nodo.start_point[0] + 1}
            (funciones if tipo in _TS_NODOS_FUNCION else clases).append(entrada)
        elif tipo in _TS_NODOS_IMPORT:
            fragmento = " ".join(_texto(nodo).split())
            if fragmento and fragmento not in imports:
                imports.append(fragmento[:200])
        elif tipo in _TS_NODOS_LLAMADA:
            for hijo in nodo.children:
                if hijo.type in ("identifier", "attribute", "member_expression"):
                    texto = " ".join(_texto(hijo).split())[:120]
                    if texto and texto not in llamadas:
                        llamadas.append(texto)
                    break
        pila.extend(nodo.children)
    return {"funciones": funciones, "clases": clases,
            "imports": imports[:100], "llamadas": llamadas[:200]}


def _tool_ast_avanzado(ruta: str) -> dict:
    """Herramienta `ast_avanzado` (v1.4.0).

    An·lisis sint·ctico multi-lenguaje con **tree-sitter** si est· instalado
    (`pip install snapcontext[mcp_avanzado]`). Si no, hace fallback al mÛdulo
    `ast` de la stdlib (solo para archivos Python). Nunca lanza excepciones.
    """
    contenido = _leer_archivo(ruta)
    if contenido is None:
        return {"ok": False, "ruta": ruta, "error": "no se pudo leer"}
    lenguaje = _lenguaje_tree_sitter(ruta)

    # 1) Intento con tree-sitter (multi-lenguaje).
    _importar_tree_sitter()
    if tree_sitter is not None and _ts_lang is not None and lenguaje:
        try:
            idioma = _ts_lang.get_language(lenguaje)
            parser = tree_sitter.Parser()
            try:
                parser.set_language(idioma)          # API antigua (<0.22)
            except (AttributeError, TypeError):
                parser.language = idioma             # API nueva (>=0.22)
            arbol = parser.parse(contenido.encode("utf-8"))
            simbolos = _extraer_simbolos_ts(arbol, lenguaje)
            return {"ok": True, "ruta": ruta, "motor": "tree-sitter",
                    "lenguaje": lenguaje, **simbolos}
        except Exception as exc:                 # gram·tica ausente, API distinta...
            depurar(f"[ast_avanzado] tree-sitter fallÛ ({exc}); fallback a ast.")

    # 2) Fallback: ast de la stdlib (solo Python).
    if lenguaje == "python":
        base = _tool_ast(ruta)
        if base.get("ok"):
            return {**base, "motor": "ast", "lenguaje": "python"}
        return base
    return {"ok": False, "ruta": ruta, "lenguaje": lenguaje,
            "error": "sin analizador disponible para este lenguaje "
                     "(instala tree-sitter: pip install snapcontext[mcp_avanzado])"}


def _tool_semantic_search(consulta: str, directorio: str = ".",
                          max_resultados: int = 10) -> dict:
    """Herramienta `semantic_search` (v1.4.0).

    B˙squeda sem·ntica por embeddings integrada en el sistema MCP: el agente
    puede usarla autom·ticamente como contexto. Falla elegantemente si el
    extra `embeddings` no est· instalado.
    """
    if not consulta.strip():
        return {"ok": False, "error": "falta la consulta de b˙squeda"}
    if not _embeddings_disponibles():
        return {"ok": False, "consulta": consulta,
                "error": "b˙squeda sem·ntica no disponible; instala el extra "
                         "'embeddings' (pip install snapcontext[embeddings])"}
    try:
        resultados = _buscar_semanticamente(consulta, directorio,
                                            max_resultados=max(1, max_resultados))
    except Exception as exc:                      # nunca romper al agente
        return {"ok": False, "consulta": consulta, "error": str(exc)}
    return {"ok": bool(resultados), "consulta": consulta,
            "directorio": str(directorio), "total": len(resultados),
            "resultados": resultados}


# --- Dispatcher MCP: valida permisos y ejecuta la herramienta --------------
def _ejecutar_herramienta_mcp(nombre: str, argumentos: Optional[dict] = None,
                              confirmar: Optional[bool] = None) -> dict:
    """Ejecuta una herramienta MCP por nombre con argumentos ``dict``.

    Devuelve un resultado estructurado::

        {"ok": bool, "herramienta": nombre, "resultado": <dict>,
         "error": str|None}

    Si la herramienta requiere permiso, pasa por ``_confirmar_accion``
    (tipo "herramienta"); denegada devuelve ok=False sin ejecutarla.
    """
    argumentos = argumentos or {}
    # v6.22.0: hook `before_tool_use` ‚Äî puede enriquecer argumentos o abortar.
    _ctx_hook = {"herramienta": nombre, "argumentos": argumentos}
    _abortado, _ctx_hook = _hooks.ejecutar_hook("before_tool_use", _ctx_hook)
    if _abortado:
        return {"ok": False, "herramienta": nombre,
                "error": "abortado por hook before_tool_use"}
    argumentos = _ctx_hook.get("argumentos") or argumentos
    herramientas = _cargar_herramientas_mcp()
    cfg = herramientas.get(nombre)
    if cfg is None:
        return {"ok": False, "herramienta": nombre, "error":
                f"herramienta desconocida '{nombre}'. Disponibles: "
                f"{', '.join(sorted(herramientas))}"}

    if cfg.get("requiere_permiso"):
        detalles = json.dumps(argumentos, ensure_ascii=False) if argumentos else None
        if not _confirmar_accion(f"usar herramienta '{nombre}'",
                                 tipo="herramienta", detalles=detalles,
                                 confirmar=confirmar):
            return {"ok": False, "herramienta": nombre,
                    "error": "denegado por el usuario"}

    depurar(f"[mcp] Ejecutando herramienta '{nombre}' con {argumentos}")
    try:
        if nombre == "grep":
            resultado = _tool_grep(
                str(argumentos.get("patron", "")),
                str(argumentos.get("directorio", ".")),
                int(argumentos.get("max_resultados", 50)))
        elif nombre == "read_file":
            resultado = _tool_read_file(
                str(argumentos.get("ruta", "")),
                _entero_opcional(argumentos.get("linea_inicio")),
                _entero_opcional(argumentos.get("linea_fin")))
        elif nombre == "list_files":
            resultado = _tool_list_files(
                str(argumentos.get("directorio", ".")),
                argumentos.get("extensiones"),
                int(argumentos.get("max_archivos", 200)))
        elif nombre == "ast":
            resultado = _tool_ast(str(argumentos.get("ruta", "")))
        elif nombre == "ast_avanzado":
            resultado = _tool_ast_avanzado(str(argumentos.get("ruta", "")))
        elif nombre == "semantic_search":
            resultado = _tool_semantic_search(
                str(argumentos.get("consulta", "")),
                str(argumentos.get("directorio", ".")),
                _entero_opcional(argumentos.get("max_resultados")) or 10)
        elif nombre == "git_status":
            resultado = _tool_git_status(str(argumentos.get("directorio", ".")))
        elif nombre == "git_diff":
            archivo = argumentos.get("archivo")
            resultado = _tool_git_diff(str(argumentos.get("directorio", ".")),
                                       str(archivo) if archivo else None)
        elif nombre == "execute_command":
            resultado = _tool_execute_command(
                str(argumentos.get("comando", "")),
                str(argumentos.get("directorio", ".")),
                background=bool(argumentos.get("background", False)),
                capture_output=bool(argumentos.get("capture_output", True)))
        elif nombre == "execute_command_status":
            resultado = _estado_proceso_fondo(_entero_opcional(
                argumentos.get("pid")))
        elif nombre == "db_query":
            try:
                import mcp_tools_db as _dbt
                resultado = _dbt.db_query(
                    str(argumentos.get("consulta", "")),
                    auto=bool(argumentos.get("auto", False)))
            except ImportError as exc:
                resultado = {"ok": False,
                             "error": f"mcp_tools_db no disponible: {exc}"}
        elif nombre == "db_schema":
            try:
                import mcp_tools_db as _dbt
                resultado = _dbt.db_schema()
            except ImportError as exc:
                resultado = {"ok": False,
                             "error": f"mcp_tools_db no disponible: {exc}"}
        elif nombre == "api_request":
            try:
                import mcp_tools_api as _apit
                resultado = _apit.api_request(
                    str(argumentos.get("url", "")),
                    metodo=str(argumentos.get("metodo", "GET")),
                    headers=dict(argumentos.get("headers") or {}),
                    body=str(argumentos.get("body", "")),
                    timeout=_entero_opcional(argumentos.get("timeout")) or 15)
            except ImportError as exc:
                resultado = {"ok": False,
                             "error": f"mcp_tools_api no disponible: {exc}"}
        elif nombre == "api_inspect":
            try:
                import mcp_tools_api as _apit
                resultado = _apit.api_inspect(
                    str(argumentos.get("url", "")),
                    timeout=_entero_opcional(argumentos.get("timeout")) or 15)
            except ImportError as exc:
                resultado = {"ok": False,
                             "error": f"mcp_tools_api no disponible: {exc}"}
        elif nombre.startswith("browser_"):
            # v6.10.0: herramientas de navegador (Playwright, modo --browser).
            try:
                import mcp_tools_browser as _btool
            except ImportError as exc:
                resultado = {"ok": False,
                             "error": f"mcp_tools_browser no disponible: {exc}"}
            else:
                accion = nombre[len("browser_"):]
                if accion == "abrir":
                    resultado = _btool.browser_abrir(
                        str(argumentos.get("url", "")),
                        wait_for=(str(argumentos["wait_for"])
                                  if argumentos.get("wait_for") else None),
                        timeout=_entero_opcional(
                            argumentos.get("timeout")) or 30)
                elif accion == "screenshot":
                    resultado = _btool.browser_screenshot(
                        str(argumentos.get("url", "") or ""),
                        full_page=bool(argumentos.get("full_page", False)),
                        selector=(str(argumentos["selector"])
                                  if argumentos.get("selector") else None))
                elif accion == "click":
                    resultado = _btool.browser_click(
                        str(argumentos.get("selector", "")))
                elif accion == "type":
                    resultado = _btool.browser_type(
                        str(argumentos.get("selector", "")),
                        str(argumentos.get("texto", "")))
                elif accion == "get_text":
                    resultado = _btool.browser_get_text(
                        str(argumentos.get("selector", "")))
                elif accion == "analizar_imagen":
                    resultado = _btool.browser_analizar_imagen(
                        str(argumentos.get("imagen_base64", "")),
                        str(argumentos.get("pregunta", "")))
                elif accion == "cerrar":
                    resultado = _btool.browser_cerrar()
                else:
                    resultado = {"ok": False,
                                 "error": f"acciÛn desconocida: {nombre}"}
        else:
            # Herramienta de usuario definida en mcp_tools.json ‚Üí comando.
            if cfg.get("plugin"):
                # v4.0.0: los plugins reciben los argumentos como JSON por
                # stdin y responden un JSON {"ok": ..., ...} por stdout.
                try:
                    import subprocess as _subprocess
                    comando_plugin = str(cfg.get("comando") or "")
                    # seguridad: se valida el peligro antes de ejecutar
                    # el comando de un plugin definido por el usuario.
                    if es_comando_peligroso(comando_plugin):
                        resultado = {
                            "ok": False,
                            "error": "Comando del plugin bloqueado "
                                     "(detecciÛn de peligro)."}
                    else:
                        # seguridad: helper seguro. Los plugins reciben
                        # los argumentos por stdin y responden JSON por stdout.
                        import sandbox_utils as _su
                        proceso = _su.ejecutar_comando_con_politica(
                            comando_plugin, timeout=120,
                            entrada=json.dumps(argumentos, ensure_ascii=False))
                        lineas = (proceso.stdout or "").strip().splitlines()
                        analizado = json.loads(lineas[-1]) if lineas else None
                        if isinstance(analizado, dict):
                            analizado.setdefault("ok", proceso.returncode == 0)
                            resultado = analizado
                        else:
                            resultado = {
                                "ok": proceso.returncode == 0,
                                "codigo_retorno": proceso.returncode,
                                "stdout": (proceso.stdout or "").strip(),
                                "stderr": (proceso.stderr or "").strip()}
                except subprocess.TimeoutExpired:
                    resultado = {"ok": False,
                                 "error": "el plugin excediÛ el tiempo lÌmite"}
                except Exception as exc:    # noqa: BLE001 ‚Äî blindaje agente
                    resultado = {"ok": False, "error": str(exc)}
            else:
                resultado = _tool_execute_command(
                    cfg["comando"], str(argumentos.get("directorio", ".")))
    except Exception as exc:                    # blindaje del agente
        resultado = {"ok": False, "error": f"excepciÛn: {exc}"}
    _salida = {"ok": bool(resultado.get("ok")), "herramienta": nombre,
               "resultado": resultado}
    # v6.22.0: hook `after_tool_use` ‚Äî observabilidad / auditorÌa post-llamada.
    try:
        _hooks.ejecutar_hook("after_tool_use", {
            "herramienta": nombre, "argumentos": argumentos,
            "resultado": _salida})
    except Exception:                            # noqa: BLE001 ‚Äî nunca romper
        pass
    return _salida


def _entero_opcional(valor) -> Optional[int]:
    """Convierte a int o devuelve None (para argumentos de herramientas)."""
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _formatear_resultado_mcp(llamada: dict, max_lineas: int = 40) -> str:
    """Convierte el resultado de una llamada MCP en texto legible."""
    if not llamada.get("ok"):
        return f"‚úñ {llamada.get('herramienta', 'herramienta')}: " \
               f"{llamada.get('error', 'fallo')}"
    res = llamada.get("resultado", {})
    partes: List[str] = []
    for clave, valor in res.items():
        if clave in ("contenido", "diff") and isinstance(valor, str):
            lineas = valor.splitlines()
            muestra = "\n".join(lineas[:max_lineas])
            extra = f"\n‚Ä¶ (+{len(lineas) - max_lineas} lÌneas)" \
                if len(lineas) > max_lineas else ""
            partes.append(f"{clave}:\n{muestra}{extra}")
        elif isinstance(valor, list):
            muestra = ", ".join(map(str, valor[:20]))
            extra = " ‚Ä¶" if len(valor) > 20 else ""
            partes.append(f"{clave} ({len(valor)}): {muestra}{extra}")
        else:
            partes.append(f"{clave}: {valor}")
    return "\n".join(partes) or "(sin datos)"


def _contexto_automatico_mcp(mensaje: str, max_llamadas: int = 2) -> str:
    """Uso autom·tico de herramientas de solo lectura seg˙n el mensaje.

    HeurÌstica ligera: si el usuario pregunta dÛnde est· algo, el estado del
    repo o quÈ archivos hay, se ejecutan hasta ``max_llamadas`` herramientas
    de solo lectura y se devuelve un bloque de contexto (str) para aÒadir al
    prompt del proveedor. Cadena vacÌa si no aplica.
    """
    texto = mensaje.lower()
    llamadas: List[tuple] = []

    if any(p in texto for p in ("busca ", "buscar ", "dÛnde est·",
                                "donde esta", "grep", "quiÈn usa",
                                "quien usa")):
        # TÈrminos demasiado genÈricos para usar como patrÛn de b˙squeda.
        paradas = {"busca", "buscar", "dÛnde", "donde", "est·", "esta",
                   "quiÈn", "quien", "usa", "usan", "usado", "usar", "usos"}
        candidatos = [p for p in re.findall(r"\w+", mensaje)
                      if len(p) >= 3 and p.lower() not in paradas]
        if candidatos:
            # El tÈrmino m·s largo suele ser el identificador relevante.
            llamadas.append(("grep",
                             {"patron": max(candidatos, key=len)}))
    if any(p in texto for p in ("estado de git", "git status", "sin commitear",
                                "cambios pendientes")):
        llamadas.append(("git_status", {}))
    if any(p in texto for p in ("lista los archivos", "list_files",
                                "quÈ archivos hay", "que archivos hay")):
        llamadas.append(("list_files", {"max_archivos": 50}))

    bloques: List[str] = []
    for nombre, argumentos in llamadas[:max_llamadas]:
        llamada = _ejecutar_herramienta_mcp(nombre, argumentos)
        bloques.append(f"[{nombre}] "
                       + _formatear_resultado_mcp(llamada, max_lineas=15))
    return "\n".join(bloques)


# ---------------------------------------------------------------------------
# Memoria de proyecto (CLAUDE.md / SNAPCONTEXT.md) ‚Äî v0.15.0
# ---------------------------------------------------------------------------
NOMBRES_MEMORIA = ("CLAUDE.md", "SNAPCONTEXT.md")
MEMORIA_MAX_CARACTERES = 6000

# Contexto persistente del proyecto cargado al inicio (cadena vacÌa si no hay
# memoria). La rellenan flujo_principal, --chat y --plan.
MEMORIA_PROYECTO = ""

# Skills din·micos (v6.6.0): extracciÛn de reglas abstractas de planes
# exitosos. Activado por defecto; se desactiva con --sin-skills-dinamicos.
SKILLS_DINAMICOS = True


def _enriquecer_prompt_con_reglas(prompt: str, consulta: str) -> str:
    """Skills din·micos (v6.6.0): aÒade las reglas aprendidas que coinciden
    con ``consulta`` al ``prompt`` del planificador (m·x. 3, priorizadas por
    confianza). Si ``SKILLS_DINAMICOS`` est· desactivado, no hay reglas o
    falla la b˙squeda, devuelve el prompt intacto. Nunca lanza.
    """
    if not SKILLS_DINAMICOS:
        return prompt
    try:
        import skill_abstraction as _sa
        reglas = _sa.buscar_reglas(consulta)
        if reglas:
            bloque = "\n".join(_sa.regla_a_linea(r) for r in reglas)
            prompt += ("\n\nREGLAS APRENDIDAS de tareas anteriores "
                       "(tenlas en cuenta al proponer pasos):\n" + bloque)
            info("?? Regla(s) aprendida(s) aplicada(s) al plan ("
                 f"{len(reglas)}).")
    except Exception as exc:             # noqa: BLE001 ‚Äî best-effort
        depurar(f"[skills-dinamicos] b˙squeda de reglas fallÛ: {exc}")
    return prompt


def _buscar_claude_md(raiz: str = ".") -> Optional[Path]:
    """Devuelve la ruta de CLAUDE.md (o SNAPCONTEXT.md) en ``raiz``, o None."""
    for nombre in NOMBRES_MEMORIA:
        camino = Path(raiz) / nombre
        if camino.is_file():
            return camino
    return None


def _cargar_claude_md(raiz: str = ".",
                      max_caracteres: int = MEMORIA_MAX_CARACTERES) -> str:
    """Carga el contenido de la memoria del proyecto (o "" si no existe).

    Se recorta a ``max_caracteres`` para no desbordar el contexto del modelo.
    """
    camino = _buscar_claude_md(raiz)
    if camino is None:
        return ""
    try:
        contenido = camino.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        aviso(f"No se pudo leer {camino}: {exc}")
        return ""
    if len(contenido) > max_caracteres:
        contenido = contenido[:max_caracteres] + "\n\n‚Ä¶ (recortado)"
    return contenido


def _plantilla_claude_md_basica(directorio: str = ".") -> str:
    """Plantilla offline generada con un escaneo local (sin IA).

    Se usa como fallback de ``--init-claude`` cuando no hay proveedor
    disponible o falla la llamada.
    """
    tipo = _detectar_tipo_proyecto(str(Path(directorio).resolve())) or "desconocido"
    listado = _tool_list_files(directorio, max_archivos=40)
    archivos = listado.get("archivos", [])
    manifiestos = [n for n in ("pubspec.yaml", "package.json", "pyproject.toml",
                               "requirements.txt", "go.mod", "Cargo.toml")
                   if (Path(directorio) / n).is_file()]
    return (
        "# Memoria del proyecto\n\n"
        f"Generada por SnapContext v{VERSION} (modo b·sico, sin IA).\n\n"
        "## Objetivo\n\n(Describe aquÌ para quÈ sirve este proyecto.)\n\n"
        f"## TecnologÌas\n\n- Tipo de proyecto detectado: **{tipo}**\n"
        + ("- Manifiestos encontrados: " + ", ".join(manifiestos) + "\n"
           if manifiestos else "- Sin manifiestos detectados.\n")
        + "\n## Estructura\n\nArchivos principales:\n"
        + "".join(f"- {a}\n" for a in archivos[:20])
        + "\n## Convenciones\n\n"
          "- (Describe convenciones de estilo y ramas.)\n\n"
          "## Comandos ˙tiles\n\n"
          "- (Describe cÛmo ejecutar tests/build.)\n")


def _generar_claude_md(proveedor: Optional[str] = None,
                       modelo: Optional[str] = None,
                       directorio: str = ".") -> Path:
    """Genera un CLAUDE.md inicial escaneando el proyecto (``--init-claude``).

    Usa el proveedor de IA para redactar el contenido; si falta clave/librerÌa
    o la llamada falla, cae a una plantilla b·sica offline. Devuelve la ruta
    escrita. Si ya existÌa memoria, pide confirmaciÛn antes de sobreescribir.
    """
    raiz = Path(directorio).resolve()
    destino = _buscar_claude_md(str(raiz)) or (raiz / "CLAUDE.md")

    # 1) Escaneo local: tipo de proyecto, estructura y estado git (vÌa MCP).
    tipo = _detectar_tipo_proyecto(str(raiz)) or "desconocido"
    listado = _ejecutar_herramienta_mcp(
        "list_files", {"directorio": str(raiz), "max_archivos": 60},
        confirmar=False)
    estructura = "\n".join(listado["resultado"]["archivos"]) \
        if listado.get("ok") else "(escaneo no disponible)"
    estado_git = _ejecutar_herramienta_mcp("git_status",
                                           {"directorio": str(raiz)},
                                           confirmar=False)

    prompt = (
        "Eres un asistente que documenta proyectos. Analiza esta informaciÛn "
        "de un proyecto y genera el contenido de un archivo CLAUDE.md: la "
        "memoria persistente de un agente de cÛdigo.\n\n"
        f"Tipo de proyecto detectado: {tipo}\n"
        f"Estado git: "
        f"{json.dumps(estado_git.get('resultado', {}), ensure_ascii=False)}\n"
        f"Estructura de archivos:\n{estructura}\n\n"
        "Devuelve SOLO el contenido markdown del archivo, con estas secciones:\n"
        "# <nombre del proyecto>\n## Objetivo\n## TecnologÌas\n"
        "## Estructura\n## Convenciones\n## Comandos ˙tiles\n"
        "SÈ concreto y breve (m·ximo ~80 lÌneas).")

    contenido = ""
    preferencias = cargar_configuracion()
    proveedor = proveedor or preferencias.get("provider") or PROVEEDOR_DEFECTO
    try:
        contenido = _enviar_al_proveedor(proveedor, modelo,
                                         [{"role": "user", "content": prompt}])
        info(f"Contenido generado con {PROVEEDORES[proveedor]['nombre']}.")
    except RuntimeError as exc:
        aviso(f"Sin generaciÛn por IA ({str(exc).splitlines()[0]}); "
              "se usar· una plantilla b·sica.")
    if not contenido.strip():
        contenido = _plantilla_claude_md_basica(str(raiz))

    # 2) ConfirmaciÛn si se va a sobreescribir una memoria existente.
    if destino.exists() and not _confirmar_accion(
            f"sobreescribir {destino.name}", tipo="editar",
            detalles=f"tamaÒo actual: {destino.stat().st_size} bytes"):
        aviso("OperaciÛn cancelada; no se modificÛ la memoria.")
        return destino

    destino.write_text(contenido.strip() + "\n", encoding="utf-8")
    exito(f"Memoria de proyecto creada: {destino}")
    return destino


def _actualizar_claude_md_automatico(resumen_tarea: str,
                                     directorio: str = ".") -> bool:
    """Tras una tarea significativa, propone actualizar la memoria (opcional).

    Pide confirmaciÛn; si se acepta, el proveedor reescribe la memoria
    incorporando el resumen de lo aprendido. Solo act˙a si ya existe memoria:
    la creaciÛn inicial es responsabilidad de ``--init-claude``.
    """
    camino = _buscar_claude_md(directorio)
    if camino is None:
        return False
    actual = _cargar_claude_md(directorio)
    if CONFIRMAR_ACCIONES and not _confirmar_accion(
            f"actualizar {camino.name} con lo aprendido", tipo="editar",
            detalles=resumen_tarea[:200]):
        return False
    prompt = (
        "Actualiza esta memoria de proyecto incorporando la informaciÛn nueva. "
        "MantÈn el formato y las secciones; devuelve SOLO el markdown final.\n\n"
        f"--- MEMORIA ACTUAL ---\n{actual or '(vacÌa)'}\n\n"
        f"--- LO APRENDIDO EN LA √öLTIMA TAREA ---\n{resumen_tarea}\n")
    preferencias = cargar_configuracion()
    try:
        nuevo = _enviar_al_proveedor(preferencias.get("provider")
                                     or PROVEEDOR_DEFECTO, None,
                                     [{"role": "user", "content": prompt}])
    except RuntimeError as exc:
        aviso(f"No se pudo actualizar la memoria: {str(exc).splitlines()[0]}")
        return False
    if not nuevo.strip():
        aviso("El proveedor devolviÛ contenido vacÌo; memoria sin cambios.")
        return False
    camino.write_text(nuevo.strip() + "\n", encoding="utf-8")
    exito(f"Memoria actualizada: {camino}")
    return True


# ---------------------------------------------------------------------------
# Embeddings locales: b˙squeda sem·ntica de archivos ‚Äî v1.1.0
# ---------------------------------------------------------------------------
MENSAJE_EMBEDDINGS_FALTANTE = (
    "La b˙squeda sem·ntica requiere la librerÌa 'sentence-transformers'.\n"
    "Inst·lala con:  pip install snapcontext[embeddings]\n"
    "  (descarga torch; primera ejecuciÛn descarga el modelo "
    "all-MiniLM-L6-v2, ~90 MB)"
)

INDICE_DIR = CONFIG_DIR / "index"
MODELO_EMBEDDINGS_NOMBRE = "all-MiniLM-L6-v2"
EXTENSIONES_EMBEDDINGS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".dart", ".go", ".rs", ".java",
    ".kt", ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift",
    ".md", ".yaml", ".yml", ".toml",
}
CARPETAS_IGNORADAS = {".git", "__pycache__", "node_modules", "venv", ".venv",
                      "dist", "build", ".idea", ".vscode"}
CHUNK_CARACTERES = 2000          # ~512 tokens con heurÌstica de 4 chars/token

_MODELO_EMBEDDINGS = None        # singleton del modelo cargado

# v6.9.0 ‚Äî CachÈ persistente de embeddings (SQLite)
# `~/.snapcontext/embeddings.db` almacena el vector por hash de contenido del
# fragmento. En re-escaneos solo se recalculan los fragmentos cuyo contenido
# cambiÛ (reutiliza el resto), reduciendo el tiempo de selecciÛn hasta ~80%.
# Es opcional y best-effort: si no hay soporte/espacio en disco falla
# silenciosamente y se recomputa todo desde cero.
EMBEDDINGS_DB = CONFIG_DIR / "embeddings.db"


def _serializar_vector(vector) -> bytes:
    """Empaqueta un vector de floats como bytes (``struct`` '<Nd')."""
    import struct
    return struct.pack(f"<{len(vector)}d", *(float(x) for x in vector))


def _deserializar_vector(blob: bytes) -> List[float]:
    """Desempaqueta un blob a su lista de floats original."""
    import struct
    n = len(blob) // struct.calcsize("<d")
    return list(struct.unpack(f"<{n}d", blob))


def _init_db_embeddings(con) -> None:
    """Crea la tabla de embeddings si no existe."""
    with con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS embeddings ("
            "hash TEXT PRIMARY KEY, archivo TEXT, embedding BLOB)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_emb_archivo "
                    "ON embeddings(archivo)")


def _conexion_embeddings():
    """Abre (y prepara) la cachÈ SQLite de embeddings, o None si falla."""
    try:
        EMBEDDINGS_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(EMBEDDINGS_DB), timeout=2.0)
        _init_db_embeddings(con)
        return con
    except Exception:                       # noqa: BLE001 ‚Äî cachÈ best-effort
        return None


def _consultar_embedding_cache(clave_hash: str) -> Optional[bytes]:
    """Devuelve el blob del embedding cached por ``clave_hash`` o None."""
    try:
        import sqlite3 as _sqlite3
        con = _conexion_embeddings()
        if con is None:
            return None
        try:
            fila = con.execute(
                "SELECT embedding FROM embeddings WHERE hash = ?",
                (clave_hash,)).fetchone()
            return fila[0] if fila and fila[0] else None
        finally:
            con.close()
    except _sqlite3.Error:                   # noqa: BLE001
        return None


def _guardar_embedding_cache(clave_hash: str, archivo: str, vector) -> bool:
    """Guarda o actualiza un embedding en la cachÈ SQLite (best-effort)."""
    try:
        import sqlite3 as _sqlite3
        con = _conexion_embeddings()
        if con is None:
            return False
        try:
            with con:
                con.execute(
                    "INSERT INTO embeddings (hash, archivo, embedding) "
                    "VALUES (?, ?, ?) ON CONFLICT(hash) DO UPDATE SET "
                    "archivo=excluded.archivo, embedding=excluded.embedding",
                    (clave_hash, archivo, _serializar_vector(vector)))
            return True
        finally:
            con.close()
    except _sqlite3.Error:                   # noqa: BLE001
        return False


def _embeddings_disponibles() -> bool:
    """True si sentence-transformers est· instalado."""
    return _importar_sentence_transformer() is not None


def _modelo_embeddings():
    """Devuelve el modelo de embeddings (singleton) o None si no est· instalado.

    Si ``sc._MODELO_EMBEDDINGS`` ya fue establecido (p. ej. por tests o por una
    carga previa), se reutiliza tal cual.
    """
    global _MODELO_EMBEDDINGS
    if _MODELO_EMBEDDINGS is not None:
        return _MODELO_EMBEDDINGS
    if _importar_sentence_transformer() is None:
        return None
    try:
        _MODELO_EMBEDDINGS = SentenceTransformer(MODELO_EMBEDDINGS_NOMBRE)
    except Exception as exc:            # sin red para descargar el modelo, etc.
        aviso(f"No se pudo cargar el modelo de embeddings: {exc}")
        return None
    return _MODELO_EMBEDDINGS


def _calcular_embeddings(textos: List[str]) -> List[List[float]]:
    """Calcula embeddings para una lista de textos (lista de vectores).

    Lanza RuntimeError con MENSAJE_EMBEDDINGS_FALTANTE si la librerÌa no est·
    disponible. Normaliza los vectores a longitud 1 para que la similitud de
    coseno sea un simple producto escalar.
    """
    modelo = _modelo_embeddings()
    if modelo is None:
        raise RuntimeError(MENSAJE_EMBEDDINGS_FALTANTE)
    vectores = modelo.encode(textos, normalize_embeddings=True)
    return [[float(x) for x in vector] for vector in vectores]


def _calcular_embeddings_con_cache(
        textos: List[str], claves: Optional[List[tuple]] = None) -> List[List[float]]:
    """Calcula embeddings reutilizando la cachÈ SQLite persistente (v6.9.0).

    Para cada ``texto`` consulta ``~/.snapcontext/embeddings.db`` por el hash de
    su contenido; si existe, reutiliza el vector y solo recalcula los que fallan
    (cambio de contenido o primera vez), guardando los nuevos en cachÈ. AsÌ, en
    proyectos re-escaneados se reduce el tiempo de selecciÛn hasta ~80%.
    Payload por si la cachÈ no est· disponible: recalcula todo desde cero.
    """
    vectores: List[Optional[List[float]]] = [None] * len(textos)
    pendientes: List[int] = []
    for i, texto in enumerate(textos):
        blob = _consultar_embedding_cache(_hash_texto(texto))
        if blob is not None:
            try:
                vectores[i] = _deserializar_vector(blob)
            except Exception:               # noqa: BLE001 ‚Äî blob corrupto
                vectores[i] = None
        if vectores[i] is None:
            pendientes.append(i)
    if pendientes:
        aviso(f"[embeddings] Calculando embeddings de {len(pendientes)} "
              f"fragmento(s) nuevo(s)‚Ä¶")
        nuevos = _calcular_embeddings([textos[i] for i in pendientes])
        for j, idx in enumerate(pendientes):
            vectores[idx] = nuevos[j]
            archivo = claves[idx][0] if claves and idx < len(claves) else ""
            _guardar_embedding_cache(_hash_texto(textos[idx]), archivo,
                                     nuevos[j])
    return [v for v in vectores if v is not None]  # type: ignore[return-value]


def _similitud_coseno(a: List[float], b: List[float]) -> float:
    """Similitud de coseno entre dos vectores (sin depender de numpy)."""
    punto = sum(x * y for x, y in zip(a, b))
    norma_a = sum(x * x for x in a) ** 0.5
    norma_b = sum(x * x for x in b) ** 0.5
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return punto / (norma_a * norma_b)


def _hash_texto(texto: str) -> str:
    import hashlib
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def _dividir_en_fragmentos(texto: str,
                           max_caracteres: int = CHUNK_CARACTERES) -> List[dict]:
    """Divide el contenido en fragmentos de ~``max_caracteres`` (~512 tokens).

    Corta por lÌneas para no partir sentencias a mitad y registra la lÌnea de
    inicio de cada fragmento (1-based).
    """
    fragmentos: List[dict] = []
    actual: List[str] = []
    linea_inicio = 1
    linea_actual = 0
    for numero, linea in enumerate(texto.splitlines(), start=1):
        linea_actual = numero
        actual.append(linea)
        if sum(len(l) + 1 for l in actual) >= max_caracteres:
            fragmentos.append({"linea_inicio": linea_inicio,
                               "texto": "\n".join(actual)})
            actual = []
            linea_inicio = numero + 1
    if actual:
        fragmentos.append({"linea_inicio": linea_inicio,
                           "texto": "\n".join(actual)})
    if linea_actual == 0:               # archivo vacÌo
        fragmentos.append({"linea_inicio": 1, "texto": ""})
    return fragmentos


def _patrones_gitignore(raiz: Path) -> List[str]:
    """Lee .gitignore de ``raiz`` y devuelve patrones simples (fnmatch)."""
    patrones: List[str] = []
    gitignore = raiz / ".gitignore"
    try:
        if gitignore.is_file():
            for linea in gitignore.read_text(encoding="utf-8",
                                             errors="replace").splitlines():
                linea = linea.strip()
                if linea and not linea.startswith("#") and not linea.startswith("!"):
                    patrones.append(linea.rstrip("/"))
    except OSError:
        pass
    return patrones


def _ruta_indice(directorio: str) -> Path:
    """Ruta del Ìndice en disco para ``directorio`` (hash de la ruta absoluta)."""
    clave = _hash_texto(str(Path(directorio).resolve()))
    return INDICE_DIR / f"{clave}.json"


def _cargar_indice(directorio: str) -> dict:
    """Lee el Ìndice de embeddings de ``directorio`` ({} si no existe)."""
    camino = _ruta_indice(directorio)
    try:
        if camino.is_file():
            datos = json.loads(camino.read_text(encoding="utf-8"))
            if isinstance(datos, dict) and "fragmentos" in datos:
                return datos
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"√çndice de embeddings corrupto ({camino}): {exc}")
    return {}


def _guardar_indice(directorio: str, indice: dict) -> bool:
    """Persiste el Ìndice en ~/.snapcontext/index/<hash>.json."""
    try:
        INDICE_DIR.mkdir(parents=True, exist_ok=True)
        _ruta_indice(directorio).write_text(
            json.dumps(indice, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError as exc:
        aviso(f"No se pudo guardar el Ìndice: {exc}")
        return False


def _es_ignorado(relativo: str, patrones: List[str]) -> bool:
    """True si ``relativo`` (ruta POSIX relativa) casa con alg˙n patrÛn."""
    partes = relativo.split("/")
    for patron in patrones:
        if fnmatch.fnmatch(relativo, patron) or fnmatch.fnmatch(
                partes[-1], patron):
            return True
        # PatrÛn de directorio: ignorar todo lo que cuelga de Èl.
        if any(fnmatch.fnmatch(parte, patron) for parte in partes):
            return True
    return False


def _hash_proyecto(raiz) -> str:
    """Computa un hash que representa el estado actual del proyecto.

    Recorre los archivos de cÛdigo (misma lÛgica que ``_indexar_proyecto`` pero
    sin calcular embeddings) y devuelve un hash combinado de todos los hashes de
    contenido. Muy r·pido comparado con el indexado completo.
    """
    raiz = raiz if isinstance(raiz, Path) else Path(raiz)
    patrones = _patrones_gitignore(raiz)
    hashes: dict = {}
    for camino in sorted(raiz.rglob("*")):
        if not camino.is_file() or camino.suffix.lower() not in EXTENSIONES_EMBEDDINGS:
            continue
        if any(parte in CARPETAS_IGNORADAS for parte in camino.parts):
            continue
        relativo = camino.relative_to(raiz).as_posix()
        if _es_ignorado(relativo, patrones):
            continue
        try:
            contenido = camino.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hashes[relativo] = _hash_texto(contenido)
    return _hash_texto(json.dumps(hashes, sort_keys=True, ensure_ascii=False))


def _indexar_proyecto(directorio: str = ".",
                      extensiones: Optional[set] = None) -> dict:
    """Indexa el proyecto: embeddings por fragmento de cada archivo de cÛdigo.

    - Escanea recursivamente respetando .gitignore y ``CARPETAS_IGNORADAS``.
    - Divide cada archivo en fragmentos (~512 tokens) y calcula su embedding
      con el modelo local (all-MiniLM-L6-v2).
    - Cache por hash de contenido: los archivos sin cambios reutilizan los
      embeddings del Ìndice previo.

    Lanza RuntimeError si los embeddings no est·n disponibles.
    """
    raiz = Path(directorio).resolve()
    if not raiz.is_dir():
        raise RuntimeError(f"El directorio no existe: {raiz}")
    extensiones = extensiones or EXTENSIONES_EMBEDDINGS
    patrones = _patrones_gitignore(raiz)
    indice_previo = _cargar_indice(str(raiz))
    fragmentos_previos = {(f["archivo"], f.get("hash_archivo")): f
                          for f in indice_previo.get("fragmentos", [])}

    # 1) Recolectar archivos candidatos (relativo, contenido, hash) en paralelo.
    candidatos_rutas: List[Path] = []
    for camino in sorted(raiz.rglob("*")):
        if not camino.is_file() or camino.suffix.lower() not in extensiones:
            continue
        if any(parte in CARPETAS_IGNORADAS for parte in camino.parts):
            continue
        relativo = camino.relative_to(raiz).as_posix()
        if _es_ignorado(relativo, patrones):
            continue
        candidatos_rutas.append(camino)

    def _leer_archivo_candidato(camino: Path) -> Optional[tuple]:
        rel = camino.relative_to(raiz).as_posix()
        try:
            cont = camino.read_text(encoding="utf-8", errors="replace")
            return (rel, cont, _hash_texto(cont))
        except OSError as exc:
            aviso(f"No se pudo leer {rel}: {exc}")
            return None

    archivos: List[tuple] = []
    if candidatos_rutas:
        num_hilos = min(8, len(candidatos_rutas), (os.cpu_count() or 4) * 2)
        with ThreadPoolExecutor(max_workers=num_hilos) as pool:
            resultados = pool.map(_leer_archivo_candidato, candidatos_rutas)
            for res in resultados:
                if res is not None:
                    archivos.append(res)

    if not archivos:
        raise RuntimeError("No se encontraron archivos de cÛdigo para indexar.")

    # 2) Separar fragmentos cacheados (mismo hash+texto) de los nuevos.
    fragmentos: List[dict] = []
    nuevos_textos: List[str] = []
    nuevos_claves: List[tuple] = []
    for relativo, contenido, hash_archivo in archivos:
        for frag in _dividir_en_fragmentos(contenido):
            previo = fragmentos_previos.get((relativo, hash_archivo))
            if previo is not None and previo["texto"] == frag["texto"]:
                fragmentos.append({**frag, "archivo": relativo,
                                   "hash_archivo": hash_archivo,
                                   "embedding": previo["embedding"]})
            else:
                nuevos_textos.append(frag["texto"])
                nuevos_claves.append((relativo, hash_archivo,
                                      frag["linea_inicio"]))
                fragmentos.append({**frag, "archivo": relativo,
                                   "hash_archivo": hash_archivo,
                                   "embedding": None})   # marcador temporal

    # 3) Calcular embeddings de los fragmentos nuevos (cachÈ SQLite v6.9.0).
    if nuevos_textos:
        vectores = _calcular_embeddings_con_cache(nuevos_textos, nuevos_claves)
        pendientes = list(zip(nuevos_claves, vectores))
        for frag in fragmentos:
            if frag.get("embedding") is not None:
                continue
            for (relativo, hash_archivo, linea_inicio), vector in pendientes:
                if (frag["archivo"] == relativo
                        and frag["hash_archivo"] == hash_archivo
                        and frag["linea_inicio"] == linea_inicio):
                    frag["embedding"] = vector
                    break
            if frag.get("embedding") is None:
                raise RuntimeError(
                    "No se pudo asignar un embedding a un fragmento "
                    f"({frag['archivo']}:{frag['linea_inicio']})")

    indice = {"version": 1, "directorio": str(raiz),
              "modelo": MODELO_EMBEDDINGS_NOMBRE,
              "hash_proyecto": _hash_proyecto(raiz),
              "hashes": {rel: h for rel, _, h in archivos},
              "fragmentos": fragmentos}
    _guardar_indice(str(raiz), indice)
    return indice


def _asegurar_indice(directorio: str) -> dict:
    """Devuelve el Ìndice del proyecto; lo crea o reindexa si ha cambiado.

    Invalida el cachÈ autom·ticamente cuando el proyecto cambia (se compara el
    ``hash_proyecto`` almacenado con el hash actual) y reindexa con aviso.
    """
    indice = _cargar_indice(directorio)
    if indice.get("fragmentos"):
        hash_actual = _hash_proyecto(Path(directorio).resolve())
        if indice.get("hash_proyecto") == hash_actual:
            return indice
        aviso("[embeddings] El proyecto ha cambiado; reindexando‚Ä¶")
    info("[embeddings] Indexando el proyecto (primera vez o Ìndice vacÌo)‚Ä¶")
    return _indexar_proyecto(directorio)


def _buscar_semanticamente(consulta: str, directorio: str = ".",
                           max_resultados: int = 20) -> List[dict]:
    """B˙squeda sem·ntica: fragmentos m·s similares a ``consulta``.

    Devuelve una lista ordenada por similitud::

        [{"archivo", "linea_inicio", "similitud", "texto"}]

    Lanza RuntimeError si los embeddings no est·n disponibles.
    """
    indice = _asegurar_indice(directorio)
    fragmentos = [f for f in indice.get("fragmentos", [])
                  if f.get("embedding")]
    if not fragmentos:
        return []
    vector_consulta = _calcular_embeddings([consulta])[0]
    puntuados = []
    for frag in fragmentos:
        similitud = _similitud_coseno(vector_consulta, frag["embedding"])
        puntuados.append({"archivo": frag["archivo"],
                          "linea_inicio": frag["linea_inicio"],
                          "similitud": round(similitud, 4),
                          "texto": frag["texto"]})
    puntuados.sort(key=lambda f: f["similitud"], reverse=True)
    return puntuados[:max_resultados]


def _seleccionar_archivos_con_embeddings(consulta: str, directorio: str = ".",
                                         max_archivos: int = 3,
                                         umbral: float = 0.6) -> List[str]:
    """Selecciona archivos relevantes por similitud sem·ntica.

    Agrupa las similitudes por archivo (sumando sus fragmentos), filtra por
    ``umbral`` y devuelve hasta ``max_archivos`` rutas. Si no llegan a
    ``max_archivos``, rellena con los mejores candidatos de la heurÌstica
    local (``escanear_repositorio``) que no estÈn ya incluidos.
    """
    resultados = _buscar_semanticamente(consulta, directorio,
                                        max_resultados=50)
    puntuaciones: dict = {}
    for frag in resultados:
        puntuaciones[frag["archivo"]] = (
            puntuaciones.get(frag["archivo"], 0.0) + frag["similitud"])
    ordenados = sorted(puntuaciones.items(), key=lambda kv: kv[1],
                       reverse=True)
    seleccion = [archivo for archivo, puntuacion in ordenados
                 if puntuacion >= umbral][:max_archivos]

    if len(seleccion) < max_archivos:
        try:
            candidatos = escanear_repositorio(consulta, directorio=directorio)
        except Exception:
            candidatos = []
        for candidato in candidatos:
            if len(seleccion) >= max_archivos:
                break
            if candidato not in seleccion:
                seleccion.append(candidato)
    return seleccion
# ---------------------------------------------------------------------------
# Editor web y visualizaciÛn de dependencias ‚Äî v1.2.0
# ---------------------------------------------------------------------------
# Mapa extensiÛn ‚Üí lenguaje de Monaco Editor (resaltado de sintaxis).
_MAPA_LENGUAJE_MONACO = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".dart": "dart", ".go": "go", ".rs": "rust", ".java": "java",
    ".kt": "kotlin", ".rb": "ruby", ".php": "php", ".c": "c", ".cpp": "cpp",
    ".h": "cpp", ".hpp": "cpp", ".hxx": "cpp", ".cs": "csharp",
    ".swift": "swift", ".md": "markdown", ".json": "json", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "ini", ".html": "html", ".css": "css",
    ".sh": "shell", ".bash": "shell", ".sql": "sql", ".xml": "xml",
}
# Extensiones de cÛdigo consideradas al construir el grafo de dependencias.
_GRP_EXT_DEPS = {
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".dart", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".swift",
}


def _comando_para_monaco(archivo: str) -> str:
    """Devuelve el id de lenguaje de Monaco para ``archivo`` (detecciÛn por ext.)."""
    ext = Path(archivo).suffix.lower()
    return _MAPA_LENGUAJE_MONACO.get(ext, "plaintext")


def _extraer_dependencias(contenido: str, lenguaje: str) -> List[str]:
    """Extrae las referencias de importaciÛn de ``contenido`` para ``lenguaje``.

    Devuelve una lista ordenada y sin duplicados de mÛdulos/sÌmbolos importados.
    No resuelve a rutas absolutas: eso lo hace :func:`_grafo_dependencias` junto
    con el Ìndice de archivos del proyecto.
    """
    dependencias: set = set()

    if lenguaje == "python":
        for m in re.finditer(
                r"^\s*(?:from\s+([\w.]+)\s+import|\bimport\s+([\w.]+))",
                contenido, re.M):
            modulo = (m.group(1) or m.group(2) or "").split(".")[0]
            if modulo:
                dependencias.add(modulo)
    elif lenguaje in ("javascript", "typescript"):
        for m in re.finditer(
                r"(?:from\s+['\"]([^'\"]+)['\"]"
                r"|require\(\s*['\"]([^'\"]+)['\"]\s*\))", contenido):
            modulo = (m.group(1) or m.group(2) or "")
            if modulo:
                dependencias.add(modulo)
    elif lenguaje == "dart":
        for m in re.finditer(r"^\s*import\s+['\"]([^'\"]+)['\"]", contenido, re.M):
            if m.group(1):
                dependencias.add(m.group(1))
    elif lenguaje == "go":
        for m in re.finditer(r"^\s*[\w.]+\s+\"([^\"]+)\"", contenido, re.M):
            if m.group(1):
                dependencias.add(m.group(1))
    elif lenguaje == "rust":
        for m in re.finditer(r"^\s*(?:use|extern crate)\s+([\w:]+)", contenido, re.M):
            if m.group(1):
                dependencias.add(m.group(1))
    elif lenguaje in ("java", "kotlin"):
        for m in re.finditer(r"^\s*import\s+([\w.]+)", contenido, re.M):
            simbolo = (m.group(1) or "").split(".")[-1]
            if simbolo:
                dependencias.add(simbolo)

    return sorted(d for d in dependencias if d and d != "__future__")


def _resolver_dependencia(rel, camino, dep, por_ruta, por_stem, raiz):
    """Intenta localizar un archivo del proyecto que satisfaga una dependencia.

    Estrategias, en orden: ruta relativa (./foo), extensiÛn directa,
    coincidencia por nombre de archivo (stem) y coincidencia de prefijo de
    carpeta (pagos ‚Üí pagos/pago_service.dart). Devuelve la ruta POSIX relativa
    o None si no se encuentra ning˙n candidato en el repo.
    """
    dep_limpia = dep.strip("'\"")
    if dep_limpia.startswith("."):
        base = camino.parent.resolve()
        candidata = (base / dep_limpia).resolve()
        for sufijo in _GRP_EXT_DEPS:
            probar = candidata if candidata.suffix else candidata.with_suffix(sufijo)
            if probar.is_file():
                try:
                    rel_nueva = probar.relative_to(raiz).as_posix()
                    if rel_nueva in por_ruta:
                        return rel_nueva
                except ValueError:
                    return None
        for nombre in ("index.js", "index.ts", "index.dart", "main.dart"):
            probar = (candidata / nombre) if candidata.is_dir() else candidata
            if probar.is_file():
                try:
                    rel_nueva = probar.relative_to(raiz).as_posix()
                    if rel_nueva in por_ruta:
                        return rel_nueva
                except ValueError:
                    return None
        return None
    if Path(dep_limpia).suffix.lower() in _GRP_EXT_DEPS:
        if dep_limpia.lstrip("./") in por_ruta:
            return dep_limpia.lstrip("./")
    stem = Path(dep_limpia).stem
    if stem in por_stem:
        return por_stem[stem]
    for clave in por_ruta:
        if clave.startswith(dep_limpia.rstrip("/") + "/"):
            return clave
    return None


def _grafo_dependencias(directorio="."):
    """Construye un grafo de dependencias entre archivos de cÛdigo del proyecto.

    Devuelve {"nodos": [{"id", "etiqueta", "lenguaje"}], "enlaces": [{"origen",
    "destino"}]}. Los enlaces unen archivos del proyecto que se importan entre
    sÌ. Es la fuente del panel de dependencias de la interfaz web.
    """
    raiz = Path(directorio).resolve()
    if not raiz.is_dir():
        return {"nodos": [], "enlaces": []}
    archivos = []
    for camino in sorted(raiz.rglob("*")):
        if not camino.is_file() or camino.suffix.lower() not in _GRP_EXT_DEPS:
            continue
        if any(parte in CARPETAS_IGNORADAS for parte in camino.parts):
            continue
        archivos.append(camino)
    por_ruta = {}
    por_stem = {}
    nodos = []
    for camino in archivos:
        rel = camino.relative_to(raiz).as_posix()
        nodos.append({"id": rel, "etiqueta": camino.name,
                      "lenguaje": _comando_para_monaco(rel)})
        por_ruta[rel] = camino.name
        por_stem.setdefault(camino.stem, rel)
    enlaces = []
    vistos = set()
    for camino in archivos:
        rel = camino.relative_to(raiz).as_posix()
        try:
            contenido = camino.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lenguaje = _comando_para_monaco(rel)
        for dep in _extraer_dependencias(contenido, lenguaje):
            destino = _resolver_dependencia(rel, camino, dep, por_ruta,
                                            por_stem, raiz)
            if destino and destino != rel and destino in por_ruta:
                clave = (rel, destino)
                if clave not in vistos:
                    vistos.add(clave)
                    enlaces.append({"origen": rel, "destino": destino})
    return {"nodos": nodos, "enlaces": enlaces}


def _buscar_en_codigo(tema, directorio=".", max_resultados=50):
    """Busca ``tema`` en el cÛdigo del repositorio (rg/grep/findstr).

    Devuelve una lista de lÌneas de coincidencia ya formateadas para poder
    reutilizarlas en la interfaz web. [] si no hay buscador o coincidencias.
    """
    if not tema:
        return []
    herramienta = _herramienta_busqueda()
    if herramienta is None:
        return []
    if herramienta == "rg":
        comando = f'rg -n -i --max-count 5 "{tema}"'
    elif herramienta == "grep":
        comando = f'grep -rn -i -m 5 "{tema}" .'
    else:
        comando = (f'findstr /s /n /i "{tema}" '
                   "*.py *.dart *.js *.ts *.go *.rs *.java *.kt *.rb *.php")
    codigo, stdout, _stderr = _ejecutar_comando(comando, directorio, timeout=60)
    if codigo != 0 or not stdout:
        return []
    lineas = [l for l in (stdout or "").splitlines() if l.strip()]
    return lineas[:max_resultados]


# ---------------------------------------------------------------------------
# Ayuda agrupada y coloreada (`snapcontext --help`)
# ---------------------------------------------------------------------------
# CÛdigos ANSI; si el terminal no soporta color (o NO_COLOR est· definido), se
# degradan a texto plano. `colorama` se usa solo para inicializar en Windows
# si est· disponible; nunca es obligatorio.
_ANSI = {
    "negrita": "\033[1m", "cian": "\033[96m", "amarillo": "\033[93m",
    "verde": "\033[92m", "gris": "\033[90m", "reset": "\033[0m",
}
_AYUDA_CON_COLOR = False   # se calcula una sola vez al mostrar --help


def _colores_activos() -> bool:
    """True si se pueden usar colores ANSI en la ayuda."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    try:
        import colorama  # opcional; solo inicializa Windows
        colorama.just_fix_windows_console()
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32          # type: ignore[attr-defined]
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass                                       # sin VT ‚Üí texto plano
    return True


def _pintar(texto: str, clave: str) -> str:
    """Aplica el color ANSI ``clave`` si los colores est·n activos."""
    if not _AYUDA_CON_COLOR:
        return texto
    return f"{_ANSI.get(clave, '')}{texto}{_ANSI['reset']}"

# CategorÌas en orden de apariciÛn; cada opciÛn se muestra una sola vez.
CATEGORIAS_AYUDA = (
    ("Modos de ejecuciÛn",
     ("--plan", "--auto", "--editor", "--modo-edicion", "--validar", "--no-validar-sintaxis", "--max-intentos-validacion",
      "--max-context-tokens", "--editor-fallback", "--mostrar-razonamiento",
      "--mostrar-diff",
      "--asesor", "--asesor-auto", "--asesor-umbral", "--modelo-ligero",
      "--asesor-profundo", "--graph-rag", "--graph-rag-lsp",
      "--lsp-profundidad", "--lsp-simbolos-max", "--multi-agent",
      "--api", "--api-puerto", "--api-host", "--api-token", "--api-generate-key",
      "--chat", "--web", "--web-puerto", "--demo", "--tui",
      "--init", "--init-claude", "--historial", "--historial-limpiar",
      "--diagnostico", "--reparar", "--bienvenida")),
    ("SelecciÛn de archivos",
     ("consulta", "--local", "--iniciar-proyecto", "--no-validar-proyecto",
      "--experto", "--vista-previa", "--carpetas", "--max-archivos", "--candidatos")),
    ("Proveedores de IA",
     ("--provider", "--model", "--no-persist")),
    ("Permisos y seguridad",
     ("--confirmar", "--no-confirmar")),
    ("Git y control de versiones",
     ("--git-commit", "--no-git-commit", "--branch")),
    ("Planificador y bucles",
     ("--paralelo", "--max-intentos", "--test-loop", "--server-loop",
      "--manual-loop", "--comando-test", "--sandbox", "--no-sandbox",
      "--sandbox-session", "--sandbox-session-clean", "--sandbox-imagen",
      "--sandbox-comando", "--dispositivo", "--url-defecto",
      "--max-iteraciones")),
    ("Otros",
     ("consulta", "--directorio", "--aider-opciones", "--depurar",
      "--setup-path", "--version", "-h", "--help")),
)

# Alias/subcomandos que resuelve _preparar_argv_aliases().
ALIAS_AYUDA = (
    ("fix <consulta>", "Ejecuta la consulta con --test-loop."),
    ("review <consulta>", "Ejecuta con --vista-previa --experto."),
    ("server <consulta>", "Ejecuta con --server-loop."),
    ("interactive", "Abre la interfaz web (--web)."),
    ("plan <tarea>", "Ejecuta el planificador (--plan)."),
    ("auto <tarea>", "Ejecuta el planificador autÛnomo (--plan --auto)."),
)

EJEMPLOS_AYUDA = (
    'snapcontext "el botÛn de pago no funciona"',
    'snapcontext fix "el botÛn de pago no funciona"',
    'snapcontext plan "aÒadir validaciÛn al formulario" --auto',
    'snapcontext review "revisar el login"',
    'snapcontext interactive',
    'snapcontext --chat',
    'snapcontext --demo',
    'snapcontext "..." --provider groq --model llama-3.3-70b-versatile',
)


def _invocacion_accion(accion) -> str:
    """RepresentaciÛn compacta de una opciÛn (p. ej. ``--max-archivos N``)."""
    if not accion.option_strings:
        return accion.dest.upper()
    partes = ", ".join(accion.option_strings)
    metavar = accion.metavar
    if not metavar and accion.nargs is None and accion.type is not None:
        metavar = accion.dest.replace("_", "-").upper()
    return f"{partes} {metavar}" if metavar else partes


def action_toma_valor(accion) -> bool:
    """True si la opciÛn espera un valor (no es un flag booleano)."""
    return accion.nargs != 0


def _construir_ayuda(parser: argparse.ArgumentParser) -> str:
    """Genera el texto completo de `--help`: uso, categorias, alias y ejemplos."""
    ancho = max(min(shutil.get_terminal_size().columns - 2, 100), 70)
    lineas: List[str] = []
    COL_IZQ = 30  # ancho de la columna izquierda (invocacion + padding)

    def titulo(texto: str) -> None:
        lineas.append("")
        lineas.append(_pintar(texto.upper(), "negrita"))

    # -- Cabecera ------------------------------------------------------------
    lineas.append(
        _pintar(f"SnapContext v{VERSION}", "cian")
        + _pintar(" - asistente de IA con contexto automatico", "gris")
    )
    lineas.append("")
    lineas.append(
        _pintar("Uso:", "negrita")
        + ' snapcontext [alias] "<consulta>" [opciones]'
    )

    # -- Helpers de formateo -------------------------------------------------
    def _envolver(texto: str) -> List[str]:
        """Divide 'texto' en lineas de maximo (ancho - COL_IZQ) caracteres."""
        ancho_texto = max(ancho - COL_IZQ, 36)
        palabras = texto.split()
        if not palabras:
            return [""]
        resultado: List[str] = []
        actual = ""
        for palabra in palabras:
            if actual and len(actual) + 1 + len(palabra) > ancho_texto:
                resultado.append(actual)
                actual = palabra
            else:
                actual = f"{actual} {palabra}".strip()
        if actual:
            resultado.append(actual)
        return resultado

    def _emit_opcion(invocacion: str, ayuda: str) -> None:
        """Agrega a lineas las lineas formateadas de una sola opcion."""
        etiqueta_raw = f"  {invocacion}"
        etiqueta_col = _pintar(etiqueta_raw, "amarillo")
        envueltas = _envolver(ayuda)
        if len(etiqueta_raw) < COL_IZQ:
            padding = COL_IZQ - len(etiqueta_raw)
            lineas.append(f"{etiqueta_col}{chr(32) * padding}{envueltas[0]}")
            for extra in envueltas[1:]:
                lineas.append(f"{chr(32) * COL_IZQ}{extra}")
        else:
            lineas.append(etiqueta_col)
            for extra in envueltas:
                lineas.append(f"{chr(32) * COL_IZQ}{extra}")

    # -- Mapa de acciones ----------------------------------------------------
    acciones_por_opcion: dict = {}
    accion_consulta = None
    for accion in parser._actions:                     # noqa: SLF001
        if not accion.option_strings:
            if accion.dest == "consulta":
                accion_consulta = accion
        else:
            for op in accion.option_strings:
                acciones_por_opcion[op] = accion

    # -- Opciones agrupadas por categorias -----------------------------------
    titulo("Opciones")
    usadas: set = set()

    for categoria, opciones in CATEGORIAS_AYUDA:
        lineas_cat: List[tuple] = []

        for op in opciones:
            if op == "consulta":
                if accion_consulta is not None and "consulta" not in usadas:
                    usadas.add("consulta")
                    inv = _invocacion_accion(accion_consulta)
                    ayuda = " ".join((accion_consulta.help or "").split())
                    lineas_cat.append((inv, ayuda))
                continue
            if op not in acciones_por_opcion or op in usadas:
                continue
            accion = acciones_por_opcion[op]
            usadas.update(accion.option_strings)
            inv = _invocacion_accion(accion)
            ayuda = " ".join((accion.help or "").split())
            lineas_cat.append((inv, ayuda))

        if lineas_cat:
            lineas.append("")
            lineas.append(_pintar(f"  {categoria}:", "cian"))
            for inv, ayuda in lineas_cat:
                _emit_opcion(inv, ayuda)

    # Opciones no categorizadas al final
    restantes = [
        a for a in parser._actions
        if a.option_strings
        and not any(op in usadas for op in a.option_strings)
    ]
    if restantes:
        lineas.append("")
        lineas.append(_pintar("  Sin categorizar:", "cian"))
        for accion in restantes:
            usadas.update(accion.option_strings)
            _emit_opcion(
                _invocacion_accion(accion),
                " ".join((accion.help or "").split()),
            )

    # -- Alias rapidos -------------------------------------------------------
    titulo("Alias rapidos")
    for alias, descripcion in ALIAS_AYUDA:
        alias_raw = f"  {alias}"
        padding = max(COL_IZQ - len(alias_raw), 1)
        lineas.append(
            f"{_pintar(alias_raw, 'amarillo')}{chr(32) * padding}{descripcion}"
        )

    # -- Ejemplos ------------------------------------------------------------
    titulo("Ejemplos")
    for ejemplo in EJEMPLOS_AYUDA:
        lineas.append(_pintar(f"  $ {ejemplo}", "verde"))

    lineas.append("")
    lineas.append(_pintar(
        "Variables de entorno: GEMINI_API_KEY / ANTHROPIC_API_KEY / "
        "DEEPSEEK_API_KEY / GROQ_API_KEY ¬∑ OLLAMA_URL ¬∑ "
        "SNAPCONTEXT_PROVIDER ¬∑ SNAPCONTEXT_MODELO", "gris",
    ))
    lineas.append("")
    return "\n".join(lineas)



def _mostrar_ayuda_resumida() -> None:
    """Ayuda amigable cuando se ejecuta `snapcontext` sin argumentos (v3.1.1).

    M·s corta que --help: comandos de uso com˙n con ejemplos listos para
    copiar y pegar.
    """
    _ui_mostrar_banner(VERSION)   # v4.8.0: banner Rich en vez de print plano.
    lineas = [
        "Bienvenido a SnapContext ‚Äî tu asistente de IA con contexto autom·tico.",
        "",
        _pintar("Uso b·sico:", _CYAN),
        '  snapcontext "describe lo que quieres cambiar"',
        "",
        _pintar("Comandos m·s ˙tiles:", _CYAN),
        "  snapcontext --bienvenida     Tutorial interactivo de primeros pasos",
        "  snapcontext --init           Configurar claves API y proveedor",
        "  snapcontext --diagnostico    Revisar tu instalaciÛn",
        "  snapcontext --reparar        Arreglar una instalaciÛn rota",
        "  snapcontext --demo           Demo autÛnoma (sin API key)",
        "  snapcontext --chat           Conversar con el proveedor de IA",
        "  snapcontext --plan \"tarea\"   Planificar y ejecutar paso a paso",
        "  snapcontext --help           Ayuda completa agrupada",
        "",
        _pintar("Ejemplos:", _CYAN),
        '  snapcontext "el botÛn de pago no funciona"',
        '  snapcontext "aÒadir login" --test-loop',
        '  snapcontext "revisar pago" --vista-previa   # solo ver, no editar',
        "",
        _pintar("Sin API key", _CYAN) +
        ": SnapContext usa Ollama local autom·ticamente (modo offline).",
        "Instala Ollama desde https://ollama.com y ejecuta: ollama pull llama3.2",
        "",
    ]
    print("\n".join(lineas))


class _AyudaAccion(argparse.Action):
    """Muestra la ayuda agrupada por categorÌas y termina."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):  # noqa: A002
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, valores, opcion=None):
        global _AYUDA_CON_COLOR
        _AYUDA_CON_COLOR = _colores_activos()
        sys.stdout.write(_construir_ayuda(parser))
        parser.exit()


# ---------------------------------------------------------------------------
# Asesor de cÛdigo proactivo (v3.5.0)
# ---------------------------------------------------------------------------
# An·lisis est·tico ligero que sugiere mejoras SIN modificar cÛdigo. Solo con
# --asesor-auto se aplican las refactorizaciones marcadas como seguras, siempre
# validando la sintaxis del resultado antes de escribir en disco.

ASESOR_EXTENSIONES = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".dart": "dart",
    ".go": "go", ".rs": "rust", ".java": "java",
}
ASESOR_CARPETAS_IGNORADAS = {".git", "__pycache__", "node_modules", ".venv",
                             "venv", "env", "dist", "build", ".idea",
                             ".vscode", ".mypy_cache", ".pytest_cache"}
ASESOR_UMBRALES_DEFECTO = {
    "funcion_larga": 20,      # m·x. lÌneas por funciÛn
    "clase_metodos": 10,      # m·x. mÈtodos por clase
    "duplicado_lineas": 6,    # tamaÒo mÌnimo de un bloque duplicado
}

# Nombres cortos legÌtimos (Ìndices de bucle, coordenadas...) que el detector
# de nombres poco descriptivos ignora.
_NOMBRES_CORTOS_VALIDOS = {"i", "j", "k", "x", "y", "z", "_", "ok", "id", "ex",
                           "ax", "ay", "bx", "by"}

# Diccionario de nombres descriptivos propuestos para abreviaturas comunes
# (usado solo como sugerencia; el usuario puede rechazarla).
_NOMBRES_SUGERIDOS = {
    "d": "datos", "n": "numero", "s": "texto", "t": "temporal", "f": "archivo",
    "e": "error", "m": "mensaje", "r": "resultado", "l": "lista",
    "p": "parametro", "c": "contador", "v": "valor", "b": "bandera",
    "w": "ruta", "q": "cola", "g": "grafo", "h": "diccionario",
    "df": "dataframe", "fn": "funcion", "cb": "callback", "tmp": "temporal",
}

_PRIORIDAD_ORDEN = {"alta": 0, "media": 1, "baja": 2}


def _asesor_umbrales() -> dict:
    """Umbrales del asesor: defectos sobrescritos por ``~/.snapcontext/
    config.json`` bajo la clave ``"asesor"`` (p. ej. ``{"funcion_larga": 30}``)."""
    umbrales = dict(ASESOR_UMBRALES_DEFECTO)
    try:
        config = cargar_configuracion()
        personal = config.get("asesor")
        if isinstance(personal, dict):
            for clave, valor in personal.items():
                if clave in umbrales and isinstance(valor, int):
                    umbrales[clave] = valor
    except Exception:
        pass
    return umbrales


def _detectar_funciones_largas(contenido: str, umbral: int) -> List[dict]:
    """Funciones/mÈtodos con m·s de ``umbral`` lÌneas (AST de Python)."""
    hallazgos: List[dict] = []
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return hallazgos
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fin = getattr(nodo, "end_lineno", nodo.lineno) or nodo.lineno
            lineas = fin - nodo.lineno + 1
            if lineas > umbral:
                hallazgos.append({"nombre": nodo.name, "linea": nodo.lineno,
                                  "lineas": lineas})
    return hallazgos


def _detectar_clases_grandes(contenido: str, max_metodos: int) -> List[dict]:
    """Clases con demasiadas responsabilidades (> ``max_metodos`` mÈtodos)."""
    hallazgos: List[dict] = []
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return hallazgos
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ClassDef):
            metodos = sum(
                1 for hijo in nodo.body
                if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)))
            if metodos > max_metodos:
                hallazgos.append({"nombre": nodo.name, "linea": nodo.lineno,
                                  "metodos": metodos})
    return hallazgos


def _detectar_nombres_cortos(contenido: str) -> List[dict]:
    """Variables/funciones con nombres poco descriptivos (‚â§ 2 caracteres)."""
    hallazgos: List[dict] = []
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return hallazgos
    vistos: Dict[str, int] = {}
    for nodo in ast.walk(arbol):
        nombre = None
        linea = getattr(nodo, "lineno", 1)
        if isinstance(nodo, ast.Name) and isinstance(nodo.ctx, ast.Store):
            nombre = nodo.id
        elif isinstance(nodo, ast.arg):
            nombre = nodo.arg
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nombre = nodo.name
        if not nombre or nombre in _NOMBRES_CORTOS_VALIDOS:
            continue
        if len(nombre) <= 2 and nombre not in vistos:
            vistos[nombre] = linea
    for nombre, linea in sorted(vistos.items(), key=lambda kv: kv[1]):
        sugerido = _NOMBRES_SUGERIDOS.get(nombre.lower(),
                                          f"{nombre}_descriptivo")
        hallazgos.append({"nombre": nombre, "linea": linea,
                          "sugerido": sugerido})
    return hallazgos


_PATRONES_OBSOLETOS = [
    (re.compile(r"^\s*except\s*:\s*(#.*)?$"),
     "'except:' desnudo captura todo; especifica la excepciÛn "
     "(p. ej. 'except ValueError:')"),
    (re.compile(r"==\s*None\b"), "usa 'is None' en lugar de '== None'"),
    (re.compile(r"\bNone\s*=="), "usa 'is None' en lugar de 'None =='"),
    (re.compile(r"\.has_key\("), "'.has_key()' es de Python 2; usa 'in'"),
]


def _detectar_patrones_obsoletos(contenido: str) -> List[dict]:
    """LÌneas con patrones obsoletos o antipatrones (heurÌstica por regex)."""
    hallazgos: List[dict] = []
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        codigo = linea.split("#", 1)[0]      # ignora comentarios
        for patron, mensaje in _PATRONES_OBSOLETOS:
            if patron.search(codigo):
                hallazgos.append({"linea": numero, "mensaje": mensaje,
                                  "codigo": codigo.strip()})
                break
    return hallazgos


def _normalizar_linea_duplicado(linea: str) -> str:
    """Normaliza una lÌnea para comparaciÛn de bloques duplicados."""
    return " ".join(linea.strip().split())


def _detectar_duplicados(contenidos: Dict[str, str],
                         min_lineas: int) -> List[dict]:
    """Bloques de ``min_lineas`` lÌneas normalizadas repetidos entre archivos.

    HeurÌstica por ventanas deslizantes: dos bloques son duplicados si todas
    sus lÌneas normalizadas coinciden. Devuelve como m·ximo una sugerencia por
    par de archivos (limitada a 20 para no saturar la salida).
    """
    huellas: Dict[str, tuple] = {}
    hallazgos: List[dict] = []
    vistos_par: set = set()
    for archivo in sorted(contenidos):
        lineas = [_normalizar_linea_duplicado(x)
                  for x in contenidos[archivo].splitlines()]
        for inicio in range(0, max(0, len(lineas) - min_lineas + 1)):
            bloque = lineas[inicio:inicio + min_lineas]
            if any(not x for x in bloque):
                continue
            clave = "\n".join(bloque)
            previo = huellas.get(clave)
            if previo is None:
                huellas[clave] = (archivo, inicio + 1)
                continue
            par = (previo[0], archivo)
            if par in vistos_par:
                continue
            vistos_par.add(par)
            hallazgos.append({
                "archivo": archivo, "linea": inicio + 1,
                "original": f"{previo[0]}:{previo[1]}",
                "lineas": min_lineas})
            if len(hallazgos) >= 20:
                return hallazgos
    return hallazgos


# ---------------------------------------------------------------------------
# An·lisis de seguridad y rendimiento del asesor (v4.2.0)
# ---------------------------------------------------------------------------

# Patrones de vulnerabilidades comunes (regex sobre cÛdigo sin comentarios).
_VULNERABILIDADES_PATRONES = [
    (re.compile(r"\bos\.system\s*\("),
     "Command injection: 'os.system' con entrada no sanitizada.",
     "Usa 'subprocess.run' con lista de argumentos y shell=False.", "alta"),
    (re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
     "Command injection: 'subprocess' con shell=True permite inyecciÛn.",
     "Usa shell=False y pasa los argumentos como lista.", "alta"),
    (re.compile(r"\beval\s*\("),
     "Uso inseguro de 'eval': ejecuta cÛdigo din·mico arbitrario.",
     "Sustit˙yelo por 'ast.literal_eval' o lÛgica explÌcita.", "alta"),
    (re.compile(r"\bexec\s*\("),
     "Uso inseguro de 'exec': ejecuta cÛdigo din·mico arbitrario.",
     "Evita 'exec'; refactoriza el cÛdigo din·mico en funciones.", "alta"),
    (re.compile(r"(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)[^\n]*"
                r"(\+|%|\bf\"|\.format\()", re.IGNORECASE),
     "Posible inyecciÛn SQL: consulta construida por concatenaciÛn.",
     "Usa consultas parametrizadas ('?' o '%s') u ORM.", "alta"),
    (re.compile(r"open\s*\(\s*[^)]*\"\.\./"),
     "Posible path traversal: ruta con '../' construida din·micamente.",
     "Valida y normaliza la ruta (resolve + comprobar base).", "alta"),
    (re.compile(r"innerHTML\s*="),
     "Posible XSS: asignaciÛn directa a innerHTML.",
     "Usa textContent o sanea la entrada antes de insertarla.", "media"),
    (re.compile(r"dangerouslySetInnerHTML"),
     "Posible XSS React: uso de dangerouslySetInnerHTML.",
     "Sanea el HTML (DOMPurify) o usa componentes seguros.", "alta"),
]

# Nombres de variables que sugieren secretos embebidos.
_SECRETES_RE = re.compile(
    r"^\s*([A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD|PASSWD|TOKEN|ACCESS_KEY)"
    r"[A-Z0-9_]*)\s*=\s*[\"']([^\"']{8,})[\"']", re.IGNORECASE)


def _detectar_vulnerabilidades(contenido: str,
                               lenguaje: str = "") -> List[dict]:
    """Detecta vulnerabilidades comunes por heurÌsticas propias (v4.2.0).

    No requiere herramientas externas (bandit etc.); devuelve hallazgos con
    ``linea``, ``mensaje``, ``solucion`` y ``prioridad``.
    """
    hallazgos: List[dict] = []
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        codigo = linea.split("#", 1)[0]
        if not codigo.strip():
            continue
        for patron, mensaje, solucion, prioridad in \
                _VULNERABILIDADES_PATRONES:
            if patron.search(codigo):
                hallazgos.append({"linea": numero, "mensaje": mensaje,
                                  "solucion": solucion,
                                  "prioridad": prioridad})
        coincidencia = _SECRETES_RE.match(codigo)
        if coincidencia:
            hallazgos.append({
                "linea": numero,
                "mensaje": f"Hardcoded secret en '{coincidencia.group(1)}'.",
                "solucion": "MuÈvelo a una variable de entorno o gestor de "
                            "secretos; nunca al repositorio.",
                "prioridad": "alta"})
    return hallazgos


_RENDIMIENTO_PATRONES = [
    (re.compile(r"for\s+\w+\s+in\s+range\s*\(\s*len\s*\("),
     "'range(len(...))': patrÛn innecesario y propenso a recalcular.",
     "Itera directamente sobre la secuencia o usa enumerate().", "media"),
    (re.compile(r"\.read\(\)\s*$"),
     "Lectura completa del archivo en memoria.",
     "Procesa lÌnea a lÌnea ('for linea in fichero') si es grande.", "media"),
    (re.compile(r"\.objects\.get\s*\("),
     "Posible consulta N+1: acceso al ORM dentro de un bucle.",
     "Usa select_related/prefetch_related o una consulta por lotes.", "alta"),
]


def _detectar_rendimiento(contenido: str, lenguaje: str = "") -> List[dict]:
    """Detecta problemas comunes de rendimiento por heurÌsticas (v4.2.0)."""
    hallazgos: List[dict] = []
    lineas_codigo = [(n, l.split("#", 1)[0])
                     for n, l in enumerate(contenido.splitlines(), start=1)]

    for indice, (numero, codigo) in enumerate(lineas_codigo):
        if not codigo.strip():
            continue

        # Bucles anidados (O(n¬≤)): un 'for' seguido de otro m·s indentado.
        coincide_for = re.match(r"^(\s*)for\s+", codigo)
        if coincide_for:
            sangria = len(coincide_for.group(1))
            for _, codigo2 in lineas_codigo[indice + 1:]:
                if not codigo2.strip():
                    continue
                coincide2 = re.match(r"^(\s*)for\s+", codigo2)
                if coincide2:
                    if len(coincide2.group(1)) > sangria:
                        hallazgos.append({
                            "linea": numero,
                            "mensaje": "Bucles anidados: coste cuadr·tico "
                                       "O(n¬≤).",
                            "solucion": "Considera sets/dicts para b˙squedas "
                                        "(O(1)) o reformula el algoritmo.",
                            "prioridad": "media"})
                    break
                break

        # ConcatenaciÛn de cadenas con '+=' dentro de un bucle cercano.
        if re.search(r"^\s*\w+\s*\+=\s*[\"']", codigo) and \
                any(re.match(r"^\s*(for|while)\s+", c)
                    for _, c in lineas_codigo[max(0, indice - 5):indice]):
            hallazgos.append({
                "linea": numero,
                "mensaje": "ConcatenaciÛn de cadenas con '+=' en bucle: "
                           "copias repetidas.",
                "solucion": "Acumula en una lista y usa ''.join(lista).",
                "prioridad": "media"})

        for patron, mensaje, solucion, prioridad in _RENDIMIENTO_PATRONES:
            if patron.search(codigo):
                hallazgos.append({"linea": numero, "mensaje": mensaje,
                                  "solucion": solucion,
                                  "prioridad": prioridad})
    return hallazgos


def _asesor_analizar(directorio: str = ".",
                     umbral_funcion: Optional[int] = None,
                     max_archivos: int = 400,
                     profundo: bool = False) -> List[dict]:
    """Analiza el proyecto y devuelve sugerencias de mejora ordenadas.

    Cada sugerencia es un dict con ``descripcion``, ``archivo``, ``linea``,
    ``solucion``, ``prioridad`` (alta|media|baja) y, si se puede aplicar de
    forma segura, ``operaciones`` + ``auto=True``.

    Con ``profundo=True`` (v4.2.0, ``--asesor-profundo``) aÒade an·lisis de
    seguridad (?? tipos ``vulnerabilidad``) y rendimiento (‚ö° tipo
    ``rendimiento``).
    """
    umbrales = _asesor_umbrales()
    if umbral_funcion:
        umbrales["funcion_larga"] = max(3, int(umbral_funcion))

    raiz = Path(directorio).resolve()
    contenidos: Dict[str, str] = {}
    for camino in sorted(raiz.rglob("*")):
        if not camino.is_file() or camino.suffix not in ASESOR_EXTENSIONES:
            continue
        if any(parte in ASESOR_CARPETAS_IGNORADAS for parte in camino.parts):
            continue
        try:
            contenidos[str(camino.relative_to(raiz)).replace(os.sep, "/")] = \
                camino.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(contenidos) >= max_archivos:
            break

    sugerencias: List[dict] = []
    for relativo, contenido in sorted(contenidos.items()):
        lenguaje = ASESOR_EXTENSIONES[Path(relativo).suffix]

        # Patrones obsoletos: disponibles para todos los lenguajes (regex).
        for hallazgo in _detectar_patrones_obsoletos(contenido):
            auto = "is None" in hallazgo["mensaje"] and lenguaje == "python"
            sugerencias.append({
                "tipo": "patron_obsoleto",
                "descripcion": f"PatrÛn obsoleto: {hallazgo['mensaje']}",
                "archivo": relativo, "linea": hallazgo["linea"],
                "solucion": hallazgo["mensaje"],
                "prioridad": "alta" if "except" in hallazgo["mensaje"]
                             else "media",
                "auto": auto,
            })

        # v4.2.0: seguridad y rendimiento solo en modo profundo.
        if profundo:
            for hallazgo in _detectar_vulnerabilidades(contenido, lenguaje):
                sugerencias.append({
                    "tipo": "vulnerabilidad",
                    "descripcion": f"?? Vulnerabilidad: {hallazgo['mensaje']}",
                    "archivo": relativo, "linea": hallazgo["linea"],
                    "solucion": hallazgo["solucion"],
                    "prioridad": hallazgo["prioridad"],
                })
            for hallazgo in _detectar_rendimiento(contenido, lenguaje):
                sugerencias.append({
                    "tipo": "rendimiento",
                    "descripcion": f"‚ö° Rendimiento: {hallazgo['mensaje']}",
                    "archivo": relativo, "linea": hallazgo["linea"],
                    "solucion": hallazgo["solucion"],
                    "prioridad": hallazgo["prioridad"],
                })

        if lenguaje != "python":
            continue     # AST detallado solo para Python; resto heurÌsticas.

        for hallazgo in _detectar_funciones_largas(
                contenido, umbrales["funcion_larga"]):
            sugerencias.append({
                "tipo": "funcion_larga",
                "descripcion": (
                    f"La funciÛn '{hallazgo['nombre']}' tiene "
                    f"{hallazgo['lineas']} lÌneas (> {umbrales['funcion_larga']})."),
                "archivo": relativo, "linea": hallazgo["linea"],
                "solucion": "Extrae bloques coherentes en funciones auxiliares.",
                "prioridad": "media",
            })

        for hallazgo in _detectar_clases_grandes(
                contenido, umbrales["clase_metodos"]):
            sugerencias.append({
                "tipo": "clase_grande",
                "descripcion": (
                    f"La clase '{hallazgo['nombre']}' tiene "
                    f"{hallazgo['metodos']} mÈtodos "
                    f"(> {umbrales['clase_metodos']}): posibles demasiadas "
                    "responsabilidades."),
                "archivo": relativo, "linea": hallazgo["linea"],
                "solucion": ("Divide la clase en clases m·s pequeÒas con una "
                             "responsabilidad ˙nica."),
                "prioridad": "media",
            })

        for hallazgo in _detectar_nombres_cortos(contenido):
            operaciones = [{
                "tipo": "renombrar", "nombre": hallazgo["nombre"],
                "nuevo": hallazgo["sugerido"]}]
            sugerencias.append({
                "tipo": "nombre_poco_descriptivo",
                "descripcion": (
                    f"El nombre '{hallazgo['nombre']}' no es descriptivo."),
                "archivo": relativo, "linea": hallazgo["linea"],
                "solucion": f"RenÛmbralo a algo como '{hallazgo['sugerido']}'.",
                "prioridad": "baja",
                "operaciones": operaciones, "auto": True,
            })

    min_dup = umbrales["duplicado_lineas"]
    for hallazgo in _detectar_duplicados(contenidos, min_dup):
        sugerencias.append({
            "tipo": "codigo_duplicado",
            "descripcion": (
                f"Bloque duplicado de {hallazgo['lineas']} lÌneas "
                f"(original en {hallazgo['original']})."),
            "archivo": hallazgo["archivo"], "linea": hallazgo["linea"],
            "solucion": "Extrae el bloque com˙n a una funciÛn compartida.",
            "prioridad": "media",
        })

    sugerencias.sort(key=lambda s: (_PRIORIDAD_ORDEN.get(s["prioridad"], 3),
                                    s["archivo"], s["linea"]))
    return sugerencias


def _asesor_analizar_por_tipo(directorio: str, tipos: tuple) -> List[dict]:
    """Ejecuta el an·lisis profundo y devuelve solo los ``tipos`` pedidos."""
    return [s for s in _asesor_analizar(directorio, profundo=True)
            if s.get("tipo") in tipos]


def _analizar_seguridad(directorio: str = ".") -> List[dict]:
    """An·lisis de seguridad del proyecto (?? tipo 'vulnerabilidad')."""
    return _asesor_analizar_por_tipo(directorio, ("vulnerabilidad",))


def _analizar_rendimiento(directorio: str = ".") -> List[dict]:
    """An·lisis de rendimiento del proyecto (‚ö° tipo 'rendimiento')."""
    return _asesor_analizar_por_tipo(directorio, ("rendimiento",))


def _asesor_mostrar(sugerencias: List[dict]) -> None:
    """Muestra las sugerencias en la CLI con colores por prioridad."""
    if not sugerencias:
        exito("Asesor: sin sugerencias. El cÛdigo est· limpio. ‚úî")
        return
    aviso(f"Asesor de cÛdigo ‚Äî {len(sugerencias)} sugerencia(s):")
    color_prioridad = {"alta": _ROJO, "media": _AMARILLO, "baja": _CYAN}
    for indice, sugg in enumerate(sugerencias, start=1):
        color = color_prioridad.get(sugg.get("prioridad"), _CYAN)
        _emitir(sys.stdout, _pintar(
            f"  {indice}. [{sugg['prioridad'].upper()}] "
            f"{sugg['archivo']}:{sugg['linea']} ‚Äî {sugg['descripcion']}",
            color))
        _emitir(sys.stdout, _pintar(f"       ‚Üí {sugg['solucion']}", _VERDE))
        if sugg.get("auto"):
            _emitir(sys.stdout, _pintar(
                "       (aplicable autom·ticamente con --asesor-auto)",
                _CYAN))


def _asesor_aplicar_automaticas(sugerencias: List[dict],
                                directorio: str = ".") -> int:
    """Aplica solo las sugerencias marcadas ``auto=True`` (--asesor-auto).

    Cada cambio se valida con ``_validar_sintaxis`` antes de escribir; si la
    validaciÛn falla, se descarta el cambio y el archivo queda intacto.
    Devuelve el n˙mero de cambios aplicados.
    """
    raiz = Path(directorio).resolve()
    aplicadas = 0
    for sugg in sugerencias:
        if not sugg.get("auto") or not sugg.get("operaciones"):
            continue
        archivo = raiz / sugg["archivo"]
        try:
            contenido = archivo.read_text(encoding="utf-8")
        except OSError as exc:
            aviso(f"[asesor-auto] No se pudo leer {sugg['archivo']}: {exc}")
            continue
        nuevo = _aplicar_operaciones_ast(contenido, sugg["operaciones"])
        if not nuevo or nuevo == contenido:
            continue
        exito_val, err = _validar_sintaxis(sugg["archivo"], nuevo, str(raiz))
        if not exito_val:
            aviso(f"[asesor-auto] Cambio descartado en {sugg['archivo']} "
                  f"(validaciÛn fallÛ: {err}).")
            continue
        try:
            archivo.write_text(nuevo, encoding="utf-8")
        except OSError as exc:
            aviso(f"[asesor-auto] No se pudo escribir {sugg['archivo']}: {exc}")
            continue
        exito(f"[asesor-auto] Aplicado en {sugg['archivo']}:"
              f"{sugg['linea']} ‚Äî {sugg['solucion']}")
        aplicadas += 1
    return aplicadas


def _ejecutar_asesor(args: argparse.Namespace) -> int:
    """Modo asesor (`--asesor` / `--sugerir` / `--asesor-auto`)."""
    from agentes import AgenteAsesor      # import diferido (evita ciclos)
    directorio = getattr(args, "directorio", ".") or "."
    agente = AgenteAsesor()
    info("?? Asesor de cÛdigo proactivo analizando el proyecto...")
    sugerencias = agente.analizar(
        directorio,
        umbral_funcion=getattr(args, "asesor_umbral", None),
        profundo=getattr(args, "asesor_profundo", False))
    agente.mostrar(sugerencias)
    if getattr(args, "asesor_auto", False):
        aplicadas = agente.aplicar_automaticas(sugerencias, directorio)
        if aplicadas:
            exito(f"{aplicadas} mejora(s) aplicada(s) autom·ticamente.")
        else:
            info("Ninguna sugerencia era aplicable autom·ticamente.")
    else:
        info("Modo informativo: no se modificÛ ning˙n archivo "
             "(usa --asesor-auto para aplicar mejoras seguras).")
    return 0


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapcontext",
        add_help=False,   # -h/--help se gestionan con _AyudaAccion (agrupada)
        description=_LOGO_SMALL + (
            "SnapContext ‚Äî Asistente de IA para desarrollo con Flutter/Supabase. "
            "Escanea el repo, el proveedor de IA (Gemini, Ollama, DeepSeek o "
            "Groq) elige los archivos relevantes y Aider realiza los cambios."
        ),
        epilog=(
            "Ejemplos:\n"
            '  snapcontext "el botÛn de pago no funciona"\n'
            '  snapcontext "aÒadir Ìndice a la tabla pedidos" --test-loop\n'
            '  snapcontext "revisar login" --vista-previa\n'
            '  snapcontext "revisar pago" --experto\n'
            '  snapcontext "arreglar el checkout" --server-loop\n'
            '  snapcontext "arreglar login" --manual-loop\n'
            '  snapcontext fix "el botÛn de pago no funciona"\n'
            '  snapcontext review "revisar cÛdigo"\n'
            '  snapcontext server "iniciar servidor"\n'
            '  snapcontext interactive\n'
            '  snapcontext --chat\n'
            '  snapcontext --historial\n'
            '  snapcontext --demo\n'
            '  snapcontext "..." --provider groq --model llama-3.3-70b-versatile\n'
            "Variables de entorno: clave seg˙n --provider (GEMINI_API_KEY / "
            "ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GROQ_API_KEY), OLLAMA_URL "
            "(default localhost:11434), SNAPCONTEXT_PROVIDER y SNAPCONTEXT_MODELO "
            "(opcionales).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "consulta", type=str, nargs="?", default=None,
        help="La tarea a resolver (p·sala entre comillas). Omitible con --init.",
    )
    parser.add_argument(
        "--init", action="store_true",
        help="Asistente de configuraciÛn inicial: claves API, proveedor y "
             "modelo favorito (se guarda en ~/.snapcontext/config.json). "
             "Independiente de la consulta y el escaneo.",
    )
    parser.add_argument(
        "--directorio", default=".",
        help="Repositorio donde trabajar (por defecto: raÌz git detectada desde "
             "el directorio actual).",
    )
    parser.add_argument(
        "--carpetas", nargs="+", default=None,
        help="Carpetas a escanear (por defecto: lib supabase).",
    )
    parser.add_argument(
        "--max-archivos", type=int, default=MAX_ARCHIVOS_DEFECTO,
        help="N˙mero de archivos que recibe Aider (por defecto: 3).",
    )
    parser.add_argument(
        "--candidatos", type=int, default=MAX_CANDIDATOS_DEFECTO,
        help="M·ximo de candidatos que recibe Gemini (por defecto: 80).",
    )
    parser.add_argument(
        "--provider", choices=sorted(PROVEEDORES), default=None,
        help="Proveedor de IA que elige los archivos (gemini | ollama | "
             "deepseek | groq). Si no se indica -y tampoco --local-, se usa el "
             "guardado en ~/.snapcontext/config.json o, si es el primer uso, "
             "se muestra un men˙ interactivo (questionary); con --no-persist "
             "se fuerza siempre el men˙. Env: SNAPCONTEXT_PROVIDER.",
    )
    parser.add_argument(
        "--no-persist", action="store_true",
        help="Ignora la configuracion guardada (~/.snapcontext/config.json) y "
             "fuerza el men˙ interactivo de proveedor (si no hay --local).",
    )
    parser.add_argument(
        "--model", "--modelo", dest="modelo", default=MODELO_DEFECTO,
        help="Modelo del proveedor. Si no se indica, se usa el modelo por "
             "defecto de cada proveedor (o SNAPCONTEXT_MODELO).",
    )
    parser.add_argument(
        "--local", action="store_true",
        help="SelecciÛn local por heurÌstica, sin llamar a Gemini "
             "(˙til para probar offline). TambiÈn desactiva la validaciÛn "
             "de carpeta de proyecto.",
    )
    parser.add_argument(
        "--multi-agent", dest="multi_agent", action="store_true", default=False,
        help="Sistema multi-agente (v6.0.0): un Supervisor coordina a un "
             "Arquitecto (plan), un Programador (editor propio) y un Tester "
             "(pruebas) con bucle de realimentaciÛn. Env: "
             "SNAPCONTEXT_MULTI_AGENT=1.",
    )
    parser.add_argument(
        "--sub-agents", dest="sub_agents", action="store_true", default=False,
        help="Sub-agentes din\u00e1micos (v6.13.0): el Supervisor instancia "
             "agentes especializados bajo demanda (scout, debugger, "
             "frontender, tester, documentador) con contexto aislado y los "
             "ejecuta en paralelo. Requiere --multi-agent.",
    )
    parser.add_argument(
        "--max-parallel", dest="max_parallel", type=int, default=3,
        help="N\u00famero m\u00e1ximo de sub-agentes en paralelo "
             "(por defecto 3). Usar con --sub-agents.",
    )
    parser.add_argument(
        "--sub-agente-nuevo", dest="sub_agente_nuevo", nargs=2,
        metavar=("NOMBRE", "DESCRIPCION"), default=None,
        help="v6.20.0: registra un sub-agente din\u00e1mico nuevo "
             "(<nombre> + <descripcion>) para usarlo bajo demanda, como plugin.",
    )
    parser.add_argument(
        "--sub-agente-listar", dest="sub_agente_listar", action="store_true",
        default=False,
        help="v6.20.0: lista los sub-agentes din\u00e1micos registrados y sale.",
    )
    parser.add_argument(
        "--lsp", dest="lsp", action="store_true", default=False,
        help="LSP (v6.14.0): activa herramientas lsp_definicion / "
             "lsp_referencias / lsp_tipo en el agente (pyright, tsserver, "
             "gopls, rust-analyzer...). Cliente perezoso y con cach\u00e9. "
             "Env: SNAPCONTEXT_LSP=1.",
    )
    parser.add_argument(
        "--graph-rag", dest="graph_rag", action="store_true", default=False,
        help="Grafo de conocimiento (v5.5.0): combina AST + embeddings y "
             "amplÌa el contexto con archivos relacionados (imports, "
             "llamadas, herencia). Env: SNAPCONTEXT_GRAPH_RAG=1.",
    )
    parser.add_argument(
        "--graph-rag-lsp", dest="graph_rag_lsp", action="store_true",
        default=False,
        help="IntegraciÛn Graph RAG + LSP (v6.33.0): inyecta sÌmbolos precisos "
             "(definiciones, referencias) en lugar de archivos completos. "
             "Requiere --graph-rag y --lsp. Env: SNAPCONTEXT_GRAPH_RAG_LSP=1.",
    )
    parser.add_argument(
        "--lsp-profundidad", dest="lsp_profundidad", type=int, default=None,
        help="Profundidad de expansiÛn en Graph RAG para LSP (v6.33). "
             "Por defecto: 2.",
    )
    parser.add_argument(
        "--lsp-simbolos-max", dest="lsp_simbolos_max", type=int, default=None,
        help="N˙mero m·ximo de sÌmbolos a inyectar vÌa LSP (v6.33). "
             "Por defecto: 10.",
    )
    parser.add_argument(
        "--iniciar-proyecto", "--no-validar", dest="iniciar_proyecto",
        action="store_true",
        help="Desactiva por completo la validaciÛn de carpeta de proyecto: "
             "trabaja en el directorio actual (o --directorio) aunque estÈ "
             "vacÌo. Ideal para empezar un proyecto desde cero.",
    )
    parser.add_argument(
        "--no-validar-proyecto", dest="no_validar_proyecto",
        action="store_true",
        help="Omite la verificaciÛn temprana de directorio de proyecto "
             "(mostrada al inicio cuando no se detectan archivos de proyecto). "
             "Para usuarios avanzados que quieren saltar este aviso.",
    )
    parser.add_argument(
        "--vista-previa", action="store_true",
        help="Solo muestra los archivos seleccionados y sale, sin ejecutar Aider.",
    )
    # v6.10.0: modo navegador (Playwright) ‚Äî ver/depurar interfaces visuales.
    parser.add_argument(
        "--browser", dest="browser", action="store_true", default=False,
        help="(v6.10.0) Activa el modo navegador: el agente puede abrir "
             "URLs, tomar capturas de pantalla, hacer clic, escribir y "
             "analizar la interfaz visual (requiere 'pip install "
             "snapcontext[browser]' y 'playwright install chromium').",
    )
    parser.add_argument(
        "--browser-headed", dest="browser_headed", action="store_true",
        default=False,
        help="(v6.10.0) Muestra la ventana del navegador (por defecto es "
             "headless, sin interfaz gr·fica).",
    )
        # v6.16.0: Prompt Caching (activado por defecto; mÈtricas en --depurar).
    parser.add_argument(
        "--prompt-caching", dest="prompt_caching", action="store_true",
        default=PROMPT_CACHING_DEFECTO,
        help="(v6.16.0) Activa el Prompt Caching para proveedores compatibles "
             "(Anthropic, DeepSeek): mantiene en cachÈ el mensaje del sistema, "
             "las herramientas MCP y CLAUDE.md. Activado por defecto. Con "
             "--depurar se muestran mÈtricas de tokens cacheados. Se "
             "desactiva con --no-prompt-caching, SNAPCONTEXT_PROMPT_CACHING=0 "
             "o 'prompt_caching': false en config.json.",
    )
    parser.add_argument(
        "--no-prompt-caching", dest="prompt_caching", action="store_false",
        help="(v6.16.0) Desactiva el Prompt Caching (no aÒade marcas "
             "cache_control). Sin efecto para proveedores que no lo soportan.",
    )
    # v6.31.0: Prompt Caching por Capas (activado por defecto si el caching
    # b·sico est· activo; --no-prompt-caching-capas usa el caching de v6.16.0).
    parser.add_argument(
        "--prompt-caching-capas", dest="prompt_caching_capas",
        action=argparse.BooleanOptionalAction, default=None,
        help="(v6.31.0) Activa/desactiva la estructuraciÛn del Prompt Caching "
             "por Capas inmutables: est·tica (system + herramientas MCP), "
             "semi-est·tica (GraphRAG, CLAUDE.md, reglas) y vol·til (mensajes "
             "recientes, tool_results, diffs). Maximiza el prefijo idÈntico "
             "entre peticiones. Por defecto: activado si --prompt-caching lo "
             "est· (o 'prompt_caching.capas_activo' de config.json); con "
             "--no-prompt-caching-capas se usa el caching b·sico de v6.16.0.",
    )
    # v6.32.0: Pruning proactivo de contexto (ediciÛn quir˙rgica del historial).
    parser.add_argument(
        "--prune-context", dest="prune_context",
        action=argparse.BooleanOptionalAction, default=None,
        help="(v6.32.0) Activa/desactiva el pruning proactivo: poda resultados "
             "extensos de herramientas (logs, salidas, diffs) reemplaz·ndolos "
             "por un resumen de una lÌnea. Por defecto: activado (o 'pruning.activo' "
             "de config.json); con --no-prune-context se desactiva.",
    )
    parser.add_argument(
        "--prune-umbral", dest="prune_umbral", type=int, default=None,
        help="(v6.32.0) N˙mero m·ximo de lÌneas antes de podar un resultado "
             "(por defecto: 10). Un valor menor poda m·s agresivamente.",
    )
    # v6.34.0: Soporte para Intel XPU (GPU Intel Arc) vÌa IPEX.
    parser.add_argument(
        "--xpu-model", dest="xpu_model", type=str, default=None,
        help="(v6.34.0) Modelo de Hugging Face para inferencia en Intel XPU "
             "(por defecto: 'Qwen/Qwen3.5-35B-A3B' o el valor en config.json).",
    )
    parser.add_argument(
        "--xpu-max-tokens", dest="xpu_max_tokens", type=int, default=None,
        help="(v6.34.0) N˙mero m·ximo de tokens a generar (por defecto: 500).",
    )
    parser.add_argument(
        "--xpu-temperature", dest="xpu_temperature", type=float, default=None,
        help="(v6.34.0) Temperatura para la inferencia (por defecto: 0.7).",
    )
    # v6.9.0: benchmark de rendimiento por fases.
    parser.add_argument(
        "--benchmark", action="store_true",
        help="(v6.9.0) Mide y muestra en una tabla el tiempo de cada fase "
             "(inicio, escaneo, selecciÛn, plan, ediciÛn, pruebas y total). "
             "No necesita API key.",
    )
    parser.add_argument(
        "--experto", "--expert", action="store_true",
        help="Modo experto: revisar la selecciÛn y aÒadir/eliminar archivos "
             "antes de ejecutar Aider.",
    )
    parser.add_argument(
        "--aider-opciones", default="",
        help='Opciones extra para Aider entre comillas (p. ej. "--model sonnet").',
    )
    parser.add_argument(
        "--asesor", "--sugerir", dest="asesor", action="store_true",
        help="Asesor de cÛdigo proactivo (v3.5.0): analiza el proyecto y "
             "muestra sugerencias de mejora sin modificar cÛdigo.",
    )
    parser.add_argument(
        "--asesor-auto", dest="asesor_auto", action="store_true",
        help="Como --asesor, pero aplica autom·ticamente las mejoras seguras "
             "(renombrar sÌmbolos); cada cambio se valida antes de guardarse. "
             "Las dem·s sugerencias solo se muestran.",
    )
    parser.add_argument(
        "--asesor-umbral", dest="asesor_umbral", type=int, default=None,
        help="Umbral de lÌneas por funciÛn para el asesor (por defecto 20; "
             "tambiÈn configurable en config.json clave 'asesor').",
    )
    parser.add_argument(
        "--api", "--api-server", dest="api", action="store_true",
        help="API p˙blica (v3.6.0): arranca el servidor HTTP REST en "
             "http://host:puerto con documentaciÛn OpenAPI en /docs. "
             "Requiere las dependencias web: pip install snapcontext[web].",
    )
    parser.add_argument(
        "--api-puerto", dest="api_puerto", type=int, default=8001,
        help="Puerto del servidor de la API (por defecto 8001; la web usa "
             "8000 para no interferir).",
    )
    parser.add_argument(
        "--api-host", dest="api_host", default="127.0.0.1",
        help="Host de escucha de la API (por defecto 127.0.0.1).",
    )
    parser.add_argument(
        "--api-token", dest="api_token", default=None,
        help="Token/API key exigido en los endpoints /api/v1/* (header "
             "X-API-Key). Si se omite, se usa ‚Äîo genera y guarda‚Äî el de "
             "config.json clave 'api_key'.",
    )
    parser.add_argument(
        "--api-generate-key", dest="api_generate_key", action="store_true",
        help="Genera una API key segura, la guarda en config.json "
             "('api_key') y la muestra por pantalla. No arranca el servidor.",
    )
    bucle = parser.add_mutually_exclusive_group()
    bucle.add_argument(
        "--test-loop", action="store_true",
        help="Tras Aider ejecuta las pruebas y repite si fallan "
             "(bucle agÈntico b·sico).",
    )
    bucle.add_argument(
        "--server-loop", action="store_true",
        help="Bucle agÈntico con servidor Flutter en MODO AUTOM√ÅTICO: "
             "reintenta hasta --max-intentos y pregunta s/n al usuario.",
    )
    bucle.add_argument(
        "--manual-loop", action="store_true",
        help="Bucle agÈntico con servidor Flutter en MODO MANUAL: "
             "el usuario decide en cada paso.",
    )
    parser.add_argument(
        "--comando-test", default=None,
        help='Comando de pruebas del bucle. Si se omite se detecta '
             'autom·ticamente seg˙n el lenguaje del proyecto '
             '(p. ej. "go test ./...", "pytest", "flutter test").',
    )
    # v4.3.0/v5.4.0: sandbox Docker, ahora inteligente.
    #   --sandbox        ‚Üí fuerza el contenedor para TODO (como siempre).
    #   --no-sandbox     ‚Üí lo desactiva por completo, incluso ante comandos
    #                      peligrosos (prioridad m·xima, opt-out explÌcito).
    #   Sin ninguno      ‚Üí modo inteligente: solo se encapsulan los comandos
    #                      peligrosos detectados (sandbox_utils).
    parser.add_argument(
        "--sandbox", action="store_true",
        help="(v4.3.0) Ejecuta TODOS los comandos y pruebas dentro de un "
             "contenedor Docker aislado (monta el proyecto en /workspace). "
             "Si Docker no est· disponible, falla con error claro. "
             "(v5.4.0) Sin este flag, el sandbox se activa de forma "
             "inteligente SOLO ante comandos peligrosos.",
    )
    parser.add_argument(
        "--no-sandbox", dest="no_sandbox", action="store_true",
        help="(v5.4.0) Desactiva el sandboxing inteligente: ning˙n comando se "
             "ejecuta en Docker, incluso si se detecta peligro. Tiene "
             "prioridad sobre --sandbox y sobre SNAPCONTEXT_SANDBOX=1. "
             "Equivalente a la variable SNAPCONTEXT_SANDBOX=0.",
    )
    parser.add_argument(
        "--sandbox-session", dest="sandbox_session", action="store_true",
        help="(v6.4.0) Persistencia de Docker por sesiÛn: crea UN contenedor "
             "al inicio de la tarea y lo reutiliza para todos los comandos "
             "(mantiene estado: `npm install` ‚Üí `npm test`, `pip install` ‚Üí "
             "`pytest`). Se destruye al finalizar (o con Ctrl+C). Sin este "
             "flag se usa `docker run --rm` (comportamiento histÛrico).",
    )
    parser.add_argument(
        "--sandbox-session-clean", dest="sandbox_session_clean",
        action="store_true",
        help="(v6.4.0) Elimina los contenedores de sesiÛn huÈrfanos "
             "(snap-session-*) de sesiones anteriores y sale. Con "
             "--auto los borra sin preguntar.",
    )
    parser.add_argument(
        "--sandbox-imagen", dest="sandbox_imagen", default=None,
        help="Imagen Docker del sandbox (por defecto: python:3.11-slim o "
             "SNAPCONTEXT_SANDBOX_IMAGE).",
    )
    parser.add_argument(
        "--sandbox-comando", dest="sandbox_comando", default=None,
        help='Comando de preparaciÛn dentro del contenedor antes del comando '
             'principal (ej.: "apt update && apt install -y make").',
    )
    parser.add_argument(
        "--max-iteraciones", type=int, default=MAX_ITERACIONES_TEST_DEFECTO,
        help="M·ximo de iteraciones del bucle de pruebas.",
    )
    # v6.29.0: Sistema de autocorreccion (bucle pruebas + correccion).
    parser.add_argument(
        "--autocorregir", dest="autocorregir",
        action=argparse.BooleanOptionalAction, default=True,
        help="(v6.29.0) Activa el bucle de autocorreccion: ejecuta pruebas, "
             "analiza errores y aplica correcciones automaticamente hasta "
             "que pasen o se alcance --max-ciclos. Activado por defecto.",
    )
    parser.add_argument(
        "--max-ciclos", dest="max_ciclos", type=int, default=3,
        metavar="N",
        help="(v6.29.0) Numero maximo de iteraciones de autocorreccion "
             "(por defecto: 3).",
    )
    parser.add_argument(
        "--max-intentos", type=int, default=3,
        help="Intentos m·ximos del bucle autom·tico --server-loop "
             "(por defecto: 3).",
    )
    parser.add_argument(
        "--dispositivo", default="web-server",
        help='Dispositivo/plataforma de "flutter run" '
             "(por defecto: web-server).",
    )
    parser.add_argument(
        "--url-defecto", default="http://localhost:5000",
        help="URL para abrir el navegador si Flutter no reporta una "
             "(por defecto: http://localhost:5000).",
    )
    parser.add_argument(
        "--depurar", action="store_true", help="Logs de depuraciÛn.",
    )
    parser.add_argument(
        "--version", action=_VersionAction, nargs=0,
    )
    parser.add_argument(
        "--setup-path", action="store_true",
        help="Configura autom·ticamente el PATH del usuario para Windows: aÒade la "
             "carpeta de ejecutable al PATH persistente. √ötil si instalaste con "
             "'pip install snapcontext' sin usar el one-liner. Solo funciona en Windows.",
    )
    parser.add_argument(
        "--diagnostico", action="store_true",
        help="(v3.1.0) Revisa la instalaciÛn: Python, paquete, dependencias "
             "opcionales, PATH, proveedor de IA (API key / Ollama) y memoria "
             "SQLite, con resumen en colores y soluciones sugeridas.",
    )
    parser.add_argument(
        "--reparar", action="store_true",
        help="(v3.1.0) Repara una instalaciÛn rota: limpia entornos uv "
             "corruptos, reinstala SnapContext con pip, recrea la base de "
             "datos SQLite si est· corrupta y ajusta el PATH (Windows).",
    )
    parser.add_argument(
        "--bienvenida", action="store_true",
        help="(v3.1.0) Muestra el tutorial interactivo de primeros pasos.",
    )
    parser.add_argument(
        "--web", action="store_true",
        help="Inicia la interfaz web en http://localhost:8000 (FastAPI + WebSockets "
             "con logs en tiempo real). Requiere: pip install snapcontext[web].",
    )
    parser.add_argument(
        "--web-puerto", type=int, default=8000,
        help="Puerto para la interfaz web (por defecto: 8000). Requiere --web.",
    )
    # v6.12.0: TUI inmersiva con Textual (grupo opcional [tui]).
    parser.add_argument(
        "--tui", action="store_true",
        help="Inicia la TUI inmersiva en la terminal (Textual): pestaÒas de "
             "logs, ·rbol de archivos, control del agente y visor de diffs. "
             "Requiere: pip install snapcontext[tui].",
    )
    # v6.27.0: TUI interactiva (edicion de plan + visualizacion del grafo).
    parser.add_argument(
        "--tui-plan-editor", dest="tui_plan_editor",
        action=argparse.BooleanOptionalAction, default=True,
        help="(v6.27.0) Permite editar el plan del agente en la TUI "
             "(reordenar, eliminar, insertar pasos). Requiere --tui. "
             "Activado por defecto; desactivar con --no-tui-plan-editor.",
    )
    parser.add_argument(
        "--tui-grafo", dest="tui_grafo",
        action=argparse.BooleanOptionalAction, default=True,
        help="(v6.27.0) Muestra el grafo de dependencias (GraphRAG) en "
             "una pestana de la TUI. Requiere --tui. Activado por defecto; "
             "desactivar con --no-tui-grafo.",
    )
    # v6.7.0: expansiÛn MCP ‚Äî conexiÛn perezosa a base de datos.
    parser.add_argument(
        "--db-url", default=None,
        help="(v6.7.0) URL de la base de datos para las herramientas MCP "
             "(db_query/db_schema). Ej: sqlite:///ruta/db.sqlite, "
             "postgresql://user:pass@localhost/db, mysql://user:pass@host/db.",
    )
    parser.add_argument(
        "--db-driver", default=None,
        choices=("sqlite", "postgresql", "mysql"),
        help="(v6.7.0) Fuerza el driver de la base de datos (por defecto se "
             "deduce de --db-url).",
    )
    # v6.8.0: omnicanalidad avanzada ‚Äî GitHub webhooks y tareas asÌncronas.
    parser.add_argument(
        "--github-webhook-secreto", default=None,
        help="(v6.8.0) Secreto para validar firmas HMAC de webhooks de GitHub.",
    )
    parser.add_argument(
        "--github-token", default=None,
        help="(v6.8.0) Token de GitHub (PAT) para interactuar con la API (comentar PRs, diffs).",
    )
    parser.add_argument(
        "--webhook-url", default=None,
        help="(v6.8.0) URL p˙blica del webhook de SnapContext para registrar en servicios externos.",
    )
    parser.add_argument(
        "--web-interactive", action="store_true",
        help="(v6.5.0) Activa el centro de control web interactivo adem·s de la "
             "web actual: timeline de ReAct en tiempo real (Pensamiento‚ÜíAcciÛn‚Üí"
             "ObservaciÛn), diff viewer Monaco para resolver conflictos de "
             "parches y panel de estado del agente en http://localhost:8000/"
             "interactive. Requiere --web.",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Ejecuta una demo autÛnoma de SnapContext: crea un proyecto de prueba "
             "temporal, muestra la selecciÛn de archivos (--vista-previa --local) y "
             "el bucle de pruebas completo, sin necesidad de API key.",
    )
    parser.add_argument(
        "--chat", action="store_true",
        help="Abre el modo chat interactivo (REPL): conversa con el proveedor de IA, "
             "cambia de proveedor (/provider), selecciona archivos (/seleccion) y "
             "consulta el historial (/historial). No necesita consulta.",
    )
    parser.add_argument(
        "--historial", action="store_true",
        help="Muestra las ˙ltimas 20 tareas guardadas en ~/.snapcontext/historial.json.",
    )
    parser.add_argument(
        "--historial-limpiar", action="store_true",
        help="Borra el historial persistente (~/.snapcontext/historial.json) y sale.",
    )
    parser.add_argument(
        "--plan", action="store_true",
        help="Usar el planificador est·tico (modo legacy) ‚Äî ˙til para scripts "
             "que requieren pasos predefinidos: pide al proveedor de IA que "
             "descomponga la tarea en pasos y los ejecuta secuencialmente con "
             "control continuar/reintentar/saltar. Requiere consulta.",
    )
    parser.add_argument(
        "--react", action="store_true",
        help="Usar el modo ReAct (razonamiento din·mico) ‚Äî este es el "
             "comportamiento por defecto si no se usa --plan; el flag se "
             "conserva por compatibilidad pero ya es redundante.",
    )
    parser.add_argument(
        "--react-max-iter", dest="react_max_iter", type=int, default=15,
        metavar="N",
        help="En modo --react, tope de iteraciones del bucle (por defecto: 15).",
    )

    parser.add_argument(
        "--git-commit", action=argparse.BooleanOptionalAction, default=True,
        help="En modo --plan, hace 'git add . && git commit' tras cada paso exitoso "
             "(por defecto: activado; desactivar con --no-git-commit).",
    )
    # v6.20.0 ‚Äî Git profundo: revert nativo y mensaje manual de commit.
    parser.add_argument(
        "--git-revert", dest="git_revert", nargs="?", const=-1, default=None,
        type=int, metavar="STEP",
        help="Revierte el paso indicado (id de la tabla 'pasos') con "
             "'git revert'. Sin valor, revierte el ˙ltimo paso commiteado.",
    )
    parser.add_argument(
        "--git-mensaje", dest="git_mensaje", default=None, metavar="TEXTO",
        help="Mensaje manual para los commits autom·ticos por paso "
             "(si se omite, se genera con IA en formato Conventional Commits).",
    )
    parser.add_argument(
        "--branch", dest="branch", default=None, metavar="NOMBRE",
        help="En modo --plan, crea y cambia a una rama git nueva antes de ejecutar "
             "los pasos (p. ej. --branch fix/checkout).",
    )
    parser.add_argument(
        "--confirmar", action=argparse.BooleanOptionalAction, default=True,
        help="Pide confirmaciÛn (s/n/todos/nunca) antes de acciones sensibles "
             "(pasos del planificador, /run y /edit del chat). Por defecto "
             "activado; desactivar con --no-confirmar para modo autom·tico.",
    )
    parser.add_argument(
        "--init-claude", action="store_true",
        help="Escanea el proyecto (estructura, dependencias, git) y genera una "
             "memoria persistente CLAUDE.md (o SNAPCONTEXT.md) usando el "
             "proveedor de IA; sin conexiÛn usa una plantilla b·sica.",
    )
    parser.add_argument(
        "--auto", action="store_true", default=False,
        help="Modo autÛnomo para --plan: salta las confirmaciones paso a paso "
             "(siguiendo respetando permisos.json) y reintenta autom·ticamente "
             "cada paso fallido hasta 3 veces antes de continuar. Con "
             "--no-confirmar no aÒade diferencia adicional.",
    )
    parser.add_argument(
        "--paralelo", type=int, default=1, metavar="N",
        help="En modo --plan --auto: ejecuta hasta N pasos sin dependencias "
             "mutuas en paralelo (por defecto 1 = secuencial). Los logs de cada "
             "paso llevan su identificador [paso N]. Los pasos con campo "
             "'dependencias' esperan a que sus dependencias tengan Èxito y "
             "las condiciones que referencien resultados de pasos previos o "
             "variables MCP bloquean al paso hasta estar disponibles.",
    )
    parser.add_argument(
        "--editor", choices=["aider", "propio"], default="propio",
        help="Editor a usar para aplicar cambios: 'propio' (por defecto "
             "desde v4.1.0; editor integrado con estrategias AST ‚Üí parche ‚Üí "
             "sobrescritura, validaciÛn sint·ctica y backups) o 'aider' "
             "(requiere Aider instalado).",
    )
    # v6.22.0: hooks / lifecycle events ‚Äî activaciÛn y listado.
    parser.add_argument(
        "--hooks", action=argparse.BooleanOptionalAction, default=True,
        help="Activa/desactiva el sistema de hooks/lifecycle events (v6.22.0). "
             "Por defecto: activado; desactivar con --no-hooks.",
    )
    # v6.24.0: orquestaciÛn inteligente de modelos (model_router.py).
    parser.add_argument(
        "--model-routing", dest="model_routing",
        action=argparse.BooleanOptionalAction, default=True,
        help="Activa/desactiva el enrutamiento inteligente de modelos "
             "(v6.24.0): cada categorÌa de tarea (indexacion, busqueda_semantica, "
             "planificacion_simple, edicion_critica, razonamiento_complejo, "
             "chat_general) usa el modelo configurado en la secciÛn "
             "'model_routing' de ~/.snapcontext/config.json. Sin configuraciÛn "
             "se usa el modelo por defecto, como siempre. Los flags --model y "
             "--provider tienen prioridad m·xima; desactivar con "
             "--no-model-routing.",
    )
    # v6.30.0: enrutamiento hÌbrido Local-Nube (fallback entre modelos).
    parser.add_argument(
        "--model-fallback", dest="model_fallback",
        action=argparse.BooleanOptionalAction, default=None,
        help="Activa/desactiva el fallback entre modelos del enrutamiento "
             "hÌbrido Local-Nube (v6.30.0): las tareas simples usan modelos "
             "locales (Ollama) y escalan a la nube (Gemini, Claude, DeepSeek) "
             "cuando la tarea es compleja o el modelo falla (timeout, error "
             "de API; los errores de autenticaciÛn no se reintentan). Por "
             "defecto: activado (o seg˙n 'fallback_automatico' de "
             "config.json); desactivar con --no-model-fallback.",
    )
    parser.add_argument(
        "--complejidad-umbral", dest="complejidad_umbral", type=int,
        default=None, metavar="N",
        help="Longitud mÌnima (en palabras) de la consulta para considerarla "
             "compleja y escalar a modelos cloud (v6.30.0; por defecto 100, "
             "o el 'umbral_complejidad.longitud_consulta' de config.json).",
    )
    parser.add_argument(
        "--model-prioridad-local", dest="model_prioridad_local", nargs="+",
        metavar="PROVEEDOR/MODELO", default=None,
        help="Orden de prioridad de modelos locales (v6.30.0), p. ej. "
             "--model-prioridad-local ollama/qwen3.5:9b ollama/llama3.2. "
             "Sobrescribe 'prioridad_local' de config.json.",
    )
    parser.add_argument(
        "--model-prioridad-nube", dest="model_prioridad_nube", nargs="+",
        metavar="PROVEEDOR/MODELO", default=None,
        help="Orden de prioridad de modelos cloud (v6.30.0), p. ej. "
             "--model-prioridad-nube gemini/gemini-2.5-pro "
             "anthropic/claude-3.7-sonnet. Sobrescribe 'prioridad_nube' de "
             "config.json.",
    )
    parser.add_argument(
        "--hook-list", dest="hook_list", action="store_true",
        help="Lista los hooks registrados por evento y sale (v6.22.0).",
    )
    parser.add_argument(
        "--modelo-ligero", dest="modelo_ligero", action="store_true",
        help="Usa prompts concisos en el editor propio (pensados para "
             "modelos pequeÒos); se activa autom·ticamente con Ollama.",
    )
    parser.add_argument(
        "--asesor-profundo", dest="asesor_profundo", action="store_true",
        help="Asesor exhaustivo (v4.2.0): aÒade an·lisis de seguridad ?? "
             "(inyecciÛn SQL, command injection, path traversal, secretos, "
             "eval/exec, XSS) y rendimiento ‚ö° al asesor b·sico.",
    )
    parser.add_argument(
        "--modo-edicion",
        choices=["sobrescribir", "parche", "auto", "ast"], default="auto",
        help="Estrategia del editor propio: 'auto' (intenta aplicar parche unificado, "
             "fallback a sobrescritura), 'parche' (solo parches unificados), "
             "'sobrescribir' (sobrescritura completa del archivo) o 'ast' "
             "(ediciÛn basada en el ·rbol sint·ctico con fallback a sobrescritura).",
    )
    parser.add_argument(
        "--validar", dest="validar", action="store_const", const=True,
        default=True,
        help="Valida la sintaxis del cÛdigo antes de guardar en el editor propio "
             "(por defecto activado).",
    )
    parser.add_argument(
        "--no-validar-sintaxis", dest="validar", action="store_const",
        const=False,
        help="Desactiva la validaciÛn de sintaxis en el editor propio "
             "(comportamiento previo a v3.4.0). Nota: su nombre no es "
             "'--no-validar' porque ese alias ya est· reservado por "
             "--iniciar-proyecto.",
    )
    parser.add_argument(
        "--max-intentos-validacion", type=int,
        default=MAX_INTENTOS_VALIDACION, metavar="N",
        help=f"Intentos m·ximos de validaciÛn de sintaxis antes de cancelar la "
             f"ediciÛn (por defecto: {MAX_INTENTOS_VALIDACION}).",
    )
    # v6.1.0 ‚Äî Manejo de contexto inteligente (modelos con poca ventana).
    parser.add_argument(
        "--max-context-tokens", type=int, default=MAX_CONTEXT_TOKENS,
        metavar="N",
        help=f"LÌmite m·ximo de tokens estimados a enviar al proveedor en una "
             f"sola peticiÛn de ediciÛn (por defecto: {MAX_CONTEXT_TOKENS}). Los "
             f"archivos m·s grandes se envÌan con contexto selectivo (resumen "
             f"AST + bloque objetivo), evitando los fallos por ventana de "
             f"contexto de los modelos pequeÒos (p. ej. deepseek-r1:14b).",
    )
    parser.add_argument(
        "--mostrar-razonamiento", dest="mostrar_razonamiento",
        action="store_true",
        help="v6.2.0: muestra el razonamiento del modelo (chain-of-thought) "
             "antes de cada acciÛn/respuesta en chat, planificador, editor y "
             "ReAct. TambiÈn activable con la variable de entorno "
             "SNAPCONTEXT_MOSTRAR_RAZONAMIENTO=1.")
    # v6.3.0 ‚Äî RevisiÛn interactiva del parche antes de aplicar.
    parser.add_argument(
        "--mostrar-diff", dest="mostrar_diff", action="store_true",
        help="v6.3.0: muestra el diff propuesto (coloreado) antes de aplicar "
             "un parche y pregunta si aplicarlo, cancelarlo o editarlo "
             "manualmente. En modo --auto muestra el diff sin bloquear. Sin "
             "este flag el parche se aplica sin preguntar (como siempre).",
    )
    parser.add_argument(
        "--editor-fallback", dest="editor_fallback", action="store_true",
        help="v6.1.0: si el editor propio falla (por contexto o por estrategia), "
             "intenta autom·ticamente Aider como respaldo para los archivos "
             "fallidos (requiere 'aider' en el PATH; si no, muestra una "
             "sugerencia clara).",
    )
    # Aprendizaje autÛnomo / memoria avanzada (v3.0.0)
    parser.add_argument(
        "--daemon", action="store_true",
        help="Ejecuta el daemon en segundo plano: corre el curador cada "
             "--daemon-intervalo horas y procesa la cola de skills pendientes.",
    )
    parser.add_argument(
        "--daemon-intervalo", type=int,
        default=DAEMON_INTERVALO_HORAS_DEFECTO, metavar="HORAS",
        help="Horas entre pasadas del curador cuando el daemon est· activo "
             "(por defecto 168 = 7 dÌas).",
    )
    # v6.28.0: Gestor de sesiones persistentes (Agente Fantasma).
    parser.add_argument(
        "--new-session", dest="new_session", nargs="?", const=True,
        default=None, metavar="CONSULTA",
        help="(v6.28.0) Crea una nueva sesion persistente y devuelve su ID. "
             "Si se proporciona CONSULTA, la ejecuta en la nueva sesion.",
    )
    parser.add_argument(
        "--attach", dest="attach", default=None, metavar="ID",
        help="(v6.28.0) Conecta a una sesion existente (por ID).",
    )
    parser.add_argument(
        "--session-timeout", dest="session_timeout", type=int, default=3600,
        metavar="SEGUNDOS",
        help="(v6.28.0) Tiempo de inactividad antes de eliminar una sesion "
             "(por defecto: 3600 = 1 hora).",
    )
    parser.add_argument(
        "--list-sessions", dest="list_sessions", action="store_true",
        default=False,
        help="(v6.28.0) Lista las sesiones activas y sale.",
    )
    parser.add_argument(
        "--curador", action="store_true",
        help="Ejecuta una pasada ˙nica del curador (archiva skills antiguos, "
             "fusiona duplicados) y termina.",
    )
    parser.add_argument(
        "--skills", action="store_true",
        help="Lista los skills aprendidos guardados en la memoria SQLite y "
             "termina.",
    )
    parser.add_argument(
        "--sin-aprendizaje", action="store_true",
        help="Desactiva el aprendizaje continuo (no genera ni refuerza "
             "skills al terminar las tareas).",
    )
    parser.add_argument(
        "--skills-dinamicos", dest="skills_dinamicos",
        action="store_true", default=True,
        help="(v6.6.0) Skills din·micos: extrae reglas abstractas de planes "
             "exitosos y las reutiliza en el planificador (activado por "
             "defecto).",
    )
    parser.add_argument(
        "--sin-skills-dinamicos", dest="skills_dinamicos",
        action="store_false",
        help="Desactiva los skills din·micos (no extrae ni aplica reglas "
             "abstractas).",
    )
    parser.add_argument(
        "--inyectar-reglas", action="store_true",
        help="(v6.6.0) Fuerza la inyecciÛn de todas las reglas aprendidas en "
             "CLAUDE.md/SNAPCONTEXT.md (secciÛn '## Reglas aprendidas') y "
             "termina.",
    )
            # v6.26.0: Memoria a largo plazo (historial de decisiones en SQLite).
    parser.add_argument(
        "--memoria", dest="memoria",
        action=argparse.BooleanOptionalAction, default=True,
        help="Activa/desactiva la memoria a largo plazo (v6.26.0): guarda "
             "decisiones pasadas en SQLite y las reutiliza para enriquecer "
             "el contexto. Activado por defecto; desactivar con --no-memoria.",
    )
    parser.add_argument(
        "--memoria-limite", dest="memoria_limite", type=int, default=100,
        metavar="N",
        help="Numero maximo de decisiones a guardar en el historial "
             "(por defecto: 100).",
    )
    parser.add_argument(
        "--memoria-ver", dest="memoria_ver", action="store_true",
        default=False,
        help="Muestra las ultimas decisiones guardadas y sale (v6.26.0).",
    )

# v6.25.0: QA Tester adversarial (revision destructiva de codigo).
    parser.add_argument(
        "--qa-tester", dest="qa_tester",
        action=argparse.BooleanOptionalAction, default=True,
        help="Activa/desactiva el QA Tester adversarial (v6.25.0): revisa "
             "automaticamente el codigo generado por el Programador buscando "
             "errores de seguridad, rendimiento, logica y estilo. Activado por "
             "defecto; desactivar con --no-qa-tester.",
    )
    parser.add_argument(
        "--qa-iteraciones", dest="qa_iteraciones", type=int, default=2,
        metavar="N",
        help="Numero maximo de iteraciones Programador <-> QA Tester "
             "(por defecto: 2).",
    )
    parser.add_argument(
        "--qa-severidad", dest="qa_severidad",
        choices=["baja", "media", "alta"], default="media",
        help="Nivel de exigencia del QA Tester: 'baja' (solo errores criticos), "
             "'media' (errores y advertencias) o 'alta' (revision exhaustiva). "
             "Por defecto: 'media'.",
    )

# Ayuda agrupada por categorÌas (-h/--help) ‚Äî v1.7.
    parser.add_argument(
        "-h", "--help", action=_AyudaAccion,
        help="Muestra esta ayuda agrupada por categorÌas, con alias y ejemplos.",
    )
    return parser


def _preparar_argv_aliases(argv: Optional[List[str]]) -> List[str]:
    """Convierte el primer token en un alias de comando com˙n.

    Sintaxis ``snapcontext <alias> "mensaje"``:

      - ``fix``         ‚Üí equivalente a ``--test-loop``
      - ``review``      ‚Üí equivalente a ``--vista-previa --experto``
      - ``server``      ‚Üí equivalente a ``--server-loop``
      - ``interactive`` ‚Üí equivalente a ``--web``

    Si el primer token no es un alias conocido, se devuelve ``argv`` intacto
    (comportamiento actual: se trata como consulta del usuario).
    """
    argv = list(argv or [])
    if not argv:
        return argv
    primer = argv[0]
    if primer == "fix":
        return ["--test-loop"] + argv[1:]
    if primer == "review":
        return ["--vista-previa", "--experto"] + argv[1:]
    if primer == "server":
        return ["--server-loop"] + argv[1:]
    if primer == "interactive":
        return ["--web"] + argv[1:]
    return argv


def _candidatos_carpetas_scripts() -> List[str]:
    """Devuelve, en orden de prioridad, las carpetas donde suele instalarse el
    comando `snapcontext` (carpetas de scripts/bin de Python), sin comprobar
    todavÌa si existen. Prioriza el intÈrprete Python en uso."""
    candidatos: List[str] = []

    # 1) Carpeta de scripts del intÈrprete Python en uso (donde pip y
    #    `pip install -e .` registran el comando `snapcontext`). Prioridad m·xima.
    try:
        import sysconfig
        candidatos.append(sysconfig.get_path("scripts"))
    except Exception:
        pass

    # 2) Carpeta Scripts hermana de python.exe.
    dir_python = os.path.dirname(sys.executable)
    candidatos.append(os.path.join(dir_python, "Scripts"))

    # 3) Rutas tÌpicas de instalaciones de usuario en Windows.
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        candidatos.append(os.path.join(appdata, "Python", "Scripts"))
    if localappdata:
        candidatos.append(
            os.path.join(localappdata, "Programs", "Python", "Scripts")
        )
        # Python3X: localizaciones con n˙mero de versiÛn (p. ej. Python313).
        base_prog = os.path.join(localappdata, "Programs", "Python")
        try:
            for nombre in sorted(os.listdir(base_prog)):
                if nombre.lower().startswith("python"):
                    candidatos.append(os.path.join(base_prog, nombre, "Scripts"))
        except OSError:
            pass

    # 4) Ejecutable empaquetado (PyInstaller / cx_Freeze): su propia carpeta.
    if getattr(sys, "frozen", False):
        candidatos.append(os.path.dirname(os.path.abspath(sys.executable)))

    # Eliminar vacÌos y duplicados conservando el orden de prioridad.
    vistos = set()
    unicos: List[str] = []
    for c in candidatos:
        if c and c not in vistos:
            vistos.add(c)
            unicos.append(c)
    return unicos


def _localizar_carpeta_scripts() -> Optional[str]:
    """Localiza la carpeta donde se registra el comando `snapcontext`.

    Prioriza `sysconfig.get_path("scripts")`: si esa carpeta existe se devuelve
    directamente, sin exigir que contenga el ejecutable, porque en instalaciones
    en modo editable el stub `snapcontext` puede tener otro nombre o no estar
    todavÌa en el mismo lugar que apunta el sysconfig.

    Si esa carpeta no existe, se devuelve la primera de las dem·s rutas tÌpicas
    de Python que sÌ exista. Nunca devuelve el directorio del proyecto actual.
    """
    marcadores = ("snapcontext.exe", "snapcontext", "snapcontext.bat")
    intentadas: List[str] = []

    for c in _candidatos_carpetas_scripts():
        if not os.path.isdir(c):
            intentadas.append(c)
            continue
        # En ejecutables empaquetados la carpeta propia no es de scripts;
        # exigimos ahÌ el ejecutable para no devolver una carpeta cualquiera.
        if getattr(sys, "frozen", False):
            if any(os.path.exists(os.path.join(c, m)) for m in marcadores):
                return c
            continue
        return c

    if intentadas:
        depurar("--setup-path: rutas probadas sin Èxito: " + "; ".join(intentadas))
    return None


def _guardar_path_windows(nuevo_path: str) -> bool:
    """Persiste el PATH en la variable de entorno de USUARIO.

    Intenta `setx PATH` (sin /M, no requiere admin) y, si no esta disponible,
    escribe directamente en HKCU\\Environment con `winreg`."""
    try:
        res = subprocess.run(
            ["setx", "PATH", nuevo_path],
            capture_output=True, text=True, timeout=20,
        )
        if res.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as clave:
            winreg.SetValueEx(clave, "PATH", 0, winreg.REG_EXPAND_SZ, nuevo_path)
        return True
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# DiagnÛstico y reparaciÛn (v3.1.0)
# ---------------------------------------------------------------------------
def _diagnostico_item(nombre: str, ok: bool, detalle: str,
                      solucion: Optional[str] = None) -> bool:
    """Imprime una lÌnea de diagnÛstico con color seg˙n el estado."""
    if ok:
        exito(f"{nombre}: {detalle}")
    elif solucion:
        aviso(f"{nombre}: {detalle}")
        print(_pintar("    ‚Üí SoluciÛn: " + solucion, _AMARILLO))
    else:
        error(f"{nombre}: {detalle}")
    return ok


def _comprobar_dependencias_opcionales() -> List[tuple]:
    """Devuelve (modulo, instalado, instalacion) para dependencias opcionales."""
    modulos = [
        ("questionary", "questionary", "pip install snapcontext[interactive]"),
        ("fastapi", "fastapi", "pip install snapcontext[web]"),
        ("uvicorn", "uvicorn", "pip install snapcontext[web]"),
        ("sentence_transformers", "sentence-transformers",
         "pip install sentence-transformers"),
        ("openai", "openai", "pip install openai"),
        ("google.generativeai", "google-generativeai",
         "pip install google-generativeai"),
        ("aider", "aider-chat", "pip install aider-chat"),
    ]
    resultados = []
    for modulo, paquete, extra in modulos:
        try:
            __import__(modulo)
            resultados.append((paquete, True, extra))
        except ImportError:
            resultados.append((paquete, False, extra))
    return resultados


def snapcontext_en_path() -> bool:
    """True si el comando 'snapcontext' es accesible desde el PATH."""
    return shutil.which("snapcontext") is not None


def _estado_memoria() -> dict:
    """Comprueba la base SQLite y el n˙mero de skills.

    Devuelve {'ok': bool, 'skills': int, 'error': str|None}.
    """
    if not os.path.exists(DB_PATH):
        return {"ok": False, "skills": 0,
                "error": f"No existe {DB_PATH} (se crea al primer uso)."}
    try:
        import sqlite3
        con = sqlite3.connect(str(DB_PATH))
        try:
            try:
                resultado = con.execute("PRAGMA quick_check").fetchone()
                if not resultado or resultado[0] != "ok":
                    return {"ok": False, "skills": 0,
                            "error": "La base de datos est· corrupta."}
            except sqlite3.DatabaseError:
                return {"ok": False, "skills": 0,
                        "error": "La base de datos est· corrupta."}
            try:
                skills = con.execute(
                    "SELECT COUNT(*) FROM skills").fetchone()[0]
            except sqlite3.Error:
                skills = 0
            return {"ok": True, "skills": skills, "error": None}
        finally:
            con.close()
    except Exception as exc:
        return {"ok": False, "skills": 0, "error": str(exc)}


def _ejecutar_diagnostico(args: argparse.Namespace) -> int:
    """Modo --diagnostico: revisa la instalaciÛn y muestra un resumen.

    Comprueba Python, instalaciÛn del paquete, dependencias opcionales,
    PATH, proveedor de IA (API key / Ollama) y memoria SQLite.
    Devuelve 0 si todo est· OK, 1 si hay errores y 2 si solo hay avisos.
    """
    info("=== SnapContext ¬∑ DiagnÛstico ===")
    problemas = 0
    avisos = 0

    # 1) Python
    version_py = sys.version.split()[0]
    en_path = any(shutil.which(cmd) for cmd in ("python", "python3", "py"))
    if not _diagnostico_item(
            "Python", en_path,
            f"v{version_py} ({sys.executable})" if en_path
            else "no se encontrÛ 'python' en el PATH",
            "Instala Python 3.9+ desde https://python.org y marca "
            "'Add to PATH'"):
        problemas += 1

    # 2) InstalaciÛn de SnapContext
    if getattr(sys, "frozen", False):
        exito("SnapContext: instalado como ejecutable empaquetado.")
    else:
        try:
            from importlib.metadata import version as _meta_version
            instalada = _meta_version("snapcontext")
            exito(f"SnapContext: instalado (v{instalada}). "
                  "`python -m snapcontext --version` disponible.")
        except Exception:
            aviso("SnapContext no consta como paquete instalado.")
            print(_pintar("    ‚Üí SoluciÛn: pip install snapcontext "
                          "(o python -m pip install -e .)", _AMARILLO))
            avisos += 1

    # 3) Dependencias opcionales
    for paquete, presente, extra in _comprobar_dependencias_opcionales():
        if presente:
            exito(f"Dependencia '{paquete}': OK.")
        else:
            aviso(f"Dependencia opcional '{paquete}' no instalada.")
            print(_pintar(f"    ‚Üí Instalar con: {extra}", _AMARILLO))
            avisos += 1

    # 4) PATH
    if snapcontext_en_path():
        exito("PATH: el comando 'snapcontext' es accesible.")
    else:
        aviso("PATH: 'snapcontext' no es accesible como comando global.")
        print(_pintar("    ‚Üí SoluciÛn: ejecuta 'snapcontext --setup-path' "
                      "(Windows) o reinstala con install.ps1/install.sh",
                      _AMARILLO))
        avisos += 1

    # 5) Proveedor de IA / modo offline
    if hay_api_key_configurada():
        exito("Proveedor de IA: API key configurada.")
    else:
        estado_ol = _estado_ollama()
        if estado_ol["modelos"]:
            ligero = _elegir_modelo_ligero(estado_ol["modelos"])
            exito("Proveedor de IA: sin API key, pero Ollama est· listo "
                  f"(modo offline con '{ligero}').")
        elif estado_ol["instalado"]:
            aviso("Ollama instalado pero sin modelos descargados.")
            print(_pintar("    ‚Üí SoluciÛn: ollama pull llama3.2", _AMARILLO))
            avisos += 1
        else:
            error("No se encontrÛ una API key ni Ollama.")
            print(_pintar("    ‚Üí SoluciÛn: instala Ollama desde "
                          "https://ollama.com o ejecuta 'snapcontext --init'.",
                          _ROJO))
            problemas += 1

    # 6) Memoria (SQLite + skills)
    memoria = _estado_memoria()
    if memoria["ok"]:
        exito(f"Memoria: base de datos OK ({memoria['skills']} skills).")
    elif memoria["error"] and "corrupta" in (memoria["error"] or ""):
        error(f"Memoria: {memoria['error']}")
        print(_pintar("    ‚Üí SoluciÛn: ejecuta 'snapcontext --reparar'",
                      _ROJO))
        problemas += 1
    else:
        aviso(f"Memoria: {memoria['error']}")
        avisos += 1

    print()
    if problemas:
        error(f"DiagnÛstico completado con {problemas} problema(s) y "
              f"{avisos} aviso(s). Ejecuta 'snapcontext --reparar' si lo "
              "necesitas.")
        return 1
    if avisos:
        aviso(f"DiagnÛstico completado: todo funcional, {avisos} aviso(s).")
        return 2
    exito("DiagnÛstico completado: todo correcto ‚úî")
    return 0


def _limpiar_entorno_uv_corrupto() -> bool:
    """Elimina carpetas de entorno de 'uv' vacÌas/corruptas (v3.1.0).

    Un fallo conocido deja entornos vacÌos que rompen reintentos.
    Devuelve True si se limpiÛ algo.
    """
    limpio = False
    for carpeta in (CONFIG_DIR / ".venv-uv", CONFIG_DIR / ".venv"):
        try:
            if carpeta.is_dir() and not any(carpeta.iterdir()):
                carpeta.rmdir()
                info(f"Entorno uv vacÌo eliminado: {carpeta}")
                limpio = True
        except OSError:
            pass
    return limpio


def _reinstalar_snapcontext() -> bool:
    """Reinstala SnapContext con pip (con fallback a `uv pip`)."""
    comando = [sys.executable, "-m", "pip", "install", "--upgrade",
               "--force-reinstall", "--no-deps", "snapcontext"]
    info("Reinstalando SnapContext con pip...")
    try:
        proc = subprocess.run(comando, capture_output=True, text=True,
                              timeout=600)
        if proc.returncode == 0:
            exito("SnapContext reinstalado correctamente.")
            return True
        aviso("pip devolviÛ un error: " +
              ((proc.stderr or proc.stdout or "").strip()[-300:]))
    except (OSError, subprocess.SubprocessError) as exc:
        aviso(f"No se pudo ejecutar pip: {exc}")
    return False


def _reparar_memoria_si_corrupta() -> bool:
    """Recrea la base SQLite si est· corrupta. True si quedÛ operativa."""
    estado = _estado_memoria()
    if estado["ok"]:
        return True
    if estado["error"] and "corrupta" in estado["error"]:
        try:
            copia = DB_PATH.with_suffix(".db.corrupto")
            if os.path.exists(copia):
                copia.unlink()
            DB_PATH.rename(copia)
            _db_init()
            aviso(f"Base de datos corrupta respaldada como '{copia.name}' "
                  "y recreada.")
            return True
        except OSError as exc:
            error(f"No se pudo reparar la base de datos: {exc}")
            return False
    # No existe a˙n: crearla.
    try:
        _db_init()
        exito("Memoria inicializada.")
        return True
    except Exception as exc:                       # pragma: no cover
        error(f"No se pudo inicializar la memoria: {exc}")
        return False


def _ejecutar_reparacion(args: argparse.Namespace) -> int:
    """Modo --reparar: arregla instalaciones rotas paso a paso.

    Pasos: limpiar entornos uv corruptos, reinstalar con pip, reparar la
    base SQLite y aÒadir la carpeta de scripts al PATH (Windows).
    """
    info("=== SnapContext ¬∑ ReparaciÛn ===")
    ok_global = True

    if _limpiar_entorno_uv_corrupto():
        exito("Entornos uv corruptos eliminados.")

    if not _reinstalar_snapcontext():
        ok_global = False

    if _reparar_memoria_si_corrupta():
        exito("Memoria verificada/reparada.")
    else:
        ok_global = False

    if sys.platform.startswith("win") and not snapcontext_en_path():
        aviso("El comando 'snapcontext' sigue sin estar en el PATH; "
              "ejecutando --setup-path...")
        if configurar_path() != 0:
            ok_global = False
    elif snapcontext_en_path():
        exito("PATH correcto: 'snapcontext' accesible.")

    if ok_global:
        exito("ReparaciÛn completada. Prueba 'snapcontext --diagnostico'.")
        return 0
    error("La reparaciÛn terminÛ con incidencias; revisa los mensajes.")
    return 1


def _tutorial_interactivo() -> int:
    """Tutorial interactivo (--bienvenida): guÌa de primeros pasos."""
    info("=== SnapContext ¬∑ Tutorial interactivo ===")
    pasos = [
        ("1. Comprueba tu instalaciÛn",
         "  Ejecuta 'snapcontext --version' y 'snapcontext --diagnostico'\n"
         "  para verificar que todo est· listo."),
        ("2. Configura tu cerebro",
         "  Sin API key, SnapContext usa Ollama local autom·ticamente.\n"
         "  Con clave: 'snapcontext --init' guarda tu proveedor favorito."),
        ("3. Tu primera tarea",
         '  En tu proyecto ejecuta:\n'
         '    snapcontext "describe brevemente este proyecto" --vista-previa\n'
         "  Ver·s quÈ archivos seleccionarÌa la IA sin tocar nada."),
        ("4. Deja que trabaje",
         "  Quita --vista-previa y SnapContext usar· Aider para editar.\n"
         "  AÒade --test-loop para que verifique con tus pruebas."),
        ("5. Aprende m·s",
         "  'snapcontext --help' (ayuda agrupada), 'snapcontext --demo'\n"
         '  y \'snapcontext --plan "tarea"\' (planificador).'),
    ]
    for titulo, detalle in pasos:
        exito(titulo)
        print(detalle)
        print()
        try:
            respuesta = input(
                _pintar("  [Enter] continuar ('q' para salir)... ",
                        _CYAN)).strip().lower()
        except EOFError:
            break
        if respuesta in ("q", "quit", "salir"):
            info("Tutorial interrumpido. Puedes volver a verlo con "
                 "'snapcontext --bienvenida'.")
            return 0
    exito("¬°Tutorial completado! Bienvenido a SnapContext üéâ")
    return 0


def configurar_path() -> int:
    """Configura el PATH del usuario en Windows (--setup-path).

    Es independiente de la consulta: localiza la carpeta de ejecutables, la
    aÒade al PATH persistente del usuario y sale. CÛdigo 0 = Èxito.
    """
    if not sys.platform.startswith("win"):
        error("--setup-path solo funciona en Windows.")
        return 1

    info("Configurando el PATH del usuario para Windows...")
    carpeta = _localizar_carpeta_scripts()
    if not carpeta:
        error("No se pudo localizar autom·ticamente la carpeta de ejecutables "
              "de SnapContext.")
        aviso("Rutas tÌpicas donde suele instalarse el comando 'snapcontext':")
        for r in _candidatos_carpetas_scripts():
            if r:
                aviso("  - " + r)

        # Fallback interactivo: ofrecer indicar la ruta manualmente.
        if _preguntar_si("¬øQuieres indicar la carpeta de Scripts manualmente?"):
            try:
                manual = input(
                    _pintar("Ruta de la carpeta Scripts (p. ej. "
                            "C:\\...\\Python313\\Scripts): ", _CYAN)
                ).strip().strip('"').strip("'")
            except EOFError:  # entrada no interactiva ‚Üí abandonar
                manual = ""
            if manual and os.path.isdir(manual):
                carpeta = manual
            else:
                aviso("Ruta no v·lida o inexistente: no se modificar· el PATH.")

        if not carpeta or not os.path.isdir(carpeta):
            aviso("AÒade la ruta de Scripts manualmente al PATH del usuario "
                  "o vuelve a ejecutar --setup-path tras instalar SnapContext.")
            aviso("SnapContext seguir· funcionando con: 'python -m snapcontext'")
            return 1

    path_actual = os.environ.get("PATH", "")
    if carpeta in [p for p in path_actual.split(";") if p]:
        exito("'" + carpeta + "' ya est· en el PATH del usuario (sin cambios).")
        return 0

    nuevo = carpeta + ";" + path_actual
    os.environ["PATH"] = nuevo

    if _guardar_path_windows(nuevo):
        exito("'" + carpeta + "' aÒadido al PATH persistente del usuario.")
        info("Reinicia tu terminal para que el cambio surta efecto.")
        info("Si instalaste Chocolatey, ejecuta 'refreshenv'.")
        return 0

    error("No se pudo guardar el PATH de forma permanente.")
    aviso("El PATH solo queda activo para esta sesion de terminal.")
    return 1


# ---------------------------------------------------------------------------
# Modo demo (--demo): muestra el valor de SnapContext sin API key ni Aider
# ---------------------------------------------------------------------------
def _crear_demo_proyecto(directorio: Path) -> None:
    """Crea un proyecto Python de ejemplo (con un bug) en ``directorio``.

    Estructura:
      - ``src/main.py``: ``saludar(nombre)`` con un error (usa ``name``).
      - ``tests/test_main.py``: test que falla con el bug.
      - ``src/__init__.py``: hace ``src`` importable para el comando de prueba.

    La carpeta ``src``/``tests`` hace que la auto-detecciÛn clasifique la demo
    como proyecto Python y que el escaneo (--local) encuentre los archivos.
    """
    (directorio / "src").mkdir(parents=True, exist_ok=True)
    (directorio / "tests").mkdir(parents=True, exist_ok=True)
    # Archivo identificador: fuerza la auto-detecciÛn como proyecto Python
    # (evita que `src/` haga que se clasifique como Node en el respaldo por carpetas).
    (directorio / "requirements.txt").write_text("", encoding="utf-8")
    (directorio / "src" / "__init__.py").write_text("", encoding="utf-8")
    (directorio / "src" / "main.py").write_text(
        "def saludar(nombre):\n"
        '    return f"Hola, {name}"  # bug: deberÌa ser {nombre}\n',
        encoding="utf-8",
    )
    (directorio / "tests" / "test_main.py").write_text(
        "from src.main import saludar\n\n\n"
        "def test_saludo():\n"
        '    assert saludar("Mundo") == "Hola, Mundo"\n',
        encoding="utf-8",
    )


def _crear_demo_editor(directorio: Path):
    """Devuelve un "editor" de demostraciÛn que sustituye a Aider en la demo.

    En la primera llamada simula que Aider intenta corregir pero deja el bug
    (para que el tester falle); en la segunda recibe el error realimentado y
    corrige ``name`` ‚Üí ``nombre`` en ``src/main.py``. AsÌ se muestra el ciclo
    completo Editor ‚Üí Tester ‚Üí error ‚Üí correcciÛn ‚Üí Èxito, sin dependencias.
    """
    estado = {"llamadas": 0}
    ruta_main = directorio / "src" / "main.py"

    def _editor(archivos, mensaje, directorio, opciones_aider=""):
        estado["llamadas"] += 1
        if estado["llamadas"] == 1:
            info("‚Üí Aider (demo) intenta corregir el saludo... (a˙n quedar· un error)")
            return True
        info("‚Üí Aider (demo) recibe el error realimentado y corrige 'name' ‚Üí 'nombre'.")
        texto = ruta_main.read_text(encoding="utf-8")
        ruta_main.write_text(
            texto.replace('return f"Hola, {name}"', 'return f"Hola, {nombre}"'),
            encoding="utf-8",
        )
        return True

    return _editor


def _ejecutar_demo() -> int:
    """Ejecuta una demo autÛnoma de SnapContext (sin API key ni Aider real).

    Fases:
      1. Crea un proyecto Python de ejemplo en ``tempfile.mkdtemp()``.
      2. ``--vista-previa --local``: muestra la selecciÛn de archivos relevantes.
      3. ``--test-loop`` (equivalente): ejecuta el bucle de pruebas completo
         (Editor ‚Üí Tester ‚Üí error realimentado ‚Üí correcciÛn ‚Üí Èxito).
      4. Resume el tiempo total, los archivos seleccionados y el resultado.

    Devuelve el cÛdigo de salida (0 = Èxito, 1 = fallo).
    """
    t_inicio = time.monotonic()
    info("=== SnapContext ¬∑ Demo (sin API key) ===")
    info("Creando un proyecto de prueba temporal...")
    tmp = Path(tempfile.mkdtemp(prefix="snapcontext-demo-"))
    try:
        _crear_demo_proyecto(tmp)
        consulta = ("Corrige la funciÛn saludar para que devuelva el saludo "
                    "correcto")

        args = crear_parser().parse_args(
            _preparar_argv_aliases(
                [consulta, "--directorio", str(tmp), "--local", "--depurar"]
            )
        )

        # FASE 1: mostrar la selecciÛn de archivos (sin tocar cÛdigo).
        info("‚îÄ‚îÄ FASE 1 ¬∑ SelecciÛn de archivos (--vista-previa --local) ‚îÄ‚îÄ")
        args.vista_previa = True
        if flujo_principal(args) != 0:
            error("La selecciÛn de archivos fallÛ durante la demo.")
            return 1

        # Tras la fase 1, args ya trae carpetas/extensiones ajustadas por tipo.
        carpetas = list(args.carpetas or CARPETAS_DEFECTO)
        extensiones = getattr(args, "extensiones", None)
        seleccion = listar_archivos_candidatos(
            tmp, carpetas, extensiones=extensiones
        )[: args.max_archivos]

        # FASE 2: bucle de pruebas completo (Editor ‚Üí Tester) offline.
        info("‚îÄ‚îÄ FASE 2 ¬∑ Bucle de pruebas (Editor ‚Üí Tester) ‚îÄ‚îÄ")
        from orquestador import Orquestador  # import diferido para evitar ciclos

        orch = Orquestador()
        orch.agente_editor.ejecutar_aider = _crear_demo_editor(tmp)
        comando_test = [
            sys.executable, "-c",
            "from src.main import saludar; "
            "assert saludar('Mundo') == 'Hola, Mundo', 'saludo incorrecto'; "
            "print('prueba superada')",
        ]
        ok = orch._bucle_test(
            consulta, seleccion, str(tmp),
            opciones_aider="",
            comando_test=comando_test,
            max_iteraciones=3,
        )

        # RESUMEN
        total = time.monotonic() - t_inicio
        _emitir(sys.stdout, "")
        _emitir(sys.stdout, _pintar("=" * 46, _CYAN))
        _emitir(sys.stdout, _pintar("  RESUMEN DE LA DEMO", _CYAN))
        _emitir(sys.stdout, _pintar("=" * 46, _CYAN))
        exito(f"Tiempo total: {total:.1f} s")
        exito(f"Archivos seleccionados ({len(seleccion)}):")
        for archivo in seleccion:
            _emitir(sys.stdout, "   " + _pintar("‚Ä¢ " + archivo, _VERDE))
        exito(f"Resultado de las pruebas: {'√âXITO ‚úî' if ok else 'FALLO ‚úñ'}")
        _emitir(sys.stdout, "")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _registrar_historial_async(args: argparse.Namespace, codigo: int,
                               duracion: float) -> None:
    """Guarda la tarea en el historial en un hilo secundario (no bloquea)."""
    entrada = {
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "consulta": getattr(args, "consulta", None) or "(sin consulta)",
        "archivos": list(getattr(args, "archivos_seleccionados", []) or []),
        "resultado": "Èxito" if codigo == 0 else "fallo",
        "duracion": round(duracion, 2),
    }
    hilo = threading.Thread(target=_guardar_historial,
                            args=(entrada,), daemon=True)
    hilo.start()
    hilo.join(timeout=5)   # espera breve: evita perder la entrada al salir


def flujo_principal(args: argparse.Namespace) -> int:
    """Orquesta el pipeline completo. Devuelve el cÛdigo de salida.

    La lÛgica se delega en el Orquestador (arquitectura de agentes); aquÌ solo
    se conserva la firma de la CLI, la bandera de depuraciÛn global y, desde
    v0.10.0, el registro autom·tico de la tarea en el historial persistente.
    """
    global DEPURAR
    DEPURAR = args.depurar
    # Al ejecutar como `python -m snapcontext` el archivo vive como `__main__`,
    # pero agentes/orquestador hacen `import snapcontext` (copia de mÛdulo). Se
    # sincroniza el flag en el mÛdulo compartido para que los logs salgan.
    import snapcontext as _snap_sync
    _snap_sync.DEPURAR = args.depurar
    from orquestador import Orquestador  # import diferido para evitar ciclos
    inicio = time.monotonic()
    codigo = 1
    try:
        codigo = Orquestador().ejecutar_flujo(args)
        return codigo
    finally:
        # Memoria persistente (v0.10.0): se guarda aunque haya fallo, en un
        # hilo para no bloquear la salida del proceso.
        _registrar_historial_async(args, codigo, time.monotonic() - inicio)
        # Memoria de proyecto (v0.15.0): tras una tarea exitosa se propone
        # (con confirmaciÛn) actualizar CLAUDE.md con lo aprendido.
        if codigo == 0 and MEMORIA_PROYECTO:
            try:
                _actualizar_claude_md_automatico(
                    f"Tarea completada: {getattr(args, 'consulta', '')}",
                    directorio=getattr(args, "directorio", ".") or ".")
            except Exception as exc:        # nunca romper la salida
                depurar(f"[memoria] actualizaciÛn fallÛ: {exc}")

def conectar_db_inicial(args: argparse.Namespace) -> int:
    """Conecta perezosamente a la base de datos si se pasÛ ``--db-url`` (v6.7.0).

    Prepara la conexiÛn para las herramientas MCP ``db_query``/``db_schema``.
    Devuelve 0 (Èxito), 1 (error de conexiÛn) o 2 (driver no instalado).
    """
    url = (getattr(args, "db_url", None) or "").strip()
    if not url:
        return 0
    info("?? Conectando a base de datos...")
    try:
        import mcp_tools_db as dbt
    except Exception as exc:                    # noqa: BLE001
        aviso(f"‚ö†Ô∏è Herramientas de base de datos no disponibles: {exc}")
        return 2
    try:
        resultado = dbt.db_connect(url,
                                   driver=getattr(args, "db_driver", None))
    except Exception as exc:                    # noqa: BLE001
        error(f"‚ö†Ô∏è Error de conexiÛn: {exc}")
        return 1
    if resultado.get("ok"):
        exito(f"‚úÖ Conectado a {resultado.get('motor', 'base de datos')}")
        return 0
    error(f"‚ö†Ô∏è Error de conexiÛn: {resultado.get('error', 'desconocido')}")
    return 1


def iniciar_servidor_web(args: argparse.Namespace) -> int:
    """Arranca la interfaz web (FastAPI + WebSockets) en http://localhost:puerto.

    Importa ``web.app`` de forma diferida para que la CLI funcione sin FastAPI;
    si falta la dependencia opcional, devuelve un mensaje claro y sal con 1.

    v6.5.0: con ``--web-interactive`` se activa adem·s el centro de control
    interactivo (timeline ReAct + diff viewer) en ``/interactive``.
    """
    puerto = int(getattr(args, "web_puerto", 8000) or 8000)
    interactiva = bool(getattr(args, "web_interactive", False))
    try:
        from web.app import arrancar_servidor
    except ImportError as exc:
        error(
            "La interfaz web necesita dependencias opcionales. Instala:\n"
            "  pip install snapcontext[web]\n"
            f"  (o pip install fastapi uvicorn websockets) ‚Äî error: {exc}"
        )
        return 1
    info(f"Interfaz web en http://localhost:{puerto}  (Ctrl+C para salir)...")
    if interactiva:
        info(f"?? Interfaz web interactiva: http://localhost:{puerto}/interactive")
    try:
        arrancar_servidor(puerto=puerto, interactiva=interactiva)
    except KeyboardInterrupt:
        info("Interfaz web detenida.")
    finally:
        if interactiva:
            try:
                from web.interactive import desactivar as _desactivar_hub
                _desactivar_hub()
            except Exception:                # noqa: BLE001 ‚Äî limpieza best-effort
                pass
    return 0


def iniciar_api(args: argparse.Namespace) -> int:
    """Arranca la API p˙blica (v3.6.0) en http://host:puerto.

    Reutiliza ``web.app``; si falta FastAPI/uvicorn muestra cÛmo instalarlas
    (``pip install snapcontext[web]``) y devuelve 1.
    """
    puerto = int(getattr(args, "api_puerto", 8001) or 8001)
    host = getattr(args, "api_host", "127.0.0.1") or "127.0.0.1"
    token = getattr(args, "api_token", None)
    try:
        from web.app import arrancar_api
    except ImportError as exc:
        error(
            "La API necesita dependencias opcionales. Instala:\n"
            "  pip install snapcontext[web]\n"
            f"  (o pip install fastapi uvicorn websockets) ‚Äî error: {exc}"
        )
        return 1
    if not token:
        configuracion = cargar_configuracion()
        if not (configuracion.get("api_key") or "").strip():
            _generar_clave_api()
            aviso("No habÌa API key: se generÛ una nueva y se guardÛ en "
                  "config.json ('api_key'). Consulta con --api-generate-key.")
    info(f"API de SnapContext en http://{host}:{puerto} "
         f"(docs interactivas en /docs y /redoc). Ctrl+C para salir...")
    try:
        arrancar_api(puerto=puerto, host=host, token=token)
    except KeyboardInterrupt:
        info("API detenida.")
    return 0

def _ejecutar_benchmark(args: argparse.Namespace) -> int:
    """``--benchmark``: mide y muestra el tiempo de cada fase (v6.9.0).

    Mide fases reales de SnapContext sin necesidad de API key:
      ‚Ä¢ Inicio (import del mÛdulo + CLI).
      ‚Ä¢ Escaneo de archivos.
      ‚Ä¢ SelecciÛn (embeddings si disponible; si no, heurÌstica local).
      ‚Ä¢ PreparaciÛn de plan (prompt + contexto, offline).
      ‚Ä¢ EdiciÛn (fuzzy matching incremental sobre un archivo sintÈtico).
      ‚Ä¢ DetecciÛn/validaciÛn de pruebas.
      ‚Ä¢ Total.
    Muestra una tabla con `rich` (fallo a print plano si no est· instalado).
    """
    import time as _t
    directorio = getattr(args, "directorio", None) or "."
    filas: List[tuple] = []

    filas.append(("Inicio (import + CLI)",
                  _t.perf_counter() - _TIEMPO_INICIO_MODULO))

    _t0 = _t.perf_counter()
    crear_parser()
    filas.append(("CLI (crear_parser)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    carpetas = list(getattr(args, "carpetas", None) or CARPETAS_DEFECTO)
    try:
        candidatos = listar_archivos_candidatos(
            directorio, carpetas,
            extensiones=getattr(args, "extensiones", None))
    except Exception:                                # noqa: BLE001
        candidatos = []
    filas.append(("Escaneo de archivos", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    seleccion = list(candidatos[:3])
    try:
        if _embeddings_disponibles():
            _indexar_proyecto(directorio)
            seleccion = _seleccionar_archivos_con_embeddings(
                "(benchmark)", directorio, max_archivos=3)
    except Exception:                                # noqa: BLE001
        pass
    filas.append(("SelecciÛn (embeddings/heurÌstica)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    try:
        prompt = PROMPT_PLAN.format(consulta="(benchmark)")
        _enriquecer_prompt_con_reglas(prompt, "(benchmark)")
    except Exception:                                # noqa: BLE001
        pass
    filas.append(("GeneraciÛn de plan (prompt+contexto)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    _fuzzy = _bench_fuzzy_edicion(directorio)
    filas.append(("EdiciÛn (fuzzy matching)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    try:
        import detector_tests as _det                 # noqa: F401
        _det = _det
    except Exception:                                # noqa: BLE001
        pass
    filas.append(("DetecciÛn de pruebas", _t.perf_counter() - _t0))

    total = _t.perf_counter() - _TIEMPO_INICIO_MODULO
    filas.append(("Tiempo total", total))

    _mostrar_tabla_benchmark(filas)
    return 0


def _bench_fuzzy_edicion(directorio: str) -> bool:
    """Ejercita el fuzzy matching incremental sobre un archivo sintÈtico."""
    import tempfile
    try:
        tmp = Path(tempfile.mkdtemp(prefix="sc_bench_"))
        linea = "    return valor * 2\n"
        contenido = ("def calcular_bench(num):\n"
                     + linea * 60
                     + "    return procesar(num)\n")
        archivo = tmp / "bench.py"
        archivo.write_text(contenido, encoding="utf-8")
        original = "    return procesar(num)\n"
        nuevo = "    return procesar_mejor(num)\n"
        parche = _generar_parche(original, nuevo, "bench.py")
        ok = _aplicar_hunks_incremental(parche, str(tmp))
        return ok
    except Exception:                                # noqa: BLE001
        return False


def _mostrar_tabla_benchmark(filas: List[tuple]) -> None:
    """Pinta la tabla de tiempos con `rich` (o print plano sin Èl)."""
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        tabla = Table(title=f"‚ö° Benchmark de rendimiento ‚Äî SnapContext {VERSION}",
                      title_style="bold cyan", header_style="bold magenta")
        tabla.add_column("Fase", style="cyan")
        tabla.add_column("Tiempo (s)", justify="right")
        for nombre, seg in filas:
            tabla.add_row(nombre, f"{seg:.4f}")
        console.print(tabla)
    except Exception:                                # noqa: BLE001 ‚Äî sin rich
        _emitir(sys.stdout, f"‚ö° Benchmark de rendimiento ‚Äî SnapContext {VERSION}")
        for nombre, seg in filas:
            _emitir(sys.stdout, f"  {nombre:<40} {seg:.4f} s")

def _ejecutar_tui(args: argparse.Namespace) -> int:
    """Modo TUI inmersiva (v6.12.0): ``snapcontext --tui [consulta]``.

    Lanza la aplicaciÛn Textual (``tui_app.py``) y ejecuta el flujo de tarea
    habitual (ReAct por defecto) en un hilo demonio. La comunicaciÛn agente ‚Üí
    TUI se realiza vÌa la cola de ``tui_hub`` (nunca bloquea al agente).

    Si Textual no est· instalado, muestra un error claro y devuelve 2.
    """
    try:
        import tui_app
    except Exception as exc:                     # noqa: BLE001
        error(f"No se pudo cargar la TUI: {exc}")
        return 2
    if not getattr(tui_app, "TEXTUAL_DISPONIBLE", False):
        error("Textual no est· instalado. Instala el grupo opcional:\n"
              "    pip install snapcontext[tui]\n"
              "    (o directamente: pip install 'textual>=0.50.0')")
        return 2
    consulta = str(getattr(args, "consulta", "") or "")
    print("üñ•Ô∏è Interfaz TUI inmersiva (Textual) ‚Äî Ctrl+C para salir")
    # Import tardÌo: evita ciclos y mantiene el CLI tradicional intacto.
    import tui_hub
    tui_hub.reiniciar()
    tui_hub.activar()

    def _trabajo_agente() -> None:
        try:
            _ejecutar_modo_tarea(args)
        except Exception as exc:                 # noqa: BLE001 ‚Äî reportar en TUI
            try:
                tui_hub.enviar_log("error", f"Error del agente: {exc}")
                tui_hub.enviar_fin(False, str(exc))
            except Exception:                    # noqa: BLE001
                pass

    hilo = threading.Thread(target=_trabajo_agente, daemon=True,
                            name="snap-tui-agente")
    try:
        codigo = tui_app.ejecutar_tui(consulta=consulta, tarea=hilo,
                                      version=VERSION)
        return int(codigo or 0)
    except Exception as exc:                     # noqa: BLE001
        error(f"La TUI fallÛ: {exc}")
        return 1
    finally:
        tui_hub.desactivar()


def _ejecutar_comando_hook(argv: Optional[list] = None) -> int:
    """Gateway ``snapcontext hook list`` (v6.22.0).

    Por ahora la ˙nica subacciÛn es ``list``, que vuelca los hooks registrados
    (tras cargar plugins y directorio de hooks) en formato legible.
    """
    try:
        import hooks as _hooks
    except Exception as exc:                             # noqa: BLE001
        error(f"No se pudo importar el mÛdulo de hooks: {exc}")
        return 1
    if not argv or argv[0].lower() == "list":
        _hooks.cargar_todos_los_hooks()
        texto = _hooks._listar_hooks_texto()
        if texto:
            _emitir(sys.stdout, texto)
        else:
            info("No hay hooks registrados.")
        return 0
    error(f"Subcomando hook desconocido: {argv[0]!r}. "
          "Uso: 'snapcontext hook list'.")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    # Instala los manejadores de Ctrl+C / SIGTERM (cierre limpio, subprocesos
    # incluidos) antes de hacer nada. Es seguro en Windows y Linux/macOS.
    _registrar_manejadores_senales()
    if argv is None:
        # Al ejecutar como script (`python snapcontext.py ...`) argparse debe
        # ver los argumentos reales; si pas·ramos [] vacÌo, se perderÌan.
        argv = sys.argv[1:]
    # v3.1.1: sin argumentos ‚Üí ayuda resumida y amigable (no un error).
    if not argv:
        _mostrar_ayuda_resumida()
        return 0
    # v4.0.0: subcomando `snapcontext plugin ...` se resuelve antes del
    # parser principal (sus subacciones no son flags de la CLI).
    if argv and argv[0].lower() == "plugin":
        return _ejecutar_comando_plugin(argv[1:])
    # v4.4.0: gateway de omnicanalidad ‚Äî `snapcontext telegram setup ...`.
    if argv and argv[0].lower() == "telegram":
        return _ejecutar_comando_telegram(argv[1:])
    # v4.5.0: gateway de omnicanalidad ‚Äî `snapcontext discord setup ...`.
    if argv and argv[0].lower() == "discord":
        return _ejecutar_comando_discord(argv[1:])
    # v6.8.0: gateway de omnicanalidad ‚Äî `snapcontext github setup ...`.
    if argv and argv[0].lower() == "github":
        return _ejecutar_comando_github(argv[1:])
    # v5.0.0: curador proactivo ‚Äî `snapcontext curador estado|ejecutar|activar|desactivar`.
    if argv and argv[0].lower() == "curador":
        return _ejecutar_comando_curador(argv[1:])
    # v6.20.0: git profundo ‚Äî `snapcontext revert <step>` deshace un paso.
    if argv and argv[0].lower() == "revert":
        return _ejecutar_revert(argv[1] if len(argv) > 1 else None)
    # v6.22.0: hooks ‚Äî `snapcontext hook list` muestra los hooks registrados.
    if argv and argv[0].lower() in ("hook", "hooks"):
        return _ejecutar_comando_hook(argv[1:])
    args = crear_parser().parse_args(_preparar_argv_aliases(argv))
    try:
        # v6.9.0: benchmark de rendimiento por fases (no necesita API key).
        if getattr(args, "benchmark", False):
            return _ejecutar_benchmark(args)
        # Permisos (v0.13.0): sincroniza el interruptor global con --confirmar
        # para todos los modos (chat, planificador, ...).
        global CONFIRMAR_ACCIONES
        CONFIRMAR_ACCIONES = getattr(args, "confirmar", True)
        # v6.24.0: enrutamiento de modelos. Los flags explÌcitos --model /
        # --provider desactivan el enrutado (prioridad m·xima del usuario).
        _explicito = bool(getattr(args, "modelo", None)
                          or getattr(args, "provider", None))
        _configurar_model_routing(
            bool(getattr(args, "model_routing", True)), explicito=_explicito)
        # v6.30.0: enrutamiento hÌbrido Local-Nube (fallback + umbrales CLI).
        _mf_flag = getattr(args, "model_fallback", None)
        if _mf_flag is None:
            try:
                _mf_flag = bool(_cargar_configuracion_routing().get(
                    "fallback_automatico", True))
            except Exception:                            # noqa: BLE001
                _mf_flag = True
        _configurar_model_fallback(bool(_mf_flag))
        _configurar_hibrido_cli(
            umbral=getattr(args, "complejidad_umbral", None),
            prioridad_local=getattr(args, "model_prioridad_local", None),
            prioridad_nube=getattr(args, "model_prioridad_nube", None),
        )
        # v6.31.0: Prompt Caching por Capas (None ? entorno/config/defecto).
        _configurar_prompt_caching_capas(
            getattr(args, "prompt_caching_capas", None))
        # v6.32.0: Pruning proactivo de contexto (None ? config/defecto activado).
        _configurar_pruning(getattr(args, "prune_context", None))
        _configurar_umbral_pruning(getattr(args, "prune_umbral", None))
        # v4.8.0: sincroniza el modo no interactivo de la capa UI (--auto).
        _ui_configurar_auto(bool(getattr(args, "auto", False)))
# v4.8.0: sincroniza el modo no interactivo de la capa UI (--auto).
        _ui_configurar_auto(bool(getattr(args, "auto", False)))
        # v6.4.0: `--sandbox-session-clean` limpia contenedores huÈrfanos y sale.
        if getattr(args, "sandbox_session_clean", False):
            _limpiar_sesiones_huÈrfanas(
                auto=bool(getattr(args, "auto", False)))
            return 0
        # v4.3.0/v5.4.0: polÌtica de sandbox. --no-sandbox gana sobre todo;
        # despuÈs --sandbox explÌcito; y SNAPCONTEXT_SANDBOX=1 activa el
        # contenedor para todo (no estricto: si falta Docker se contin˙a sin
        # Èl, los comandos peligrosos los gestiona _decidir_ejecucion_sandbox).
        if getattr(args, "no_sandbox", False) or \
                os.environ.get("SNAPCONTEXT_SANDBOX") == "0":
            _configurar_no_sandbox(True)
        elif getattr(args, "sandbox_session", False):
            if not _docker_disponible():
                raise RuntimeError(
                    "--sandbox-session solicita Docker pero no est· disponible "
                    "(¬øinstalado? ¬øel daemon est· en ejecuciÛn?). Instala Docker "
                    "Desktop o inicia el servicio 'docker'.")
            _activar_sandbox(
                imagen=getattr(args, "sandbox_imagen", None),
                comando_prep=getattr(args, "sandbox_comando", None),
                estricto=True)
            _SESION_DOCKER_SOLICITADA = True
        elif getattr(args, "sandbox", False):
            _activar_sandbox(
                imagen=getattr(args, "sandbox_imagen", None),
                comando_prep=getattr(args, "sandbox_comando", None),
                estricto=True)
        elif os.environ.get("SNAPCONTEXT_SANDBOX") == "1":
            _activar_sandbox(
                imagen=getattr(args, "sandbox_imagen", None),
                comando_prep=getattr(args, "sandbox_comando", None),
                estricto=False)
        # v5.0.0: arranca el daemon del curador proactivo en segundo plano
        # (hilo demonio; nunca bloquea el CLI). Se omite si se corre bajo un
        # test runner y se puede desactivar con CURADOR_DAEMON=0.
        try:
            import curador_proactivo as _cp
            _argv0 = (sys.argv[0] or "").lower()
            _en_tests = any(x in _argv0 for x in (
                "unittest", "pytest", "py.test"))
            if (os.environ.get("CURADOR_DAEMON", "1") == "1"
                    and not _en_tests):
                _cp.iniciar_daemon_fondo()
        except Exception:                        # noqa: BLE001 ‚Äî nunca bloquea
            pass
        # v5.6.0: verificaciÛn temprana de directorio de proyecto.
        # Si el directorio actual no parece ser raÌz de un proyecto y no se ha
        # usado un flag que no requiera proyecto, muestra un aviso ˙til.
        _salida_proyecto = _advertencia_directorio_proyecto(args)
        if _salida_proyecto is not None:
            return _salida_proyecto
        # v3.1.1: --bienvenida explÌcito ejecuta el tutorial y marca el
        # primer uso como completado (por si quiere volver a verlo).
        if getattr(args, "bienvenida", False):
            codigo = _tutorial_interactivo()
            _marcar_primer_uso_completado()
            return codigo
        # v3.1.1: primer uso ‚Üí tutorial autom·tico y se contin˙a con el
        # comando pedido. Solo en terminales interactivos (nunca en tests,
        # CI o scripts) para evitar bloqueos.
        if _primer_uso_pendiente() and _entrada_interactiva():
            info("?? Parece que es tu primera vez con SnapContext. "
                 "Mostrando el tutorial (--bienvenida)...")
            print()
            _tutorial_interactivo()
            _marcar_primer_uso_completado()
        # --init-claude es independiente: crea la memoria del proyecto y sale.
        if getattr(args, "init_claude", False):
            _generar_claude_md(getattr(args, "provider", None),
                               getattr(args, "modelo", None))
            return 0
        # Memoria de proyecto (v0.15.0): carga CLAUDE.md/SNAPCONTEXT.md si
        # existe, para todos los modos que hablan con el agente.
        global MEMORIA_PROYECTO
        MEMORIA_PROYECTO = _cargar_claude_md()
        # Skills din·micos (v6.6.0): flag global (activado por defecto,
        # se desactiva con --sin-skills-dinamicos).
        global SKILLS_DINAMICOS
        SKILLS_DINAMICOS = bool(getattr(args, "skills_dinamicos", True))
        if MEMORIA_PROYECTO:
            info("?? Memoria de proyecto cargada ("
                 + (_buscar_claude_md().name or "CLAUDE.md") + ").")
        # --init es independiente: configura claves/proveedor y sale.
        if getattr(args, "init", False):
            return asistente_configuracion_inicial()
        # --setup-path es independiente de la consulta: configura el PATH
        # y termina sin recorrer el pipeline (ni pedir consulta).
        if getattr(args, "setup_path", False):
            return configurar_path()
        # v3.1.0: diagnÛstico, reparaciÛn y tutorial son independientes.
        if getattr(args, "diagnostico", False):
            return _ejecutar_diagnostico(args)
        if getattr(args, "reparar", False):
            return _ejecutar_reparacion(args)
        # --web inicia la interfaz web (FastAPI + WebSockets) y bloquea hasta parar.
        if getattr(args, "web", False):
            return iniciar_servidor_web(args)
        # v6.12.0: --tui inicia la TUI inmersiva (Textual) y bloquea hasta salir.
        if getattr(args, "tui", False):
            return _ejecutar_tui(args)
        # v6.20.0: gestiÛn de sub-agentes din·micos (independiente).
        if getattr(args, "sub_agente_listar", False):
            return _ejecutar_listar_sub_agentes()
        if getattr(args, "sub_agente_nuevo", None):
            _nombre, _desc = args.sub_agente_nuevo
            return _registrar_sub_agente_cli(_nombre, _desc)
        # v6.20.0: git profundo ‚Äî revert de un paso (independiente).
        if getattr(args, "git_revert", None) is not None:
            _paso = args.git_revert
            return _ejecutar_revert(
                None if _paso == -1 else str(_paso))
        # --demo ejecuta una demo autÛnoma (sin API key ni Aider) y termina.
        if getattr(args, "demo", False):
            return _ejecutar_demo()
        # --historial-limpiar borra la memoria persistente y termina.
        if getattr(args, "historial_limpiar", False):
            return 0 if _limpiar_historial() else 1
        # --historial muestra las ˙ltimas tareas guardadas y termina.
        if getattr(args, "historial", False):
            _mostrar_historial()
            return 0
        # Memoria avanzada / aprendizaje (v3.0.0). El daemon corre hasta Ctrl+C.
        if getattr(args, "daemon", False):
            _daemon_bucle(
                intervalo_horas=getattr(args, "daemon_intervalo",
                                        DAEMON_INTERVALO_HORAS_DEFECTO))
            return 0
        # --curador ejecuta una pasada ˙nica del curador y termina.
        if getattr(args, "curador", False):
            _curador_ejecutar()
            return 0
        # v6.6.0: --inyectar-reglas vuelca todas las reglas aprendidas en
        # CLAUDE.md/SNAPCONTEXT.md y termina (idempotente).
        if getattr(args, "inyectar_reglas", False):
            import skill_abstraction as _sa
            aÒadidas = _sa.inyectar_todas_las_reglas(
                getattr(args, "directorio", ".") or ".")
            info(f"?? Reglas inyectadas en CLAUDE.md: {aÒadidas} nueva(s).")
            return 0
        # v3.5.0/4.2.0: asesor de cÛdigo (--asesor/--sugerir/--asesor-auto/
        # --asesor-profundo; el profundo implica ejecutar el an·lisis).
        if (getattr(args, "asesor", False)
                or getattr(args, "asesor_auto", False)
                or getattr(args, "asesor_profundo", False)):
            return _ejecutar_asesor(args)
        # v3.6.0: API p˙blica ‚Äî generar clave y/o arrancar el servidor REST.
        if getattr(args, "api_generate_key", False):
            clave = _generar_clave_api()
            exito("API key generada y guardada en ~/.snapcontext/config.json "
                  "('api_key'):")
            _emitir(sys.stdout, _pintar(f"    {clave}", _VERDE))
            return 0
        if getattr(args, "api", False):
            return iniciar_api(args)
        # --skills lista los skills aprendidos y termina.
        if getattr(args, "skills", False):
            filas = _skill_listar(incluir_archivados=True)
            if not filas:
                info("A˙n no hay skills aprendidos. Se crean al completar "
                     "tareas con --plan.")
            for f in filas:
                estado = "archivado" if f["archivado"] else "activo"
                exito(f"#{f['id']} [{estado}] {f['nombre']} "
                      f"(confiabilidad {f['confiabilidad']:.2f}, "
                      f"{f['usos']} usos, {f['fallos']} fallos)")
                if f["descripcion"]:
                    print(f"      {f['descripcion']}")
            return 0
        # --chat abre el REPL interactivo (no requiere consulta).
        # v5.4.1: se respetan los flags --provider/--model en el chat.
        if getattr(args, "chat", False):
            return _ejecutar_chat(
                proveedor=getattr(args, "provider", None),
                modelo=getattr(args, "modelo", None),
                prompt_caching=getattr(args, "prompt_caching",
                                       PROMPT_CACHING_DEFECTO),
            )
        # v5.2.0: el motor ReAct es el modo por defecto; --plan queda como
        # legacy para scripts. El flag --react se acepta (redundante).
        # v6.23.0: modo inteligente por defecto ‚Äî detecta la complejidad y
        # aplica defaults (--local/--mostrar-razonamiento/--auto/--paralelo)
        # solo si el usuario no eligiÛ flags explÌcitos.
        args = _aplicar_modo_inteligente(args)
        # v6.24.0: mensaje de enrutamiento al inicio de la tarea.
        if (_MODEL_ROUTING_ACTIVO and not _MODELO_EXPLICITO
                and getattr(args, "consulta", None)):
            try:
                import model_router as _mr              # noqa: E402
                _cat = _mr.clasificar_tarea(args.consulta)
                _p, _m = _mr.seleccionar_modelo(
                    _cat,
                    {"model_routing": _cargar_configuracion_routing()})
                if _p:
                    _m_ef = _m or PROVEEDORES[_p]["modelo_default"]
                    info(f"?? Modelo enrutado: {_cat} ‚Üí {_p}/{_m_ef}")
            except Exception:                            # noqa: BLE001
                pass
        return _ejecutar_modo_tarea(args)
    except KeyboardInterrupt:
        error("Interrumpido por el usuario.")
        return 130
    except RuntimeError as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
