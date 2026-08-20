#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SnapContext — Asistente de IA para desarrollo con contexto automático.

Pipeline:
    1) Escanea automáticamente el repositorio (por defecto: lib/ y supabase/)
       buscando archivos relevantes para la consulta del usuario.
    2) Usa Gemini (Google AI Studio) para seleccionar los archivos más
       relevantes, sin que el desarrollador tenga que listarlos a mano.
    3) Ejecuta Aider con los archivos seleccionados y la consulta original.
    4) (Opcional, --test-loop) Después de Aider ejecuta las pruebas
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
    snapcontext "..." --provider groq --model llama-3.3-70b-versatile

Open-source y pensado para ser fácil de extender (ver ejecutar_bucle_test).
"""

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
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

VERSION = "0.6.0"

# ─── Logo ASCII ──────────────────────────────────────────────────────────
_LOGO = r"""
  ┌──────────────────────────────────────────────────────────┐
  │                                                          │
  │                                                          │
  │    ███████╗███╗   ██╗ █████╗ ██████╗  ██████╗ ██████╗   │
  │    ██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔════╝██╔════╝   │
  │    ███████╗██╔██╗ ██║███████║██████╔╝██║     ██║        │
  │    ╚════██║██║╚██╗██║██╔══██║██╔═══╝ ██║     ██║        │
  │    ███████║██║ ╚████║██║  ██║██║     ╚██████╗╚██████╗   │
  │    ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝      ╚═════╝ ╚═════╝   │
  │                                                          │
  │    » Selección inteligente de archivos                  │
  │    » Soporte: Gemini · Ollama · DeepSeek · Groq        │
  │    » v0.5.0                                             │
  │                                                          │
  └──────────────────────────────────────────────────────────┘
"""

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


def _emitir(stream, texto: str) -> None:
    """Escribe texto con seguridad ante codificaciones limitadas."""
    seguro = _texto_seguro(texto)
    try:
        print(seguro, file=stream)
    except UnicodeEncodeError:
        print(seguro.encode("ascii", "replace").decode("ascii"), file=stream)


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
    """Devuelve True si 'directorio' contiene al menos una carpeta típica de proyecto."""
    ruta = Path(directorio)
    if not ruta.is_dir():
        return False
    return any((ruta / carpeta).is_dir() for carpeta in CARPETAS_PROYECTO_VALIDAS)


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


def listar_archivos_candidatos(raiz: Path, carpetas: List[str]) -> List[str]:
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

    return sorted({
        ruta for ruta in coleccion
        if _pertenece_a_carpetas(ruta, carpetas) and _es_archivo_indexable(ruta)
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
    archivos = listar_archivos_candidatos(raiz, carpetas)
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
            '  snapcontext "..." --provider groq --model llama-3.3-70b-versatile\n'
            "Variables de entorno: clave según --provider (GEMINI_API_KEY / "
            "DEEPSEEK_API_KEY / GROQ_API_KEY), OLLAMA_URL (default "
            "localhost:11434), SNAPCONTEXT_PROVIDER y SNAPCONTEXT_MODELO "
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
             "(útil para probar offline).",
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
    return parser


def flujo_principal(args: argparse.Namespace) -> int:
    """Orquesta el pipeline completo. Devuelve el código de salida."""
    global DEPURAR
    DEPURAR = args.depurar

    if not getattr(args, "consulta", None):
        raise RuntimeError(
            "Falta la consulta (la tarea a resolver). Uso:\n"
            '  snapcontext "el botón de pago no funciona"\n'
            "  snapcontext --init   (para la configuracion inicial)"
        )

    # --- Configuración automática del PATH (Windows) con --setup-path ---
    if args.setup_path and sys.platform.startswith("win"):
        info("Configurando PATH del usuario para Windows...")
        
        import os
        
        try:
            # Obtener la ruta del sitio de paquetes de usuario
            site_packages = os.path.dirname(
                __import__("sysconfig").get_path("scripts")
            )
            
            # En Python 3.9+, los scripts están en Scripts, no en bin/
            scripts_path = site_packages.replace("\\site-packages", "\\Scripts")
            
            # Verificar si existe el ejecutable
            ejecutable_path = os.path.join(scripts_path, "snapcontext.exe")
            
            if os.path.exists(ejecutable_path):
                # Obtener PATH actual del usuario
                path_actual = os.environ.get("PATH", "")
                
                # Añadir la carpeta Scripts al PATH si no está presente
                if scripts_path not in path_actual:
                    new_path = scripts_path + ";" + path_actual
                    os.environ["PATH"] = new_path
                    
                    # Guardar permanentemente en el registro de Windows
                    import subprocess
                    result = subprocess.run(
                        ['setx', '/M', 'PATH', new_path],
                        capture_output=True,
                        text=True
                    )
                    
                    if result.returncode == 0:
                        info(f"✓ Ruta '{scripts_path}' añadida permanentemente al PATH de usuario")
                        info("Reinicia tu terminal para que los cambios surtan efecto.")
                        info("O ejecuta 'refreshenv' si tienes Chocolatey instalado.")
                    else:
                        error(f"Error al guardar en PATH: {result.stderr}")
                else:
                    info(f"Ruta '{scripts_path}' ya presente en el PATH (sin cambios necesarios)")
            else:
                warn(f"El ejecutable no se encuentra en: {ejecutable_path}")
                info("Intentando localizar snapcontext.exe...")
                
                # Fallback: buscar en rutas comunes de pip/uv
                for candidate in [
                    os.path.join(os.environ.get("APPDATA", ""), "Python", "Scripts"),
                    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python3*", "Scripts"),
                    scripts_path,
                ]:
                    candidate_path = os.path.join(candidate, "snapcontext.exe")
                    if os.path.exists(candidate_path):
                        scripts_path = candidate
                        
                        path_actual = os.environ.get("PATH", "")
                        if scripts_path not in path_actual:
                            new_path = scripts_path + ";" + path_actual
                            os.environ["PATH"] = new_path
                            
                            result = subprocess.run(
                                ['setx', '/M', 'PATH', new_path],
                                capture_output=True,
                                text=True
                            )
                            
                            if result.returncode == 0:
                                info(f"✓ Ruta '{scripts_path}' añadida al PATH de usuario (fallback)")
                            else:
                                warn(f"Error al guardar en PATH: {result.stderr}")
                        else:
                            info(f"Ruta ya presente en el PATH del usuario")
                        break
                else:
                    warn("No se pudo encontrar snapcontext.exe automáticamente.")
        except Exception as e:
            error(f"Error configurando PATH: {e}")
            info("La funcionalidad de SnapContext seguirá funcionando normalmente.")

    if args.max_archivos < 1:
        raise RuntimeError("--max-archivos debe ser al menos 1.")

    raiz = resolver_raiz(args.directorio)

    # Mejora 1: validación de carpeta de proyecto antes de cualquier otra acción.
    # Si el directorio no contiene ninguna carpeta típica de proyecto, avisamos
    # con un mensaje amigable y salimos con código de error 1 (sin seguir).
    if not _es_proyecto_valido(raiz):
        error(
            "⚠️ No parece que estés en una carpeta de proyecto. "
            "SnapContext espera carpetas como lib/, src/, supabase/, etc. "
            "Puedes indicar una carpeta con --directorio <ruta> o navega a "
            "la raíz de tu proyecto y vuelve a intentarlo."
        )
        return 1

    info(f"Repositorio: {raiz}")

    # 1) –––– Escaneo del repositorio (heurística local) ––––
    carpetas = args.carpetas or list(CARPETAS_DEFECTO)
    info("Escaneando el repositorio para encontrar candidatos...")
    candidatos = escanear_repositorio(
        args.consulta, directorio=str(raiz),
        carpetas=carpetas, max_candidatos=max(args.candidatos, 1),
    )
    if not candidatos:
        error(
            "No se encontraron archivos. ¿Hay código dentro de "
            f"{', '.join(carpetas)}? Revisa --carpetas."
        )
        return 1
    info(f"{len(candidatos)} candidato(s) relevante(s) localmente.")

    # 2) Selección final (Gemini o heurística local)
    if args.local:
        aviso("Modo --local: selección por heurística, sin proveedor de IA.")
        seleccion = candidatos[: args.max_archivos]
    elif len(candidatos) <= args.max_archivos:
        aviso("Hay pocos candidatos; se usan todos sin consultar al selector IA.")
        seleccion = candidatos
    else:
        # Mejora 2: si no se eligió proveedor con --provider, se resuelve con
        # persistencia: configuración guardada, env o menú interactivo.
        pref = _determinar_proveedor(args)
        seleccion = seleccionar_archivos(
            args.consulta, candidatos,
            proveedor=pref["provider"], modelo=pref["model"],
            max_archivos=args.max_archivos,
        )
        if not seleccion:
            aviso("El proveedor no devolvió rutas válidas; se usan las mejor "
                  "puntuadas localmente.")
            seleccion = candidatos[: args.max_archivos]

    _emitir(sys.stdout, "")
    exito(f"Archivos seleccionados ({len(seleccion)}):")
    for archivo in seleccion:
        _emitir(sys.stdout, "   " + _pintar("\u2022 " + archivo, _VERDE))

    if args.vista_previa:
        aviso("Modo --vista-previa: no se ejecuta Aider.")
        return 0

    # --- Modo experto: revisar/editar la selección antes de ejecutar Aider ----
    if args.experto and _preguntar_si(
        "¿Quieres revisar los archivos seleccionados? (s/n): "
    ):
        seleccion = modo_experto(seleccion, raiz)
        _emitir(sys.stdout, "")
        exito("Lista final para Aider:")
        for archivo in seleccion:
            _emitir(sys.stdout, "   " + _pintar("• " + archivo, _VERDE))
        _emitir(sys.stdout, "")

    # 3) Ejecución (Aider directo, pruebas o bucle con servidor Flutter)
    _emitir(sys.stdout, "")
    if args.server_loop or args.manual_loop:
        ok = ejecutar_bucle_agente(
            args.consulta, seleccion,
            modo="auto" if args.server_loop else "manual",
            max_intentos=args.max_intentos,
            directorio=str(raiz), opciones_aider=args.aider_opciones,
            dispositivo=args.dispositivo, url_defecto=args.url_defecto,
        )
    elif args.test_loop:
        ok = ejecutar_bucle_test(
            args.consulta, seleccion, str(raiz),
            opciones_aider=args.aider_opciones,
            comando_test=shlex.split(args.comando_test),
            max_iteraciones=max(args.max_iteraciones, 1),
        )
    else:
        ok = ejecutar_aider(
            seleccion, args.consulta, str(raiz),
            opciones_aider=args.aider_opciones,
        )
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    # Instala los manejadores de Ctrl+C / SIGTERM (cierre limpio, subprocesos
    # incluidos) antes de hacer nada. Es seguro en Windows y Linux/macOS.
    _registrar_manejadores_senales()
    args = crear_parser().parse_args(argv)
    try:
        # --init es independiente: configura claves/proveedor y sale.
        if getattr(args, "init", False):
            return asistente_configuracion_inicial()
        return flujo_principal(args)
    except KeyboardInterrupt:
        error("Interrumpido por el usuario.")
        return 130
    except RuntimeError as exc:
        error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())