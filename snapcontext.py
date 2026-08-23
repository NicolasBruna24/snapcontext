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
from typing import List, Optional, Union
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

VERSION = "1.3.0"


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
   │    » v0.5.0                                             │
   │                                                          │
   └──────────────────────────────────────────────────────────┘
"""


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
    return 0


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

    # 4) Primer uso sin configuración: menú interactivo + preguntar si guardar.
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
                      timeout: int = 120) -> tuple:
    """Ejecuta ``comando`` (str de shell) en ``directorio``.

    Devuelve ``(codigo_retorno, stdout, stderr)``. Usa ``shell=True`` en todas
    las plataformas (cmd.exe en Windows, sh en Linux/macOS). Errores comunes
    (timeout, directorio inválido) devuelven ``(-1, "", mensaje_de_error)``
    sin lanzar excepciones.
    """
    raiz = Path(directorio).expanduser()
    if not raiz.is_dir():
        return (-1, "", f"El directorio no existe: {raiz}")
    try:
        proc = subprocess.run(
            comando,
            cwd=str(raiz),
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        return (proc.returncode, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired:
        return (-1, "", f"El comando tardó demasiado (timeout={timeout}s)")
    except OSError as exc:
        return (-1, "", f"Error ejecutando '{comando}': {exc}")


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

        # ---- búsqueda semántica (v1.1.0) ----------------------------------
        if linea.startswith("/search "):
            consulta_busqueda = linea[len("/search "):].strip()
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
    '  "accion": "editar" | "ejecutar" | "consultar",\n'
    '  "archivos": ["ruta/relativa.py"],   // solo para accion "editar"\n'
    '  "comando": "comando shell"          // solo para accion "ejecutar"\n'
    "}}]}}\n\n"
    "Significado de las acciones:\n"
    ' - "editar": modificar código (Aider). Indica los archivos implicados.\n'
    ' - "ejecutar": lanzar un comando (tests, build, migraciones...).\n'
    ' - "consultar": aclarar una duda sobre el proyecto sin cambiar nada.\n'
)

ACCIONES_VALIDAS = {"editar", "ejecutar", "consultar"}


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

    # Confirmación de permisos (v0.13.0) antes de cualquier acción.
    # En modo autónomo (--auto, v0.17.0) no se pregunta: solo se respetan las
    # preferencias ya guardadas en permisos.json (nunca → denegado).
    if accion == "ejecutar":
        detalles_paso = paso.get("comando") or None
    elif accion == "editar":
        detalles_paso = "\n".join(paso.get("archivos", [])) or None
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

    # accion == "editar": reutiliza el pipeline existente con este paso.
    paso_args = argparse.Namespace(**vars(args))
    paso_args.consulta = descripcion
    orch = Orquestador()
    plan = orch._planificar(paso_args, sc)
    if plan is None:
        return (False, "no se pudo planificar la edición (sin candidatos)")
    _, ruta_raiz, _, seleccion = plan
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

    # 3) Ejecución secuencial con control por paso.
    resultados: List[dict] = []
    indice = 0
    abortar = False
    while indice < len(pasos) and not abortar:
        paso = pasos[indice]
        numero = indice + 1
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
        "parametros": {"comando": "str", "directorio": "str='.'"},
        "requiere_permiso": True,
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


def _tool_execute_command(comando: str, directorio: str = ".") -> dict:
    """Herramienta `execute_command`: ejecuta un comando shell arbitrario.

    Requiere confirmación estricta (se valida en el dispatcher).
    """
    if not comando:
        return {"ok": False, "error": "falta el comando a ejecutar"}
    codigo, stdout, stderr = _ejecutar_comando(comando, directorio)
    return {"ok": codigo == 0, "codigo_retorno": codigo,
            "stdout": stdout.strip(), "stderr": stderr.strip()}


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
        elif nombre == "git_status":
            resultado = _tool_git_status(str(argumentos.get("directorio", ".")))
        elif nombre == "git_diff":
            archivo = argumentos.get("archivo")
            resultado = _tool_git_diff(str(argumentos.get("directorio", ".")),
                                       str(archivo) if archivo else None)
        elif nombre == "execute_command":
            resultado = _tool_execute_command(
                str(argumentos.get("comando", "")),
                str(argumentos.get("directorio", ".")))
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

    # 1) Recolectar archivos candidatos (relativo, contenido, hash).
    archivos: List[tuple] = []
    for camino in sorted(raiz.rglob("*")):
        if not camino.is_file() or camino.suffix.lower() not in extensiones:
            continue
        if any(parte in CARPETAS_IGNORADAS for parte in camino.parts):
            continue
        relativo = camino.relative_to(raiz).as_posix()
        if _es_ignorado(relativo, patrones):
            continue
        try:
            contenido = camino.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            aviso(f"No se pudo leer {relativo}: {exc}")
            continue
        archivos.append((relativo, contenido, _hash_texto(contenido)))

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


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapcontext",
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
    args = crear_parser().parse_args(_preparar_argv_aliases(argv))
    try:
        # Permisos (v0.13.0): sincroniza el interruptor global con --confirmar
        # para todos los modos (chat, planificador, ...).
        global CONFIRMAR_ACCIONES
        CONFIRMAR_ACCIONES = getattr(args, "confirmar", True)
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
