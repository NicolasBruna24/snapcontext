"""Planificador: gestión de contexto y visualización de planes (Fase 5)."""
from threading import Lock
from typing import List, Optional

# --- Contexto dinámico del plan (v6.30.0) ---------------------------------
_CONTEXTO_PLAN = {"variables": {}, "pasos": {}}
_CANDADO_CONTEXTO_PLAN = Lock()


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



# --- Visualización resumida del plan (v6.23.0) ----------------------------
def _mostrar_plan_resumido(plan: Optional[list]) -> str:
    """Devuelve un resumen legible del plan en 3-5 líneas (v6.23.0).

    En lugar de listar el plan completo, genera una frase compacta
    ``"Voy a: 1) leer el login, 2) corregir el error, ..."``; si hay más de 5
    pasos añade ``"y N más"``. Devuelve ``""`` si el plan está vacío.
    """
    if not plan:
        return ""
    pasos = list(plan)[:5]
    trozos: List[str] = []
    for i, paso in enumerate(pasos, start=1):
        if isinstance(paso, dict):
            desc = paso.get("descripcion") or paso.get("comando") or ""
            desc = str(desc).strip()
        else:
            desc = str(paso).strip()
        trozos.append(f"{i}) {desc}".strip())
    resumen = ", ".join(t for t in trozos if t)
    resto = len(list(plan)) - len(pasos)
    if resto > 0:
        resumen += f" y {resto} más"
    return f"Voy a: {resumen}"

