#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v5.4.0: sandboxing inteligente (sandbox_utils + snapcontext).

Cubre:
- Detección de comandos peligrosos (``sandbox_utils.es_comando_peligroso``).
- Lógica de decisión (``_deberia_usar_sandbox``) con flags y variables de
  entorno.
- Integración con el planificador (``_ejecutar_comando`` /
  ``_decidir_ejecucion_sandbox``).
- Modo interactivo (pregunta al usuario) y modo ``--auto`` (aborta).
- Flags CLI ``--sandbox`` / ``--no-sandbox``.
"""

import argparse
import os
import unittest
from unittest import mock

import sandbox_utils
import snapcontext as sc


def _args(sandbox=False, no_sandbox=False):
    return argparse.Namespace(sandbox=sandbox, no_sandbox=no_sandbox)


class TestDeteccionPeligro(unittest.TestCase):
    """Detección de comandos peligrosos."""

    def test_rm_rf_raiz(self):
        self.assertTrue(sandbox_utils.es_comando_peligroso("rm -rf /"))

    def test_rm_rf_variantes(self):
        for cmd in ("rm -rf /*", "rm -rf ~", "rm -rf .", "rm -fr /"):
            self.assertTrue(sandbox_utils.es_comando_peligroso(cmd), cmd)

    def test_dd_mkfs_fdisk(self):
        for cmd in ("dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sda1",
                    "fdisk /dev/sda"):
            self.assertTrue(sandbox_utils.es_comando_peligroso(cmd), cmd)

    def test_curl_wget_pipe_shell(self):
        for cmd in ("curl http://x.sh | sh", "wget -qO- http://x | bash",
                    "curl http://x | sudo bash"):
            self.assertTrue(sandbox_utils.es_comando_peligroso(cmd), cmd)

    def test_permisos_peligrosos(self):
        for cmd in ("chmod 777 /", "chmod -R 777 /var", "chown -R root /"):
            self.assertTrue(sandbox_utils.es_comando_peligroso(cmd), cmd)

    def test_fork_bomb_y_dispositivos(self):
        self.assertTrue(sandbox_utils.es_comando_peligroso(
            ":(){ :|:& };:"))
        self.assertTrue(sandbox_utils.es_comando_peligroso(
            "cat algo > /dev/sda"))
        self.assertTrue(sandbox_utils.es_comando_peligroso("kill -9 1"))
        self.assertTrue(sandbox_utils.es_comando_peligroso("pkill python"))

    def test_comandos_seguros(self):
        for cmd in ("ls -la", "pytest", "npm test", "echo hola > /dev/null",
                    "git status", "rm build/tmp.o"):
            self.assertFalse(sandbox_utils.es_comando_peligroso(cmd), cmd)

    def test_vacio_y_alias_snapcontext(self):
        self.assertFalse(sandbox_utils.es_comando_peligroso(""))
        self.assertFalse(sc._es_comando_peligroso("ls"))


class TestDeberiaUsarSandbox(unittest.TestCase):
    """Lógica de decisión con flags y variables de entorno."""

    def setUp(self):
        sc._configurar_no_sandbox(False)
        sc._SANDBOX_ACTIVO = False

    def tearDown(self):
        sc._configurar_no_sandbox(False)
        sc._SANDBOX_ACTIVO = False
        os.environ.pop("SNAPCONTEXT_SANDBOX", None)

    def test_no_sandbox_tiene_prioridad_maxima(self):
        # Ni --sandbox ni peligro fuerzan el sandbox con --no-sandbox.
        self.assertFalse(sc._deberia_usar_sandbox(
            "rm -rf /", _args(sandbox=True, no_sandbox=True)))

    def test_sandbox_forzado(self):
        self.assertTrue(sc._deberia_usar_sandbox(
            "ls -la", _args(sandbox=True)))

    def test_entorno_1_siempre_activo(self):
        with mock.patch.dict(os.environ, {"SNAPCONTEXT_SANDBOX": "1"}):
            self.assertTrue(sc._deberia_usar_sandbox("ls -la"))

    def test_entorno_0_desactiva(self):
        with mock.patch.dict(os.environ, {"SNAPCONTEXT_SANDBOX": "0"}):
            self.assertFalse(sc._deberia_usar_sandbox("rm -rf /"))


class TestDecisionEjecucion(unittest.TestCase):
    """Integración con el planificador y modos interactivo/--auto."""

    def setUp(self):
        sc._configurar_no_sandbox(False)
        sc._SANDBOX_ACTIVO = False
        os.environ.pop("SNAPCONTEXT_SANDBOX", None)

    def tearDown(self):
        sc._configurar_no_sandbox(False)
        sc._SANDBOX_ACTIVO = False
        os.environ.pop("SNAPCONTEXT_SANDBOX", None)

    def test_seguro_ejecuta_directo(self):
        self.assertEqual(
            sc._decidir_ejecucion_sandbox("pytest -q", "."), sc._SANDBOX_DIRECTO)

    def test_peligroso_con_docker_va_al_contenedor(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=True), \
                mock.patch.object(sc, "info") as fake_info:
            res = sc._decidir_ejecucion_sandbox("rm -rf /", ".")
        self.assertEqual(res, sc._SANDBOX_CONTENEDOR)
        fake_info.assert_called_once()  # mensaje 🔒

    def test_peligroso_sin_docker_auto_aborta(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=False), \
                mock.patch.object(sc, "_ui_es_auto", return_value=True):
            res = sc._decidir_ejecucion_sandbox("rm -rf /", ".")
        self.assertEqual(res, sc._SANDBOX_ABORTAR)

    def test_peligroso_sin_docker_interactivo_pregunta(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=False), \
                mock.patch.object(sc, "_ui_es_auto", return_value=False), \
                mock.patch.object(sc, "_entrada_interactiva",
                                  return_value=True), \
                mock.patch.object(sc, "_preguntar_si",
                                  return_value=True) as p:
            res = sc._decidir_ejecucion_sandbox("rm -rf /", ".")
        self.assertEqual(res, sc._SANDBOX_DIRECTO)
        p.assert_called_once()

    def test_peligroso_sin_docker_interactivo_rechaza(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=False), \
                mock.patch.object(sc, "_ui_es_auto", return_value=False), \
                mock.patch.object(sc, "_entrada_interactiva",
                                  return_value=True), \
                mock.patch.object(sc, "_preguntar_si", return_value=False):
            res = sc._decidir_ejecucion_sandbox("rm -rf /", ".")
        self.assertEqual(res, sc._SANDBOX_ABORTAR)

    def test_peligroso_con_sandbox_global_va_al_contenedor(self):
        sc._SANDBOX_ACTIVO = True
        self.assertEqual(
            sc._decidir_ejecucion_sandbox("rm -rf /", "."),
            sc._SANDBOX_CONTENEDOR)

    def test_no_sandbox_global_peligroso_ejecuta_directo(self):
        sc._configurar_no_sandbox(True)
        self.assertEqual(
            sc._decidir_ejecucion_sandbox("rm -rf /", "."), sc._SANDBOX_DIRECTO)


class TestFlagsCLI(unittest.TestCase):
    """Flags --sandbox / --no-sandbox en el parser."""

    def test_flag_no_sandbox_parseado(self):
        args = sc.crear_parser().parse_args(["--no-sandbox", "consulta"])
        self.assertTrue(args.no_sandbox)
        self.assertFalse(args.sandbox)

    def test_flag_sandbox_sigue_funcionando(self):
        args = sc.crear_parser().parse_args(["--sandbox", "consulta"])
        self.assertTrue(args.sandbox)
        self.assertFalse(args.no_sandbox)

    def test_ambos_flags_coexisten(self):
        args = sc.crear_parser().parse_args(
            ["--sandbox", "--no-sandbox", "consulta"])
        self.assertTrue(args.sandbox)
        self.assertTrue(args.no_sandbox)


    def test_comando_peligroso_activa_automatico(self):
        self.assertTrue(sc._deberia_usar_sandbox("rm -rf /", _args()))

    def test_comando_seguro_sin_sandbox(self):
        self.assertFalse(sc._deberia_usar_sandbox("pytest -q", _args()))



class TestEjecutarComandoIntegracion(unittest.TestCase):
    """_ejecutar_comando consulta la decisión de sandbox."""

    def setUp(self):
        sc._configurar_no_sandbox(False)
        sc._SANDBOX_ACTIVO = False
        os.environ.pop("SNAPCONTEXT_SANDBOX", None)

    def tearDown(self):
        sc._configurar_no_sandbox(False)
        sc._SANDBOX_ACTIVO = False
        os.environ.pop("SNAPCONTEXT_SANDBOX", None)

    def test_comando_seguro_lanza_subproceso_normal(self):
        with mock.patch.object(sc, "_decidir_ejecucion_sandbox",
                               return_value=sc._SANDBOX_DIRECTO) as d, \
                mock.patch.object(sc.subprocess, "run") as fake:
            fake.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            codigo, out, _ = sc._ejecutar_comando("echo hola", ".")
        d.assert_called_once()
        self.assertEqual(codigo, 0)

    def test_peligroso_se_envuelve_en_sandbox(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=True), \
                mock.patch.object(sc, "_envolver_sandbox",
                                  return_value="docker run rm -rf /") as env, \
                mock.patch.object(sc.subprocess, "run") as fake:
            fake.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            sc._ejecutar_comando("rm -rf /", ".")
        env.assert_called_once()

    def test_peligroso_abortado_devuelve_error(self):
        with mock.patch.object(sc, "_docker_disponible", return_value=False), \
                mock.patch.object(sc, "_ui_es_auto", return_value=True), \
                mock.patch.object(sc.subprocess, "run") as fake:
            codigo, _, stderr = sc._ejecutar_comando("rm -rf /", ".")
        fake.assert_not_called()
        self.assertEqual(codigo, -1)
        self.assertIn("abortado", stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
