#!/usr/bin/env python3
"""Modo demo (--demo): onboarding zero-config en menos de 30 segundos.

Muestra las funcionalidades clave de SnapContext sin necesidad de API key
ni configuración previa. La demo:

1. Crea un proyecto de ejemplo en un directorio temporal.
2. Indexa el proyecto con Graph RAG (simulado).
3. Ejecuta una consulta de ejemplo (simulada u Ollama si está disponible).
4. Muestra las funcionalidades clave: sandbox, multi-proveedor, perfiles, MCP.
5. Limpia el directorio temporal al final.

Diseñado para ejecutarse en < 30 segundos sin llamadas a APIs externas.
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from presentacion import error, exito, info

# Colores re-exportados para uso del módulo.
from snapcontext import _CYAN, _GRIS, _VERDE, _pintar

# Protocolo MCP soportado (mostrado en la demo).
_PROTOCOLO_MCP = "2024-11-05"


def _verificar_ollama(url: str = "http://localhost:11434") -> bool:
    """Comprueba si Ollama está corriendo (sin dependencias externas).

    Hace un GET ``/api/tags`` con timeout corto; si responde 200 hay un
    servidor Ollama local disponible para una demo "real".
    """
    try:
        from urllib.error import URLError
        from urllib.request import urlopen

        with urlopen(f"{url}/api/tags", timeout=2) as resp:
            return resp.status == 200
    except (URLError, OSError, Exception):
        return False


def _crear_proyecto_ejemplo(directorio: Path) -> list[str]:
    """Crea un proyecto Python mínimo de ejemplo para la demo.

    Devuelve la lista de rutas relativas creadas.
    """
    src = directorio / "src"
    src.mkdir()

    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "main.py").write_text(
        '"""Módulo principal del proyecto de ejemplo."""\n'
        "\n"
        "\n"
        "def saludar(nombre: str) -> str:\n"
        '    """Devuelve un saludo personalizado."""\n'
        '    return f"Hola, {nombre}"\n'
        "\n"
        "\n"
        "def suma(a: int, b: int) -> int:\n"
        '    """Suma dos números."""\n'
        "    return a + b\n",
        encoding="utf-8",
    )
    (src / "utils.py").write_text(
        '"""Utilidades auxiliares."""\n'
        "\n"
        "\n"
        "def formatear(texto: str) -> str:\n"
        '    """Formatea un texto (mayúsculas y strip)."""\n'
        "    return texto.strip().upper()\n",
        encoding="utf-8",
    )

    tests = directorio / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text(
        '"""Pruebas del módulo principal."""\n'
        "from src.main import saludar, suma\n"
        "\n"
        "\n"
        "def test_saludar():\n"
        "    assert saludar('Mundo') == 'Hola, Mundo'\n"
        "\n"
        "\n"
        "def test_suma():\n"
        "    assert suma(2, 3) == 5\n",
        encoding="utf-8",
    )

    (directorio / "README.md").write_text(
        "# Proyecto de ejemplo\n\nDemo de SnapContext.\n", encoding="utf-8"
    )
    (directorio / "requirements.txt").write_text("pytest\n", encoding="utf-8")

    return [
        "src/__init__.py",
        "src/main.py",
        "src/utils.py",
        "tests/test_main.py",
        "README.md",
        "requirements.txt",
    ]


def _emitir_linea(texto: str = "") -> None:
    """Escribe una línea en stdout con flush inmediato."""
    sys.stdout.write(texto + "\n")
    sys.stdout.flush()


def _mostrar_paso(n: int, total: int, mensaje: str) -> None:
    """Muestra un paso de la demo con formato consistente."""
    _emitir_linea(_pintar(f"[{n}/{total}] {mensaje}", _CYAN))


def _mostrar_funcionalidades() -> None:
    """Muestra el catálogo de funcionalidades clave de SnapContext."""
    _emitir_linea()
    _emitir_linea(_pintar("═" * 50, _GRIS))
    _emitir_linea(_pintar("  Funcionalidades clave de SnapContext", _CYAN))
    _emitir_linea(_pintar("═" * 50, _GRIS))

    funcionalidades = [
        ("Sandbox Docker", "Ejecuta código aislado con Docker (seguridad)."),
        ("Multi-proveedor", "Claude · Gemini · OpenAI · Ollama · Groq · XPU."),
        ("Perfiles de prompt", "Optimización automática por modelo y tarea."),
        ("Graph RAG", "Grafo de dependencias del código (tree-sitter)."),
        ("Cliente MCP", f"Conecta servidores MCP externos (protocolo {_PROTOCOLO_MCP})."),
    ]

    for nombre, desc in funcionalidades:
        _emitir_linea(f"    {_pintar('✓', _VERDE)} {nombre}: {desc}")
    _emitir_linea()


def _demo_consulta_simulada(consulta: str) -> dict:
    """Respuesta predefinida para la demo offline."""
    return {
        "ok": True,
        "respuesta": (
            "Es un proyecto Python con funciones de utilidad: saludar() devuelve "
            "un saludo personalizado y suma() añade dos números. Incluye tests."
        ),
    }


def _demo_consulta_ollama(directorio: Path, consulta: str) -> dict:
    """Intenta una consulta real a Ollama (si está disponible)."""
    try:
        from urllib.request import Request, urlopen

        payload = json.dumps(
            {
                "model": "llama3.2:latest",
                "prompt": f"Proyecto en {directorio}. {consulta}",
                "stream": False,
            }
        ).encode("utf-8")

        req = Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=15) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "respuesta": datos.get("response", "(sin respuesta)")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def ejecutar_demo() -> int:
    """Ejecuta la demo autónoma de SnapContext (sin API key).

    Devuelve 0 si la demo se completó correctamente, 1 si falló.
    """
    t_inicio = time.monotonic()
    total_pasos = 4

    _emitir_linea()
    _emitir_linea(_pintar("╔══════════════════════════════════════════════════╗", _CYAN))
    _emitir_linea(_pintar("║       Demostración de SnapContext (30 segundos)  ║", _CYAN))
    _emitir_linea(_pintar("╚══════════════════════════════════════════════════╝", _CYAN))
    _emitir_linea()

    # Paso 1: crear proyecto de ejemplo.
    _mostrar_paso(1, total_pasos, "Creando proyecto de ejemplo...")
    tmp = Path(tempfile.mkdtemp(prefix="snapcontext-demo-"))
    try:
        archivos = _crear_proyecto_ejemplo(tmp)
        exito(f"Proyecto creado en: {tmp}")
        for archivo in archivos:
            _emitir_linea(f"    {_pintar('•', _VERDE)} {archivo}")
        _emitir_linea()

        # Paso 2: indexar con Graph RAG (simulado).
        _mostrar_paso(2, total_pasos, "Indexando el proyecto con Graph RAG...")
        time.sleep(0.3)  # pausa visual
        exito(f"Indexados {len(archivos)} archivos (grafo de dependencias).")
        _emitir_linea()

        # Paso 3: consulta de ejemplo.
        _mostrar_paso(3, total_pasos, "Ejecutando una consulta de ejemplo...")
        consulta = "¿Qué hace este proyecto?"
        info(f'Consulta: "{consulta}"')

        # Intentar Ollama primero; si no, demo simulada.
        if _verificar_ollama():
            info("Ollama detectado → consulta real al modelo local.")
            resultado = _demo_consulta_ollama(tmp, consulta)
        else:
            info("Sin Ollama → demo offline con respuesta predefinida.")
            resultado = _demo_consulta_simulada(consulta)

        if resultado.get("ok"):
            exito("Respuesta del agente:")
            _emitir_linea(f'    "{resultado["respuesta"]}"')
        else:
            error("La consulta no pudo completarse.")
        _emitir_linea()

        # Paso 4: funcionalidades clave.
        _mostrar_paso(4, total_pasos, "Mostrando funcionalidades clave...")
        _mostrar_funcionalidades()

        # Resumen final.
        duracion = time.monotonic() - t_inicio
        _emitir_linea(_pintar("═" * 50, _GRIS))
        _emitir_linea(_pintar(f"  Demo completada en {duracion:.1f} segundos", _VERDE))
        _emitir_linea(_pintar("═" * 50, _GRIS))
        _emitir_linea()
        exito("Para usar SnapContext con tu propio proyecto:")
        _emitir_linea(_pintar("    snapcontext --init", _CYAN))
        _emitir_linea()
        exito("Documentación: https://nicolasbruna24.github.io/snapcontext/")
        _emitir_linea()

        return 0
    except Exception as exc:
        error(f"La demo falló: {exc}")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
