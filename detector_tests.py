#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detección automática de pruebas de SnapContext — v5.3.0.

Permite al agente ReAct y al planificador (``--test-loop``) ejecutar las
pruebas de un proyecto **sin configurar el comando manualmente**: escanea
archivos clave del directorio raíz, detecta el lenguaje/framework y devuelve el
comando de test apropiado.

El módulo es **rápido y ligero** (solo mira nombres de archivos en la raíz y,
en el caso de ``pyproject.toml``, lee su contenido) y **extensible**: para
añadir un nuevo lenguaje basta con registrar su comando/estructura en
``_LENGUAJES`` y su archivo identificador en ``_DETECCION_POR_ARCHIVO`` o
``_REGLAS_CONTENIDO``.

Diseñado sin dependencias externas (solo stdlib: ``os``, ``pathlib``, ``re``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional


# Registro de lenguajes/frameworks soportados.
#   "comando"  : comando de prueba exacto a ejecutar.
#   "estructura": {carpeta, patron} de tests.
_LENGUAJES: Dict[str, Dict[str, str]] = {
    "go": {
        "comando": "go test ./...",
        "estructura": {"carpeta": "", "patron": "*_test.go"},
    },
    "rust": {
        "comando": "cargo test",
        "estructura": {"carpeta": "tests/", "patron": "*.rs"},
    },
    "java-maven": {
        "comando": "mvn test",
        "estructura": {"carpeta": "src/test/java", "patron": "*Test.java"},
    },
    "java-gradle": {
        "comando": "gradle test",
        "estructura": {"carpeta": "src/test/java", "patron": "*Test.java"},
    },
    "python-pytest": {
        "comando": "pytest",
        "estructura": {"carpeta": "tests/", "patron": "test_*.py"},
    },
    "python-unittest": {
        "comando": "python -m unittest discover",
        "estructura": {"carpeta": "tests/", "patron": "test_*.py"},
    },
    "node-npm": {
        "comando": "npm test",
        "estructura": {"carpeta": "test/", "patron": "*.test.js"},
    },
    "node-yarn": {
        "comando": "yarn test",
        "estructura": {"carpeta": "test/", "patron": "*.test.js"},
    },
    "flutter": {
        "comando": "flutter test",
        "estructura": {"carpeta": "test/", "patron": "*_test.dart"},
    },
    "dotnet": {
        "comando": "dotnet test",
        "estructura": {"carpeta": "tests/", "patron": "*Tests.cs"},
    },
    "ruby": {
        "comando": "bundle exec rspec",
        "estructura": {"carpeta": "spec/", "patron": "*_spec.rb"},
    },
    "elixir": {
        "comando": "mix test",
        "estructura": {"carpeta": "test/", "patron": "*_test.exs"},
    },
}

# Índice comando por lenguaje para consultas rápidas.
_COMANDOS = {L: info["comando"] for L, info in _LENGUAJES.items()}

# Archivos cuya sola presencia en la raíz identifica el lenguaje.
# Extensible: añade `"mi_archivo": "mi-lenguaje"` para soportar un nuevo
# lenguaje basado en un único archivo identificador.
_DETECCION_POR_ARCHIVO: Dict[str, str] = {
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java-maven",
    "build.gradle": "java-gradle",
    "pubspec.yaml": "flutter",
    "Gemfile": "ruby",
    "mix.exs": "elixir",
    "Cargo.lock": "rust",          # refuerzo, por si no hay Cargo.toml en la raíz
}

# Archivos cuyo identificador puede depender (opcionalmente) de su contenido.
# Cada regla: {archivo, marcador, lenguaje}. Si `marcador` está vacío, la sola
# presencia del archivo basta. Se evalúan en orden de prioridad.
_REGLAS_CONTENIDO: List[Dict[str, str]] = [
    {"archivo": "yarn.lock", "marcador": "", "lenguaje": "node-yarn"},
    {"archivo": "package.json", "marcador": "", "lenguaje": "node-npm"},
    {
        "archivo": "pyproject.toml", "marcador": "pytest",
        "lenguaje": "python-pytest",
    },
    {
        "archivo": "pyproject.toml", "marcador": "",
        "lenguaje": "python-unittest",
    },
]

_RE_CSPROJ = re.compile(r".*\.csproj$", re.IGNORECASE)


def _leer_pyproject(pyproject):
    try:
        return pyproject.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def detectar_lenguaje(directorio):
    """Detecta el lenguaje/framework de un proyecto escaneando la raiz.

    Args:
        directorio: Ruta (absoluta o relativa) del directorio raiz.

    Returns:
        Identificador del lenguaje ("go", "python-pytest", ...) o None.
    """
    ruta = Path(directorio)
    if not ruta.is_dir():
        return None

    # 1) Archivos inequivocos por nombre.
    for archivo, lenguaje in _DETECCION_POR_ARCHIVO.items():
        if (ruta / archivo).exists():
            return lenguaje

    # 2) Archivos cuyo identificador depende (opcionalmente) del contenido.
    for regla in _REGLAS_CONTENIDO:
        camino = ruta / regla["archivo"]
        if not camino.exists():
            continue
        marcador = regla.get("marcador", "")
        if marcador:
            contenido = _leer_pyproject(camino) or ""
            if marcador not in contenido:
                continue
        return regla["lenguaje"]

    # 3) Patron por extension: *.csproj en la raiz -> dotnet.
    for candidato in ruta.iterdir():
        if candidato.is_file() and _RE_CSPROJ.match(candidato.name):
            return "dotnet"

    # 4) Comodines de Python: setup.py (unittest) / requirements.txt (pytest).
    if (ruta / "setup.py").exists():
        return "python-unittest"
    if (ruta / "requirements.txt").exists():
        return "python-pytest"

    return None


def detectar_comando_test(directorio, lenguaje=""):
    """Devuelve el comando de prueba exacto para el lenguaje detectado.

    Args:
        directorio: Directorio raiz.
        lenguaje: Identificador devuelto por ``detectar_lenguaje``.

    Returns:
        Comando a ejecutar (p. ej. ``"go test ./..."``) o None si el
        lenguaje no esta soportado.
    """
    if not lenguaje:
        lenguaje = detectar_lenguaje(directorio)
    if not lenguaje:
        return None
    return _COMANDOS.get(lenguaje)


def detectar_estructura_tests(directorio):
    """Devuelve informacion sobre la estructura de tests del proyecto.

    Returns:
        Dict con las claves "carpeta" y "patron" (p. ej.
        {"carpeta": "tests/", "patron": "test_*.py"}). Si no hay lenguaje
        devuelve {}.
    """
    lenguaje = detectar_lenguaje(directorio)
    if not lenguaje:
        return {}
    info = _LENGUAJES.get(lenguaje)
    return dict(info["estructura"]) if info else {}


def detectar_automaticamente(directorio):
    """Deteccion completa: lenguaje + comando + estructura.

    Funcion principal que llaman el agente ReAct y el planificador. Devuelve::

        {
            "lenguaje": "go",
            "comando": "go test ./...",
            "estructura": {"carpeta": "", "patron": "*_test.go"},
            "detectado": True,   # False si no se pudo detectar nada
        }
    """
    lenguaje = detectar_lenguaje(directorio)
    if lenguaje is None:
        return {
            "lenguaje": None,
            "comando": None,
            "estructura": {},
            "detectado": False,
        }
    return {
        "lenguaje": lenguaje,
        "comando": detectar_comando_test(directorio, lenguaje),
        "estructura": detectar_estructura_tests(directorio),
        "detectado": True,
    }


def resolver_comando_test(directorio, comando_explicito=""):
    """Resuelve el comando a usar: explicito > auto-detectado > None.

    Prioriza un comando proporcionado por el usuario y solo si no viene uno
    cae a la deteccion automatica. Devuelve None si nada funciona.
    """
    if comando_explicito and str(comando_explicito).strip():
        return str(comando_explicito).strip()
    det = detectar_automaticamente(directorio)
    if det["detectado"] and det["comando"]:
        return str(det["comando"])
    return None