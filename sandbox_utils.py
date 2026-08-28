#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

import re
from typing import List, Tuple

# Registro extensible de patrones de comandos peligrosos.
# Cada elemento es ``(regex_compilada, descripcion)``.
#
# Nota de diseño (evita falsos positivos):
#   - ``> /dev/null`` NO se considera peligroso (es una redirección inocua a un
#     dispositivo nulo que cierra la salida). Solo se marcan las escrituras a
#     **dispositivos de bloque reales** (/dev/sda*, /dev/nvme*, ...), que sí
#     pueden destruir el disco.
_PATRONES_PELIGROSOS: List[Tuple[re.Pattern, str]] = [
    # ── Eliminación masiva de archivos ─────────────────────────────────────
    (re.compile(r"\brm\s+-(?:rf|fr|r\s+-f|f\s+-r)\s+"
                r"(?:/(?=$|\*)|/\*\s*|\*(?=\s|$)|\.(?=\s|$)|~(?=\s|$))",
                re.IGNORECASE),
     "rm -rf sobre ruta raíz/usuario (borrado masivo)"),
    (re.compile(r"\brm\s+-r\s+-f\s+(?:/|\.\s|\.$)", re.IGNORECASE),
     "rm -rf sobre directorio raíz/actual"),
    (re.compile(r"\brm\s+--no-preserve-root\b", re.IGNORECASE),
     "rm con omisión de protección de raíz"),
    # ── Manipulación de discos / particiones ────────────────────────────────
    (re.compile(r"\bdd\s+(?:if=|of=|bs=|conv=)", re.IGNORECASE),
     "dd con gestión de dispositivos (if=/of=)"),
    (re.compile(r"\bmkfs\b[\w.-]*", re.IGNORECASE),
     "mkfs (formatear sistema de archivos)"),
    (re.compile(r"\bfdisk\b", re.IGNORECASE),
     "fdisk (particionado de disco)"),
    (re.compile(r"\bwipefs\b|\bmkswap\b|\bparted\s+-a\s+optimal", re.IGNORECASE),
     "borrado de firmas / formateo / particionado agresivo"),
    # ── Descarga y ejecución de scripts remotos ─────────────────────────────
    (re.compile(r"\bcurl\s+.*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b",
                re.IGNORECASE),
     "curl piped a shell (descarga y ejecución)"),
    (re.compile(r"\bwget\s+.*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b",
                re.IGNORECASE),
     "wget piped a shell (descarga y ejecución)"),
    (re.compile(r"\b(?:curl|wget)\b.*\|\s*sudo\s+(?:sh|bash|zsh)\b",
                re.IGNORECASE),
     "descarga remota ejecutada con sudo"),
    # ── Cambios de permisos peligrosos ──────────────────────────────────────
    (re.compile(r"\bchmod\s+-R\s*\+?\s*777\b", re.IGNORECASE),
     "chmod -R 777 sobre el árbol"),
    (re.compile(r"\bchmod\s+777\s*/?(?:\s|$)", re.IGNORECASE),
     "chmod 777 sobre la raíz o amplio"),
    (re.compile(r"\bchmod\s+[0-7]{4}\s+/(?:\s|$)", re.IGNORECASE),
     "chmod de la raíz"),
    (re.compile(r"\bchown\s+-R\b", re.IGNORECASE),
     "chown -R (cambio de propietario recursivo)"),
    # ── Fork bomb ───────────────────────────────────────────────────────────
    (re.compile(r":\s*\(\s*\)\s*\{", re.IGNORECASE),
     "fork bomb (bucle recursivo infinito)"),
    # ── sudo con comandos peligrosos ────────────────────────────────────────
    (re.compile(r"\bsudo\s+(?:rm\s+-rf|mkfs|dd|fdisk|shutdown|reboot|reboot|"
                r"poweroff|halt)\b", re.IGNORECASE),
     "sudo + comando destructivo"),
    # ── Escritura en dispositivos de bloque (excluye /dev/null a propósito) ─
    (re.compile(r">\s*/dev/(?:sd[a-z]+\d*|hd[a-z]+\d*|nvme\d+n\d+|"
                r"mmcblk\d+|mapper/\S+|disk/by-id/\S+|mem|shm)\b",
                re.IGNORECASE),
     "escritura en buffer de dispositivo de bloque"),
    # ── Terminación de procesos críticos ────────────────────────────────────
    (re.compile(r"\bkill\s+-9\b", re.IGNORECASE),
     "kill -9 (forzar terminación)"),
    (re.compile(r"\bpkill\b", re.IGNORECASE),
     "pkill (terminación masiva de procesos)"),
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


def patrones_peligrosos_info() -> List[Tuple[str, str]]:
    """Devuelve las descripciones de los patrones (para logging/auditoría)."""
    return [(p.pattern, d) for p, d in _PATRONES_PELIGROSOS]