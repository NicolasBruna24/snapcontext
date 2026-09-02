#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v6.12.0: Prompt Caching.

Cubre:
  - Detección de proveedores que soportan caching (Anthropic, DeepSeek).
  - Inserción de marcas `cache_control` en sistema / herramientas MCP / CLAUDE.md.
  - Compatibilidad con proveedores que NO soportan caching (Gemini, Groq, Ollama).
  - Resolución del estado (flag --prompt-caching / entorno / config.json).
  - Integración en `_enviar_al_proveedor` (DeepSeek/Anthropic sí, Groq no).
  - Mensajes de sesión (`🧠 Prompt Caching activado/no soportado`).
  - Resumen automático de ReAct preservando las marcas.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc

_EPHEMERAL = {"type": "ephemeral"}


class _FakeOpenAI:
    """Sustituye al módulo `openai` (Groq/DeepSeek/Ollama)."""

    def __init__(self):
        self.captured = None

    def OpenAI(self, **kwargs):
        self.kwargs = kwargs
        # Encadenamiento `cliente.chat.completions.create(...)`
        self.chat = self
        self.messages = self
        self.completions = self
        return self

    def create(self, **kwargs):
        self.captured = kwargs
        msg = type("X", (), {"content": "respuesta"})()
        choice = type("X", (), {"message": msg})()
        resp = type("X", (), {"choices": [choice]})()
        return resp


class _FakeAnthropic:
    """Sustituye al módulo `anthropic`."""

    def __init__(self):
        self.captured = None

    def Anthropic(self, **kwargs):
        self.kwargs = kwargs
        # Encadenamiento `cliente.messages.create(...)`
        self.messages = self
        return self

    def create(self, **kwargs):
        self.captured = kwargs
        bloque = type("X", (), {"type": "text", "text": "respuesta"})()
        resp = type("X", (), {"content": [bloque]})()
        return resp


class _FakeGenai:
    def __init__(self):
        self.captured = None

    def configure(self, **kw):
        pass

    def GenerativeModel(self, **kw):
        return self

    def generate_content(self, contenidos):
        self.captured = contenidos
        return type("X", (), {"text": "respuesta"})()


# 1) Detección de proveedores que soportan caching
class TestDeteccionSoporte(unittest.TestCase):
    def test_anthropic_soporta_caching(self):
        self.assertTrue(sc._soporta_prompt_caching("anthropic"))

    def test_deepseek_soporta_caching(self):
        self.assertTrue(sc._soporta_prompt_caching("deepseek"))

    def test_gemini_no_soporta_caching(self):
        self.assertFalse(sc._soporta_prompt_caching("gemini"))

    def test_groq_no_soporta_caching(self):
        self.assertFalse(sc._soporta_prompt_caching("groq"))

    def test_ollama_no_soporta_caching(self):
        self.assertFalse(sc._soporta_prompt_caching("ollama"))

    def test_proveedor_desconocido_no_soporta(self):
        self.assertFalse(sc._soporta_prompt_caching("proveedor-fantasma"))


# 2) Inserción de marcas cache_control
class TestAplicarCacheControl(unittest.TestCase):
    def test_primera_sin_role_se_marca_como_sistema(self):
        msgs = [{"role": "user", "content": "hola"}]
        out = sc._aplicar_cache_control(msgs)
        self.assertEqual(out[0]["cache_control"], _EPHEMERAL)

    def test_mensaje_system_se_marca(self):
        out = sc._aplicar_cache_control(
            [{"role": "system", "content": "Eres un agente"}])
        self.assertEqual(out[0]["cache_control"], _EPHEMERAL)

    def test_mensaje_con_herramientas_mcp_se_marca(self):
        msgs = [
            {"role": "system", "content": "sis"},
            {"role": "user", "content": "Usa estas HERRAMIENTAS: editar_archivo"},
        ]
        out = sc._aplicar_cache_control(msgs)
        self.assertEqual(out[1]["cache_control"], _EPHEMERAL)

    def test_mensaje_con_claude_md_se_marca(self):
        msgs = [
            {"role": "system", "content": "sis"},
            {"role": "user", "content": "Memoria del proyecto (CLAUDE.md):"},
            {"role": "user", "content": "tarea normal"},
        ]
        out = sc._aplicar_cache_control(msgs)
        self.assertEqual(out[1]["cache_control"], _EPHEMERAL)

    def test_mensaje_normal_no_recibe_marca(self):
        msgs = [
            {"role": "system", "content": "sis"},
            {"role": "user", "content": "explica el código de login"},
        ]
        out = sc._aplicar_cache_control(msgs)
        self.assertNotIn("cache_control", out[1])

    def test_no_muta_la_lista_original(self):
        msgs = [
            {"role": "system", "content": "HERRAMIENTAS MCP"},
            {"role": "user", "content": "CLAUDE.md memoria"},
            {"role": "user", "content": "hola"},
        ]
        copia = [dict(m) for m in msgs]
        sc._aplicar_cache_control(msgs)
        self.assertEqual(msgs, copia)

    def test_lista_vacia_devuelve_vacia(self):
        self.assertEqual(sc._aplicar_cache_control([]), [])

    def test_marcas_no_alteran_contenido(self):
        msgs = [{"role": "system", "content": "contenido exacto"}]
        out = sc._aplicar_cache_control(msgs)
        self.assertEqual(out[0]["content"], "contenido exacto")
        self.assertEqual(out[0]["cache_control"], _EPHEMERAL)


# 3) Resolución del estado (flag > entorno > config > defecto)
class TestResolverPromptCaching(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(sc.ENV_PROMPT_CACHING, None)

    def test_defecto_activado(self):
        self.assertTrue(sc._resolver_prompt_caching(None))

    def test_explicito_false_gana_sobre_env_1(self):
        os.environ[sc.ENV_PROMPT_CACHING] = "1"
        self.assertFalse(sc._resolver_prompt_caching(False))

    def test_explicito_true_gana_sobre_env_0(self):
        os.environ[sc.ENV_PROMPT_CACHING] = "0"
        self.assertTrue(sc._resolver_prompt_caching(True))

    def test_env_0_desactiva(self):
        os.environ[sc.ENV_PROMPT_CACHING] = "0"
        self.assertFalse(sc._resolver_prompt_caching(None))

    def test_env_false_desactiva(self):
        os.environ[sc.ENV_PROMPT_CACHING] = "false"
        self.assertFalse(sc._resolver_prompt_caching(None))

    def test_env_1_activa(self):
        os.environ[sc.ENV_PROMPT_CACHING] = "1"
        self.assertTrue(sc._resolver_prompt_caching(None))

    def test_config_json_prompt_caching_false(self):
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"provider": "deepseek",
                                             "prompt_caching": False}):
            self.assertFalse(sc._resolver_prompt_caching(None))

    def test_config_json_prompt_caching_true(self):
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"prompt_caching": True}):
            self.assertTrue(sc._resolver_prompt_caching(None))


# 4) Integración en _enviar_al_proveedor
class TestEnviarAlProveedorCaching(unittest.TestCase):
    def setUp(self):
        self.fake_ai = _FakeOpenAI()
        self.org_openai = sc.openai
        self.org_import_openai = sc._importar_openai
        self.env = mock.patch.dict(
            "os.environ",
            {"DEEPSEEK_API_KEY": "k", "GROQ_API_KEY": "k"},
            clear=False)
        self.env.start()
        sc.openai = self.fake_ai
        sc._importar_openai = lambda: self.fake_ai
        self.addCleanup(self.env.stop)
        self.addCleanup(lambda: setattr(sc, "openai", self.org_openai))
        self.addCleanup(
            lambda: setattr(sc, "_importar_openai", self.org_import_openai))

    def test_deepseek_recibe_cache_control(self):
        msgs = [{"role": "system", "content": "HERRAMIENTAS MCP"}]
        sc._enviar_al_proveedor("deepseek", None, msgs, prompt_caching=True)
        enviados = self.fake_ai.captured["messages"]
        self.assertEqual(enviados[0].get("cache_control"), _EPHEMERAL)

    def test_deepseek_con_caching_desactivado_no_marca(self):
        msgs = [{"role": "system", "content": "HERRAMIENTAS MCP"}]
        sc._enviar_al_proveedor("deepseek", None, msgs, prompt_caching=False)
        enviados = self.fake_ai.captured["messages"]
        self.assertNotIn("cache_control", enviados[0])

    def test_groq_no_recibe_cache_control(self):
        msgs = [{"role": "system", "content": "HERRAMIENTAS MCP"}]
        sc._enviar_al_proveedor("groq", None, msgs, prompt_caching=True)
        enviados = self.fake_ai.captured["messages"]
        self.assertNotIn("cache_control", enviados[0])


class TestEnviarAlProveedorAnthropic(unittest.TestCase):
    def setUp(self):
        self.fake_ai = _FakeAnthropic()
        self.org = sc.anthropic
        self.org_import = sc._importar_anthropic
        sc.anthropic = self.fake_ai
        sc._importar_anthropic = lambda: self.fake_ai
        self.env = mock.patch.dict("os.environ",
                                   {"ANTHROPIC_API_KEY": "k"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(lambda: setattr(sc, "anthropic", self.org))
        self.addCleanup(
            lambda: setattr(sc, "_importar_anthropic", self.org_import))

    def test_anthropic_recibe_cache_control(self):
        msgs = [{"role": "system", "content": "CLAUDE.md memoria"},
                {"role": "user", "content": "hola"}]
        sc._enviar_al_proveedor("anthropic", None, msgs, prompt_caching=True)
        enviados = self.fake_ai.captured["messages"]
        self.assertEqual(enviados[0].get("cache_control"), _EPHEMERAL)


class TestGeminiSinMarcas(unittest.TestCase):
    def setUp(self):
        self.fake_genai = _FakeGenai()
        self.org = sc.genai
        self.org_import = sc._importar_genai
        sc.genai = self.fake_genai
        sc._importar_genai = lambda: self.fake_genai
        self.env = mock.patch.dict("os.environ",
                                   {"GEMINI_API_KEY": "k"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(lambda: setattr(sc, "genai", self.org))
        self.addCleanup(
            lambda: setattr(sc, "_importar_genai", self.org_import))

    def test_gemini_envia_sin_marcas(self):
        msgs = [{"role": "system", "content": "HERRAMIENTAS MCP"},
                {"role": "user", "content": "hola"}]
        sc._enviar_al_proveedor("gemini", None, msgs, prompt_caching=True)
        for contenido in self.fake_genai.captured:
            self.assertNotIn("cache_control", contenido)


# 5) Flags CLI
class TestFlagPromptCachingCLI(unittest.TestCase):
    def test_flag_por_defecto_activado(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertTrue(args.prompt_caching)

    def test_flag_explicito_activado(self):
        args = sc.crear_parser().parse_args(["--prompt-caching", "consulta"])
        self.assertTrue(args.prompt_caching)

    def test_flag_no_prompt_caching_desactiva(self):
        args = sc.crear_parser().parse_args(["--no-prompt-caching", "consulta"])
        self.assertFalse(args.prompt_caching)


# 6) Mensajes de sesión
class TestMensajeCachingInicio(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(sc.ENV_PROMPT_CACHING, None)

    def test_activado_para_proveedor_que_soporta(self):
        self.assertEqual(sc._mensaje_caching_inicio("anthropic"),
                         "🧠 Prompt Caching activado para anthropic")

    def test_no_soportado_para_gemini(self):
        self.assertEqual(sc._mensaje_caching_inicio("gemini"),
                         "🧠 Prompt Caching no soportado para gemini")

    def test_no_mensaje_si_soporta_pero_desactivado(self):
        os.environ[sc.ENV_PROMPT_CACHING] = "0"
        self.assertIsNone(sc._mensaje_caching_inicio("deepseek"))


# 7) Resumen automático de ReAct preserva las marcas
class TestComprimirHistorialPreservaMarcas(unittest.TestCase):
    def test_resumen_recibe_cache_control_con_proveedor_compatible(self):
        import react_agent as ra
        agente = ra.ReactAgent(directorio=".", auto=True, proveedor="anthropic",
                               modelo="claude-x")
        agente.historial = [
            {"role": "system", "content": "sistema"},
            {"role": "user", "content": "paso 1"},
            {"role": "assistant", "content": "acción"},
        ]
        with mock.patch.object(agente, "_llamar_llm",
                               return_value="resumen del trabajo"):
            agente._comprimir_historial()
        self.assertEqual(len(agente.historial), 2)
        self.assertEqual(agente.historial[1]["cache_control"], _EPHEMERAL)
        self.assertEqual(agente.historial[0]["cache_control"], _EPHEMERAL)

    def test_resumen_sin_marcas_con_proveedor_no_compatible(self):
        import react_agent as ra
        agente = ra.ReactAgent(directorio=".", auto=True, proveedor="groq",
                               modelo="llama-x")
        agente.historial = [
            {"role": "system", "content": "sistema"},
            {"role": "user", "content": "paso 1"},
        ]
        with mock.patch.object(agente, "_llamar_llm",
                               return_value="resumen del trabajo"):
            agente._comprimir_historial()
        self.assertNotIn("cache_control", agente.historial[1])


if __name__ == "__main__":
    unittest.main()



