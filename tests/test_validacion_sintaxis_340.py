#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la validación de sintaxis del Editor Propio (v3.4.0).

Cubre ``_validar_sintaxis`` (con mocks de subprocess para distintos lenguajes),
los flags ``--validar / --no-validar / --max-intentos-validacion``, el flujo de
reintentos de ``_aplicar_modo_sobrescribir`` y la vista previa de parches.
"""

import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import snapcontext as sc
from agentes import AgenteEditorPropio


class BaseValidacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc340_")
        self.raiz = Path(self.tmp).resolve()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def _proc(codigo, stderr="", stdout=""):
    return SimpleNamespace(returncode=codigo, stderr=stderr, stdout=stdout)


class TestValidarSintaxis(BaseValidacion):
    """_validar_sintaxis con mocks de lenguaje y subprocess."""

    def test_python_valido_devuelve_true(self):
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="python"), \
                mock.patch("snapcontext.shutil.which", return_value="/a/python"), \
                mock.patch("snapcontext.subprocess.run", return_value=_proc(0)) as run:
            ok, mensaje = sc._validar_sintaxis("modulo.py", "def f(): pass\n", self.tmp)
        self.assertTrue(ok)
        self.assertEqual(mensaje, "")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[1:3], ["-m", "py_compile"])

    def test_lenguaje_sin_validador_omite(self):
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="json"):
            ok, mensaje = sc._validar_sintaxis("datos.json", "{}", self.tmp)
        self.assertTrue(ok)
        self.assertEqual(mensaje, "")

    def test_comando_no_disponible_omite(self):
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="dart"), \
                mock.patch("snapcontext.shutil.which", return_value=None), \
                mock.patch("snapcontext.subprocess.run") as run:
            ok, mensaje = sc._validar_sintaxis("app.dart", "void main(){}\n", self.tmp)
        self.assertTrue(ok)
        self.assertEqual(mensaje, "")
        run.assert_not_called()

    def test_dart_fallback_analyze_indisponible(self):
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="dart"), \
                mock.patch("snapcontext.shutil.which",
                           side_effect=lambda b: b if b == "dart" else None), \
                mock.patch("snapcontext.subprocess.run",
                           side_effect=[FileNotFoundError, _proc(0)]) as run:
            ok, mensaje = sc._validar_sintaxis("lib/main.dart", "dart", self.tmp)
        self.assertTrue(ok)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1][0][0][0:2],
                         ["dart", "format"])

    def test_timeout_devuelve_false(self):
        exc = subprocess.TimeoutExpired("cmd", 60)
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="python"), \
                mock.patch("snapcontext.shutil.which", return_value="/a/python"), \
                mock.patch("snapcontext.subprocess.run", side_effect=exc):
            ok, mensaje = sc._validar_sintaxis("modulo.py", "x = 1\n", self.tmp)
        self.assertFalse(ok)
        self.assertIn("tiempo", mensaje.lower())

    def test_java_y_node_comandos(self):
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="java"), \
                mock.patch("snapcontext.shutil.which", return_value="/a/javac"), \
                mock.patch("snapcontext.subprocess.run", return_value=_proc(0)) as run:
            ok, _ = sc._validar_sintaxis("Main.java", "class Main {}", self.tmp)
        self.assertTrue(ok)
        self.assertEqual(run.call_args[0][0][0], "javac")

        with mock.patch.object(sc, "_lenguaje_archivo", return_value="javascript"), \
                mock.patch("snapcontext.shutil.which", return_value="/a/node"), \
                mock.patch("snapcontext.subprocess.run", return_value=_proc(0)) as run:
            ok, _ = sc._validar_sintaxis("script.js", "const x = 1;", self.tmp)
        self.assertTrue(ok)
        self.assertEqual(run.call_args[0][0][0], "node")

    def test_python_invalido_devuelve_false_y_mensaje(self):
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="python"), \
                mock.patch("snapcontext.shutil.which", return_value="/a/python"), \
                mock.patch("snapcontext.subprocess.run",
                           return_value=_proc(1, stderr="SyntaxError: invalid syntax")):
            ok, mensaje = sc._validar_sintaxis("modulo.py", "def (\n", self.tmp)
        self.assertFalse(ok)
        self.assertIn("SyntaxError", mensaje)
class TestFlagsValidacion(unittest.TestCase):
    def test_validar_por_defecto_true(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertTrue(args.validar)

    def test_no_validar_sintaxis_false(self):
        args = sc.crear_parser().parse_args(["consulta", "--no-validar-sintaxis"])
        self.assertFalse(args.validar)

    def test_validar_explicito_true(self):
        args = sc.crear_parser().parse_args(["consulta", "--validar"])
        self.assertTrue(args.validar)

    def test_max_intentos_validacion_defecto_y_personalizado(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertEqual(args.max_intentos_validacion, 3)
        args2 = sc.crear_parser().parse_args(
            ["consulta", "--max-intentos-validacion", "5"])
        self.assertEqual(args2.max_intentos_validacion, 5)
class TestReintentosSobrescribir(BaseValidacion):
    """Flujo de reintentos de validación en _aplicar_modo_sobrescribir."""

    def test_falla_y_luego_aprueba(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        (self.raiz / archivo).write_text("def f(): return 1\n", encoding="utf-8")
        with mock.patch.object(sc, "cargar_configuracion", return_value={}), \
                mock.patch.object(sc, "_enviar_al_proveedor",
                                  return_value="def f(): return 2\n") as enviar, \
                mock.patch.object(sc, "_validar_sintaxis",
                                  side_effect=[(False, "SyntaxError"), (True, "")]) as val, \
                mock.patch.object(agente, "sobrescribir", return_value=True) as sob:
            ok = agente._aplicar_modo_sobrescribir(
                archivo, "cambiar retorno", "def f(): return 1\n",
                modelo=None, directorio=str(self.raiz))
        self.assertTrue(ok)
        self.assertEqual(enviar.call_count, 2)   # 1ª generación + reintento
        self.assertEqual(val.call_count, 2)
        sob.assert_called_once_with(archivo, "def f(): return 2\n",
                                    str(self.raiz))

    def test_agota_intentos_cancela_sin_guardar(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        (self.raiz / archivo).write_text("x = 1\n", encoding="utf-8")
        with mock.patch.object(sc, "cargar_configuracion", return_value={}), \
                mock.patch.object(sc, "_enviar_al_proveedor",
                                  return_value="malo()(") as enviar, \
                mock.patch.object(sc, "_validar_sintaxis",
                                  return_value=(False, "SyntaxError")) as val, \
                mock.patch.object(agente, "sobrescribir") as sob:
            ok = agente._aplicar_modo_sobrescribir(
                archivo, "cambia", "x = 1\n", modelo=None,
                directorio=self.tmp, max_intentos_validacion=3)
        self.assertFalse(ok)
        self.assertEqual(enviar.call_count, 3)
        self.assertEqual(val.call_count, 3)
        sob.assert_not_called()

    def test_validar_false_no_valida(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        with mock.patch.object(sc, "cargar_configuracion", return_value={}), \
                mock.patch.object(sc, "_enviar_al_proveedor",
                                  return_value="x = 1\n") as enviar, \
                mock.patch.object(sc, "_validar_sintaxis") as val, \
                mock.patch.object(agente, "sobrescribir", return_value=True) as sob:
            ok = agente._aplicar_modo_sobrescribir(
                archivo, "cambiar", "", modelo=None, directorio=self.tmp,
                validar=False)
        self.assertTrue(ok)
        self.assertEqual(enviar.call_count, 1)
        val.assert_not_called()
        sob.assert_called_once()


class TestPreviewParche(BaseValidacion):
    def test_preview_aplica_hunk(self):
        agente = AgenteEditorPropio()
        parche = ("--- a/modulo.py\n+++ b/modulo.py\n@@ -1 +1 @@\n"
                  "-def fn(): return 1\n+def fn(): return 2\n")
        resultado, aplicados = agente._aplicar_parche_preview(
            parche, "def fn(): return 1\n")
        self.assertEqual(aplicados, 1)
        self.assertIn("def fn(): return 2", resultado)


if __name__ == "__main__":
    unittest.main()