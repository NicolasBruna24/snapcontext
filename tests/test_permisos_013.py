#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de permisos y confirmaciones (--confirmar) — v0.13.0."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc


class BasePermisos(unittest.TestCase):
    """Aísla CONFIG_DIR/PERMISOS_PATH en un directorio temporal."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp),
            mock.patch.object(sc, "PERMISOS_PATH",
                              self.dir_tmp / "permisos.json"),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        # Restaura el interruptor global tras cada test.
        restaurar = mock.patch.object(sc, "CONFIRMAR_ACCIONES", True)
        restaurar.start()
        self.addCleanup(restaurar.stop)


class TestPermisosPersistencia(BasePermisos):
    def test_cargar_sin_archivo_devuelve_vacio(self):
        self.assertEqual(sc._cargar_permisos(), {})

    def test_guardar_y_cargar(self):
        self.assertTrue(sc._guardar_permiso("ejecutar", "siempre"))
        datos = json.loads((self.dir_tmp / "permisos.json").read_text("utf-8"))
        self.assertEqual(datos, {"ejecutar": "siempre"})
        self.assertEqual(sc._cargar_permisos(), {"ejecutar": "siempre"})

    def test_limpiar_permisos(self):
        sc._guardar_permiso("editar", "nunca")
        self.assertTrue((self.dir_tmp / "permisos.json").exists())
        self.assertTrue(sc._limpiar_permisos())
        self.assertFalse((self.dir_tmp / "permisos.json").exists())
        self.assertTrue(sc._limpiar_permisos())   # sin archivo también ok

    def test_archivo_corrupto_devuelve_vacio(self):
        (self.dir_tmp / "permisos.json").write_text("{roto", encoding="utf-8")
        self.assertEqual(sc._cargar_permisos(), {})


class TestConfirmarAccion(BasePermisos):
    def test_desactivado_global_permite_sin_preguntar(self):
        sc.CONFIRMAR_ACCIONES = False
        with mock.patch("builtins.input") as fake_input:
            self.assertTrue(sc._confirmar_accion("borrar todo"))
        fake_input.assert_not_called()

    def test_confirmar_false_parametro_bypassa(self):
        with mock.patch("builtins.input") as fake_input:
            self.assertTrue(sc._confirmar_accion("x", confirmar=False))
        fake_input.assert_not_called()

    def test_respuesta_si_una_vez(self):
        with mock.patch("builtins.input", return_value="s"):
            self.assertTrue(sc._confirmar_accion("comando", tipo="ejecutar"))
        # 's' no persiste preferencias.
        self.assertEqual(sc._cargar_permisos(), {})

    def test_respuesta_no_salta(self):
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(sc._confirmar_accion("comando", tipo="ejecutar"))

    def test_respuesta_todos_persiste_siempre(self):
        with mock.patch("builtins.input", side_effect=["t"]):
            self.assertTrue(sc._confirmar_accion("cmd1", tipo="ejecutar"))
        self.assertEqual(sc._cargar_permisos(), {"ejecutar": "siempre"})
        # La siguiente llamada del mismo tipo NO pregunta.
        with mock.patch("builtins.input") as fake_input:
            self.assertTrue(sc._confirmar_accion("cmd2", tipo="ejecutar"))
        fake_input.assert_not_called()

    def test_respuesta_anular_persiste_nunca(self):
        with mock.patch("builtins.input", return_value="a"):
            self.assertFalse(sc._confirmar_accion("paso", tipo="editar"))
        self.assertEqual(sc._cargar_permisos(), {"editar": "nunca"})
        with mock.patch("builtins.input") as fake_input:
            self.assertFalse(sc._confirmar_accion("otro paso", tipo="editar"))
        fake_input.assert_not_called()

    def test_opcion_invalida_reintenta(self):
        with mock.patch("builtins.input", side_effect=["zzz", "s"]):
            self.assertTrue(sc._confirmar_accion("algo"))

    def test_eof_deniega_por_seguridad(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertFalse(sc._confirmar_accion("algo"))


class TestIntegracionPermisos(BasePermisos):
    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp2 = tempfile.TemporaryDirectory()
        self.dir_trabajo = Path(self.tmp2.name)
        self.addCleanup(self.tmp2.cleanup)

    def test_cmd_run_denegado_no_ejecuta(self):
        with mock.patch.object(sc, "_ejecutar_comando") as ej, \
             mock.patch("builtins.input", return_value="n"):
            sc._cmd_chat_run("cmd /c echo peligro", str(self.dir_trabajo))
        ej.assert_not_called()

    def test_cmd_run_permitido_ejecuta(self):
        with mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "salida", "")) as ej, \
             mock.patch("builtins.input", return_value="s"):
            sc._cmd_chat_run("cmd /c echo ok", str(self.dir_trabajo))
        ej.assert_called_once()

    def test_cmd_run_sin_confirmacion_global(self):
        sc.CONFIRMAR_ACCIONES = False
        with mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "", "")) as ej:
            sc._cmd_chat_run("cmd /c echo auto", str(self.dir_trabajo))
        ej.assert_called_once()

    def test_cmd_edit_denegado_no_abre_editor(self):
        destino = self.dir_trabajo / "archivo.txt"
        destino.write_text("hola", encoding="utf-8")
        with mock.patch.object(sc.subprocess, "Popen") as popen, \
             mock.patch("builtins.input", return_value="n"):
            sc._cmd_chat_edit(str(destino))
        popen.assert_not_called()

    def test_paso_plan_denegado_devuelve_fallo(self):
        args = sc.argparse.Namespace(consulta="tarea", confirmar=True)
        with mock.patch.object(sc, "_confirmar_accion", return_value=False):
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "ejecutar", "descripcion": "paso",
                 "comando": "cmd /c echo x"}, args, str(self.dir_trabajo))
        self.assertFalse(ok)
        self.assertEqual(detalle, "denegado por el usuario")

    def test_paso_plan_permitido_continua(self):
        args = sc.argparse.Namespace(consulta="tarea", confirmar=True)
        comando = ("cmd /c echo hola" if sys.platform.startswith("win")
                   else "echo hola")
        with mock.patch.object(sc, "_confirmar_accion", return_value=True):
            ok, _ = sc._ejecutar_paso_plan(
                {"accion": "ejecutar", "descripcion": "paso",
                 "comando": comando}, args, str(self.dir_trabajo))
        self.assertTrue(ok)

    def test_explore_no_pide_permiso(self):
        """`/explore` es solo lectura: nunca debe llamar a _confirmar_accion."""
        with mock.patch.object(sc, "_confirmar_accion") as conf, \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "", "")):
            sc._cmd_chat_explore("cualquier cosa", str(self.dir_trabajo))
        conf.assert_not_called()


class TestFlagsConfirmarCLI(unittest.TestCase):
    def _parse(self, argv):
        return sc.crear_parser().parse_args(argv)

    def test_confirmar_por_defecto_true(self):
        self.assertTrue(self._parse(["--plan", "x"]).confirmar)
        self.assertTrue(self._parse(["--chat"]).confirmar)

    def test_no_confirmar_desactiva(self):
        self.assertFalse(self._parse(["--plan", "x", "--no-confirmar"]).confirmar)
        self.assertFalse(self._parse(["--chat", "--no-confirmar"]).confirmar)

    def test_version_es_1_2_0(self):
        self.assertEqual(sc.VERSION, "4.2.0")


if __name__ == "__main__":
    unittest.main()

