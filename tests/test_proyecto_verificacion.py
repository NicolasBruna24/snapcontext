#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la verificación temprana de directorio de proyecto (v5.6.0/v6.0.0).

Cubre:
- ``_es_directorio_proyecto``: qué archivos/carpetas cuentan como raíz de
  proyecto (package.json, go.mod, pyproject.toml, src/, tests/, *.csproj…).
- ``_advertencia_directorio_proyecto``: se muestra el aviso en un directorio
  vacío, no se muestra con proyecto, con ``--demo`` ni con
  ``--no-validar-proyecto``, y ofrece continuar/demo/salir en modo interactivo.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import snapcontext as sc        # noqa: E402
import ui                       # noqa: E402


class BaseProyecto(unittest.TestCase):
    """Directorio temporal limpio por test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.addCleanup(self.tmp.cleanup)


class TestEsDirectorioProyecto(BaseProyecto):
    """Detección de raíz de proyecto por archivos/carpetas típicos."""

    def test_true_con_package_json(self):
        (Path(self.dir, "package.json")).write_text("{}", encoding="utf-8")
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_true_con_go_mod(self):
        Path(self.dir, "go.mod").write_text("module x\n", encoding="utf-8")
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_true_con_pyproject_toml(self):
        Path(self.dir, "pyproject.toml").write_text("[project]\n",
                                                    encoding="utf-8")
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_true_con_requirements_txt(self):
        Path(self.dir, "requirements.txt").write_text("requests\n",
                                                      encoding="utf-8")
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_true_con_csproj(self):
        Path(self.dir, "App.Tests.csproj").write_text("<Project/>",
                                                      encoding="utf-8")
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_true_con_carpeta_src(self):
        (Path(self.dir, "src")).mkdir()
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_true_con_carpeta_tests(self):
        (Path(self.dir, "tests")).mkdir()
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_true_con_carpeta_app(self):
        (Path(self.dir, "app")).mkdir()
        self.assertTrue(sc._es_directorio_proyecto(self.dir))

    def test_false_con_directorio_vacio(self):
        self.assertFalse(sc._es_directorio_proyecto(self.dir))

    def test_false_con_archivos_sueltos(self):
        Path(self.dir, "notas.txt").write_text("hola", encoding="utf-8")
        Path(self.dir, "foto.png").write_bytes(b"\x89PNG")
        self.assertFalse(sc._es_directorio_proyecto(self.dir))

    def test_false_directorio_inexistente(self):
        self.assertFalse(sc._es_directorio_proyecto("/no/existe/xyz"))


class TestAdvertenciaDirectorioProyecto(BaseProyecto):
    """La advertencia temprana según flags y directorio."""

    def _args(self, *flags):
        return sc.crear_parser().parse_args(list(flags) + ["consulta"])

    def test_muestra_aviso_y_continuar(self):
        args = self._args()
        with mock.patch.object(sc, "_es_directorio_proyecto",
                               return_value=False), \
             mock.patch.object(sc, "_ui_mostrar_banner"), \
             mock.patch.object(ui, "mostrar_estado") as estado, \
             mock.patch.object(ui, "preguntar_interactivo",
                               return_value="c") as preg:
            res = sc._advertencia_directorio_proyecto(args)
        self.assertIsNone(res)          # continua con el flujo normal
        estado.assert_called_once()
        preg.assert_called_once()

    def test_opcion_demo_ejecuta_demo(self):
        args = self._args()
        with mock.patch.object(sc, "_es_directorio_proyecto",
                               return_value=False), \
             mock.patch.object(ui, "mostrar_estado"), \
             mock.patch.object(ui, "preguntar_interactivo",
                               return_value="d"), \
             mock.patch.object(sc, "_ejecutar_demo", return_value=99) as demo:
            res = sc._advertencia_directorio_proyecto(args)
        self.assertEqual(res, 99)
        demo.assert_called_once()

    def test_opcion_salir_devuelve_cero(self):
        args = self._args()
        with mock.patch.object(sc, "_es_directorio_proyecto",
                               return_value=False), \
             mock.patch.object(ui, "mostrar_estado"), \
             mock.patch.object(ui, "preguntar_interactivo",
                               return_value="s"):
            res = sc._advertencia_directorio_proyecto(args)
        self.assertEqual(res, 0)

    def test_no_valida_proyecto_omite_aviso(self):
        args = self._args("--no-validar-proyecto")
        with mock.patch.object(sc, "_es_directorio_proyecto",
                               return_value=False), \
             mock.patch.object(ui, "mostrar_estado") as estado, \
             mock.patch.object(ui, "preguntar_interactivo",
                               side_effect=AssertionError("no debe preguntar")):
            res = sc._advertencia_directorio_proyecto(args)
        self.assertIsNone(res)
        estado.assert_not_called()

    def test_con_demo_omite_aviso(self):
        args = self._args("--demo")
        with mock.patch.object(sc, "_es_directorio_proyecto",
                               return_value=False), \
             mock.patch.object(ui, "mostrar_estado") as estado, \
             mock.patch.object(ui, "preguntar_interactivo",
                               side_effect=AssertionError("no debe preguntar")):
            res = sc._advertencia_directorio_proyecto(args)
        self.assertIsNone(res)
        estado.assert_not_called()

    def test_con_init_omite_aviso(self):
        args = self._args("--init")
        with mock.patch.object(ui, "mostrar_estado") as estado, \
             mock.patch.object(ui, "preguntar_interactivo",
                               side_effect=AssertionError("no debe preguntar")):
            res = sc._advertencia_directorio_proyecto(args)
        self.assertIsNone(res)
        estado.assert_not_called()

    def test_con_proyecto_no_muestra_aviso(self):
        args = self._args()
        with mock.patch.object(sc, "_es_directorio_proyecto",
                               return_value=True) as es, \
             mock.patch.object(ui, "mostrar_estado") as estado, \
             mock.patch.object(ui, "preguntar_interactivo",
                               side_effect=AssertionError("no debe preguntar")):
            res = sc._advertencia_directorio_proyecto(args)
        self.assertIsNone(res)
        es.assert_called_once()
        estado.assert_not_called()

    def test_auto_muestra_aviso_y_continua_sin_preguntar(self):
        args = self._args("--auto")
        with mock.patch.object(sc, "_es_directorio_proyecto",
                               return_value=False), \
             mock.patch.object(ui, "mostrar_estado") as estado, \
             mock.patch.object(ui, "preguntar_interactivo",
                               side_effect=AssertionError("no debe preguntar")):
            res = sc._advertencia_directorio_proyecto(args)
        self.assertIsNone(res)
        estado.assert_called_once()


if __name__ == "__main__":
    unittest.main()