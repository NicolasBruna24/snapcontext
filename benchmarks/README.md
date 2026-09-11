# Benchmark de edición de SnapContext

Suite reproducible de tareas para evaluar la fiabilidad del motor de edición
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
    └── ...
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

| Métrica | SnapContext |
|---------|-------------|
| Tareas | 10 (suite inicial) |
| Modo | light (motor de edición) |

Los resultados detallados se guardan en `benchmarks/results.json` tras cada
ejecución.

## Cómo añadir nuevas tareas

1. Crea un directorio `benchmarks/tasks/NNN_nombre/` (ej. `011_nueva_tarea/`).
2. Añade `input.py` (código con el problema) y `expected.py` (código esperado).
3. Añade `task.json` con los metadatos:

```json
{
  "id": "011",
  "nombre": "Descripción corta",
  "tipo": "bug_fix | refactorizacion | feature | complejo",
  "dificultad": "facil | medio | dificil",
  "descripcion": "Descripción del problema.",
  "instruccion": "Instrucción para el agente."
}
```

## Distribución de tareas

La suite inicial incluye 10 tareas distribuidas como:

- **5 bug fixes** (fáciles): errores simples de lógica.
- **3 refactorizaciones**: renombrar variables, extraer funciones, simplificar.
- **2 adiciones de features**: añadir parámetros, validaciones.

En futuras versiones se ampliará a 50 tareas con distribución similar a
SWE-bench (20 bug fixes, 15 refactorizaciones, 10 features, 5 complejos).
