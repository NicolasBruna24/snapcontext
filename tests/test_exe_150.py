#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v1.5.0: empaquetado .exe (PyInstaller + NSIS)."""

import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import snapcontext as sc  # noqa: E402


class TestVersion(unittest.TestCase):
    def test_version_150_coherente(self):
        self.assertEqual(sc.VERSION, "2.0.0")


class TestEstaticoWeb(unittest.TestCase):
    """El estático de la web debe resolverse igual en dev y 'frozen' (exe)."""

    def test_estatico_existe_en_desarrollo(self):
        # web/app.py resuelve web/static tanto con __file__ como con
        # sys._MEIPASS; en desarrollo debe existir el index.html real.
        try:
            from web import app as app_web
        except ImportError:                      # fastapi no instalada
            self.skipTest("web (fastapi) no instalada")
        self.assertTrue((app_web._ESTATICO / "index.html").is_file(),
                        f"No se encontró: {app_web._ESTATICO}")

    def test_frozen_usa_meipass(self):
        try:
            from web import app as app_web
        except ImportError:                      # fastapi no instalada
            self.skipTest("web (fastapi) no instalada")
        if getattr(sys, "frozen", False):
            self.assertIn("_MEIPASS", repr(app_web._ESTATICO))
        else:
            self.assertNotIn("_MEIPASS", str(app_web._ESTATICO))


class TestArtefactosEmpaquetado(unittest.TestCase):
    """snapcontext.spec, installer.nsi y scripts existen y son coherentes."""

    def test_spec_existe_y_empaqueta_el_estatico(self):
        spec = RAIZ / "snapcontext.spec"
        contenido = spec.read_text(encoding="utf-8")
        self.assertIn("web/static/index.html", contenido)
        self.assertIn('name="snapcontext"', contenido)
        self.assertIn("SNAPCONTEXT_EXE_FULL", contenido)   # modo full opcional

    def test_nsi_existe_con_path_y_uninstall(self):
        nsi = (RAIZ / "installer.nsi").read_text(encoding="utf-8")
        self.assertIn("OutFile", nsi)
        self.assertIn("WriteRegExpandStr HKCU \"Environment\" \"Path\"",
                      nsi)
        self.assertIn("CreateShortCut", nsi)
        self.assertIn("Section \"Uninstall\"", nsi)

    def test_scripts_de_empaquetado_existen(self):
        self.assertTrue((RAIZ / "scripts" / "empaquetar_exe.ps1").is_file())
        self.assertTrue((RAIZ / "scripts" / "empaquetar_exe.sh").is_file())
        ps1 = (RAIZ / "scripts" / "empaquetar_exe.ps1").read_text(
            encoding="utf-8")
        self.assertIn("pyinstaller snapcontext.spec", ps1)
        self.assertIn("makensis installer.nsi", ps1)


if __name__ == "__main__":
    unittest.main()
