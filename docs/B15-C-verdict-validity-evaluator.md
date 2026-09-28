# B15-C — Pure Verification Verdict Validity Evaluator

> Añade `ValidityResult` y `evaluar_validez_verdict()` a `work_verdict.py`.
> No modifica WorkState, ni `state.md`, ni MCP, ni el agente. Sin commit/push.

## 1. Qué implementa (versión original)

El evaluador puro definido en B15-A §9, sobre las entradas de B15-B:

```
Verdict.git_anchor  +  CurrentGitState  →  ValidityResult
```

## 2. CORRECCIÓN DE CONTRATO (B15-E / B15-F)

> **Este bloque originalmente usaba `(commit, working_tree_clean)` como identidad.
> B15-E demostró empíricamente que era incorrecto; B15-F lo corrigió.**

Evidencia de la corrección (ejecutada contra el evaluador original):

| Escenario | Anchor | Actual | B15-C original | Realidad |
|---|---|---|---|---|
| Caso 6 | `(X, dirty)` | `(X, dirty)` | `VALID` | **el contenido cambió** |
| Caso 5 | `(X, dirty)` | `(Y, clean)` | `OBSOLETE` | **el contenido es idéntico** |

Dos fallos opuestos. Con el árbol sucio —el estado normal del bucle agéntico— el
par histórico es **constante** mientras el contenido cambia, así que no puede
funcionar como identidad.

**Semántica vigente (B15-F):**

* `VALID` ⇔ `anchor.tree_sha == current.tree_sha`
* `OBSOLETE` ⇔ los tree SHA difieren
* Motivos: `tree_coincidente` / `tree_distinto` (ya no hay precedencia: con
  identidad de contenido hay una sola causa posible)
* `commit` y `working_tree_clean` son **metadatos**: no participan

## 3. `UNDETERMINED` descartado

Las tres condiciones de B15-A §7.1 son inalcanzables por la firma del
evaluador: un anchor sin `commit`/`tree_sha` no es construible, y un Git
inobservable falla en el adapter **antes** de evaluar. No se conserva.

El caso de dominio sigue existiendo —un trabajo recién creado tiene
`(no hay verificación registrada)`, sin anchor— pero pertenece a quien decide
*si llamar* al evaluador: si no hay verdict, no hay nada que evaluar.

## 4. Fronteras

* `ValidityResult` lleva solo `status` y `motivo`. No incluye `Verdict`,
  `CurrentGitState`, `WorkState` ni salidas de Git.
* Derivable, no mutante: no toca `state.md`, `veredictos_obsoletos` ni Git.
* No decide identidad entre verificaciones: ignora `resultado`, `comando`,
  `scope`, `autor` e `instante`.
* Sin productor artificial de `Verdict`.

## 5. Trazabilidad

```
B13-B evidencia        → un caso: árbol limpio → sucio
B15-A contrato inicial → anchor = (commit, working_tree_clean)
B15-B implementación   → GitAnchor(commit, working_tree_clean)
B15-C evaluador        → igualdad de dos componentes
B15-E corrección       → identidad = tree SHA; el resto, metadato
B15-F alineación       → GitAnchor/CurrentGitState + tree SHA del working tree
```

B15-A §5 y §7.1 **no se reescriben**: describen el contrato original, y su
alcance queda delimitado por B15-E §I.
