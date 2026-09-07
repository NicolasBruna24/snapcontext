#!/usr/bin/env python3
"""Tests del helper de ejecución segura de comandos (v6.34.2).

Cubre ``sandbox_utils.ejecutar_comando_seguro``,
``sandbox_utils.ejecutar_comando_con_politica`` y
``sandbox_utils.tiene_metacaracteres_shell``.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from unittest import mock

import sandbox_utils


class TestEjecutarComandoSeguro(unittest.TestCase):
    """Ejecución con ``shell=False`` (lista de argumentos obligatoria)."""

    def test_comando_simple_funciona(self):
        proc = sandbox_utils.ejecutar_comando_seguro([sys.executable, "-c", "print('hola')"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("hola", proc.stdout)

    def test_rechaza_string_en_lugar_de_lista(self):
        with self.assertRaises(ValueError):
            sandbox_utils.ejecutar_comando_seguro("echo hola")

    def test_rechaza_bytes(self):
        with self.assertRaises(ValueError):
            sandbox_utils.ejecutar_comando_seguro(b"echo hola")

    def test_rechaza_lista_vacia(self):
        with self.assertRaises(ValueError):
            sandbox_utils.ejecutar_comando_seguro([])

    def test_usa_shell_false(self):
        with mock.patch.object(
            sandbox_utils.subprocess, "run", return_value=mock.Mock(returncode=0)
        ) as run:
            sandbox_utils.ejecutar_comando_seguro(["git", "status"])
        run.assert_called_once()
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.args[0], ["git", "status"])

    def test_timeout_lanza_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            sandbox_utils.ejecutar_comando_seguro(
                [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2
            )


class TestTieneMetacaracteresShell(unittest.TestCase):
    """Detección de sintaxis de shell no expresable por lista."""

    def test_casos(self):
        for cmd, esperado in [
            ("pytest -q", False),
            ("git status", False),
            ("npm test", False),
            ("echo a | cat", True),
            ("ls > out.txt", True),
            ("a && b", True),
            ("a || b", True),
            ("find . -name '*.py'", True),  # glob
            ("echo $(pwd)", True),  # sustitución de comandos
            (None, False),
            ("", False),
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(sandbox_utils.tiene_metacaracteres_shell(cmd), esperado)


class TestEjecutarComandoConPolitica(unittest.TestCase):
    """Elige la vía más segura: split+shell=False o shell=True validado."""

    def test_simple_divide_y_ejecuta_seguro(self):
        with mock.patch.object(
            sandbox_utils, "ejecutar_comando_seguro", return_value=mock.Mock(returncode=0)
        ) as seguro:
            proc = sandbox_utils.ejecutar_comando_con_politica("pytest -q")
        seguro.assert_called_once()
        argv = seguro.call_args.args[0]
        self.assertEqual(argv, ["pytest", "-q"])
        self.assertEqual(proc.returncode, 0)

    def test_vacio_lanza_value_error(self):
        with self.assertRaises(ValueError):
            sandbox_utils.ejecutar_comando_con_politica("   ")

    def test_con_pipe_usa_shell_true(self):
        with (
            mock.patch.object(sandbox_utils, "es_comando_peligroso", return_value=False),
            mock.patch.object(
                sandbox_utils.subprocess, "run", return_value=mock.Mock(returncode=0)
            ) as run,
        ):
            sandbox_utils.ejecutar_comando_con_politica("echo a | cat")
        run.assert_called_once()
        self.assertTrue(run.call_args.kwargs["shell"])

    def test_peligroso_sin_confirmacion_lanza_runtime_error(self):
        with mock.patch.object(sandbox_utils, "es_comando_peligroso", return_value=True):
            with self.assertRaises(RuntimeError):
                sandbox_utils.ejecutar_comando_con_politica("rm -rf / | cat")

    def test_peligroso_rechazado_por_confirmacion_lanza(self):
        with mock.patch.object(sandbox_utils, "es_comando_peligroso", return_value=True):
            with self.assertRaises(RuntimeError):
                sandbox_utils.ejecutar_comando_con_politica(
                    "rm -rf / | cat", confirmar=lambda c: False
                )

    def test_peligroso_confirmado_ejecuta(self):
        with (
            mock.patch.object(sandbox_utils, "es_comando_peligroso", return_value=True),
            mock.patch.object(
                sandbox_utils.subprocess, "run", return_value=mock.Mock(returncode=0)
            ) as run,
        ):
            proc = sandbox_utils.ejecutar_comando_con_politica(
                "rm -rf /tmp/x | cat", confirmar=lambda c: True
            )
        run.assert_called_once()
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
