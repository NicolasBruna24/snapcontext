#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v6.31.0: Prompt Caching por Capas.

Cubre:
  - Detección de capas (estática / semi-estática / volátil) por marcadores.
  - Prioridad estática > semi-estática > volátil (un mensaje, una capa).
  - Configuración personalizada de capas (prompt_caching.capas).
  - Ensamblado en orden estricto + marcas cache_control por capa.
  - Métricas de tokens por capa (modo --depurar).
  - Resolución del estado (flag --prompt-caching-capas / entorno / config).
  - Integración en `_enviar_al_proveedor` (capas vs caching básico v6.16.0).
  - Mensajes de sesión (básico intacto + línea por Capas).
  - Compatibilidad total con el caching básico de v6.16.0.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prompt_cache as pc          # noqa: E402
import snapcontext as sc           # noqa: E402

_EPHEMERAL = {"type": "ephemeral"}

_MENSAJES_MIXTOS = [
    {"role": "system", "content": "Eres un asistente de código."},
    {"role": "user", "content": "HERRAMIENTAS MCP: editar_archivo, "
                                "ejecutar_comando"},
    {"role": "user", "content": "Memoria del proyecto (CLAUDE.md): "
                                "reglas del repositorio"},
    {"role": "user", "content": "Arregla el login, por favor"},
    {"role": "assistant", "content": "Vale, lo reviso ahora mismo."},
]


class _FakeOpenAI:
    """Sustituye al módulo `openai` (DeepSeek)."""

    def __init__(self):
        self.captured = None

    def OpenAI(self, **kwargs):
        self.kwargs = kwargs
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
        self.messages = self
        return self

    def create(self, **kwargs):
        self.captured = kwargs
        bloque = type("X", (), {"type": "text", "text": "respuesta"})()
        resp = type("X", (), {"content": [bloque]})()
        return resp


# 1) Detección de capas
class TestDeteccionCapas(unittest.TestCase):
    def test_system_es_estatica(self):
        self.assertTrue(pc.es_capa_estatica(
            {"role": "system", "content": "Eres un asistente."}))

    def test_herramientas_son_estaticas(self):
        self.assertTrue(pc.es_capa_estatica(
            {"role": "user", "content": "HERRAMIENTAS MCP: editar_archivo"}))

    def test_claude_md_es_semi_estatica(self):
        mensaje = {"role": "user", "content": "Memoria (CLAUDE.md)"}
        self.assertTrue(pc.es_capa_semi_estatica(mensaje))
        self.assertFalse(pc.es_capa_estatica(mensaje))

    def test_graph_rag_es_semi_estatica(self):
        self.assertTrue(pc.es_capa_semi_estatica(
            {"role": "user",
             "content": "GRAFO DE DEPENDENCIAS del proyecto:\na -> b"}))

    def test_reglas_son_semi_estaticas(self):
        self.assertTrue(pc.es_capa_semi_estatica(
            {"role": "user",
             "content": "Reglas del repositorio: usa type hints"}))

    def test_usuario_normal_es_volatil(self):
        mensaje = {"role": "user", "content": "arregla el login"}
        self.assertTrue(pc.es_capa_volatil(mensaje))
        self.assertFalse(pc.es_capa_estatica(mensaje))
        self.assertFalse(pc.es_capa_semi_estatica(mensaje))

    def test_asistente_es_volatil(self):
        self.assertTrue(pc.es_capa_volatil(
            {"role": "assistant", "content": "hecho"}))

    def test_prioridad_estatica_sobre_semi(self):
        """Un 'system' que también trae CLAUDE.md cuenta como estático."""
        mensaje = {"role": "system", "content": "sis + memoria CLAUDE.md"}
        self.assertTrue(pc.es_capa_estatica(mensaje))
        self.assertFalse(pc.es_capa_semi_estatica(mensaje))

    def test_capas_configurables(self):
        """El usuario puede mover 'tools' a la capa semi-estática."""
        config = {"prompt_caching": {"capas": {
            "estatica": ["system"],
            "semi_estatica": ["tools", "claude_md"],
            "volatil": ["user_messages"],
        }}}
        mensaje = {"role": "user", "content": "HERRAMIENTAS MCP: grep"}
        self.assertFalse(pc.es_capa_estatica(mensaje, config))
        self.assertTrue(pc.es_capa_semi_estatica(mensaje, config))
        self.assertFalse(pc.es_capa_volatil(mensaje, config))

    def test_mensaje_vacio_es_volatil(self):
        self.assertTrue(pc.es_capa_volatil({"role": "user", "content": ""}))


# 2) Clasificación de una lista plana
class TestClasificarMensajes(unittest.TestCase):
    def test_reparto_y_orden_relativo(self):
        capas = pc.clasificar_mensajes(_MENSAJES_MIXTOS)
        self.assertEqual(len(capas["estatica"]), 2)       # system + tools
        self.assertEqual(len(capas["semi_estatica"]), 1)  # CLAUDE.md
        self.assertEqual(len(capas["volatil"]), 2)        # user + assistant
        self.assertEqual(capas["estatica"][0]["role"], "system")
        self.assertIn("HERRAMIENTAS", capas["estatica"][1]["content"])
        self.assertIn("CLAUDE.md", capas["semi_estatica"][0]["content"])
        self.assertEqual(capas["volatil"][0]["role"], "user")
        self.assertEqual(capas["volatil"][1]["role"], "assistant")

    def test_primer_mensaje_sin_role_es_estatico(self):
        capas = pc.clasificar_mensajes(
            [{"content": "prompt sin role"}, {"role": "user", "content": "x"}])
        self.assertEqual(len(capas["estatica"]), 1)
        self.assertEqual(len(capas["volatil"]), 1)

    def test_no_muta_la_lista_original(self):
        copia = [dict(m) for m in _MENSAJES_MIXTOS]
        pc.clasificar_mensajes(_MENSAJES_MIXTOS)
        self.assertEqual(_MENSAJES_MIXTOS, copia)

    def test_lista_vacia(self):
        self.assertEqual(pc.clasificar_mensajes([]),
                         {"estatica": [], "semi_estatica": [],
                          "volatil": []})


# 3) Ensamblado del prompt estructurado
class TestEnsamblarPromptEstructurado(unittest.TestCase):
    def test_orden_estricto_por_capas(self):
        salida = pc.ensamblar_prompt_estructurado(
            "Eres un asistente.",
            [{"role": "user", "content": "HERRAMIENTAS MCP: editar_archivo"}],
            [{"role": "user", "content": "Memoria (CLAUDE.md)"}],
            [{"role": "user", "content": "arregla el login"},
             {"role": "assistant", "content": "ok"}],
        )
        self.assertEqual([m["role"] for m in salida],
                         ["system", "user", "user", "user", "assistant"])
        self.assertIn("asistente", salida[0]["content"])
        self.assertIn("HERRAMIENTAS", salida[1]["content"])
        self.assertIn("CLAUDE.md", salida[2]["content"])
        self.assertEqual(salida[3]["content"], "arregla el login")

    def test_cache_control_en_estatica_y_semi(self):
        salida = pc.ensamblar_prompt_estructurado(
            "sistema",
            [{"role": "user", "content": "HERRAMIENTAS MCP"}],
            [{"role": "user", "content": "CLAUDE.md"}],
            [{"role": "user", "content": "tarea"}],
        )
        self.assertEqual(salida[0]["cache_control"], _EPHEMERAL)   # estática
        self.assertEqual(salida[1]["cache_control"], _EPHEMERAL)   # estática
        self.assertEqual(salida[2]["cache_control"], _EPHEMERAL)   # semi
        self.assertNotIn("cache_control", salida[3])               # volátil

    def test_sistema_str_se_envuelve_como_system(self):
        salida = pc.ensamblar_prompt_estructurado(
            "prompt del sistema", None, None, None)
        self.assertEqual(salida,
                         [{"role": "system", "content": "prompt del sistema",
                           "cache_control": _EPHEMERAL}])

    def test_sistema_vacio_se_ignora(self):
        self.assertEqual(pc.ensamblar_prompt_estructurado(
            "", None, None, None), [])

    def test_acepta_dict_y_lista(self):
        salida = pc.ensamblar_prompt_estructurado(
            {"role": "system", "content": "sis"},
            {"role": "user", "content": "HERRAMIENTAS MCP"},
            [{"role": "user", "content": "CLAUDE.md"},
             {"role": "user", "content": "reglas del repositorio"}],
            [{"role": "user", "content": "tarea"}],
        )
        self.assertEqual(len(salida), 5)
        for mensaje in salida[:4]:
            self.assertIn("cache_control", mensaje)
        self.assertNotIn("cache_control", salida[4])

    def test_no_muta_las_entradas(self):
        sistema = {"role": "system", "content": "sis"}
        estatico = [{"role": "user", "content": "HERRAMIENTAS MCP"}]
        semi = [{"role": "user", "content": "CLAUDE.md"}]
        recientes = [{"role": "user", "content": "tarea"}]
        pc.ensamblar_prompt_estructurado(sistema, estatico, semi, recientes)
        self.assertNotIn("cache_control", sistema)
        for lista in (estatico, semi, recientes):
            for mensaje in lista:
                self.assertNotIn("cache_control", mensaje)

    def test_capa_volatil_preserva_orden_relativo(self):
        salida = pc.ensamblar_prompt_estructurado(
            "sis", None, None,
            [{"role": "user", "content": "primero"},
             {"role": "assistant", "content": "segundo"},
             {"role": "user", "content": "tercero"}])
        self.assertEqual([m["content"] for m in salida[1:]],
                         ["primero", "segundo", "tercero"])


# 4) Métricas por capa (modo --depurar)
class TestMetricasCapas(unittest.TestCase):
    def test_tokens_por_capa(self):
        mensajes = [
            {"role": "system", "content": "abcd"},                       # 1
            {"role": "user", "content": "HERRAMIENTAS MCP: abcdefgh"},   # 6
            {"role": "user", "content": "CLAUDE.md abcde"},              # 3 (13 chars)
            {"role": "user", "content": "abcd"},                         # 1
        ]
        m = pc.metricas_capas(mensajes)
        self.assertEqual(m["estatica"], 7)       # 1 + 6
        self.assertEqual(m["semi_estatica"], 3)  # "CLAUDE.md abcde" = 13 chars → 3
        self.assertEqual(m["volatil"], 1)

    def test_lista_vacia_cero_tokens(self):
        self.assertEqual(pc.metricas_capas([]),
                         {"estatica": 0, "semi_estatica": 0, "volatil": 0})

    def test_heuristica_1_token_4_chars(self):
        self.assertEqual(pc.contar_tokens("abcd"), 1)
        self.assertEqual(pc.contar_tokens(""), 0)


# 5) Resolución del estado de las Capas (snapcontext)
class TestResolverPromptCachingCapas(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(sc.ENV_PROMPT_CACHING_CAPAS, None)
        sc._configurar_prompt_caching_capas(None)

    def test_defecto_activado(self):
        self.assertTrue(sc._resolver_prompt_caching_capas(None))

    def test_explicito_true_gana_sobre_env_0(self):
        os.environ[sc.ENV_PROMPT_CACHING_CAPAS] = "0"
        self.assertTrue(sc._resolver_prompt_caching_capas(True))

    def test_explicito_false_gana_sobre_env_1(self):
        os.environ[sc.ENV_PROMPT_CACHING_CAPAS] = "1"
        self.assertFalse(sc._resolver_prompt_caching_capas(False))

    def test_env_0_desactiva(self):
        os.environ[sc.ENV_PROMPT_CACHING_CAPAS] = "0"
        self.assertFalse(sc._resolver_prompt_caching_capas(None))

    def test_env_1_activa(self):
        os.environ[sc.ENV_PROMPT_CACHING_CAPAS] = "1"
        self.assertTrue(sc._resolver_prompt_caching_capas(None))

    def test_config_capas_activo_false_desactiva(self):
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"prompt_caching": {
                                   "activo": True, "capas_activo": False}}):
            self.assertFalse(sc._resolver_prompt_caching_capas(None))

    def test_config_prompt_caching_bool_ignorado(self):
        """`prompt_caching: false` (v6.16.0) no toca el estado de las capas."""
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"prompt_caching": False}):
            self.assertTrue(sc._resolver_prompt_caching_capas(None))

    def test_estado_global_tiene_prioridad(self):
        sc._configurar_prompt_caching_capas(False)
        os.environ[sc.ENV_PROMPT_CACHING_CAPAS] = "1"
        self.assertFalse(sc._capas_caching_activo())
        sc._configurar_prompt_caching_capas(True)
        self.assertTrue(sc._capas_caching_activo())
        sc._configurar_prompt_caching_capas(None)      # vuelve a resolver
        self.assertTrue(sc._capas_caching_activo())

    def test_tolerancia_dict_en_resolver_basico(self):
        """prompt_caching como dict (v6.31.0): 'activo' manda; bool intacto."""
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"prompt_caching": {
                                   "activo": False, "capas": {}}}):
            self.assertFalse(sc._resolver_prompt_caching(None))
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"prompt_caching": {
                                   "activo": True, "capas": {}}}):
            self.assertTrue(sc._resolver_prompt_caching(None))
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"prompt_caching": False}):
            self.assertFalse(sc._resolver_prompt_caching(None))
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"prompt_caching": True}):
            self.assertTrue(sc._resolver_prompt_caching(None))


# 6) Flags CLI (--prompt-caching-capas / --no-prompt-caching-capas)
class TestFlagPromptCachingCapasCLI(unittest.TestCase):
    def test_por_defecto_none(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.prompt_caching_capas)

    def test_flag_explicito_activa(self):
        args = sc.crear_parser().parse_args(
            ["consulta", "--prompt-caching-capas"])
        self.assertTrue(args.prompt_caching_capas)

    def test_flag_no_capas_desactiva(self):
        args = sc.crear_parser().parse_args(
            ["consulta", "--no-prompt-caching-capas"])
        self.assertFalse(args.prompt_caching_capas)
