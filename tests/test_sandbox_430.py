#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v4.3.0: sandbox opcional con Docker."""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import snapcontext as sc  # noqa: E402


def _args_base(**extra) -> argparse.Namespace:
    base = {"consulta": None, "depurar": False, "auto": False,
            "confirmar": True, "modelo": None, "test_loop": False,
            "aider_opciones": "", "comando_test": "pytest"}
    base.update(extra or {})
    return argparse.Namespace(**base)


class TestDeteccionDocker(unittest.TestCase):
    """Detección de Docker: presencia, ausencia y daemon parado."""

    def test_sin_binario_en_path_no_disponible(self):
        with mock.patch.object(sc.shutil, "which", return_value=None):
            self.assertFalse(sc._docker_disponible())

    def test_docker_info_ok_disponible(self):
        proc = mock.Mock(returncode=0)
        with mock.patch.object(sc.shutil, "which", return_value="docker"), \
                mock.patch.object(sc.subprocess, "run", return_value=proc):
            self.assertTrue(sc._docker_disponible())

    def test_docker_daemon_parado_no_disponible(self):
        proc = mock.Mock(returncode=1)
        with mock.patch.object(sc.shutil, "which", return_value="docker"), \
                mock.patch.object(sc.subprocess, "run", return_value=proc):
            self.assertFalse(sc._docker_disponible())

    def test_error_al_llamar_docker_tolerado(self):
        with mock.patch.object(sc.shutil, "which", return_value="docker"), \
                mock.patch.object(sc.subprocess, "run",
                                  side_effect=OSError("boom")):
            self.assertFalse(sc._docker_disponible())


class TestActivacionSandbox(unittest.TestCase):
    def setUp(self):
        sc._desactivar_sandbox()

    def tearDown(self):
        sc._desactivar_sandbox()

    def test_activacion_estricta_falla_sin_docker(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=False):
            with self.assertRaises(RuntimeError):
                sc._activar_sandbox(estricto=True)
        self.assertFalse(sc.sandbox_activo())

    def test_activacion_no_estricta_avisa_y_continua(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=False), \
                mock.patch.object(sc, "aviso") as fake_aviso:
            self.assertFalse(sc._activar_sandbox(estricto=False))
            fake_aviso.assert_called_once()
        self.assertFalse(sc.sandbox_activo())

    def test_activacion_con_docker(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=True):
            self.assertTrue(sc._activar_sandbox(imagen="ubuntu:22.04",
                                                comando_prep="apt update"))
        self.assertTrue(sc.sandbox_activo())
        self.assertEqual(sc._SANDBOX_IMAGEN, "ubuntu:22.04")
        self.assertEqual(sc._SANDBOX_COMANDO_PREP, "apt update")

    def test_imagen_por_variable_de_entorno(self):
        env = {"SNAPCONTEXT_SANDBOX_IMAGE": "python:3.12"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(sc._sandbox_imagen_resuelta(), "python:3.12")
            # El flag explícito tiene prioridad sobre la variable.
            self.assertEqual(sc._sandbox_imagen_resuelta("alpine"),
                             "alpine")

    def test_flag_cli_parseado(self):
        args = sc.crear_parser().parse_args(
            ["--sandbox", "--sandbox-imagen", "ubuntu:22.04",
             "--sandbox-comando", "apt update", "consulta"])
        self.assertTrue(args.sandbox)
        self.assertEqual(args.sandbox_imagen, "ubuntu:22.04")
        self.assertEqual(args.sandbox_comando, "apt update")
        # Sin flags → False (compatibilidad).
        args2 = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(args2.sandbox)


class TestEnvolverSandbox(unittest.TestCase):
    def setUp(self):
        sc._desactivar_sandbox()
        self.dir_tmp = tempfile.mkdtemp()

    def tearDown(self):
        sc._desactivar_sandbox()

    def test_sin_sandbox_devuelve_comando_intacto(self):
        self.assertEqual(sc._envolver_sandbox("pytest", "."), "pytest")

    def test_envuelve_con_mount_y_workdir(self):
        sc._SANDBOX_ACTIVO = True
        sc._SANDBOX_IMAGEN = "python:3.11-slim"
        sc._SANDBOX_COMANDO_PREP = None
        envuelto = sc._envolver_sandbox("pytest -q", self.dir_tmp)
        self.assertIn("docker run --rm", envuelto)
        self.assertIn("-w /workspace", envuelto)
        self.assertIn("python:3.11-slim", envuelto)
        self.assertIn("pytest -q", envuelto)

    def test_comando_prep_antepuesto(self):
        sc._SANDBOX_ACTIVO = True
        sc._SANDBOX_IMAGEN = "python:3.11-slim"
        sc._SANDBOX_COMANDO_PREP = "pip install pytest"
        envuelto = sc._envolver_sandbox("pytest", self.dir_tmp)
        self.assertIn("pip install pytest && (pytest)", envuelto)


class TestEjecucionEnSandbox(unittest.TestCase):
    def setUp(self):
        sc._desactivar_sandbox()

    def tearDown(self):
        sc._desactivar_sandbox()

    def _activar(self):
        sc._SANDBOX_ACTIVO = True
        sc._SANDBOX_IMAGEN = "python:3.11-slim"
        sc._SANDBOX_COMANDO_PREP = None

    def test_ejecutar_comando_usa_docker_run(self):
        self._activar()
        with mock.patch.object(sc.subprocess, "run") as fake_run:
            fake_run.return_value = mock.Mock(returncode=0, stdout="hola",
                                              stderr="")
            codigo, stdout, _ = sc._ejecutar_comando("echo hola", ".")
            self.assertEqual(codigo, 0)
            self.assertIn("hola", stdout)
            comando_lanzado = fake_run.call_args[0][0]
            self.assertIn("docker run --rm", comando_lanzado)
            self.assertIn("echo hola", comando_lanzado)

    def test_ejecutar_comando_error_se_propaga_igual(self):
        self._activar()
        with mock.patch.object(sc.subprocess, "run") as fake_run:
            fake_run.return_value = mock.Mock(returncode=5, stdout="",
                                              stderr="boom")
            codigo, _, stderr = sc._ejecutar_comando("exit 5", ".")
        self.assertEqual(codigo, 5)
        self.assertIn("boom", stderr)

    def test_ejecutar_pruebas_argv_en_sandbox(self):
        self._activar()
        with mock.patch.object(sc.subprocess, "run") as fake_run:
            fake_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            codigo, _, _ = sc._ejecutar_pruebas_argv(["pytest", "-q"], ".")
            self.assertEqual(codigo, 0)
            self.assertIn("pytest -q", fake_run.call_args[0][0])

    def test_mcp_execute_command_en_sandbox(self):
        self._activar()
        with mock.patch.object(sc.subprocess, "run") as fake_run:
            fake_run.return_value = mock.Mock(returncode=0, stdout="ok",
                                              stderr="")
            res = sc._tool_execute_command("echo ok", ".")
            self.assertTrue(res["ok"])
            self.assertIn("docker run --rm", fake_run.call_args[0][0])

    def test_herramientas_lectura_no_usan_docker(self):
        # grep es de solo lectura: no pasa por docker (usa su propia función).
        self._activar()
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "a.py").write_text("def hola():\n    pass\n",
                                        encoding="utf-8")
        with mock.patch.object(sc.subprocess, "run") as fake_run:
            fake_run.return_value = mock.Mock(returncode=0, stdout="a.py:1:x",
                                              stderr="")
            res = sc._tool_grep("hola", tmp)
            # Solo lectura → se ejecuta en el host, SIN envolver en docker.
            lanzado = fake_run.call_args[0][0]
            self.assertNotIn("docker", str(lanzado))
        self.assertTrue(res["ok"])


class TestIntegracionPlanYBucle(unittest.TestCase):
    def setUp(self):
        sc._desactivar_sandbox()
        self.dir_tmp = tempfile.mkdtemp()
        # Comando real no destructivo según plataforma.
        self.comando = ("cmd /c echo hola"
                        if os.name == "nt" else "echo hola")

    def tearDown(self):
        sc._desactivar_sandbox()

    def _activar_mock_docker(self):
        sc._SANDBOX_ACTIVO = True
        sc._SANDBOX_IMAGEN = "python:3.11-slim"
        sc._SANDBOX_COMANDO_PREP = None

    def test_paso_plan_ejecutar_en_sandbox(self):
        self._activar_mock_docker()
        with mock.patch.object(sc, "_confirmar_accion", return_value=True), \
                mock.patch.object(sc.subprocess, "run") as fake_run:
            fake_run.return_value = mock.Mock(returncode=0, stdout="hola",
                                              stderr="")
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "ejecutar", "descripcion": "paso",
                 "comando": self.comando},
                _args_base(), self.dir_tmp)
        self.assertTrue(ok)
        self.assertIn("docker run --rm", fake_run.call_args[0][0])

    def test_bucle_test_saltara_check_host_en_sandbox(self):
        # Con sandbox activo NO se exige el binario en el PATH del host.
        self._activar_mock_docker()
        with mock.patch.object(sc.shutil, "which",
                               return_value=None) as fake_which, \
                mock.patch.object(sc, "ejecutar_aider"), \
                mock.patch.object(sc, "_ejecutar_pruebas_argv",
                                  return_value=(0, "ok", "")):
            self.assertTrue(sc.ejecutar_bucle_test(
                "tarea", ["a.py"], ".", "", ["binario-inexistente"], 1))
            fake_which.assert_not_called()

    def test_agente_tester_en_sandbox(self):
        from agentes import AgenteTester
        self._activar_mock_docker()
        with mock.patch.object(sc.subprocess, "run") as fake_run:
            fake_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            res = AgenteTester().ejecutar_pruebas(["pytest"], ".")
            self.assertEqual(res.returncode, 0)
            self.assertIn("docker run --rm", fake_run.call_args[0][0])


if __name__ == "__main__":
    unittest.main()

