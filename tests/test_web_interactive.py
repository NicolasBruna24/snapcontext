#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la UI web interactiva (v6.5.0).

Cubre el hub ``web.interactive`` (timeline ReAct, diff conflicts, validación y
seguridad), los endpoints de ``web.app`` en modo interactivo, el flag
``--web-interactive`` y la integración con el agente ReAct y el editor propio.
Los WebSockets se prueban a nivel de cola/eventos (sin servidor real) para que
la suite sea rápida y determinista.
"""

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import snapcontext as sc

import web.interactive as wi


class BaseInteractivo(unittest.TestCase):
    def setUp(self):
        wi.reiniciar()

    def tearDown(self):
        wi.reiniciar()


class TestHubBasico(BaseInteractivo):
    """Activación del hub y emisión de eventos."""

    def test_inactivo_por_defecto(self):
        self.assertFalse(wi.esta_activo())
        self.assertIsNone(wi.cola_eventos())

    def test_activar_devuelve_cola(self):
        self.assertTrue(wi.activar())
        self.assertTrue(wi.esta_activo())
        self.assertIsInstance(wi.cola_eventos(), queue.Queue)

    def test_emitir_inactivo_devuelve_false(self):
        self.assertFalse(wi.emitir("react_step", fase="accion"))

    def test_emitir_encola_evento_con_timestamp(self):
        wi.activar()
        self.assertTrue(wi.emitir("react_step", fase="accion"))
        evento = wi.cola_eventos().get_nowait()
        self.assertEqual(evento["tipo"], "react_step")
        self.assertEqual(evento["fase"], "accion")
        self.assertIn("timestamp", evento)

    def test_emitir_recorta_cadenas_largas(self):
        wi.activar()
        wi.emitir("x", texto="a" * (wi.MAX_CONTENIDO + 500))
        evento = wi.cola_eventos().get_nowait()
        self.assertEqual(len(evento["texto"]), wi.MAX_CONTENIDO)

    def test_desactivar_limpia_estado(self):
        wi.activar()
        wi.desactivar()
        self.assertFalse(wi.esta_activo())
        self.assertIsNone(wi.cola_eventos())


class TestTimelineReact(BaseInteractivo):
    """Eventos del timeline ReAct (pensamiento/acción/observación/error)."""

    FASES = ("pensamiento", "accion", "observacion", "error")

    def test_paso_valido_se_encola(self):
        wi.activar()
        self.assertTrue(wi.enviar_paso_react(3, "pensamiento", "Analizo…"))
        ev = wi.cola_eventos().get_nowait()
        self.assertEqual((ev["tipo"], ev["iteracion"], ev["fase"]),
                         ("react_step", 3, "pensamiento"))
        self.assertEqual(ev["contenido"], "Analizo…")

    def test_fase_invalida_se_normaliza_a_observacion(self):
        wi.activar()
        wi.enviar_paso_react(1, "fase_inventada", "x")
        self.assertEqual(wi.cola_eventos().get_nowait()["fase"], "observacion")

    def test_las_cuatro_fases_se_emiten(self):
        wi.activar()
        for fase in self.FASES:
            wi.enviar_paso_react(1, fase, "c")
        salidas = [wi.cola_eventos().get_nowait()["fase"] for _ in self.FASES]
        self.assertEqual(salidas, list(self.FASES))

    def test_enviar_estado_y_log(self):
        wi.activar()
        wi.enviar_estado("Pensando...", "detalle")
        wi.enviar_log("warning", "cuidado")
        ev1 = wi.cola_eventos().get_nowait()
        ev2 = wi.cola_eventos().get_nowait()
        self.assertEqual(ev1["tipo"], "agent_status")
        self.assertEqual(ev2["tipo"], "log_interactivo")
        self.assertEqual(ev2["nivel"], "warning")

    def test_log_nivel_invalido_se_normaliza(self):
        wi.activar()
        wi.enviar_log("panic", "boom")
        self.assertEqual(wi.cola_eventos().get_nowait()["nivel"], "info")


class TestDiffConflicto(BaseInteractivo):
    """Diff viewer: conflicto → espera → respuesta del cliente."""

    @staticmethod
    def _responder_en_hilo(decision, contenido="nuevo contenido"):
        def _cuerpo():
            ev = wi.cola_eventos().get(timeout=2)
            wi._recibir_mensaje({"tipo": "diff_respuesta", "id": ev["id"],
                                 "decision": decision,
                                 "contenido": contenido})
        hilo = threading.Thread(target=_cuerpo)
        hilo.start()
        return hilo

    def test_inactivo_devuelve_none_inmediato(self):
        self.assertIsNone(
            wi.enviar_conflicto_diff("a.py", "origen", "propuesto"))

    def test_respuesta_aceptar(self):
        wi.activar()
        hilo = self._responder_en_hilo("aceptar")
        respuesta = wi.enviar_conflicto_diff("a.py", "original", "propuesto",
                                             timeout=5)
        hilo.join(5)
        self.assertIsNotNone(respuesta)
        self.assertEqual(respuesta["decision"], "aceptar")
        self.assertEqual(respuesta["contenido"], "nuevo contenido")

    def test_respuesta_rechazar(self):
        wi.activar()
        hilo = self._responder_en_hilo("rechazar", "")
        respuesta = wi.enviar_conflicto_diff("a.py", "o", "p", timeout=5)
        hilo.join(5)
        self.assertIsNotNone(respuesta)
        self.assertEqual(respuesta["decision"], "rechazar")

    def test_decision_invalida_no_resuelve(self):
        wi.activar()
        hilo = self._responder_en_hilo("quizas", "x")
        self.assertIsNone(wi.enviar_conflicto_diff("a.py", "o", "p",
                                                   timeout=1))
        hilo.join(5)

    def test_respuesta_para_id_desconocido_se_ignora(self):
        wi._recibir_mensaje({"tipo": "diff_respuesta", "id": "fantasma",
                             "decision": "aceptar", "contenido": "x"})
        self.assertTrue(wi.esta_activo() or True)   # no lanzó

    def test_contenido_no_cadena_se_vacia(self):
        wi.activar()

        def _cuerpo():
            ev = wi.cola_eventos().get(timeout=2)
            wi._recibir_mensaje({"tipo": "diff_respuesta", "id": ev["id"],
                                 "decision": "aceptar",
                                 "contenido": {"mal": 1}})
        hilo = threading.Thread(target=_cuerpo)
        hilo.start()
        respuesta = wi.enviar_conflicto_diff("a.py", "o", "p", timeout=5)
        hilo.join(5)
        self.assertIsNotNone(respuesta)
        self.assertEqual(respuesta["contenido"], "")

    def test_original_no_cadena_devuelve_none(self):
        wi.activar()
        self.assertIsNone(
            wi.enviar_conflicto_diff("a.py", 12345, "propuesto"))

    def test_recibir_mensaje_invalido_no_lanza(self):
        wi._recibir_mensaje(None)
        wi._recibir_mensaje("texto")

    def test_callback_libre_recibe_otros_mensajes(self):
        recibidos = []
        wi.registrar_callback_libre(recibidos.append)
        wi._recibir_mensaje({"tipo": "otro", "dato": 1})
        self.assertEqual(recibidos, [{"tipo": "otro", "dato": 1}])


class TestIntegracionAppYFlags(unittest.TestCase):
    """Endpoints de web.app, flags CLI y compatibilidad."""

    def setUp(self):
        wi.reiniciar()

    def tearDown(self):
        wi.reiniciar()

    def test_app_normal_no_tiene_ruta_interactiva(self):
        from web.app import crear_app
        app = crear_app()
        rutas = {r.path for r in app.routes}
        self.assertNotIn("/interactive", rutas)
        self.assertNotIn("/ws/interactive", rutas)
        self.assertFalse(wi.esta_activo())

    def test_app_interactiva_registra_rutas_y_activa_hub(self):
        from web.app import crear_app
        app = crear_app(interactiva=True)
        rutas = {r.path for r in app.routes}
        self.assertIn("/interactive", rutas)
        self.assertIn("/ws/interactive", rutas)
        self.assertIn("/interactive/health", rutas)
        self.assertTrue(wi.esta_activo())
        self.assertIsNotNone(wi.cola_eventos())

    def test_flag_cli_registrado(self):
        namespace = sc.crear_parser().parse_args(
            ["--web", "--web-interactive"])
        self.assertTrue(namespace.web_interactive)
        self.assertTrue(namespace.web)
        # Compatibilidad: sin el flag, False.
        namespace2 = sc.crear_parser().parse_args(["--web"])
        self.assertFalse(namespace2.web_interactive)

    def test_iniciar_servidor_web_pasa_interactiva(self):
        namespace = SimpleNamespace(web_puerto=8123, web_interactive=True)
        with mock.patch("web.app.arrancar_servidor") as arranque:
            codigo = sc.iniciar_servidor_web(namespace)
        self.assertEqual(codigo, 0)
        arranque.assert_called_once_with(puerto=8123, interactiva=True)
        self.assertFalse(wi.esta_activo())   # hub desactivado tras salir

    def test_iniciar_servidor_web_sin_flag(self):
        namespace = SimpleNamespace(web_puerto=8000, web_interactive=False)
        with mock.patch("web.app.arrancar_servidor") as arranque:
            codigo = sc.iniciar_servidor_web(namespace)
        self.assertEqual(codigo, 0)
        arranque.assert_called_once_with(puerto=8000, interactiva=False)


class TestIntegracionReAct(unittest.TestCase):
    """ReactAgent emite pasos cuando web_interactive está activo."""

    def setUp(self):
        wi.reiniciar()

    def tearDown(self):
        wi.reiniciar()

    def _agente(self, web_interactive):
        import react_agent as ra
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"provider": "mock"}):
            return ra.ReactAgent(auto=True, max_iter=1, proveedor="mock",
                                 web_interactive=web_interactive)

    def test_agente_emite_pasos_react(self):
        wi.activar()
        agente = self._agente(True)
        self.assertIsNotNone(agente._wi)
        decision = {"pensamiento": "Razono", "accion": "finalizar",
                    "argumentos": {"resumen": "hecho"}}
        with mock.patch.object(agente, "_pedir_decision",
                               return_value=decision), \
             mock.patch.object(agente, "_resumir_si_hace_falta"):
            resultado = agente.ejecutar("tarea")
        self.assertTrue(resultado["ok"])
        eventos = []
        while True:
            try:
                eventos.append(wi.cola_eventos().get_nowait())
            except queue.Empty:
                break
        self.assertIn("react_step", [e["tipo"] for e in eventos])
        fases = [e["fase"] for e in eventos if e["tipo"] == "react_step"]
        self.assertIn("pensamiento", fases)

    def test_agente_sin_web_interactive_no_emite(self):
        agente = self._agente(False)
        self.assertIsNone(agente._wi)
        decision = {"pensamiento": "R", "accion": "finalizar",
                    "argumentos": {"resumen": "f"}}
        with mock.patch.object(agente, "_pedir_decision",
                               return_value=decision), \
             mock.patch.object(agente, "_resumir_si_hace_falta"):
            agente.ejecutar("tarea")
        self.assertFalse(wi.esta_activo())

    def test_react_recibe_web_interactive_de_args(self):
        """_ejecutar_react propaga el flag del CLI al agente."""
        import inspect
        import react_agent as ra
        self.assertIn("web_interactive=bool(getattr(args, "
                      "\"web_interactive\", False))",
                      inspect.getsource(sc))
        self.assertIn("web_interactive",
                      inspect.signature(ra.ReactAgent.__init__).parameters)


class TestIntegracionEditor(unittest.TestCase):
    """El editor propio resuelve conflictos vía web cuando el hub está activo."""

    def _editor(self):
        import agentes as ag
        return ag.AgenteEditorPropio.__new__(ag.AgenteEditorPropio)

    def test_conflicto_web_aceptar(self):
        import agentes as ag
        wi.activar()
        editor = self._editor()
        with mock.patch.object(ag.AgenteEditorPropio, "aplicar_parche",
                               return_value=False), \
             mock.patch.object(ag.AgenteEditorPropio, "sobrescribir",
                               return_value=True) as sobra:
            def resolver():
                ev = wi.cola_eventos().get(timeout=2)
                wi._recibir_mensaje({"tipo": "diff_respuesta", "id": ev["id"],
                                     "decision": "aceptar",
                                     "contenido": "contenido aceptado"})
            hilo = threading.Thread(target=resolver)
            hilo.start()
            resultado = editor._aplicar_con_conflicto(
                "a.py", "diff", ".", "actual", preview="preview",
                auto=False, mostrar_diff=False)
            hilo.join(5)
        self.assertEqual(resultado, "ok")
        sobra.assert_called_once_with("a.py", "contenido aceptado", ".")

    def test_conflicto_web_rechazar_reintenta(self):
        import agentes as ag
        wi.activar()
        editor = self._editor()
        with mock.patch.object(ag.AgenteEditorPropio, "aplicar_parche",
                               return_value=False), \
             mock.patch.object(ag.AgenteEditorPropio, "sobrescribir") as sobra:
            def resolver():
                ev = wi.cola_eventos().get(timeout=2)
                wi._recibir_mensaje({"tipo": "diff_respuesta", "id": ev["id"],
                                     "decision": "rechazar"})
            hilo = threading.Thread(target=resolver)
            hilo.start()
            resultado = editor._aplicar_con_conflicto(
                "a.py", "diff", ".", "actual", auto=False)
            hilo.join(5)
        self.assertEqual(resultado, "reintentar")
        sobra.assert_not_called()

    def test_conflicto_hub_inactivo_usa_menu_terminal(self):
        import agentes as ag
        editor = self._editor()
        with mock.patch.object(ag.AgenteEditorPropio, "aplicar_parche",
                               return_value=False), \
             mock.patch.object(sc, "_menu_conflicto_parche",
                               return_value="r"):
            resultado = editor._aplicar_con_conflicto(
                "a.py", "diff", ".", "actual", auto=False)
        self.assertEqual(resultado, "reintentar")


if __name__ == "__main__":
    unittest.main()


