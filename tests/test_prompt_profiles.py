# encoding: utf-8
"""Tests de la Fase 17: perfiles de prompt optimizados por modelo."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prompt_profiles as pp


class TestPerfilesDefinidos(unittest.TestCase):
    def test_todos_los_proveedores_tienen_perfil(self):
        esperados = {"claude", "gemini", "openai", "deepseek", "ollama", "xpu", "groq"}
        self.assertTrue(esperados.issubset(pp.PERFILES))

    def test_cada_perfil_tiene_claves_minimas(self):
        for nombre, perfil in pp.PERFILES.items():
            with self.subTest(proveedor=nombre):
                self.assertIn("system_prompt", perfil)
                self.assertIn("user_prompt_template", perfil)
                self.assertIn("config", perfil)
                self.assertTrue(str(perfil["system_prompt"]).strip())

    def test_alias_anthropic_a_claude(self):
        perfil = pp.obtener_perfil("anthropic", "general")
        self.assertIn("razonamiento", perfil["system_prompt"].lower())
        self.assertTrue(perfil["config"]["cache_control"])


class TestPromptsPorProveedor(unittest.TestCase):
    def test_claude_incluye_razonamiento_deep(self):
        perfil = pp.obtener_perfil("claude", "general")
        self.assertIn("razonamiento", perfil["system_prompt"].lower())
        self.assertEqual(perfil["config"]["max_tokens"], 4096)

    def test_gemini_claro_y_directo(self):
        perfil = pp.obtener_perfil("gemini", "general")
        self.assertIn("directa", perfil["system_prompt"].lower())

    def test_ollama_concisa(self):
        perfil = pp.obtener_perfil("ollama", "general")
        self.assertIn("concisa", perfil["system_prompt"].lower())
        self.assertLessEqual(perfil["config"]["max_tokens"], 1024)

    def test_openai_en_ingles(self):
        perfil = pp.obtener_perfil("openai", "general")
        self.assertIn("programming assistant", perfil["system_prompt"].lower())

    def test_groq_directo(self):
        perfil = pp.obtener_perfil("groq", "general")
        self.assertIn("directo", perfil["system_prompt"].lower())


class TestVariantesPorTarea(unittest.TestCase):
    def test_planificacion_usa_variante_de_claude(self):
        perfil = pp.obtener_perfil("anthropic", "planificacion")
        self.assertIn("arquitecto", perfil["system_prompt"].lower())

    def test_tipo_tarea_desconocida_cae_a_general(self):
        perfil = pp.obtener_perfil("anthropic", "tarea_rarisima")
        self.assertIn("razonamiento", perfil["system_prompt"].lower())


class TestFallbackGenerico(unittest.TestCase):
    def test_proveedor_no_registrado_usa_generico(self):
        perfil = pp.obtener_perfil("proveedor-misterioso", "general")
        self.assertIn("asistente de programación experto", perfil["system_prompt"])

    def test_apply_no_rompe_con_proveedor_desconocido(self):
        msgs = [{"role": "user", "content": "hola"}]
        nuevos, config = pp.aplicar_perfil(msgs, "no-existe", "simple")
        # Fallback: se aplica PERFIL_GENERICO (system + plantilla) sin romperse.
        self.assertEqual(nuevos[0]["role"], "system")
        self.assertEqual(nuevos[0]["content"], pp.PERFIL_GENERICO["system_prompt"])
        self.assertIn("hola", nuevos[1]["content"])
        self.assertEqual(
            config, dict(pp.PERFIL_GENERICO["config"])
        )


class TestAplicarPerfil(unittest.TestCase):
    def test_inyecta_system_y_plantilla_de_usuario_en_consulta_unica(self):
        msgs = [{"role": "user", "content": "haz login"}]
        nuevos, _ = pp.aplicar_perfil(msgs, "gemini", "simple", contexto_extra="ctx")
        self.assertEqual(nuevos[0]["role"], "system")
        self.assertIn("asistente", nuevos[0]["content"])
        self.assertIn("Pregunta: haz login", nuevos[1]["content"])

    def test_react_multiturno_no_reformatea_usuario(self):
        msgs = [
            {"role": "system", "content": "sistema react"},
            {"role": "user", "content": "analiza"},
            {"role": "assistant", "content": "pensando"},
            {"role": "user", "content": "tool_result"},
        ]
        nuevos, _ = pp.aplicar_perfil(msgs, "deepseek", "simple")
        self.assertIn("programación experto", nuevos[0]["content"])
        self.assertIn("sistema react", nuevos[0]["content"])
        self.assertEqual(nuevos[-1]["content"], "tool_result")

    def test_plan_no_reformatea_usuario(self):
        msgs = [{"role": "user", "content": '[{"accion": "editar"}]'}]
        nuevos, _ = pp.aplicar_perfil(msgs, "anthropic", "planificacion")
        self.assertEqual(nuevos[1]["content"], '[{"accion": "editar"}]')


class TestConfigPersonalizable(unittest.TestCase):
    def test_merge_perfil_personalizado(self):
        personalizados = {
            "claude": {
                "system_prompt": "PERSONALIZADO claude algo",
                "config": {"temperature": 0.9},
            }
        }
        perfil = pp.obtener_perfil("anthropic", "general", personalizados=personalizados)
        self.assertIn("PERSONALIZADO", perfil["system_prompt"])
        self.assertEqual(perfil["config"]["temperature"], 0.9)

    def test_nuevo_proveedor_via_config(self):
        personalizados = {
            "mistral": {
                "system_prompt": "Soy Mistral",
                "user_prompt_template": "Q: {consulta}",
                "config": {"temperature": 0.5, "max_tokens": 1000, "cache_control": False},
                "variantes": {},
            }
        }
        perfil = pp.obtener_perfil("mistral", "general", personalizados=personalizados)
        self.assertIn("Mistral", perfil["system_prompt"])


if __name__ == "__main__":
    unittest.main()