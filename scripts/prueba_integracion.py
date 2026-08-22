#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pruebas de integración de SnapContext 1.0.0.

Ejecuta los modos principales de la CLI sobre un proyecto de prueba temporal
y verifica que responden correctamente. No requiere API key: los modos que
hablarían con un proveedor usan `--local`/fallback offline.

Uso:
    python scripts/prueba_integracion.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import snapcontext as sc  # noqa: E402

FALLOS = []
GUARDADOS = []   # entradas de historial capturadas durante el plan simulado


def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
    marca = "✔" if condicion else "✖"
    print(f"  {marca} {nombre}" + (f" — {detalle}" if detalle and not condicion else ""))
    if not condicion:
        FALLOS.append(nombre)


def cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RAIZ / "snapcontext.py"), *args],
        input=stdin, capture_output=True, text=True, timeout=120,
        cwd=str(RAIZ), encoding="utf-8", errors="replace",
    )


def main() -> int:
    print(f"SnapContext v{sc.VERSION} — pruebas de integración\n")

    # 0) Versión.
    r = cli("--version")
    comprobar("--version muestra 1.0.0", "1.0.0" in r.stdout, r.stdout[-200:])

    # 1) Proyecto de prueba temporal (Python con un bug y su test).
    proyecto = Path(tempfile.mkdtemp(prefix="snapcontext-it-"))
    src = proyecto / "src"; src.mkdir()
    (src / "calc.py").write_text("def suma(a, b):\n    return a - b\n",
                                 encoding="utf-8")
    tests = proyecto / "tests"; tests.mkdir()
    (tests / "test_calc.py").write_text(
        "from src.calc import suma\n"
        "def test_suma():\n    assert suma(2, 3) == 5\n", encoding="utf-8")
    (proyecto / "pyproject.toml").write_text(
        "[project]\nname = 'demo-it'\nversion = '0.1.0'\n", encoding="utf-8")

    # 2) --demo (sin API key ni Aider).
    r = cli("--demo")
    comprobar("--demo termina con éxito", r.returncode == 0,
              (r.stderr or r.stdout)[-200:])

    # 3) --chat con comandos internos (no requiere proveedor).
    r = cli("--chat", stdin="/ayuda\n/salir\n")
    comprobar("--chat abre REPL y responde /ayuda",
              r.returncode == 0 and "SnapContext Chat" in r.stdout
              and "/ayuda" in r.stdout, r.stdout[-200:])

    # 4) --init-claude sin API key → plantilla offline.
    env = {k: v for k, v in os.environ.items()
           if not k.endswith("API_KEY")}
    r = subprocess.run(
        [sys.executable, str(RAIZ / "snapcontext.py"), "--init-claude"],
        capture_output=True, text=True, timeout=120,
        cwd=str(proyecto), env=env, encoding="utf-8", errors="replace")
    memoria = proyecto / "CLAUDE.md"
    comprobar("--init-claude genera CLAUDE.md (offline)",
              r.returncode == 0 and memoria.is_file()
              and "## Objetivo" in memoria.read_text(encoding="utf-8"),
              (r.stderr or r.stdout)[-200:])

    # 5) Planificador en modo autónomo con proveedor simulado (in-process).
    pasos = [{"descripcion": "listar archivos", "accion": "ejecutar",
              "comando": f'"{sys.executable}" --version'}]
    guardados = []
    with mock_plan(pasos):
        codigo = sc._ejecutar_planificador(sc.argparse.Namespace(
            consulta="integración", depurar=False, provider="ollama",
            modelo=None, git_commit=False, branch=None, directorio=str(proyecto),
            test_loop=False, aider_opciones="", comando_test="pytest",
            max_iteraciones=1, confirmar=False, auto=True))
    comprobar("--plan --auto ejecuta pasos (proveedor simulado)",
              codigo == 0 and len(GUARDADOS) == 1
              and GUARDADOS[0]["tipo"] == "plan",
              f"codigo={codigo}")

    # 6) Historial persistente.
    r = cli("--historial")
    comprobar("--historial muestra tareas", r.returncode == 0
              and ("tarea" in r.stdout or "Historial" in r.stdout
                   or "plan" in r.stdout), r.stdout[-200:])

    print()
    if FALLOS:
        print(f"✖ {len(FALLOS)} prueba(s) fallaron: {', '.join(FALLOS)}")
        return 1
    print("✔ Todas las pruebas de integración pasaron.")
    return 0


def mock_plan(pasos):
    """Context manager que simula el proveedor para _generar_plan y guarda
    las entradas de historial para verificarlas."""
    from unittest import mock
    import tempfile

    dir_tmp = Path(tempfile.mkdtemp(prefix="snapcontext-it-cfg-"))
    cm1 = mock.patch.object(sc, "CONFIG_DIR", dir_tmp)
    cm2 = mock.patch.object(sc, "HISTORIAL_PATH", dir_tmp / "historial.json")
    cm3 = mock.patch.object(sc, "_generar_plan", return_value=pasos)
    cm4 = mock.patch.object(
        sc, "_guardar_historial",
        side_effect=lambda e: (GUARDADOS.append(e), True)[1])
    cm1.start(); cm2.start(); cm3.start(); cm4.start()

    class _CM:
        def __enter__(self):
            return None

        def __exit__(self, *a):
            cm1.stop(); cm2.stop(); cm3.stop(); cm4.stop()

    return _CM()


if __name__ == "__main__":
    raise SystemExit(main())
