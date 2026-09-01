#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para mcp_tools_browser.py — navegador MCP (v6.10.0).

Ejecuta con:
    python -m unittest tests.test_mcp_tools_browser -v
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

import mcp_tools_browser as btool


def _activar():
    btool.browser_activar(headless=True)


def _desactivar():
    btool.browser_desactivar()


def _pagina_mock():
    """Página de Playwright simulada."""
    pagina = MagicMock()
    pagina.url = "http://localhost:3000"
    pagina.title.return_value = "Mi App"
    pagina.screenshot.return_value = b"PNG-DATA"
    elemento = MagicMock()
    elemento.inner_text.return_value = "Hola mundo"
    elemento.screenshot.return_value = b"ELEM-DATA"
    pagina.query_selector.return_value = elemento
    return pagina


class BaseBrowserTest(unittest.TestCase):
    def setUp(self):
        _activar()
        self._pagina = _pagina_mock()

    def tearDown(self):
        _desactivar()

    def _con_pagina(self):
        """Parchea el arranque para devolver la página simulada."""
        return (patch.object(btool, "_asegurar_navegador",
                             return_value=self._pagina),
                patch.object(btool, "_importar_playwright",
                             return_value=True))


class TestGestionSesion(BaseBrowserTest):
    def test_inactivo_por_defecto(self):
        _desactivar()
        self.assertFalse(btool.browser_activo())
        r = btool.browser_abrir("http://localhost")
        self.assertFalse(r["ok"])
        self.assertIn("--browser", r["error"])

    def test_activar_desactivar(self):
        self.assertTrue(btool.browser_activo())
        _desactivar()
        self.assertFalse(btool.browser_activo())

    def test_headless_por_defecto(self):
        _desactivar()
        btool.browser_activar()
        self.assertTrue(btool._HEADLESS)
        btool.browser_activar(headless=False)
        self.assertFalse(btool._HEADLESS)

    def test_cerrar_libera_recursos(self):
        navegador = MagicMock()
        contexto = MagicMock()
        pw = MagicMock()
        with patch.object(btool, "_NAVEGADOR", navegador, create=True), \
             patch.object(btool, "_CONTEXTO", contexto, create=True), \
             patch.object(btool, "_PLAYWRIGHT", pw, create=True):
            btool.browser_cerrar()
        navegador.close.assert_called_once()
        contexto.close.assert_called_once()
        pw.stop.assert_called_once()
        self.assertIsNone(btool._NAVEGADOR)
        self.assertIsNone(btool._PAGINA)

    def test_navegador_vivo_false_sin_pagina(self):
        with patch.object(btool, "_PAGINA", None, create=True), \
             patch.object(btool, "_NAVEGADOR", None, create=True):
            self.assertFalse(btool._navegador_vivo())


class TestBrowserAbrir(BaseBrowserTest):
    def test_abrir_ok(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_abrir("http://localhost:3000")
        self.assertTrue(r["ok"])
        self.assertEqual(r["titulo"], "Mi App")
        self._pagina.goto.assert_called_once()

    def test_abrir_con_wait_for(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_abrir("http://localhost:3000",
                                    wait_for="#app")
        self.assertTrue(r["ok"])
        self._pagina.wait_for_selector.assert_called_once()

    def test_url_invalida(self):
        r = btool.browser_abrir("no-es-una-url")
        self.assertFalse(r["ok"])
        self.assertIn("URL inválida", r["error"])

    def test_url_vacia(self):
        r = btool.browser_abrir("")
        self.assertFalse(r["ok"])

    def test_timeout_en_goto(self):
        self._pagina.goto.side_effect = TimeoutError("tardó mucho")
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_abrir("http://localhost:3000")
        self.assertFalse(r["ok"])
        self.assertIn("TimeoutError", r["error"])

    def test_inactivo_devuelve_error(self):
        _desactivar()
        r = btool.browser_abrir("http://localhost:3000")
        self.assertFalse(r["ok"])


class TestBrowserScreenshot(BaseBrowserTest):
    def test_screenshot_ok(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_screenshot()
        self.assertTrue(r["ok"])
        self.assertEqual(base64.b64decode(r["imagen"]), b"PNG-DATA")
        self.assertEqual(r["formato"], "png")

    def test_screenshot_selector(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_screenshot(selector="#boton")
        self.assertTrue(r["ok"])
        self.assertEqual(r["selector"], "#boton")

    def test_screenshot_selector_no_encontrado(self):
        self._pagina.query_selector.return_value = None
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_screenshot(selector="#fantasma")
        self.assertFalse(r["ok"])
        self.assertIn("no encontrado", r["error"])

    def test_screenshot_navega_primero(self):
        with patch.object(btool, "browser_abrir",
                          return_value={"ok": False, "error": "x"}) as m:
            r = btool.browser_screenshot(url="http://malo")
        m.assert_called_once()
        self.assertFalse(r["ok"])


class TestBrowserInteraccion(BaseBrowserTest):
    """browser_click / browser_type / browser_get_text."""

    def test_click_ok(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_click("#boton")
        self._pagina.click.assert_called_once()
        self.assertTrue(r["ok"])
        self.assertEqual(r["selector"], "#boton")

    def test_click_sin_selector(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_click("  ")
        self.assertFalse(r["ok"])

    def test_click_error_playwright(self):
        self._pagina.click.side_effect = TimeoutError("timeout 10s")
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_click("#lento")
        self.assertFalse(r["ok"])
        self.assertIn("TimeoutError", r["error"])

    def test_type_ok(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_type("#usuario", "ana")
        self._pagina.fill.assert_called_once_with("#usuario", "ana",
                                                  timeout=10000)
        self.assertTrue(r["ok"])
        self.assertEqual(r["texto"], "ana")

    def test_get_text_ok(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_get_text("h1")
        self.assertTrue(r["ok"])
        self.assertEqual(r["texto"], "Hola mundo")

    def test_get_text_selector_no_encontrado(self):
        self._pagina.query_selector.return_value = None
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = btool.browser_get_text("#fantasma")
        self.assertFalse(r["ok"])
        self.assertIn("no encontrado", r["error"])


class TestMultimodalidad(BaseBrowserTest):
    """Análisis visual (solo modelos con visión)."""

    def test_sin_vision_error_claro(self):
        p1, p2 = self._con_pagina()
        with p1, p2:
            with patch.object(btool, "modelo_soporta_vision",
                              return_value=False):
                r = btool.browser_analizar_imagen("aG9sYQ==", "¿qué ves?")
        self.assertFalse(r["ok"])
        self.assertIn("visión", r["error"])

    def test_imagen_vacia(self):
        with patch.object(btool, "modelo_soporta_vision",
                          return_value=True):
            r = btool.browser_analizar_imagen("", "¿qué ves?")
        self.assertFalse(r["ok"])

    def test_soporta_vision_gemini(self):
        self.assertTrue(btool.modelo_soporta_vision("gemini",
                                                    "gemini-2.5-pro"))

    def test_soporta_vision_claude(self):
        self.assertTrue(btool.modelo_soporta_vision("anthropic",
                                                    "claude-3-7-sonnet"))

    def test_no_soporta_vision_otros(self):
        self.assertFalse(btool.modelo_soporta_vision("deepseek",
                                                     "deepseek-chat"))

    def test_registro_en_mcp(self):
        herramientas: dict = {}
        btool.registrar_en(herramientas)
        for esperada in ("browser_abrir", "browser_screenshot",
                         "browser_click", "browser_type",
                         "browser_get_text", "browser_analizar_imagen",
                         "browser_cerrar"):
            self.assertIn(esperada, herramientas)


class TestIntegracionReAct(BaseBrowserTest):
    """Integración con ReactAgent (react_agent.py)."""

    def _agente(self):
        import react_agent as ra
        with patch.object(ra.ReactAgent, "_pedir_decision", lambda *a: None):
            return ra.ReactAgent(directorio=".", auto=True, max_iter=1,
                                 proveedor="gemini",
                                 modelo="gemini-2.5-pro")

    def test_herramientas_browser_registradas(self):
        agente = self._agente()
        import react_agent as ra
        for accion in ("browser_abrir", "browser_screenshot",
                       "browser_click", "browser_type",
                       "browser_get_text", "browser_analizar_imagen"):
            self.assertIn(accion, agente.herramientas)
            self.assertIn(accion, ra.ReactAgent.ACCIONES_VALIDAS)

    def test_despacho_browser_abrir(self):
        agente = self._agente()
        p1, p2 = self._con_pagina()
        with p1, p2:
            r = agente._ejecutar_accion("browser_abrir",
                                        {"url": "http://localhost:3000"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["titulo"], "Mi App")

    def test_despacho_desconocido_devuelve_none(self):
        agente = self._agente()
        self.assertIsNone(agente._ejecutar_accion("browser_nada", {}))

    def test_screenshot_sin_vision_no_analiza(self):
        agente = self._agente()
        agente.modelo = "deepseek-chat"
        agente.proveedor = "deepseek"
        p1, p2 = self._con_pagina()
        with p1, p2:
            with patch.object(btool, "modelo_soporta_vision",
                              return_value=False):
                r = agente._ejecutar_accion("browser_screenshot", {})
        self.assertTrue(r["ok"])
        self.assertNotIn("analisis", r)

