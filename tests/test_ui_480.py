#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests v4.8.0: capa de presentación profesional con Rich (ui.py).

No se hacen snapshots de colores (frágiles). En su lugar se usa
`unittest.mock` para verificar que las funciones de `rich` (p. ej.
`Console.print`, `rich.prompt.Prompt.ask`, `rich.progress.track`) son
invocadas con los argumentos correctos, y que el modo `--auto` silencia
progreso y preguntas devolviendo `'c'` por defecto sin preguntar.
"""

import importlib
import os
import unittest
from unittest import mock

import ui


class TestModoAuto(unittest.TestCase):
    """El modo no interactivo (--auto) silencia progreso y preguntas."""

    def setUp(self):
        ui.configurar_auto(False)

    def tearDown(self):
        ui.configurar_auto(False)

    def test_preguntar_interactivo_en_auto_devuelve_c(self):
        """En --auto nunca se pregunta: se devuelve 'c' (contrato v4.7.0)."""
        ui.configurar_auto(True)
        with mock.patch.object(ui.Prompt, "ask",
                               side_effect=AssertionError("Prompt en auto")), \
                mock.patch("builtins.input",
                           side_effect=AssertionError("input() en auto")):
            self.assertEqual(
                ui.preguntar_interactivo(None, "Continuar con el cambio?"), "c")
            self.assertEqual(
                ui.preguntar_interactivo(ui.OPCIONES_IMPACTO_DEFECTO,
                                         "¿Qué hago?", "a"), "a")

    def test_mostrar_progreso_auto_devuelve_iterable_sin_envolver(self):
        """En --auto la barra de progreso está silenciada (mismo iterable)."""
        ui.configurar_auto(True)
        datos = [1, 2, 3]
        with mock.patch("ui.track",
                        side_effect=AssertionError("track en auto")):
            self.assertIs(ui.mostrar_progreso(datos, "Escaneando..."), datos)

    def test_es_auto_refleja_configurar_auto(self):
        self.assertFalse(ui.es_auto())
        ui.configurar_auto(True)
        self.assertTrue(ui.es_auto())


class TestRichDisponible(unittest.TestCase):
    """Con `rich` instalado las funciones llaman a Console.print correctamente.

    Estos tests parchean `ui._console` con un Mock para inspeccionar las
    llamadas, manteniéndose deterministas (sin depender de colores reales).
    """

    def setUp(self):
        ui.configurar_auto(False)
        self.assertEqual(True, ui.RICH_DISPONIBLE)   # prerequisito del entorno

    def tearDown(self):
        ui.configurar_auto(False)

    def test_mostrar_estado_imprime_mensaje_con_emoji(self):
        with mock.patch("ui._console") as consola:
            ui.mostrar_estado("Compilando...", emoji="🔨")
        consola.print.assert_called_once_with("[cyan]🔨 Compilando...[/cyan]")

    def test_mostrar_error_imprime_panel_rojo(self):
        with mock.patch("ui._console") as consola:
            ui.mostrar_error("Algo salió mal")
        consola.print.assert_called_once()
        panel = consola.print.call_args.args[0]
        self.assertIsInstance(panel, ui.Panel)
        self.assertEqual(panel.border_style, "red")
        self.assertEqual(panel.title, "✖ Error")

    def test_mostrar_diff_imprime_cabecera_y_syntax(self):
        with mock.patch("ui._console") as consola:
            ui.mostrar_diff("app.py", 5, 2, "--- a\n+++ b\n+hola\n-hola\n")
        self.assertEqual(consola.print.call_count, 2)
        cabecera, synth = [c.args[0] for c in consola.print.call_args_list]
        self.assertIn("app.py", str(cabecera))
        self.assertIn("+5", str(cabecera))
        self.assertIn("-2", str(cabecera))
        self.assertIsInstance(synth, ui.Syntax)
        self.assertEqual(synth.lexer.name.lower(), "diff")

    def test_mostrar_tabla_impacto_construye_columnas_y_filas(self):
        dependencias = {"main.py": ["utils.py", "config.py"],
                        "otro.py": ["utils.py"]}
        criticas = {"main.py"}   # filas críticas → amarillo
        with mock.patch("ui._console") as consola:
            ui.mostrar_tabla_impacto(dependencias, criticas=criticas)
        consola.print.assert_called_once()
        tabla = consola.print.call_args.args[0]
        self.assertIsInstance(tabla, ui.Table)
        self.assertEqual(len(tabla.columns), 3)
        # 3 dependencias en total → 3 filas de datos.
        filas = [r for r in tabla.rows]
        self.assertEqual(len(filas), 3)

    def test_mostrar_tabla_impacto_vacia_no_imprime(self):
        with mock.patch("ui._console") as consola:
            ui.mostrar_tabla_impacto({})
        consola.print.assert_not_called()

    def test_mostrar_banner_imprime_arte_y_tabla(self):
        with mock.patch("ui._console") as consola:
            ui.mostrar_banner("4.8.0")
        self.assertGreaterEqual(consola.print.call_count, 2)

    def test_banner_muestra_el_repo_url_configurable(self):
        """El banner imprime REPO_URL (no una URL fija hardcodeada)."""
        with mock.patch("ui._console") as consola:
            ui.mostrar_banner("4.8.0")
        salida = " ".join(str(c.args[0]) for c in consola.print.call_args_list)
        self.assertIn(ui.REPO_URL, salida)
        self.assertNotIn("TU_USUARIO", salida)

    def test_repo_url_usado_tambien_sin_rich(self):
        """Sin `rich` el fallback plano también usa REPO_URL."""
        with mock.patch("ui.RICH_DISPONIBLE", False), \
                mock.patch("builtins.print") as print_mock:
            ui.mostrar_banner("4.8.0")
        texto = " ".join(str(c.args[0]) for c in print_mock.call_args_list)
        self.assertIn(ui.REPO_URL, texto)

    def test_repo_url_se_puede_sobreescribir_con_env(self):
        """SNAPCONTEXT_REPO (si está definida) reemplaza el valor por defecto."""
        previo = ui.REPO_URL
        try:
            with mock.patch.dict(
                    os.environ,
                    {"SNAPCONTEXT_REPO": "https://github.com/mi-fork/snapcontext"}):
                recargado = importlib.reload(ui)
            self.assertEqual(
                recargado.REPO_URL,
                "https://github.com/mi-fork/snapcontext")
        finally:
            ui.REPO_URL = previo   # restaura por si el env quedó seteada

    def test_preguntar_interactivo_usa_prompt_y_devuelve_tecla(self):
        with mock.patch("ui._console") as consola, \
                mock.patch.object(ui.Prompt, "ask", return_value="s") as ask:
            resultado = ui.preguntar_interactivo(
                ui.OPCIONES_IMPACTO_DEFECTO, "Cambio con impacto")
        self.assertEqual(resultado, "s")
        # Se muestra el menú [c]/[a]/[s] y se pide con Prompt.ask.
        consola.print.assert_called_once()
        ask.assert_called_once()
        self.assertEqual(ask.call_args.kwargs["choices"], ["c", "a", "s"])

    def test_mostrar_progreso_no_auto_envuelve_con_track(self):
        datos = [1, 2, 3]
        envuelto = object()
        with mock.patch("ui.track", return_value=envuelto) as track:
            resultado = ui.mostrar_progreso(datos, "Procesando...")
        self.assertIs(resultado, envuelto)
        track.assert_called_once_with(
            datos, description="Procesando...", console=ui._console)


class TestFallbackSinRich(unittest.TestCase):
    """Sin `rich` instalado la UI degrada a print()/input() plano."""

    def test_mostrar_estado_fallback_a_print_plano(self):
        with mock.patch("ui.RICH_DISPONIBLE", False), \
                mock.patch("builtins.print") as print_mock:
            ui.mostrar_estado("mensaje", emoji="⚙️")
        print_mock.assert_called_once_with("⚙️ mensaje")

    def test_preguntar_fallback_a_input_plano(self):
        with mock.patch("ui.RICH_DISPONIBLE", False), \
                mock.patch("builtins.input", return_value="s"):
            self.assertEqual(
                ui.preguntar_interactivo(ui.OPCIONES_IMPACTO_DEFECTO, "¿Qué?"),
                "s")

    def test_preguntar_fallback_input_invalido_devuelve_defecto(self):
        with mock.patch("ui.RICH_DISPONIBLE", False), \
                mock.patch("builtins.input", return_value="zzz"):
            self.assertEqual(
                ui.preguntar_interactivo(ui.OPCIONES_IMPACTO_DEFECTO,
                                         "¿Qué?", defecto="c"), "c")


if __name__ == "__main__":
    unittest.main()

