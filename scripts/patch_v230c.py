#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parche v2.3.0 parte 2c: resolutor de operandos y normalización."""
import ast
import io

p = "snapcontext.py"
BS = chr(92)


def bs(texto):
    return texto.replace("@BS@", BS)


with io.open(p, encoding="utf-8") as f:
    src = f.read()

ancla = "def _partir_argumentos("
nuevo = bs('''# Sentinela fin de evaluador
_DESCONOCIDO = object()


def _resolver_operando_condicion(operando: str, contexto: dict):
    """Convierte un operando de condición en un valor Python concreto.

    Acepta literales ('texto', números, true/false/null) y referencias al
    contexto: pasos[N].campo, resultados.nombre o un identificador simple.
    Devuelve _DESCONOCIDO si no se puede resolver.
    """
    operando = operando.strip()
    if len(operando) >= 2 and operando[0] in "'@BS@\"" \\
            and operando[-1] == operando[0]:
        return operando[1:-1]
    if operando.lower() in ("true", "verdad"):
        return True
    if operando.lower() in ("false", "falso"):
        return False
    if operando.lower() in ("none", "null", "nulo"):
        return None
    try:
        return int(operando)
    except ValueError:
        pass
    try:
        return float(operando)
    except ValueError:
        pass

    m = re.match(r"^pasos@BS@[@BS@(@BS@d+)@BS@]@BS@.(@BS@w+)$", operando)
    if m:
        numero, campo = int(m.group(1)), m.group(2)
        with _CANDADO_CONTEXTO_PLAN:
            paso_ctx = contexto.get("pasos", {}).get(str(numero))
        if not isinstance(paso_ctx, dict) or campo not in paso_ctx:
            return _DESCONOCIDO
        return paso_ctx[campo]

    m = re.match(r"^resultados?@BS@.(@BS@w+)$", operando)
    if m:
        with _CANDADO_CONTEXTO_PLAN:
            variables = contexto.get("variables", {})
        return variables.get(m.group(1), _DESCONOCIDO)

    if re.match(r"^[a-z_][@BS@w]*$", operando):
        with _CANDADO_CONTEXTO_PLAN:
            variables = contexto.get("variables", {})
        return variables.get(operando, _DESCONOCIDO)

    return _DESCONOCIDO


def _normalizar_comparacion(valor):
    """Normaliza valores para poder compararlos entre sí."""
    if isinstance(valor, bool):
        return "ok" if valor else "fallo"
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, (dict, list)):
        try:
            import json as _json
            return _json.dumps(valor, sort_keys=True, ensure_ascii=False)
        except Exception:
            return str(valor)
    return str(valor)


''')
assert ancla in src
src = src.replace(ancla, nuevo + ancla, 1)

with io.open(p, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(src)
ast.parse(io.open(p, encoding="utf-8").read())
print("OK parte 2c")
