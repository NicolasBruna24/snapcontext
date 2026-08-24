#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parche v2.3.0 parte 2a: VERSION, helpers de contexto, evaluador, paso mcp."""
import ast
import io

p = "snapcontext.py"
BS = chr(92)
NLIT = BS + "n"
NL = chr(10)
I4 = " " * 4


def bs(texto):
    return texto.replace("@BS@", BS)


with io.open(p, encoding="utf-8") as f:
    src = f.read()


def reemplazar(viejo, nuevo):
    global src
    assert viejo in src, "NO ENCONTRADO >>> " + ascii(viejo[:100])
    src = src.replace(viejo, nuevo, 1)


# ── A) VERSION ───────────────────────────────────────────────────────────────
reemplazar('VERSION = "2.2.0"', 'VERSION = "2.3.0"')

# ── B) Helpers de contexto dinámico (antes de _CANDADO_GIT_PLAN) ────────────
ancla_candado = "_CANDADO_GIT_PLAN = threading.Lock()"
helpers = bs('''# --- Contexto dinámico del plan (v2.3.0) ------------------------------------
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
    """Sustituye @BS@{{clave}}@BS@ por el valor del contexto del plan.

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

    return re.sub(r"@BS@{@BS@{@BS@s*([@BS@w.]+)@BS@s*@BS@}@BS@}", _sustituir, texto)


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


''')
reemplazar(ancla_candado, helpers + ancla_candado)

with io.open(p, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(src)
ast.parse(io.open(p, encoding="utf-8").read())
print("OK parte 2a")
