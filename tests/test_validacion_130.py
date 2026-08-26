#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v1.3.0: validación de carpeta permisiva y --iniciar-proyecto."""

import os
import shutil
import tempfile
import unittest

from orquestador import Orquestador, VISTA_PREVIA


class TestEsProyectoValido(unittest.TestCase):
    """_es_proyecto_valido() debe aceptar carpetas/archivos VACÍOS (v1.3.0)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_valid_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _crear(self, *rutas):
        for rel in rutas:
            ruta = os.path.join(self.tmp, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            with open(ruta, "w", encoding="utf-8"):
                pass  # archivos vacíos a propósito

    # --- casos que AHORA son válidos ---
    def test_carpeta_tipica_vacia(self):
        import snapcontext as sc
        os.makedirs(os.path.join(self.tmp, "lib"))
        self.assertTrue(sc._es_proyecto_valido(self.tmp))

    def test_archivo_codigo_vacio_en_raiz(self):
        import snapcontext as sc
        self._crear("main.py")
        self.assertTrue(sc._es_proyecto_valido(self.tmp))

    def test_config_vacio_en_raiz(self):
        import snapcontext as sc
        self._crear("pubspec.yaml")
        self.assertTrue(sc._es_proyecto_valido(self.tmp))

    def test_pyproject_toml_vacio(self):
        import snapcontext as sc
        self._crear("pyproject.toml")
        self.assertTrue(sc._es_proyecto_valido(sc.resolver_raiz(self.tmp)))

    def test_carpeta_completamente_vacia(self):
        import snapcontext as sc
        self.assertFalse(sc._es_proyecto_valido(self.tmp))

    def test_solo_archivo_no_codigo(self):
        import snapcontext as sc
        self._crear("leeme.txt")
        self.assertFalse(sc._es_proyecto_valido(self.tmp))

    def test_directorio_inexistente(self):
        import snapcontext as sc
        self.assertFalse(
            sc._es_proyecto_valido(os.path.join(self.tmp, "no_existe")))


class TestIniciarProyecto(unittest.TestCase):
    """Flag --iniciar-proyecto y comportamiento de la validación en _planificar."""

    def setUp(self):
        self.prev = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="sc_init_")
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.prev)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self, extra=None):
        import snapcontext as sc
        return sc.crear_parser().parse_args(["consulta"] + (extra or []))

    def test_flag_existe_en_parser(self):
        args = self._args(["--iniciar-proyecto"])
        self.assertTrue(args.iniciar_proyecto)
        args2 = self._args(["--no-validar"])
        self.assertTrue(args2.iniciar_proyecto)
        self.assertFalse(self._args().iniciar_proyecto)

    def _planificar_con_escaneo_simulado(self, args):
        import snapcontext as snap
        orch = Orquestador()
        orch.agente_contexto.escanear_candidatos = (
            lambda *a, **k: ["main.py"])
        return orch._planificar(args, snap)

    def test_directorio_vacio_bloquea_sin_flags(self):
        self.assertIsNone(self._planificar_con_escaneo_simulado(self._args()))

    def test_iniciar_proyecto_permite_carpeta_vacia(self):
        plano = self._planificar_con_escaneo_simulado(
            self._args(["--iniciar-proyecto"]))
        self.assertIsNotNone(plano)
        consulta, raiz, carpetas, seleccion = plano
        self.assertEqual(seleccion, ["main.py"])

    def test_local_no_bloquea_en_carpeta_vacia(self):
        plano = self._planificar_con_escaneo_simulado(self._args(["--local"]))
        self.assertIsNotNone(plano)

    def test_directorio_explicito_no_bloquea_en_carpeta_vacia(self):
        import snapcontext as snap
        args = snap.crear_parser().parse_args(
            ["consulta", "--directorio", self.tmp])
        plano = self._planificar_con_escaneo_simulado(args)
        self.assertIsNotNone(plano)

    def test_vista_previa_con_iniciar_proyecto(self):
        import snapcontext as snap
        orch = Orquestador()
        orch.agente_contexto.escanear_candidatos = (
            lambda *a, **k: ["main.py"])
        args = snap.crear_parser().parse_args(
            ["consulta", "--iniciar-proyecto", "--vista-previa"])
        self.assertEqual(orch._planificar(args, snap), VISTA_PREVIA)


class TestVersion(unittest.TestCase):
    def test_version_130_coherente(self):
        import snapcontext as sc
        self.assertEqual(sc.VERSION, "4.5.0")


if __name__ == "__main__":
    unittest.main()
