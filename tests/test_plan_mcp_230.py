#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v2.3.0: pasos MCP en el planificador, condiciones dinÃ¡micas,
contexto de resultados ({{resultado}}) y paralelismo con dependencias
dinÃ¡micas."""

import argparse
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc  # noqa: E402


def _args_base(extra=None):
    base = {"plan": True, "consulta": "tarea", "auto": True,
            "paralelo": 1, "git_commit": False, "confirmar": False,
            "branch": None, "modelo": None, "depurar": False}
    base.update(extra or {})
    return argparse.Namespace(**base)


class TestContextoPlan(unittest.TestCase):
    def setUp(self):
        sc._contexto_plan_reiniciar()

    tearDown = setUp

    def test_variable_y_resultado(self):
        sc._contexto_plan_variable("salida", {"stdout": "hola"})
        self.assertEqual(sc._CONTEXTO_PLAN["variables"]["resultado"],
                         {"stdout": "hola"})
        self.assertEqual(sc._CONTEXTO_PLAN["variables"]["salida"],
                         {"stdout": "hola"})

    def test_reiniciar(self):
        sc._contexto_plan_variable("x", 1)
        sc._registrar_resultado_plan(1, True, "ok")
        sc._contexto_plan_reiniciar()
        self.assertEqual(sc._CONTEXTO_PLAN["variables"], {})
        self.assertEqual(sc._CONTEXTO_PLAN["pasos"], {})

    def test_resolver_marcadores(self):
        sc._contexto_plan_variable("nombre", "main")
        self.assertEqual(sc._resolver_marcadores("rama {{nombre}}"),
                         "rama main")
        # clave desconocida: se deja tal cual
        self.assertEqual(sc._resolver_marcadores("{{nadie}}"), "{{nadie}}")
        # no-string pasa intacto
        self.assertEqual(sc._resolver_marcadores(42), 42)

    def test_registrar_resultado(self):
        sc._registrar_resultado_plan(2, True, "bien")
        self.assertEqual(sc._CONTEXTO_PLAN["pasos"]["2"]["resultado"], "ok")
        sc._registrar_resultado_plan(3, False, "mal")
        self.assertEqual(sc._CONTEXTO_PLAN["pasos"]["3"]["resultado"],
                         "fallo")


class TestNormalizarPasosMCP(unittest.TestCase):
    def test_paso_mcp_completo(self):
        pasos = sc._normalizar_pasos({"pasos": [{
            "descripcion": "buscar main", "accion": "mcp",
            "herramienta": "grep", "args": {"patron": "def main"},
            "variable": "coincidencias", "dependencias": [1]}]})
        self.assertEqual(len(pasos), 1)
        paso = pasos[0]
        self.assertEqual(paso["accion"], "mcp")
        self.assertEqual(paso["herramienta"], "grep")
        self.assertEqual(paso["args"], {"patron": "def main"})
        self.assertEqual(paso["variable"], "coincidencias")

    def test_args_no_dict_se_descarta(self):
        pasos = sc._normalizar_pasos([
            {"descripcion": "p", "accion": "mcp", "herramienta": "grep",
             "args": "no-soy-dict"}])
        self.assertEqual(pasos[0]["args"], {})

    def test_accion_mcp_es_valida(self):
        self.assertIn("mcp", sc.ACCIONES_VALIDAS)


class TestCondicionesDinamicas(unittest.TestCase):
    def setUp(self):
        sc._contexto_plan_reiniciar()

    tearDown = setUp

    def test_comparacion_pasos_ok(self):
        sc._registrar_resultado_plan(1, True, "hecho")
        self.assertTrue(sc._evaluar_condicion("pasos[1].resultado == 'ok'"))
        self.assertFalse(
            sc._evaluar_condicion("pasos[1].resultado == 'fallo'"))
        self.assertTrue(
            sc._evaluar_condicion("pasos[1].resultado != 'fallo'"))

    def test_paso_inexistente_es_false(self):
        self.assertFalse(sc._evaluar_condicion("pasos[9].resultado == 'ok'"))

    def test_variables_de_contexto(self):
        sc._contexto_plan_variable("estado", "listo")
        self.assertTrue(sc._evaluar_condicion("resultados.estado == 'listo'"))
        self.assertTrue(sc._evaluar_condicion("estado != ''"))
        self.assertFalse(sc._evaluar_condicion("estado == 'otro'"))

    def test_variable_existe(self):
        sc._contexto_plan_variable("dato", {"stdout": "x"})
        self.assertTrue(sc._evaluar_condicion("variable_existe('dato')"))
        self.assertFalse(sc._evaluar_condicion("variable_existe('falta')"))

    def test_formas_clasicas_sigue_funcionando(self):
        tmp = tempfile.mkdtemp()
        try:
            ruta = os.path.join(tmp, "mini.txt")
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write("contenido util")
            self.assertTrue(sc._evaluar_condicion(
                "archivo_existe('mini.txt')", tmp))
            self.assertTrue(sc._evaluar_condicion(
                "archivo_contiene('mini.txt', 'util')", tmp))
            self.assertFalse(
                sc._evaluar_condicion("archivo_existe('nada.txt')", tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_refs_de_condicion(self):
        indices, nombres = sc._refs_de_condicion(
            "pasos[2].resultado == 'ok'")
        self.assertEqual(indices, {1})
        nombres2 = sc._refs_de_condicion("resultados.salida != ''")[1]
        self.assertIn("salida", nombres2)


class TestPasoPlanMCP(unittest.TestCase):
    def setUp(self):
        sc._contexto_plan_reiniciar()
        self.dir_tmp = tempfile.mkdtemp()

    def tearDown(self):
        sc._contexto_plan_reiniciar()
        shutil.rmtree(self.dir_tmp, ignore_errors=True)

    def test_ejecutar_herramienta_mcp_ok(self):
        paso = {"descripcion": "listar archivos", "accion": "mcp",
                "herramienta": "list_files",
                "args": {"max_archivos": 5}, "variable": "archivos"}
        resultado = {"ok": True, "archivos": ["a.py"]}
        with mock.patch.object(
                sc, "_ejecutar_herramienta_mcp",
                return_value={"ok": True, "herramienta": "list_files",
                              "resultado": resultado}) as ejecutora:
            ok, detalle = sc._ejecutar_paso_plan(
                paso, _args_base(), self.dir_tmp)
        self.assertTrue(ok)
        self.assertIn("list_files", detalle)
        ejecutora.assert_called_once_with("list_files", {"max_archivos": 5})
        self.assertEqual(sc._CONTEXTO_PLAN["variables"]["archivos"],
                         resultado)
        self.assertEqual(sc._CONTEXTO_PLAN["variables"]["resultado"],
                         resultado)

    def test_ejecutar_herramienta_mcp_fallo(self):
        paso = {"descripcion": "romper", "accion": "mcp",
                "herramienta": "execute_command", "args": {}, "variable": ""}
        with mock.patch.object(
                sc, "_ejecutar_herramienta_mcp",
                return_value={"ok": False,
                              "resultado": {"error": "comando invalido"}}):
            ok, _ = sc._ejecutar_paso_plan(paso, _args_base(), self.dir_tmp)
        self.assertFalse(ok)

    def test_mcp_sin_herramienta_falla(self):
        ok, detalle = sc._ejecutar_paso_plan(
            {"descripcion": "vacio", "accion": "mcp", "args": {},
             "variable": ""},
            _args_base(), self.dir_tmp)
        self.assertFalse(ok)
        self.assertIn("herramienta", detalle)


class TestEjecutarToolExecuteCommand(unittest.TestCase):
    """execute_command ya soporta background y capture_output."""

    def test_firma_acepta_background_y_capture(self):
        import inspect
        firma = inspect.signature(sc._tool_execute_command)
        self.assertIn("background", firma.parameters)
        self.assertIn("capture_output", firma.parameters)
        self.assertIs(firma.parameters["capture_output"].default, True)

    def test_ejecuta_y_devuelve_salida(self):
        comando = ("cmd /c echo hola" if sys.platform.startswith("win")
                   else "echo hola")
        res = sc._tool_execute_command(comando, ".")
        self.assertTrue(res["ok"])
        self.assertIn("hola", res.get("stdout", ""))

    def test_background_devuelve_pid(self):
        with mock.patch.object(sc, "_lanzar_proceso_fondo",
                               return_value={"ok": True, "pid": 4242}):
            res = sc._tool_execute_command("algo", ".", background=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["pid"], 4242)


class TestVersionYFlags(unittest.TestCase):
    def test_version_230(self):
        self.assertEqual(sc.VERSION, "6.3.0")

    def test_flag_paralelo(self):
        args = sc.crear_parser().parse_args(["--plan", "t", "--paralelo", "3"])
        self.assertEqual(args.paralelo, 3)


if __name__ == "__main__":
    unittest.main()


