#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de las funcionalidades de SnapContext v2.0.0 (Editor propio y optimizaciones)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapcontext as sc
from agentes import AgenteEditorPropio
from orquestador import Orquestador


class TestEditorPropio(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.raiz = Path(self.tmp_dir).resolve()
        self.backups_dir = self.raiz / ".snapcontext_backups"
        self.patch_backup = mock.patch.object(sc, "BACKUPS_DIR", self.backups_dir)
        self.patch_backup.start()

    def tearDown(self):
        self.patch_backup.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_sobrescribir_archivo_nuevo(self):
        archivo = "src/nuevo.py"
        contenido = "print('hola v2.0.0')\n"
        ok = sc._editor_sobrescribir(archivo, contenido, directorio=str(self.raiz))
        self.assertTrue(ok)
        destino = self.raiz / "src" / "nuevo.py"
        self.assertTrue(destino.is_file())
        self.assertEqual(destino.read_text(encoding="utf-8"), contenido)

    def test_sobrescribir_archivo_existente_con_backup(self):
        archivo = "lib/main.dart"
        destino = self.raiz / "lib" / "main.dart"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text("void main() { print('v1'); }", encoding="utf-8")

        nuevo_contenido = "void main() { print('v2'); }"
        ok = sc._editor_sobrescribir(archivo, nuevo_contenido, directorio=str(self.raiz))
        self.assertTrue(ok)
        self.assertEqual(destino.read_text(encoding="utf-8"), nuevo_contenido)

        # Verificar que se creó el backup
        backups = list(self.backups_dir.glob("*_main.dart"))
        self.assertGreaterEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "void main() { print('v1'); }")

    def test_ruta_fuera_del_repo_bloqueada(self):
        ok = sc._editor_sobrescribir("../secreto.txt", "hacked", directorio=str(self.raiz))
        self.assertFalse(ok)

    def test_ruta_vacia_bloqueada(self):
        self.assertFalse(sc._editor_sobrescribir("", "contenido", directorio=str(self.raiz)))
        self.assertFalse(sc._editor_sobrescribir("   ", "contenido", directorio=str(self.raiz)))

    def test_agente_editor_propio(self):
        agente = AgenteEditorPropio()
        archivo = "test.txt"
        ok = agente.sobrescribir(archivo, "prueba agente", directorio=str(self.raiz))
        self.assertTrue(ok)
        self.assertEqual((self.raiz / archivo).read_text(encoding="utf-8"), "prueba agente")


class TestFlagsEditor(unittest.TestCase):
    def test_editor_defecto_es_aider(self):
        parser = sc.crear_parser()
        args = parser.parse_args(["consulta"])
        self.assertEqual(args.editor, "aider")

    def test_editor_propio_aceptado(self):
        parser = sc.crear_parser()
        args = parser.parse_args(["--editor", "propio", "consulta"])
        self.assertEqual(args.editor, "propio")

    def test_orquestador_incluye_editor_propio(self):
        orch = Orquestador()
        self.assertIsNotNone(getattr(orch, "agente_editor_propio", None))
        self.assertIsInstance(orch.agente_editor_propio, AgenteEditorPropio)

    def test_version_es_2_0_0(self):
        self.assertEqual(sc.VERSION, "3.3.0")


if __name__ == "__main__":
    unittest.main()
