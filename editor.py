"""Helpers puros del editor de parches (Fase 2, refactor del monolito).

Funciones independientes de análisis/reparación de diffs unificados extraídas
de ``snapcontext.py``. No tienen dependencias del estado global del agente.

Solo ``_ratio_bloque`` usa la constante ``UMBRAL_DIFUSO_BLOQUE`` de la fachada
``snapcontext``; se resuelve con import diferido (como en ``planificador.py``).

Las funciones de aplicación de parches que dependen del logging/TUI de
``snapcontext`` (``_aplicar_parche``, ``aplicar_reemplazo_estructurado``,
``_mostrar_diff_parche``) se mantienen en el monolito; ver Fase 2b.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

__all__ = [
    "_contar_cambios_parche",
    "_lineas_equivalentes",
    "_parsear_hunks",
    "_quitar_comentario",
    "_ratio_bloque",
    "_ruta_del_parche",
    "_validar_parche_previo",
    "_variantes_linea",
]


def _ruta_del_parche(parche: str) -> str | None:
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


def _validar_parche_previo(parche: str, directorio: str, contenido_esperado: str | None) -> tuple:
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
        return False, (
            f"'{ruta}' cambió desde que se generó el parche (posible cambio concurrente)"
        )
    return True, "el archivo coincide con la referencia"


def _parsear_hunks(parche: str) -> list[tuple]:
    """Divide un diff unificado en hunks ``(linea_inicio_original, cambios)``.

    ``cambios`` es una lista de ``(marca, texto)`` con marca ' ', '-' o '+'.
    Se omiten los hunks sin líneas modificadas. Devuelve [] si no hay ninguno.
    """
    hunks: list[tuple] = []
    hunk_actual: list[tuple] | None = None
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
            continue  # encabezados ---/+++/ruido
        if texto.startswith("+"):
            hunk_actual.append(("+", texto[1:]))
        elif texto.startswith("-"):
            hunk_actual.append(("-", texto[1:]))
        else:
            hunk_actual.append((" ", texto[1:] if texto else ""))
    if hunk_actual:
        hunks.append((inicio_orig, hunk_actual))
    return [(i, hunk) for i, hunk in hunks if any(marca != " " for marca, _ in hunk)]


def _quitar_comentario(linea: str) -> str:
    """Elimina de forma conservadora un comentario final ``#`` o ``//``.

    Solo se recorta si el marcador está al inicio de la línea o va precedido
    de un espacio (así no se rompen URLs tipo ``https://…`` ni cadenas que
    contengan ``#``). Devuelve la línea sin el comentario y sin espacios
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


def _variantes_linea(linea: str) -> tuple[str, str, str]:
    """Variantes progresivamente más laxas de una línea (v6.3.0).

    1. La línea tal cual (sin salto final).
    2. Con los espacios colapsados (tolera indentación/espacios extra).
    3. Además, sin comentario final ``#``/``//`` (tolera comentarios
       añadidos o eliminados por el usuario o el formateador).

    Se usan en el emparejamiento por variantes del editor de parches; la
    variante 1 reproduce la comparación exacta histórica.
    """
    cruda = linea.rstrip("\r\n")
    normalizada = " ".join(cruda.split())
    return cruda, normalizada, _quitar_comentario(normalizada)


def _lineas_equivalentes(a: str, b: str) -> bool:
    """True si dos líneas coinciden en alguna de sus variantes (v6.3.0)."""
    va, vb = _variantes_linea(a), _variantes_linea(b)
    return va[0] == vb[0] or va[1] == vb[1] or va[2] == vb[2]


def _ratio_bloque(a: str, b: str) -> float:
    """Ratio de similitud de dos bloques de texto (v6.3.0).

    ``SequenceMatcher.real_quick_ratio`` y ``quick_ratio`` son cotas
    superiores del ratio final: se usan para descartar ventanas imposibles
    sin pagar el coste completo. Devuelve 0.0 si no supera
    ``UMBRAL_DIFUSO_BLOQUE``.
    """
    import snapcontext as _sc  # perezoso: evita import circular (constante)

    sm = difflib.SequenceMatcher(None, a, b)
    if (
        sm.real_quick_ratio() < _sc.UMBRAL_DIFUSO_BLOQUE
        or sm.quick_ratio() < _sc.UMBRAL_DIFUSO_BLOQUE
    ):
        return 0.0
    return sm.ratio()


def _contar_cambios_parche(parche: str) -> tuple[int, int]:
    """Cuenta ``(añadidas, eliminadas)`` en un diff unificado (v6.3.0)."""
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
