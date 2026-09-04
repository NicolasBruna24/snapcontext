#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para TUI interactiva (v6.27.0)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFormatoPasos(unittest.TestCase):
    """Tests para funciones de formato de pasos."""

    def test_esquema_pasos_a_texto_vacio(self):
        import tui_interactiva as ti
        resultado = ti.esquema_pasos_a_texto([])
        self.assertEqual(resultado, "")

    def test_esquema_pasos_a_texto_un_paso(self):
        import tui_interactiva as ti
        pasos = [{"descripcion": "Paso 1", "estado": "pendiente"}]
        resultado = ti.esquema_pasos_a_texto(pasos)
        self.assertIn("Paso 1", resultado)
        self.assertIn("○", resultado)  # Icono de pendiente

    def test_esquema_pasos_a_texto_varios_pasos(self):
        import tui_interactiva as ti
        pasos = [
            {"descripcion": "Paso A", "estado": "completado"},
            {"descripcion": "Paso B", "estado": "en_progreso"},
            {"descripcion": "Paso C", "estado": "pendiente"},
        ]
        resultado = ti.esquema_pasos_a_texto(pasos)
        self.assertIn("Paso A", resultado)
        self.assertIn("Paso B", resultado)
        self.assertIn("Paso C", resultado)

    def test_texto_a_esquema_pasos_vacio(self):
        import tui_interactiva as ti
        resultado = ti.texto_a_esquema_pasos("")
        self.assertEqual(resultado, [])

    def test_texto_a_esquema_pasos_una_linea(self):
        import tui_interactiva as ti
        texto = "1. [pendiente] Hacer algo"
        resultado = ti.texto_a_esquema_pasos(texto)
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["descripcion"], "Hacer algo")

    def test_texto_a_esquema_pasos_completado(self):
        import tui_interactiva as ti
        texto = "1. [completado] Tarea hecha"
        resultado = ti.texto_a_esquema_pasos(texto)
        self.assertEqual(resultado[0]["estado"], "completado")

    def test_texto_a_esquema_pasos_en_progreso(self):
        import tui_interactiva as ti
        texto = "1. [en_progreso] Tarea en curso"
        resultado = ti.texto_a_esquema_pasos(texto)
        self.assertEqual(resultado[0]["estado"], "en_progreso")


class TestFormatoGrafo(unittest.TestCase):
    """Tests para funciones de formato del grafo."""

    def test_grafo_a_texto_vacio(self):
        import tui_interactiva as ti
        resultado = ti.grafo_a_texto({})
        self.assertEqual(resultado, "(grafo vacio)")

    def test_grafo_a_texto_con_nodos(self):
        import tui_interactiva as ti
        grafo = {
            "nodos": {
                "archivo.py::funcion_a": {"tipo": "funcion"},
                "archivo.py::ClaseB": {"tipo": "clase"},
            }
        }
        resultado = ti.grafo_a_texto(grafo)
        self.assertIn("archivo.py", resultado)
        self.assertIn("funcion_a", resultado)

    def test_grafo_a_texto_con_aristas(self):
        import tui_interactiva as ti
        grafo = {
            "aristas": [
                {"origen": "a.py::f", "destino": "b.py::g"},
            ]
        }
        resultado = ti.grafo_a_texto(grafo)
        self.assertIn("a.py", resultado)
        self.assertIn("b.py", resultado)

    def test_grafo_a_texto_sin_expandir(self):
        import tui_interactiva as ti
        grafo = {
            "nodos": {
                "archivo.py::funcion_a": {"tipo": "funcion"},
            }
        }
        resultado = ti.grafo_a_texto(grafo, expandir=False)
        self.assertIn("archivo.py", resultado)


class TestValidarPaso(unittest.TestCase):
    """Tests para validacion de pasos."""

    def test_paso_valido(self):
        import tui_interactiva as ti
        valido, error = ti.validar_paso({"descripcion": "Hacer algo"})
        self.assertTrue(valido)
        self.assertEqual(error, "")

    def test_paso_sin_descripcion(self):
        import tui_interactiva as ti
        valido, error = ti.validar_paso({"estado": "pendiente"})
        self.assertFalse(valido)
        self.assertIn("descripcion", error.lower())

    def test_paso_no_es_dict(self):
        import tui_interactiva as ti
        valido, error = ti.validar_paso("no es dict")
        self.assertFalse(valido)

    def test_paso_descripcion_larga(self):
        import tui_interactiva as ti
        valido, error = ti.validar_paso({"descripcion": "x" * 501})
        self.assertFalse(valido)
        self.assertIn("larga", error.lower())


class TestTUIFlags(unittest.TestCase):
    """Tests para flags CLI de TUI interactiva."""

    def test_flag_tui_plan_editor_existe(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--tui", "test"])
        self.assertTrue(hasattr(args, "tui_plan_editor"))
        self.assertTrue(args.tui_plan_editor)

    def test_flag_no_tui_plan_editor(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--tui", "--no-tui-plan-editor", "test"])
        self.assertFalse(args.tui_plan_editor)

    def test_flag_tui_grafo_existe(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--tui", "test"])
        self.assertTrue(hasattr(args, "tui_grafo"))
        self.assertTrue(args.tui_grafo)

    def test_flag_no_tui_grafo(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--tui", "--no-tui-grafo", "test"])
        self.assertFalse(args.tui_grafo)


if __name__ == "__main__":
    unittest.main()
