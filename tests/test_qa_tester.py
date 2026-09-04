#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para QA Tester adversarial (v6.25.0)."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestQATesterLogic(unittest.TestCase):
    """Tests para qa_tester_logic.py."""

    def test_severidad_minima_mapping(self):
        from qa_tester_logic import SEVERIDAD_MINIMA
        self.assertEqual(SEVERIDAD_MINIMA["baja"], 0)
        self.assertEqual(SEVERIDAD_MINIMA["media"], 1)
        self.assertEqual(SEVERIDAD_MINIMA["alta"], 2)

    def test_detectar_lenguaje_python(self):
        from qa_tester_logic import _detectar_lenguaje
        self.assertEqual(_detectar_lenguaje("app.py"), "python")

    def test_detectar_lenguaje_javascript(self):
        from qa_tester_logic import _detectar_lenguaje
        self.assertEqual(_detectar_lenguaje("index.js"), "javascript")

    def test_detectar_lenguaje_typescript(self):
        from qa_tester_logic import _detectar_lenguaje
        self.assertEqual(_detectar_lenguaje("main.ts"), "typescript")

    def test_detectar_lenguaje_java(self):
        from qa_tester_logic import _detectar_lenguaje
        self.assertEqual(_detectar_lenguaje("App.java"), "java")

    def test_detectar_lenguaje_desconocido(self):
        from qa_tester_logic import _detectar_lenguaje
        self.assertEqual(_detectar_lenguaje("archivo.xyz"), "python")

    def test_extraer_json_valido(self):
        from qa_tester_logic import _extraer_json
        texto = '{"aprobado": true, "hallazgos": []}'
        resultado = _extraer_json(texto)
        self.assertIsNotNone(resultado)
        self.assertTrue(resultado["aprobado"])

    def test_extraer_json_con_markdown(self):
        from qa_tester_logic import _extraer_json
        texto = '```json\n{"aprobado": false}\n```'
        resultado = _extraer_json(texto)
        self.assertIsNotNone(resultado)
        self.assertFalse(resultado["aprobado"])

    def test_extraer_json_vacio(self):
        from qa_tester_logic import _extraer_json
        self.assertIsNone(_extraer_json(""))
        self.assertIsNone(_extraer_json(None))

    def test_extraer_json_invalido(self):
        from qa_tester_logic import _extraer_json
        self.assertIsNone(_extraer_json("no es json"))

    def test_aplicar_correcciones_vacio(self):
        from qa_tester_logic import aplicar_correcciones
        codigo = "x = 1"
        resultado = aplicar_correcciones(codigo, [])
        self.assertEqual(resultado, codigo)

    def test_aplicar_correcciones_con_flecha(self):
        from qa_tester_logic import aplicar_correcciones
        codigo = "x = 1\ny = 2"
        sugerencias = ["x = 1 -> x = 10"]
        resultado = aplicar_correcciones(codigo, sugerencias)
        self.assertIn("x = 10", resultado)

    def test_aplicar_correcciones_sin_flecha(self):
        from qa_tester_logic import aplicar_correcciones
        codigo = "x = 1"
        sugerencias = ["Usar constante en vez de magic number"]
        resultado = aplicar_correcciones(codigo, sugerencias)
        self.assertIn("[QA Tester sugerencia 1]", resultado)

    def test_qa_tester_class_init(self):
        from qa_tester_logic import QA_Tester
        qa = QA_Tester()
        self.assertEqual(qa.proveedor, "gemini")
        self.assertEqual(qa.severidad, "media")
        self.assertEqual(qa.max_iteraciones, 2)

    def test_qa_tester_class_custom(self):
        from qa_tester_logic import QA_Tester
        qa = QA_Tester(proveedor="ollama", severidad="alta", max_iteraciones=5)
        self.assertEqual(qa.proveedor, "ollama")
        self.assertEqual(qa.severidad, "alta")
        self.assertEqual(qa.max_iteraciones, 5)


class TestQATesterIntegration(unittest.TestCase):
    """Tests de integracion con sub_agent y multi_agent."""

    def test_qa_tester_en_registro(self):
        from sub_agent import ROLES
        self.assertIn("qa_tester", ROLES)

    def test_qa_tester_prompt_definido(self):
        from sub_agent_prompts import PROMPTS
        self.assertIn("qa_tester", PROMPTS)
        self.assertIn("ADVERSARIAL", PROMPTS["qa_tester"])

    def test_qa_tester_en_roles_defecto(self):
        from sub_agent_prompts import ROLES_DEFECTO
        self.assertIn("qa_tester", ROLES_DEFECTO)

    def test_supervisor_acepta_qa_params(self):
        import inspect
        from multi_agent import Supervisor
        sig = inspect.signature(Supervisor.__init__)
        params = list(sig.parameters.keys())
        self.assertIn("qa_tester_activo", params)
        self.assertIn("qa_iteraciones_max", params)
        self.assertIn("qa_severidad", params)


class TestQATesterFlags(unittest.TestCase):
    """Tests para flags CLI de QA Tester."""

    def test_flag_qa_tester_existe(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["test"])
        self.assertTrue(hasattr(args, "qa_tester"))
        self.assertTrue(args.qa_tester)

    def test_flag_no_qa_tester(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--no-qa-tester", "test"])
        self.assertFalse(args.qa_tester)

    def test_flag_qa_iteraciones(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--qa-iteraciones", "5", "test"])
        self.assertEqual(args.qa_iteraciones, 5)

    def test_flag_qa_severidad(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--qa-severidad", "alta", "test"])
        self.assertEqual(args.qa_severidad, "alta")


if __name__ == "__main__":
    unittest.main()
