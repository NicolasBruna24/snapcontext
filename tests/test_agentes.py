#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de los agentes de SnapContext (agentes.py).

Se ejecuta con ``python -m pytest tests`` o ``python -m unittest
tests.test_agentes -v``. No dependen de Gemini/Aider reales: las funciones de
``snapcontext`` se reemplazan con mocks.
"""

import subprocess
import sys
import unittest
from unittest import mock

from agentes import AgenteContexto, AgenteEditor, AgenteTester


class _AgenteBase(unittest.TestCase):
    """Evita parones de import: snapcontext necesita cargarse antes de parchear."""

    @classmethod
    def setUpClass(cls):
        import snapcontext  # noqa: F401  (asegura carga única)


class TestAgenteContexto(_AgenteBase):
    def test_escanear_candidatos_delega(self):
        agente = AgenteContexto()
        with mock.patch(
            "snapcontext.escanear_repositorio", return_value=["a.dart", "b.dart"]
        ) as escanear:
            resultado = agente.escanear_candidatos(
                "consulta", "dir", ["lib"], max_candidatos=10
            )
        self.assertEqual(resultado, ["a.dart", "b.dart"])
        escanear.assert_called_once_with(
            "consulta", directorio="dir", carpetas=["lib"], max_candidatos=10
        )

    def test_seleccionar_archivos_sin_candidatos(self):
        agente = AgenteContexto()
        with mock.patch("snapcontext.escanear_repositorio", return_value=[]):
            self.assertEqual(
                agente.seleccionar_archivos(
                    "q", "dir", ["lib"], 2, provider="gemini", modelo="m"
                ),
                [],
            )

    def test_seleccionar_archivos_delega(self):
        agente = AgenteContexto()
        with mock.patch("snapcontext.escanear_repositorio", return_value=["a.dart"]), \
             mock.patch("snapcontext.seleccionar_archivos", return_value=["a.dart"]) as sel:
            resultado = agente.seleccionar_archivos(
                "consulta", "dir", ["lib"], 1, provider="groq", modelo="m"
            )
        self.assertEqual(resultado, ["a.dart"])
        sel.assert_called_once_with(
            "consulta", ["a.dart"], proveedor="groq", modelo="m", max_archivos=1
        )


class TestAgenteEditor(_AgenteBase):
    def test_ejecutar_aider_delega(self):
        agente = AgenteEditor()
        with mock.patch("snapcontext.ejecutar_aider", return_value=True) as aider:
            ok = agente.ejecutar_aider(["a.dart"], "cambia X", "dir", "--auto")
        self.assertTrue(ok)
        aider.assert_called_once_with(["a.dart"], "cambia X", "dir", "--auto")


class TestAgenteTester(_AgenteBase):
    def test_ejecutar_pruebas_retorna_objeto(self):
        agente = AgenteTester()
        resultado = agente.ejecutar_pruebas(  # comando real y no destructivo
            [sys.executable, "-c", "import sys; sys.exit(3)"], "."
        )
        self.assertEqual(resultado.returncode, 3)
        self.assertIsInstance(resultado, subprocess.CompletedProcess)

    def test_analizar_error_string_limpia_ansi(self):
        agente = AgenteTester()
        limpio = agente.analizar_error("\x1b[31mFALLO\x1b[0m en a.dart")
        self.assertIn("FALLO en a.dart", limpio)
        self.assertNotIn("\x1b[", limpio)

    def test_analizar_error_usar_proceso(self):
        agente = AgenteTester()
        proc = subprocess.CompletedProcess(["x"], 1, "\x1b[31mboom\x1b[0m", "detalle")
        resultado = agente.analizar_error(proc)
        self.assertIn("boom", resultado)
        self.assertIn("detalle", resultado)

    def test_analizar_error_recorta(self):
        agente = AgenteTester()
        import snapcontext as sc
        limite = getattr(sc, "MAX_ERROR_SALIDA", 4000)
        largo = "A" * (limite + 500)
        resultado = agente.analizar_error(largo)
        self.assertIn("salida recortada", resultado)
        self.assertLessEqual(len(resultado), limite + 40)


if __name__ == "__main__":
    unittest.main()