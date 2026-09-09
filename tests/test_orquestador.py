#!/usr/bin/env python3
"""Tests del orquestador de SnapContext (orquestador.py).

Se cubren el planificador (escaneo/selección con AgenteContexto, --vista-previa)
y el bucle de pruebas (coreografía Editor→Tester). El resto se delega en las
funciones ya probadas de ``snapcontext``.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from orquestador import VISTA_PREVIA, Orquestador


class TestPlanificacion(unittest.TestCase):
    """La planificación necesita un directorio que parezca proyecto."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        carpeta = os.path.join(cls._tmp, "lib", "core")
        os.makedirs(carpeta, exist_ok=True)
        for nombre in ("a.dart", "b.dart"):
            ruta = os.path.join(carpeta, nombre)
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write("void main(){}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _parser_args(self, extra=None):
        import snapcontext as sc

        argv = ["consulta", "--directorio", self._tmp, "--local"] + (extra or [])
        return sc.crear_parser().parse_args(argv)

    def test_planifica_local(self):
        import snapcontext as snap

        orch = Orquestador()
        args = self._parser_args([])
        plano = orch._planificar(args, snap)
        self.assertIsNotNone(plano)
        consulta, raiz, carpetas, seleccion = plano
        self.assertEqual(consulta, "consulta")
        self.assertEqual(len(seleccion), 2)

    def test_vista_previa_devuelve_centinela(self):
        import snapcontext as snap

        args = self._parser_args(["--vista-previa"])
        self.assertEqual(Orquestador()._planificar(args, snap), VISTA_PREVIA)

    def test_proyecto_invalido_devuelve_none(self):
        import snapcontext as snap

        args = snap.crear_parser().parse_args(["consulta"])
        # v1.3.0: solo bloquea si NO hay --local ni --directorio explicito.
        with mock.patch.object(snap, "_es_proyecto_valido", return_value=False):
            self.assertIsNone(Orquestador()._planificar(args, snap))


class TestBucleTest(unittest.TestCase):
    def setUp(self):
        self.orch = Orquestador()
        self.llamadas_edicion = []

    def test_pasa_tras_fallar(self):
        def edicion(*a):
            self.llamadas_edicion.append(a[1])  # mensaje

        def pruebas(*a):
            if len(self.llamadas_edicion) == 1:
                return type("R", (), {"returncode": 1, "stdout": "boom", "stderr": ""})()
            return type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        self.orch.agente_editor.ejecutar_aider = mock.Mock(side_effect=edicion)
        self.orch.agente_tester.ejecutar_pruebas = mock.Mock(side_effect=pruebas)

        ok = self.orch._bucle_test("tarea", ["a.dart"], ".", "", ["pytest"], 3)
        self.assertTrue(ok)
        self.assertEqual(len(self.llamadas_edicion), 2)

    def test_agota_intentos(self):
        self.orch.agente_editor.ejecutar_aider = mock.Mock()
        iter_fail = type("R", (), {"returncode": 1, "stdout": "x", "stderr": ""})
        self.orch.agente_tester.ejecutar_pruebas = mock.Mock(return_value=iter_fail)

        ok = self.orch._bucle_test("tarea", ["a.dart"], ".", "", ["pytest"], 2)
        self.assertFalse(ok)
        self.assertEqual(self.orch.agente_editor.ejecutar_aider.call_count, 2)


class TestOrquestadorNucleo(unittest.TestCase):
    def test_init_instancia_agentes(self):
        with mock.patch("snapcontext._plugins_herramientas", return_value=[]):
            orch = Orquestador()
        self.assertIsNotNone(orch.agente_contexto)
        self.assertIsNotNone(orch.agente_editor)
        self.assertIsNotNone(orch.agente_editor_propio)
        self.assertIsNotNone(orch.agente_tester)
        self.assertEqual(orch.herramientas_plugins, [])

    def test_init_carga_herramientas_plugins(self):
        with mock.patch("snapcontext._plugins_herramientas", return_value=["db", "api"]):
            orch = Orquestador()
        self.assertEqual(orch.herramientas_plugins, ["api", "db"])

    def test_on_evento_sin_callback_no_hace_nada(self):
        orch = mock.Mock(spec=Orquestador)
        orch.evento_callback = None
        # Sin callback no debe lanzar
        Orquestador._on_evento(orch, {"tipo": "log"})

    def test_on_evento_reenvia_al_callback(self):
        eventos = []
        orch = mock.Mock(spec=Orquestador)
        orch.evento_callback = eventos.append
        Orquestador._on_evento(orch, {"tipo": "log", "detalle": "x"})
        self.assertEqual(eventos[0]["tipo"], "log")

    def test_on_evento_ignora_error_del_consumidor(self):
        orch = mock.Mock(spec=Orquestador)

        def explota(_e):
            raise RuntimeError("boom")

        orch.evento_callback = explota
        Orquestador._on_evento(orch, {"tipo": "log"})  # no debe lanzar

    def test_emitir_tipo_envia_tipado(self):
        orch = mock.Mock(spec=Orquestador)
        orch.evento_callback = None
        orch._on_evento = mock.Mock()
        Orquestador._emitir_tipo(orch, "seleccion", archivos=["a.dart"])
        orch._on_evento.assert_called_once()
        self.assertEqual(orch._on_evento.call_args[0][0]["tipo"], "seleccion")


if __name__ == "__main__":
    unittest.main()
