#!/usr/bin/env python3
"""
Gestion de permisos y confirmaciones de usuario de SnapContext.

Este modulo centraliza todo lo relacionado con:
    - El almacenamiento persistente de preferencias del usuario
       (~/.snapcontext/permisos.json).
    - El interruptor global CONFIRMAR_ACCIONES.
    - El flujo interactivo de confirmacion de acciones (_confirmar_accion).

Diseno de dependencias (sin ciclos):
    - UI: import perezoso desde presentacion.
    - CONFIG_DIR: import perezoso dentro de funciones para evitar ciclo
      snapcontext -> permisos -> presentacion.
"""

import json
import sys
from pathlib import Path

from presentacion import (
    _AMARILLO,
    _emitir,
    _pintar,
    aviso,
    depurar,
    error,
    exito,
    info,
)

# --- Estado global ---
PERMISOS_PATH = None  # Se resuelve via _ruta_permisos()
CONFIRMAR_ACCIONES = True


def _obtener_config_dir() -> Path:
    import snapcontext

    return snapcontext.CONFIG_DIR


def _ruta_permisos() -> Path:
    import snapcontext

    parchado = getattr(snapcontext, "PERMISOS_PATH", None)
    if parchado is not None:
        return Path(parchado)
    return _obtener_config_dir() / "permisos.json"


def _cargar_permisos() -> dict:
    """Devuelve las preferencias guardadas en ~/.snapcontext/permisos.json.

    Formato: {"<tipo>": "siempre" | "nunca"} para cada tipo de acción
    ("editar", "ejecutar", "consultar", ...). Archivo corrupto → {}.
    """
    ruta = _ruta_permisos()
    try:
        if ruta.is_file():
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                return {str(k): str(v) for k, v in datos.items()}
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"No se pudieron leer los permisos ({ruta}): {exc}")
    return {}


def _guardar_permiso(tipo: str, valor: str) -> bool:
    """Guarda ``{"<tipo>": valor}`` en permisos.json (valor: siempre/nunca)."""
    try:
        ruta = _ruta_permisos()
        permisos = _cargar_permisos()
        permisos[tipo] = valor
        _obtener_config_dir().mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(permisos, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        aviso(f"No se pudo guardar el permiso ({ruta}): {exc}")
        return False


def _permiso_recordado(tipo: str) -> bool | None:
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
    ruta = _ruta_permisos()
    try:
        if ruta.exists():
            ruta.unlink()
            exito(f"Permisos restablecidos ({ruta} borrado).")
        else:
            info("No hay preferencias de permisos guardadas.")
        return True
    except OSError as exc:
        error(f"No se pudieron borrar los permisos: {exc}")
        return False


def _confirmar_accion(  # noqa: C901  (refactor de complejidad: Fase 10c)
    descripcion: str,
    tipo: str = "editar",
    detalles: str | None = None,
    confirmar: bool | None = None,
) -> bool:
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
    import snapcontext

    activo = snapcontext.CONFIRMAR_ACCIONES if confirmar is None else confirmar
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
    ruta = _ruta_permisos()
    while True:
        try:
            eleccion = (
                input(
                    _pintar(
                        "¿Permitir esta acción? "
                        "[s]í · [n]o · [t]odos este tipo · [a]nular todas (s/n/t/a): ",
                        _AMARILLO,
                    )
                )
                .strip()
                .lower()
            )
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
            exito(
                f"Se recordará: '{tipo}' siempre permitido "
                f"({ruta}). Usa --init o borra el archivo para "
                "restaurar las preguntas."
            )
            return True
        if eleccion in ("a", "anular", "nunca"):
            _guardar_permiso(tipo, "nunca")
            aviso(f"Se recordará: '{tipo}' nunca permitido ({ruta}).")
            return False
        aviso("Opción no válida; responde s, n, t o a.")
