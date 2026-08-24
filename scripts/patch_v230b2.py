#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parche v2.3.0 parte 2b: _evaluar_condicion con comparaciones dinámicas."""
import ast
import io

p = "snapcontext.py"
BS = chr(92)


def bs(texto):
    return texto.replace("@BS@", BS)


with io.open(p, encoding="utf-8") as f:
    src = f.read()

inicio = src.index("def _evaluar_condicion(")
fin = src.index("def _partir_argumentos(")

evaluador = bs('''def _evaluar_condicion(condicion: str, raiz: str = ".",
                      contexto: Optional[dict] = None) -> bool:
    """Evalúa la condición de un paso del plan. Devuelve True si se cumple.

    Formatos soportados:

      Funciones (v1.4.0):
        archivo_existe('src/main.py')
        archivo_contiene('src/main.py', 'def main')
        comando_exito('flutter test')
        variable_existe('mi_variable')            # v2.3.0

      Comparaciones dinámicas (v2.3.0), con resultados de pasos previos o
      variables dejadas en el contexto (p. ej. por pasos "mcp"):
        pasos[0].resultado == 'ok'
        pasos[2].resultado != 'fallo'
        resultados.mi_variable == 'listo'
        mi_variable != ''                         # forma abreviada

    Las cadenas pueden ir con comillas simples o dobles. Cualquier condición
    mal formada o desconocida devuelve False con un aviso (fallo elegante:
    el paso se salta, nunca se aborta el plan).
    """
    if contexto is None:
        contexto = _CONTEXTO_PLAN
    condicion = (condicion or "").strip()
    if not condicion:
        return True

    # 1) Comparaciones dinámicas (== / !=).
    comparacion = re.match(r"^(.+?)@BS@s*(==|!=)@BS@s*(.+)$", condicion, re.S)
    if comparacion and "(" not in condicion.split("==")[0].split("!=")[0]:
        izquierdo = _resolver_operando_condicion(
            comparacion.group(1).strip(), contexto)
        derecho = _resolver_operando_condicion(
            comparacion.group(3).strip(), contexto)
        if izquierdo is _DESCONOCIDO or derecho is _DESCONOCIDO:
            aviso(f"Condición con referencia desconocida: '{condicion}'.")
            return False
        iguales = (_normalizar_comparacion(izquierdo)
                   == _normalizar_comparacion(derecho))
        return iguales if comparacion.group(2) == "==" else not iguales

    # 2) Formas funcionales clásicas.
    coincidencia = re.match(r"^([a-zA-Z_]@BS@w*)@BS@s*@BS@((.*)@BS@)@BS@s*$",
                            condicion, re.S)
    if not coincidencia:
        aviso(f"Condición de paso mal formada: '{condicion}'. Se interpreta "
              f"como no cumplida.")
        return False
    funcion, crudo_args = coincidencia.group(1), coincidencia.group(2)
    try:
        argumentos = [a.strip()
                      for a in _partir_argumentos(crudo_args)]
    except ValueError as exc:
        aviso(f"Condición inválida '{condicion}': {exc}")
        return False

    if funcion == "archivo_existe":
        return len(argumentos) == 1 and (Path(raiz) / argumentos[0]).exists()
    if funcion == "archivo_contiene":
        if len(argumentos) != 2:
            return False
        contenido = _leer_archivo(Path(raiz) / argumentos[0])
        return contenido is not None and argumentos[1] in contenido
    if funcion == "comando_exito":
        if not argumentos or not argumentos[0]:
            return False
        codigo, _, _ = _ejecutar_comando(argumentos[0], raiz, timeout=300)
        return codigo == 0
    if funcion == "variable_existe":
        with _CANDADO_CONTEXTO_PLAN:
            variables = dict(contexto.get("variables", {}))
        return bool(argumentos) and argumentos[0] in variables

    aviso(f"Función de condición desconocida: '{funcion}'. Soportadas: "
          f"archivo_existe, archivo_contiene, comando_exito, "
          f"variable_existe.")
    return False


''')

src = src[:inicio] + evaluador + "# Sentinela fin de evaluador" + src[fin:]
with io.open(p, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(src)
ast.parse(io.open(p, encoding="utf-8").read())
print("OK parte 2b")
