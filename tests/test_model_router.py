#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para model_router (v6.24.0) — orquestación inteligente de modelos."""

import os
import sys
import unittest
from unittest import mock

import model_router as mr


class TestClasificarTarea(unittest.TestCase):
    """Clasificación de tareas por heurísticas rápidas."""

    def test_indexacion_keyword(self):
        self.assertEqual(mr.clasificar_tarea("indexa el proyecto"), "indexacion")

    def test_busqueda_semantica_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("busca archivos similares a login.py"),
            "busqueda_semantica",
        )

    def test_edicion_critica_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("arregla el botón de pago"),
            "edicion_critica",
        )

    def test_planificacion_simple_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("planifica los pasos para el deploy"),
            "planificacion_simple",
        )

    def test_razonamiento_complejo_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("analiza la arquitectura del sistema"),
            "razonamiento_complejo",
        )

    def test_chat_general_corta(self):
        self.assertEqual(mr.clasificar_tarea("hola"), "chat_general")

    def test_chat_general_vacia(self):
        self.assertEqual(mr.clasificar_tarea(""), "chat_general")

    def test_chat_general_none(self):
        self.assertEqual(mr.clasificar_tarea(None), "chat_general")

    def test_accion_explicita_indexar(self):
        self.assertEqual(
            mr.clasificar_tarea("foo", contexto={"accion": "indexar"}),
            "indexacion",
        )

    def test_accion_explicita_edicion(self):
        self.assertEqual(
            mr.clasificar_tarea("foo", contexto={"accion": "editar"}),
            "edicion_critica",
        )

    def test_archivos_a_editar(self):
        self.assertEqual(
            mr.clasificar_tarea(
                "procesa esto",
                contexto={"archivos": ["a.py", "b.py"]},
            ),
            "edicion_critica",
        )

    def test_consulta_larga_razonamiento(self):
        consulta = " ".join(["palabra"] * 70)
        self.assertEqual(mr.clasificar_tarea(consulta), "razonamiento_complejo")


class TestSeleccionarModelo(unittest.TestCase):
    """Selección de modelo según configuración."""

    def test_sin_config_default(self):
        prov, mod = mr.seleccionar_modelo("indexacion", {})
        self.assertIsNone(prov)
        self.assertIsNone(mod)

    def test_con_config_ollama(self):
        config = {"model_routing": {"indexacion": {"provider": "ollama", "model": "qwen3.5:9b"}}}
        prov, mod = mr.seleccionar_modelo("indexacion", config)
        self.assertEqual(prov, "ollama")
        self.assertEqual(mod, "qwen3.5:9b")

    def test_con_config_claude(self):
        config = {
            "model_routing": {
                "razonamiento_complejo": {"provider": "anthropic", "model": "claude-3.7-sonnet"}
            }
        }
        prov, mod = mr.seleccionar_modelo("razonamiento_complejo", config)
        self.assertEqual(prov, "anthropic")
        self.assertEqual(mod, "claude-3.7-sonnet")

    def test_categoria_sin_entrada(self):
        config = {"model_routing": {"indexacion": {"provider": "ollama", "model": "qwen3.5:9b"}}}
        prov, mod = mr.seleccionar_modelo("chat_general", config)
        self.assertIsNone(prov)
        self.assertIsNone(mod)

    def test_config_none(self):
        prov, mod = mr.seleccionar_modelo("edicion_critica", None)
        self.assertIsNone(prov)
        self.assertIsNone(mod)


class TestEnrutarTarea(unittest.TestCase):
    """Combinación clasificación + selección."""

    def test_enrutado_con_config(self):
        config = {"model_routing": {"edicion_critica": {"provider": "gemini", "model": "gemini-2.5-pro"}}}
        resultado = mr.enrutar_tarea("arregla el botón", config=config)
        self.assertEqual(resultado["categoria"], "edicion_critica")
        self.assertEqual(resultado["provider"], "gemini")
        self.assertEqual(resultado["model"], "gemini-2.5-pro")
        self.assertTrue(resultado["enrutado"])

    def test_no_enrutado_sin_config(self):
        resultado = mr.enrutar_tarea("arregla el botón", config={})
        self.assertEqual(resultado["categoria"], "edicion_critica")
        self.assertIsNone(resultado["provider"])
        self.assertFalse(resultado["enrutado"])


class TestIntegracionSnapcontext(unittest.TestCase):
    """Integración con snapcontext.py."""

    def test_existe_flag_model_routing(self):
        """Verifica que el flag --model-routing existe en snapcontext."""
        import snapcontext as sc
        self.assertTrue(hasattr(sc, "_MODEL_ROUTING_ACTIVO"))

    def test_existe_configurar_model_routing(self):
        import snapcontext as sc
        self.assertTrue(callable(getattr(sc, "_configurar_model_routing", None)))

    def test_existe_cargar_configuracion_routing(self):
        import snapcontext as sc
        self.assertTrue(callable(getattr(sc, "_cargar_configuracion_routing", None)))


if __name__ == "__main__":
    unittest.main()
