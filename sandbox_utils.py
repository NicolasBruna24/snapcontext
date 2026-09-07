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
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


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
