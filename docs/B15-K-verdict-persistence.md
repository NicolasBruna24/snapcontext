# B15-K — Persistencia de la última verificación en `state.md`

Estado: **IMPLEMENTADO**. Sin `CONTRACT GAP`.

## 1. Qué se persiste

La **última verificación conocida** del trabajo: el `Verdict` producido por F4
y el `ValidityResult` que se calculó en ese momento. No es historial.

| Dato | Origen | Dónde queda |
| ---- | ------ | ----------- |
| `resultado` | `Verdict.resultado` (`pasa`/`falla`) | `Último veredicto de verificación` |
| `validity` + `motivo` | `ValidityResult.status` / `.motivo` | ídem |
| `tree_sha` | `Verdict.git_anchor.tree_sha` (**identidad**) | ídem |
| `commit`, `working_tree_clean` | metadata del anchor | ídem |
| `scope` | `Verdict.scope` | ídem |
| `comando` | `Verdict.comando` | `Comando de verificación` |

## 2. Dónde

`.work/<work_id>/state.md`, dentro de `## Estado operativo`, en las dos claves
que el canal ya declaraba para eso. No se crea ninguna sección nueva (el
conjunto de encabezados del documento es idéntico antes y después) ni ningún
archivo paralelo.

## 3. Representación elegida

```text
## Estado operativo
- Ciclo de vida: declarado
- Trabajo completado: - (nada registrado)
- Trabajo pendiente: - (nada registrado)
- Bloqueos: - (ninguno)
- Siguiente paso: (pendiente de declarar)
- Comando de verificación: python3 -c "open('gen.txt','w').write('x')" && python3 -c "print('ok')"
- Último veredicto de verificación: [verificacion:v1] resultado=pasa; validity=OBSOLETE; motivo=tree_distinto; tree_sha=239883aa8b87920392ee2e1f271dcb62f2003b2c; commit=97362b437d1b917fa0c0c60e55e49e8b3ffadec9; working_tree_clean=false; scope=no definido
```

Decisiones:

* **Reutiliza `ultimo_veredicto`** (clave contractual existente) en lugar de
  inventar una nueva. Su semántica («texto declarado por el documento») se
  conserva intacta; lo que cambia es que el texto tiene ahora una gramática
  versionada (`[verificacion:v1]`) y es legible por máquina sin heurísticas.
* Pares `clave=valor` separados por `; ` — el mismo separador de listas en línea
  que ya usa B14-E, porque `:` ya es el separador clave/valor del documento.
  Los `;` dentro de un valor se escapan.
* Sin JSON, sin YAML, sin pickle, sin archivo paralelo (§4).
* `WorkState` **no** cambia: se añade una vista derivada,
  `UltimaVerificacion`, con `leer_ultima_verificacion(estado)`. Es la extensión
  mínima: el Reader sigue siendo «qué dice el documento», sin interpretación
  nueva de `VALID`/`OBSOLETE`.

## 4. Verdict / ValidityResult

Siguen siendo **dos dimensiones separadas** en el documento:

```text
[verificacion:v1] resultado=falla; validity=VALID; ...
```

se lee sin ambigüedad como «la verificación falló y sigue describiendo el
árbol actual». `VALID` nunca significa «pasó» y `OBSOLETE` nunca significa
«falló».

## 5. Proceso A → persistencia → proceso B

F4 recibe un `work_id` (nuevo kwarg de `ReactAgent`, por defecto `None`: sin
contexto de trabajo no se escribe nada). Con `work_id`, tras conocer el
`ValidityResult`, llama a `work_context.registrar_verificacion()`.

Round-trip real, con `sys.executable` como proceso B independiente:

```text
F4:  {"verdict.resultado": "pasa", "verdict.tree_sha": "239883aa…", "validity": "OBSOLETE", "persistida": true}
B:   {"resultado": "pasa", "validity": "OBSOLETE", "tree_sha": "239883aa…", "comando": "python3 -c …"}
```

## 6. Casos

| Caso | `resultado` | `validity` persistido |
| ---- | ----------- | --------------------- |
| Éxito sin cambios | `pasa` | `VALID` (`tree_coincidente`) |
| Éxito que modifica el árbol | `pasa` | `OBSOLETE` (`tree_distinto`) |
| Fallo sin cambios | `falla` | `VALID` |
| Dirty previo | `pasa`/`falla` | según el árbol; `working_tree_clean` queda como metadata |
| **Git PRE falla** | — | **no se persiste nada** (no hay Verdict) |
| **Git CURRENT falla** | `pasa`/`falla` | `no evaluada` |

`VALIDIDAD_NO_EVALUADA = "no evaluada"` no es un tercer estado del evaluador:
es la **ausencia** de estado, escrita explícitamente para que el documento nunca
se lea como un `VALID` implícito. `es_valida` y `es_obsoleta` son `False` en ese
caso.

## 7. Atomicidad

`registrar_verificacion()` delega en `actualizar_estado()` y, por tanto, en
`escribir_documento_trabajo()`: temporal + `os.replace`, validación de cabecera
b9-1 y edición quirúrgica. No hay un segundo sistema de escritura de `state.md`.

## 8. Compatibilidad hacia atrás

Un `state.md` sin la gramática nueva se lee igual que antes: `leer_estado()` no
cambia, `ultimo_veredicto` sigue siendo texto, y `leer_ultima_verificacion()`
devuelve `None` (ausencia de información estructurada, no error). Los
veredictos de texto libre que escribe el agente desde B14 siguen intactos.

## 9. Autoridad (B14-G/H)

Solo se escriben `comando_verificacion` y `ultimo_veredicto`. **Nunca**
`veredictos_obsoletos` (protegido) ni ninguna clave del mandato.

## 10. Tests

`tests/test_b15k_verdict_persistence.py` — 23 tests, Git real y `state.md` real:
VALID, OBSOLETE, `falla`+`VALID`, dirty, relectura desde **proceso
independiente**, reemplazo (no acumulación), idempotencia, documento válido
tras varias actualizaciones, `veredictos_obsoletos` intacto, mandato intacto,
fallo PRE (no persiste), fallo CURRENT (no inventa validez), pureza del
evaluador, `state.md` antiguo legible, sin archivos paralelos, sin secciones
nuevas, F1/F2 y MCP intactos.

Se actualizaron 4 tests de B15-I/J cuyas premisas («F4 no persiste») cambian
deliberadamente en B15-K; en ningún caso se relajan: ahora comprueban que F4
**delega** la escritura en `work_context` y que no edita el documento por su
cuenta.

## 11. Límites

Fuera de B15-K: historial de veredictos, `veredictos_obsoletos`, transición
automática a OBSOLETE por cambios posteriores, lifecycle, decisiones,
assertions, MCP, F1/F2 y cualquier clave del mandato.

**B15-L queda pendiente: historial de obsolescencia.**
