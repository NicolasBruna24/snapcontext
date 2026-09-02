#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v6.12.0: TUI inmersiva con Textual (--tui).

Cubre: tui_hub (cola de eventos), tui_app (importación, app y drenaje de
cola) y la integración con el CLI (flag --tui). No se abre ninguna interfaz.
"""

import os
import queue
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tui_hub as hub          # noqa: E402
import tui_app                 # noqa: E402
import snapcontext as sc       # noqa: E402


class TestTuiHub(unittest.TestCase):
    """Cola de eventos: activación, emisión y degradación elegante."""

    def setUp(self):
        hub.reiniciar()

    def tearDown(self):
        hub.reiniciar()

    def test_inactivo_por_defecto(self):
        self.assertFalse(hub.esta_activo())
        self.assertIsNone(hub.cola_eventos())

    def test_activar_y_desactivar(self):
        cola = queue.Queue()
        hub.activar(cola)
        self.assertTrue(hub.esta_activo())
        self.assertIs(hub.cola_eventos(), cola)
        hub.desactivar()
        self.assertFalse(hub.esta_activo())
        self.assertIsNone(hub.cola_eventos())

    def test_emitir_sin_activo_descarta(self):
        self.assertFalse(hub.enviar_log("info", "no debe encolarse"))

    def test_enviar_log_valida_nivel(self):
        hub.activar()
        self.assertTrue(hub.enviar_log("info", "hola"))
        evento = hub.cola_eventos().get_nowait()
        self.assertEqual(evento["tipo"], "log")
        self.assertEqual(evento["nivel"], "info")
        self.assertEqual(evento["texto"], "hola")

    def test_enviar_log_nivel_invalido_cae_en_info(self):
        hub.activar()
        hub.enviar_log("nivel-inexistente", "x")
        self.assertEqual(hub.cola_eventos().get_nowait()["nivel"], "info")

    def test_enviar_paso_react(self):
        hub.activar()
        self.assertTrue(hub.enviar_paso_react(3, "accion", "editar_archivo",
                                              argumentos="{}"))
        evento = hub.cola_eventos().get_nowait()
        self.assertEqual(evento["tipo"], "react_step")
        self.assertEqual(evento["iteracion"], 3)


@unittest.skipUnless(tui_app.TEXTUAL_DISPONIBLE,
                     "Textual no está instalado (grupo [tui])")
class TestTuiApp(unittest.TestCase):
    """La aplicación Textual: creación, bindings y drenaje de cola."""

    def _app(self):
        return tui_app.SnapContextTUI(consulta="prueba", version="6.13.0")

    def test_importacion_y_creacion(self):
        app = self._app()
        self.assertIsNotNone(app)
        self.assertEqual(app.consulta, "prueba")
        self.assertEqual(app.version, "6.13.0")
        self.assertIsNone(app.tarea_agente)

    def test_titulo_contiene_version(self):
        self.assertIn("6.13.0", self._app().title)

    def test_bindings_definidos(self):
        acciones = {b[1] for b in tui_app.SnapContextTUI.BINDINGS}
        self.assertIn("quit", acciones)
        self.assertIn("limpiar_logs", acciones)
        self.assertIn("toggle_arbol", acciones)

    def test_procesar_evento_log(self):
        llamados = []
        with mock.patch.object(tui_app.SnapContextTUI, "_agregar_log",
                               side_effect=lambda n, t: llamados.append((n, t))):
            self._app()._procesar_evento({"tipo": "log", "nivel": "error",
                                          "texto": "boom"})
        self.assertEqual(llamados, [("error", "boom")])

    def test_procesar_evento_react_step(self):
        llamados = []
        app = self._app()
        with mock.patch.object(tui_app.SnapContextTUI, "_agregar_log",
                               side_effect=lambda n, t: llamados.append((n, t))):
            app._procesar_evento({"tipo": "react_step", "iteracion": 2,
                                  "fase": "pensamiento", "contenido": "analizar"})
        self.assertEqual(len(llamados), 1)
        self.assertIn("pensamiento", llamados[0][1])
        self.assertEqual(app._pasos, 2)

    def test_procesar_evento_estado(self):
        with mock.patch.object(tui_app.SnapContextTUI, "_refrescar_estado") as re_:
            self._app()._procesar_evento({"tipo": "estado",
                                          "estado": "ejecutando",
                                          "detalle": "editar_archivo"})
            re_.assert_called_once_with("ejecutando", "editar_archivo")

    def test_procesar_evento_fin(self):
        llamados = []
        with mock.patch.object(tui_app.SnapContextTUI, "_agregar_log",
                               side_effect=lambda n, t: llamados.append((n, t))), \
             mock.patch.object(tui_app.SnapContextTUI, "_refrescar_estado"):
            self._app()._procesar_evento({"tipo": "fin", "ok": False,
                                          "resultado": "abortado"})
        self.assertEqual(llamados[0][0], "error")
        self.assertIn("finalizado", llamados[0][1])

    def test_refrescar_cola_drena_eventos(self):
        hub.activar()
        hub.enviar_log("info", "uno")
        hub.enviar_log("warning", "dos")
        llamados = []
        try:
            with mock.patch.object(tui_app.SnapContextTUI, "_agregar_log",
                                   side_effect=lambda n, t: llamados.append((n, t))):
                self._app().refrescar_cola()
        finally:
            hub.desactivar()
        self.assertEqual(len(llamados), 2)

    def test_refrescar_cola_sin_hub_no_falla(self):
        self._app().refrescar_cola()  # hub inactivo → no-op, sin excepción

    def test_estilos_y_constantes(self):
        self.assertEqual(set(tui_app.COLORES_NIVEL), {"info", "warning", "error"})
        self.assertIn("pensamiento", tui_app.COLORES_FASE)
        self.assertIn("ejecutando", tui_app.ESTADOS_AGENTE)

    def test_escapar_marcado(self):
        self.assertEqual(tui_app._escapar("[+] add"), "\\[+] add")


class TestIntegracionCLI(unittest.TestCase):
    """Flag --tui en el parser y lanzador con degradación elegante."""

    def test_flag_tui_existe(self):
        args = sc.crear_parser().parse_args(["--tui", "hola mundo"])
        self.assertTrue(args.tui)
        self.assertEqual(args.consulta, "hola mundo")

    def test_flag_tui_desactivado_por_defecto(self):
        args = sc.crear_parser().parse_args(["hola"])
        self.assertFalse(args.tui)

    def test_version_actualizada(self):
        self.assertEqual(sc.VERSION, "6.13.0")
        ruta = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "pyproject.toml")
        with open(ruta, encoding="utf-8") as fh:
            self.assertIn('version = "6.13.0"', fh.read())

    def test_grupo_tui_en_pyproject(self):
        ruta = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "pyproject.toml")
        with open(ruta, encoding="utf-8") as fh:
            texto = fh.read()
        self.assertIn("tui = [", texto)
        self.assertIn("textual>=0.50.0", texto)

    @unittest.skipIf(tui_app.TEXTUAL_DISPONIBLE, "Textual instalado")
    def test_sin_textual_error_claro(self):
        codigo = sc._ejecutar_tui(sc.crear_parser().parse_args(["--tui"]))
        self.assertEqual(codigo, 2)

    def test_react_agent_no_usa_hub_si_inactivo(self):
        import react_agent as ra
        original = ra.ReactAgent.__init__
        capturado = {}

        def _init(self, *a, **k):
            original(self, *a, **k)
            capturado["wi"] = self._wi

        try:
            with mock.patch.object(ra.ReactAgent, "__init__", _init):
                ra.ReactAgent(directorio=".", auto=True)
            self.assertIsNone(capturado["wi"])
        finally:
            hub.reiniciar()

    def test_react_agent_usa_hub_si_activo(self):
        hub.activar()
        import react_agent as ra
        original = ra.ReactAgent.__init__
        capturado = {}

        def _init(self, *a, **k):
            original(self, *a, **k)
            capturado["wi"] = self._wi

        try:
            with mock.patch.object(ra.ReactAgent, "__init__", _init):
                ra.ReactAgent(directorio=".", auto=True)
            self.assertIs(capturado["wi"], hub)
        finally:
            hub.reiniciar()

    def test_tui_log_encola_con_hub_activo(self):
        hub.activar()
        try:
            sc._tui_log("info", "evento para la TUI")
            self.assertFalse(hub.cola_eventos().empty())
        finally:
            hub.desactivar()
        sc._tui_log("info", "evento descartado")  # inactivo → no-op


if __name__ == "__main__":
    unittest.main()
