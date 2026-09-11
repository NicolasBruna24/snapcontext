# Benchmark de edición de SnapContext

Suite reproducible de **50 tareas** para evaluar la fiabilidad del motor de edición
de SnapContext de forma objetiva y comparable.

## Objetivo

Medir el **porcentaje de éxito** del editor de SnapContext al realizar tareas
típicas de edición de código: corrección de bugs, refactorizaciones, adición de
features y cambios complejos.

Los resultados permiten comparar con otros agentes (Aider, Claude Code, Cline)
en condiciones reproducibles.

## Estructura

```
benchmarks/
├── README.md           # Este archivo
├── runner.py           # Ejecutor del benchmark
├── results.json        # Resultados de la última ejecución
└── tasks/
    ├── 001_corregir_bug_suma/
    │   ├── input.py      # Código con el problema
    │   ├── expected.py   # Código corregido esperado
    │   └── task.json     # Metadatos (descripción, tipo, dificultad)
    └── ... (50 tareas)
```

## Ejecución

```bash
# Modo light (sin LLM, usa el motor de edición directamente)
python benchmarks/runner.py --modo=light

# Modo deep (con Ollama local si está disponible)
python benchmarks/runner.py --modo=deep

# Ejecutar una sola tarea
python benchmarks/runner.py --task=001
```

## Resultados

### Modo light (motor de edición determinista)

| Métrica | Resultado |
|---------|-----------|
| Tareas completadas | **50/50 (100%)** |
| Tiempo total | ~0.5s |
| Tiempo medio | ~0.01s |
| Tipo de tareas | 20 bug fixes, 15 refactorizaciones, 10 features, 5 complejos |

El modo light mide el **motor de edición** de SnapContext (búsqueda y reemplazo,
parches unificados, fallback difuso). No usa LLM: verifica que el editor aplica
correctamente cambios deterministas.

### Modo deep (agente con LLM local)

| Métrica | Resultado |
|---------|-----------|
| Estado | ⏳ **Pendiente** (requiere descargar modelo Ollama) |
| Modelo recomendado | `qwen2.5:0.5b` o `llama3.2:1b` |

Para ejecutar el modo deep:

```bash
ollama pull qwen2.5:0.5b
python benchmarks/runner.py --modo=deep
```

## Comparación con otros benchmarks

> **Nota importante:** Los números de Aider y Claude Code provienen de
> [SWE-bench](https://www.swebench.com/) (issues reales de GitHub, ~300 tareas),
> mientras que los nuestros son **tareas sinteticas reproducibles** (50 tareas).
> No son directamente comparativos, pero dan una idea del orden de magnitud.

| Benchmark | Tareas | % Éxito | Modelo |
|-----------|--------|---------|--------|
| **SnapContext (light)** | 50 | **100%** | Determinista (motor de edición) |
| **SnapContext (deep)** | 50 | ⏳ Pendiente | Qwen2.5-0.5B (previsto) |
| Aider (SWE-bench Lite) | 300 | ~74% | GPT-4o |
| Claude Code (SWE-bench) | 500 | ~77% | Claude Opus |

**Diferencias metodológicas:**
- **SWE-bench**: issues reales de GitHub, verificación mediante tests automáticos.
- **Nuestro benchmark**: tareas sintéticas, verificación mediante AST diff.
- El modo light mide el editor, no el agente. El modo deep (pendiente) medirá
  el agente completo con LLM local.

## Distribución de las 50 tareas

| Tipo | Cantidad | Dificultad |
|------|----------|------------|
| Bug fixes | 20 | 15 fácil, 5 medio |
| Refactorizaciones | 15 | 15 medio |
| Features | 10 | 10 medio |
| Complejos | 5 | 5 difícil |

## Cómo añadir nuevas tareas

1. Crea un directorio `benchmarks/tasks/NNN_nombre/` (ej. `051_nueva_tarea/`).
2. Añade `input.py` (código con el problema) y `expected.py` (código esperado).
3. Añade `task.json` con los metadatos:

```json
{
  "id": "051",
  "nombre": "Descripción corta",
  "tipo": "bug_fix | refactorizacion | feature | complejo",
  "dificultad": "facil | medio | dificil",
  "descripcion": "Descripción del problema.",
  "instruccion": "Instrucción para el agente."
}
```

## Reproducir

```bash
# Clonar el repo
git clone https://github.com/NicolasBruna24/snapcontext
cd snapcontext

# Instalar dependencias
pip install -e ".[all]"

# Ejecutar benchmark (modo light, sin API key)
python benchmarks/runner.py --modo=light
```
