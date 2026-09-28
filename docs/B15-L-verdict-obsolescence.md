# B15-L — Historial y transición explícita de obsolescencia

Estado: **IMPLEMENTADO**. Sin `CONTRACT GAP`.

## 1. Qué significa «obsoleto»

Una verificación queda obsoleta cuando el **contenido efectivo** del árbol deja
de coincidir con el `tree_sha` que la produjo:

```text
tree_sha_actual != tree_sha_del_verdict
```

`commit` y `working_tree_clean` son metadata y no participan. Por eso un revert
exacto, o un commit nuevo con el mismo contenido, **no** invalidan nada.

## 2. `tree_sha` como identidad

Es la única prueba de identidad. Un caso lo demuestra de forma directa: se
ensucia el índice (`git rm --cached`) sin tocar el contenido; `git status`
reporta cambios, el árbol efectivo es idéntico y el veredicto sigue `VALID`.

## 3. Último veredicto vs. historial

Son dos dimensiones y no se mezclan:

* `ultimo_veredicto` — **la última verificación realizada**. B15-L **no** la
  borra, no la sustituye por `"OBSOLETO"` y no le cambia la `validity`
  persistida: conserva `resultado`, `tree_sha`, comando, scope y metadata. Es un
  hecho, no un estado.
* `veredictos_obsoletos` — **el evento histórico**: qué verificación dejó de
  describir el árbol, contra qué árbol, cuándo se detectó y por qué motivo.

La validez vigente no se «persiste como campo»: la calcula el evaluador cuando
alguien pregunta. B15-L solo deja constancia del hecho irreversible.

## 4. Formato de `veredictos_obsoletos`

B15-A §17.3 dejó constancia de que el texto `OBSOLETO desde {ts}: {motivo}`
venía del experimento B13-B y **no** era un contrato ratificado. B15-L define
aquí la primera gramática versionada, y lo hace **reutilizando** la de la
verificación (`[verificacion:v1]` de B15-K) en vez de inventar una paralela:

```text
[obsoleto:v1] resultado=pasa; validity=OBSOLETE; motivo=tree_distinto;
tree_sha=<verificado>; tree_actual=<actual>; commit=<sha>;
working_tree_clean=true; scope=no definido; comando=<cmd>; detectado=<ts>
```

* mismos pares `clave=valor` separados por `; `, con escapes de `;`;
* `validity=OBSOLETE` explícito: la entrada describe un veredicto que ya no
  está vigente, y decirlo evita confundirla con una verificación viva;
* `motivo` reutiliza la constante del evaluador (`tree_distinto`), sin taxonomía
  paralela;
* una entrada por línea (el writer genérico unía con `; `, lo que sería
  ambigüo aquí porque la entrada ya contiene `; `).

**Timestamps.** `instante` (del Verdict, si se persistió) y `detectado` (momento
en que se observó la transición) son campos **distintos** y no se sustituyen.
`detectado` solo se escribe si el llamador lo pasa: no se inventa un reloj.

## 5. Transición e idempotencia

```text
VALID ──(cambia el árbol)──▶ OBSOLETE ──▶ una entrada en el historial
```

`detectar_obsolescencia()` es **explícita**: sin daemon, sin polling, sin
watcher y sin detección implícita al leer un `WorkState`. La secuencia es la
del enunciado: leer `ultimo_veredicto` → observar el árbol → reconstruir el
`Verdict` histórico → evaluar con el evaluador **puro** → registrar si procede.

Es idempotente porque la identidad de una entrada es el `tree_sha` **del
veredicto obsoleto**, no el instante de detección: repetir la detección con el
mismo árbol no añade nada.

## 6. Casos verificados

| Caso | Resultado |
| ---- | --------- |
| Árbol idéntico | `VALID`, sin historial |
| Árbol distinto | `OBSOLETE`, una entrada |
| Repetir la detección | `ya_registrado=True`, historial intacto |
| **Revert exacto** | `VALID`, sin entrada nueva |
| **Commit con el mismo contenido** | `VALID` |
| Índice sucio con el mismo árbol | `VALID` |
| Índice sucio con árbol distinto | `OBSOLETE` |

## 7. Errores

* **Sin verificación persistida** (trabajo recién creado, o veredicto de texto
  libre de B14): no-op con estado `sin_verificacion`. No es un error.
* **Gramática corrupta** (`[verificacion:v1]` sin cerrar, o sin
  `resultado`/`tree_sha`): `ContratoEstadoInvalidoError`. Se declara la
  corrupción en vez de inventar un `Verdict`.
* **Git no observable**: se propaga `EstadoGitIndisponibleError` y el documento
  no se toca. Sin observación no hay afirmación.

## 8. Atomicidad y autoridad

`registrar_veredicto_obsoleto()` escribe con `escribir_documento_trabajo()`
(temporal + `os.replace`), el mismo mecanismo que `actualizar_estado()`: no hay
un segundo escritor ni escrituras directas con `open()`.

Es la **única** vía productiva para modificar el historial. `veredictos_obsoletos`
se ha **retirado** de `_CAMPOS_ESCRIBIBLES`, de modo que la escritura genérica
lo rechaza por nombre; y `_exigir_autoridad()` comprueba los campos protegidos
antes que el nombre no contractual, para que la operación siga clasificándose
como `autoridad` (la política de B14-G/H no cambia, se refuerza).

## 9. Compatibilidad

`veredictos_obsoletos` pasa a leerse como lista en el Reader (el modelo ya lo
declaraba como tupla). Un documento antiguo con el valor en la misma línea se
sigue leyendo igual, como un único item.

## 10. Límites

Fuera de B15-L: exponer la operación por MCP, integrarla en F1/F2, dispararla
automáticamente, y cualquier semántica nueva de `ValidityResult`. La operación
existe como capacidad interna, probada y reutilizable.
