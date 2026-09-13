#!/usr/bin/env python3
"""Detección de comandos peligrosos para el sandboxing inteligente (v5.4.0).

Permite a SnapContext decidir *por comando* si conviene ejecutarlo dentro del
contenedor Docker aislado (``--sandbox``), sin forzarlo para todo ni añadir
fricción en los casos seguros.

El núcleo es :func:`es_comando_peligroso`, que recorre los patrones de
:data:`_PATRONES_PELIGROSOS`. Para añadir un nuevo patrón solo hay que añadir
una tupla ``(regex, descripcion)`` a esa lista; la detección es O(1) por patrón
(regex compilados) y por tanto muy rápida (pensada para no penalizar la
ejecución normal de comandos).

Ejemplo::

    es_comando_peligroso("rm -rf /")            # True
    es_comando_peligroso("curl url | sh")        # True
    es_comando_peligroso("ls -la")               # False

Diseñado sin dependencias externas (solo stdlib: ``re``).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Callable

# Registro extensible de patrones de comandos peligrosos.
# Cada elemento es ``(regex_compilada, descripcion)``.
#
# Nota de diseño (evita falsos positivos):
#   - ``> /dev/null`` NO se considera peligroso (es una redirección inocua a un
#     dispositivo nulo que cierra la salida). Solo se marcan las escrituras a
#     **dispositivos de bloque reales** (/dev/sda*, /dev/nvme*, ...), que sí
#     pueden destruir el disco.
_PATRONES_PELIGROSOS: list[tuple[re.Pattern, str]] = [
    # ── Eliminación masiva de archivos ─────────────────────────────────────
    (
        re.compile(
            r"\brm\s+-(?:rf|fr|r\s+-f|f\s+-r)\s+"
            r"(?:/(?=$|\*)|/\*\s*|\*(?=\s|$)|\.(?=\s|$)|~(?=\s|$))",
            re.IGNORECASE,
        ),
        "rm -rf sobre ruta raíz/usuario (borrado masivo)",
    ),
    (
        re.compile(r"\brm\s+-r\s+-f\s+(?:/|\.\s|\.$)", re.IGNORECASE),
        "rm -rf sobre directorio raíz/actual",
    ),
    (
        re.compile(r"\brm\s+--no-preserve-root\b", re.IGNORECASE),
        "rm con omisión de protección de raíz",
    ),
    # ── Manipulación de discos / particiones ────────────────────────────────
    (
        re.compile(r"\bdd\s+(?:if=|of=|bs=|conv=)", re.IGNORECASE),
        "dd con gestión de dispositivos (if=/of=)",
    ),
    (re.compile(r"\bmkfs\b[\w.-]*", re.IGNORECASE), "mkfs (formatear sistema de archivos)"),
    (re.compile(r"\bfdisk\b", re.IGNORECASE), "fdisk (particionado de disco)"),
    (
        re.compile(r"\bwipefs\b|\bmkswap\b|\bparted\s+-a\s+optimal", re.IGNORECASE),
        "borrado de firmas / formateo / particionado agresivo",
    ),
    # ── Descarga y ejecución de scripts remotos ─────────────────────────────
    (
        re.compile(r"\bcurl\s+.*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b", re.IGNORECASE),
        "curl piped a shell (descarga y ejecución)",
    ),
    (
        re.compile(r"\bwget\s+.*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b", re.IGNORECASE),
        "wget piped a shell (descarga y ejecución)",
    ),
    (
        re.compile(r"\b(?:curl|wget)\b.*\|\s*sudo\s+(?:sh|bash|zsh)\b", re.IGNORECASE),
        "descarga remota ejecutada con sudo",
    ),
    # ── Cambios de permisos peligrosos ──────────────────────────────────────
    (re.compile(r"\bchmod\s+-R\s*\+?\s*777\b", re.IGNORECASE), "chmod -R 777 sobre el árbol"),
    (re.compile(r"\bchmod\s+777\s*/?(?:\s|$)", re.IGNORECASE), "chmod 777 sobre la raíz o amplio"),
    (re.compile(r"\bchmod\s+[0-7]{4}\s+/(?:\s|$)", re.IGNORECASE), "chmod de la raíz"),
    (re.compile(r"\bchown\s+-R\b", re.IGNORECASE), "chown -R (cambio de propietario recursivo)"),
    # ── Fork bomb ───────────────────────────────────────────────────────────
    (re.compile(r":\s*\(\s*\)\s*\{", re.IGNORECASE), "fork bomb (bucle recursivo infinito)"),
    # ── sudo con comandos peligrosos ────────────────────────────────────────
    (
        re.compile(
            r"\bsudo\s+(?:rm\s+-rf|mkfs|dd|fdisk|shutdown|reboot|reboot|"
            r"poweroff|halt)\b",
            re.IGNORECASE,
        ),
        "sudo + comando destructivo",
    ),
    # ── Escritura en dispositivos de bloque (excluye /dev/null a propósito) ─
    (
        re.compile(
            r">\s*/dev/(?:sd[a-z]+\d*|hd[a-z]+\d*|nvme\d+n\d+|"
            r"mmcblk\d+|mapper/\S+|disk/by-id/\S+|mem|shm)\b",
            re.IGNORECASE,
        ),
        "escritura en buffer de dispositivo de bloque",
    ),
    # ── Terminación de procesos críticos ────────────────────────────────────
    (re.compile(r"\bkill\s+-9\b", re.IGNORECASE), "kill -9 (forzar terminación)"),
    (re.compile(r"\bpkill\b", re.IGNORECASE), "pkill (terminación masiva de procesos)"),
]


def es_comando_peligroso(comando: str) -> bool:
    """Indica si ``comando`` contiene algún patrón de alto riesgo.

    Recorre los patrones de :data:`_PATRONES_PELIGROSOS`. Ante cadenas vacías
    o sin coincidencias devuelve ``False``. La lógica es O(n·m) con regex
    compiladas y es lo bastante ligera para invocarse en cada comando.
    """
    if not comando or not str(comando).strip():
        return False
    texto = str(comando).strip()
    for patron, _desc in _PATRONES_PELIGROSOS:
        if patron.search(texto):
            return True
    return False


def patrones_peligrosos_info() -> list[tuple[str, str]]:
    """Devuelve las descripciones de los patrones (para logging/auditoría)."""
    return [(p.pattern, d) for p, d in _PATRONES_PELIGROSOS]


def patron_peligroso(comando: str) -> str | None:
    """Descripción del **primer** patrón de :data:`_PATRONES_PELIGROSOS` que matchea.

    Devuelve ``None`` si no hay coincidencia. Útil para mensajes de warning
    explícitos (qué exactamente disparó la blocklist legacy).
    """
    if not comando or not str(comando).strip():
        return None
    texto = str(comando).strip()
    for patron, desc in _PATRONES_PELIGROSOS:
        if patron.search(texto):
            return desc
    return None


# ---------------------------------------------------------------------------
# Clasificación de 3 niveles (v6.36.0): ALLOWLIST → DEFAULT-DENY → BLOCKLIST
# ---------------------------------------------------------------------------
# Se invierte el modelo anterior (blocklist que decidía sandbox sí/no):
#
#   1. ALLOWLIST  → ejecución directa, fricción cero. Solo si el comando no
#     tiene metacaracteres de shell Y su binario base está en la allowlist
#     (configurable en ``config.json`` → ``"sandbox_allowlist_binarios"``).
#   2. DEFAULT-DENY → sandbox Docker obligatorio. Cualquier comando que no
#     cumpla el criterio de allowlist (pipes, redirecciones, expansión de
#     variables, binarios fuera de la lista...).
#   3. BLOCKLIST legacy (`_PATRONES_PELIGROSOS`) → defensa en profundidad:
#     ya no decide sandbox sí/no; se usa para warnings más explícitos.
#
# `es_comando_peligroso` se mantiene intacta (compatibilidad de API).

# Allowlist por defecto: binarios de lectura/inspección y herramientas de
# calidad. Conservadora a propósito: NO incluye intérpretes (python, node,
# sh, bash), gestores de paquetes que ejecutan scripts arbitrarios, ni
# utilidades destructivas (rm, find, chmod...). Se sobrescribe completa con
# ``config.json`` → ``"sandbox_allowlist_binarios"`` (lista de nombres).
ALLOWLIST_BINARIOS_DEFECTO = frozenset(
    {
        # inspección de archivos
        "ls", "cat", "head", "tail", "wc", "stat", "file", "diff", "tree",
        # búsqueda
        "grep", "rg",
        # control de versiones
        "git",
        # pruebas y calidad
        "pytest", "ruff", "mypy", "black", "isort",
        # ecosistema JS (solo subcomandos de inspección/test)
        "npm",
        # ecosistema Python (listado/instalación; no ejecuta código del repo)
        "pip", "pip3",
        # misceláneos inocuos
        "echo", "which", "date",
    }
)

# Binarios que SIEMPRE van al sandbox aunque carezcan de metacaracteres
# (nivel 2 explícito). Se comprueba por nombre base y por prefijo ``mkfs*``.
_BINARIOS_SIEMPRE_SANDBOX = frozenset(
    {
        "eval", "exec", "source", "dd", "shred", "truncate", "base64",
        "xargs", "chmod", "chown", "curl", "wget", "find", "rm", "mkswap",
        "wipefs", "fdisk", "parted",
    }
)

# Metacaracteres y expansiones que impiden calificar para la allowlist:
#   | & ; < > backtick  $( )  $VAR  paréntesis  salto de línea  barra invertida
_METACARACTERES_AMPLIOS = re.compile(r"[|&;<>`$()\n\\]")

_CLAVE_CONFIG_ALLOWLIST = "sandbox_allowlist_binarios"


def _leer_allowlist_configurada() -> set[str]:
    """Lee ``config.json`` → ``"sandbox_allowlist_binarios"`` (lista de nombres).

    Si la clave existe y es una lista no vacía, **reemplaza** la allowlist por
    defecto. Si falta o está corrupta, se usa el defecto. Nunca lanza.
    """
    try:
        from configuracion import cargar_configuracion

        personal = (cargar_configuracion() or {}).get(_CLAVE_CONFIG_ALLOWLIST)
        if isinstance(personal, list):
            nombres = {str(item).strip() for item in personal if str(item).strip()}
            if nombres:
                return nombres
    except Exception:
        pass
    return set(ALLOWLIST_BINARIOS_DEFECTO)


def clasificar_comando(comando: str, allowlist: set[str] | None = None) -> tuple[str, str]:
    """Clasifica ``comando`` para decidir si corre directo o en sandbox (v6.36.0).

    Devuelve ``(nivel, motivo)`` donde ``nivel`` es:

    - ``"directo"``: ALLOWLIST — sin metacaracteres de shell y con binario
      base en la allowlist (se puede ejecutar con fricción cero).
    - ``"sandbox"``: DEFAULT-DENY — todo lo demás (metacaracteres, expansión
      de variables, binario fuera de la allowlist, blocklist legacy).

    ``motivo`` es una cadena apta para mostrar al usuario en confirmaciones.
    La blocklist legacy (:func:`es_comando_peligroso`) ya no decide aquí el
    sandbox por sí sola, pero sus patrones fuerzan ``"sandbox"`` con un
    motivo explícito (defensa en profundidad).
    """
    texto = str(comando or "").strip()
    if not texto:
        # Un comando vacío no ejecuta nada: directo (los llamadores lo rechazan).
        return "directo", "comando vacío"
    # Nivel 3 (defensa en profundidad): patrones legacy → sandbox con motivo.
    desc = patron_peligroso(texto)
    if desc:
        return "sandbox", f"blocklist legacy: {desc}"
    # Nivel 1 (allowlist): solo comandos sin ningún metacarácter de shell.
    if _METACARACTERES_AMPLIOS.search(texto):
        return "sandbox", "contiene metacaracteres de shell (pipes/redirecciones/expansión)"
    try:
        argv = shlex.split(texto)
    except ValueError as exc:
        return "sandbox", f"no se pudo interpretar el comando ({exc})"
    if not argv:
        return "sandbox", "comando vacío tras el parseo"
    base = os.path.basename(argv[0])
    # Casos explícitos de nivel 2 (aunque no tengan metacaracteres obvios).
    if base in _BINARIOS_SIEMPRE_SANDBOX or base.startswith("mkfs"):
        return "sandbox", f"binario '{base}' es de riesgo y siempre va al sandbox"
    permitidos = (
        _leer_allowlist_configurada() if allowlist is None else set(allowlist)
    )
    if base not in permitidos:
        return "sandbox", f"binario '{base}' fuera de la allowlist del sandbox"
    return "directo", f"binario '{base}' en la allowlist y sin metacaracteres"


# ---------------------------------------------------------------------------
# Ejecución segura de comandos
# ---------------------------------------------------------------------------
# Helpers para reemplazar `subprocess.run(..., shell=True)` por ejecución con
# lista de argumentos (`shell=False`), eliminando el riesgo de inyección de
# comandos cuando estos proceden del LLM o de entrada del usuario.
#
# Uso recomendado:
#   from sandbox_utils import ejecutar_comando_con_politica
#   proc = ejecutar_comando_con_politica("pytest -q", cwd=".", timeout=120)
#
# `ejecutar_comando_con_politica` usa automáticamente la vía más segura:
#   - Comandos SIN sintaxis de shell (pipes/redirecciones/glóbulos) → se
#     dividen con `shlex.split` y se ejecutan con `shell=False`.
#   - Comandos CON sintaxis de shell (no expresables por lista) → se mantienen
#     `shell=True` (imprescindible para `|`, `>`, `&&`, ...) tras validarlos con
#     `es_comando_peligroso` y, si son peligrosos, requerir confirmación vía el
#     callable `confirmar` (o rechazarlos si es `None`).


def _flags_creacion() -> int:
    """Flags de subprocess: evita ventanas de consola en Windows."""
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def ejecutar_comando_seguro(
    comando: str | list[str] | tuple[str, ...],
    cwd: str | None = None,
    timeout: float | None = None,
    env: dict | None = None,
    capturar_salida: bool = True,
    entrada: str | None = None,
) -> subprocess.CompletedProcess:
    """Ejecuta ``comando`` (lista de argumentos) con ``shell=False``.

    Acepta únicamente una **secuencia de strings** (lista o tupla). Si se pasa
    un string único se lanza :class:`ValueError`: llamar con strings obliga a
    pensar explícitamente en cómo dividir el comando y evita inyecciones.

    Args:
        comando: Lista/tupla de argumentos (el ejecutable y sus parámetros).
        cwd: Directorio de trabajo.
        timeout: Timeout en segundos (lanza ``subprocess.TimeoutExpired``).
        env: Mapa de entorno a pasar al proceso (``None`` = heredar).
        capturar_salida: Si ``True`` captura stdout/stderr; si ``False`` deja
            que fluyan a la consola y devuelve ``text=False``.
        entrada: Texto que se envía por stdin (``input=``).

    Returns:
        ``subprocess.CompletedProcess`` (misma forma que ``subprocess.run``).

    Raises:
        ValueError: si ``comando`` es un string en lugar de una secuencia.
    """
    if isinstance(comando, (str, bytes)):
        raise ValueError(
            "ejecutar_comando_seguro espera una lista de argumentos, no un "
            f"string. Comando recibido: {comando!r}. Usa shlex.split(...) o la "
            "lista construida manualmente."
        )
    argv = list(comando)
    if not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError(
            f"El comando debe ser una lista no vacía de strings. Recibido: {comando!r}"
        )
    return subprocess.run(
        argv,
        cwd=cwd,
        shell=False,
        env=env,
        capture_output=capturar_salida,
        input=entrada if capturar_salida else None,
        text=capturar_salida,
        errors="replace" if capturar_salida else None,
        timeout=timeout,
        creationflags=_flags_creacion(),
    )


# Metacaracteres que requieren un intérprete de shell (no expresables por lista).
_METACARACTERES_SHELL = re.compile(r"[|&;<>`]|\$\(|\*|\?|~")


def tiene_metacaracteres_shell(comando: str | None) -> bool:
    """Indica si ``comando`` necesita un intérprete de shell.

    Detecta pipes (``|``), redirecciones (``<``/``>``), separadores (``;``,
    ``&&``, ``||``), sustitución de comandos (``$(...)`` o backticks) y
    glóbulos (``*``/``?``). Si devuelve ``True`` no se puede ejecutar el
    comando como lista simple sin cambiar su semántica.
    """
    if not comando:
        return False
    return bool(_METACARACTERES_SHELL.search(str(comando)))


def ejecutar_comando_con_politica(
    comando: str,
    cwd: str | None = None,
    timeout: float | None = None,
    env: dict | None = None,
    capturar_salida: bool = True,
    entrada: str | None = None,
    confirmar: Callable[[str], bool] | None = None,
) -> subprocess.CompletedProcess:
    """Ejecuta un comando (string) eligiendo la vía más segura posible.

    - Sin sintaxis de shell → ``shlex.split`` + :func:`ejecutar_comando_seguro`
      (``shell=False``).
    - Con sintaxis de shell → se mantiene ``shell=True`` (necesario para
      pipes/redirecciones) tras validar con :func:`es_comando_peligroso`. Si el
      comando es peligroso se delega en ``confirmar`` (``fn(comando) -> bool``);
      si el callable es ``None`` o rechaza, se aborta con :class:`RuntimeError`.

    Devuelve siempre un ``subprocess.CompletedProcess``.
    """
    texto = str(comando or "")
    if not texto.strip():
        raise ValueError("Comando vacío en ejecutar_comando_con_politica.")
    if not tiene_metacaracteres_shell(texto):
        argv = shlex.split(texto)
        return ejecutar_comando_seguro(
            argv,
            cwd=cwd,
            timeout=timeout,
            env=env,
            capturar_salida=capturar_salida,
            entrada=entrada,
        )
    if es_comando_peligroso(texto):
        if confirmar is None or not confirmar(texto):
            raise RuntimeError(
                f"Comando potencialmente peligroso no confirmado; no se ejecuta: {texto!r}"
            )
    return subprocess.run(
        texto,
        cwd=cwd,
        shell=True,
        env=env,
        capture_output=capturar_salida,
        input=entrada if capturar_salida else None,
        text=capturar_salida,
        errors="replace" if capturar_salida else None,
        timeout=timeout,
        creationflags=_flags_creacion(),
    )


def lanzar_proceso_fondo_seguro(
    comando: str,
    cwd: str | None = None,
    capturar_salida: bool = True,
) -> subprocess.Popen:
    """Lanza ``comando`` en segundo plano aplicando la política segura.

    Igual que :func:`ejecutar_comando_con_politica` pero vía
    :class:`subprocess.Popen` (no bloquea): sin sintaxis de shell el comando
    se divide con ``shlex.split`` y se ejecuta con ``shell=False``; con
    pipes/redirecciones se mantiene ``shell=True`` **tras validar** con
    :func:`es_comando_peligroso` (lanza :class:`RuntimeError` si es peligroso).

    Devuelve el objeto ``Popen`` (el llamador es responsable de registrarlo
    y leer sus pipes si capturó la salida).
    """
    texto = str(comando or "")
    if not texto.strip():
        raise ValueError("Comando vacío en lanzar_proceso_fondo_seguro.")
    if not tiene_metacaracteres_shell(texto):
        argv = shlex.split(texto)
        return subprocess.Popen(
            argv,
            cwd=cwd,
            shell=False,
            stdout=subprocess.PIPE if capturar_salida else None,
            stderr=subprocess.PIPE if capturar_salida else None,
            text=capturar_salida,
            errors="replace" if capturar_salida else None,
            creationflags=_flags_creacion(),
        )
    if es_comando_peligroso(texto):
        raise RuntimeError(
            f"Comando potencialmente peligroso rechazado para ejecución en segundo plano: {texto!r}"
        )
    return subprocess.Popen(
        texto,
        cwd=cwd,
        shell=True,
        stdout=subprocess.PIPE if capturar_salida else None,
        stderr=subprocess.PIPE if capturar_salida else None,
        text=capturar_salida,
        errors="replace" if capturar_salida else None,
        creationflags=_flags_creacion(),
    )
