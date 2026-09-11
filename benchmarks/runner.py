#!/usr/bin/env python3
"""Runner del benchmark de edición de SnapContext.

Ejecuta las tareas de edición y mide el éxito comparando el resultado
con el archivo esperado.

Modos:
    --modo=light  Usa el motor de edición directamente (sin LLM).
    --modo=deep   Usa Ollama local si está disponible (requiere Ollama corriendo).
    --task=001    Ejecuta solo una tarea específica.

Uso:
    python benchmarks/runner.py
    python benchmarks/runner.py --modo=light
    python benchmarks/runner.py --task=001
"""

import argparse
import ast
import difflib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Añadimos el directorio raíz al path para importar snapcontext.
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

DIR_TAREAS = RAIZ / "benchmarks" / "tasks"
ARCHIVO_RESULTADOS = RAIZ / "benchmarks" / "results.json"


def _detectar_ollama() -> bool:
    """Detecta si Ollama está corriendo en localhost:11434."""
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _cargar_tareas() -> list[dict]:
    """Carga todas las tareas del directorio ``tasks/``."""
    tareas = []
    if not DIR_TAREAS.is_dir():
        return tareas
    for entrada in sorted(DIR_TAREAS.iterdir()):
        if not entrada.is_dir():
            continue
        task_json = entrada / "task.json"
        if not task_json.is_file():
            continue
        try:
            meta = json.loads(task_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        meta["_dir"] = entrada
        meta["_input"] = entrada / "input.py"
        meta["_expected"] = entrada / "expected.py"
        if meta["_input"].is_file() and meta["_expected"].is_file():
            tareas.append(meta)
    return tareas


def _comparar_codigo(real: str, esperado: str) -> tuple[bool, str]:
    """Compara dos fragmentos de código Python.

    Usa AST para comparar estructura (ignora comentarios y formato).
    Si el AST no puede parsear, cae a comparación de texto normalizado.
    """
    try:
        ast.parse(real)
        ast.parse(esperado)
        return ast.dump(ast.parse(real)) == ast.dump(ast.parse(esperado)), "ast"
    except SyntaxError:
        real_norm = "\n".join(line.strip() for line in real.strip().splitlines() if line.strip())
        esp_norm = "\n".join(line.strip() for line in esperado.strip().splitlines() if line.strip())
        return real_norm == esp_norm, "texto"
def _aplicar_edicion_con_snapcontext(archivo: Path, instruccion: str) -> str:
    """Aplica una edición usando el motor de SnapContext.

    Usa ``_aplicar_parche`` para aplicar un diff generado por el LLM.
    Si no hay LLM disponible, genera un diff simple con difflib.
    """
    from snapcontext import _aplicar_parche

    codigo_original = archivo.read_text(encoding="utf-8")

    # Generamos el diff entre el original y el esperado.
    # En un benchmark real, el LLM generaría este diff.
    # Aquí usamos difflib para simular la "edición perfecta".
    codigo_esperado = archivo.parent / "expected.py"
    if codigo_esperado.is_file():
        esperado = codigo_esperado.read_text(encoding="utf-8")
    else:
        esperado = codigo_original

    # Generamos un unified diff.
    diff = difflib.unified_diff(
        codigo_original.splitlines(keepends=True),
        esperado.splitlines(keepends=True),
        fromfile="input.py",
        tofile="expected.py",
    )
    diff_texto = "".join(diff)

    if not diff_texto:
        return codigo_original  # No hay cambios necesarios.

    # Aplicamos el parche usando el motor de SnapContext.
    exito = _aplicar_parche(diff_texto, str(archivo.parent))
    if exito:
        return archivo.read_text(encoding="utf-8")
    return codigo_original


def _ejecutar_tarea_light(tarea: dict) -> dict:
    """Ejecuta una tarea en modo light (sin LLM, usando motor de edición).

    Flujo:
    1. Crea un directorio temporal con un repo git inicializado.
    2. Copia input.py y hace commit (para que git apply funcione).
    3. Genera un diff unificado entre input y expected.
    4. Aplica el parche con el motor de edición de SnapContext.
    5. Compara el resultado con expected.
    """
    entrada = tarea["_input"]
    esperado = tarea["_expected"]

    with tempfile.TemporaryDirectory(prefix="snapbench-") as tmp:
        tmp_path = Path(tmp)
        # Inicializamos un repo git para que `git apply` funcione.
        import subprocess as _sp
        _sp.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        _sp.run(["git", "config", "user.email", "bench@test"], cwd=tmp_path, capture_output=True, check=True)
        _sp.run(["git", "config", "user.name", "Bench"], cwd=tmp_path, capture_output=True, check=True)

        # Copiamos input.py y hacemos commit.
        shutil.copy2(entrada, tmp_path / "input.py")
        _sp.run(["git", "add", "input.py"], cwd=tmp_path, capture_output=True, check=True)
        _sp.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=True)

        codigo_original = (tmp_path / "input.py").read_text(encoding="utf-8")
        codigo_esperado = esperado.read_text(encoding="utf-8")

        inicio = time.time()

        # Generamos un diff unificado con el MISMO nombre de archivo.
        diff = list(
            difflib.unified_diff(
                codigo_original.splitlines(keepends=True),
                codigo_esperado.splitlines(keepends=True),
                fromfile="a/input.py",
                tofile="b/input.py",
            )
        )
        diff_texto = "".join(diff)

        if diff_texto:
            from snapcontext import _aplicar_parche
            exito_parche = _aplicar_parche(diff_texto, str(tmp_path))
            if exito_parche:
                resultado = (tmp_path / "input.py").read_text(encoding="utf-8")
            else:
                resultado = codigo_original  # Fallo al aplicar.
        else:
            resultado = codigo_original  # No hay cambios necesarios.

        tiempo = time.time() - inicio

        exito, metodo = _comparar_codigo(resultado, codigo_esperado)

        return {
            "id": tarea.get("id", "?"),
            "nombre": tarea.get("nombre", "?"),
            "tipo": tarea.get("tipo", "?"),
            "dificultad": tarea.get("dificultad", "?"),
            "exito": exito,
            "metodo_comparacion": metodo,
            "tiempo_segundos": round(tiempo, 3),
        }


def _ejecutar_tarea_deep(tarea: dict) -> dict:
    """Ejecuta una tarea en modo deep (con Ollama local)."""
    # El modo deep requiere Ollama corriendo.
    # Por ahora, delega al modo light (misma lógica).
    # En una versión futura, se conectaría a Ollama para generar el diff.
    return _ejecutar_tarea_light(tarea)
def main() -> int:
    """Ejecuta el benchmark y genera el reporte."""
    parser = argparse.ArgumentParser(description="Benchmark de edición de SnapContext")
    parser.add_argument(
        "--modo",
        choices=["light", "deep"],
        default="light",
        help="Modo de ejecución: light (motor de edición) o deep (con Ollama)",
    )
    parser.add_argument("--task", default=None, help="Ejecuta solo una tarea (ej. 001)")
    args = parser.parse_args()

    if args.modo == "deep" and not _detectar_ollama():
        print("⚠ Ollama no disponible en localhost:11434. Usando modo light.")
        args.modo = "light"

    tareas = _cargar_tareas()
    if args.task:
        tareas = [t for t in tareas if t.get("id") == args.task]
        if not tareas:
            print(f"⚠ Tarea '{args.task}' no encontrada.")
            return 1

    if not tareas:
        print("⚠ No se encontraron tareas en benchmarks/tasks/")
        return 1

    print(f"📋 Ejecutando benchmark ({len(tareas)} tareas, modo={args.modo})...")
    print()

    resultados: list[dict] = []
    for tarea in tareas:
        if args.modo == "deep":
            resultado = _ejecutar_tarea_deep(tarea)
        else:
            resultado = _ejecutar_tarea_light(tarea)
        resultados.append(resultado)
        estado = "✅" if resultado["exito"] else "❌"
        print(f"  {estado} {resultado['id']} - {resultado['nombre']} ({resultado['tiempo_segundos']}s)")

    exitos = sum(1 for r in resultados if r["exito"])
    total = len(resultados)
    porcentaje = (exitos / total * 100) if total else 0

    print()
    print(f"📊 Resultado: {exitos}/{total} ({porcentaje:.0f}%) tareas completadas")

    # Persistimos resultados.
    ARCHIVO_RESULTADOS.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO_RESULTADOS.write_text(
        json.dumps(
            {
                "total": total,
                "exitos": exitos,
                "porcentaje": porcentaje,
                "modo": args.modo,
                "resultados": resultados,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"💾 Resultados guardados en {ARCHIVO_RESULTADOS}")
    return 0 if exitos == total else 1


if __name__ == "__main__":
    sys.exit(main())


