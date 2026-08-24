#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parche v2.3.0 parte 2e: contexto en secuencial + paralelo dinámico."""
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


# ── Secuencial: reinicio de contexto ────────────────────────────────────────
ancla_hilos = '    max_hilos = max(1, int(getattr(args, "paralelo", 1) or 1))'
reemplazar(
    ancla_hilos,
    '    _contexto_plan_reiniciar()   # v2.3.0: contexto dinámico por plan'
    + NL + ancla_hilos)

# ── Secuencial: registro del resultado de cada paso ─────────────────────────
ancla_estado = '            estado_seq[indice] = "éxito" if ok else "fallo"'
reemplazar(
    ancla_estado,
    '            _registrar_resultado_plan(numero, ok, detalle)'
    + NL + ancla_estado)

# ── Paralelo: registro dentro del hilo ──────────────────────────────────────
ancla_marca = '    marca = "✔" if ok else "✖"'
reemplazar(
    ancla_marca,
    '    _registrar_resultado_plan(numero, ok, detalle)' + NL + ancla_marca)

# ── Paralelo: lanzables con dependencias dinámicas ──────────────────────────
viejo_lanzables = bs('''            lanzables = [i for i in sorted(pendientes)
                         if all(estado.get(d) == "éxito"
                                for d in (d - 1 for d in
                                          (pasos[i].get("dependencias")
                                           or [])))]
            if not lanzables:''')
nuevo_lanzables = bs('''            # v2.3.0: además de las dependencias explícitas, un paso queda
            # bloqueado mientras su condición referencie variables que algún
            # paso pendiente aún puede producir (p. ej. un paso "mcp").
            producibles = set()
            for j in pendientes:
                _pj = pasos[j]
                _ri, _rv = _refs_de_condicion(_pj.get("condicion") or "")
                producibles |= _rv
                if _pj.get("accion") == "mcp":
                    producibles.add(str(_pj.get("variable")
                                        or _pj.get("herramienta") or ""))
                    producibles.add("resultado")

            def _listo(i):
                deps = [d - 1 for d in (pasos[i].get("dependencias") or [])]
                if any(estado.get(d) != "éxito" for d in deps):
                    return False
                ref_i, ref_v = _refs_de_condicion(
                    pasos[i].get("condicion") or "")
                if any(estado.get(d) != "éxito" for d in ref_i):
                    return False
                with _CANDADO_CONTEXTO_PLAN:
                    disponibles = set(_CONTEXTO_PLAN["variables"])
                    registrados = set(_CONTEXTO_PLAN["pasos"])
                for v in ref_v:
                    if v not in disponibles and v in producibles:
                        return False       # esperar a que se produzca
                for d in ref_i:
                    if str(d + 1) not in registrados:
                        return False
                return True

            lanzables = [i for i in sorted(pendientes) if _listo(i)]
            if not lanzables:''')
reemplazar(viejo_lanzables, nuevo_lanzables)

with io.open(p, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(src)
ast.parse(io.open(p, encoding="utf-8").read())
print("OK parte 2e")
