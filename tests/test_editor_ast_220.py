#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la Fase 3 del Editor Propio (Edición basada en AST) — v2.2.0."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapcontext as sc
from agentes import AgenteEditorAST, AgenteEditorPropio, _tarea_estructura


class TestEditorAST(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.raiz = Path(self.tmp_dir).resolve()
        self.backups_dir = self.raiz / ".snapcontext_backups"
        self.patch_backup = mock.patch.object(sc, "BACKUPS_DIR", self.backups_dir)
        self.patch_backup.start()

    def tearDown(self):
        self.patch_backup.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _escribir(self, nombre, contenido):
        destino = self.raiz / nombre
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8")
        return nombre

    def test_ast_disponible_python(self):
        self.assertTrue(sc._ast_disponible("src/modulo.py"))
        # .txt no tiene analizador AST (aunque tree-sitter esté instalado)
        self.assertFalse(sc._ast_disponible("modulo.txt"))
        self.assertFalse(sc._ast_disponible(""))

    def test_resumen_ast_python(self):
        contenido = (
            "import os\n"
            "from math import sqrt\n"
            "class Foo:\n"
            "    def bar(self):\n"
            "        return 1\n"
            "def foo():\n"
            "    x = 10\n"
            "    return x\n"
        )
        resumen = sc._resumen_ast(contenido, "modulo.py")
        self.assertTrue(resumen["ok"])
        self.assertEqual(resumen["motor"], "ast")
        nombres_funciones = [f["nombre"] for f in resumen["funciones"]]
        self.assertIn("foo", nombres_funciones)
        self.assertIn("bar", nombres_funciones)
        nombres_clases = [c["nombre"] for c in resumen["clases"]]
        self.assertIn("Foo", nombres_clases)
        self.assertTrue(any(i["tipo"] == "import" for i in resumen["imports"]))
        self.assertTrue(any(i["tipo"] == "from" for i in resumen["imports"]))

    def test_renombrar_identificador(self):
        nuevo = sc._renombrar_identificador(
            "def foo():\n    return foo\n", "foo", "bar")
        self.assertIn("def bar", nuevo)
        self.assertIn("return bar", nuevo)
        self.assertNotIn("foo", nuevo)

    def test_interpretar_operaciones_json_y_fenced(self):
        ops = sc._interpretar_operaciones_ast(
            '```json\n[{"tipo": "renombrar", "nombre": "x", "nuevo": "y"}]\n```')
        self.assertEqual(ops[0]["tipo"], "renombrar")
        # Sin JSON → se trata como código completo
        ops2 = sc._interpretar_operaciones_ast("def fn(): return 1\n")
        self.assertEqual(ops2[0]["tipo"], "completo")

    def test_editor_ast_renombrar(self):
        archivo = self._escribir(
            "modulo.py", "def foo():\n    x = 1\n    return foo(x)\n")
        with mock.patch.object(
                sc, "_enviar_al_proveedor",
                return_value='[{"tipo": "renombrar", "nombre": "foo", "nuevo": "bar"}]'):
            ok = sc._editor_ast(archivo, "renombrar foo a bar",
                                directorio=str(self.raiz))
        self.assertTrue(ok)
        contenido = (self.raiz / archivo).read_text(encoding="utf-8")
        self.assertIn("def bar", contenido)
        self.assertIn("bar(x)", contenido)
        self.assertNotIn("foo", contenido)

    def test_editor_ast_completo(self):
        archivo = self._escribir("modulo.py", "def fn():\n    return 1\n")
        with mock.patch.object(
                sc, "_enviar_al_proveedor",
                return_value='[{"tipo": "completo", "codigo": "def fn(): return 2\\n"}]'):
            ok = sc._editor_ast(archivo, "cambiar retorno a 2",
                                directorio=str(self.raiz))
        self.assertTrue(ok)
        contenido = (self.raiz / archivo).read_text(encoding="utf-8")
        self.assertIn("def fn(): return 2", contenido)

    def test_editor_ast_archivo_no_existe(self):
        self.assertFalse(sc._editor_ast("no_existe.py", "tarea",
                                        directorio=str(self.raiz)))

    def test_editor_ast_sin_ast_disponible(self):
        # .txt no tiene analizador AST → delega (devuelve False)
        self._escribir("notas.txt", "hola")
        self.assertFalse(sc._editor_ast("notas.txt", "tarea",
                                        directorio=str(self.raiz)))

    def test_editor_ast_respuesta_vacia(self):
        archivo = self._escribir("modulo.py", "def fn():\n    return 1\n")
        with mock.patch.object(sc, "_enviar_al_proveedor", return_value=""):
            ok = sc._editor_ast(archivo, "tarea", directorio=str(self.raiz))
        self.assertFalse(ok)
class TestAgenteEditorAST(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.raiz = Path(self.tmp_dir).resolve()
        self.backups_dir = self.raiz / ".snapcontext_backups"
        self.patch_backup = mock.patch.object(sc, "BACKUPS_DIR", self.backups_dir)
        self.patch_backup.start()

    def tearDown(self):
        self.patch_backup.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_clase_agente_editor_ast(self):
        agente = AgenteEditorAST()
        self.assertTrue(hasattr(agente, "editar"))
        self.assertTrue(hasattr(agente, "editar_archivo"))

    def test_ejecutar_agente_propio_modo_ast_fallback(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        (self.raiz / archivo).write_text("def fn(): return 1\n", encoding="utf-8")
        with mock.patch.object(sc, "_editor_ast", return_value=False), \
             mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="def fn(): return 2\n"), \
             mock.patch.object(agente, "sobrescribir", return_value=True) as sob_mock:
            ok = agente.ejecutar([archivo], "cambiar retorno a 2",
                                 directorio=str(self.raiz), modo_edicion="ast")
        self.assertTrue(ok)
        sob_mock.assert_called_once_with(archivo, "def fn(): return 2\n",
                                         str(self.raiz))

    def test_cadena_modos(self):
        agente = AgenteEditorPropio()
        self.assertEqual(agente._cadena_modos("m.py", "x", "sobrescribir"),
                         ["sobrescribir"])
        self.assertEqual(agente._cadena_modos("m.py", "x", "parche"), ["parche"])
        self.assertEqual(agente._cadena_modos("m.py", "x", "ast"),
                         ["ast", "sobrescribir"])
        # auto: estructural → AST primero
        self.assertEqual(agente._cadena_modos("m.py", "renombrar la funcion", "auto"),
                         ["ast", "parche", "sobrescribir"])
        # auto: tarea simple → parche → sobrescritura
        self.assertEqual(agente._cadena_modos("m.py", "cambiar retorno a 2", "auto"),
                         ["parche", "sobrescribir"])

    def test_tarea_estructura(self):
        self.assertTrue(_tarea_estructura("renombra la variable foo"))
        self.assertTrue(_tarea_estructura("refactoriza la funcion"))
        self.assertFalse(_tarea_estructura("cambiar retorno a 2"))


class TestFlagsEdicionAST(unittest.TestCase):
    def test_flag_modo_edicion_ast(self):
        parser = sc.crear_parser()
        args = parser.parse_args(["--modo-edicion", "ast", "consulta"])
        self.assertEqual(args.modo_edicion, "ast")
        # El valor por defecto sigue siendo 'auto'
        args2 = parser.parse_args(["consulta"])
        self.assertEqual(args2.modo_edicion, "auto")

    def test_version_es_2_2_0(self):
        self.assertEqual(sc.VERSION, "6.17.0")


if __name__ == "__main__":
    unittest.main()
