#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SnapContext — Asistente de IA para desarrollo con contexto automático.

Pipeline:
    1) Detecta automáticamente el tipo de proyecto (Flutter, Node, Python, Go, Rust, etc.)
       y ajusta carpetas/extensiones por defecto.
    2) Escanea automáticamente el repositorio (por defecto según el tipo de proyecto):
       buscando archivos relevantes para la consulta del usuario.
    3) Usa Gemini (Google AI Studio) para seleccionar los archivos más
       relevantes, sin que el desarrollador tenga que listarlos a mano.
    4) Ejecuta Aider con los archivos seleccionados y la consulta original.
    5) (Opcional, --test-loop) Después de Aider ejecuta las pruebas
       (flutter test) y, si fallan, vuelve a llamar a Aider con el error
       para que las arregle.

Requisitos:
    - Python 3.9+
    - pip install google-generativeai   (proveedor por defecto: Gemini)
    - pip install openai                (DeepSeek, Groq y Ollama)
    - Variable de entorno según proveedor (GEMINI_API_KEY, DEEPSEEK_API_KEY,
      GROQ_API_KEY) y, opcionalmente, OLLAMA_URL para Ollama local
    - Aider instalado: pip install aider-chat

Uso:
    snapcontext "el botón de pago no funciona"
    snapcontext "el botón de pago no funciona" --test-loop
    snapcontext "arreglar el checkout" --server-loop      # servidor automático
    snapcontext "arreglar login" --manual-loop            # servidor manual
    snapcontext "revisar pago" --experto                  # revisar/editar archivos
    snapcontext fix "el botón de pago no funciona"        # alias: test-loop
    snapcontext review "revisar código"                   # alias: vista-previa + experto
    snapcontext server "iniciar servidor"                 # alias: server-loop
    snapcontext "..." --provider groq --model llama-3.3-70b-versatile

Open-source y pensado para ser fácil de extender (ver ejecutar_bucle_test).
"""

import argparse
import ast
import difflib
import fnmatch
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import warnings
import webbrowser
from pathlib import Path
from typing import Any, List, Optional, Union
from urllib.parse import urlparse

# `google-generativeai` es la dependencia del proveedor por defecto (Gemini).
# Se importa de forma opcional para que incluso `--help` funcione sin ella.
# La versión actual emite un FutureWarning al importar; lo silenciamos porque
# es la API estable que pedimos usar (documentada) y no afecta al código.
try:
    with warnings.catch_warnings():
        # Silenciamos SOLO el FutureWarning de puesta al día de la librería
        # dentro de este import; no afecta al resto del programa.
        warnings.simplefilter("ignore", FutureWarning)
        import google.generativeai as genai
except ImportError:  # pragma: no cover
    genai = None

# DeepSeek, Groq y Ollama exponen una API compatible con OpenAI, así que usan
# la librería `openai`. Import opcional por la misma razón que la anterior.
try:
    import openai
except ImportError:  # pragma: no cover
    openai = None

# Claude (Anthropic) usa su propio SDK oficial (`anthropic`), distinto de la
# API estilo OpenAI. Import diferido por la misma razón que los anteriores.
try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

# Embeddings locales (búsqueda semántica, v1.1.0). Opcional: sin él, la
# selección de archivos usa heurística + proveedor como siempre.
try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except ImportError:  # pragma: no cover
    SentenceTransformer = None

# Análisis sintáctico avanzado con tree-sitter (v1.3.0, extra `mcp_avanzado`).
# Opcional: sin él, la herramienta `ast_avanzado` vuelve a `ast` (solo Python).
try:
    import tree_sitter  # type: ignore
    from tree_sitter import Language  # type: ignore
    try:
        import tree_sitter_languages as _ts_lang  # type: ignore
    except ImportError:  # pragma: no cover
        _ts_lang = None
except ImportError:  # pragma: no cover
    tree_sitter = None
    Language = None
    _ts_lang = None

# Ejecución en paralelo de pasos del plan (v1.3.0) — stdlib, sin deps extra.
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

VERSION = "3.3.0"

# v3.1.0 — Claves de API reconocidas para el modo por defecto (offline).
CLAVES_API_CONOCIDAS = (
    "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
    "GROQ_API_KEY", "OPENAI_API_KEY",
)

# v3.1.0 — Modelos ligeros preferidos en modo offline (por orden de prioridad).
MODELOS_LIGEROS_OLLAMA = ("llama3.2:1b", "llama3.2", "phi3", "gemma2:2b",
                          "qwen2.5:0.5b")

# v3.1.0 — Mensaje cuando no hay ni API key ni Ollama disponible.
MENSAJE_SIN_CLAVE_NI_OLLAMA = (
    "No se encontró una API key ni Ollama.\n"
    "Puedes instalar Ollama desde https://ollama.com o configurar una API\n"
    "key con 'snapcontext --init'.\n"
    "Alternativas:\n"
    "  PowerShell :  $env:GEMINI_API_KEY=\"tu_clave\"\n"
    "  Linux/Mac  :  export GEMINI_API_KEY=tu_clave\n"
    "  Diagnóstico:  snapcontext --diagnostico"
)


# ─── Configuración por tipo de proyecto ────────────────────────────────────
# Detección automática de carpetas y extensiones según el tipo de proyecto detectado.
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

# Mapeo de archivos clave para la detección automática de tipo de proyecto.
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
   ┌──────────────────────────────────────────────────────────┐
   │                                                          │
   │                                                          │
   │    ███████╗███╗   ██╗ █████╗ ██████╗  ██████╗ ██████╗   │
   │    ██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔════╝██╔════╝   │
   │    ███████╗██╔██╗ ██║███████║██████╔╝██║     ██║        │
   │    ╚════██║██║╚██╗██║██╔═══╝ ██║     ██║     ██║        │
   │    ███████║██║ ╚████║██║  ██║██║     ╚██████╗╚██████╗   │
   │    ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝      ╚═════╝ ╚═════╝   │
   │                                                          │
   │    » Selección inteligente de archivos                  │
   │    » Soporte: Gemini · Ollama · DeepSeek · Groq        │
   │    » __VERSION__                                             │
   │                                                          │
   └──────────────────────────────────────────────────────────┘
""".replace("__VERSION__", "v" + VERSION)


def _detectar_tipo_proyecto(directorio: str) -> Optional[str]:
    """Detecta automáticamente el tipo de proyecto buscando archivos clave.

    Args:
        directorio: Ruta del directorio a analizar (raíz ya resuelta).

    Returns:
        El tipo detectado (flutter, node, python, go, rust, …) o None si no hay.
    """
    ruta = Path(directorio)
    if not ruta.is_dir():
        return None

    for nombre_archivo, tipo in _CORRECTORES_ARCHIVOS_IDENTIFICADORES.items():
        if (ruta / nombre_archivo).exists():
            depurar(f"[Detección] {nombre_archivo} encontrado → tipo: {tipo}")
            return tipo

    # Sin archivo identificador, se busca una carpeta típica por tipo.
    for tipo, info in _CORRECTORES_CARPETAS_PROYECTO.items():
        for carpeta in info.get("carpetas_defecto", []):
            if (ruta / carpeta).exists():
                depurar(f"[Detección] Carpetilla típica de {tipo}: {carpeta}/")
                return tipo
    return None


def _ajustar_parametros_por_tipo(tipo: Optional[str], args):
    """Ajusta ``args`` según el tipo de proyecto detectado.

    Solo modifica ``carpetas`` y ``extensiones`` si NO se pasaron explícitamente
    por CLI, y es transparente para el usuario (nada se muestra salvo ``--depurar``).
    """
    if getattr(args, "carpetas", None):
        depurar("[Detección] Carpetas explícitas — no se sobrescriben.")
        return args

    if not tipo:
        depurar("[Detección] Sin tipo detectado — carpetas por defecto actuales.")
        return args

    info = _CORRECTORES_CARPETAS_PROYECTO.get(tipo)
    if info and not getattr(args, "carpetas", None):
        args.carpetas = list(info["carpetas_defecto"])
        depurar(f"[Detección] Carpetas para {tipo}: {args.carpetas}")

    extensiones = _CORRECTORES_EXTENSIONES_PROYECTO.get(tipo)
    if extensiones and not getattr(args, "extensiones", None):
        args.extensiones = list(extensiones)
        depurar(f"[Detección] Extensiones para {tipo}: {args.extensiones}")

    return args

_LOGO_SMALL = f"""
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551           SnapContext  v{VERSION: <9}            \u2551
\u2551   Asistente de IA con contexto autom\u00e1tico    \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d
"""

# ---------------------------------------------------------------------------
# Configuración por defecto (se puede sobrescribir con argumentos CLI o env)
# ---------------------------------------------------------------------------
CARPETAS_DEFECTO = ("lib", "supabase")   # carpetas que se escanean
CARPETAS_PROYECTO_VALIDAS = ("lib", "src", "supabase", "app", "packages", "backend")
# Archivos de configuración que indican un proyecto válido aunque estén vacíos
# (v1.3.0: la validación ya no exige contenido, solo su presencia).
ARCHIVOS_CONFIG_PROYECTO = frozenset((
    "pubspec.yaml", "package.json", "requirements.txt", "go.mod",
    "cargo.toml", "setup.py", "pyproject.toml",
))
# Extensiones de código que, presentes en la raíz (aunque el archivo esté
# vacío), también validan la carpeta como proyecto.
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
MAX_CANDIDATOS_DEFECTO = 80                        # candidatos que se envían al selector IA
MAX_ITERACIONES_TEST_DEFECTO = 3
COMANDO_TEST_DEFECTO = "flutter test"

# ---------------------------------------------------------------------------
# Proveedores de IA para la selección de archivos
# ---------------------------------------------------------------------------
#  tipo           : "gemini" usa la librería google.generativeai;
#                   "openai" usa la librería openai (APIs compatibles con OpenAI).
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

# Palabras vacías (español/inglés) que no aportan información al buscar.
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

MAX_LINEAS_CONTENIDO = 250       # líneas por archivo que se puntúan al escanear
TAMANO_MAX_ARCHIVO = 512 * 1024  # bytes; archivos más grandes no se leen
MAX_ERROR_SALIDA = 6000          # caracteres de salida de test que se muestran a Aider

# En Windows la consola puede usar cp1252/cp437 y los símbolos unicode rompen
# los print. Aquí forzamos UTF-8 con reemplazo seguro y, además, tenemos una
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
    "\u2139": "[i]",           # ℹ
    "\u2714": "[OK]",          # ✔
    "\u26a0": "[!]",           # ⚠
    "\u2716": "[ERROR]",       # ✖
    "\u2022": "-",             # •
    "\u2192": "->",            # →
    "\u2014": "-",            # — (em dash)
}


def _consola_es_utf8() -> bool:
    """Heurística: todas las salidas estándar soportan UTF-8 sin excepción."""
    for _flujo in (sys.stdout, sys.stderr):
        try:
            codificacion = (_flujo.encoding or "").lower().replace("-", "")
        except Exception:
            codificacion = ""
        if codificacion and "utf" not in codificacion:
            return False
    return True


def _texto_seguro(texto: str) -> str:
    """Reemplaza símbolos Unicode por alternativas ASCII si la consola no es UTF-8."""
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


def info(msg: str) -> None:
    _emitir(sys.stdout, _pintar("\u2139 " + msg, _CYAN))


def exito(msg: str) -> None:
    _emitir(sys.stdout, _pintar("\u2714 " + msg, _VERDE))


def aviso(msg: str) -> None:
    _emitir(sys.stdout, _pintar("\u26a0 " + msg, _AMARILLO))


def error(msg: str) -> None:
    _emitir(sys.stderr, _pintar("\u2716 " + msg, _ROJO))


def depurar(msg: str) -> None:
    if DEPURAR:
        _emitir(sys.stdout, _pintar("  [depuración] " + msg, _GRIS))


# ---------------------------------------------------------------------------
# Mensajes de error reutilizables
# ---------------------------------------------------------------------------
MENSAJE_GENAI_FALTANTE = (
    "No se encontró la librería 'google.generativeai'.\n"
    "Instálala con:  pip install google-generativeai   (o: pip install -e .)"
)
MENSAJE_API_KEY = (
    "No se encontró la variable de entorno GEMINI_API_KEY.\n"
    "Crea una API key en https://aistudio.google.com/apikey y configúrala:\n"
    "  PowerShell:  $env:GEMINI_API_KEY=\"tu_clave\"\n"
    "  Linux/Mac :  export GEMINI_API_KEY=tu_clave"
)
MENSAJE_AIDER_FALTANTE = (
    "No se encontró el comando 'aider' en el PATH.\n"
    "Instálalo con:  pip install aider-chat"
)
MENSAJE_OPENAI_FALTANTE = (
    "Este proveedor usa la librería 'openai' (API compatible con OpenAI).\n"
    "Instálala con:  pip install openai"
)
MENSAJE_ANTHROPIC_FALTANTE = (
    "Este proveedor usa la librería 'anthropic' (API oficial de Claude).\n"
    "Instálala con:  pip install snapcontext[anthropic]\n"
    "  (o directamente: pip install anthropic>=0.30.0)"
)
# Memoria persistente (~/.snapcontext/historial.json): últimas tareas realizadas.
HISTORIAL_PATH = CONFIG_DIR / "historial.json"
MAX_HISTORIAL_ENTRADAS = 200      # se recorta para que el archivo no crezca sin límite

# ---------------------------------------------------------------------------
# Señales y cierre limpio (Ctrl+C / SIGTERM) — multiplataforma
# ---------------------------------------------------------------------------
# Registro de subprocesos activos (servidores Flutter...) para poder cerrarlos
# desde el manejador de señales y no dejar procesos huérfanos.
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
    código 0 (cierre controlado en lugar de la excepción por defecto). Se
    protege con try/except por si la plataforma no permite registrar alguna
    señal (p. ej. SIGTERM no se entrega en Windows).
    """
    def _manejar(signum, frame):  # noqa: ARG001
        _apagar_subprocesos()
        error(f"Señal {signum} recibida. SnapContext se está cerrando...")
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
    """Minúsculas y sin acentos. 'botón' -> 'boton' (clave para buscar en español)."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def tokenizar(consulta: str) -> List[str]:
    """Convierte la consulta en palabras clave útiles (sin stopwords)."""
    tokens = re.findall(r"[a-z0-9_]+", normalizar(consulta))
    return [t for t in tokens if len(t) > 1 and t not in PALABRAS_VACIAS]


# ---------------------------------------------------------------------------
# Resolución del repositorio
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

    - Si el usuario pasa `--directorio` explícito, se usa esa ruta tal cual
      (solo se comporta como repo git si contiene .git directamente). Así un
      directorio suelto (p. ej. una copia en %TEMP%) no "hereda" repos git
      de carpetas padre (como el home de usuario).
    - Si no se pasa directorio (por defecto: '.'), se busca la raíz del repo
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

    Criterios (v1.3.0, más permisivos para proyectos nuevos):
      - Existe al menos una carpeta típica (lib/, src/, supabase/, app/,
        packages/, backend/), AUNQUE ESTÉ VACÍA.
      - O existe al menos un archivo de código en la raíz (.py, .dart, .js,
        .ts, .go, .rs, .java, ...), AUNQUE ESTÉ VACÍO.
      - O existe un archivo de configuración típico (pubspec.yaml,
        package.json, requirements.txt, go.mod, Cargo.toml, setup.py,
        pyproject.toml), AUNQUE ESTÉ VACÍO.
    """
    ruta = Path(directorio)
    if not ruta.is_dir():
        return False
    # 1) Carpetas típicas (aunque estén vacías).
    if any((ruta / carpeta).is_dir() for carpeta in CARPETAS_PROYECTO_VALIDAS):
        return True
    try:
        entradas = list(ruta.iterdir())
    except OSError:
        return False
    for entrada in entradas:
        nombre = entrada.name.lower()
        # 2) Archivo de configuración típico en la raíz (aunque vacío).
        if nombre in ARCHIVOS_CONFIG_PROYECTO:
            return True
        # 3) Archivo de código en la raíz (aunque vacío).
        if entrada.is_file() and entrada.suffix.lower() in EXT_CODIGO_RAIZ:
            return True
    return False


def _normalizar_relativa(ruta: str) -> str:
    """Normaliza una ruta relativa a POSIX sin '.' ni '..' ni dobles '//'.

    Se usa para que los archivos que pasan a Aider (o que añade el usuario)
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
# Escaneo del repositorio (búsqueda local de candidatos)
# ---------------------------------------------------------------------------
def _pertenece_a_carpetas(ruta: str, carpetas: List[str]) -> bool:
    """True si la ruta relativa cae dentro de alguna carpeta de interés."""
    for carpeta in carpetas:
        prefijo = carpeta.replace("\\", "/").rstrip("/") + "/"
        if ruta.startswith(prefijo) or ruta == carpeta.rstrip("/"):
            return True
    return False


def _es_archivo_indexable(ruta: str) -> bool:
    """Descarta binarios, imágenes, fuentes y archivos en carpetas ignoradas."""
    if Path(ruta).suffix.lower() in EXT_IGNORADAS:
        return False
    partes = ruta.split("/")
    return not any(p in DIRS_IGNORADOS for p in partes[:-1])


def listar_archivos_candidatos(raiz: Path, carpetas: List[str],
                               extensiones: Optional[List[str]] = None) -> List[str]:
    """Devuelve las rutas (relativas, formato POSIX) de `carpetas` bajo `raiz`.

    Prioridad:
      1. `git ls-files -c -o --exclude-standard`: respeta .gitignore e incluye
         archivos nuevos aún sin commitear.
      2. Si no hay repo git (o falla), recorre el árbol con os.walk.
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
                depurar(f"git ls-files devolvió {proc.returncode}; se usará os.walk")
                usa_git = False
        except (OSError, subprocess.SubprocessError):
            depurar("git no está disponible; se usará os.walk")
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
    """Puntos por coincidencia en la ruta: el nombre del archivo pesa más que
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
                break  # un punto por carpeta coincidente, como máximo
    return puntuacion


def puntuar_contenido(archivo: Path, tokens: List[str]) -> float:
    """Lee las primeras líneas del archivo y suma cuántas veces aparece cada
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
    """Escanea el repositorio y devuelve los mejores candidatos (heurística
    local) para la consulta, ordenados de más a menos relevante.

    Fases:
      1. Listar archivos de `carpetas` (con git o walking).
      2. Si hay muchos, pre-filtrar por coincidencia en la ruta.
      3. Puntuar también el contenido de los que quedaron.
      4. Devolver los `max_candidatos` mejores para que Gemini elija.
    """
    carpetas = list(carpetas) if carpetas else list(CARPETAS_DEFECTO)
    raiz = resolver_raiz(directorio)
    archivos = listar_archivos_candidatos(raiz, carpetas, extensiones=extensiones)
    if not archivos:
        return []

    tokens = tokenizar(consulta)
    if not tokens:
        # Consulta sin palabras clave útiles: se devuelve una muestra ordenada.
        return archivos[:max_candidatos]

    if len(archivos) > 200:
        # Pre-filtro por ruta para no leer el contenido de miles de archivos.
        archivos = sorted(
            archivos, key=lambda p: puntuar_ruta(p, tokens), reverse=True
        )[:200]

    puntuados = [
        (p, puntuar_ruta(p, tokens) + 0.5 * puntuar_contenido(raiz / p, tokens))
        for p in archivos
    ]
    puntuados.sort(key=lambda par: par[1], reverse=True)
    return [p for p, _ in puntuados[:max_candidatos]]

# ---------------------------------------------------------------------------
# Selección con Gemini (elige los archivos más relevantes entre candidatos)
# ---------------------------------------------------------------------------
def construir_prompt_seleccion(consulta: str, archivos: List[str],
                               max_archivos: int) -> str:
    """Prompt que pide a Gemini elegir las `max_archivos` rutas más relevantes
    respondiendo solo con JSON (facilita el parseo)."""
    lista = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(archivos))
    return (
        "Eres el módulo de selección de archivos de SnapContext, una herramienta "
        "de IA para desarrollo con Flutter y Supabase.\n\n"
        f"TAREA A RESOLVER (la pidió el desarrollador):\n\"{consulta}\"\n\n"
        f"ARCHIVOS CANDIDATOS (rutas relativas al repositorio):\n{lista}\n\n"
        f"Devuelve EXCLUSIVAMENTE un único objeto JSON válido, sin markdown y sin "
        f"texto adicional, con la clave \"archivos\" cuyo valor es un array con "
        f"EXACTAMENTE {max_archivos} rutas tomadas de la lista anterior, escritas "
        "en el MISMO formato exacto (sin \"./\" y sin modificarlas), ordenadas de "
        "más a menos relevantes para resolver la tarea.\n"
        "Prioriza los archivos que probablemente necesiten MODIFICARSE, no solo "
        "los que aportan contexto.\n"
        'Ejemplo de formato:\n{"archivos": ["lib/features/.../a.dart", '
        '"supabase/migrations/b.sql"]}'
    )


def parsear_json(texto) -> Optional[object]:
    """Convierte la respuesta del modelo en Python de forma tolerante: quita
    cercas de código ```json y busca el bloque JSON más grande de la respuesta."""
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
    rutas de la lista de disponibles y respetando el límite."""
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
      - librería google.generativeai no instalada  -> MENSAJE_GENAI_FALTANTE
      - variable GEMINI_API_KEY sin configurar      -> MENSAJE_API_KEY
      - errores de red/API de Google                -> RuntimeError descriptivo
    """
    if genai is None:
        raise RuntimeError(MENSAJE_GENAI_FALTANTE)

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(MENSAJE_API_KEY)

    modelo = modelo or PROVEEDORES["gemini"]["modelo_default"]
    info(f"Seleccionando con Gemini ({modelo})...")

    genai.configure(api_key=api_key)
    prompt = construir_prompt_seleccion(consulta, archivos, max_archivos)
    depurar(f"Prompt de selección: {len(prompt)} caracteres, {len(archivos)} candidatos")

    generador = genai.GenerativeModel(model_name=modelo)
    configuracion = genai.types.GenerationConfig(
        temperature=0.2,
        response_mime_type="application/json",  # pedimos JSON estructurado
    )

    try:
        respuesta = generador.generate_content(prompt, generation_config=configuracion)
    except Exception as exc:  # errores de red, cuota agotada, modelo inválido...
        raise RuntimeError(f"Error al llamar a Gemini: {exc}") from exc

    depurar(f"Respuesta de Gemini ({len(respuesta.text)} caracteres): {respuesta.text[:200]}")
    datos = parsear_json(respuesta.text)
    return normalizar_seleccion(datos, archivos, max_archivos)


def _resolver_url_openai(cfg: dict) -> str:
    """URL base para proveedores de tipo 'openai'.

    DeepSeek/Groq traen su base_url en la configuración. Ollama se conecta a
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
        f"No se encontró la variable de entorno {var} (necesaria para "
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
    if openai is None:
        raise RuntimeError(MENSAJE_OPENAI_FALTANTE)

    cfg = PROVEEDORES[proveedor]
    api_key = os.environ.get(cfg["clave_env"], "").strip()
    if cfg["requiere_clave"] and not api_key:
        raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))

    base_url = _resolver_url_openai(cfg)
    info(f"Seleccionando con {cfg['nombre']} ({modelo})...")
    depurar(f"{cfg['nombre']} → base_url={base_url}, modelo={modelo}")

    cliente = openai.OpenAI(
        api_key=api_key or "ollama-local",  # Ollama no exige clave; el SDK pide un valor
        base_url=base_url,
        timeout=120,
    )
    prompt = construir_prompt_seleccion(consulta, archivos, max_archivos)
    depurar(f"Prompt de selección: {len(prompt)} caracteres, {len(archivos)} candidatos")

    mensajes = [{"role": "user", "content": prompt}]
    try:
        try:
            respuesta = cliente.chat.completions.create(
                model=modelo, messages=mensajes,
                temperature=0.2, response_format={"type": "json_object"},
            )
        except Exception:
            # Algunos endpoints (p. ej. ciertas versiones de Ollama) no aceptan
            # response_format; reintentamos sin él (el prompt ya pide JSON).
            respuesta = cliente.chat.completions.create(
                model=modelo, messages=mensajes, temperature=0.2,
            )
    except Exception as exc:  # red, clave inválida, modelo inexistente...
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
      - librería `anthropic` no instalada        -> MENSAJE_ANTHROPIC_FALTANTE
      - variable ANTHROPIC_API_KEY sin configurar -> mensaje con la var exacta
      - errores de red/API de Anthropic           -> RuntimeError descriptivo
    """
    if anthropic is None:
        raise RuntimeError(MENSAJE_ANTHROPIC_FALTANTE)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "No se encontró la variable de entorno ANTHROPIC_API_KEY "
            "(necesaria para Claude, proveedor 'anthropic').\n"
            "  PowerShell:  $env:ANTHROPIC_API_KEY=\"tu_clave\"\n"
            "  Linux/Mac :  export ANTHROPIC_API_KEY=tu_clave"
        )

    modelo = modelo or PROVEEDORES["anthropic"]["modelo_default"]
    info(f"Seleccionando con Claude ({modelo})...")

    cliente = anthropic.Anthropic(api_key=api_key)
    prompt = construir_prompt_seleccion(consulta, archivos, max_archivos)
    depurar(f"Prompt de selección: {len(prompt)} caracteres, {len(archivos)} candidatos")

    try:
        respuesta = cliente.messages.create(
            model=modelo,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
    except Exception as exc:  # red, clave inválida, modelo inexistente...
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
    """Escribe el dict de estado en ESTADO_PATH. True si tuvo éxito."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ESTADO_PATH.write_text(
            json.dumps(datos, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        return True
    except OSError:
        return False


def _primer_uso_pendiente() -> bool:
    """True si la bienvenida aún no se ha mostrado (estado ausente o True)."""
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
    """Lee la configuración guardada en ~/.snapcontext/config.json.

    El archivo es un JSON con las claves 'provider' y, opcionalmente, 'model'.
    Si no existe o está corrupto, se devuelve un dict vacío.

    CORRECCIÓN 0.6.0: Manejo explícito de FileNotFoundError y json.JSONDecodeError
    para evitar silenciar errores importantes sin aviso.
    """
    try:
        if CONFIG_PATH.is_file():
            datos = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                return datos
        elif not CONFIG_PATH.exists():
            # Archivo no existe aún; devolver vacío sin error
            return {}
    except FileNotFoundError:
        aviso(f"Archivo de configuración no encontrado: {CONFIG_PATH}")
        pass  # Devolver {} si el directorio/config no existe aún
    except json.JSONDecodeError as exc:
        error(f"Configuración corrupta en {CONFIG_PATH}: {exc}")
        pass  # No intentar recuperar, devolver {} para evitar estado inconsistente
    except (OSError, ValueError) as exc:
        aviso(f"Error leyendo configuración: {type(exc).__name__}: {exc}")
        pass  # Opcional: continuar sin la configuración previa
    return {}


def guardar_configuracion(provider: str, model: Optional[str] = None,
                          api_keys: Optional[dict] = None) -> bool:
    """Guarda el proveedor preferido, modelo opcional y claves API.

    Recibe además `api_keys` (dict {proveedor: clave}) que se mezcla con las
    existentes, de modo que guardar solo el proveedor (como hace
    `_determinador_proveedor`) no borre las claves ya configuradas con --init.
    Devuelve True si se escribió correctamente en ~/.snapcontext/config.json.
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


def _importar_questionary():
    """Devuelve el módulo 'questionary' o None si no está instalado."""
    try:
        import questionary
        return questionary
    except ImportError:  # pragma: no cover
        return None


def _listar_modelos_ollama() -> tuple:
    """Devuelve (modelos, error) consultando los modelos locales vía `ollama list`.

    La primera columna de cada fila (la cabecera se ignora) es el nombre del
    modelo. Si `ollama` no está o falla, devuelve ([], mensaje de error).
    """
    try:
        proc = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError:
        return [], "No se encontró 'ollama' en el PATH. ¿Está instalado?"
    except subprocess.TimeoutExpired:
        return [], "El comando 'ollama list' tardó demasiado (60 s)."
    except OSError as exc:
        return [], f"No se pudo ejecutar 'ollama list': {exc}"

    if proc.returncode != 0:
        fallo = (proc.stderr or proc.stdout or "").strip()
        return [], fallo or "El comando 'ollama list' devolvió un error."

    modelos: List[str] = []
    for num_linea, linea in enumerate((proc.stdout or "").splitlines()):
        if num_linea == 0:          # cabecera: ID  NAME  SIZE  MODIFIED
            continue
        partes = linea.split()
        if partes:
            modelos.append(partes[0])
    return modelos, None


def seleccionar_proveedor_interactivo() -> tuple:
    """Menú interactivo (questionary) para elegir proveedor y, si es Ollama,
    su modelo local. Devuelve (provider, model).

    - Pregunta primero si se quiere elegir el proveedor ahora.
    - Si se elige Ollama, se auto-detectan los modelos con `ollama list`.
      Sin modelos / sin ollama instalado, se avisa y se ofrece volver al menú
      de proveedores o usar Gemini por defecto.
    - Sin questionary se avisa y se usa PROVEEDOR_DEFECTO (gemini), model None.
    """
    questionary = _importar_questionary()
    if questionary is None:
        _emitir(
            sys.stdout,
            "💡 Para usar el modo interactivo, instala: pip install questionary",
        )
        return (PROVEEDOR_DEFECTO, None)

    if not questionary.confirm("¿Deseas seleccionar el proveedor de IA ahora?").ask():
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
            "🤖 Selecciona el proveedor de IA:",
            choices=opciones,
        ).ask() or PROVEEDOR_DEFECTO

        # Ollama → auto-detección de modelos locales (Mejora 2).
        if proveedor == "ollama":
            modelos, error = _listar_modelos_ollama()
            if modelos:
                elegido = questionary.select(
                    "🤖 Selecciona el modelo de Ollama:",
                    choices=list(modelos),
                ).ask()
                return ("ollama", elegido or modelos[0])

            if error:
                aviso(f"No se pudieron listar modelos de Ollama: {error}")
            else:
                aviso("Ollama no tiene modelos instalados. "
                      "Prueba: ollama pull llama3.2")
            usar_gemini = questionary.confirm(
                "¿Quieres usar Gemini por defecto? (No = volver al proveedor)"
            ).ask()
            if usar_gemini:
                return ("gemini", None)
            # Si responde "no": vuelve al menú de proveedores.
            continue

        return (proveedor, None)


def _preguntar_guardar_config() -> bool:
    """Pregunta si guardar el proveedor elegido como predeterminado.

    Solo hace la pregunta si questionary está instalada; si no, devuelve False
    y no se persiste nada (comportamiento elegante sin dependencia extra).
    """
    questionary = _importar_questionary()
    if questionary is None:
        return False
    return bool(
        questionary.confirm(
            "¿Guardar este proveedor como predeterminado?"
        ).ask()
    )


def _probar_conexion_proveedor(provider: str, model: Optional[str] = None) -> bool:
    """Comprueba la conexión con la API del proveedor elegido (usado por --init).

    Reutiliza la clave guardada en la configuración o, como plan B, la variable
    de entorno correspondiente. Hace una llamada mínima y devuelve True si ok.
    """
    cfg = PROVEEDORES[provider]
    api_keys = cargar_configuracion().get("api_keys") or {}

    if provider == "gemini":
        if genai is None:
            aviso("Falta google-generativeai. Instala: pip install google-generativeai")
            return False
        clave = (api_keys.get("gemini") or "").strip() \
            or os.environ.get("GEMINI_API_KEY", "").strip()
        if not clave:
            aviso("No se encontró ninguna clave de Gemini.")
            return False
        try:
            genai.configure(api_key=clave)
            genai.GenerativeModel(model or cfg["modelo_default"]).generate_content("responde ok")
            return True
        except Exception:
            return False

    # Claude (Anthropic): SDK oficial, distinto de la API estilo OpenAI.
    if provider == "anthropic":
        if anthropic is None:
            aviso(MENSAJE_ANTHROPIC_FALTANTE)
            return False
        clave = (api_keys.get("anthropic") or "").strip() \
            or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not clave:
            aviso("No se encontró ninguna clave de Anthropic.")
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
    if openai is None:
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
    """Asistente interactivo de configuración inicial (SNAPCONTEXT --init).

    Guía en la configuración de claves API y el proveedor/modelo favorito en
    ~/.snapcontext/config.json. Devuelve el código de salida (0 = éxito).
    """
    questionary = _importar_questionary()
    if questionary is None:
        aviso(
            "El asistente requiere questionary. "
            "Instálalo con: pip install questionary"
            "  (o: pip install snapcontext[interactive])"
        )
        return 1

    if CONFIG_PATH.exists() and not questionary.confirm(
        "¿Ya existe una configuración. ¿Quieres sobrescribirla?"
    ).ask():
        aviso("Configuración no modificada.")
        return 0

    exito("Configuración inicial de SnapContext")
    api_keys: dict = dict(cargar_configuracion().get("api_keys") or {})

    clave = questionary.password(
        "Clave de API de Gemini (GEMINI_API_KEY):",
        default=api_keys.get("gemini", ""),
    ).ask()
    if clave and clave.strip():
        api_keys["gemini"] = clave.strip()

    if questionary.confirm(
        "¿Quieres configurar otros proveedores (Groq, DeepSeek)?"
    ).ask():
        for prov in ("groq", "deepseek"):
            env = PROVEEDORES[prov]["clave_env"]
            # CORRECCIÓN 0.6.0: Usar questionary.password() en lugar de text(password=True)
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
        error(f"No se pudo escribir la configuración en {CONFIG_PATH}")
        return 1
    exito(f"Configuración guardada en {CONFIG_PATH}")

    if questionary.confirm("¿Quieres probar la conexión con la API ahora?").ask():
        if _probar_conexion_proveedor(proveedor, modelo):
            exito("¡Conexión con la API verificada correctamente!")
        else:
            error("No se pudo conectar con la API. Revisa la clave.")
            return 1

    # ── v3.1.0: Ollama, proyecto de prueba y tutorial ─────────────────────
    if questionary.confirm(
        "¿Quieres configurar Ollama (modo offline, sin API key)?"
    ).ask():
        estado_ol = _estado_ollama()
        if estado_ol["modelos"]:
            ligero = _elegir_modelo_ligero(estado_ol["modelos"])
            exito(f"Ollama ya está listo (modelo más ligero: '{ligero}').")
            if questionary.confirm(
                "¿Usar Ollama como proveedor por defecto?"
            ).ask():
                guardar_configuracion("ollama", ligero, api_keys)
                proveedor, modelo = "ollama", ligero
                exito(f"Proveedor guardado: ollama / {ligero}.")
        else:
            aviso("Ollama no está instalado o no tiene modelos descargados.")
            info("Descárgalo desde https://ollama.com y después ejecuta:")
            info("  ollama pull llama3.2")
            try:
                import webbrowser
                if questionary.confirm(
                    "¿Abrir https://ollama.com en el navegador?"
                ).ask():
                    webbrowser.open("https://ollama.com")
            except Exception:
                pass

    if questionary.confirm(
        "¿Quieres crear un proyecto de prueba para empezar?"
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
                info("Pruébalo con:")
                info(f'  cd "{ruta}" && snapcontext '
                     '"describe este proyecto" --vista-previa --local')
            except OSError as exc:
                error(f"No se pudo crear el proyecto: {exc}")

    if questionary.confirm(
        "¿Quieres ejecutar el tutorial interactivo ahora (--bienvenida)?"
    ).ask():
        return _tutorial_interactivo()
    return 0


# ---------------------------------------------------------------------------
# Modo offline por defecto (v3.1.0): sin API key → Ollama automáticamente
# ---------------------------------------------------------------------------
def hay_api_key_configurada() -> bool:
    """True si hay alguna clave de API en el entorno o en la configuración.

    Comprueba las variables GEMINI_API_KEY / ANTHROPIC_API_KEY /
    DEEPSEEK_API_KEY / GROQ_API_KEY / OPENAI_API_KEY y, además, las claves
    guardadas en ~/.snapcontext/config.json (sección 'api_keys').
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
    """Elige el modelo más ligero disponible según MODELOS_LIGEROS_OLLAMA.

    Devuelve None si la lista está vacía.
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
    """Resuelve proveedor y modelo con persistencia en la configuración.

    Devuelve un dict con las claves 'provider' y 'model'. Prioridad:
      1) --provider por CLI (y se guarda, salvo --no-persist).
      2) Configuración guardada en ~/.snapcontext/config.json.
      3) Env SNAPCONTEXT_PROVIDER (si no hay configuración guardada).
      4) Primer uso → menú interactivo y preguntar si se guarda.
    """
    persistir = not getattr(args, "no_persist", False)
    proveedor_cli = getattr(args, "provider", None)
    modelo_cli = getattr(args, "modelo", None) or MODELO_DEFECTO

    # 1) Proveedor explícito en CLI: máxima prioridad; además se recuerda.
    if proveedor_cli:
        if persistir:
            guardar_configuracion(proveedor_cli, modelo_cli)
        return {"provider": proveedor_cli, "model": modelo_cli}

    # 2) Preferencia guardada (primer uso → todavía no existe el archivo).
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

    # 4) Primer uso sin configuración. v3.1.0: si no hay ninguna API key,
    #    se intenta Ollama automáticamente antes del menú interactivo.
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
    proveedor (o el de SNAPCONTEXT_MODELO si está definida)."""
    if proveedor not in PROVEEDORES:
        raise RuntimeError(
            f"Proveedor desconocido '{proveedor}'. "
            f"Válidos: {', '.join(sorted(PROVEEDORES))}"
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
# Ejecución de Aider y bucle de pruebas
# ---------------------------------------------------------------------------
def ejecutar_aider(archivos: List[str], consulta: str, directorio: str,
                   opciones_aider: str = "") -> bool:
    """Ejecuta Aider en `directorio` con los `archivos` añadidos y la consulta
    como mensaje. `--yes` evita confirmaciones manuales (auto-commit de git).

    El resto de la configuración de Aider (modelo, API key, etc.) se toma de
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
        exito("Aider terminó correctamente.")
        return True
    aviso(f"Aider terminó con código {resultado.returncode}.")
    return False


def _editor_sobrescribir(archivo: str, contenido: str,
                          directorio: str = ".") -> bool:
    """Editor propio (Fase 1 — Sobrescritura de archivos).

    Escribe `contenido` en `archivo` dentro de `directorio`.
    - Valida que la ruta esté dentro del repositorio.
    - Crea copia de seguridad en ~/.snapcontext/backups/ antes de sobrescribir.
    - Crea carpetas intermedias si no existen.
    - Devuelve True si tuvo éxito, False en caso de error.
    """
    if not archivo or not str(archivo).strip():
        error("La ruta del archivo no puede estar vacía.")
        return False

    raw = str(archivo).replace("\\", "/").strip()
    partes_raw = raw.split("/")
    if ".." in partes_raw or raw.startswith("/"):
        error(f"Acceso denegado: el archivo '{archivo}' contiene referencias a directorios padre o raíz.")
        return False

    raiz_res = Path(directorio).resolve()
    limpia = _normalizar_relativa(raw)
    if not limpia:
        error(f"Ruta no válida: {archivo}")
        return False

    destino = (raiz_res / limpia).resolve()
    try:
        destino.relative_to(raiz_res)
    except ValueError:
        error(f"Acceso denegado: el archivo '{archivo}' está fuera del repositorio.")
        return False

    # Si el archivo ya existe, guardar backup
    if destino.exists() and destino.is_file():
        try:
            BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            nombre_backup = f"{ts}_{destino.name}"
            backup_path = BACKUPS_DIR / nombre_backup
            shutil.copy2(destino, backup_path)
            depurar(f"[EditorPropio] Backup guardado en {backup_path}")
        except Exception as exc:
            aviso(f"[EditorPropio] No se pudo crear backup de {limpia}: {exc}")

    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8")
        exito(f"[EditorPropio] Archivo actualizado: {limpia}")
        return True
    except Exception as exc:
        error(f"[EditorPropio] Error al escribir {limpia}: {exc}")
        return False


def _generar_parche(original: str, nuevo: str, ruta_archivo: str) -> str:
    """Genera un parche unificado (unified diff) entre `original` y `nuevo`.

    El encabezado cumple con el estándar de `patch` y `git apply` (a/ruta b/ruta).
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
    3. Si `git` falla o no está disponible, intenta con `patch -p1 -i <temp_file>`.
    4. Devuelve True si se aplicó limpiamente, False si hubo error o conflicto.
    """
    if not parche or not parche.strip():
        aviso("[EditorPropio] Parche vacío; no se aplicaron cambios.")
        return False

    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", encoding="utf-8", delete=False) as f:
        f.write(parche)
        temp_path = f.name

    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        raiz_res = str(Path(directorio).resolve())

        # Intentar primero con git apply (muy estándar en repos de desarrollo)
        if shutil.which("git"):
            cmd_git = ["git", "apply", "--whitespace=nowarn", temp_path]
            res_git = subprocess.run(
                cmd_git, cwd=raiz_res, capture_output=True, text=True, creationflags=flags
            )
            if res_git.returncode == 0:
                exito("[EditorPropio] Parche unificado aplicado correctamente con git apply.")
                return True
            depurar(f"[EditorPropio] git apply falló (código {res_git.returncode}): {res_git.stderr}")

        # Fallback a patch
        if shutil.which("patch"):
            cmd_patch = ["patch", "-p1", "-i", temp_path]
            res_patch = subprocess.run(
                cmd_patch, cwd=raiz_res, capture_output=True, text=True, creationflags=flags
            )
            if res_patch.returncode == 0:
                exito("[EditorPropio] Parche unificado aplicado correctamente con patch.")
                return True
            depurar(f"[EditorPropio] patch falló (código {res_patch.returncode}): {res_patch.stderr}")

        aviso("[EditorPropio] No se pudo aplicar el parche automáticamente (ni git apply ni patch tuvieron éxito).")
        return False
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Manejo de conflictos y aplicación incremental (v3.3.0)
# ---------------------------------------------------------------------------
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
    archivo difiere del que se pasó al proveedor, aplicar a ciegas corrompería
    la edición. Devuelve ``(ok, detalle)``.
    """
    if contenido_esperado is None:
        return True, "sin validación (no hay contenido de referencia)"
    ruta = _ruta_del_parche(parche)
    if not ruta:
        return True, "parche sin encabezado reconocible; se omite la validación"
    destino = Path(directorio or ".").resolve() / ruta
    if not destino.is_file():
        return False, f"el archivo '{ruta}' ya no existe"
    try:
        actual = destino.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"no se pudo leer '{ruta}': {exc}"
    if actual != contenido_esperado:
        return False, (f"'{ruta}' cambió desde que se generó el parche "
                       "(posible cambio concurrente)")
    return True, "el archivo coincide con la referencia"


def _parsear_hunks(parche: str) -> List[tuple]:
    """Divide un diff unificado en hunks ``(linea_inicio_original, cambios)``.

    ``cambios`` es una lista de ``(marca, texto)`` con marca ' ', '-' o '+'.
    Se omiten los hunks sin líneas modificadas. Devuelve [] si no hay ninguno.
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


def _aplicar_hunks_incremental(parche: str, directorio: str) -> bool:
    """Resolución automática de conflictos: aplica el parche línea a línea.

    Estrategia puramente Python (sin git/patch): para cada hunk busca el
    bloque original con tolerancia a desfases (posiciones cercanas + búsqueda
    difusa por contexto con difflib) y aplica solo las líneas modificadas.
    Los hunks irresolubles se omiten con aviso; se escribe el resultado si al
    menos un hunk se aplicó, siempre con copia de seguridad previa.

    Devuelve True si se aplicó algún cambio.
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
        """True si ``resultado[desde:]`` encaja con las líneas no-'+'."""
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

    aplicados = 0
    desplazamiento = 0                     # acumulado por hunks previos
    for inicio_orig, cambios in hunks:
        base = max(inicio_orig - 1 + desplazamiento, 0)
        n_borrados = _n_borrar(cambios)

        posicion = -1
        # 1) Coincidencia exacta cerca de la posición declarada.
        offsets = sorted({0, 1, -1, 2, -2, 3, -3, 5, -5, 10, -10, 20, -20})
        for delta in offsets:
            candidato = base + delta
            if 0 <= candidato <= len(resultado) and \
                    _coincide(candidato, cambios):
                posicion = candidato
                break
        # 2) Búsqueda difusa por líneas de contexto con difflib.
        if posicion < 0:
            contexto_idx = [i for i, (m, _) in enumerate(cambios)
                            if m == " " and _.strip()]
            if contexto_idx:
                primera_ctx = cambios[contexto_idx[0]][1].strip()
                for idx_linea, linea in enumerate(resultado):
                    if linea.strip() == primera_ctx:
                        candidato = idx_linea - contexto_idx[0]
                        if _coincide(max(candidato, 0), cambios):
                            posicion = max(candidato, 0)
                            break
        if posicion < 0 or not _coincide(posicion, cambios):
            aviso(f"[EditorPropio] Hunk en línea {inicio_orig} no aplicable; "
                  "se omite (resolución automática).")
            continue

        nuevo_bloque = [texto for marca, texto in cambios if marca != "-"]
        resultado[posicion:posicion + n_borrados] = nuevo_bloque
        desplazamiento += len(nuevo_bloque) - n_borrados
        aplicados += 1

    if not aplicados:
        aviso("[EditorPropio] Ningún hunk pudo aplicarse; archivo intacto.")
        return False
    if aplicados < len(hunks):
        aviso(f"[EditorPropio] Aplicación parcial: {aplicados}/{len(hunks)} "
              "hunks. Revisa el resultado antes de confirmar.")

    exito(f"[EditorPropio] Parche aplicado con resolución incremental "
          f"(línea a línea) sobre '{ruta}'.")
    return _editor_sobrescribir(ruta, "\n".join(resultado) + "\n",
                                directorio=directorio)


def _aplicar_parche_con_resolucion(parche: str, directorio: str = ".",
                                   contenido_esperado: Optional[str] = None
                                   ) -> bool:
    """Aplica un parche con validación previa y resolución de conflictos.

    Flujo (v3.3.0):
      1. Validación previa: si se pasa ``contenido_esperado`` (el contenido
         usado para generar el parche), comprueba que el archivo actual
         coincida para evitar conflictos concurrentes.
      2. Intento estándar: ``git apply`` → ``patch -p1``.
      3. Resolución automática: aplicación incremental línea a línea.
      4. Si todo falla, avisa para resolución manual (ya no sobrescribe a
         ciegas).
    """
    ok_validacion, detalle = _validar_parche_previo(
        parche, directorio, contenido_esperado)
    if not ok_validacion:
        aviso(f"[EditorPropio] Validación previa fallida: {detalle}. "
              "Se intentará la resolución automática.")
    elif contenido_esperado is not None:
        depurar(f"[EditorPropio] Validación previa OK: {detalle}")

    if _aplicar_parche(parche, directorio=directorio):
        return True
    info("[EditorPropio] Conflicto detectado; probando resolución "
         "incremental (línea a línea)...")
    return _aplicar_hunks_incremental(parche, directorio)



# ---------------------------------------------------------------------------
# Editor propio (Fase 3 — Edición basada en AST)  — v2.2.0
# ---------------------------------------------------------------------------
def _es_extension_python(ruta: str) -> bool:
    """True si ``ruta`` parece un archivo de Python editable con ``ast``."""
    return str(ruta).lower().endswith((".py", ".pyx", ".pxd"))


def _ast_disponible(ruta: str) -> bool:
    """True si se puede generar un AST para ``ruta`` (Python o tree-sitter)."""
    if not ruta or not str(ruta).strip():
        return False
    if _es_extension_python(ruta):
        return True
    lenguaje = _lenguaje_tree_sitter(str(ruta))
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
        resumen["error"] = f"sintaxis inválida: {exc}"
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
    """Genera un resumen del AST de ``ruta`` para pasárselo al proveedor de IA.

    - Python: usa el módulo ``ast`` de la stdlib.
    - Otros lenguajes: usa ``tree_sitter`` si está instalado.

    Devuelve un dict con ``ok``, ``motor``, ``lenguaje`` y una proyección simple
    (funciones/clases/imports/variables/llamadas). Nunca lanza excepciones.
    """
    lenguaje = _lenguaje_archivo(ruta, contenido) or ""
    if _es_extension_python(ruta) or lenguaje == "python":
        return _resumen_ast_python(contenido)
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
        except Exception as exc:                     # gramática ausente, API distinta…
            return {"ok": False, "motor": "tree-sitter", "lenguaje": lenguaje,
                    "error": f"tree-sitter falló para {lenguaje}: {exc}"}
    return {"ok": False, "motor": None, "lenguaje": lenguaje,
            "error": "sin analizador AST disponible para este lenguaje "
                     "(usa .py o instala tree-sitter)"}

def _formatear_resumen_ast(resumen: dict, ruta: str) -> str:
    """Devuelve una representación textual compacta del resumen para el prompt."""
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
                sufijo = f" (línea {linea_n})" if linea_n else ""
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

    Prefiere una lista JSON de operaciones; si no es JSON válido, la trata como
    el código completo resultante (envuelto en una operación ``completo``).
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
    """Convierte (fila 1-based, col 0-based) a índice de caracteres del código."""
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
    """Renombra un identificador (variable, función, clase, parámetro) en ``contenido``.

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
    """Inserta ``importacion`` tras los imports de la cabecera si aún no existe."""
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
    """Aplica una lista de operaciones AST al código.

    Soporta al menos:
      - ``{"tipo": "completo", "codigo": "..."}`` → reemplaza todo el archivo.
      - ``{"tipo": "renombrar", "nombre": "x", "nuevo": "y"}`` → renombra símbolo.
      - ``{"tipo": "insertar_import", "codigo": "import os"}`` → añade import.

    Devuelve el código resultante o ``None`` si no hubo cambio aplicable.
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
                modelo: Optional[str] = None) -> bool:
    """Editor propio (Fase 3 — Edición basada en AST).

    1) Lee el contenido del archivo.
    2) Genera el AST (Python con ``ast``; otros lenguajes con ``tree-sitter``).
    3) Pasa un resumen del AST + la tarea al proveedor de IA.
    4) El proveedor devuelve un *parche AST* (instrucciones de modificación del
       árbol) o el código nuevo completo.
    5) Aplica los cambios y guarda el archivo modificado (con copia de seguridad).

    Devuelve ``True`` si tuvo éxito; ``False`` si no se pudo editar (para que el
    agente haga fallback a parche o sobrescritura).
    """
    if not archivo or not str(archivo).strip():
        error("La ruta del archivo no puede estar vacía.")
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
    num_lineas = contenido.count("\n") + 1
    prompt = (
        f"Vas a modificar un archivo comprendiendo su estructura sintáctica "
        f"(AST). Objetivo: precisión máxima y cambios mínimos.\n\n"
        f"Tarea: {tarea}\n"
        f"Archivo: {ruta_posix}  (lenguaje: {lenguaje}, {num_lineas} líneas)\n\n"
        f"Resumen del AST (símbolos disponibles y sus posiciones):\n"
        f"{_formatear_resumen_ast(resumen, ruta_posix)}\n\n"
        f"Contenido actual completo:\n```\n{contenido}\n```\n\n"
        f"Reglas de edición:\n"
        f"- Conserva el estilo existente (indentación, comillas, convenciones).\n"
        f"- Modifica SOLO lo necesario para la tarea; no reorganices el resto.\n"
        f"- Usa los símbolos del resumen del AST para anclar tus cambios.\n\n"
        f"Responde ÚNICAMENTE con una lista JSON de operaciones de edición, por ejemplo:\n"
        f'[{{"tipo": "renombrar", "nombre": "viejo", "nuevo": "nuevo"}}]\n'
        f'O, si prefieres devolver el código completo resultante:\n'
        f'[{{"tipo": "completo", "codigo": "def fn(): ...\\n..."}}]\n'
        f"Sin explicaciones ni markdown fuera del JSON."
    )
    try:
        respuesta = _enviar_al_proveedor(
            proveedor, modelo, [{"role": "user", "content": prompt}])
    except Exception as exc:
        error(f"[EditorAST] Error generando cambios para {ruta_posix}: {exc}")
        return False

    opos = _interpretar_operaciones_ast(respuesta)
    if not opos:
        depurar(f"[EditorAST] El proveedor no devolvió operaciones AST para '{ruta_posix}'.")
        return False
    nuevo_contenido = _aplicar_operaciones_ast(contenido, opos)
    if not nuevo_contenido or nuevo_contenido == contenido:
        aviso(f"[EditorAST] No hubo cambio neto aplicable en '{ruta_posix}'.")
        return False
    exito(f"[EditorAST] Edición AST aplicada sobre {ruta_posix} (método AST).")
    return _editor_sobrescribir(ruta_posix, nuevo_contenido, directorio=directorio)


def _extraer_error(resultado: "subprocess.CompletedProcess") -> str:
    """Une stdout+stderr, limpia códigos ANSI y limita el tamaño del error
    que se mostrará a Aider (evita llenar el contexto)."""
    salida = (resultado.stdout or "") + "\n" + (resultado.stderr or "")
    salida = re.sub(r"\x1b\[[0-9;]*m", "", salida)  # quitar colores ANSI
    salida = salida.strip() or "(el comando de prueba no devolvió salida)"
    if len(salida) > MAX_ERROR_SALIDA:
        salida = "\n... (salida recortada) ...\n" + salida[-MAX_ERROR_SALIDA:]
    return salida


def ejecutar_bucle_test(consulta: str, archivos: List[str], directorio: str,
                        opciones_aider: str, comando_test: List[str],
                        max_iteraciones: int = MAX_ITERACIONES_TEST_DEFECTO) -> bool:
    """Bucle agéntico básico: Aider → pruebas → si fallan, Aider las arregla.

    Este es el punto natural de extensión: aquí puedes añadir más herramientas
    al bucle (p. ej. linters, analysizer de Flutter, generación de tests...).
    """
    if not comando_test:
        raise RuntimeError("El comando de pruebas está vacío (--comando-test).")
    if shutil.which(comando_test[0]) is None:
        raise RuntimeError(
            f"No se encontró el comando de pruebas '{comando_test[0]}'. "
            "Ajusta --comando-test."
        )

    ultimo_error = ""
    for iteracion in range(1, max_iteraciones + 1):
        info(f"Iteración {iteracion} de {max_iteraciones} — Aider...")
        if iteracion == 1 or not ultimo_error:
            mensaje = consulta
        else:
            # Devolvemos el error real de la iteración anterior para que Aider
            # repare el código sin perder de vista la tarea original.
            mensaje = (
                f"La tarea original era:\n{consulta}\n\n"
                f"El comando de prueba falló en la iteración {iteracion - 1} con:\n"
                f"```\n{ultimo_error}\n```\n"
                "Corrige esos errores sin cambiar el alcance de la tarea original."
            )
        ejecutar_aider(archivos, mensaje, directorio, opciones_aider)

        solicitud = " ".join(comando_test)
        info(f"Ejecutando pruebas: {solicitud}")
        resultado = subprocess.run(
            comando_test, cwd=directorio, capture_output=True, text=True
        )
        if resultado.returncode == 0:
            exito(f"¡Pruebas superadas en la iteración {iteracion}!")
            return True
        ultimo_error = _extraer_error(resultado)
        aviso(
            f"Pruebas fallidas (código {resultado.returncode}). "
            "Se envía el error a Aider para que lo corrija..."
        )

    error(f"No se consiguió que las pruebas pasaran tras {max_iteraciones} iteraciones.")
    return False


# ---------------------------------------------------------------------------
# Bucle agéntico con servidor Flutter (--server-loop / --manual-loop)
# ---------------------------------------------------------------------------
# Patrones que suelen anunciar que Flutter terminó de compilar y sirve.
_PATRONES_SERVIDOR = re.compile(
    r"Running on|Synced|is being served at|served at|available at|VM Service"
)
_RE_URL = re.compile(r"https?://[^\s\"'()<>]+")


def lanzar_servidor(directorio: str = ".",
                    dispositivo: str = "web-server",
                    puerto: int = 5000) -> subprocess.Popen:
    """Lanza `flutter run` en segundo plano y devuelve el subproceso.

    Se fusiona stderr en stdout (así es más fácil analizar la salida y, para
    dispositivos web, se fija el puerto para que coincida con --url-defecto).
    """
    if shutil.which("flutter") is None:
        raise RuntimeError(
            "No se encontró 'flutter' en el PATH. Instala Flutter o revisa tu "
            "configuración (https://flutter.dev)."
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
        bufsize=1,                  # lectura por líneas
        creationflags=flags,
    )
    _PROCESOS_ACTIVOS.add(proceso)  # para poder cerrarlo desde la señal SIGINT/SIGTERM
    return proceso


def _iniciar_lector_salida(proceso: subprocess.Popen) -> List[str]:
    """Hilo lector que acumula la salida de `proceso` en una lista.

    Esto permite a `esperar_servidor` revisar la salida sin bloquearse en un
    readline() (truco cross-platform, sin depender de select/fcntl) y deja la
    salida disponible también para `obtener_error`.
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
    """Espera a que el servidor Flutter esté listo leyendo su salida en vivo.

    Busca patrones típicos de arranque ("Running on", "Synced", "served at",
    "available at", "VM Service") y, si aparece, extrae la URL real.

    Devuelve:
      - URL real o `url_defecto` si el servidor está en marcha.
      - None si el proceso murió sin arrancar (la salida queda disponible
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
            break  # el proceso terminó antes de arrancar el servidor
        time.sleep(0.3)

    # Se agotó el tiempo sin patrón conocido: si sigue vivo, asumimos que sirve.
    if proceso.poll() is None:
        aviso("No se detectó el patrón de arranque esperado; se asume que el "
              "servidor responde en " + url_defecto)
        return url_defecto
    return None


def _detener_servidor(proceso) -> None:
    """Termina el proceso del servidor (terminate → kill) sin dejar huérfanos."""
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
        # Si quedaron líneas sin leer las drenamos (el proceso ya está parado).
        if proceso.stdout:
            lineas.extend(proceso.stdout.read().splitlines())
    except (AttributeError, ValueError, OSError):
        pass

    texto = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lineas))  # quitar ANSI
    texto = texto.strip() or "(el servidor no devolvió salida)"
    if len(texto) > max_caracteres:
        # Nos quedamos con el final: ahí suele estar el error real.
        texto = "\n... (salida recortada) ...\n" + texto[-max_caracteres:]
    return texto


def _recortar(texto: str, longitud: int) -> str:
    texto = texto.strip()
    if len(texto) > longitud:
        return texto[:longitud] + "\n... (salida recortada) ..."
    return texto


def abrir_navegador(url: str) -> bool:
    """Abre la URL en el navegador del sistema (multiplataforma).

    Intenta primero `webbrowser.open` (portátil). Si no lo consigue (devuelve
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

    aviso("No se pudo abrir el navegador automáticamente. Abre "
          + url + " manualmente.")
    return False


def _preguntar_si(pregunta: str) -> bool:
    """Pregunta s/n (acepta si/sí) hasta obtener una respuesta válida."""
    while True:
        try:
            respuesta = input(_pintar(pregunta, _CYAN)).strip().lower()
        except EOFError:  # entrada no interactiva → se asume 'n'
            return False
        if respuesta in ("s", "si", "sí"):
            return True
        if respuesta in ("n", "no"):
            return False
        aviso("Responde 's' o 'n'.")


def _pedir_detalle_error(error_servidor: str) -> str:
    """Pide al usuario (modo manual) que describa el error para dárselo a Aider."""
    aviso("El usuario reportará el problema y Aider lo corregirá.")
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
    """Bucle agéntico con servidor Flutter (flutter run en segundo plano).

    --server-loop (modo="auto"):
        Reintenta hasta `max_intentos`. Si el servidor arranca, pregunta si se
        quiere probar la app (abre el navegador con la URL real y espera Enter).
        Si se agotan los intentos, ofrece cambiar a modo manual.
    --manual-loop (modo="manual"):
        Tras cada intento pregunta siempre "¿La app funciona correctamente?",
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
            info(f"Intento {intento} — Aider...")
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
                exito(f"El servidor arrancó en {url}.")
                if modo == "auto":
                    # --- modo automático --------------------------------------
                    if _preguntar_si(
                        "✅ El servidor arrancó correctamente. "
                        "¿Quieres probar la app manualmente? (s/n): "
                    ):
                        abrir_navegador(url)
                        try:
                            input(_pintar("Pulsa Enter cuando hayas terminado de "
                                          "probar la app...", _CYAN))
                        except EOFError:
                            pass
                    _detener_servidor(proceso_actual)
                    proceso_actual = None
                    exito("Bucle agéntico completado (el servidor respondió).")
                    return True

                # --- modo manual: el usuario decide ---------------------------
                if _preguntar_si("¿La app funciona correctamente? (s/n): "):
                    _detener_servidor(proceso_actual)
                    proceso_actual = None
                    exito("¡Bucle agéntico completado!")
                    return True
                _detener_servidor(proceso_actual)
                proceso_actual = None
                error_ultimo = _pedir_detalle_error(error_ultimo)
                continue

            # ---- el servidor no arrancó --------------------------------------
            salida_error = obtener_error(proceso_actual)
            error_ultimo = f"Arregla este error: {salida_error}"
            proceso_actual = None
            error(f"El servidor no arrancó en el intento {intento}.")
            _emitir(sys.stdout, _pintar("Salida del servidor:", _GRIS))
            _emitir(sys.stdout, _recortar(salida_error, 500))

            if modo == "manual":
                if _preguntar_si("¿La app funciona correctamente? (s/n): "):
                    exito("¡Bucle agéntico completado!")
                    return True
                error_ultimo = _pedir_detalle_error(error_ultimo)
                continue

            # ---- modo automático: decidir reintentar o cambiar a manual -------
            if intento < max_intentos:
                aviso(
                    f"Intento {intento}/{max_intentos} fallido. Aider corregirá "
                    "y se reintentará automáticamente..."
                )
                continue
            if _preguntar_si(
                f"❌ El bucle automático falló después de {max_intentos} intentos. "
                "¿Quieres cambiar a modo manual? (s/n): "
            ):
                info("Cambiando a modo manual...")
                return ejecutar_bucle_agente(
                    consulta + "\n\n" + error_ultimo,
                    archivos, modo="manual", max_intentos=max_intentos,
                    directorio=directorio, opciones_aider=opciones_aider,
                    dispositivo=dispositivo, url_defecto=url_defecto,
                )
            error("Finalizado: el usuario decidió no continuar en modo manual.")
            return False
    finally:
        # Garantiza que, aunque haya excepción o Ctrl+C, no queden servidores sueltos.
        _detener_servidor(proceso_actual)


# ---------------------------------------------------------------------------
# Modo experto (--experto): revisar/editar la selección antes de Aider
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
        ruta = input(_pintar("Ruta del archivo a añadir (relativa al repo): ",
                             _CYAN)).strip()
    except EOFError:
        return None

    normalizada = _normalizar_ruta_manual(raiz, ruta)
    if not normalizada:
        aviso("Ruta no válida.")
        return None

    # Comprobación de que el archivo existe y NO sale del repo (evita "..").
    candidata = (raiz / normalizada).resolve()
    try:
        candidata.relative_to(raiz.resolve())
    except ValueError:
        aviso(f"'{normalizada}' está fuera del repositorio.")
        return None
    if not candidata.is_file():
        aviso(f"No existe el archivo: {normalizada}")
        return None
    if normalizada in seleccion:
        aviso(f"'{normalizada}' ya está en la lista.")
        return None
    return normalizada


def _eliminar_por_indice(seleccion: List[str]) -> List[str]:
    """Pide un índice y elimina ese archivo (valida que esté en rango)."""
    try:
        entrada = input(_pintar("Índice a eliminar: ", _CYAN)).strip()
        indice = int(entrada)
    except (ValueError, EOFError):
        aviso("Índice no válido.")
        return seleccion
    if not (1 <= indice <= len(seleccion)):
        aviso(f"Índice fuera de rango (la lista tiene {len(seleccion)} archivo(s)).")
        return seleccion
    eliminado = seleccion.pop(indice - 1)
    exito(f"Eliminado: {eliminado}")
    return seleccion


def modo_experto(seleccion: List[str], raiz: Path) -> List[str]:
    """Modo experto: revisar/añadir/eliminar/limpiar archivos de la selección.

    Opciones del menú:
      [a]gregar   → pide una ruta (debe existir y estar dentro del repo).
      [e]liminar  → pide un índice (fuera de rango se rechaza).
      [l]impiar   → vacía la lista (con confirmación).
      [c]ontinuar → devuelve la lista final que usará Aider.

    Devuelve la lista final (rutas POSIX relativas al repositorio).
    """
    while True:
        _emitir(sys.stdout, _pintar("── Modo experto ─────────────────────────", _CYAN))
        if not seleccion:
            aviso("La lista de archivos está vacía.")
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
                aviso("No se puede continuar con la lista vacía: añade archivos "
                      "con [a] o sal con Ctrl+C.")
                continue
            return seleccion

        if opcion in ("a", "agregar", "add"):
            ruta = _pedir_archivo_para_agregar(raiz, seleccion)
            if ruta:
                seleccion.append(ruta)
                exito(f"Añadido: {ruta}")
            continue

        if opcion in ("e", "eliminar", "remove"):
            seleccion = _eliminar_por_indice(seleccion)
            continue

        if opcion in ("l", "limpiar", "clear"):
            if _preguntar_si("¿Vaciar toda la lista? (s/n): "):
                seleccion = []
                aviso("Lista vaciada.")
            continue

        aviso(f"Opción no válida: '{opcion}'. Usa a, e, l o c.")

# ---------------------------------------------------------------------------
# Interfaz CLI
# ---------------------------------------------------------------------------
class _VersionAction(argparse.Action):
    """Acción personalizada para --version: muestra el logo grande y sale."""
    def __init__(self, option_strings, dest, nargs=0, **kwargs):
        super().__init__(option_strings, dest, nargs=nargs, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(_LOGO)
        print(f"SnapContext v{VERSION}")
        print("Open-source · MIT · https://github.com/TU_USUARIO/snapcontext")
        parser.exit()


# ---------------------------------------------------------------------------
# Memoria persistente (~/.snapcontext/historial.json) — v0.10.0
# ---------------------------------------------------------------------------
def _cargar_historial() -> List[dict]:
    """Devuelve la lista de tareas guardadas en ~/.snapcontext/historial.json.

    Si el archivo no existe o está corrupto se devuelve [] (sin lanzar error),
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
    """Añade ``entrada`` al historial persistente y lo recorta si crece mucho.

    ``entrada`` típico::

        {"fecha": "2026-08-21T12:00:00", "consulta": "...",
         "archivos": ["..."], "resultado": "éxito"/"fallo",
         "duracion": 12.5}

    Devuelve True si se escribió correctamente. Los errores solo avisan: la
    memoria es un extra y no debe interrumpir una tarea que sí funcionó.
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
    """Muestra las ``ultimas`` entradas más recientes del historial.

    Devuelve el número de entradas mostradas.
    """
    historial = _cargar_historial()
    if not historial:
        info("Historial vacío: aún no hay tareas guardadas.")
        return 0
    recientes = historial[-ultimas:]
    exito(f"Últimas {len(recientes)} tarea(s) guardada(s) "
          f"({HISTORIAL_PATH}):")
    for entrada in reversed(recientes):     # la más reciente primero
        fecha = str(entrada.get("fecha", "?"))
        consulta = str(entrada.get("consulta", "?"))[:70]
        resultado = str(entrada.get("resultado", "?"))
        duracion = entrada.get("duracion")
        duracion_txt = f"{duracion:.1f}s" if isinstance(duracion, (int, float)) else "?"
        archivos = entrada.get("archivos") or []
        _emitir(sys.stdout, f"  [{fecha}] {resultado} · {duracion_txt}")
        _emitir(sys.stdout, f"      consulta : {consulta}")
        if archivos:
            _emitir(sys.stdout, f"      archivos : {', '.join(map(str, archivos[:5]))}"
                    + ("…" if len(archivos) > 5 else ""))
    return len(recientes)


def _limpiar_historial() -> bool:
    """Borra ~/.snapcontext/historial.json. Devuelve True si se eliminó."""
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
# Utilidades genéricas para el agente autónomo — v0.10.0
# ---------------------------------------------------------------------------
def _leer_archivo(ruta: Union[str, Path]) -> Optional[str]:
    """Lee un archivo (ruta relativa o absoluta) y devuelve su contenido.

    Devuelve ``None`` si no existe, es un directorio o falla la lectura
    (el error se registra con ``aviso``). Pensado para ser usado por el chat,
    el orquestador y futuros planificadores autónomos.
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
    (timeout, directorio inválido) devuelven ``(-1, "", mensaje_de_error)``
    sin lanzar excepciones.

    Con ``capture_output=False`` la salida se muestra en tiempo real en la
    consola (no se captura), y ``stdout``/``stderr`` devueltos serán vacíos.
    """
    raiz = Path(directorio).expanduser()
    if not raiz.is_dir():
        return (-1, "", f"El directorio no existe: {raiz}")
    try:
        proc = subprocess.run(
            comando,
            cwd=str(raiz),
            shell=True,
            capture_output=capture_output,
            text=bool(capture_output),
            errors="replace" if capture_output else None,
            timeout=timeout,
        )
        if not capture_output:
            return (proc.returncode, "", "")
        return (proc.returncode, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired:
        return (-1, "", f"El comando tardó demasiado (timeout={timeout}s)")
    except OSError as exc:
        return (-1, "", f"Error ejecutando '{comando}': {exc}")

# --- Procesos en segundo plano para execute_command (v2.3.0) -----------------
_PROCESOS_FONDO: dict = {}   # pid → estado (para ejecución en background)


def _lanzar_proceso_fondo(comando: str, directorio: str = ".",
                          capture_output: bool = True) -> dict:
    """Lanza ``comando`` en segundo plano (Popen). Devuelve un registro con el PID.

    El proceso queda registrado en ``_PROCESOS_FONDO`` para poder consultarlo
    después con :func:`_estado_proceso_fondo`. Nunca lanza excepciones.
    """
    raiz = Path(directorio).expanduser()
    if not raiz.is_dir():
        return {"ok": False, "error": f"El directorio no existe: {raiz}"}
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

    Si ya terminó, captura su stdout/stderr (si se pidió captura) y lo marca
    como finalizado. Devuelve un dict con ``estado``, ``pid`` y (si terminó)
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
    # Ya terminó: capturar salida si se pidió.
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
# Modo chat interactivo (--chat) — v0.10.0
# ---------------------------------------------------------------------------
AYUDA_CHAT = """Comandos disponibles:
  /salir                 → salir del chat
  /archivos              → mostrar los archivos del contexto actual
  /context               → alias de /archivos (contexto de la conversación)
  /limpiar               → limpiar el historial de conversación
  /seleccion <consulta>  → seleccionar archivos relevantes con el proveedor actual
  /provider <proveedor>  → cambiar proveedor (gemini | anthropic | ollama | deepseek | groq)
  /historial             → mostrar las últimas tareas guardadas
  /run <comando>         → ejecutar un comando de shell y mostrar su salida
                           (pide permiso salvo con --no-confirmar)
  /read <archivo>        → mostrar el contenido de un archivo
  /explore <tema>        → buscar un tema en el código (rg/grep/findstr, sin permiso)
  /fix <mensaje>         → ejecutar el alias fix (bucle de pruebas)
  /review <mensaje>      → ejecutar el alias review (vista previa + experto)
  /server <mensaje>      → ejecutar el alias server (bucle con servidor)
  /edit <archivo>        → abrir el archivo en el editor (VSCode/nano/notepad;
                           pide permiso salvo con --no-confirmar)
  /save                  → guardar la sesión actual en historial.json
  /tools                 → listar las herramientas MCP disponibles
  /tool <nombre> <args>  → ejecutar una herramienta MCP
                           (p. ej.: /tool grep login · /tool read_file a.py)
                           args en JSON también válidos: /tool read_file {"ruta": "a.py", "linea_inicio": 10}
  /search <consulta>     → búsqueda semántica de archivos (embeddings; requiere
                           pip install snapcontext[embeddings])
  /buscar <consulta>     → alias de /search (v1.4.0)
  /grafo                 → grafo de dependencias del proyecto en texto ASCII
  /dependencias <archivo> → imports y dependencias inversas de un archivo
  /claude                → mostrar la memoria del proyecto (CLAUDE.md)
  /context               → mostrar memoria del proyecto y archivos en contexto
  /ayuda                 → mostrar esta ayuda
Cualquier otro texto se envía como mensaje al proveedor de IA; si parece una
pregunta de exploración, SnapContext puede usar herramientas MCP de solo
lectura automáticamente y añadir el resultado como contexto.
Los comandos /run, /explore, /fix, /review y /server se ejecutan en un hilo
separado para no bloquear el chat."""


def _enviar_al_proveedor(proveedor: str, modelo: Optional[str],
                         mensajes: List[dict]) -> str:
    """Envía ``mensajes`` ([{"role": ..., "content": ...}, ...]) al proveedor.

    Soporta todos los tipos registrados en PROVEEDORES (gemini, openai-compatible
    y anthropic). Devuelve el texto de respuesta o lanza RuntimeError.
    """
    if proveedor not in PROVEEDORES:
        raise RuntimeError(
            f"Proveedor desconocido '{proveedor}'. "
            f"Válidos: {', '.join(sorted(PROVEEDORES))}"
        )
    cfg = PROVEEDORES[proveedor]
    modelo = modelo or cfg["modelo_default"]
    tipo = cfg["tipo"]

    if tipo == "gemini":
        if genai is None:
            raise RuntimeError(MENSAJE_GENAI_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(MENSAJE_API_KEY)
        genai.configure(api_key=api_key)
        generador = genai.GenerativeModel(model_name=modelo)
        # Gemini distingue user/model; convertimos "assistant" → "model".
        contenidos = [
            {"role": "user" if m["role"] != "assistant" else "model",
             "parts": [m["content"]]}
            for m in mensajes
        ]
        respuesta = generador.generate_content(contenidos)
        return respuesta.text or ""

    if tipo == "anthropic":
        if anthropic is None:
            raise RuntimeError(MENSAJE_ANTHROPIC_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))
        cliente = anthropic.Anthropic(api_key=api_key)
        respuesta = cliente.messages.create(
            model=modelo, max_tokens=2048, messages=mensajes,
        )
        return "".join(
            bloque.text for bloque in respuesta.content
            if getattr(bloque, "type", None) == "text"
        )

    # Tipo "openai": Groq, DeepSeek y Ollama (API compatible).
    if openai is None:
        raise RuntimeError(MENSAJE_OPENAI_FALTANTE)
    api_key = os.environ.get(cfg["clave_env"], "").strip()
    if cfg["requiere_clave"] and not api_key:
        raise RuntimeError(_mensaje_clave_faltante(proveedor, cfg))
    cliente = openai.OpenAI(
        api_key=api_key or "ollama-local",
        base_url=_resolver_url_openai(cfg), timeout=120,
    )
    respuesta = cliente.chat.completions.create(
        model=modelo, messages=mensajes, temperature=0.4,
    )
    return respuesta.choices[0].message.content or ""


def _ejecutar_chat(proveedor: Optional[str] = None,
                   modelo: Optional[str] = None) -> int:
    """REPL interactivo (`snapcontext --chat`). Devuelve código de salida.

    Mantiene la conversación en memoria (`historial_chat`) y da acceso a los
    comandos /salir, /archivos, /limpiar, /seleccion, /provider, /historial y
    /ayuda. Cualquier otro texto se envía al proveedor actual.
    """
    preferencias = cargar_configuracion()
    proveedor = proveedor or preferencias.get("provider") or PROVEEDOR_DEFECTO

    _emitir(sys.stdout, _pintar(
        f"💬 SnapContext Chat (v{VERSION}) — Escribe tu tarea, "
        "/salir para terminar", _CYAN))
    info(f"Proveedor actual: {PROVEEDORES[proveedor]['nombre']} "
         f"({modelo or PROVEEDORES[proveedor]['modelo_default']}). "
         "Escribe /ayuda para ver los comandos.")

    historial_chat: List[dict] = []       # conversación de esta sesión
    contexto_archivos: List[str] = []     # selección actual (/seleccion)
    hilos: List[threading.Thread] = []    # comandos de agente en 2º plano

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

        if linea == "/limpiar":
            historial_chat = []
            exito("Historial de conversación limpiado.")
            continue

        if linea == "/archivos" or linea == "/context":
            if MEMORIA_PROYECTO:
                exito("── Memoria del proyecto (CLAUDE.md) ──")
                for linea_memoria in MEMORIA_PROYECTO.splitlines()[:60]:
                    _emitir(sys.stdout, "  " + linea_memoria)
            if not contexto_archivos and not MEMORIA_PROYECTO:
                aviso("Sin memoria de proyecto ni archivos en contexto "
                      "(usa --init-claude o /seleccion).")
            elif contexto_archivos:
                exito(f"Archivos en contexto ({len(contexto_archivos)}):")
                for archivo in contexto_archivos:
                    _emitir(sys.stdout, "   • " + archivo)
            continue

        # ---- memoria del proyecto (v0.15.0) -------------------------------
        if linea == "/claude":
            if not MEMORIA_PROYECTO:
                aviso("No hay CLAUDE.md ni SNAPCONTEXT.md en este proyecto. "
                      "Créalos con: snapcontext --init-claude")
            else:
                exito(f"── {_buscar_claude_md().name} ──")
                _emitir(sys.stdout, MEMORIA_PROYECTO)
            continue

        if linea == "/historial":
            _mostrar_historial()
            continue

        if linea == "/save":
            _cmd_chat_save(historial_chat)
            continue

        # ---- búsqueda semántica (v1.1.0; alias /buscar desde v1.4.0) -------
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
                aviso("Sin resultados semánticos.")
                continue
            exito(f"Resultados semánticos para '{consulta_busqueda}':")
            for resultado in resultados[:10]:
                _emitir(sys.stdout, _pintar(
                    f"   • {resultado['archivo']}:{resultado['linea_inicio']} "
                    f"(similitud {resultado['similitud']})", _VERDE))
            continue

        # ---- grafo y dependencias (v1.4.0) --------------------------------
        if linea == "/grafo":
            grafo_chat = _grafo_dependencias(".")
            nodos_chat = grafo_chat.get("nodos", [])
            enlaces_chat = grafo_chat.get("enlaces", [])
            if not nodos_chat:
                aviso("Sin archivos de código detectados para construir el grafo.")
                continue
            exito(f"Grafo de dependencias ({len(nodos_chat)} nodo(s), "
                  f"{len(enlaces_chat)} enlace(s)):")
            salientes: dict = {}
            for enlace in enlaces_chat:
                salientes.setdefault(enlace["origen"], []).append(
                    enlace["destino"])
            for nodo in nodos_chat:
                nodo_id = nodo["id"]
                _emitir(sys.stdout, _pintar(f"  ▸ {nodo_id}", _CYAN))
                for destino in salientes.get(nodo_id, []):
                    _emitir(sys.stdout, f"      ──▶ {destino}")
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
                      f"(¿existe el archivo y tiene imports?).")
                continue
            exito(f"Dependencias de {archivo_objetivo}:")
            _emitir(sys.stdout, f"  Importa de ({len(directas)}):")
            for destino in directas:
                _emitir(sys.stdout, _pintar(f"    ──▶ {destino}", _VERDE))
            if not directas:
                _emitir(sys.stdout, "    (ninguna)")
            _emitir(sys.stdout, f"  Importado por ({len(inversas)}):")
            for origen in inversas:
                _emitir(sys.stdout, _pintar(f"    ◀── {origen}", _AMARILLO))
            if not inversas:
                _emitir(sys.stdout, "    (ninguno)")
            continue

        # ---- herramientas MCP (v0.14.0) -----------------------------------
        if linea == "/tools":
            herramientas = _cargar_herramientas_mcp()
            exito(f"Herramientas MCP disponibles ({len(herramientas)}):")
            for nombre in sorted(herramientas):
                cfg = herramientas[nombre]
                permiso = "🔒 requiere permiso" if cfg.get("requiere_permiso") \
                    else "lectura"
                _emitir(sys.stdout,
                        f"   • {nombre} — {cfg['descripcion']} [{permiso}]")
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
            info(f"🛠 Ejecutando herramienta MCP '{nombre}'...")
            llamada = _ejecutar_herramienta_mcp(nombre, argumentos,
                                                confirmar=CONFIRMAR_ACCIONES)
            texto = _formatear_resultado_mcp(llamada)
            _emitir(sys.stdout, _pintar(texto, _VERDE if llamada["ok"]
                                        else _AMARILLO))
            if llamada["ok"]:
                exito("Herramienta completada.")
            else:
                error("La herramienta devolvió un error.")
            # El resultado queda en el contexto de la conversación.
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
                aviso(f"Proveedores válidos: {', '.join(sorted(PROVEEDORES))}")
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
                    exito(f"Selección ({len(contexto_archivos)}):")
                    for archivo in contexto_archivos:
                        _emitir(sys.stdout, "   • " + archivo)
                else:
                    aviso("El proveedor no devolvió archivos.")
            except RuntimeError as exc:
                error(str(exc))
            continue

        # ---- mensaje normal → proveedor de IA ----------------------------
        historial_chat.append({"role": "user", "content": linea})
        try:
            # MCP automático (v0.14.0): si el mensaje parece una pregunta de
            # exploración, se recopila contexto con herramientas de solo
            # lectura y se añade al turno del usuario.
            contexto_mcp = _contexto_automatico_mcp(linea)
        except Exception as exc:                # nunca romper el chat
            depurar(f"[mcp] contexto automático falló: {exc}")
            contexto_mcp = ""
        if contexto_mcp:
            info("🛠 Contexto MCP añadido a la consulta "
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
            # Se envían solo los últimos 20 turnos para no crecer sin límite.
            respuesta = _enviar_al_proveedor(
                proveedor, modelo, historial_chat[-20:],
            )
        except RuntimeError as exc:
            error(str(exc))
            historial_chat.pop()            # no conservar el turno fallido
            continue
        except Exception as exc:            # errores de red/API no controlados
            error(f"Error hablando con {PROVEEDORES[proveedor]['nombre']}: {exc}")
            historial_chat.pop()
            continue
        historial_chat.append({"role": "assistant", "content": respuesta})
        _emitir(sys.stdout, _pintar(respuesta, _VERDE))
        _emitir(sys.stdout, "")


# ---------------------------------------------------------------------------
# Comandos de agente del chat (--chat) — v0.10.x
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
        error(f"Comando terminado con código {codigo}.")


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
    exito(f"── {archivo} ({len(lineas)} línea(s)) " + "─" * 20)
    # Se muestran como máximo 400 líneas para no saturar la consola.
    for linea in lineas[:400]:
        _emitir(sys.stdout, "  " + linea)
    if len(lineas) > 400:
        aviso(f"(salida recortada: {len(lineas) - 400} línea(s) más)")


def _herramienta_busqueda() -> Optional[str]:
    """Devuelve el buscador disponible: ripgrep ('rg'), 'grep' o 'findstr'."""
    for herramienta in ("rg", "grep", "findstr"):
        if shutil.which(herramienta):
            return herramienta
    return None


def _cmd_chat_explore(tema: str, directorio: str = ".") -> None:
    """`/explore <tema>`: busca ``tema`` en el código del repositorio.

    Usa ripgrep si está instalado; si no, `grep` en Linux/macOS o `findstr`
    en Windows. Recursivo e insensible a mayúsculas.
    """
    if not tema:
        aviso("Uso: /explore <tema>")
        return
    herramienta = _herramienta_busqueda()
    if herramienta is None:
        error("No se encontró ningún buscador (rg, grep ni findstr) en el PATH.")
        return
    info(f"Explorando '{tema}' con {herramienta}...")
    if herramienta == "rg":
        comando = f'rg -n -i --max-count 5 "{tema}"'
    elif herramienta == "grep":
        comando = f'grep -rn -i -m 5 "{tema}" .'
    else:  # findstr (Windows): /s recursivo, /i sin mayúsculas
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
        error(f"La búsqueda falló (código {codigo}): "
              f"{stderr.strip() or 'sin detalle'}")


def _cmd_chat_alias(alias: str, mensaje: str) -> int:
    """Ejecuta los alias fix/review/server desde el chat.

    Reutiliza exactamente la lógica existente: convierte el alias con
    ``_preparar_argv_aliases``, parsea los argumentos con ``crear_parser`` y
    llama a ``flujo_principal`` (que además registra la tarea en el historial).
    Devuelve el código de salida del pipeline.
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
    error("No se encontró ningún editor (code/nano/notepad/$EDITOR).")


def _cmd_chat_save(historial_chat: List[dict]) -> None:
    """`/save`: guarda un resumen de la sesión actual en historial.json."""
    if not historial_chat:
        aviso("No hay conversación que guardar.")
        return
    turnos_usuario = [m["content"] for m in historial_chat
                      if m.get("role") == "user"]
    entrada = {
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "consulta": " | ".join(t[:120] for t in turnos_usuario),
        "archivos": [],
        "resultado": "éxito",
        "duracion": round(len(turnos_usuario), 2),   # nº de turnos del usuario
        "tipo": "sesion-chat",
        "mensajes": len(historial_chat),
    }
    if _guardar_historial(entrada):
        exito(f"Sesión guardada en {HISTORIAL_PATH} "
              f"({entrada['mensajes']} mensajes).")


# ---------------------------------------------------------------------------
# Planificador de tareas (--plan) — v0.12.0
# ---------------------------------------------------------------------------
PROMPT_PLAN = (
    "Eres un planificador de tareas de desarrollo. Descompón la siguiente "
    "tarea en pasos CONCRETOS y ATÓMICOS (máximo 8).\n\n"
    "TAREA: {consulta}\n\n"
    "Devuelve SOLO un objeto JSON con esta forma exacta (sin explicaciones):\n"
    '{{"pasos": [{{\n'
    '  "descripcion": "qué hace este paso",\n'
    '  "accion": "editar" | "ejecutar" | "consultar" | "mcp",\n'
    '  "archivos": ["ruta/relativa.py"],   // solo para accion "editar"\n'
    '  "comando": "comando shell",         // solo para accion "ejecutar"\n'
    '  "herramienta": "grep|read_file|list_files|ast|git_status|git_diff|\n'
    '                 "execute_command|...",   // solo para accion "mcp"\n'
    '  "args": {{"patron": "..."}},        // solo para accion "mcp"\n'
    '  "variable": "mi_resultado",        // opcional (mcp): nombre del resultado\n'
    "}}]}}\n\n"
    "Significado de las acciones:\n"
    ' - "editar": modificar código (Aider). Indica los archivos implicados.\n'
    ' - "ejecutar": lanzar un comando (tests, build, migraciones...).\n'
    ' - "consultar": aclarar una duda sobre el proyecto sin cambiar nada.\n'
    ' - "mcp": ejecutar una herramienta MCP (campos "herramienta" y "args") y\n'
    '   usar su resultado en pasos posteriores con {{{{resultado}}}} o {{{{mi_variable}}}}.\n'
    "Condiciones admitidas (el paso se salta si son falsas):\n"
    ' - archivo_existe(ruta), archivo_contiene(ruta, texto), comando_exito(cmd),\n'
    "   variable_existe(nombre) o comparaciones como\n"
    "   \"pasos[0].resultado == 'ok'\"  ·  \"resultados.mi_variable != ''\".\n"
)

ACCIONES_VALIDAS = {"editar", "ejecutar", "consultar", "mcp"}


def _normalizar_pasos(datos) -> List[dict]:
    """Normaliza la respuesta del proveedor a una lista de pasos válidos.

    Acepta ``{"pasos": [...]}``, una lista directa o un único paso suelto.
    Descarta pasos mal formados (sin descripción o con acción desconocida).
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
            # v1.3.0: dependencias entre pasos y ejecución condicional.
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
    """Convierte el campo ``dependencias`` de un paso en una lista de índices.

    Acepta lista de enteros/strings numéricos o un único valor. Se descartan
    los índices no válidos (negativos o fuera de rango se validan en la
    ejecución, aquí solo se normaliza el tipo).
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

    Devuelve la lista de pasos normalizada (vacía si el proveedor no devolvió
    nada utilizable). Lanza RuntimeError ante fallos de configuración/API.
    """
    preferencias = cargar_configuracion()
    proveedor = proveedor or preferencias.get("provider") or PROVEEDOR_DEFECTO
    cfg = PROVEEDORES[proveedor]
    modelo = modelo or cfg["modelo_default"]
    prompt = PROMPT_PLAN.format(consulta=consulta)
    info(f"Generando plan con {cfg['nombre']} ({modelo})...")

    # MCP (v0.14.0): explora el proyecto con herramientas de solo lectura para
    # generar pasos más precisos (best-effort: nunca rompe la planificación).
    try:
        contexto_proyecto: List[str] = []
        estado = _ejecutar_herramienta_mcp("git_status", {},
                                           confirmar=False)
        if estado.get("ok"):
            res = estado["resultado"]
            contexto_proyecto.append(
                f"Rama git: {res.get('rama')} · cambios sin commitear: "
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
            info("🛠 Contexto MCP del proyecto añadido al planificador.")
    except Exception as exc:
        depurar(f"[mcp] contexto de planificación falló: {exc}")

    # Memoria de proyecto (v0.15.0): CLAUDE.md como contexto persistente.
    if MEMORIA_PROYECTO:
        prompt += ("\n\nMEMORIA DEL PROYECTO (CLAUDE.md, respeta sus "
                   "convenciones al proponer pasos):\n"
                   + MEMORIA_PROYECTO[:3000])
        info("📄 Memoria del proyecto (CLAUDE.md) incluida en la planificación.")


    tipo = cfg["tipo"]
    if tipo == "gemini":
        if genai is None:
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
        if anthropic is None:
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
        if openai is None:
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

    depurar(f"Plan recibido ({len(texto)} caracteres): {texto[:200]}")
    return _normalizar_pasos(parsear_json(texto))


# --- Git explícito para el planificador ------------------------------------
def _es_repo_git(directorio: str) -> bool:
    """True si ``directorio`` está dentro de un repositorio git."""
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
        aviso(f"La rama '{nombre.strip()}' ya existía; se ha cambiado a ella.")
        return True
    error(f"No se pudo crear/cambiar a la rama '{nombre.strip()}': "
          f"{stderr.strip()}")
    return False


def _git_commit_paso(descripcion: str, directorio: str = ".") -> bool:
    """`git add .` + `git commit -m "paso: <descripcion>"`. True si ok.

    Si no hay cambios que commitear se considera éxito silencioso.
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
    aviso(f"El commit del paso falló: {(stderr or '').strip()}")
    return False


def _ejecutar_paso_plan(paso: dict, args: argparse.Namespace,
                        raiz: str) -> tuple:
    """Ejecuta un paso del plan. Devuelve (ok: bool, detalle: str).

    - "editar": usa el orquestador actual — ``_planificar`` para elegir los
      archivos y ``_bucle_test``/AgenteEditor para aplicar la descripción.
    - "ejecutar": lanza ``paso["comando"]`` con ``_ejecutar_comando``.
    - "consultar": pregunta al proveedor y muestra su respuesta.
    """
    import snapcontext as sc
    from orquestador import Orquestador

    accion = paso["accion"]
    descripcion = paso["descripcion"]
    # v2.3.0: sustitución de marcadores {{variable}} / {{resultado}} en los
    # campos del paso usando el contexto dinámico del plan.
    descripcion = _resolver_marcadores(descripcion)
    for _clave in ("comando", "herramienta", "contenido"):
        _valor = paso.get(_clave)
        if isinstance(_valor, str) and "{{" in _valor:
            paso[_clave] = _resolver_marcadores(_valor)
    if isinstance(paso.get("archivos"), list):
        paso["archivos"] = [_resolver_marcadores(a) for a in paso["archivos"]]
    if isinstance(paso.get("args"), dict) and paso["args"]:
        paso["args"] = _resolver_marcadores_args(paso["args"])

    # Confirmación de permisos (v0.13.0) antes de cualquier acción.
    # En modo autónomo (--auto, v0.17.0) no se pregunta: solo se respetan las
    # preferencias ya guardadas en permisos.json (nunca → denegado).
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
        return (codigo == 0, f"código {codigo}")

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
            muestra = muestra[:400] + "…"
        if llamada.get("ok"):
            exito("[mcp] resultado: " + muestra)
            _contexto_plan_variable(str(paso.get("variable") or herramienta),
                                    res)
            return (True, herramienta + ": ok")
        error("[mcp] falló: " + muestra)
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
                             "Responde de forma breve y útil."}],
            )
            _emitir(sys.stdout, _pintar(respuesta, _VERDE))
            return (True, "respuesta mostrada")
        except RuntimeError as exc:
            error(str(exc))
            return (False, str(exc))

    # accion == "editar": reutiliza el pipeline existente o usa el editor propio
    editor_elegido = getattr(args, "editor", "aider") or "aider"
    if editor_elegido == "propio":
        archivos_paso = paso.get("archivos", [])
        contenido_paso = paso.get("contenido")
        if archivos_paso and contenido_paso is not None:
            # Si el paso trae archivo y contenido explícito
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
        return (False, "no se pudo planificar la edición (sin candidatos)")
    _, ruta_raiz, _, seleccion = plan

    if editor_elegido == "propio":
        modo_ed = getattr(args, "modo_edicion", "auto") or "auto"
        todo_ok = orch.agente_editor_propio.ejecutar(
            seleccion,
            descripcion,
            directorio=str(ruta_raiz),
            modo_edicion=modo_ed,
            modelo=getattr(args, "modelo", None),
        )
        return (todo_ok, f"EditorPropio sobre {len(seleccion)} archivo(s)")

    if getattr(args, "test_loop", False):
        ok = orch._bucle_test(
            descripcion, seleccion, str(ruta_raiz),
            opciones_aider=getattr(args, "aider_opciones", ""),
            comando_test=shlex.split(getattr(args, "comando_test",
                                             COMANDO_TEST_DEFECTO)),
            max_iteraciones=max(getattr(args, "max_iteraciones", 1), 1),
        )
        return (ok, "bucle de pruebas")
    ok = orch.agente_editor.ejecutar_aider(
        seleccion, descripcion, str(ruta_raiz),
        opciones_aider=getattr(args, "aider_opciones", ""),
    )
    return (ok, f"Aider sobre {len(seleccion)} archivo(s)")


# --- Condiciones y paralelismo del planificador (v1.4.0) --------------------
def _evaluar_condicion(condicion: str, raiz: str = ".",
                      contexto: Optional[dict] = None) -> bool:
    """Evalúa la condición de un paso del plan. Devuelve True si se cumple.

    Formatos soportados:

      Funciones (v1.4.0):
        archivo_existe('src/main.py')
        archivo_contiene('src/main.py', 'def main')
        comando_exito('flutter test')
        variable_existe('mi_variable')            # v2.3.0

      Comparaciones dinámicas (v2.3.0), con resultados de pasos previos o
      variables dejadas en el contexto (p. ej. por pasos "mcp"):
        pasos[0].resultado == 'ok'
        pasos[2].resultado != 'fallo'
        resultados.mi_variable == 'listo'
        mi_variable != ''                         # forma abreviada

    Las cadenas pueden ir con comillas simples o dobles. Cualquier condición
    mal formada o desconocida devuelve False con un aviso (fallo elegante:
    el paso se salta, nunca se aborta el plan).
    """
    if contexto is None:
        contexto = _CONTEXTO_PLAN
    condicion = (condicion or "").strip()
    if not condicion:
        return True

    # 1) Comparaciones dinámicas (== / !=).
    comparacion = re.match(r"^(.+?)\s*(==|!=)\s*(.+)$", condicion, re.S)
    if comparacion and "(" not in condicion.split("==")[0].split("!=")[0]:
        izquierdo = _resolver_operando_condicion(
            comparacion.group(1).strip(), contexto)
        derecho = _resolver_operando_condicion(
            comparacion.group(3).strip(), contexto)
        if izquierdo is _DESCONOCIDO or derecho is _DESCONOCIDO:
            aviso(f"Condición con referencia desconocida: '{condicion}'.")
            return False
        iguales = (_normalizar_comparacion(izquierdo)
                   == _normalizar_comparacion(derecho))
        return iguales if comparacion.group(2) == "==" else not iguales

    # 2) Formas funcionales clásicas.
    coincidencia = re.match(r"^([a-zA-Z_]\w*)\s*\((.*)\)\s*$",
                            condicion, re.S)
    if not coincidencia:
        aviso(f"Condición de paso mal formada: '{condicion}'. Se interpreta "
              f"como no cumplida.")
        return False
    funcion, crudo_args = coincidencia.group(1), coincidencia.group(2)
    try:
        argumentos = [a.strip()
                      for a in _partir_argumentos(crudo_args)]
    except ValueError as exc:
        aviso(f"Condición inválida '{condicion}': {exc}")
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

    aviso(f"Función de condición desconocida: '{funcion}'. Soportadas: "
          f"archivo_existe, archivo_contiene, comando_exito, "
          f"variable_existe.")
    return False


# Resultados desconocidos para condiciones dinamicas.
_DESCONOCIDO = object()


def _resolver_operando_condicion(operando: str, contexto: dict):
    """Convierte un operando de condición en un valor Python concreto.

    Acepta literales ('texto', números, true/false/null) y referencias al
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
    """Normaliza valores para poder compararlos entre sí."""
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
    """Separa los argumentos de una condición respetando comillas."""
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


# --- Contexto dinámico del plan (v2.3.0) ------------------------------------
# Los pasos pueden dejar resultados (p. ej. herramientas MCP) en este contexto
# y los pasos posteriores los consumen con {{resultado}}, {{mi_variable}} o
# condiciones como "pasos[0].resultado == 'ok'" / "resultados.mi_var == 'x'".
_CONTEXTO_PLAN = {"variables": {}, "pasos": {}}
_CANDADO_CONTEXTO_PLAN = threading.Lock()


def _contexto_plan_reiniciar() -> None:
    """Limpia el contexto dinámico al empezar cada ejecución del plan."""
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["variables"].clear()
        _CONTEXTO_PLAN["pasos"].clear()


def _contexto_plan_variable(nombre: str, valor) -> None:
    """Guarda ``valor`` bajo ``nombre`` (y como último ``resultado``)."""
    if not nombre:
        return
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["variables"][nombre] = valor
        _CONTEXTO_PLAN["variables"]["resultado"] = valor


def _registrar_resultado_plan(numero: int, ok: bool, detalle: str,
                              estado: str = "") -> None:
    """Registra el resultado de un paso (base 1) para condiciones dinámicas."""
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["pasos"][str(numero)] = {
            "resultado": estado or ("ok" if ok else "fallo"),
            "ok": ok, "detalle": detalle}


def _resolver_marcadores(texto: str):
    """Sustituye la marca de doble llave {{clave}} por el valor que
    tenga esa clave en el contexto dinámico del plan. Si la clave
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
    """Extrae los índices de pasos y nombres de variables que usa una condición."""
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
    """Aplica la sustitución de marcadores a los valores string de un dict."""
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
        aviso(f"{prefijo} condición no cumplida ({condicion}); se salta.")
        return {"paso": numero, "descripcion": paso["descripcion"],
                "accion": paso["accion"], "resultado": "saltado",
                "detalle": f"condición no cumplida: {condicion}", "intentos": 0}
    try:
        ok, detalle = _ejecutar_paso_plan(paso, args, raiz)
    except Exception as exc:                     # blindaje del hilo
        ok, detalle = False, f"excepción: {exc}"
    _registrar_resultado_plan(numero, ok, detalle)
    marca = "✔" if ok else "✖"
    _emitir(sys.stdout, f"  {marca} {prefijo} terminado ({detalle})")
    if ok and getattr(args, "git_commit", True):
        with _CANDADO_GIT_PLAN:
            _git_commit_paso(paso["descripcion"], raiz)
    return {"paso": numero, "descripcion": paso["descripcion"],
            "accion": paso["accion"], "resultado": "éxito" if ok else "fallo",
            "detalle": detalle, "intentos": 1}


def _ejecutar_plan_en_paralelo(pasos: List[dict], args: argparse.Namespace,
                               raiz: str, max_hilos: int) -> List[dict]:
    """Ejecuta el plan con ``--paralelo N`` (modo --auto).

    Rondas de ejecución: en cada ronda se lanzan todos los pasos cuyas
    dependencias ya tuvieron éxito (ThreadPoolExecutor limita la concurrencia
    a ``max_hilos``); los pasos con dependencias fallidas o saltadas se marcan
    como saltados. Los logs llevan el identificador ``[paso N]``.
    """
    estado: dict = {}                            # índice → resultado terminal
    resultados: List[dict] = []
    pendientes = set(range(len(pasos)))
    MALOS_TERMINALES = ("fallo", "saltado")

    with ThreadPoolExecutor(max_workers=max(1, max_hilos)) as pool:
        while pendientes:
            # 'dependencias' guarda números de paso (base 1): convertimos.
            for i in sorted(pendientes):
                deps = [d - 1 for d in (pasos[i].get("dependencias") or [])]
                if any(estado.get(d) in MALOS_TERMINALES for d in deps):
                    numero = i + 1
                    aviso(f"[paso {numero}] saltado: dependencia(s) sin éxito "
                          f"({[d + 1 for d in deps]}).")
                    estado[i] = "saltado"
                    resultados.append(
                        {"paso": numero, "descripcion": pasos[i]["descripcion"],
                         "accion": pasos[i]["accion"], "resultado": "saltado",
                         "detalle": "dependencia sin éxito", "intentos": 0})
                    pendientes.discard(i)

            # v2.3.0: además de las dependencias explícitas, un paso queda
            # bloqueado mientras su condición referencie variables que algún
            # paso pendiente aún puede producir (p. ej. un paso "mcp").
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
                if any(estado.get(d) != "éxito" for d in deps):
                    return False
                ref_i, ref_v = _refs_de_condicion(
                    pasos[i].get("condicion") or "")
                if any(estado.get(d) != "éxito" for d in ref_i):
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
                if pendientes:                   # nada ejecutable → evitar bloqueo
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


def _ejecutar_planificador(args: argparse.Namespace) -> int:
    """Modo planificador (`snapcontext --plan "tarea"`). Devuelve código 0/1.

    Flujo: generar plan con IA → confirmación → ejecución secuencial con menú
    continuar/reintentar/saltar tras cada paso → resumen final. Con
    ``--branch`` crea una rama antes de empezar y con ``--git-commit``
    (por defecto) commitea `paso: <descripción>` tras cada paso exitoso.
    """
    global DEPURAR
    DEPURAR = getattr(args, "depurar", False)
    consulta = getattr(args, "consulta", None)
    if not consulta:
        error("El modo --plan necesita una consulta. Uso:\n"
              '  snapcontext --plan "añadir login con Google"')
        return 1

    directorio = getattr(args, "directorio", ".") or "."
    raiz = str(resolver_raiz(directorio))

    # Rama git opcional antes de empezar.
    rama = getattr(args, "branch", None)
    if rama and not _git_crear_rama(rama, raiz):
        return 1

    # 1) Generación del plan (con un reintento si viene vacío/mal formado).
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
        aviso("El plan vino vacío o mal formado; reintentando...")
    if not pasos:
        error("No se pudo obtener un plan válido del proveedor.")
        return 1

    # Modo autónomo (v0.17.0): sin confirmación inicial ni menú por paso;
    # reintentos automáticos de pasos fallidos.
    auto = bool(getattr(args, "auto", False))
    MAX_REINTENTOS_AUTO = 3
    if auto:
        exito(f"Modo autónomo (--auto): {len(pasos)} paso(s) se ejecutarán "
              f"sin confirmaciones, con hasta {MAX_REINTENTOS_AUTO} "
              f"reintentos por paso. Permisos guardados en permisos.json "
              f"siguen aplicándose.")
    else:
        # 2) Mostrar el plan y pedir confirmación.
        exito(f"Plan generado ({len(pasos)} paso(s)):")
        for numero, paso in enumerate(pasos, start=1):
            extra = paso.get("comando") or ", ".join(paso.get("archivos", []))
            sufijo = f" → {extra}" if extra else ""
            _emitir(sys.stdout, f"  {numero}. [{paso['accion']}] "
                                f"{paso['descripcion']}{sufijo}")
        if not _preguntar_si("\n¿Quieres ejecutar estos pasos? (s/n): "):
            aviso("Plan cancelado por el usuario.")
            return 0

    # 3) Ejecución (v1.4.0): con --paralelo N (y --auto) se lanzan varios pasos
    # sin dependencias mutuas a la vez; en caso contrario, secuencial.
    _contexto_plan_reiniciar()   # v2.3.0: contexto dinámico por plan
    max_hilos = max(1, int(getattr(args, "paralelo", 1) or 1))
    resultados: List[dict] = []
    if auto and max_hilos > 1:
        exito(f"Modo --paralelo: hasta {max_hilos} paso(s) simultáneo(s).")
        resultados = _ejecutar_plan_en_paralelo(pasos, args, raiz, max_hilos)
    else:
        indice = 0
        abortar = False
        estado_seq: dict = {}   # índice → "éxito"|"fallo"|"saltado"
        while indice < len(pasos) and not abortar:
            paso = pasos[indice]
            numero = indice + 1

            # v1.4.0: un paso solo se ejecuta si sus dependencias tuvieron éxito.
            # 'dependencias' guarda números de paso (base 1); convertimos.
            deps_paso = [d - 1 for d in (paso.get("dependencias") or [])]
            fallidas = [d + 1 for d in deps_paso
                        if estado_seq.get(d) != "éxito"]
            if fallidas:
                aviso(f"Paso {numero} saltado: dependencia(s) sin éxito "
                      f"{fallidas}.")
                resultados.append(
                    {"paso": numero, "descripcion": paso["descripcion"],
                     "accion": paso["accion"], "resultado": "saltado",
                     "detalle": f"dependencia(s) sin éxito: {fallidas}",
                     "intentos": 0})
                estado_seq[indice] = "saltado"
                indice += 1
                continue

            # v1.4.0: ejecución condicional del paso.
            condicion = paso.get("condicion")
            if condicion and not _evaluar_condicion(condicion, raiz):
                aviso(f"Paso {numero} saltado: condición no cumplida "
                      f"({condicion}).")
                resultados.append(
                    {"paso": numero, "descripcion": paso["descripcion"],
                     "accion": paso["accion"], "resultado": "saltado",
                     "detalle": f"condición no cumplida: {condicion}",
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
                    ok, detalle = False, f"excepción: {exc}"
                    error(f"El paso lanzó una excepción: {exc}")
                if ok or not auto:
                    break
                if intentos < MAX_REINTENTOS_AUTO:
                    aviso(f"Paso {numero} falló (intento {intentos}/"
                          f"{MAX_REINTENTOS_AUTO}); reintentando automáticamente…")
                else:
                    aviso(f"Paso {numero} agotó sus {MAX_REINTENTOS_AUTO} "
                          f"intentos; se continúa con el siguiente paso.")
                    break

            _registrar_resultado_plan(numero, ok, detalle)
            estado_seq[indice] = "éxito" if ok else "fallo"
            if ok and getattr(args, "git_commit", True):
                _git_commit_paso(paso["descripcion"], raiz)

            if auto:
                # Autónomo: cada paso se registra una única vez (último intento).
                resultados.append({"paso": numero,
                                   "descripcion": paso["descripcion"],
                                   "accion": paso["accion"],
                                   "resultado": "éxito" if ok else "fallo",
                                   "detalle": detalle, "intentos": intentos})
                indice += 1
                continue

            # Interactivo: menú post-paso y registro único al abandonar el paso.
            while True:
                try:
                    eleccion = input(_pintar(
                        "[c]ontinuar · [r]eintentar · [s]altar · [x]abortar "
                        "(c/r/s/x): ", _CYAN)).strip().lower()
                except EOFError:
                    eleccion = "c"
                if eleccion in ("", "c", "continuar"):
                    resultados.append(
                        {"paso": numero, "descripcion": paso["descripcion"],
                         "accion": paso["accion"],
                         "resultado": "éxito" if ok else "fallo",
                         "detalle": detalle, "intentos": intentos})
                    indice += 1
                    break
                if eleccion in ("r", "reintentar"):
                    break                        # mismo índice: repetir el paso
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
                aviso("Opción no válida; usa c, r, s o x.")

    # 4) Resumen final + memoria persistente.
    _emitir(sys.stdout, "")
    exito("── Resumen del plan " + "─" * 30)
    for r in resultados:
        marca = "✔" if r["resultado"] == "éxito" else "✖"
        reintentos = (f", {r['intentos']} intento(s)"
                      if r.get("intentos", 1) > 1 else "")
        _emitir(sys.stdout,
                f"  {marca} Paso {r['paso']} [{r['accion']}] "
                f"{r['descripcion']} ({r['resultado']}: {r['detalle']}"
                f"{reintentos})")
    saltados = len(pasos) - len(resultados)
    if saltados > 0:
        aviso(f"{saltados} paso(s) sin ejecutar (saltados o abortados).")
    exitos = sum(1 for r in resultados if r["resultado"] == "éxito")
    exito(f"Resultado: {exitos}/{len(resultados)} paso(s) exitoso(s).")

    todo_ok = bool(resultados) and exitos == len(resultados) and saltados == 0
    _guardar_historial({
        "fecha": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "consulta": consulta,
        "archivos": [],
        "resultado": "éxito" if todo_ok else ("fallo" if exitos == 0 else "parcial"),
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

    # Memoria de proyecto (v0.15.0): tras un plan exitoso se propone (con
    # confirmación) actualizar CLAUDE.md con lo aprendido.
    if todo_ok and MEMORIA_PROYECTO:
        resumen = "; ".join(
            f"{r['descripcion']} [{r['accion']}] ({r['resultado']})"
            for r in resultados)
        _actualizar_claude_md_automatico(resumen, raiz)
    return 0 if todo_ok or abortar else 1


# ---------------------------------------------------------------------------
# Permisos y confirmaciones (--confirmar / --no-confirmar) — v0.13.0
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Memoria persistente avanzada (SQLite) y aprendizaje autónomo — v3.0.0
# ---------------------------------------------------------------------------
# Sustituye/complementa el historial JSON con una base de datos robusta en
# ~/.snapcontext/memoria.db. sqlite3 forma parte de la stdlib: sin dependencias.
import sqlite3
import datetime

DB_PATH = CONFIG_DIR / "memoria.db"
_DB_CONEXION = None                    # conexión singleton (check_same_thread=False)
_CANDADO_DB = threading.RLock()        # reentrante: _db_insert → _db → _db_init


def _db_init() -> str:
    """Crea la base de datos y sus tablas si no existen. Devuelve la ruta.

    Tablas:
      skills               → procedimientos reutilizables aprendidos.
      historial_aprendizaje → tareas completadas (éxito/fallo/correcciones).
      contexto_kv          → preferencias del usuario y metadatos (clave/valor).
      cola                 → skills pendientes de ejecutar por el daemon.
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
                fallos         INTEGER DEFAULT 0,
                confiabilidad  REAL DEFAULT 0.5,
                archivado      INTEGER DEFAULT 0
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
        """)
        _DB_CONEXION.commit()
    return str(ruta)


def _db():
    """Devuelve la conexión activa, inicializando la base si hace falta."""
    if _DB_CONEXION is None:
        _db_init()
    return _DB_CONEXION


def _db_cerrar() -> None:
    """Cierra la conexión (útil en tests tras re-apuntar DB_PATH)."""
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
    """Ejecuta UPDATE/DELETE y devuelve el número de filas afectadas."""
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


# ─── Skills: procedimientos reutilizables ──────────────────────────────────
def _skill_normalizar_nombre(consulta: str, max_len: int = 60) -> str:
    """Genera un nombre estable a partir de la consulta del usuario.

    Translitera acentos y eñes (á→a, ñ→n) para que el nombre sea estable
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


# ─── Skills del editor propio: patrones de edición (v3.3.0) ────────────────
_EDITOR_PATRONES = (
    ("renombrar", re.compile(
        r"\b(renombrar|rename|cambia(r| el nombre)( de| la)? (la )?(función|"
        r"funcion|variable|clase|método|metodo))\b", re.IGNORECASE)),
    ("añadir_import", re.compile(
        r"\b(a[nñ]ad(i|ir)|importar|import|agregar)\s+(el\s+|la\s+)?"
        r"(import|módulo|modulo|librería|libreria|paquete)\b", re.IGNORECASE)),
    ("refactorizar_clase", re.compile(
        r"\b(refactoriza(r)?|reestructura|rdivide|extraer\s+clase|"
        r"reorganiza(r)?)\b.*\b(clase|class|módulo|modulo)\b", re.IGNORECASE)),
    ("añadir_funcion", re.compile(
        r"\b(a[nñ]ade?|a[nñ]adir|crear?|agrega(r)?)\s+(una?\s+)?"
        r"(función|funcion|función nueva|nueva funci|nuevo m[ée]todo|"
        r"m[ée]todo)\b", re.IGNORECASE)),
    ("corregir_error", re.compile(
        r"\b(arregla|r|corrige|fix|bug|error|fallo|excepci[oó]n)\b",
        re.IGNORECASE)),
)


def _editor_clasificar_tarea(tarea: str) -> str:
    """Clasifica una tarea de edición en un patrón conocido (v3.3.0).

    Devuelve uno de: 'renombrar', 'añadir_import', 'refactorizar_clase',
    'añadir_funcion', 'corregir_error' o 'general'.
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
    """Guarda/actualiza un skill con el patrón de edición exitoso (v3.3.0).

    Idempotente por nombre (`editor-<patrón>`): si ya existe se actualiza.
    Nunca lanza excepciones (los errores de memoria solo avisan).
    """
    try:
        return _skill_guardar(
            nombre=f"editor-{patron}",
            consulta=tarea or f"editar {archivo}",
            pasos=[{
                "descripcion": (f"Edición '{patron}' aplicada con éxito "
                                f"sobre {archivo}"),
                "accion": "editor_propio",
                "estrategia": estrategia,
            }],
            contexto={"archivo": archivo, "patron": patron,
                      "estrategia": estrategia},
            descripcion=f"Patrón de edición del editor propio: {patron}")
    except Exception as exc:                   # pragma: no cover
        depurar(f"[skills-editor] No se pudo guardar el skill: {exc}")
        return None


def _skill_editor_estrategia(tarea: str, umbral: float = 0.6) -> Optional[str]:
    """Busca un skill de edición previo y devuelve su estrategia (v3.3.0).

    Permite que el editor propio aplique directamente la estrategia que ya
    funcionó para tareas similares, sin pasar por el proveedor de IA.
    Solo se aceptan skills de editor no archivados y con confiabilidad >= 0.6.
    """
    try:
        skill = _skill_buscar(f"editor {(tarea or '').strip()}", umbral=umbral)
    except Exception as exc:
        depurar(f"[skills-editor] Búsqueda falló: {exc}")
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


def _skill_registrar_exito(skill_id: int) -> float:
    """Refuerza un skill tras un uso exitoso. Devuelve la nueva confiabilidad.

    Con 3+ usos sin fallos el skill se considera 'confiable' (confiabilidad
    1.0) y el planificador lo prioriza.
    """
    ahora = time.strftime("%Y-%m-%dT%H:%M:%S")
    _db_ejecutar(
        "UPDATE skills SET usos = usos + 1, ultimo_exito = ?, "
        "confiabilidad = MIN(1.0, confiabilidad + 0.15) WHERE id = ?",
        (ahora, skill_id))
    _db_ejecutar(
        "UPDATE skills SET confiabilidad = 1.0 "
        "WHERE id = ? AND usos >= 3 AND fallos = 0", (skill_id,))
    filas = _db_query("SELECT confiabilidad FROM skills WHERE id = ?",
                      (skill_id,))
    return float(filas[0]["confiabilidad"]) if filas else 0.5


def _skill_registrar_fallo(skill_id: int) -> float:
    """Penaliza un skill tras un fallo. Devuelve la nueva confiabilidad.

    A partir de 2 fallos la confiabilidad cae por debajo de 0.4 y el skill
    queda marcado para revisión por el curador/agente.
    """
    _db_ejecutar(
        "UPDATE skills SET fallos = fallos + 1, "
        "confiabilidad = MAX(0.0, confiabilidad - 0.25) WHERE id = ?",
        (skill_id,))
    filas = _db_query("SELECT confiabilidad FROM skills WHERE id = ?",
                      (skill_id,))
    return float(filas[0]["confiabilidad"]) if filas else 0.5


def _skill_similitud(texto_a: str, texto_b: str) -> float:
    """Similitud [0..1] entre dos textos.

    Usa embeddings (coseno) si sentence-transformers está disponible; si no,
    cae a similitud Jaccard de palabras (fallo elegante, cero dependencias).
    """
    modelo = _modelo_embeddings()
    if modelo is not None:
        try:
            import numpy as _np
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
    # Contención: captura frases donde una contiene a la otra ("... ya",
    # "... ahora"), que Jaccard penaliza en exceso. Exige >= 2 palabras en
    # común para evitar falsos positivos con consultas muy cortas.
    contencion = (interseccion / min(len(palabras_a), len(palabras_b))
                  if interseccion >= 2 else 0.0)
    return min(1.0, max(jaccard, contencion))


def _skill_buscar(consulta: str, umbral: float = 0.75) -> Optional[dict]:
    """Busca el skill activo más similar a ``consulta``.

    Compara con similitud semántica (embeddings o Jaccard como fallback)
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
    """Genera un skill a partir de una tarea completada con éxito.

    Extrae los pasos clave de ``resultados`` (del planificador). Si hay
    proveedor de IA disponible, pide una descripción breve; si no, construye
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
      - exito → refuerza el skill similar existente o genera uno nuevo.
      - fallo → penaliza el skill similar (queda marcado para revisión).
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
            return int(skill_previo["id"])
        nuevo_id = _skill_generar(consulta, resultados, raiz)
        if nuevo_id is not None:
            info(f"[aprendizaje] Nuevo skill guardado: "
                 f"{_skill_normalizar_nombre(consulta)} (#{nuevo_id})")
        return nuevo_id

    if skill_previo is not None:
        conf = _skill_registrar_fallo(int(skill_previo["id"]))
        aviso(f"[aprendizaje] Skill #{skill_previo['id']} marcado para "
              f"revision (confiabilidad {conf:.2f})")
        return int(skill_previo["id"])
    return None


def _cola_encolar(skill_id: int) -> int:
    """Encola un skill para ejecución en segundo plano por el daemon."""
    return _db_insert(
        "INSERT INTO cola (skill_id, estado, fecha) VALUES (?, 'pendiente', ?)",
        (skill_id, time.strftime("%Y-%m-%dT%H:%M:%S")))


# ─── Curador autónomo ──────────────────────────────────────────────────────
CURADOR_DIAS_SIN_USO = 30          # skills sin uso > 30 días → archivados
CURADOR_UMBRAL_FUSION = 0.90       # similitud mínima para fusionar skills
CLAVE_CURADOR_ULTIMA = "curador_ultima_ejecucion"


def _curador_ejecutar(dias_sin_uso: int = CURADOR_DIAS_SIN_USO,
                      umbral_fusion: float = CURADOR_UMBRAL_FUSION) -> dict:
    """Ejecuta una pasada del curador. Devuelve un resumen de acciones.

    Acciones:
      - Archiva skills activos cuyo ultimo_exito (o creado) sea anterior a
        ``dias_sin_uso`` días.
      - Fusiona pares de skills muy similares (sim >= ``umbral_fusion``):
        conserva el más usado sumando usos/fallos y archiva el otro.
      - Notifica por la CLI los skills con baja confiabilidad (revisión).
    """
    _db_init()
    acciones = {"archivados": [], "fusiones": [], "revision": []}
    ahora = datetime.datetime.now()

    def _antiguedad_dias(valor):
        if not valor:
            return dias_sin_uso + 1.0     # nunca usado → candidato
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

    # 2) Fusionar skills muy similares entre sí.
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

    # 3) Notificar skills marcados para revisión (muchos fallos).
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


# ─── Daemon: proceso en segundo plano (--daemon) ───────────────────────────
DAEMON_INTERVALO_HORAS_DEFECTO = 168     # curador cada 7 días
DAEMON_PAUSA_SEGUNDOS = 60               # frecuencia de sondeo del bucle


def _daemon_tick(intervalo_horas: int = DAEMON_INTERVALO_HORAS_DEFECTO,
                 ahora=None) -> dict:
    """Una iteración del daemon (función aislada para facilitar los tests).

    Ejecuta el curador si ha pasado ``intervalo_horas`` desde su última
    pasada (registrada en contexto_kv) y procesa la cola de skills
    pendientes marcándolos como 'hecho' (o 'descartado').
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
                + skill["nombre"] + "' listo para ejecución en segundo "
                "plano (" + str(len(skill.get("pasos") or [])) + " pasos)")
        _db_ejecutar("UPDATE cola SET estado = 'hecho' WHERE id = ?",
                     (tarea["id"],))
        resultado["procesados"].append(skill["id"])
    return resultado


def _daemon_bucle(intervalo_horas: int = DAEMON_INTERVALO_HORAS_DEFECTO,
                  pausa_segundos: int = DAEMON_PAUSA_SEGUNDOS) -> None:
    """Bucle principal del daemon (`snapcontext --daemon`)."""
    ok("Daemon iniciado (curador cada " + str(intervalo_horas)
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
# True). Con --no-confirmar todas las preguntas se omiten (modo automático).
CONFIRMAR_ACCIONES = True


def _cargar_permisos() -> dict:
    """Devuelve las preferencias guardadas en ~/.snapcontext/permisos.json.

    Formato: {"<tipo>": "siempre" | "nunca"} para cada tipo de acción
    ("editar", "ejecutar", "consultar", ...). Archivo corrupto → {}.
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

    True → "siempre" permitido · False → "nunca" · None → sin preferencia.
    Lo usa el modo autónomo (--auto), que no puede preguntar pero sí debe
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
    """Pide permiso al usuario antes de una acción sensible.

    - Muestra un resumen (tipo, descripción y detalles opcionales).
    - Respeta las preferencias guardadas en permisos.json:
      "siempre" → permite sin preguntar; "nunca" → deniega sin preguntar.
    - Pregunta ``¿Permitir esta acción? (s/n/t/a)`` donde:
        s → permitir solo esta vez · n → saltar esta vez
        t → permitir TODAS las de este tipo (se guarda)
        a → no permitir NINGUNA de este tipo (se guarda)

    Devuelve True si la acción está permitida. Con confirmaciones desactivadas
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

    exito("── Permiso requerido " + "─" * 30)
    _emitir(sys.stdout, f"  tipo        : {tipo}")
    _emitir(sys.stdout, f"  acción      : {descripcion}")
    if detalles:
        for linea in str(detalles).splitlines()[:6]:
            _emitir(sys.stdout, f"  detalle     : {linea}")
    while True:
        try:
            eleccion = input(_pintar(
                "¿Permitir esta acción? "
                "[s]í · [n]o · [t]odos este tipo · [a]nular todas (s/n/t/a): ",
                _AMARILLO)).strip().lower()
        except EOFError:
            aviso("Sin entrada disponible; acción denegada por seguridad.")
            return False
        if eleccion in ("s", "si", "sí", "y", "yes"):
            return True
        if eleccion in ("n", "no"):
            aviso("Acción denegada por el usuario.")
            return False
        if eleccion in ("t", "todos", "todo"):
            _guardar_permiso(tipo, "siempre")
            exito(f"Se recordará: '{tipo}' siempre permitido "
                  f"({PERMISOS_PATH}). Usa --init o borra el archivo para "
                  "restaurar las preguntas.")
            return True
        if eleccion in ("a", "anular", "nunca"):
            _guardar_permiso(tipo, "nunca")
            aviso(f"Se recordará: '{tipo}' nunca permitido ({PERMISOS_PATH}).")
            return False
        aviso("Opción no válida; responde s, n, t o a.")


# ---------------------------------------------------------------------------
# MCP (Model Context Protocol): herramientas para el agente — v0.14.0
# ---------------------------------------------------------------------------
MCP_TOOLS_PATH = CONFIG_DIR / "mcp_tools.json"

# Registro de herramientas predefinidas. Cada entrada describe la herramienta
# (para que el agente/usuario sepa cómo usarla) y si requiere permiso.
HERRAMIENTAS_PREDEFINIDAS = {
    "grep": {
        "descripcion": "Busca un patrón en el código (rg/grep/findstr).",
        "parametros": {"patron": "str", "directorio": "str='.'"},
        "requiere_permiso": False,          # solo lectura
    },
    "read_file": {
        "descripcion": "Lee un archivo completo o un rango de líneas.",
        "parametros": {"ruta": "str", "linea_inicio": "int?", "linea_fin": "int?"},
        "requiere_permiso": False,          # solo lectura
    },
    "list_files": {
        "descripcion": "Lista archivos de una carpeta, con filtro de extensión.",
        "parametros": {"directorio": "str='.'", "extensiones": "list?",
                       "max_archivos": "int=200"},
        "requiere_permiso": False,          # solo lectura
    },
    "ast": {
        "descripcion": "Analiza un .py y extrae imports, clases y funciones.",
        "parametros": {"ruta": "str"},
        "requiere_permiso": False,          # solo lectura
    },
    # v1.4.0: análisis sintáctico multi-lenguaje (tree-sitter) y búsqueda
    # semántica integrada en el sistema de herramientas MCP.
    "ast_avanzado": {
        "descripcion": "Análisis sintáctico multi-lenguaje con tree-sitter "
                       "(funciones, clases, imports y llamadas); sin "
                       "tree-sitter usa ast de Python.",
        "parametros": {"ruta": "str"},
        "requiere_permiso": False,          # solo lectura
    },
    "semantic_search": {
        "descripcion": "Búsqueda semántica por embeddings; devuelve los "
                       "fragmentos/archivos más relevantes para una consulta.",
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
        "descripcion": "Ejecuta cualquier comando shell (confirmación estricta).",
        "parametros": {"comando": "str", "directorio": "str='.'",
                       "background": "bool=False",
                       "capture_output": "bool=True"},
        "requiere_permiso": True,
    },
    "execute_command_status": {
        "descripcion": "Consulta el estado de un comando lanzado en segundo plano "
                       "(devuelve stdout/stderr/código si terminó).",
        "parametros": {"pid": "int"},
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
    corrupto o entradas inválidas se ignoran con aviso (sin romper nada).
    """
    herramientas = {nombre: dict(cfg)
                    for nombre, cfg in HERRAMIENTAS_PREDEFINIDAS.items()}
    try:
        if MCP_TOOLS_PATH.is_file():
            datos = json.loads(MCP_TOOLS_PATH.read_text(encoding="utf-8"))
            for cruda in datos.get("tools", []) if isinstance(datos, dict) else []:
                nombre = str(cruda.get("nombre") or "").strip()
                comando = str(cruda.get("comando") or "").strip()
                if not nombre or not comando:
                    aviso(f"[mcp] Herramienta de usuario inválida ignorada: "
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
    return herramientas


# --- Implementaciones de las herramientas (resultados estructurados) -------
def _tool_grep(patron: str, directorio: str = ".",
               max_resultados: int = 50) -> dict:
    """Herramienta `grep`: busca un patrón en el código del proyecto."""
    if not patron:
        return {"ok": False, "error": "falta el patrón de búsqueda"}
    herramienta = _herramienta_busqueda()
    if herramienta is None:
        return {"ok": False, "error": "sin buscador disponible (rg/grep/findstr)"}
    if herramienta == "rg":
        comando = f'rg -n -i --max-count 5 "{patron}"'
    elif herramienta == "grep":
        comando = f'grep -rn -i -m 5 "{patron}" .'
    else:
        comando = f'findstr /s /n /i "{patron}" *.py *.dart *.js *.ts *.go *.rs'
    codigo, stdout, stderr = _ejecutar_comando(comando, directorio, timeout=60)
    lineas = [l for l in (stdout or "").splitlines() if l.strip()]
    return {"ok": codigo == 0 or bool(lineas),
            "buscador": herramienta, "total": len(lineas),
            "coincidencias": lineas[:max_resultados],
            "error": None if (codigo == 0 or lineas) else stderr.strip()}


def _tool_read_file(ruta: str, linea_inicio: Optional[int] = None,
                    linea_fin: Optional[int] = None) -> dict:
    """Herramienta `read_file`: lee un archivo completo o un rango de líneas."""
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
                "error": f"sintaxis inválida: {exc}"}
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
    codigo, salida, stderr = _ejecutar_comando(comando, directorio, timeout=60)
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
      ``pid`` para consultarlo después.
    - ``capture_output=False`` muestra la salida en tiempo real (en su lugar
      ``stdout``/``stderr`` quedan vacíos).

    Requiere confirmación estricta (se valida en el dispatcher).
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


# --- Herramientas avanzadas (v1.4.0): tree-sitter + búsqueda semántica ------
# Tipos de nodo tree-sitter por categoría (nombres comunes entre gramáticas).
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
    """Adivina el nombre de gramática tree-sitter para ``ruta``."""
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


# ─── Detección de lenguaje por contenido (v3.3.0) ──────────────────────────
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

    Se usa como refuerzo cuando la extensión es ambigua o desconocida
    (p. ej. scripts sin extensión, archivos generados, proyectos mixtos).
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
    """Detecta el lenguaje de ``ruta`` combinando extensión y contenido.

    Prioridad: extensión conocida → heurística de contenido → None.
    Más robusto que la detección solo por extensión en proyectos mixtos.
    """
    por_extension = _lenguaje_tree_sitter(ruta)
    if por_extension:
        return por_extension
    if contenido is None:
        leido = _leer_archivo(ruta)
        contenido = leido or ""
    return _detectar_lenguaje_contenido(contenido)



def _extraer_simbolos_ts(arbol, lenguaje: str) -> dict:
    """Recorre el árbol tree-sitter y extrae funciones/clases/imports/llamadas."""
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

    Análisis sintáctico multi-lenguaje con **tree-sitter** si está instalado
    (`pip install snapcontext[mcp_avanzado]`). Si no, hace fallback al módulo
    `ast` de la stdlib (solo para archivos Python). Nunca lanza excepciones.
    """
    contenido = _leer_archivo(ruta)
    if contenido is None:
        return {"ok": False, "ruta": ruta, "error": "no se pudo leer"}
    lenguaje = _lenguaje_tree_sitter(ruta)

    # 1) Intento con tree-sitter (multi-lenguaje).
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
        except Exception as exc:                 # gramática ausente, API distinta...
            depurar(f"[ast_avanzado] tree-sitter falló ({exc}); fallback a ast.")

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

    Búsqueda semántica por embeddings integrada en el sistema MCP: el agente
    puede usarla automáticamente como contexto. Falla elegantemente si el
    extra `embeddings` no está instalado.
    """
    if not consulta.strip():
        return {"ok": False, "error": "falta la consulta de búsqueda"}
    if not _embeddings_disponibles():
        return {"ok": False, "consulta": consulta,
                "error": "búsqueda semántica no disponible; instala el extra "
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
        else:
            # Herramienta de usuario definida en mcp_tools.json → comando.
            resultado = _tool_execute_command(cfg["comando"],
                                              str(argumentos.get("directorio",
                                                                 ".")))
    except Exception as exc:                    # blindaje del agente
        resultado = {"ok": False, "error": f"excepción: {exc}"}
    return {"ok": bool(resultado.get("ok")), "herramienta": nombre,
            "resultado": resultado}


def _entero_opcional(valor) -> Optional[int]:
    """Convierte a int o devuelve None (para argumentos de herramientas)."""
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _formatear_resultado_mcp(llamada: dict, max_lineas: int = 40) -> str:
    """Convierte el resultado de una llamada MCP en texto legible."""
    if not llamada.get("ok"):
        return f"✖ {llamada.get('herramienta', 'herramienta')}: " \
               f"{llamada.get('error', 'fallo')}"
    res = llamada.get("resultado", {})
    partes: List[str] = []
    for clave, valor in res.items():
        if clave in ("contenido", "diff") and isinstance(valor, str):
            lineas = valor.splitlines()
            muestra = "\n".join(lineas[:max_lineas])
            extra = f"\n… (+{len(lineas) - max_lineas} líneas)" \
                if len(lineas) > max_lineas else ""
            partes.append(f"{clave}:\n{muestra}{extra}")
        elif isinstance(valor, list):
            muestra = ", ".join(map(str, valor[:20]))
            extra = " …" if len(valor) > 20 else ""
            partes.append(f"{clave} ({len(valor)}): {muestra}{extra}")
        else:
            partes.append(f"{clave}: {valor}")
    return "\n".join(partes) or "(sin datos)"


def _contexto_automatico_mcp(mensaje: str, max_llamadas: int = 2) -> str:
    """Uso automático de herramientas de solo lectura según el mensaje.

    Heurística ligera: si el usuario pregunta dónde está algo, el estado del
    repo o qué archivos hay, se ejecutan hasta ``max_llamadas`` herramientas
    de solo lectura y se devuelve un bloque de contexto (str) para añadir al
    prompt del proveedor. Cadena vacía si no aplica.
    """
    texto = mensaje.lower()
    llamadas: List[tuple] = []

    if any(p in texto for p in ("busca ", "buscar ", "dónde está",
                                "donde esta", "grep", "quién usa",
                                "quien usa")):
        # Términos demasiado genéricos para usar como patrón de búsqueda.
        paradas = {"busca", "buscar", "dónde", "donde", "está", "esta",
                   "quién", "quien", "usa", "usan", "usado", "usar", "usos"}
        candidatos = [p for p in re.findall(r"\w+", mensaje)
                      if len(p) >= 3 and p.lower() not in paradas]
        if candidatos:
            # El término más largo suele ser el identificador relevante.
            llamadas.append(("grep",
                             {"patron": max(candidatos, key=len)}))
    if any(p in texto for p in ("estado de git", "git status", "sin commitear",
                                "cambios pendientes")):
        llamadas.append(("git_status", {}))
    if any(p in texto for p in ("lista los archivos", "list_files",
                                "qué archivos hay", "que archivos hay")):
        llamadas.append(("list_files", {"max_archivos": 50}))

    bloques: List[str] = []
    for nombre, argumentos in llamadas[:max_llamadas]:
        llamada = _ejecutar_herramienta_mcp(nombre, argumentos)
        bloques.append(f"[{nombre}] "
                       + _formatear_resultado_mcp(llamada, max_lineas=15))
    return "\n".join(bloques)


# ---------------------------------------------------------------------------
# Memoria de proyecto (CLAUDE.md / SNAPCONTEXT.md) — v0.15.0
# ---------------------------------------------------------------------------
NOMBRES_MEMORIA = ("CLAUDE.md", "SNAPCONTEXT.md")
MEMORIA_MAX_CARACTERES = 6000

# Contexto persistente del proyecto cargado al inicio (cadena vacía si no hay
# memoria). La rellenan flujo_principal, --chat y --plan.
MEMORIA_PROYECTO = ""


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
        contenido = contenido[:max_caracteres] + "\n\n… (recortado)"
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
        f"Generada por SnapContext v{VERSION} (modo básico, sin IA).\n\n"
        "## Objetivo\n\n(Describe aquí para qué sirve este proyecto.)\n\n"
        f"## Tecnologías\n\n- Tipo de proyecto detectado: **{tipo}**\n"
        + ("- Manifiestos encontrados: " + ", ".join(manifiestos) + "\n"
           if manifiestos else "- Sin manifiestos detectados.\n")
        + "\n## Estructura\n\nArchivos principales:\n"
        + "".join(f"- {a}\n" for a in archivos[:20])
        + "\n## Convenciones\n\n"
          "- (Describe convenciones de estilo y ramas.)\n\n"
          "## Comandos útiles\n\n"
          "- (Describe cómo ejecutar tests/build.)\n")


def _generar_claude_md(proveedor: Optional[str] = None,
                       modelo: Optional[str] = None,
                       directorio: str = ".") -> Path:
    """Genera un CLAUDE.md inicial escaneando el proyecto (``--init-claude``).

    Usa el proveedor de IA para redactar el contenido; si falta clave/librería
    o la llamada falla, cae a una plantilla básica offline. Devuelve la ruta
    escrita. Si ya existía memoria, pide confirmación antes de sobreescribir.
    """
    raiz = Path(directorio).resolve()
    destino = _buscar_claude_md(str(raiz)) or (raiz / "CLAUDE.md")

    # 1) Escaneo local: tipo de proyecto, estructura y estado git (vía MCP).
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
        "Eres un asistente que documenta proyectos. Analiza esta información "
        "de un proyecto y genera el contenido de un archivo CLAUDE.md: la "
        "memoria persistente de un agente de código.\n\n"
        f"Tipo de proyecto detectado: {tipo}\n"
        f"Estado git: "
        f"{json.dumps(estado_git.get('resultado', {}), ensure_ascii=False)}\n"
        f"Estructura de archivos:\n{estructura}\n\n"
        "Devuelve SOLO el contenido markdown del archivo, con estas secciones:\n"
        "# <nombre del proyecto>\n## Objetivo\n## Tecnologías\n"
        "## Estructura\n## Convenciones\n## Comandos útiles\n"
        "Sé concreto y breve (máximo ~80 líneas).")

    contenido = ""
    preferencias = cargar_configuracion()
    proveedor = proveedor or preferencias.get("provider") or PROVEEDOR_DEFECTO
    try:
        contenido = _enviar_al_proveedor(proveedor, modelo,
                                         [{"role": "user", "content": prompt}])
        info(f"Contenido generado con {PROVEEDORES[proveedor]['nombre']}.")
    except RuntimeError as exc:
        aviso(f"Sin generación por IA ({str(exc).splitlines()[0]}); "
              "se usará una plantilla básica.")
    if not contenido.strip():
        contenido = _plantilla_claude_md_basica(str(raiz))

    # 2) Confirmación si se va a sobreescribir una memoria existente.
    if destino.exists() and not _confirmar_accion(
            f"sobreescribir {destino.name}", tipo="editar",
            detalles=f"tamaño actual: {destino.stat().st_size} bytes"):
        aviso("Operación cancelada; no se modificó la memoria.")
        return destino

    destino.write_text(contenido.strip() + "\n", encoding="utf-8")
    exito(f"Memoria de proyecto creada: {destino}")
    return destino


def _actualizar_claude_md_automatico(resumen_tarea: str,
                                     directorio: str = ".") -> bool:
    """Tras una tarea significativa, propone actualizar la memoria (opcional).

    Pide confirmación; si se acepta, el proveedor reescribe la memoria
    incorporando el resumen de lo aprendido. Solo actúa si ya existe memoria:
    la creación inicial es responsabilidad de ``--init-claude``.
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
        "Actualiza esta memoria de proyecto incorporando la información nueva. "
        "Mantén el formato y las secciones; devuelve SOLO el markdown final.\n\n"
        f"--- MEMORIA ACTUAL ---\n{actual or '(vacía)'}\n\n"
        f"--- LO APRENDIDO EN LA ÚLTIMA TAREA ---\n{resumen_tarea}\n")
    preferencias = cargar_configuracion()
    try:
        nuevo = _enviar_al_proveedor(preferencias.get("provider")
                                     or PROVEEDOR_DEFECTO, None,
                                     [{"role": "user", "content": prompt}])
    except RuntimeError as exc:
        aviso(f"No se pudo actualizar la memoria: {str(exc).splitlines()[0]}")
        return False
    if not nuevo.strip():
        aviso("El proveedor devolvió contenido vacío; memoria sin cambios.")
        return False
    camino.write_text(nuevo.strip() + "\n", encoding="utf-8")
    exito(f"Memoria actualizada: {camino}")
    return True


# ---------------------------------------------------------------------------
# Embeddings locales: búsqueda semántica de archivos — v1.1.0
# ---------------------------------------------------------------------------
MENSAJE_EMBEDDINGS_FALTANTE = (
    "La búsqueda semántica requiere la librería 'sentence-transformers'.\n"
    "Instálala con:  pip install snapcontext[embeddings]\n"
    "  (descarga torch; primera ejecución descarga el modelo "
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
CHUNK_CARACTERES = 2000          # ~512 tokens con heurística de 4 chars/token

_MODELO_EMBEDDINGS = None        # singleton del modelo cargado


def _embeddings_disponibles() -> bool:
    """True si sentence-transformers está instalado."""
    return SentenceTransformer is not None


def _modelo_embeddings():
    """Devuelve el modelo de embeddings (singleton) o None si no está instalado.

    Si ``sc._MODELO_EMBEDDINGS`` ya fue establecido (p. ej. por tests o por una
    carga previa), se reutiliza tal cual.
    """
    global _MODELO_EMBEDDINGS
    if _MODELO_EMBEDDINGS is not None:
        return _MODELO_EMBEDDINGS
    if SentenceTransformer is None:
        return None
    try:
        _MODELO_EMBEDDINGS = SentenceTransformer(MODELO_EMBEDDINGS_NOMBRE)
    except Exception as exc:            # sin red para descargar el modelo, etc.
        aviso(f"No se pudo cargar el modelo de embeddings: {exc}")
        return None
    return _MODELO_EMBEDDINGS


def _calcular_embeddings(textos: List[str]) -> List[List[float]]:
    """Calcula embeddings para una lista de textos (lista de vectores).

    Lanza RuntimeError con MENSAJE_EMBEDDINGS_FALTANTE si la librería no está
    disponible. Normaliza los vectores a longitud 1 para que la similitud de
    coseno sea un simple producto escalar.
    """
    modelo = _modelo_embeddings()
    if modelo is None:
        raise RuntimeError(MENSAJE_EMBEDDINGS_FALTANTE)
    vectores = modelo.encode(textos, normalize_embeddings=True)
    return [[float(x) for x in vector] for vector in vectores]


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

    Corta por líneas para no partir sentencias a mitad y registra la línea de
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
    if linea_actual == 0:               # archivo vacío
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
    """Ruta del índice en disco para ``directorio`` (hash de la ruta absoluta)."""
    clave = _hash_texto(str(Path(directorio).resolve()))
    return INDICE_DIR / f"{clave}.json"


def _cargar_indice(directorio: str) -> dict:
    """Lee el índice de embeddings de ``directorio`` ({} si no existe)."""
    camino = _ruta_indice(directorio)
    try:
        if camino.is_file():
            datos = json.loads(camino.read_text(encoding="utf-8"))
            if isinstance(datos, dict) and "fragmentos" in datos:
                return datos
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"Índice de embeddings corrupto ({camino}): {exc}")
    return {}


def _guardar_indice(directorio: str, indice: dict) -> bool:
    """Persiste el índice en ~/.snapcontext/index/<hash>.json."""
    try:
        INDICE_DIR.mkdir(parents=True, exist_ok=True)
        _ruta_indice(directorio).write_text(
            json.dumps(indice, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError as exc:
        aviso(f"No se pudo guardar el índice: {exc}")
        return False


def _es_ignorado(relativo: str, patrones: List[str]) -> bool:
    """True si ``relativo`` (ruta POSIX relativa) casa con algún patrón."""
    partes = relativo.split("/")
    for patron in patrones:
        if fnmatch.fnmatch(relativo, patron) or fnmatch.fnmatch(
                partes[-1], patron):
            return True
        # Patrón de directorio: ignorar todo lo que cuelga de él.
        if any(fnmatch.fnmatch(parte, patron) for parte in partes):
            return True
    return False


def _hash_proyecto(raiz) -> str:
    """Computa un hash que representa el estado actual del proyecto.

    Recorre los archivos de código (misma lógica que ``_indexar_proyecto`` pero
    sin calcular embeddings) y devuelve un hash combinado de todos los hashes de
    contenido. Muy rápido comparado con el indexado completo.
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
    """Indexa el proyecto: embeddings por fragmento de cada archivo de código.

    - Escanea recursivamente respetando .gitignore y ``CARPETAS_IGNORADAS``.
    - Divide cada archivo en fragmentos (~512 tokens) y calcula su embedding
      con el modelo local (all-MiniLM-L6-v2).
    - Cache por hash de contenido: los archivos sin cambios reutilizan los
      embeddings del índice previo.

    Lanza RuntimeError si los embeddings no están disponibles.
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
        raise RuntimeError("No se encontraron archivos de código para indexar.")

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

    # 3) Calcular embeddings de los fragmentos nuevos en lote.
    if nuevos_textos:
        aviso(f"[embeddings] Calculando embeddings de {len(nuevos_textos)} "
              f"fragmento(s) nuevo(s)…")
        vectores = _calcular_embeddings(nuevos_textos)
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
    """Devuelve el índice del proyecto; lo crea o reindexa si ha cambiado.

    Invalida el caché automáticamente cuando el proyecto cambia (se compara el
    ``hash_proyecto`` almacenado con el hash actual) y reindexa con aviso.
    """
    indice = _cargar_indice(directorio)
    if indice.get("fragmentos"):
        hash_actual = _hash_proyecto(Path(directorio).resolve())
        if indice.get("hash_proyecto") == hash_actual:
            return indice
        aviso("[embeddings] El proyecto ha cambiado; reindexando…")
    info("[embeddings] Indexando el proyecto (primera vez o índice vacío)…")
    return _indexar_proyecto(directorio)


def _buscar_semanticamente(consulta: str, directorio: str = ".",
                           max_resultados: int = 20) -> List[dict]:
    """Búsqueda semántica: fragmentos más similares a ``consulta``.

    Devuelve una lista ordenada por similitud::

        [{"archivo", "linea_inicio", "similitud", "texto"}]

    Lanza RuntimeError si los embeddings no están disponibles.
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
    """Selecciona archivos relevantes por similitud semántica.

    Agrupa las similitudes por archivo (sumando sus fragmentos), filtra por
    ``umbral`` y devuelve hasta ``max_archivos`` rutas. Si no llegan a
    ``max_archivos``, rellena con los mejores candidatos de la heurística
    local (``escanear_repositorio``) que no estén ya incluidos.
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
# Editor web y visualización de dependencias — v1.2.0
# ---------------------------------------------------------------------------
# Mapa extensión → lenguaje de Monaco Editor (resaltado de sintaxis).
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
# Extensiones de código consideradas al construir el grafo de dependencias.
_GRP_EXT_DEPS = {
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".dart", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".swift",
}


def _comando_para_monaco(archivo: str) -> str:
    """Devuelve el id de lenguaje de Monaco para ``archivo`` (detección por ext.)."""
    ext = Path(archivo).suffix.lower()
    return _MAPA_LENGUAJE_MONACO.get(ext, "plaintext")


def _extraer_dependencias(contenido: str, lenguaje: str) -> List[str]:
    """Extrae las referencias de importación de ``contenido`` para ``lenguaje``.

    Devuelve una lista ordenada y sin duplicados de módulos/símbolos importados.
    No resuelve a rutas absolutas: eso lo hace :func:`_grafo_dependencias` junto
    con el índice de archivos del proyecto.
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

    Estrategias, en orden: ruta relativa (./foo), extensión directa,
    coincidencia por nombre de archivo (stem) y coincidencia de prefijo de
    carpeta (pagos → pagos/pago_service.dart). Devuelve la ruta POSIX relativa
    o None si no se encuentra ningún candidato en el repo.
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
    """Construye un grafo de dependencias entre archivos de código del proyecto.

    Devuelve {"nodos": [{"id", "etiqueta", "lenguaje"}], "enlaces": [{"origen",
    "destino"}]}. Los enlaces unen archivos del proyecto que se importan entre
    sí. Es la fuente del panel de dependencias de la interfaz web.
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
    """Busca ``tema`` en el código del repositorio (rg/grep/findstr).

    Devuelve una lista de líneas de coincidencia ya formateadas para poder
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
# Códigos ANSI; si el terminal no soporta color (o NO_COLOR está definido), se
# degradan a texto plano. `colorama` se usa solo para inicializar en Windows
# si está disponible; nunca es obligatorio.
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
            pass                                       # sin VT → texto plano
    return True


def _pintar(texto: str, clave: str) -> str:
    """Aplica el color ANSI ``clave`` si los colores están activos."""
    if not _AYUDA_CON_COLOR:
        return texto
    return f"{_ANSI.get(clave, '')}{texto}{_ANSI['reset']}"

# Categorías en orden de aparición; cada opción se muestra una sola vez.
CATEGORIAS_AYUDA = (
    ("Modos de ejecución",
     ("--plan", "--auto", "--editor", "--modo-edicion", "--chat", "--web", "--web-puerto", "--demo",
      "--init", "--init-claude", "--historial", "--historial-limpiar",
      "--diagnostico", "--reparar", "--bienvenida")),
    ("Selección de archivos",
     ("consulta", "--local", "--iniciar-proyecto", "--experto",
      "--vista-previa", "--carpetas", "--max-archivos", "--candidatos")),
    ("Proveedores de IA",
     ("--provider", "--model", "--no-persist")),
    ("Permisos y seguridad",
     ("--confirmar", "--no-confirmar")),
    ("Git y control de versiones",
     ("--git-commit", "--no-git-commit", "--branch")),
    ("Planificador y bucles",
     ("--paralelo", "--max-intentos", "--test-loop", "--server-loop",
      "--manual-loop", "--comando-test", "--dispositivo", "--url-defecto",
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
    ("auto <tarea>", "Ejecuta el planificador autónomo (--plan --auto)."),
)

EJEMPLOS_AYUDA = (
    'snapcontext "el botón de pago no funciona"',
    'snapcontext fix "el botón de pago no funciona"',
    'snapcontext plan "añadir validación al formulario" --auto',
    'snapcontext review "revisar el login"',
    'snapcontext interactive',
    'snapcontext --chat',
    'snapcontext --demo',
    'snapcontext "..." --provider groq --model llama-3.3-70b-versatile',
)


def _invocacion_accion(accion) -> str:
    """Representación compacta de una opción (p. ej. ``--max-archivos N``)."""
    if not accion.option_strings:
        return accion.dest.upper()
    partes = ", ".join(accion.option_strings)
    metavar = accion.metavar
    if not metavar and accion.nargs is None and accion.type is not None:
        metavar = accion.dest.replace("_", "-").upper()
    return f"{partes} {metavar}" if metavar else partes


def action_toma_valor(accion) -> bool:
    """True si la opción espera un valor (no es un flag booleano)."""
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
        "DEEPSEEK_API_KEY / GROQ_API_KEY · OLLAMA_URL · "
        "SNAPCONTEXT_PROVIDER · SNAPCONTEXT_MODELO", "gris",
    ))
    lineas.append("")
    return "\n".join(lineas)



def _mostrar_ayuda_resumida() -> None:
    """Ayuda amigable cuando se ejecuta `snapcontext` sin argumentos (v3.1.1).

    Más corta que --help: comandos de uso común con ejemplos listos para
    copiar y pegar.
    """
    print(_LOGO_SMALL)
    lineas = [
        "Bienvenido a SnapContext — tu asistente de IA con contexto automático.",
        "",
        _pintar("Uso básico:", _CYAN),
        '  snapcontext "describe lo que quieres cambiar"',
        "",
        _pintar("Comandos más útiles:", _CYAN),
        "  snapcontext --bienvenida     Tutorial interactivo de primeros pasos",
        "  snapcontext --init           Configurar claves API y proveedor",
        "  snapcontext --diagnostico    Revisar tu instalación",
        "  snapcontext --reparar        Arreglar una instalación rota",
        "  snapcontext --demo           Demo autónoma (sin API key)",
        "  snapcontext --chat           Conversar con el proveedor de IA",
        "  snapcontext --plan \"tarea\"   Planificar y ejecutar paso a paso",
        "  snapcontext --help           Ayuda completa agrupada",
        "",
        _pintar("Ejemplos:", _CYAN),
        '  snapcontext "el botón de pago no funciona"',
        '  snapcontext "añadir login" --test-loop',
        '  snapcontext "revisar pago" --vista-previa   # solo ver, no editar',
        "",
        _pintar("Sin API key", _CYAN) +
        ": SnapContext usa Ollama local automáticamente (modo offline).",
        "Instala Ollama desde https://ollama.com y ejecuta: ollama pull llama3.2",
        "",
    ]
    print("\n".join(lineas))


class _AyudaAccion(argparse.Action):
    """Muestra la ayuda agrupada por categorías y termina."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):  # noqa: A002
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, valores, opcion=None):
        global _AYUDA_CON_COLOR
        _AYUDA_CON_COLOR = _colores_activos()
        sys.stdout.write(_construir_ayuda(parser))
        parser.exit()


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapcontext",
        add_help=False,   # -h/--help se gestionan con _AyudaAccion (agrupada)
        description=_LOGO_SMALL + (
            "SnapContext — Asistente de IA para desarrollo con Flutter/Supabase. "
            "Escanea el repo, el proveedor de IA (Gemini, Ollama, DeepSeek o "
            "Groq) elige los archivos relevantes y Aider realiza los cambios."
        ),
        epilog=(
            "Ejemplos:\n"
            '  snapcontext "el botón de pago no funciona"\n'
            '  snapcontext "añadir índice a la tabla pedidos" --test-loop\n'
            '  snapcontext "revisar login" --vista-previa\n'
            '  snapcontext "revisar pago" --experto\n'
            '  snapcontext "arreglar el checkout" --server-loop\n'
            '  snapcontext "arreglar login" --manual-loop\n'
            '  snapcontext fix "el botón de pago no funciona"\n'
            '  snapcontext review "revisar código"\n'
            '  snapcontext server "iniciar servidor"\n'
            '  snapcontext interactive\n'
            '  snapcontext --chat\n'
            '  snapcontext --historial\n'
            '  snapcontext --demo\n'
            '  snapcontext "..." --provider groq --model llama-3.3-70b-versatile\n'
            "Variables de entorno: clave según --provider (GEMINI_API_KEY / "
            "ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GROQ_API_KEY), OLLAMA_URL "
            "(default localhost:11434), SNAPCONTEXT_PROVIDER y SNAPCONTEXT_MODELO "
            "(opcionales).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "consulta", type=str, nargs="?", default=None,
        help="La tarea a resolver (pásala entre comillas). Omitible con --init.",
    )
    parser.add_argument(
        "--init", action="store_true",
        help="Asistente de configuración inicial: claves API, proveedor y "
             "modelo favorito (se guarda en ~/.snapcontext/config.json). "
             "Independiente de la consulta y el escaneo.",
    )
    parser.add_argument(
        "--directorio", default=".",
        help="Repositorio donde trabajar (por defecto: raíz git detectada desde "
             "el directorio actual).",
    )
    parser.add_argument(
        "--carpetas", nargs="+", default=None,
        help="Carpetas a escanear (por defecto: lib supabase).",
    )
    parser.add_argument(
        "--max-archivos", type=int, default=MAX_ARCHIVOS_DEFECTO,
        help="Número de archivos que recibe Aider (por defecto: 3).",
    )
    parser.add_argument(
        "--candidatos", type=int, default=MAX_CANDIDATOS_DEFECTO,
        help="Máximo de candidatos que recibe Gemini (por defecto: 80).",
    )
    parser.add_argument(
        "--provider", choices=sorted(PROVEEDORES), default=None,
        help="Proveedor de IA que elige los archivos (gemini | ollama | "
             "deepseek | groq). Si no se indica -y tampoco --local-, se usa el "
             "guardado en ~/.snapcontext/config.json o, si es el primer uso, "
             "se muestra un menú interactivo (questionary); con --no-persist "
             "se fuerza siempre el menú. Env: SNAPCONTEXT_PROVIDER.",
    )
    parser.add_argument(
        "--no-persist", action="store_true",
        help="Ignora la configuracion guardada (~/.snapcontext/config.json) y "
             "fuerza el menú interactivo de proveedor (si no hay --local).",
    )
    parser.add_argument(
        "--model", "--modelo", dest="modelo", default=MODELO_DEFECTO,
        help="Modelo del proveedor. Si no se indica, se usa el modelo por "
             "defecto de cada proveedor (o SNAPCONTEXT_MODELO).",
    )
    parser.add_argument(
        "--local", action="store_true",
        help="Selección local por heurística, sin llamar a Gemini "
             "(útil para probar offline). También desactiva la validación "
             "de carpeta de proyecto.",
    )
    parser.add_argument(
        "--iniciar-proyecto", "--no-validar", dest="iniciar_proyecto",
        action="store_true",
        help="Desactiva por completo la validación de carpeta de proyecto: "
             "trabaja en el directorio actual (o --directorio) aunque esté "
             "vacío. Ideal para empezar un proyecto desde cero.",
    )
    parser.add_argument(
        "--vista-previa", action="store_true",
        help="Solo muestra los archivos seleccionados y sale, sin ejecutar Aider.",
    )
    parser.add_argument(
        "--experto", "--expert", action="store_true",
        help="Modo experto: revisar la selección y añadir/eliminar archivos "
             "antes de ejecutar Aider.",
    )
    parser.add_argument(
        "--aider-opciones", default="",
        help='Opciones extra para Aider entre comillas (p. ej. "--model sonnet").',
    )
    bucle = parser.add_mutually_exclusive_group()
    bucle.add_argument(
        "--test-loop", action="store_true",
        help="Tras Aider ejecuta las pruebas y repite si fallan "
             "(bucle agéntico básico).",
    )
    bucle.add_argument(
        "--server-loop", action="store_true",
        help="Bucle agéntico con servidor Flutter en MODO AUTOMÁTICO: "
             "reintenta hasta --max-intentos y pregunta s/n al usuario.",
    )
    bucle.add_argument(
        "--manual-loop", action="store_true",
        help="Bucle agéntico con servidor Flutter en MODO MANUAL: "
             "el usuario decide en cada paso.",
    )
    parser.add_argument(
        "--comando-test", default=COMANDO_TEST_DEFECTO,
        help='Comando de pruebas del bucle (por defecto: "flutter test").',
    )
    parser.add_argument(
        "--max-iteraciones", type=int, default=MAX_ITERACIONES_TEST_DEFECTO,
        help="Máximo de iteraciones del bucle de pruebas.",
    )
    parser.add_argument(
        "--max-intentos", type=int, default=3,
        help="Intentos máximos del bucle automático --server-loop "
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
        "--depurar", action="store_true", help="Logs de depuración.",
    )
    parser.add_argument(
        "--version", action=_VersionAction, nargs=0,
    )
    parser.add_argument(
        "--setup-path", action="store_true",
        help="Configura automáticamente el PATH del usuario para Windows: añade la "
             "carpeta de ejecutable al PATH persistente. Útil si instalaste con "
             "'pip install snapcontext' sin usar el one-liner. Solo funciona en Windows.",
    )
    parser.add_argument(
        "--diagnostico", action="store_true",
        help="(v3.1.0) Revisa la instalación: Python, paquete, dependencias "
             "opcionales, PATH, proveedor de IA (API key / Ollama) y memoria "
             "SQLite, con resumen en colores y soluciones sugeridas.",
    )
    parser.add_argument(
        "--reparar", action="store_true",
        help="(v3.1.0) Repara una instalación rota: limpia entornos uv "
             "corruptos, reinstala SnapContext con pip, recrea la base de "
             "datos SQLite si está corrupta y ajusta el PATH (Windows).",
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
    parser.add_argument(
        "--demo", action="store_true",
        help="Ejecuta una demo autónoma de SnapContext: crea un proyecto de prueba "
             "temporal, muestra la selección de archivos (--vista-previa --local) y "
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
        help="Muestra las últimas 20 tareas guardadas en ~/.snapcontext/historial.json.",
    )
    parser.add_argument(
        "--historial-limpiar", action="store_true",
        help="Borra el historial persistente (~/.snapcontext/historial.json) y sale.",
    )
    parser.add_argument(
        "--plan", action="store_true",
        help="Modo planificador: pide al proveedor de IA que descomponga la tarea "
             "en pasos (editar/ejecutar/consultar), los muestra para confirmación "
             "y los ejecuta secuencialmente con control continuar/reintentar/saltar. "
             "Requiere consulta.",
    )
    parser.add_argument(
        "--git-commit", action=argparse.BooleanOptionalAction, default=True,
        help="En modo --plan, hace 'git add . && git commit' tras cada paso exitoso "
             "(por defecto: activado; desactivar con --no-git-commit).",
    )
    parser.add_argument(
        "--branch", dest="branch", default=None, metavar="NOMBRE",
        help="En modo --plan, crea y cambia a una rama git nueva antes de ejecutar "
             "los pasos (p. ej. --branch fix/checkout).",
    )
    parser.add_argument(
        "--confirmar", action=argparse.BooleanOptionalAction, default=True,
        help="Pide confirmación (s/n/todos/nunca) antes de acciones sensibles "
             "(pasos del planificador, /run y /edit del chat). Por defecto "
             "activado; desactivar con --no-confirmar para modo automático.",
    )
    parser.add_argument(
        "--init-claude", action="store_true",
        help="Escanea el proyecto (estructura, dependencias, git) y genera una "
             "memoria persistente CLAUDE.md (o SNAPCONTEXT.md) usando el "
             "proveedor de IA; sin conexión usa una plantilla básica.",
    )
    parser.add_argument(
        "--auto", action="store_true", default=False,
        help="Modo autónomo para --plan: salta las confirmaciones paso a paso "
             "(siguiendo respetando permisos.json) y reintenta automáticamente "
             "cada paso fallido hasta 3 veces antes de continuar. Con "
             "--no-confirmar no añade diferencia adicional.",
    )
    parser.add_argument(
        "--paralelo", type=int, default=1, metavar="N",
        help="En modo --plan --auto: ejecuta hasta N pasos sin dependencias "
             "mutuas en paralelo (por defecto 1 = secuencial). Los logs de cada "
             "paso llevan su identificador [paso N]. Los pasos con campo "
             "'dependencias' esperan a que sus dependencias tengan éxito y "
             "las condiciones que referencien resultados de pasos previos o "
             "variables MCP bloquean al paso hasta estar disponibles.",
    )
    parser.add_argument(
        "--editor", choices=["aider", "propio"], default="aider",
        help="Editor a usar para aplicar cambios: 'aider' (por defecto, "
             "requiere Aider instalado) o 'propio' (editor integrado de "
             "SnapContext con soporte de parches unificados y backups automáticos).",
    )
    parser.add_argument(
        "--modo-edicion",
        choices=["sobrescribir", "parche", "auto", "ast"], default="auto",
        help="Estrategia del editor propio: 'auto' (intenta aplicar parche unificado, "
             "fallback a sobrescritura), 'parche' (solo parches unificados), "
             "'sobrescribir' (sobrescritura completa del archivo) o 'ast' "
             "(edición basada en el árbol sintáctico con fallback a sobrescritura).",
    )
    # Aprendizaje autónomo / memoria avanzada (v3.0.0)
    parser.add_argument(
        "--daemon", action="store_true",
        help="Ejecuta el daemon en segundo plano: corre el curador cada "
             "--daemon-intervalo horas y procesa la cola de skills pendientes.",
    )
    parser.add_argument(
        "--daemon-intervalo", type=int,
        default=DAEMON_INTERVALO_HORAS_DEFECTO, metavar="HORAS",
        help="Horas entre pasadas del curador cuando el daemon está activo "
             "(por defecto 168 = 7 días).",
    )
    parser.add_argument(
        "--curador", action="store_true",
        help="Ejecuta una pasada única del curador (archiva skills antiguos, "
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
    # Ayuda agrupada por categorías (-h/--help) — v1.7.
    parser.add_argument(
        "-h", "--help", action=_AyudaAccion,
        help="Muestra esta ayuda agrupada por categorías, con alias y ejemplos.",
    )
    return parser


def _preparar_argv_aliases(argv: Optional[List[str]]) -> List[str]:
    """Convierte el primer token en un alias de comando común.

    Sintaxis ``snapcontext <alias> "mensaje"``:

      - ``fix``         → equivalente a ``--test-loop``
      - ``review``      → equivalente a ``--vista-previa --experto``
      - ``server``      → equivalente a ``--server-loop``
      - ``interactive`` → equivalente a ``--web``

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
    todavía si existen. Prioriza el intérprete Python en uso."""
    candidatos: List[str] = []

    # 1) Carpeta de scripts del intérprete Python en uso (donde pip y
    #    `pip install -e .` registran el comando `snapcontext`). Prioridad máxima.
    try:
        import sysconfig
        candidatos.append(sysconfig.get_path("scripts"))
    except Exception:
        pass

    # 2) Carpeta Scripts hermana de python.exe.
    dir_python = os.path.dirname(sys.executable)
    candidatos.append(os.path.join(dir_python, "Scripts"))

    # 3) Rutas típicas de instalaciones de usuario en Windows.
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        candidatos.append(os.path.join(appdata, "Python", "Scripts"))
    if localappdata:
        candidatos.append(
            os.path.join(localappdata, "Programs", "Python", "Scripts")
        )
        # Python3X: localizaciones con número de versión (p. ej. Python313).
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

    # Eliminar vacíos y duplicados conservando el orden de prioridad.
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
    todavía en el mismo lugar que apunta el sysconfig.

    Si esa carpeta no existe, se devuelve la primera de las demás rutas típicas
    de Python que sí exista. Nunca devuelve el directorio del proyecto actual.
    """
    marcadores = ("snapcontext.exe", "snapcontext", "snapcontext.bat")
    intentadas: List[str] = []

    for c in _candidatos_carpetas_scripts():
        if not os.path.isdir(c):
            intentadas.append(c)
            continue
        # En ejecutables empaquetados la carpeta propia no es de scripts;
        # exigimos ahí el ejecutable para no devolver una carpeta cualquiera.
        if getattr(sys, "frozen", False):
            if any(os.path.exists(os.path.join(c, m)) for m in marcadores):
                return c
            continue
        return c

    if intentadas:
        depurar("--setup-path: rutas probadas sin éxito: " + "; ".join(intentadas))
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
# Diagnóstico y reparación (v3.1.0)
# ---------------------------------------------------------------------------
def _diagnostico_item(nombre: str, ok: bool, detalle: str,
                      solucion: Optional[str] = None) -> bool:
    """Imprime una línea de diagnóstico con color según el estado."""
    if ok:
        exito(f"{nombre}: {detalle}")
    elif solucion:
        aviso(f"{nombre}: {detalle}")
        print(_pintar("    → Solución: " + solucion, _AMARILLO))
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
    """Comprueba la base SQLite y el número de skills.

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
                            "error": "La base de datos está corrupta."}
            except sqlite3.DatabaseError:
                return {"ok": False, "skills": 0,
                        "error": "La base de datos está corrupta."}
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
    """Modo --diagnostico: revisa la instalación y muestra un resumen.

    Comprueba Python, instalación del paquete, dependencias opcionales,
    PATH, proveedor de IA (API key / Ollama) y memoria SQLite.
    Devuelve 0 si todo está OK, 1 si hay errores y 2 si solo hay avisos.
    """
    info("=== SnapContext · Diagnóstico ===")
    problemas = 0
    avisos = 0

    # 1) Python
    version_py = sys.version.split()[0]
    en_path = any(shutil.which(cmd) for cmd in ("python", "python3", "py"))
    if not _diagnostico_item(
            "Python", en_path,
            f"v{version_py} ({sys.executable})" if en_path
            else "no se encontró 'python' en el PATH",
            "Instala Python 3.9+ desde https://python.org y marca "
            "'Add to PATH'"):
        problemas += 1

    # 2) Instalación de SnapContext
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
            print(_pintar("    → Solución: pip install snapcontext "
                          "(o python -m pip install -e .)", _AMARILLO))
            avisos += 1

    # 3) Dependencias opcionales
    for paquete, presente, extra in _comprobar_dependencias_opcionales():
        if presente:
            exito(f"Dependencia '{paquete}': OK.")
        else:
            aviso(f"Dependencia opcional '{paquete}' no instalada.")
            print(_pintar(f"    → Instalar con: {extra}", _AMARILLO))
            avisos += 1

    # 4) PATH
    if snapcontext_en_path():
        exito("PATH: el comando 'snapcontext' es accesible.")
    else:
        aviso("PATH: 'snapcontext' no es accesible como comando global.")
        print(_pintar("    → Solución: ejecuta 'snapcontext --setup-path' "
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
            exito("Proveedor de IA: sin API key, pero Ollama está listo "
                  f"(modo offline con '{ligero}').")
        elif estado_ol["instalado"]:
            aviso("Ollama instalado pero sin modelos descargados.")
            print(_pintar("    → Solución: ollama pull llama3.2", _AMARILLO))
            avisos += 1
        else:
            error("No se encontró una API key ni Ollama.")
            print(_pintar("    → Solución: instala Ollama desde "
                          "https://ollama.com o ejecuta 'snapcontext --init'.",
                          _ROJO))
            problemas += 1

    # 6) Memoria (SQLite + skills)
    memoria = _estado_memoria()
    if memoria["ok"]:
        exito(f"Memoria: base de datos OK ({memoria['skills']} skills).")
    elif memoria["error"] and "corrupta" in (memoria["error"] or ""):
        error(f"Memoria: {memoria['error']}")
        print(_pintar("    → Solución: ejecuta 'snapcontext --reparar'",
                      _ROJO))
        problemas += 1
    else:
        aviso(f"Memoria: {memoria['error']}")
        avisos += 1

    print()
    if problemas:
        error(f"Diagnóstico completado con {problemas} problema(s) y "
              f"{avisos} aviso(s). Ejecuta 'snapcontext --reparar' si lo "
              "necesitas.")
        return 1
    if avisos:
        aviso(f"Diagnóstico completado: todo funcional, {avisos} aviso(s).")
        return 2
    exito("Diagnóstico completado: todo correcto ✔")
    return 0


def _limpiar_entorno_uv_corrupto() -> bool:
    """Elimina carpetas de entorno de 'uv' vacías/corruptas (v3.1.0).

    Un fallo conocido deja entornos vacíos que rompen reintentos.
    Devuelve True si se limpió algo.
    """
    limpio = False
    for carpeta in (CONFIG_DIR / ".venv-uv", CONFIG_DIR / ".venv"):
        try:
            if carpeta.is_dir() and not any(carpeta.iterdir()):
                carpeta.rmdir()
                info(f"Entorno uv vacío eliminado: {carpeta}")
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
        aviso("pip devolvió un error: " +
              ((proc.stderr or proc.stdout or "").strip()[-300:]))
    except (OSError, subprocess.SubprocessError) as exc:
        aviso(f"No se pudo ejecutar pip: {exc}")
    return False


def _reparar_memoria_si_corrupta() -> bool:
    """Recrea la base SQLite si está corrupta. True si quedó operativa."""
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
    # No existe aún: crearla.
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
    base SQLite y añadir la carpeta de scripts al PATH (Windows).
    """
    info("=== SnapContext · Reparación ===")
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
        exito("Reparación completada. Prueba 'snapcontext --diagnostico'.")
        return 0
    error("La reparación terminó con incidencias; revisa los mensajes.")
    return 1


def _tutorial_interactivo() -> int:
    """Tutorial interactivo (--bienvenida): guía de primeros pasos."""
    info("=== SnapContext · Tutorial interactivo ===")
    pasos = [
        ("1. Comprueba tu instalación",
         "  Ejecuta 'snapcontext --version' y 'snapcontext --diagnostico'\n"
         "  para verificar que todo está listo."),
        ("2. Configura tu cerebro",
         "  Sin API key, SnapContext usa Ollama local automáticamente.\n"
         "  Con clave: 'snapcontext --init' guarda tu proveedor favorito."),
        ("3. Tu primera tarea",
         '  En tu proyecto ejecuta:\n'
         '    snapcontext "describe brevemente este proyecto" --vista-previa\n'
         "  Verás qué archivos seleccionaría la IA sin tocar nada."),
        ("4. Deja que trabaje",
         "  Quita --vista-previa y SnapContext usará Aider para editar.\n"
         "  Añade --test-loop para que verifique con tus pruebas."),
        ("5. Aprende más",
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
    exito("¡Tutorial completado! Bienvenido a SnapContext 🎉")
    return 0


def configurar_path() -> int:
    """Configura el PATH del usuario en Windows (--setup-path).

    Es independiente de la consulta: localiza la carpeta de ejecutables, la
    añade al PATH persistente del usuario y sale. Código 0 = éxito.
    """
    if not sys.platform.startswith("win"):
        error("--setup-path solo funciona en Windows.")
        return 1

    info("Configurando el PATH del usuario para Windows...")
    carpeta = _localizar_carpeta_scripts()
    if not carpeta:
        error("No se pudo localizar automáticamente la carpeta de ejecutables "
              "de SnapContext.")
        aviso("Rutas típicas donde suele instalarse el comando 'snapcontext':")
        for r in _candidatos_carpetas_scripts():
            if r:
                aviso("  - " + r)

        # Fallback interactivo: ofrecer indicar la ruta manualmente.
        if _preguntar_si("¿Quieres indicar la carpeta de Scripts manualmente?"):
            try:
                manual = input(
                    _pintar("Ruta de la carpeta Scripts (p. ej. "
                            "C:\\...\\Python313\\Scripts): ", _CYAN)
                ).strip().strip('"').strip("'")
            except EOFError:  # entrada no interactiva → abandonar
                manual = ""
            if manual and os.path.isdir(manual):
                carpeta = manual
            else:
                aviso("Ruta no válida o inexistente: no se modificará el PATH.")

        if not carpeta or not os.path.isdir(carpeta):
            aviso("Añade la ruta de Scripts manualmente al PATH del usuario "
                  "o vuelve a ejecutar --setup-path tras instalar SnapContext.")
            aviso("SnapContext seguirá funcionando con: 'python -m snapcontext'")
            return 1

    path_actual = os.environ.get("PATH", "")
    if carpeta in [p for p in path_actual.split(";") if p]:
        exito("'" + carpeta + "' ya está en el PATH del usuario (sin cambios).")
        return 0

    nuevo = carpeta + ";" + path_actual
    os.environ["PATH"] = nuevo

    if _guardar_path_windows(nuevo):
        exito("'" + carpeta + "' añadido al PATH persistente del usuario.")
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

    La carpeta ``src``/``tests`` hace que la auto-detección clasifique la demo
    como proyecto Python y que el escaneo (--local) encuentre los archivos.
    """
    (directorio / "src").mkdir(parents=True, exist_ok=True)
    (directorio / "tests").mkdir(parents=True, exist_ok=True)
    # Archivo identificador: fuerza la auto-detección como proyecto Python
    # (evita que `src/` haga que se clasifique como Node en el respaldo por carpetas).
    (directorio / "requirements.txt").write_text("", encoding="utf-8")
    (directorio / "src" / "__init__.py").write_text("", encoding="utf-8")
    (directorio / "src" / "main.py").write_text(
        "def saludar(nombre):\n"
        '    return f"Hola, {name}"  # bug: debería ser {nombre}\n',
        encoding="utf-8",
    )
    (directorio / "tests" / "test_main.py").write_text(
        "from src.main import saludar\n\n\n"
        "def test_saludo():\n"
        '    assert saludar("Mundo") == "Hola, Mundo"\n',
        encoding="utf-8",
    )


def _crear_demo_editor(directorio: Path):
    """Devuelve un "editor" de demostración que sustituye a Aider en la demo.

    En la primera llamada simula que Aider intenta corregir pero deja el bug
    (para que el tester falle); en la segunda recibe el error realimentado y
    corrige ``name`` → ``nombre`` en ``src/main.py``. Así se muestra el ciclo
    completo Editor → Tester → error → corrección → éxito, sin dependencias.
    """
    estado = {"llamadas": 0}
    ruta_main = directorio / "src" / "main.py"

    def _editor(archivos, mensaje, directorio, opciones_aider=""):
        estado["llamadas"] += 1
        if estado["llamadas"] == 1:
            info("→ Aider (demo) intenta corregir el saludo... (aún quedará un error)")
            return True
        info("→ Aider (demo) recibe el error realimentado y corrige 'name' → 'nombre'.")
        texto = ruta_main.read_text(encoding="utf-8")
        ruta_main.write_text(
            texto.replace('return f"Hola, {name}"', 'return f"Hola, {nombre}"'),
            encoding="utf-8",
        )
        return True

    return _editor


def _ejecutar_demo() -> int:
    """Ejecuta una demo autónoma de SnapContext (sin API key ni Aider real).

    Fases:
      1. Crea un proyecto Python de ejemplo en ``tempfile.mkdtemp()``.
      2. ``--vista-previa --local``: muestra la selección de archivos relevantes.
      3. ``--test-loop`` (equivalente): ejecuta el bucle de pruebas completo
         (Editor → Tester → error realimentado → corrección → éxito).
      4. Resume el tiempo total, los archivos seleccionados y el resultado.

    Devuelve el código de salida (0 = éxito, 1 = fallo).
    """
    t_inicio = time.monotonic()
    info("=== SnapContext · Demo (sin API key) ===")
    info("Creando un proyecto de prueba temporal...")
    tmp = Path(tempfile.mkdtemp(prefix="snapcontext-demo-"))
    try:
        _crear_demo_proyecto(tmp)
        consulta = ("Corrige la función saludar para que devuelva el saludo "
                    "correcto")

        args = crear_parser().parse_args(
            _preparar_argv_aliases(
                [consulta, "--directorio", str(tmp), "--local", "--depurar"]
            )
        )

        # FASE 1: mostrar la selección de archivos (sin tocar código).
        info("── FASE 1 · Selección de archivos (--vista-previa --local) ──")
        args.vista_previa = True
        if flujo_principal(args) != 0:
            error("La selección de archivos falló durante la demo.")
            return 1

        # Tras la fase 1, args ya trae carpetas/extensiones ajustadas por tipo.
        carpetas = list(args.carpetas or CARPETAS_DEFECTO)
        extensiones = getattr(args, "extensiones", None)
        seleccion = listar_archivos_candidatos(
            tmp, carpetas, extensiones=extensiones
        )[: args.max_archivos]

        # FASE 2: bucle de pruebas completo (Editor → Tester) offline.
        info("── FASE 2 · Bucle de pruebas (Editor → Tester) ──")
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
            _emitir(sys.stdout, "   " + _pintar("• " + archivo, _VERDE))
        exito(f"Resultado de las pruebas: {'ÉXITO ✔' if ok else 'FALLO ✖'}")
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
        "resultado": "éxito" if codigo == 0 else "fallo",
        "duracion": round(duracion, 2),
    }
    hilo = threading.Thread(target=_guardar_historial,
                            args=(entrada,), daemon=True)
    hilo.start()
    hilo.join(timeout=5)   # espera breve: evita perder la entrada al salir


def flujo_principal(args: argparse.Namespace) -> int:
    """Orquesta el pipeline completo. Devuelve el código de salida.

    La lógica se delega en el Orquestador (arquitectura de agentes); aquí solo
    se conserva la firma de la CLI, la bandera de depuración global y, desde
    v0.10.0, el registro automático de la tarea en el historial persistente.
    """
    global DEPURAR
    DEPURAR = args.depurar
    # Al ejecutar como `python -m snapcontext` el archivo vive como `__main__`,
    # pero agentes/orquestador hacen `import snapcontext` (copia de módulo). Se
    # sincroniza el flag en el módulo compartido para que los logs salgan.
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
        # (con confirmación) actualizar CLAUDE.md con lo aprendido.
        if codigo == 0 and MEMORIA_PROYECTO:
            try:
                _actualizar_claude_md_automatico(
                    f"Tarea completada: {getattr(args, 'consulta', '')}",
                    directorio=getattr(args, "directorio", ".") or ".")
            except Exception as exc:        # nunca romper la salida
                depurar(f"[memoria] actualización falló: {exc}")

def iniciar_servidor_web(args: argparse.Namespace) -> int:
    """Arranca la interfaz web (FastAPI + WebSockets) en http://localhost:puerto.

    Importa ``web.app`` de forma diferida para que la CLI funcione sin FastAPI;
    si falta la dependencia opcional, devuelve un mensaje claro y sal con 1.
    """
    puerto = int(getattr(args, "web_puerto", 8000) or 8000)
    try:
        from web.app import arrancar_servidor
    except ImportError as exc:
        error(
            "La interfaz web necesita dependencias opcionales. Instala:\n"
            "  pip install snapcontext[web]\n"
            f"  (o pip install fastapi uvicorn websockets) — error: {exc}"
        )
        return 1
    info(f"Interfaz web en http://localhost:{puerto}  (Ctrl+C para salir)...")
    try:
        arrancar_servidor(puerto=puerto)
    except KeyboardInterrupt:
        info("Interfaz web detenida.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    # Instala los manejadores de Ctrl+C / SIGTERM (cierre limpio, subprocesos
    # incluidos) antes de hacer nada. Es seguro en Windows y Linux/macOS.
    _registrar_manejadores_senales()
    if argv is None:
        # Al ejecutar como script (`python snapcontext.py ...`) argparse debe
        # ver los argumentos reales; si pasáramos [] vacío, se perderían.
        argv = sys.argv[1:]
    # v3.1.1: sin argumentos → ayuda resumida y amigable (no un error).
    if not argv:
        _mostrar_ayuda_resumida()
        return 0
    args = crear_parser().parse_args(_preparar_argv_aliases(argv))
    try:
        # Permisos (v0.13.0): sincroniza el interruptor global con --confirmar
        # para todos los modos (chat, planificador, ...).
        global CONFIRMAR_ACCIONES
        CONFIRMAR_ACCIONES = getattr(args, "confirmar", True)
        # v3.1.1: --bienvenida explícito ejecuta el tutorial y marca el
        # primer uso como completado (por si quiere volver a verlo).
        if getattr(args, "bienvenida", False):
            codigo = _tutorial_interactivo()
            _marcar_primer_uso_completado()
            return codigo
        # v3.1.1: primer uso → tutorial automático y se continúa con el
        # comando pedido. Solo en terminales interactivos (nunca en tests,
        # CI o scripts) para evitar bloqueos.
        if _primer_uso_pendiente() and _entrada_interactiva():
            info("👋 Parece que es tu primera vez con SnapContext. "
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
        if MEMORIA_PROYECTO:
            info("📄 Memoria de proyecto cargada ("
                 + (_buscar_claude_md().name or "CLAUDE.md") + ").")
        # --init es independiente: configura claves/proveedor y sale.
        if getattr(args, "init", False):
            return asistente_configuracion_inicial()
        # --setup-path es independiente de la consulta: configura el PATH
        # y termina sin recorrer el pipeline (ni pedir consulta).
        if getattr(args, "setup_path", False):
            return configurar_path()
        # v3.1.0: diagnóstico, reparación y tutorial son independientes.
        if getattr(args, "diagnostico", False):
            return _ejecutar_diagnostico(args)
        if getattr(args, "reparar", False):
            return _ejecutar_reparacion(args)
        # --web inicia la interfaz web (FastAPI + WebSockets) y bloquea hasta parar.
        if getattr(args, "web", False):
            return iniciar_servidor_web(args)
        # --demo ejecuta una demo autónoma (sin API key ni Aider) y termina.
        if getattr(args, "demo", False):
            return _ejecutar_demo()
        # --historial-limpiar borra la memoria persistente y termina.
        if getattr(args, "historial_limpiar", False):
            return 0 if _limpiar_historial() else 1
        # --historial muestra las últimas tareas guardadas y termina.
        if getattr(args, "historial", False):
            _mostrar_historial()
            return 0
        # Memoria avanzada / aprendizaje (v3.0.0). El daemon corre hasta Ctrl+C.
        if getattr(args, "daemon", False):
            _daemon_bucle(
                intervalo_horas=getattr(args, "daemon_intervalo",
                                        DAEMON_INTERVALO_HORAS_DEFECTO))
            return 0
        # --curador ejecuta una pasada única del curador y termina.
        if getattr(args, "curador", False):
            _curador_ejecutar()
            return 0
        # --skills lista los skills aprendidos y termina.
        if getattr(args, "skills", False):
            filas = _skill_listar(incluir_archivados=True)
            if not filas:
                info("Aún no hay skills aprendidos. Se crean al completar "
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
        if getattr(args, "chat", False):
            return _ejecutar_chat()
        # --plan ejecuta el planificador de tareas (requiere consulta).
        if getattr(args, "plan", False):
            return _ejecutar_planificador(args)
        return flujo_principal(args)
    except KeyboardInterrupt:
        error("Interrumpido por el usuario.")
        return 130
    except RuntimeError as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
