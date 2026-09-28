# B15-A — Verification Verdict Validity Boundary

> Solo lectura salvo este documento. Sin cambios en código, tests, MCP, planificador,
> `work_context.py`, `state.md`, `decisions.md` ni `assertions.md`; sin commit/push.
> Bloque de especificación y decisión: no implementa el Verification Engine.

## 1. Objective

Determinar la frontera arquitectónica que responde:

> ¿Cómo debe SnapContext decidir si un verification verdict sigue siendo válido
> cuando existe un Git anchor asociado al estado que fue verificado?

Este documento **no implementa** esa frontera: la define, la separa de `WorkState`
y de Git, y deja explícito qué está demostrado por evidencia y qué requiere decisión.

## 2. Evidence Reviewed

| Fuente | Qué aporta |
|---|---|
| `tests/test_b12_vertical_slice.py::test_veredicto_anclado_queda_obsoleto_tras_cambio` (líneas 194-228) | **Experimento B13-B.** Demuestra la regla de validez sobre su Git anchor. |
| `work_context.py:303` (generador de `state.md`) | El campo `Último veredicto de verificación` nace con el placeholder `(no hay verificación registrada)`, **sin anchor**. |
| `work_context.py:569` (`WorkState.veredictos_obsoletos`) | El campo existe en el modelo documental, sin semántica implementada. |
| B14-G-R §11 | `veredictos_obsoletos` está **protegido**; su semántica pertenece al Verification Engine. |
| B14-B / B14-C | `ultimo_veredicto` es **texto opaco**: el Reader no lo descompone ni lo valida. |
| B14-I | Confirmado end-to-end: Work-State no ejecuta Git ni valida verdicts. |
| `snapcontext.py:9146` `_tool_git_status()` | **Superficie factual de Git ya existente**: devuelve `rama`, `cambios` (porcelain), `total_cambios`. |
| `snapcontext.py` `_es_repo_git()` | Predicado de existencia de repositorio ya disponible. |
| Búsqueda repo-wide: `obsolet`, `validity`, `anchor Git`, `stale`, `verdict` | **No existe ninguna implementación de validez u obsolescencia.** |

## 3. Existing Verification Semantics

### 3.1 Qué demostró B13-B — y qué no

El experimento B13-B (test B12, C6) es la **única evidencia disponible**.
Recupérese literalmente:

1. Se crea un repositorio con commit inicial. `head = git rev-parse HEAD` (SHA completo).
2. Se registra el verdict con el anchor embebido en el texto:
   `pasa — comando \`python3 -m pytest tests/ -q\`; scope: proyecto completo; anchor Git: {head} (clean); autor: Agente B; instante: {ts1}`
3. Se **modifica el working tree sin commitear** (`README.md`), y se comprueba que
   `git status --short` lo refleja.
4. Se marca el verdict como `OBSOLETO` con motivo explícito:
   `cambio Git posterior alteró el árbol respecto a su anchor`.

**EVIDENCE — condiciones demostradas:**

- El anchor es un **commit SHA completo** (`git rev-parse HEAD`).
- El verdict transporta además un **marcador de limpieza del working tree** (`(clean)`).
- La condición de invalidación que el experimento **sí** ejerció es un cambio del
  **working tree sin commit**. El propio comentario del test lo dice: *«el anchor ya no
  describe el árbol»*.
- Un cambio de `HEAD` por un commit nuevo **no fue ejercido** por el experimento.

**EVIDENCE — lo que el experimento NO demuestra (importante):**

- El test **no contiene ningún cálculo de obsolescencia**. Escribe a mano la línea
  `OBSOLETO` mediante `.replace()` y luego comprueba que la línea está presente
  (líneas 212-228). La semántica de validez está **demostrada por construcción del
  documento**, no por código evaluador.
- El formato de la línea obsoleta y el motivo (`cambio Git posterior alteró el árbol…`)

## 4. Verdict Model

**DECISION** — modelo mínimo. No se añaden campos por anticipación.

```
Verdict
  ├── resultado        # declarativo: "pasa" | "falla" | …
  ├── identidad        # qué se verificó: comando + scope
  ├── autor            # quién
  ├── instante         # cuándo
  └── anchor_git       # ← objeto (§5), NO una subcadena de texto
```

**EVIDENCE** — el verdict se transporta hoy como **una única cadena opaca** en
`state.md`. B14-B/B14-C lo prohíben expresamente desdoblar, y B14-G-R permite escribirlo
al agente solo como «registro de evidencia», sin que registrar equivalga a verificar ni
ratificar.

**PROPOSAL** — el modelo `Verdict` debería existir como **valor estructurado en el punto
donde se produce la verificación**, y la línea de `state.md` sería su **renderizado**. La
validity debería consumir el valor estructurado, nunca la cadena.

> **Consecuencia (abierta, ver §17.1):** hoy solo existe el renderizado. Parsear
> `anchor Git: …` de vuelta desde el texto sería un acoplamiento frágil que ningún
> contrato ratificado respalda. B15-A **no resuelve** esto.

## 5. Git Anchor Model

**EVIDENCE** — el anchor que B13-B demuestra es un par, no un escalar:

| Elemento | Tipo | Origen | Demostrado en B13-B |
|---|---|---|:--:|
| `commit` | SHA completo | `git rev-parse HEAD` | **Sí** |
| `working_tree_limpio` | booleano | `git status --porcelain` vacío | **Sí** (`(clean)` / `(dirty)`) |

**DECISION** — el anchor de un verdict es **`(commit, working_tree_limpio)`**. Ambos
componentes son necesarios: el commit identifica *qué* se verificó, y el estado del
working tree indica si el árbol materializado seguía correspondiendo a ese commit. En
B13-B, con working tree limpio y `HEAD` intacto, el anchor seguía correspondiendo; al
suciar el árbol dejó de corresponder.

**DECISION** — lo siguiente **no** es parte del anchor y no debe confundirse con él:

- `git_base`, `git_actual`, `git_rama`, `git_pr_issue` de `WorkState`. Son
  **referencias declarativas del trabajo** (B14-C: texto, sin validar). El canal lo dice
  explícitamente: *«Git sigue siendo la única fuente de verdad sobre Git»*
  (`work_context.py:368`). Registrarlos no produce anchor.
- La **rama**: el experimento **no** demostró semántica de validez por nombre de rama. Una
  rama puede avanzar sin que cambie el código verificado.

**EVIDENCE negativa** — el *tree SHA* no aparece en ningún experimento del repositorio.
No se usa, no se ratifica y no se propone aquí.

## 6. Current Git State

**DECISION** — la verdad factual del repositorio la obtiene un **adaptador de Git**, y
solo él. Ese estado **no puede obtenerse de `state.md`**.

```
CurrentGitState
  ├── commit_actual        # git rev-parse HEAD
  ├── working_tree_limpio  # git status --porcelain vacío
  ├── es_repo              # _es_repo_git()
  └── disponible           # si el adaptador pudo obtenerlo
```

**EVIDENCE** — la superficie factual ya existe y está aislada:
`snapcontext._tool_git_status()` (`snapcontext.py:9146`) devuelve `rama` + `cambios` +
`total_cambios`, y ya figura como herramienta de solo lectura. Reutilizar ese patrón evita
introducir un segundo acceso a Git.

**DECISION** — el adaptador es **observacional**: no hace `commit`, `add`, `reset`,
`checkout`, `stash` ni `clean`. Solo lee. Es coherente con B8/B11 (`.work/` visible pero
fuera del staging) y con el propio experimento: el cambio que invalidó fue deliberadamente
**no commiteado**.

## 7. Validity Semantics

**DECISION** — la relación evaluada es:

```
validity(verdict, current_git_state)
```

Es una **función pura de dos entradas**. No lee ficheros, no ejecuta Git, no escribe.

La abstracción `validity(v, g)` **es suficiente** para lo demostrado. No se amplía (p. ej.
«¿el anchor es ancestro del actual?») porque no hay evidencia que lo exija (§17.2).

### 7.1 Estados

Tres estados, cada uno con una condición **concreta y alcanzable**, no por completitud.

| Estado | Condición | Evidencia del estado | Evidencia de la condición |
|---|---|:--:|---|
| `VALID` | `anchor.commit == current.commit` **y** `anchor.working_tree_limpio == current.working_tree_limpio` | **B13-B** | **B13-B** |

## 8. Obsolescence Semantics

**DECISION** — la obsolescencia es un **estado derivado**, no un dato.

- Se **calcula** comparando anchor y estado factual (§7). No se lee de ningún sitio.
- `WorkState.veredictos_obsoletos` **no** se introduce automáticamente. Sigue siendo un
  campo documental protegido (B14-G-R §11).
- El texto `OBSOLETO desde {ts}: {motivo}` observado en B13-B es **convención del
  experimento**, no formato ratificado (§17.3).

**Sobre `veredictos_obsoletos`** — que esté protegido confirma que su escritura pertenece
a un componente especializado, no al writer genérico. B15-A **no decide** si ese
componente (a) registra una entrada por verdict obsoleto, (b) deja de existir y la
obsolescencia es solo consultable, o (c) se mantiene y solo lo rellena un agente
autorizado. Queda abierto (§17.4).

  son **convención del test**, no contrato ratificado.
- La condición *«HEAD avanzó a un commit nuevo»* **no tiene evidencia**: no se demuestra
  que invalide, ni que no invalide.

**DECISION** — la regla que B13-B establece y que este bloque preserva:

> Un verification verdict es válido únicamente mientras su Git anchor siga
> correspondiendo al estado del trabajo que fue verificado.
## 9. Proposed Boundary

**DECISION** — dos capas separadas, no una:

```
        ┌──────────────────────────────┐
        │  Git adapter (observacional) │  ejecuta Git, devuelve datos
        └───────────────┬──────────────┘
                        │  CurrentGitState
                        ▼
        ┌──────────────────────────────┐
        │  Validity evaluator (puro)    │  validity(verdict, git_state)
        └───────────────┬──────────────┘
                        │  ValidityResult
                        ▼
        ┌──────────────────────────────┐
        │  Consumidores (futuros)       │
        └──────────────────────────────┘
```

**DECISION — por qué separarlas.** El acceso a Git es el único punto con efectos sobre el
entorno (subprocess, sandbox, permisos). La semántica de validez es lógica pura sobre
datos. Mezclarlas obligaría a montar un repositorio real para probar «¿un verdict con el
mismo commit es válido?», e impediría distinguir un fallo de Git de un fallo de
semántica. La separación replica el patrón que el repositorio ya usa con
`_tool_git_status()`.

La operación del evaluador es **`pure evaluation`**: sin efectos secundarios, sin
escritura, sin Git.

## 10. Inputs and Outputs

**Inputs (evaluador):** exactamente dos, ambos provistos por el llamador.

| Input | Tipo | Origen | ¿Lo obtiene el evaluador? |
|---|---|---|---|
| `verdict` | `Verdict` (§4) | Componente de verificación | **No** |
| `current_git_state` | `CurrentGitState` (§6) | Git adapter | **No** |

El evaluador **no obtiene nada por sí mismo**: ni ficheros, ni `state.md`, ni Git, ni
entorno. Esa es la propiedad que lo hace testeable y determinista.

**Output:** un único `ValidityResult`, sin efectos.

| Campo | Contenido |
|---|---|
| `estado` | `VALID` \| `OBSOLETE` \| `UNDETERMINED` |
| `motivo` | razón estructural (`commit_distinto`, `working_tree_sucio`, `sin_anchor`, …) |
| `anchor` | el anchor evaluado (eco, trazabilidad) |
| `current_git_state` | el estado factual evaluado (eco) |

**DECISION** — el resultado **no** incluye el texto del verdict ni autorización alguna: es
una observación, no una orden. Nada en él habilita escrituras.

**Errores:** la evaluación no falla por datos. `UNDETERMINED` + `motivo` es el canal para
«no se puede saber». Un fallo real de Git ocurre **en el adaptador**, antes de llamar al
evaluador, y se expresa como `current_git_state.disponible == False`, no como excepción
del evaluador.

## 11. Responsibility Matrix

| Capacidad | ¿Verdict producer? | ¿Git adapter? | ¿Validity evaluator? | ¿WorkState? | ¿`work_state_update`? |
|---|:--:|:--:|:--:|:--:|:--:|
| Ejecutar la verificación | **Sí** | No | No | No | No |
| Ejecutar/leer Git | No | **Sí** | **No** | **No** | **No** |
| Persistir el verdict en `state.md` | No | No | No | No | **Sí** (texto literal) |

## 12. WorkState Boundary

Se preserva sin cambios. **DECISION**:

`WorkState` **puede**: almacenar el último verdict registrado (texto opaco); almacenar
referencias Git declarativas (`git_base`, `git_actual`, `git_rama`, `git_pr_issue`);
exponer ambos a consumidores.

`WorkState` **no puede**: determinar validez; ejecutar Git; declarar automáticamente un
verdict válido; marcar automáticamente un verdict obsoleto; parsear el anchor fuera del
texto; sustituir al Verification Engine.

**EVIDENCE** — esto ya es el comportamiento verificado en B14-C/B14-I; B15-A no lo altera.

**DECISION** — `WorkState` es **entrada opcional** del consumidor futuro, no entrada
obligatoria del evaluador. El evaluador no debe recibir `WorkState` (§10): si lo recibiera,
el llamador podría pasarle el anchor por la puerta de atrás y duplicar la
responsabilidad. Se pasa el `Verdict`, no el documento.

| `OBSOLETE` | Las igualdades se cumplen y luego cualquiera se rompe | **B13-B** (caso working tree) | **B13-B** para working tree; **no** para commit (§17.2) |
| `UNDETERMINED` | El verdict **no tiene anchor**, el anchor no es resoluble, o no hay repositorio | **B14-B**: el placeholder del canal se genera **sin anchor** | Generador `work_context.py:303` |

**Justificación de `UNDETERMINED`** — no se introduce «por completitud». Es alcanzable sin
construcción artificial: todo trabajo recién creado por `crear_trabajo()` tiene
`ultimo_veredicto = "(no hay verificación registrada)"`, sin anchor. Sin tercer estado ese
caso tendría que clasificarse falsamente como `VALID` (un verdict que nunca corrió sería
«válido») o como `OBSOLETE` (inventar una invalidez). Ambos son incorrectos.

**DECISION** — `UNDETERMINED` lleva un **motivo** por condición (`sin_anchor`,
`anchor_no_resoluble`, `sin_repositorio`). Es un campo de dato, no un estado nuevo: el
consumidor necesita distinguirlas.

## 13. Git Boundary

```
Git adapter  →  estado factual del repositorio  →  evaluación de validez  →  resultado
```

**DECISION — la separación es compatible con la evidencia de B13-B.** B13-B obtiene el
estado factual con `git status --short` en el test, fuera de cualquier lógica de verdict.
La frontera solo traslada esa lectura a un componente explícito.

**PROHIBIDO en esta frontera** (coherente con B14 y con B8/B11):

- `git status` automático dentro de `WorkState`;
- auto-refresh del anchor;
- auto-commit, auto-clean, auto-reset;
- cualquier modificación del repositorio.

La evaluación es **observacional**. `state.md` puede *registrar* una referencia Git, pero
**no la convierte en verdad factual**: esa verdad la tiene el adaptador, y solo él.

## 14. Agent/Consumer Boundary

**DECISION** — B15-A **no integra** nada con `planificador.py`, `react_agent.py`, MCP ni
CLI. El agente no recibe lógica nueva.

**PROPOSAL** — consumidores futuros del `ValidityResult`, por orden de cercanía a la
evidencia existente:

| Consumidor | Plausibilidad | Nota |
|---|---|---|
| **Reporte de verificación** | Alta | Presentar «este verdict ya no describe el árbol» es informativo y no muta nada. |
| **Continuación de agente** | Media | Un agente que continúa debería saber si se apoya en evidencia caducada. |
| **Sincronización de Work State** | Media-baja | Exige escribir `veredictos_obsoletos`, hoy protegido y sin semántica decidida (§17.4). |
| **Soporte a decisiones** | Baja | Requeriría un Decision Resolver, explícitamente fuera de alcance. |

Ninguno se implementa aquí. **EVIDENCE relevante**: B14-I demostró que el consumidor real
(planificador) ya alcanza el dispatcher MCP, por lo que la integración futura no exigiría
un agente nuevo.

## 15. Invariants

1. El evaluador es **determinista**: mismas dos entradas → mismo `ValidityResult`.
2. El evaluador **no lee nada**: no ficheros, no entorno, no Git.
3. El evaluador **no escribe nada** y no tiene efectos secundarios.
4. Un verdict **sin anchor resoluble nunca es `VALID`**.
5. Un verdict **nunca verificado nunca es `VALID`**.
6. El adaptador de Git **no modifica el repositorio**.
7. `state.md` **no es fuente de verdad de Git**, ni de validez.
8. El `ValidityResult` **no confiere autoridad**: no habilita ninguna escritura.
9. La obsolescencia es **derivada**: no se lee de ningún documento.
10. Ningún estado de validez se infiere de la **existencia** del texto del verdict.

## 16. Non-Goals

B15-A **no** introduce, y nada de esto debe implementarse en un bloque de frontera:

base de datos · event sourcing · event bus · autenticación · usuarios · permisos ·
lifecycle machine · Decision Resolver · agente nuevo · protocolo nuevo · capa de
persistencia nueva · modelo distribuido · firmas criptográficas · hash de todos los
ficheros · servicio externo · auto-refresh · auto-commit.

Si alguno resulta necesario, se documenta como requisito futuro (§18), no se construye.

## 17. Open Decisions

### 17.1 Origen del `Verdict` estructurado — **bloqueante**
Hoy solo existe el **renderizado textual** en `state.md`. El evaluador necesita un
`Verdict` con `anchor` tipado. Opciones: (a) producir el `Verdict` estructurado donde se
genera la verificación y renderizarlo a `state.md`; (b) parsear el texto persistido
(acoplamiento frágil, no respaldado por ningún contrato). **B15-A recomienda (a)** y no la
adopta. Bloquea la implementación.

### 17.2 `HEAD` avanzado sin cambio de código — **no bloqueante, pero material**
B13-B **no** ejercitó un commit nuevo. No hay evidencia de si un commit que no altera el
código verificado debe invalidar. La comparación por igualdad de SHA (propuesta en §7)
respondería `OBSOLETE` siempre. Una alternativa sería comparar *tree* SHA, que distinguiría
«cambió el código» de «cambió el historial». No hay evidencia que lo exija; se deja como
decisión pendiente y **no** se implementa la alternativa.

### 17.3 Formato de la entrada obsoleta — **no bloqueante**
La convención `OBSOLETO desde {ts}: {motivo}` proviene del experimento, no de un
contrato. Si `veredictos_obsoletos` se mantiene, necesita formato ratificado.

### 17.4 Destino de `veredictos_obsoletos` — **no bloqueante**
Registro histórico, campo abolido o relleno por agente autorizado (§8).

### 17.5 Granularidad del adaptador — **no bloqueante**
`_tool_git_status()` devuelve `rama` y cambios, pero **no** `commit_actual`. El adapter
necesitará `git rev-parse HEAD`. Puede reutilizarse la tool existente ampliándola o usar
un adapter propio; es decisión de implementación.

## 18. Recommendation

**Siguiente bloque: B15-B — `Verdict` estructurado y Git adapter**, en dos pasos
separados y verificables:

1. **Resolver §17.1** (origen del `Verdict` estructurado). Es lo único que bloquea: sin un
   `anchor` tipado no hay nada que evaluar, y parsear el texto de `state.md` convertiría
   la opacidad de B14-B en un acoplamiento frágil.
2. **Implementar el Git adapter observacional** (§6, §13) reutilizando el patrón de
   `_tool_git_status()`, incluida la ampliación de §17.5.

El **evaluador puro** (§9) debería ir en un bloque posterior (B15-C): con `Verdict` y
`CurrentGitState` ya disponibles, es lógica pura trivialmente testeable sin repositorio
ni subprocess.

**No recomendado** integrar antes con MCP, planificador o el agente: hacerlo validaría
una frontera cuya entrada (§17.1) aún no está decidida.

| Parsear el anchor | No | No | No | **No** | **No** |
| Comparar anchor vs. estado actual | No | No | **Sí** | **No** | **No** |
| Declarar `VALID`/`OBSOLETE` | No | No | **Sí** | **No** | **No** |
| Escribir `veredictos_obsoletos` | No | No | No | No | **No** (protegido) |
| Cambiar `state.md` por la validez | No | No | **No** | No | **No** |

La fila crítica es **«Comparar anchor vs. estado actual»**: solo la tiene el evaluador.
Ninguna otra la posee, y esa exclusividad *es* la frontera.

