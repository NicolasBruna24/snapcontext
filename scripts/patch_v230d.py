#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parche v2.3.0 parte 2d: marcadores y rama mcp en _ejecutar_paso_plan."""
import ast
import io

p = "snapcontext.py"
BS = chr(92)
NLIT = BS + "n"
NL = chr(10)


def bs(texto):
    return texto.replace("@BS@", BS)


with io.open(p, encoding="utf-8") as f:
    src = f.read()


def reemplazar(viejo, nuevo):
    global src
    assert viejo in src, "NO ENCONTRADO >>> " + ascii(viejo[:100])
    src = src.replace(viejo, nuevo, 1)


# ── Marcadores al inicio del paso ────────────────────────────────────────────
viejo_acc = (
    '    accion = paso["accion"]' + NL +
    '    descripcion = paso["descripcion"]'
)
nuevo_acc = viejo_acc + NL + bs('''    # v2.3.0: sustitución de marcadores {{variable}} / {{resultado}} en los
    # campos del paso usando el contexto dinámico del plan.
    descripcion = _resolver_marcadores(descripcion)
    for _clave in ("comando", "herramienta", "contenido"):
        _valor = paso.get(_clave)
        if isinstance(_valor, str) and "{{" in _valor:
            paso[_clave] = _resolver_marcadores(_valor)
    if isinstance(paso.get("archivos"), list):
        paso["archivos"] = [_resolver_marcadores(a) for a in paso["archivos"]]
    if isinstance(paso.get("args"), dict) and paso["args"]:
        paso["args"] = _resolver_marcadores_args(paso["args"])''')
reemplazar(viejo_acc, nuevo_acc)

# ── Detalles de confirmación para mcp ───────────────────────────────────────
viejo_det = (
    '    elif accion == "editar":' + NL +
    '        detalles_paso = "' + NLIT + '".join(paso.get("archivos", []))'
    ' or None' + NL +
    '    else:' + NL +
    '        detalles_paso = None'
)
nuevo_det = (
    '    elif accion == "editar":' + NL +
    '        detalles_paso = "' + NLIT + '".join(paso.get("archivos", []))'
    ' or None' + NL +
    '    elif accion == "mcp":' + NL +
    '        detalles_paso = str(paso.get("herramienta") or "")' + NL +
    '    else:' + NL +
    '        detalles_paso = None'
)
reemplazar(viejo_det, nuevo_det)

# ── Rama de ejecución mcp (antes de "consultar") ────────────────────────────
ancla_consultar = '    if accion == "consultar":'
rama_mcp = bs('''    # accion == "mcp" (v2.3.0): ejecuta una herramienta MCP y deja su
    # resultado en el contexto del plan para los pasos siguientes.
    if accion == "mcp":
        herramienta = paso.get("herramienta")
        if not herramienta:
            return (False, 'el paso no indica "herramienta"')
        argumentos = _resolver_marcadores_args(paso.get("args") or {})
        info(("[mcp] " + herramienta + " " + str(argumentos)).rstrip())
        llamada = _ejecutar_herramienta_mcp(herramienta, argumentos)
        res = llamada.get("resultado", {})
        try:
            muestra = json.dumps(res, ensure_ascii=False)
        except Exception:
            muestra = str(res)
        if len(muestra) > 400:
            muestra = muestra[:400] + "…"
        if llamada.get("ok"):
            exito("[mcp] resultado: " + muestra)
            _contexto_plan_variable(str(paso.get("variable") or herramienta),
                                    res)
            return (True, herramienta + ": ok")
        error("[mcp] falló: " + muestra)
        return (False, herramienta + ": "
                + str(res.get("error", "fallo")))

''') + ancla_consultar
reemplazar(ancla_consultar, rama_mcp)

with io.open(p, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(src)
ast.parse(io.open(p, encoding="utf-8").read())
print("OK parte 2d")
