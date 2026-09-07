#!/usr/bin/env python3
"""Capa de presentación de SnapContext (extraída en la Fase 3 del refactor).

Contiene la salida por consola: colores ANSI con soporte Windows/NO_COLOR,
glifos ASCII de seguridad, emisión de mensajes (``info``/``aviso``/``exito``/
``error``/``depurar``), puente hacia la TUI y hacia la interfaz web
(``EVENTO_CALLBACK``), y el subsistema de color de la ayuda (``--help``).

Nota histórica: este módulo define ``_pintar`` dos veces (núcleo y ayuda),
igual que hacía ``snapcontext.py`` antes de la extracción; la segunda
definición sombrea a la primera y se conserva el orden para no alterar el
comportamiento. Los mutadores externos sincronizan los flags globales
``DEPURAR``, ``_TUI_HUB`` y ``_AYUDA_CON_COLOR`` vía el módulo.
"""

import os
import sys

DEPURAR = False

# ---------------------------------------------------------------------------
# Salida por consola (colores ANSI con soporte Windows y NO_COLOR)
# ---------------------------------------------------------------------------
_GLIFOS_ASCII = {
    "\u2139": "[i]",  # ℹ
    "\u2714": "[OK]",  # ✔
    "\u26a0": "[!]",  # ⚠
    "\u2716": "[ERROR]",  # ✖
    "\u2022": "-",  # •
    "\u2192": "->",  # →
    "\u2014": "-",  # — (em dash)
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
            EVENTO_CALLBACK(
                {
                    "tipo": "log",
                    "nivel": "error" if stream is sys.stderr else "info",
                    "texto": seguro,
                }
            )
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
        "\033[92m",
        "\033[93m",
        "\033[91m",
        "\033[96m",
        "\033[90m",
        "\033[0m",
    )
else:
    _VERDE = _AMARILLO = _ROJO = _CYAN = _GRIS = _REINICIO = ""

DEPURAR = False  # se activa con --depurar


def _pintar(texto: str, codigo: str) -> str:
    return f"{codigo}{texto}{_REINICIO}"


_TUI_HUB: object | None = False  # False = no probado, None = no disponible, módulo = listo


def _tui_log(nivel: str, msg: str) -> None:
    """Reenvía un log a la TUI (v6.12.0) si el modo está activo.

    Nunca lanza ni bloquea: si Textual/tui_hub no está disponible o la cola
    está llena, el evento simplemente se descarta. Coste ~0 cuando la TUI
    está inactiva (una comprobación booleana).
    """
    global _TUI_HUB
    if _TUI_HUB is False:
        try:
            import tui_hub as _hub

            _TUI_HUB = _hub
        except Exception:
            _TUI_HUB = None
    if _TUI_HUB and getattr(_TUI_HUB, "esta_activo", lambda: False)():
        try:
            _TUI_HUB.enviar_log(nivel, str(msg))  # type: ignore[attr-defined]
        except Exception:
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
        _emitir(sys.stdout, _pintar("  [depuración] " + msg, _GRIS))


# ---------------------------------------------------------------------------
# Ayuda agrupada y coloreada (`snapcontext --help`)
# ---------------------------------------------------------------------------
# Códigos ANSI; si el terminal no soporta color (o NO_COLOR está definido), se
# degradan a texto plano. `colorama` se usa solo para inicializar en Windows
# si está disponible; nunca es obligatorio.
_ANSI = {
    "negrita": "\033[1m",
    "cian": "\033[96m",
    "amarillo": "\033[93m",
    "verde": "\033[92m",
    "gris": "\033[90m",
    "reset": "\033[0m",
}
_AYUDA_CON_COLOR = False  # se calcula una sola vez al mostrar --help


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

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass  # sin VT → texto plano
    return True


def _pintar(texto: str, clave: str) -> str:  # type: ignore[no-redef]
    """Aplica el color ANSI ``clave`` si los colores están activos."""
    if not _AYUDA_CON_COLOR:
        return texto
    return f"{_ANSI.get(clave, '')}{texto}{_ANSI['reset']}"
