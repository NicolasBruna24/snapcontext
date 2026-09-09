"""Tests de la Fase 17: integración del perfil de prompt en el flujo de envío.

Verifica (con mocks, sin llamar a APIs reales) que ``_enviar_al_proveedor_unico``
aplica el perfil del modelo (inyecta el system_prompt) y que el system se pasa
correctamente a la API de OpenAI, Anthropic y Gemini.

Nota sobre los mocks: los SDK (``openai``, ``anthropic``,
``google.generativeai``) pueden NO estar instalados en el entorno de test. Por
eso se inyectan MagicMocks directamente en los globals del módulo
(``sc.__dict__``) en lugar de parchear los módulos reales: el lookup global
``openai.OpenAI(...)`` de ``_enviar_al_proveedor_unico`` encuentra el mock y
``_importar_openai()`` devuelve el mock (no ``None``), sin tocar el import real.
Se usan las entradas REALES de ``PROVEEDORES`` (dicts) para no duplicar claves.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prompt_profiles as pp
import snapcontext as sc


class TestIntegracionSnapcontext(unittest.TestCase):
    """Inyecta el SDK falso en los globals y verifica el envío final."""

    def test_openai_recibe_mensaje_system_del_perfil(self):
        cielo = mock.MagicMock()
        respuesta = mock.MagicMock()
        respuesta.choices[0].message.content = "hecho"
        cielo.chat.completions.create.return_value = respuesta
        openai_falso = mock.MagicMock()
        openai_falso.OpenAI.return_value = cielo
        # PROVEEDORES no registra "openai" como proveedor directo (solo los
        # compatibles: groq/ollama/deepseek), así que añadimos una entrada
        # falsa con todas las claves que espera el código (dicts, no objetos).
        proveedor_openai = {
            "tipo": "openai",
            "requiere_clave": True,
            "clave_env": "OPENAI_API_KEY",
            "url_env": "OPENAI_BASE_URL",
            "url_default": "http://localhost:9999",
            "base_url": None,
            "nombre": "OpenAI",
            "modelo_default": "gpt-fake",
        }

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k"}):
            with mock.patch.dict(sc.PROVEEDORES, {"openai": proveedor_openai}):
                with mock.patch.dict(sc.__dict__, {"openai": openai_falso}):
                    sc._enviar_al_proveedor_unico(
                        "openai",
                        "gpt-fake",
                        [{"role": "user", "content": "hola"}],
                        tipo_tarea="general",
                    )
        _, kwargs = cielo.chat.completions.create.call_args
        msgs = kwargs["messages"]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("programming assistant", msgs[0]["content"].lower())
        # El usuario original llega reformateado por la plantilla del perfil.
        self.assertIn("hola", msgs[-1]["content"])

    def test_anthropic_extrae_system_como_parametro(self):
        cliente = mock.MagicMock()
        bloque = mock.MagicMock()
        bloque.type = "text"
        bloque.text = "ok"
        respuesta = mock.MagicMock()
        respuesta.content = [bloque]
        cliente.messages.create.return_value = respuesta
        anthropic_falso = mock.MagicMock()
        anthropic_falso.Anthropic.return_value = cliente

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            with mock.patch.dict(sc.__dict__, {"anthropic": anthropic_falso}):
                sc._enviar_al_proveedor_unico(
                    "anthropic",
                    "claude-x",
                    [{"role": "user", "content": "hola"}],
                    tipo_tarea="general",
                )
        _, kwargs = cliente.messages.create.call_args
        # El system va como parámetro `system`, no como mensaje.
        self.assertIn("system", kwargs)
        self.assertIn("asistente de programación experto", kwargs["system"])
        self.assertTrue(all(m["role"] != "system" for m in kwargs["messages"]))

    def test_gemini_extrae_system_instruction(self):
        generador = mock.MagicMock()
        respuesta = mock.MagicMock()
        respuesta.text = "hecho"
        generador.generate_content.return_value = respuesta
        genai_falso = mock.MagicMock()
        genai_falso.GenerativeModel.return_value = generador

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            with mock.patch.dict(sc.__dict__, {"genai": genai_falso}):
                sc._enviar_al_proveedor_unico(
                    "gemini",
                    "gemini-x",
                    [{"role": "user", "content": "hola"}],
                    tipo_tarea="general",
                )
        _, kwargs = generador.generate_content.call_args
        self.assertEqual(
            kwargs["system_instruction"],
            pp.PERFILES["gemini"]["system_prompt"],
        )

    def test_proveedor_sin_perfil_usa_generico(self):
        """Un proveedor no registrado en PERFILES recibe PERFIL_GENERICO."""
        cielo = mock.MagicMock()
        respuesta = mock.MagicMock()
        respuesta.choices[0].message.content = "hecho"
        cielo.chat.completions.create.return_value = respuesta
        openai_falso = mock.MagicMock()
        openai_falso.OpenAI.return_value = cielo
        proveedor_falso = {
            "tipo": "openai",
            "requiere_clave": True,
            "clave_env": "MIPROVEEDOR_API_KEY",
            "url_env": "MIPROVEEDOR_BASE_URL",
            "url_default": "http://localhost:9999",
            "base_url": None,
            "nombre": "MiProveedor",
            "modelo_default": "mi-modelo",
        }

        with mock.patch.dict(os.environ, {"MIPROVEEDOR_API_KEY": "k"}):
            with mock.patch.dict(sc.PROVEEDORES, {"miproveedor": proveedor_falso}):
                with mock.patch.dict(sc.__dict__, {"openai": openai_falso}):
                    sc._enviar_al_proveedor_unico(
                        "miproveedor",
                        None,
                        [{"role": "user", "content": "hola"}],
                        tipo_tarea="general",
                    )
        _, kwargs = cielo.chat.completions.create.call_args
        msgs = kwargs["messages"]
        self.assertEqual(msgs[0]["role"], "system")
        # Fallback: exactamente el system_prompt del perfil genérico.
        self.assertEqual(msgs[0]["content"], pp.PERFIL_GENERICO["system_prompt"])


if __name__ == "__main__":
    unittest.main()
