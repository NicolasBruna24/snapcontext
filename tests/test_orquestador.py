#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

from orquestador import Orquestador, VISTA_PREVIA


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
        with mock.patch.object(snap, "_es_proyecto_valido", return_value=False):
            args = self._parser_args([])
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


if __name__ == "__main__":
    unittest.main()