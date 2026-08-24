#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parche v2.3.0 parte 2f: docstring limpio y _refs_de_condicion."""
import ast
import io

p = "snapcontext.py"
BS = chr(92)
NL = chr(10)


def bs(texto):
    return texto.replace("@BS@", BS)


with io.open(p, encoding="utf-8") as f:
    src = f.read()


def reemplazar(viejo, nuevo):
    global src
    assert viejo in src, "NO ENCONTRADO >>> " + ascii(viejo[:100])
    src = src.replace(viejo, nuevo, 1)


# Docstring con barra invertida problemática → texto plano.
lineas = src.split(NL)
for i, linea in enumerate(lineas):
    if '"""Sustituye' in linea:
        lineas[i] = ('    """Sustituye la marca de doble llave {{clave}}'
                     ' por el valor que')
        lineas.insert(i + 1,
                      '    tenga esa clave en el contexto dinámico del plan.'
                      ' Si la clave')
        lineas.insert(i + 2,
                      '    no existe o el texto no es una cadena, se devuelve'
                      ' sin cambios.')
        break
src = NL.join(lineas)

# _refs_de_condicion: referencias de una condición dinámica.
ancla = "def _refs_de_condicion("
if ancla not in src:
    ancla_ins = ("def _resolver_marcadores_args(argumentos: dict) -> dict:")
    funcion = bs('''def _refs_de_condicion(condicion: str) -> tuple:
    """Extrae los índices de pasos y nombres de variables que usa una condición."""
    condicion = condicion or ""
    indices = set()
    for m in re.findall(r"pasos@BS@[@BS@(@BS@d+)@BS@]", condicion):
        try:
            indices.add(int(m) - 1)
        except ValueError:
            continue
    nombres = set(re.findall(r"resultados?@BS@.(@BS@w+)", condicion))
    for m in re.findall(r"(?:^|@BS@(|&&|@BS@|)@BS@s*([a-z_][@BS@w]*)"
                        r"@BS@s*(?:==|!=)", condicion):
        nombre = m[1] if isinstance(m, tuple) else m
        if nombre not in ("true", "false", "none", "ok"):
            nombres.add(nombre)
    return indices, nombres


''' ) + ancla_ins
    reemplazar(ancla_ins, funcion)

with io.open(p, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(src)
ast.parse(io.open(p, encoding="utf-8").read())
print("OK parte 2f")
