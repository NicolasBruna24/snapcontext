#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la Fase 2 del Editor Propio (Diffs y Parches Unificados) â€” v2.1.0."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapcontext as sc
from agentes import AgenteEditorPropio


class TestDiffYParches(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.raiz = Path(self.tmp_dir).resolve()
        self.backups_dir = self.raiz / ".snapcontext_backups"
        self.patch_backup = mock.patch.object(sc, "BACKUPS_DIR", self.backups_dir)
        self.patch_backup.start()

    def tearDown(self):
        self.patch_backup.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_generar_parche_unificado(self):
        orig = "linea 1\nlinea 2\nlinea 3\n"
        nuevo = "linea 1\nlinea 2 modificada\nlinea 3\n"
        parche = sc._generar_parche(orig, nuevo, "src/archivo.py")
        self.assertIn("--- a/src/archivo.py", parche)
        self.assertIn("+++ b/src/archivo.py", parche)
        self.assertIn("-linea 2", parche)
        self.assertIn("+linea 2 modificada", parche)

    def test_aplicar_parche_vacio_devuelve_false(self):
        self.assertFalse(sc._aplicar_parche("", str(self.raiz)))
        self.assertFalse(sc._aplicar_parche("   ", str(self.raiz)))

    def test_aplicar_parche_con_git_mock(self):
        parche = "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-hola\n+mundo\n"
        proc_mock = mock.MagicMock(returncode=0, stderr="")
        with mock.patch("shutil.which", return_value="git"), \
             mock.patch("subprocess.run", return_value=proc_mock) as sub_mock:
            ok = sc._aplicar_parche(parche, str(self.raiz))
            self.assertTrue(ok)
            sub_mock.assert_called_once()
            cmd_args = sub_mock.call_args[0][0]
            self.assertEqual(cmd_args[:2], ["git", "apply"])

    def test_aplicar_parche_fallback_patch_mock(self):
        parche = "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-hola\n+mundo\n"
        proc_fail = mock.MagicMock(returncode=1, stderr="error git")
        proc_ok = mock.MagicMock(returncode=0, stderr="")
        with mock.patch("shutil.which", side_effect=lambda x: x if x in ("git", "patch") else None), \
             mock.patch("subprocess.run", side_effect=[proc_fail, proc_ok]) as sub_mock:
            ok = sc._aplicar_parche(parche, str(self.raiz))
            self.assertTrue(ok)
            self.assertEqual(sub_mock.call_count, 2)

    def test_ejecutar_modo_parche_exitoso(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        (self.raiz / archivo).write_text("def fn(): return 1\n", encoding="utf-8")
        diff_simulado = "--- a/modulo.py\n+++ b/modulo.py\n@@ -1 +1 @@\n-def fn(): return 1\n+def fn(): return 2\n"

        with mock.patch.object(sc, "_enviar_al_proveedor", return_value=diff_simulado), \
             mock.patch.object(agente, "aplicar_parche", return_value=True) as ap_mock:
            ok = agente.ejecutar([archivo], "cambiar retorno a 2", directorio=str(self.raiz), modo_edicion="parche")
            self.assertTrue(ok)
            ap_mock.assert_called_once()

    def test_ejecutar_fallback_a_sobrescritura(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        (self.raiz / archivo).write_text("def fn(): return 1\n", encoding="utf-8")
        # Proveedor devuelve cÃ³digo completo en lugar de diff
        codigo_completo = "def fn(): return 2\n"

        with mock.patch.object(sc, "_enviar_al_proveedor", side_effect=["no es un diff", codigo_completo]), \
             mock.patch.object(sc, "_skill_editor_estrategia", return_value=None), \
             mock.patch.object(sc, "_skill_editor_guardar", return_value=None), \
             mock.patch.object(agente, "sobrescribir", return_value=True) as sob_mock:
            ok = agente.ejecutar([archivo], "cambiar retorno a 2", directorio=str(self.raiz), modo_edicion="auto")
            self.assertTrue(ok)
            sob_mock.assert_called_once_with(archivo, codigo_completo, str(self.raiz))


class TestFlagsEdicion(unittest.TestCase):
    def test_flag_modo_edicion_por_defecto_auto(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertEqual(args.modo_edicion, "auto")

    def test_flag_modo_edicion_parche_y_sobrescribir(self):
        parser = sc.crear_parser()
        self.assertEqual(parser.parse_args(["--modo-edicion", "parche", "c"]).modo_edicion, "parche")
        self.assertEqual(parser.parse_args(["--modo-edicion", "sobrescribir", "c"]).modo_edicion, "sobrescribir")

    def test_version_es_2_1_0(self):
        self.assertEqual(sc.VERSION, "6.1.0")


if __name__ == "__main__":
    unittest.main()
