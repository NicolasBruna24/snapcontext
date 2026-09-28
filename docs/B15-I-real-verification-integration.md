# B15-I — Integración del productor de Verdict en el flujo real F4 (ReAct)

Estado: **IMPLEMENTADO** (B15-I es implementación + verificación experimental;
no introduce decisiones arquitectónicas nuevas).

## 1. Objetivo

Integrar `producir_verdict()` (B15-H) en el flujo **productivo** de SnapContext
para demostrar experimentalmente que una verificación real produce un `Verdict`
cuyo `GitAnchor` se apoya en el **estado efectivo del árbol capturado antes de
ejecutar**.

La identidad sigue siendo `tree_sha` (B15-E/F) y la validez sigue siendo
responsabilidad de `evaluar_validez_verdict()` (B15-C). B15-I no cambia ninguna
de las dos cosas.

## 2. F4 seleccionado

F4 = `ReactAgent._tool_ejecutar_pruebas()` en `react_agent.py`.

Evidencia de que es el flujo productivo y predeterminado:

* `react_agent.py` es el motor ReAct que arranca con `snapcontext --react`, y su
  bucle (Thought → Action → Observación) es el modo de ejecución por defecto
  (el `--plan` es el modo alternativo, explícito).
* `_tool_ejecutar_pruebas` es la **única** acción del catálogo ReAct que ejecuta
  una verificación real: llama a `sc._ejecutar_comando()` con el comando de test
  resuelto.
* No es F1 (`AgenteTester.ejecutar_pruebas`, bucle de tests coordinado), ni F2
  (`Orquestador._bucle_test`), ni F3 (sin caller productivo), ni F5 (comandos
  de plan).

## 3. Punto exacto de integración

Dentro de `_tool_ejecutar_pruebas`, en este orden:

1. resolución del comando (sin cambios);
2. **captura Git pre-ejecución**: `leer_estado_git(self.directorio)`;
3. `sc._ejecutar_comando(comando, directorio, timeout=600)` (sin cambios);
4. `producir_verdict(exito=(codigo == 0), comando=comando, estado=estado)`;
5. el `Verdict` viaja al agente en la **clave aditiva** `resultado["verdict"]`.

No hay segunda captura de Git: el `tree_sha` del `Verdict` es exactamente el
del paso 2.

## 4. Decisiones de integración

| Campo | Valor | Motivo |
| ----- | ----- | ------ |
| `exito` | `codigo == 0` | semántica de éxito ya existente en F4 |
| `comando` | comando efectivo ya resuelto por F4 | comando realmente ejecutado |
| `scope` | `None` → `SCOPE_NO_DEFINIDO` | F4 no tiene noción contractual de scope |
| `autor` / `instante` | `None` | F4 no los tiene; el productor ya define eso |

`producir_verdict()` **no se modificó**: su contrato público es idéntico al de
B15-H. No hubo `CONTRACT GAP`.

## 5. Preservación del contrato de F4

El resultado sigue siendo el mismo `dict` con `ok`, `codigo`, `comando`,
`stdout`, `stderr` y la misma semántica (`ok == (codigo == 0)`). Solo se **añade**
`verdict`. `_observar_resultado()` (la transformación que ve el LLM) no cambia y
no menciona el `Verdict`.

## 6. Comportamiento cuando Git es inobservable

`leer_estado_git()` falla → **no se inventa anchor**: `verdict = None` y se añade
`verdict_error` con el motivo real (`sin_repositorio`, `head_no_disponible`,
`git_no_disponible`, `directorio_inexistente`). El error no se oculta.

La ejecución del comando **sí** se realiza en ese caso, para no romper el
comportamiento preexistente de F4 en directorios que no son repositorio (el test
preexistente `tests/test_react_510.py::test_ejecutar_pruebas_captura_el_fallo`
trabaja sobre un tmpdir sin repo). El requisito de B15-I es no *inventar* un
anchor, no abortar la verificación; abortar sería un cambio de contrato del flujo
más invasivo que la propia integración.

## 7. Tests

`tests/test_b15i_verdict_integration.py` — 21 tests, repos Git reales y
temporales + `ReactAgent` real:

* T1 ejecución exitosa → `"pasa"` + anchor real;
* T2 ejecución fallida → `"falla"` + anchor de la captura previa;
* T3 `tree_sha` del Verdict = estado capturado antes, y `leer_estado_git` se
  invoca **una sola vez**;
* T4 comando que **modifica el working tree**: el anchor sigue siendo el
  pre-ejecución mientras el árbol post sí cambió;
* T5 árbol sucio antes de ejecutar → `working_tree_clean is False`;
* T6 `commit` del anchor = `CurrentGitState` previo, no recalculado;
* T7 no se llama a `evaluar_validez_verdict()`; el Verdict no es `ValidityResult`
  ni `OBSOLETE`;
* T8 no aparecen `state.md` / `decisions.md` / `assertions.md` / `.work`;
* T9 contrato de F4 intacto y `_observar_resultado` sigue funcionando;
* T10 fallo de Git: sin anchor inventado, error visible, contrato previo intacto;
* Frontera: F4 no contiene `rev-parse`/`write-tree`/`git add`/`git status`/
  `subprocess`/`tree_sha`, no evalúa validez, no construye `GitAnchor(` ni
  `Verdict(`, y F1/F2 (`agentes.py`, `orquestador.py`, `qa_tester_logic.py`)
  siguen sin mencionar `producir_verdict`.

## 8. Límites — qué NO se implementó

* No se integra `evaluar_validez_verdict()` en F4.
* No se persiste nada (ni `state.md`, ni `decisions.md`, ni `assertions.md`, ni
  `.work/`), ni historial, ni obsolescencia.
* No se toca MCP ni `work_context.py`.
* No se integra F1 (`AgenteTester.ejecutar_pruebas`) ni F2
  (`Orquestador._bucle_test`); F4 es el **primer** consumidor, no el productor
  canónico, que sigue siendo `work_verdict.producir_verdict`.
* No hay consumidor de `ValidityResult` todavía.

## 9. Siguiente paso

1. Incorporar F1/F2 **reutilizando** `producir_verdict()` (sin duplicar
   semántica) y decidir si comparten la captura pre-ejecución.
2. Etapa de consumidor: un flujo que evalúe el `Verdict` contra el estado actual
   y exponga `VALID`/`OBSOLETE`/`UNDETERMINED`.
3. Solo después, persistencia (`ultimo_veredicto`, `veredictos_obsoletos`).

## 10. Git state before / after

```text
HEAD before: 45e2e0a71d0689a979da1f492914f8ab8faf1552
HEAD after:  45e2e0a71d0689a979da1f492914f8ab8faf1552   (sin commit ni push)
```

Cambios de B15-I: `react_agent.py` (modificado),
`tests/test_b15i_verdict_integration.py` (nuevo), este documento (nuevo). El
resto del `git status` (`benchmarks/results.json`, `exceptions.py`,
`graph_lsp_integrator.py`, `graph_rag.py`, `mcp_tools.py`, `seguridad.py`,
`snapcontext.py`, `tests/test_plan_012.py`, `work_context.py`, `work_verdict.py`,
tests B14/B15 y docs B15 previos) es **preexistente** y no se tocó. Sin
`reset`/`restore`/`checkout`/`clean`.

## 11. Regresión

| Suite | Resultado |
| ----- | --------- |
| B15-I | 21 passed |
| B15-H | 41 passed |
| B15-B (adapter Git) | 36 passed |
| B15-C (evaluador) | 33 passed |
| B15-F (identidad) | 16 passed |
| WORK/ReAct (`-k "react or work or b14 or b12 or b15"`) | 452 passed, 2 skipped |
| Suite completa sin el fichero nuevo (baseline) | 3425 passed, 43 skipped, 15 warnings |
| Suite completa | **3446 passed, 43 skipped, 15 warnings** |

Diferencia: +21 tests, 0 fallos, mismos skips y warnings. Ningún test
preexistente se modificó.

