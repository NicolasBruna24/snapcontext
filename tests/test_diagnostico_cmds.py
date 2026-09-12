"""Tests de diagnostico.py: --diagnostico, --reparar y --benchmark.

Todo lo externo (subprocess, sqlite del usuario, Ollama, PATH) está mockeado;
las bases SQLite de prueba se crean en temporales.
"""

from __future__ import annotations

import argparse
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import diagnostico as diag
import snapcontext as sc


def _args(**kw):
    return argparse.Namespace(**kw)


class TestDiagnosticoItem(unittest.TestCase):
    def test_ok(self):
        with mock.patch("diagnostico.exito") as ex:
            self.assertTrue(diag._diagnostico_item("X", True, "detalle"))
        ex.assert_called_once()

    def test_aviso_con_solucion(self):
        with (
            mock.patch("diagnostico.aviso") as av,
            mock.patch("diagnostico._pintar", return_value="pintado"),
            mock.patch("builtins.print") as pr,
        ):
            self.assertFalse(diag._diagnostico_item("X", False, "detalle", "solución"))
        av.assert_called_once()
        pr.assert_called_once_with("pintado")

    def test_error_sin_solucion(self):
        with mock.patch("diagnostico.error") as er:
            self.assertFalse(diag._diagnostico_item("X", False, "detalle", None))
        er.assert_called_once()


class TestDependenciasOpcionales(unittest.TestCase):
    def test_estructura_y_fastapi_presente(self):
        resultados = diag._comprobar_dependencias_opcionales()
        self.assertGreaterEqual(len(resultados), 6)
        self.assertIn("fastapi", [r[0] for r in resultados])
        for _paquete, presente, extra in resultados:
            self.assertIsInstance(presente, bool)
            self.assertTrue(extra)


class TestEstadoMemoria(unittest.TestCase):
    def test_base_inexistente(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "no-existe.db"
            with mock.patch.object(sc, "DB_PATH", ruta):
                estado = diag._estado_memoria()
        self.assertFalse(estado["ok"])
        self.assertIn("No existe", estado["error"])

    def test_base_ok_con_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "memoria.db"
            con = sqlite3.connect(str(ruta))
            con.execute("CREATE TABLE skills (id INTEGER)")
            con.execute("INSERT INTO skills VALUES (1), (2), (3)")
            con.commit()
            con.close()
            with mock.patch.object(sc, "DB_PATH", ruta):
                estado = diag._estado_memoria()
        self.assertTrue(estado["ok"])
        self.assertEqual(estado["skills"], 3)

    def test_base_corrupta(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "memoria.db"
            ruta.write_bytes(b"esto no es sqlite\x00\x01\x02")
            with mock.patch.object(sc, "DB_PATH", ruta):
                estado = diag._estado_memoria()
        self.assertFalse(estado["ok"])
        self.assertIn("corrupta", estado["error"])

    def test_tabla_skills_ausente(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "memoria.db"
            con = sqlite3.connect(str(ruta))
            con.commit()
            con.close()
            with mock.patch.object(sc, "DB_PATH", ruta):
                estado = diag._estado_memoria()
        self.assertTrue(estado["ok"])
        self.assertEqual(estado["skills"], 0)


class TestReparaciones(unittest.TestCase):
    def test_limpiar_entorno_uv_vacio(self):
        with tempfile.TemporaryDirectory() as tmp:
            vacia = Path(tmp) / ".venv-uv"
            vacia.mkdir()
            llena = Path(tmp) / ".venv"
            llena.mkdir()
            (llena / "f.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(diag, "CONFIG_DIR", Path(tmp)):
                limpio = diag._limpiar_entorno_uv_corrupto()
                self.assertTrue(limpio)
                self.assertFalse(vacia.exists())
                self.assertTrue(llena.exists())

    def test_limpiar_sin_carpetas(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(diag, "CONFIG_DIR", Path(tmp)):
                self.assertFalse(diag._limpiar_entorno_uv_corrupto())

    def test_reinstalar_ok(self):
        proc = types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with mock.patch("diagnostico.subprocess.run", return_value=proc):
            self.assertTrue(diag._reinstalar_snapcontext())

    def test_reinstalar_fallo_pip(self):
        proc = types.SimpleNamespace(returncode=1, stdout="", stderr="error de pip")
        with mock.patch("diagnostico.subprocess.run", return_value=proc):
            self.assertFalse(diag._reinstalar_snapcontext())

    def test_reinstalar_os_error(self):
        with mock.patch("diagnostico.subprocess.run", side_effect=OSError("sin pip")):
            self.assertFalse(diag._reinstalar_snapcontext())

    def test_reparar_memoria_ok_directo(self):
        with mock.patch.object(
            diag, "_estado_memoria", return_value={"ok": True, "skills": 1, "error": None}
        ):
            self.assertTrue(diag._reparar_memoria_si_corrupta())

    def test_reparar_memoria_corrupta(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "memoria.db"
            ruta.write_text("vieja", encoding="utf-8")
            with (
                mock.patch.object(
                    diag,
                    "_estado_memoria",
                    return_value={
                        "ok": False,
                        "skills": 0,
                        "error": "La base de datos está corrupta.",
                    },
                ),
                mock.patch.object(sc, "DB_PATH", ruta),
                mock.patch.object(sc, "_db_init") as db_init,
            ):
                self.assertTrue(diag._reparar_memoria_si_corrupta())
            self.assertFalse(ruta.exists())
            self.assertTrue(Path(str(ruta).replace(".db", ".db.corrupto")).exists())
            db_init.assert_called_once()

    def test_reparar_memoria_inexistente_inicializa(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "memoria.db"
            with (
                mock.patch.object(
                    diag,
                    "_estado_memoria",
                    return_value={"ok": False, "skills": 0, "error": "No existe la base."},
                ),
                mock.patch.object(sc, "DB_PATH", ruta),
                mock.patch.object(sc, "_db_init") as db_init,
            ):
                self.assertTrue(diag._reparar_memoria_si_corrupta())
            db_init.assert_called_once()

    def test_reparar_memoria_os_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "no-existe.db"
            with (
                mock.patch.object(
                    diag,
                    "_estado_memoria",
                    return_value={
                        "ok": False,
                        "skills": 0,
                        "error": "La base de datos está corrupta.",
                    },
                ),
                mock.patch.object(sc, "DB_PATH", ruta),
            ):
                self.assertFalse(diag._reparar_memoria_si_corrupta())


class TestEjecutarDiagnostico(unittest.TestCase):
    """_ejecutar_diagnostico con todos los checks simulados."""

    def _ejecuta(self, api=True, deps=None, en_path=True, memoria=None):
        deps = deps if deps is not None else [("fastapi", True, "x")]
        memoria = memoria or {"ok": True, "skills": 2, "error": None}
        with (
            mock.patch.object(diag, "_comprobar_dependencias_opcionales", return_value=deps),
            mock.patch.object(diag, "snapcontext_en_path", return_value=en_path),
            mock.patch.object(diag, "_estado_memoria", return_value=memoria),
            mock.patch.object(sc, "hay_api_key_configurada", return_value=api),
        ):
            return diag._ejecutar_diagnostico(_args())

    def test_todo_ok_devuelve_0(self):
        # Determinista: sin el mock, depende de si snapcontext está instalado
        # (sin pip install -e . el check de metadatos añade 1 aviso → return 2).
        with mock.patch("importlib.metadata.version", return_value="6.35.3"):
            self.assertEqual(self._ejecuta(), 0)

    def test_dependencia_faltante_devuelve_2(self):
        self.assertEqual(self._ejecuta(deps=[("questionary", False, "pip install q")]), 2)

    def test_path_no_disponible_devuelve_2(self):
        self.assertEqual(self._ejecuta(en_path=False), 2)

    def test_sin_api_ni_ollama_devuelve_1(self):
        with mock.patch.object(
            sc, "_estado_ollama", return_value={"instalado": False, "modelos": []}
        ):
            self.assertEqual(self._ejecuta(api=False), 1)

    def test_ollama_listo_devuelve_0(self):
        with (
            mock.patch("importlib.metadata.version", return_value="6.35.3"),
            mock.patch.object(
                sc, "_estado_ollama", return_value={"instalado": True, "modelos": ["llama3.2"]}
            ),
            mock.patch.object(sc, "_elegir_modelo_ligero", return_value="llama3.2"),
        ):
            self.assertEqual(self._ejecuta(api=False), 0)

    def test_memoria_corrupta_devuelve_1(self):
        codigo = self._ejecuta(
            memoria={"ok": False, "skills": 0, "error": "La base de datos está corrupta."}
        )
        self.assertEqual(codigo, 1)

    def test_paquete_no_instalado_genera_aviso(self):
        with mock.patch("importlib.metadata.version", side_effect=Exception("no instalado")):
            self.assertEqual(self._ejecuta(), 2)


class TestEjecutarReparacion(unittest.TestCase):
    def _ejecuta(self, limpiar=False, reinstalar=True, memoria=True, en_path=True):
        with (
            mock.patch.object(diag, "_limpiar_entorno_uv_corrupto", return_value=limpiar),
            mock.patch.object(diag, "_reinstalar_snapcontext", return_value=reinstalar),
            mock.patch.object(diag, "_reparar_memoria_si_corrupta", return_value=memoria),
            mock.patch.object(diag, "snapcontext_en_path", return_value=en_path),
        ):
            return diag._ejecutar_reparacion(_args())

    def test_reparacion_exitosa(self):
        self.assertEqual(self._ejecuta(), 0)

    def test_reinstalacion_falla(self):
        self.assertEqual(self._ejecuta(reinstalar=False), 1)

    def test_memoria_falla(self):
        self.assertEqual(self._ejecuta(memoria=False), 1)


class TestBenchmark(unittest.TestCase):
    def test_benchmark_completo(self):
        fake_parser = types.SimpleNamespace(
            parse_args=mock.Mock(
                return_value=types.SimpleNamespace(carpetas=None, extensiones=None)
            )
        )
        with (
            mock.patch.object(sc, "crear_parser", return_value=fake_parser),
            mock.patch.object(sc, "listar_archivos_candidatos", return_value=[]),
            mock.patch.object(sc, "_embeddings_disponibles", return_value=False),
            mock.patch.object(sc, "PROMPT_PLAN", "plan {consulta}"),
            mock.patch.object(sc, "_enriquecer_prompt_con_reglas") as enriquecer,
            mock.patch.object(diag, "_bench_fuzzy_edicion", return_value=True),
            mock.patch.object(sc, "_mostrar_tabla_benchmark") as tabla,
        ):
            codigo = diag._ejecutar_benchmark(_args())
        self.assertEqual(codigo, 0)
        enriquecer.assert_called_once()
        tabla.assert_called_once()

    def test_tabla_sin_rich(self):
        with mock.patch.dict("sys.modules", {"rich.console": None, "rich.table": None}):
            diag._mostrar_tabla_benchmark([("fase", 0.001)])

    def test_tabla_con_rich_si_esta_disponible(self):
        diag._mostrar_tabla_benchmark([("fase", 0.5)])

    def test_bench_fuzzy_sintetico(self):
        with (
            mock.patch.object(sc, "_aplicar_hunks_incremental", return_value=True) as aplicar,
            mock.patch.object(sc, "_generar_parche", return_value="@@ -1 +1 @@\n-a\n+b\n"),
        ):
            self.assertTrue(diag._bench_fuzzy_edicion("."))
        aplicar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
