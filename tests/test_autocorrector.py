#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para autocorrector (v6.29.0)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAutocorrectorFunciones(unittest.TestCase):
    """Tests para funciones de autocorrector."""

    def test_detectar_comando_test_pytest(self):
        import tempfile
        from autocorrector import _detectar_comando_test
        with tempfile.TemporaryDirectory() as tmp:
            Path = __import__('pathlib').Path
            Path(tmp, "setup.py").touch()
            cmd = _detectar_comando_test(tmp)
            self.assertIn("pytest", cmd)

    def test_detectar_comando_test_npm(self):
        import tempfile
        from autocorrector import _detectar_comando_test
        with tempfile.TemporaryDirectory() as tmp:
            Path = __import__('pathlib').Path
            Path(tmp, "package.json").touch()
            cmd = _detectar_comando_test(tmp)
            self.assertIn("npm", cmd)

    def test_parseo_basico_error(self):
        from autocorrector import _parseo_basico_error
        salida = 'File "app.py", line 42\nAssertionError: expected 1, got 2'
        resultado = _parseo_basico_error(salida)
        self.assertEqual(resultado["archivo"], "app.py")
        self.assertEqual(resultado["linea"], 42)
        self.assertEqual(resultado["tipo"], "AssertionError")

    def test_parseo_basico_error_vacio(self):
        from autocorrector import _parseo_basico_error
        resultado = _parseo_basico_error("")
        self.assertEqual(resultado["archivo"], "")

    def test_extraer_codigo_con_bloque(self):
        from autocorrector import _extraer_codigo
        respuesta = "```python\nx = 1\ny = 2\n```"
        resultado = _extraer_codigo(respuesta, "original")
        self.assertIn("x = 1", resultado)

    def test_extraer_codigo_sin_bloque(self):
        from autocorrector import _extraer_codigo
        respuesta = "Solo texto sin codigo"
        resultado = _extraer_codigo(respuesta, "original")
        self.assertEqual(resultado, "original")

    def test_analizar_error_vacio(self):
        from autocorrector import analizar_error
        resultado = analizar_error("")
        self.assertEqual(resultado["tipo"], "desconocido")

    def test_analizar_error_contenido(self):
        from autocorrector import analizar_error
        resultado = analizar_error("Some error output")
        self.assertIn("mensaje", resultado)


class TestAutocorrectorClase(unittest.TestCase):
    """Tests para la clase Autocorrector."""

    def test_init(self):
        from autocorrector import Autocorrector
        ac = Autocorrector(directorio=".", max_iteraciones=5)
        self.assertEqual(ac.max_iteraciones, 5)

    def test_ejecutar_exitoso_mock(self):
        from autocorrector import Autocorrector
        ac = Autocorrector(directorio=".", max_iteraciones=3)
        with mock.patch("autocorrector._ejecutar_en_sandbox") as mock_exec:
            mock_exec.return_value = (0, "OK", "")
            resultado = ac.ejecutar("pytest")
            self.assertTrue(resultado["exito"])
            self.assertEqual(resultado["iteraciones"], 1)

    def test_ejecutar_fallido_mock(self):
        from autocorrector import Autocorrector
        ac = Autocorrector(directorio=".", max_iteraciones=2)
        with mock.patch("autocorrector._ejecutar_en_sandbox") as mock_exec:
            mock_exec.return_value = (1, "", "Error")
            resultado = ac.ejecutar("pytest")
            self.assertFalse(resultado["exito"])
            self.assertEqual(resultado["iteraciones"], 2)


class TestAutocorrectorFlags(unittest.TestCase):
    """Tests para flags CLI."""

    def test_flag_autocorregir_existe(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["test"])
        self.assertTrue(hasattr(args, "autocorregir"))
        self.assertTrue(args.autocorregir)

    def test_flag_no_autocorregir(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--no-autocorregir", "test"])
        self.assertFalse(args.autocorregir)

    def test_flag_max_ciclos(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--max-ciclos", "5", "test"])
        self.assertEqual(args.max_ciclos, 5)


if __name__ == "__main__":
    unittest.main()
