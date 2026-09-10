"""Tests de Orquestador.ejecutar_flujo (orquestador.py).

La planificación y los agentes están mockeados; solo se prueba la coreografía.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orquestador import VISTA_PREVIA, Orquestador


def _args_flujo(**extra):
    base = dict(
        consulta="tarea",
        server_loop=False,
        manual_loop=False,
        test_loop=False,
        comando_test=None,
        editor="aider",
        aider_opciones=None,
        max_iteraciones=1,
        max_intentos=2,
        dispositivo=None,
        url_defecto=None,
        modo_edicion="auto",
        modelo=None,
        validar=True,
        max_intentos_validacion=3,
        provider=None,
        modelo_ligero=False,
        auto=True,
        max_context_tokens=None,
        editor_fallback=False,
        mostrar_diff=False,
        sin_aprendizaje=False,
    )
    base.update(extra)
    return argparse.Namespace(**base)


import argparse  # noqa: E402  (tras _args_flujo por claridad de lectura)


class TestEjecutarFlujo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orch = Orquestador()
        self.orch.agente_editor = mock.Mock()
        self.orch.agente_editor_propio = mock.Mock()
        self.orch.agente_aprendizaje = mock.Mock()
        self.plan = ("tarea", Path(self.tmp), "lib", ["a.py"])
        patcher = mock.patch.object(Orquestador, "_planificar", return_value=self.plan)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_aider_exitoso(self):
        self.orch.agente_editor.ejecutar_aider.return_value = True
        codigo = self.orch.ejecutar_flujo(_args_flujo())
        self.assertEqual(codigo, 0)
        self.orch.agente_aprendizaje.aprender_de_tarea.assert_called_once()

    def test_aider_fallido(self):
        self.orch.agente_editor.ejecutar_aider.return_value = False
        self.assertEqual(self.orch.ejecutar_flujo(_args_flujo()), 1)

    def test_sin_aprendizaje(self):
        self.orch.agente_editor.ejecutar_aider.return_value = True
        self.orch.ejecutar_flujo(_args_flujo(sin_aprendizaje=True))
        self.orch.agente_aprendizaje.aprender_de_tarea.assert_not_called()

    def test_aprendizaje_fallido_no_rompe(self):
        self.orch.agente_editor.ejecutar_aider.return_value = True
        self.orch.agente_aprendizaje.aprender_de_tarea.side_effect = RuntimeError("db")
        self.assertEqual(self.orch.ejecutar_flujo(_args_flujo()), 0)

    def test_vista_previa_termina_en_0(self):
        with mock.patch.object(Orquestador, "_planificar", return_value=VISTA_PREVIA):
            self.assertEqual(self.orch.ejecutar_flujo(_args_flujo()), 0)
        self.orch.agente_editor.ejecutar_aider.assert_not_called()

    def test_plan_none_termina_en_1(self):
        with mock.patch.object(Orquestador, "_planificar", return_value=None):
            self.assertEqual(self.orch.ejecutar_flujo(_args_flujo()), 1)

    def test_editor_propio(self):
        self.orch.agente_editor_propio.ejecutar.return_value = True
        codigo = self.orch.ejecutar_flujo(_args_flujo(editor="propio"))
        self.assertEqual(codigo, 0)
        self.orch.agente_editor_propio.ejecutar.assert_called_once()

    def test_bucle_de_pruebas(self):
        self.orch.agente_editor.ejecutar_aider.return_value = True
        with (
            mock.patch.object(Orquestador, "_bucle_test", return_value=True) as bucle,
        ):
            codigo = self.orch.ejecutar_flujo(_args_flujo(test_loop=True, comando_test="pytest -q"))
        self.assertEqual(codigo, 0)
        bucle.assert_called_once()

    def test_bucle_con_servidor(self):
        with mock.patch.object(sc_mod(), "ejecutar_bucle_agente", return_value=True) as bucle:
            codigo = self.orch.ejecutar_flujo(_args_flujo(server_loop=True))
        self.assertEqual(codigo, 0)
        bucle.assert_called_once()

    def test_evento_callback_se_registra_y_limpia(self):
        eventos = []
        orch = Orquestador(evento_callback=eventos.append)
        orch.agente_editor = mock.Mock()
        orch.agente_editor.ejecutar_aider.return_value = True
        orch.agente_aprendizaje = mock.Mock()
        with mock.patch.object(Orquestador, "_planificar", return_value=self.plan):
            codigo = orch.ejecutar_flujo(_args_flujo())
        self.assertEqual(codigo, 0)
        self.assertTrue(any(e.get("tipo") == "final" for e in eventos))


def sc_mod():
    import snapcontext as sc

    return sc


if __name__ == "__main__":
    unittest.main()
