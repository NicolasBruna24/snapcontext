# B15-E — Verification Identity and Git Anchor Decision

> Decisión y discovery solamente. Sin cambios de código. Sin commit/push.
> Antecede a cualquier integración del productor real de `Verdict`.

## 1. Decision

**La identidad de un verdict es el contenido efectivo del árbol que se
verificó, no el par `(commit, working_tree_clean)`.**

> Un verdict es válido mientras el contenido efectivo del árbol que produjo la
> verificación siga siendo idéntico.

`commit` y `working_tree_clean` pasan a ser **metadatos de observación**, no
componentes de identidad.

**Contract impact:** B15-B y B15-C requieren alineación. No es integrable el
productor con los contratos actuales.

## 2. Por qué el contrato actual es insatisfacible

Comprobado empíricamente contra el evaluador de B15-C (`/tmp`, sin tocar el
proyecto):

| Escenario | Anchor | Actual | B15-C dice | Realidad |
|---|---|---|---|---|
| Caso 6 | `(X, dirty)` | `(X, dirty)` | `VALID` | **contenido cambió** (V=2→V=3) |
| Caso 5 | `(X, dirty)` | `(Y, clean)` | `OBSOLETE` | **contenido idéntico** (commit de V=2) |

Dos defectos opuestos y ambos graves:

* **Falso positivo** (caso 6): el código probado desapareció y el evaluador lo
  da por vigente. Es el fallo que importa: la evidencia de verificación ya no
  sostiene lo que dice.
* **Falso negativo** (caso 5): el contenido se conserva intacto y el evaluador
  lo descarta. Falla por formalidad administrativa (se commiteó), no por
  pérdida de evidencia.

La igualdad de `(commit, working_tree_clean)` **no es ni necesaria ni
suficiente** para la igualdad del contenido. Con contenido sucio, el estado
observado es constante mientras el contenido cambia libremente.

## 3. Por qué el contenido resuelve la matriz

Verificado: el *tree SHA* del working tree con cambios sin commitear coincide con
el `tree` del commit que luego captura ese mismo contenido, y diverge en cuanto el
contenido cambia.

```
contenido probado (worktree) tree: 56f83fd60ab3
tras commitear el MISMO contenido, tree(Y): 56f83fd60ab3   -> iguales
tras modificar más, tree actual:    6b1db92ee040           -> distintos
```

## 4. Consecuencias

* **B15-B**: `GitAnchor` y `CurrentGitState` necesitan un campo de contenido
  (tree SHA del contenido efectivo). `commit` y `working_tree_clean` se conservan
  como metadatos observables.
* **B15-C**: la igualdad pasa a ser de **un solo** componente. `UNDETERMINED`
  sigue descartado.
* **B13-B** no se invalida: su caso (árbol limpio → sucio) es un caso particular
  donde contenido y par coinciden. Lo que no sostiene es que el par sea la
  identidad general.

## 5. Lo que esta decisión NO resuelve

* El **mecanismo** para obtener el tree SHA del working tree sin tocar el índice
  real (índice temporal + `write-tree` es el candidato barato; `stash create`
  escribe objetos). Detalle de implementación de B15-F.
* `scope` e `instante`: metadatos, fuera de la identidad.
* Dónde y cómo nace el `Verdict` (problema de B15-D, sin resolver).
