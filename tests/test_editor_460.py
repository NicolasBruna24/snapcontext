#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests v4.6.0: editor transaccional (rollback), backup obligatorio y
fuzzy matching en la aplicación incremental de parches."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agentes as ag
import snapcontext as sc


class TestRollbackMultiarchivo(unittest.TestCase):
    """v4.6.0: si un archivo falla, TODOS vuelven a su estado original."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc460roll_")
        self.contenidos = {"a.py": "print('a')\n",
                           "b.py": "print('b')\n",
                           "c.py": "print('c')\n"}
        for nombre, contenido in self.contenidos.items():
            (Path(self.tmp) / nombre).write_text(contenido, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fallo_en_segundo_archivo_revierte_el_primero(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(ag.AgenteEditorPropio,
                               "_editar_archivo_en_cadena",
                               side_effect=[True, False]) as editar:
            ok = editor.ejecutar(["a.py", "b.py"], "tarea",
                                 directorio=self.tmp)
        self.assertFalse(ok)
        self.assertEqual(editar.call_count, 2)
        for nombre, contenido in self.contenidos.items():
            self.assertEqual(
                (Path(self.tmp) / nombre).read_text(encoding="utf-8"),
                contenido)

    def test_excepcion_dispara_rollback(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(ag.AgenteEditorPropio,
                               "_editar_archivo_en_cadena",
                               side_effect=[True, RuntimeError("boom")]):
            ok = editor.ejecutar(["a.py", "b.py"], "tarea",
                                 directorio=self.tmp)
        self.assertFalse(ok)
        for nombre, contenido in self.contenidos.items():
            self.assertEqual(
                (Path(self.tmp) / nombre).read_text(encoding="utf-8"),
                contenido)

    def test_exito_no_hace_rollback(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(ag.AgenteEditorPropio,
                               "_editar_archivo_en_cadena",
                               return_value=True):
            ok = editor.ejecutar(["a.py", "b.py", "c.py"], "tarea",
                                 directorio=self.tmp)
        self.assertTrue(ok)
        for nombre, contenido in self.contenidos.items():
            self.assertEqual(
                (Path(self.tmp) / nombre).read_text(encoding="utf-8"),
                contenido)


class TestBackupBloqueante(unittest.TestCase):
    """v4.6.0: si el backup falla, la edición se aborta sin escribir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc460bkp_")
        self.backups = Path(self.tmp) / "_backups"
        self.patch_backups = mock.patch.object(sc, "BACKUPS_DIR", self.backups)
        self.patch_backups.start()

    def tearDown(self):
        self.patch_backups.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_que_falla_aborta_la_edicion(self):
        destino = Path(self.tmp) / "modulo.py"
        destino.write_text("version original\n", encoding="utf-8")
        with mock.patch.object(sc.shutil, "copy2",
                               side_effect=OSError("disco lleno")):
            ok = sc._editor_sobrescribir("modulo.py", "version nueva\n",
                                         directorio=self.tmp)
        self.assertFalse(ok)
        # Nunca se escribe sin backup: el archivo conserva el original.
        self.assertEqual(destino.read_text(encoding="utf-8"),
                         "version original\n")

    def test_backup_ok_permite_escribir(self):
        destino = Path(self.tmp) / "modulo.py"
        destino.write_text("version original\n", encoding="utf-8")
        ok = sc._editor_sobrescribir("modulo.py", "version nueva\n",
                                     directorio=self.tmp)
        self.assertTrue(ok)
        self.assertEqual(destino.read_text(encoding="utf-8"), "version nueva\n")
        self.assertEqual(len(list(self.backups.glob("*_modulo.py"))), 1)


class TestFuzzyMatching(unittest.TestCase):
    """v4.6.0: la resolución incremental tolera variaciones menores."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc460fz_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _escribir(self, nombre: str, contenido: str) -> str:
        (Path(self.tmp) / nombre).write_text(contenido, encoding="utf-8")
        return nombre

    def test_cambio_menor_en_contexto_no_rompe_el_hunk(self):
        # El LLM generó el parche contra esta versión base...
        base = ("def calc():\n"
                "    valores = [1, 2]\n"
                "    total = calcular_suma(valores, impuestos)\n"
                "    return total\n")
        nuevo = base.replace("    return total\n", "    return total + 1\n")
        # ...pero el usuario renombró una variable en una línea de contexto
        # (cambio menor que antes rompía el hunk por igualdad estricta).
        real = base.replace("calcular_suma(valores,", "calcular_suma(valores2,")
        nombre = self._escribir("f.py", real)
        parche = sc._generar_parche(base, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = (Path(self.tmp) / nombre).read_text(encoding="utf-8")
        self.assertIn("    return total + 1\n", resultado)
        # El cambio local del usuario se preserva.
        self.assertIn("calcular_suma(valores2,", resultado)

    def test_hunk_irrecuperable_aborta_sin_estado_mixto(self):
        original = "linea 1\nlinea 2\nlinea 3\n"
        nombre = self._escribir("g.py", original)
        # Dos hunks: el primero aplicaría, el segundo es totalmente ajeno.
        parche = ("--- a/g.py\n+++ b/g.py\n"
                  "@@ -1 +1 @@\n-linea 1\n+LINEA UNO\n"
                  "@@ -1 +1 @@\n-totalmente distinto\n+NADA QUE VER\n")
        self.assertFalse(sc._aplicar_hunks_incremental(parche, self.tmp))
        # El archivo queda intacto (todo-o-nada, sin aplicación parcial).
        self.assertEqual(
            (Path(self.tmp) / nombre).read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
