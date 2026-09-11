#!/usr/bin/env python3
"""Tests para el benchmark de edición (Fase 5).

Verifica que el runner funciona correctamente con las tareas definidas.
Es un test opcional (no bloqueante) que puede ejecutarse manualmente.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
RUNNER = RAIZ / "benchmarks" / "runner.py"
DIR_TAREAS = RAIZ / "benchmarks" / "tasks"


# --- Helpers ----------------------------------------------------------------


def _tareas_existen() -> bool:
    """Verifica que hay tareas definidas en benchmarks/tasks/."""
    if not DIR_TAREAS.is_dir():
        return False
    return any(DIR_TAREAS.iterdir())


# --- Tests ------------------------------------------------------------------


@pytest.mark.skipif(not _tareas_existen(), reason="No hay tareas definidas")
def test_runner_compila():
    """El runner compila sin errores."""
    import py_compile

    py_compile.compile(str(RUNNER), doraise=True)


@pytest.mark.skipif(not _tareas_existen(), reason="No hay tareas definidas")
def test_runner_ejecuta_tarea_individual():
    """El runner puede ejecutar una sola tarea sin errores."""
    resultado = subprocess.run(
        [sys.executable, str(RUNNER), "--task=001"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(RAIZ),
    )
    assert resultado.returncode == 0, f"STDERR: {resultado.stderr}"
    assert "001" in resultado.stdout


@pytest.mark.skipif(not _tareas_existen(), reason="No hay tareas definidas")
def test_runner_genera_results_json():
    """El runner genera results.json tras ejecutarse."""
    subprocess.run(
        [sys.executable, str(RUNNER), "--task=001"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(RAIZ),
    )
    archivo_resultados = RAIZ / "benchmarks" / "results.json"
    assert archivo_resultados.is_file()
    datos = json.loads(archivo_resultados.read_text(encoding="utf-8"))
    assert "exitos" in datos
    assert "total" in datos
    assert "resultados" in datos
    assert datos["total"] >= 1


@pytest.mark.skipif(not _tareas_existen(), reason="No hay tareas definidas")
def test_tareas_tienen_archivos_requeridos():
    """Todas las tareas tienen input.py, expected.py y task.json."""
    for tarea_dir in sorted(DIR_TAREAS.iterdir()):
        if not tarea_dir.is_dir():
            continue
        assert (tarea_dir / "input.py").is_file(), f"Falta input.py en {tarea_dir.name}"
        assert (tarea_dir / "expected.py").is_file(), f"Falta expected.py en {tarea_dir.name}"
        assert (tarea_dir / "task.json").is_file(), f"Falta task.json en {tarea_dir.name}"


@pytest.mark.skipif(not _tareas_existen(), reason="No hay tareas definidas")
def test_task_json_campos_requeridos():
    """Los task.json tienen los campos requeridos."""
    campos_requeridos = {"id", "nombre", "tipo", "dificultad", "descripcion", "instruccion"}
    for tarea_dir in sorted(DIR_TAREAS.iterdir()):
        if not tarea_dir.is_dir():
            continue
        task_json = tarea_dir / "task.json"
        meta = json.loads(task_json.read_text(encoding="utf-8"))
        faltantes = campos_requeridos - set(meta.keys())
        assert not faltantes, f"Task {tarea_dir.name} sin campos: {faltantes}"


@pytest.mark.skipif(not _tareas_existen(), reason="No hay tareas definidas")
def test_input_y_expected_parsean():
    """Los input.py y expected.py son código Python válido."""
    for tarea_dir in sorted(DIR_TAREAS.iterdir()):
        if not tarea_dir.is_dir():
            continue
        for nombre in ("input.py", "expected.py"):
            archivo = tarea_dir / nombre
            try:
                compile(archivo.read_text(encoding="utf-8"), str(archivo), "exec")
            except SyntaxError as exc:
                pytest.fail(f"{tarea_dir.name}/{nombre} tiene error de sintaxis: {exc}")
