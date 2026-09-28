# B15-G — Canonical Verification Producer Decision

> Solo lectura y decisión. Sin cambios de código, tests ni `pyproject.toml`.
> Sin commit/push. El único artefacto es este documento.

## A. Objective

Determinar **dónde y cuándo nace** un `Verdict` de verificación. B15-F ya
resolvió *qué* lo identifica (el `tree_sha` del contenido efectivo). Aquí se
resuelve *quién lo produce*, sin inventar el productor antes de conocer los
flujos reales.

Criterio (§2): una verificación canónica es una operación **clasificada como
verificación** cuyo resultado puede asociarse determinísticamente al `tree_sha`
del contenido comprobado.

## B. Evidence From B15-D

B15-D localizó F1–F5 y concluyó que ninguno produce un resultado estructurado
común. Este bloque **corrige y afina** dos puntos de B15-D con evidencia nueva:

1. **F3 no tiene ningún llamador de producción.** `ejecutar_bucle_test()`
   (`snapcontext.py:3008`) solo se invoca desde `tests/test_sandbox_430.py:248`.
   Es una superficie pública muerta.
2. **F4 sí está conectada al flujo productivo, y es la ruta por defecto.**
   `snapcontext.py:6589-6591`: *"ReAct es el modo por defecto"*. B14-I afirmó que
   el planificador era el flujo agéntico productivo y `react_agent` quedaba
   desconectado; eso era correcto **solo respecto al dispatcher MCP**, no
   respecto al agente. Verificado: `_ejecutar_react` instancia `ReactAgent`
   (`snapcontext.py:6315`) y es el modo por defecto de cualquier consulta sin
   `--plan`.

## C. F1 — AgentTester

`agentes.py:1349-1370`. `ejecutar_pruebas(comando: list[str], directorio) ->
CompletedProcess`.

* **Semántica**: ejecutar el *comando de pruebas*. No verifica el proyecto: no
  decide nada, solo ejecuta y devuelve el proceso.
* **Único llamador de producción**: `orquestador.py:134` (F2). Verificado por
  búsqueda: no hay más referencias fuera de tests.
* **Abstracción reutilizable**: no. Con un solo llamador no es un punto
  compartido.
* **Consecuencia**: hacerlo canónico obligaría a `agentes.py` a importar el
  dominio de `Verdict` y a **no cubrir F4**, que no lo usa. F4 llama a
  `sc._ejecutar_comando` directamente.

## D. F2 — Orquestador

`orquestador.py:87-92`. Bucle editor → tester.

* **Semántica**: *coordinador*. Su `True` significa «las pruebas acabaron
  pasando en alguna iteración», no «este contenido fue verificado una vez».
* **Granularidad incompatible**: un `Verdict` afirma sobre un `tree_sha`
  concreto; el bucle muta el árbol entre iteraciones (el editor escribe antes
  de cada prueba). El ancla correcta cambiaría en cada iteración.
* **Llama a**: F1. También invocable desde `planificador.py:527` (`test_loop`).

## E. F3 — snapcontext

`snapcontext.py:3008-3061`. Ejecuta `subprocess.run(comando_test)` **por su
cuenta**, sin pasar por F1.

* **Semántica**: coordinador, como F2. Variante sin agentes.
* **Llizadores de producción**: **cero**. Solo un test.
* **Consecuencia**: instrumentalizarla crearía un productor que nadie ejecuta, y
  una segunda ruta de verificación — justo lo que B15-D quería evitar.

## F. F4 — react_agent

`react_agent.py:652-700`. Devuelve `{ok, codigo, comando, stdout, stderr}`.

* **Semántica**: **adaptador de herramienta**. El `dict` existe para que el LLM
  lo lea; no afirma nada sobre el proyecto.
* **Conectividad**: `snapcontext.py:6315` + modo por defecto (`:6589`). **Productivo.**
* **Consecuencia**: colocar aquí la construcción del `Verdict` invertiría la
  dependencia — el dominio (`work_verdict`) importados por una capa de
  presentación que expone un `dict` al LLM. Además, seguiría sin cubrir F1/F2/F3.

## G. F5 — planificador

`planificador.py:373-381`. `accion="ejecutar"` con un **comando arbitrario**
construido por el LLM.

* **Semántica**: **ejecución de comando**, no verificación. El LLM puede
  proponer cualquier cosa.
* **El commit posterior no es identidad**: `_commit_paso` (`snapcontext.py:5925`)
  graba el hash en la tabla `pasos`. B15-E §6 ya lo delimito: eso es un *registro de
  cambios*, no el anchor del contenido verificado. Confirma la distinción.

## H. Comparative Matrix

| Flujo | Semántica | Resultado | Consumidor | Productor real | Captura tree_sha | Candidato canónico |
|---|---|---|---|---|---|---|
| F1 | ejecutar comando de pruebas | `CompletedProcess` | F2 | no: ejecutor de un solo llamador | sí (`directorio` disponible) | **no** — no cubre F4 |
| F2 | coordinar bucle editor↔test | `bool` | `ejecutar_flujo`, planificador | no: coordinador, granularidad incompatible | sí | **no** — la granularidad no encaja |
| F3 | coordinar bucle sin agentes | `bool` | *(ninguno en producción)* | no: superficie muerta | sí | **no** — nadie lo ejecuta |
| F4 | adaptar la suite a un `dict` para el LLM | `dict` | bucle ReAct (modo por defecto) | no: adaptador de presentación | sí (`self.directorio`) | **no** — acopla dominio a presentación |
| F5 | ejecutar comando arbitrario | `tuple[int, str]` | contexto del plan | no: no es verificación | sí | **no** — excluido por semántica |

## I. Verification Semantics

**CastleArq sí distingue «ejecutar» de «verificar»**, y la distinción es
explícita en el código:

* Verificación: `--comando-test`, `COMANDO_TEST_DEFECTO` y
  `detector_tests.detectar_automaticamente()["comando"]` (`detector_tests.py:201`),
  resueltos por `snapcontext._resolver_comando_test()` (`:2982-2996`), cuyo
  docstring dice *«Resuelve el comando de pruebas del bucle»*.
* Ejecución arbitraria: F5 y la herramienta MCP `execute_command`.

**Consecuencia (Pregunta B):** un `Verdict` representa **una operación
explícitamente clasificada como verificación**, no cualquier comando. F5 queda
excluido. (Pregunta C) `pytest`, `ruff` o `mypy` no tienen trato diferenciado hoy:
CastleArq tiene un único eje semántico —*es el comando de pruebas*— y no
clasifica herramientas dentro de él. Ampliarlo sería diseño nuevo, no evidencia.

## J. Tree SHA Capture Boundary

**Experimentos ejecutados** (repositorios temporales, nunca el proyecto):

| Escenario | tree antes | tree después | ¿Cambia? |
|---|---|---|---|
| `pytest -q` con `.gitignore` cubriendo `__pycache__/`, `*.pyc`, `.pytest_cache/` | `e8f16b40a49e` | `e8f16b40a49e` | **No** |
| `pytest -q` **sin** `.gitignore` | `1c69d59479` | `e226de3ed1` | **Sí** (`__pycache__` entra al árbol) |

**Decisión: capturar el `tree_sha` ANTES de ejecutar la verificación.**

* *Antes* afirma exactamente lo que se va a comprobar. Es la única captura
  cuya afirmación es cierta.
* *Después* afirmaría como «verificado» un contenido que incluye los artefactos
  que la propia verificación generó: sería una afirmación falsa.
* El caso sin `.gitignore` no invalida la decisión: el `Verdict` sigue siendo
  cierto («verifiqué el árbol T»), y la contaminación posterior lo vuelve
  `OBSOLETE`. Es un **falso positivo conservador**: falla hacia no sobre-afirmar.
  Con `.gitignore` —la práctica normal— no ocurre.

**Consecuencia operativa**: como el productor se invoca *después* de la
ejecución, **no puede capturar el árbol por sí mismo**. Quiencapture es **el
llamador**, que es quien conoce el orden. El productor recibe el
`CurrentGitState` ya observado.

## K. Canonical Producer Decision

**Ninguno de F1–F5 es canónico. El productor debe ser nuevo, en una frontera
nueva entre ejecución y presentación.** Demostración:

1. **Hay dos rutas de ejecución de pruebas independientes y sin abstracción
   compartida**: F1 (vía `orquestador`) y F4 (vía `sc._ejecutar_comando`). F3 es
   una tercera, muerta. Instrumentar cualquiera de ellas deja fuera a las otras
   y crea la duplicacion que B15-D queria evitar.
2. **F2 y F3 son coordinadores, no productores**: su unidad de resultado es el
   bucle, incompatible con un `Verdict` sobre un árbol concreto.
3. **F4 es un adaptador de presentación**: el dominio no debe depender de él.
4. **No existe hoy ninguna función que reciba «resultado de una verificación +
   contexto» y devuelva algo estructurado**: eso es exactamente la abstracción
   que falta, y crearla es la conclusión, no una invención.

**El productor canónico es una función nueva en `work_verdict.py`**, que recibe
un resultado de verificación **ya ejecutado** más el `CurrentGitState` que el
llamador capturó antes, y devuelve un `Verdict`. No ejecuta, no decide qué es
una verificación, no toca Git, no persiste.

## L. Non-Canonical Flows

| Flujo | Razón de exclusión |
|---|---|
| F1 | Ejecutor con un solo llamador; no cubre F4 |
| F2 | Coordinador; granularidad de bucle incompatible |
| F3 | Sin llamadores de producción |
| F4 | Adaptador de presentación; acoplaría dominio a UI/LLM |
| F5 | Ejecuta comandos arbitrarios: no es verificación |

## M. Proposed Producer Contract

Firma conceptual (**no implementada**):

```python
def producir_verdict(
    exito: bool,                 # obligatorio: el resultado de la verificación
    comando: str | Sequence[str],# obligatorio
    estado: CurrentGitState,      # obligatorio: capturado por el llamador ANTES
    *,
    scope: str | None = None,    # opcional
    autor: str | None = None,    # opcional
    instante: str | None = None, # opcional
) -> Verdict:
    ...
```

| Campo | Obligatorio | Quién lo decide |
|---|---|---|
| `exito` | sí | el llamador, desde el resultado de la verificación |
| `comando` | sí | el llamador, desde el comando ejecutado |
| `estado` | sí | el llamador, con `leer_estado_git()` **antes** de ejecutar |
| `scope` | no | el llamador (hoy no existe en ningún flujo: B15-E §J) |
| `autor` | no | el llamador |
| `instante` | no | el llamador; metadato auditivo |
| `resultado` | derivado | el productor: `"pasa"` / `"falla"` desde `exito` |
| `git_anchor` | derivado | el productor: `GitAnchor(estado.tree_sha, estado.commit, estado.working_tree_clean)` |

## N. Responsibilities and Non-Responsibilities

**El productor SÍ**: construir `Verdict` y `GitAnchor`; derivar `resultado`;
normalizar `comando`; devolver el objeto.

**El productor NO**: ejecutar nada; decidir qué es una verificación; capturar
`tree_sha`; **evaluar validez** (sigue en `evaluar_validez_verdict()`, B15-C/B15-F);
persistir; escribir `state.md`; tocar `decisions.md`/`assertions.md`; importar
MCP, agentes o WorkState; emitir eventos.

## O. B15-B → B15-F Traceability

```
B13-B  evidencia       → un caso (árbol limpio → sucio)
B15-A  contrato        → anchor = (commit, working_tree_clean)
B15-B  modelos         → GitAnchor / CurrentGitState / leer_estado_git
B15-C  evaluador       → igualdad de dos componentes; UNDETERMINED descartado
B15-E  corrección      → identidad = tree SHA; commit y clean, metadato
B15-F  alineación      → tree SHA del working tree vía índice temporal
B15-G  este bloque     → ningún productor canónico existe; el nuevo productor
                         recibe el estado capturado por el llamador
```

## P. B15-H Implementation Boundary

B15-H debe limitarse a:

* crear `producir_verdict()` en `work_verdict.py` con la firma de §M;
* probarlo con entradas construidas directamente;
* **no** tocar F1–F5, ni `WorkState`, ni MCP, ni persistencia;
* **no** implementar evaluación (ya existe), ni obsolescencia, ni lifecycle.

Integrar un llamador real queda fuera: hacerlo exigiría decidir **qué** flujo
adopta el productor, y esa decisión no se ha tomado (§Q).

## Q. Git Integrity

`HEAD` antes y después: `45e2e0a71d0689a979da1f492914f8ab8faf1552` (sin cambios).
`git status --short` idéntico. Sin `reset`/`restore`/`checkout`/`clean`/`stash`/
`add`/`commit`/`push`. Los experimentos Git usaron repositorios temporales en
`/tmp`; el working tree del proyecto nunca se vio afectado.
