"""Comandos de diagnóstico, reparación y benchmark (Fase 2).

Extraído del monolito: ``--diagnostico``, ``--reparar`` y ``--benchmark``
con sus helpers (estado de la memoria SQLite, dependencias opcionales,
reinstalación, tabla de tiempos). Los globals del monolito (DB_PATH,
embeddings, parser CLI, VERSION...) se leen como ``_sc.X`` en runtime
para mantener la compatibilidad con mock.patch sobre snapcontext.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from configuracion import CONFIG_DIR
from instalador import configurar_path, snapcontext_en_path
from presentacion import (
    _AMARILLO,
    _ROJO,
    _emitir,
    _pintar,
    aviso,
    error,
    exito,
    info,
)


# ---------------------------------------------------------------------------
# Diagnóstico y reparación (v3.1.0)
# ---------------------------------------------------------------------------
def _diagnostico_item(nombre: str, ok: bool, detalle: str, solucion: str | None = None) -> bool:
    """Imprime una línea de diagnóstico con color según el estado."""
    if ok:
        exito(f"{nombre}: {detalle}")
    elif solucion:
        aviso(f"{nombre}: {detalle}")
        print(_pintar("    → Solución: " + solucion, _AMARILLO))
    else:
        error(f"{nombre}: {detalle}")
    return ok


def _comprobar_dependencias_opcionales() -> list[tuple]:
    """Devuelve (modulo, instalado, instalacion) para dependencias opcionales."""
    modulos = [
        ("questionary", "questionary", "pip install snapcontext[interactive]"),
        ("fastapi", "fastapi", "pip install snapcontext[web]"),
        ("uvicorn", "uvicorn", "pip install snapcontext[web]"),
        ("sentence_transformers", "sentence-transformers", "pip install sentence-transformers"),
        ("openai", "openai", "pip install openai"),
        ("google.generativeai", "google-generativeai", "pip install google-generativeai"),
        ("aider", "aider-chat", "pip install aider-chat"),
    ]
    resultados = []
    for modulo, paquete, extra in modulos:
        try:
            __import__(modulo)
            resultados.append((paquete, True, extra))
        except ImportError:
            resultados.append((paquete, False, extra))
    return resultados


def _estado_memoria() -> dict:
    """Comprueba la base SQLite y el número de skills.

    Devuelve {'ok': bool, 'skills': int, 'error': str|None}.
    """
    import snapcontext as _sc

    if not os.path.exists(_sc.DB_PATH):
        return {
            "ok": False,
            "skills": 0,
            "error": f"No existe {_sc.DB_PATH} (se crea al primer uso).",
        }
    try:
        import sqlite3

        con = sqlite3.connect(str(_sc.DB_PATH))
        try:
            try:
                resultado = con.execute("PRAGMA quick_check").fetchone()
                if not resultado or resultado[0] != "ok":
                    return {"ok": False, "skills": 0, "error": "La base de datos está corrupta."}
            except sqlite3.DatabaseError:
                return {"ok": False, "skills": 0, "error": "La base de datos está corrupta."}
            try:
                skills = con.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            except sqlite3.Error:
                skills = 0
            return {"ok": True, "skills": skills, "error": None}
        finally:
            con.close()
    except Exception as exc:
        return {"ok": False, "skills": 0, "error": str(exc)}


def _ejecutar_diagnostico(args: argparse.Namespace) -> int:  # noqa: C901  (refactor de complejidad: Fase 10c)
    """Modo --diagnostico: revisa la instalación y muestra un resumen.

    Comprueba Python, instalación del paquete, dependencias opcionales,
    PATH, proveedor de IA (API key / Ollama) y memoria SQLite.
    Devuelve 0 si todo está OK, 1 si hay errores y 2 si solo hay avisos.
    """
    import snapcontext as _sc

    info("=== SnapContext · Diagnóstico ===")
    problemas = 0
    avisos = 0

    # 1) Python
    version_py = sys.version.split()[0]
    en_path = any(shutil.which(cmd) for cmd in ("python", "python3", "py"))
    if not _diagnostico_item(
        "Python",
        en_path,
        f"v{version_py} ({sys.executable})" if en_path else "no se encontró 'python' en el PATH",
        "Instala Python 3.9+ desde https://python.org y marca 'Add to PATH'",
    ):
        problemas += 1

    # 2) Instalación de SnapContext
    if getattr(sys, "frozen", False):
        exito("SnapContext: instalado como ejecutable empaquetado.")
    else:
        try:
            from importlib.metadata import version as _meta_version

            instalada = _meta_version("snapcontext")
            exito(
                f"SnapContext: instalado (v{instalada}). "
                "`python -m snapcontext --version` disponible."
            )
        except Exception:
            aviso("SnapContext no consta como paquete instalado.")
            print(
                _pintar(
                    "    → Solución: pip install snapcontext (o python -m pip install -e .)",
                    _AMARILLO,
                )
            )
            avisos += 1

    # 3) Dependencias opcionales
    for paquete, presente, extra in _comprobar_dependencias_opcionales():
        if presente:
            exito(f"Dependencia '{paquete}': OK.")
        else:
            aviso(f"Dependencia opcional '{paquete}' no instalada.")
            print(_pintar(f"    → Instalar con: {extra}", _AMARILLO))
            avisos += 1

    # 4) PATH
    if snapcontext_en_path():
        exito("PATH: el comando 'snapcontext' es accesible.")
    else:
        aviso("PATH: 'snapcontext' no es accesible como comando global.")
        print(
            _pintar(
                "    → Solución: ejecuta 'snapcontext --setup-path' "
                "(Windows) o reinstala con install.ps1/install.sh",
                _AMARILLO,
            )
        )
        avisos += 1

    # 5) Proveedor de IA / modo offline
    if _sc.hay_api_key_configurada():
        exito("Proveedor de IA: API key configurada.")
    else:
        estado_ol = _sc._estado_ollama()
        if estado_ol["modelos"]:
            ligero = _sc._elegir_modelo_ligero(estado_ol["modelos"])
            exito(
                "Proveedor de IA: sin API key, pero Ollama está listo "
                f"(modo offline con '{ligero}')."
            )
        elif estado_ol["instalado"]:
            aviso("Ollama instalado pero sin modelos descargados.")
            print(_pintar("    → Solución: ollama pull llama3.2", _AMARILLO))
            avisos += 1
        else:
            error("No se encontró una API key ni Ollama.")
            print(
                _pintar(
                    "    → Solución: instala Ollama desde "
                    "https://ollama.com o ejecuta 'snapcontext --init'.",
                    _ROJO,
                )
            )
            problemas += 1

    # 6) Memoria (SQLite + skills)
    memoria = _estado_memoria()
    if memoria["ok"]:
        exito(f"Memoria: base de datos OK ({memoria['skills']} skills).")
    elif memoria["error"] and "corrupta" in (memoria["error"] or ""):
        error(f"Memoria: {memoria['error']}")
        print(_pintar("    → Solución: ejecuta 'snapcontext --reparar'", _ROJO))
        problemas += 1
    else:
        aviso(f"Memoria: {memoria['error']}")
        avisos += 1

    print()
    if problemas:
        error(
            f"Diagnóstico completado con {problemas} problema(s) y "
            f"{avisos} aviso(s). Ejecuta 'snapcontext --reparar' si lo "
            "necesitas."
        )
        return 1
    if avisos:
        aviso(f"Diagnóstico completado: todo funcional, {avisos} aviso(s).")
        return 2
    exito("Diagnóstico completado: todo correcto ✔")
    return 0


def _limpiar_entorno_uv_corrupto() -> bool:
    """Elimina carpetas de entorno de 'uv' vacías/corruptas (v3.1.0).

    Un fallo conocido deja entornos vacíos que rompen reintentos.
    Devuelve True si se limpió algo.
    """
    limpio = False
    for carpeta in (CONFIG_DIR / ".venv-uv", CONFIG_DIR / ".venv"):
        try:
            if carpeta.is_dir() and not any(carpeta.iterdir()):
                carpeta.rmdir()
                info(f"Entorno uv vacío eliminado: {carpeta}")
                limpio = True
        except OSError:
            pass
    return limpio


def _reinstalar_snapcontext() -> bool:
    """Reinstala SnapContext con pip (con fallback a `uv pip`)."""
    comando = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--force-reinstall",
        "--no-deps",
        "snapcontext",
    ]
    info("Reinstalando SnapContext con pip...")
    try:
        proc = subprocess.run(comando, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0:
            exito("SnapContext reinstalado correctamente.")
            return True
        aviso("pip devolvió un error: " + ((proc.stderr or proc.stdout or "").strip()[-300:]))
    except (OSError, subprocess.SubprocessError) as exc:
        aviso(f"No se pudo ejecutar pip: {exc}")
    return False


def _reparar_memoria_si_corrupta() -> bool:
    """Recrea la base SQLite si está corrupta. True si quedó operativa."""
    import snapcontext as _sc

    estado = _estado_memoria()
    if estado["ok"]:
        return True
    if estado["error"] and "corrupta" in estado["error"]:
        try:
            copia = _sc.DB_PATH.with_suffix(".db.corrupto")
            if os.path.exists(copia):
                copia.unlink()
            _sc.DB_PATH.rename(copia)
            _sc._db_init()
            aviso(f"Base de datos corrupta respaldada como '{copia.name}' y recreada.")
            return True
        except OSError as exc:
            error(f"No se pudo reparar la base de datos: {exc}")
            return False
    # No existe aún: crearla.
    try:
        _sc._db_init()
        exito("Memoria inicializada.")
        return True
    except Exception as exc:  # pragma: no cover
        error(f"No se pudo inicializar la memoria: {exc}")
        return False


def _ejecutar_reparacion(args: argparse.Namespace) -> int:
    """Modo --reparar: arregla instalaciones rotas paso a paso.

    Pasos: limpiar entornos uv corruptos, reinstalar con pip, reparar la
    base SQLite y añadir la carpeta de scripts al PATH (Windows).
    """
    info("=== SnapContext · Reparación ===")
    ok_global = True

    if _limpiar_entorno_uv_corrupto():
        exito("Entornos uv corruptos eliminados.")

    if not _reinstalar_snapcontext():
        ok_global = False

    if _reparar_memoria_si_corrupta():
        exito("Memoria verificada/reparada.")
    else:
        ok_global = False

    if sys.platform.startswith("win") and not snapcontext_en_path():
        aviso("El comando 'snapcontext' sigue sin estar en el PATH; ejecutando --setup-path...")
        if configurar_path() != 0:
            ok_global = False
    elif snapcontext_en_path():
        exito("PATH correcto: 'snapcontext' accesible.")

    if ok_global:
        exito("Reparación completada. Prueba 'snapcontext --diagnostico'.")
        return 0
    error("La reparación terminó con incidencias; revisa los mensajes.")
    return 1


def _ejecutar_benchmark(args: argparse.Namespace) -> int:
    """``--benchmark``: mide y muestra el tiempo de cada fase (v6.9.0).

    Mide fases reales de SnapContext sin necesidad de API key:
      • Inicio (import del módulo + CLI).
      • Escaneo de archivos.
      • Selección (embeddings si disponible; si no, heurística local).
      • Preparación de plan (prompt + contexto, offline).
      • Edición (fuzzy matching incremental sobre un archivo sintético).
      • Detección/validación de pruebas.
      • Total.
    Muestra una tabla con `rich` (fallo a print plano si no está instalado).
    """
    import time as _t

    import snapcontext as _sc

    directorio = getattr(args, "directorio", None) or "."
    filas: list[tuple] = []

    filas.append(("Inicio (import + CLI)", _t.perf_counter() - _sc._TIEMPO_INICIO_MODULO))

    _t0 = _t.perf_counter()
    _sc.crear_parser()
    filas.append(("CLI (crear_parser)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    carpetas = list(getattr(args, "carpetas", None) or _sc.CARPETAS_DEFECTO)
    try:
        candidatos = _sc.listar_archivos_candidatos(
            Path(directorio), carpetas, extensiones=getattr(args, "extensiones", None)
        )
    except Exception:
        candidatos = []
    filas.append(("Escaneo de archivos", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    list(candidatos[:3])  # v6.34.12: sin efecto medible fuera del cronómetro.
    try:
        if _sc._embeddings_disponibles():
            _sc._indexar_proyecto(directorio)
            _sc._seleccionar_archivos_con_embeddings(
                "(benchmark)", directorio, max_archivos=3
            )  # v6.34.12: resultado no usado aquí (solo calienta la caché).
    except Exception:
        pass
    filas.append(("Selección (embeddings/heurística)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    try:
        prompt = _sc.PROMPT_PLAN.format(consulta="(benchmark)")
        _sc._enriquecer_prompt_con_reglas(prompt, "(benchmark)")
    except Exception:
        pass
    filas.append(("Generación de plan (prompt+contexto)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    _fuzzy = _bench_fuzzy_edicion(directorio)
    filas.append(("Edición (fuzzy matching)", _t.perf_counter() - _t0))

    _t0 = _t.perf_counter()
    try:
        import detector_tests as _det

        _det = _det
    except Exception:
        pass
    filas.append(("Detección de pruebas", _t.perf_counter() - _t0))

    total = _t.perf_counter() - _sc._TIEMPO_INICIO_MODULO
    filas.append(("Tiempo total", total))

    _sc._mostrar_tabla_benchmark(filas)
    return 0


def _bench_fuzzy_edicion(directorio: str) -> bool:
    """Ejercita el fuzzy matching incremental sobre un archivo sintético."""
    import tempfile

    import snapcontext as _sc

    try:
        tmp = Path(tempfile.mkdtemp(prefix="sc_bench_"))
        linea = "    return valor * 2\n"
        contenido = "def calcular_bench(num):\n" + linea * 60 + "    return procesar(num)\n"
        archivo = tmp / "bench.py"
        archivo.write_text(contenido, encoding="utf-8")
        original = "    return procesar(num)\n"
        nuevo = "    return procesar_mejor(num)\n"
        parche = _sc._generar_parche(original, nuevo, "bench.py")
        ok = _sc._aplicar_hunks_incremental(parche, str(tmp))
        return ok
    except Exception:
        return False


def _mostrar_tabla_benchmark(filas: list[tuple]) -> None:
    """Pinta la tabla de tiempos con `rich` (o print plano sin él)."""
    import snapcontext as _sc

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        tabla = Table(
            title=f"⚡ Benchmark de rendimiento — SnapContext {_sc.VERSION}",
            title_style="bold cyan",
            header_style="bold magenta",
        )
        tabla.add_column("Fase", style="cyan")
        tabla.add_column("Tiempo (s)", justify="right")
        for nombre, seg in filas:
            tabla.add_row(nombre, f"{seg:.4f}")
        console.print(tabla)
    except Exception:
        _emitir(sys.stdout, f"⚡ Benchmark de rendimiento — SnapContext {_sc.VERSION}")
        for nombre, seg in filas:
            _emitir(sys.stdout, f"  {nombre:<40} {seg:.4f} s")
