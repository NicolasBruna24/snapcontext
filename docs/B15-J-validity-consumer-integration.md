# B15-J — Primer consumidor productivo de `ValidityResult`

Estado: **IMPLEMENTADO**. Sin `CONTRACT GAP`.

## 1. Objetivo

Integrar `evaluar_validez_verdict()` (B15-C/F) en F4, que desde B15-I ya produce
un `Verdict` estructurado, y demostrar experimentalmente la cadena completa con
Git real: `PRE → ejecución → Verdict → CURRENT → evaluar → ValidityResult`.

## 2. Punto de integración

`ReactAgent._tool_ejecutar_pruebas()` en `react_agent.py`, inmediatamente después
de `producir_verdict()` y antes de construir el dict de resultado. Es el punto
correcto: ahí termina la ejecución real de F4 y ahí se dispone del `Verdict`
recién producido. No hay ninguna otra captura de Git en el flujo.

## 3. Flujo

```text
estado_pre      = leer_estado_git(dir)      # B15-I, sin cambios
codigo, out, err = sc._ejecutar_comando(...) # sin cambios
verdict         = producir_verdict(exito=(codigo==0), comando, estado_pre)
estado_actual   = leer_estado_git(dir)      # NUEVO (B15-J)
validity        = evaluar_validez_verdict(verdict, estado_actual)
```

La segunda lectura **no** sustituye al estado PRE: solo alimenta al evaluador.
Si el estado PRE falla no hay `Verdict` y, por tanto, tampoco `validity`; el
evaluador nunca se invoca sin `Verdict`.

## 4. Caso VALID

`python3 -c "print('ok')"` sobre un repo limpio: el árbol no cambia →
`tree_pre == tree_current` → `ValidityResult(status=VALID, motivo=tree_coincidente)`.

## 5. Caso OBSOLETE

`python3 -c "open('generado.txt','w').write('x')" && python3 -c "print('ok')"`:
el comando verificado ensucia el árbol → `tree_pre != tree_current` →
`ValidityResult(status=OBSOLETE, motivo=tree_distinto)`. El `anchor.tree_sha`
sigue siendo el PRE, es decir, identifica lo **verificado**, no el estado final.

## 6. Caso dirty

Con el repo ya sucio antes de ejecutar y sin cambio posterior → `VALID`
(`working_tree_clean` no participa). Con dirty **y** modificación posterior →
`OBSOLETE`. Se demuestra además que con el **mismo `commit`** el resultado puede
ser `OBSOLETE`: la identidad no es `(commit, working_tree_clean)`.

## 7. Verificación fallida

`raise SystemExit(1)` sin tocar el árbol → `Verdict.resultado == "falla"` y
`ValidityResult == VALID`. Resultado y validez temporal son ortogonales.

## 8. Fallos PRE/CURRENT

* **PRE falla** (política B15-I conservada): `verdict=None`, `validity=None`,
  `verdict_error` presente, la verificación se ejecuta igualmente.
* **CURRENT falla**: `verdict` **se conserva** (el hecho ocurrió), `validity` es
  `None` y se expone `validity_error` con el motivo real del adapter
  (`sin_repositorio`, `git_no_disponible`, …). No se fabrica `VALID` ni
  `OBSOLETE`, y no se añade ninguna categoría a `ValidityResult`. El contrato
  actual sí permite representar el caso (ausencia + error explícito), así que
  no hay `CONTRACT GAP`.

## 9. Preservación del contrato de F4

El resultado sigue teniendo `ok`, `codigo`, `comando`, `stdout`, `stderr`,
`verdict` con la misma semántica, y ahora además `validity` (+ `validity_error`
cuando proceda). Todo es **aditivo**. `_observar_resultado()` no se ha tocado:
el LLM sigue viendo exactamente la misma observación.

## 10. Tests

`tests/test_b15j_validity_integration.py` — 22 tests con Git real:

* T1 `VALID` con árbol sin cambios; T2 `OBSOLETE` con comando que modifica;
* T3 `"falla"` + `VALID`; T4 dirty sin cambio → `VALID`; T5 dirty + cambio →
  `OBSOLETE`; T6 mismo commit pero `OBSOLETE` (identidad = `tree_sha`);
* T7 el anchor sale de la captura PRE; T8 `leer_estado_git` se llama
  **exactamente 2 veces**; T9 el evaluador recibe `(Verdict, CurrentGitState
  actual)`; T10 sustituir el evaluador cambia el resultado de F4 (delega, no
  reimplementa);
* T11 Git PRE falla → sin `verdict` ni `validity`; T12 Git CURRENT falla →
  `verdict` conservado, `validity=None`, `validity_error`; el evaluador no se
  llama sin `Verdict`;
* contrato histórico de F4 intacto, `_observar_resultado` intacto, sin
  persistencia, F1/F2 intactas;
* pureza del evaluador (sin `subprocess`/`open`/`Path`/Git en su cuerpo, no
  llama a `_correr_git`, determinista y no muta).

`tests/test_b15i_verdict_integration.py` se **actualizó** (no se debilitó) en
tres tests, porque B15-J cambia deliberadamente su premisa:

* `test_no_evalua_validez` → `test_no_evalua_validez_dentro_del_verdict`: ahora
  comprueba que el `Verdict` no lleva campo `validity` (sigue siendo un hecho,
  no su propia evaluación);
* `test_no_se_observa_git_una_segunda_vez` →
  `test_anchor_usa_la_captura_pre_y_no_la_current`: hay 2 capturas, y el anchor
  sale de la PRE;
* `test_f4_no_evalua_validez` → `test_f4_no_calcula_validez_por_si_mismo`: F4
  llama al evaluador canónico pero no usa `VALID`/`OBSOLETE` por su cuenta.

## 11. Regresión

| Suite | Resultado |
| ----- | --------- |
| B15-J (nuevo) | 22 passed |
| B15-I | 21 passed |
| todas las B15 (`-k b15`) | 169 passed |
| WORK/ReAct (`-k "react or work or b14 or b12"`) | 317 passed, 2 skipped |
| Suite completa (baseline B15-I) | 3446 passed, 43 skipped, 15 warnings |
| Suite completa (actual) | **3468 passed, 43 skipped, 15 warnings** |

Diferencia: +22 tests, 3 tests de B15-I actualizados, 0 fallos, mismos skips y
warnings.

## 12. Límites

Quedan **fuera**, deliberadamente: persistencia (`Verdict`/`ValidityResult` →
`state.md`), obsolescencia histórica y listas de verdicts obsoletos, F1/F2
(`AgenteTester`, `Orquestador`), consumo desde MCP, actualización automática de
`state.md`, y cualquier transición de estado (`ciclo_de_vida`,
`ultimo_veredicto`). Siguiente paso natural: un bloque de persistencia que
consuma lo que F4 ya expone.
