#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v1.7.0: extensión JetBrains (estructura y coherencia)."""

import json
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
JB = RAIZ / "jetbrains"
sys.path.insert(0, str(RAIZ))

import snapcontext as sc  # noqa: E402


class TestVersion(unittest.TestCase):
    def test_version_170_coherente(self):
        self.assertEqual(sc.VERSION, "2.3.0")


class TestEstructuraJetBrains(unittest.TestCase):
    """Ficheros imprescindibles de la extensión."""

    def test_ficheros_principales(self):
        for relativo in ("build.gradle.kts", "settings.gradle.kts",
                         "gradle.properties",
                         "src/main/resources/META-INF/plugin.xml"):
            self.assertTrue((JB / relativo).is_file(), relativo)

    def test_kotlin_sources(self):
        src = JB / "src" / "main" / "kotlin" / "com" / "snapcontext" / "jetbrains"
        for archivo in ("SnapContextService.kt", "SnapContextSettings.kt",
                        "SnapContextConfigurable.kt", "ConsolaHolder.kt",
                        "SnapContextToolWindowFactory.kt",
                        "SnapActions.kt", "SnapContextActions.kt"):
            self.assertTrue((src / archivo).is_file(), archivo)


class TestPluginXml(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raiz = ET.parse(JB / "src/main/resources/META-INF/plugin.xml").getroot()

    def test_identidad(self):
        self.assertEqual(self.raiz.findtext("id"), "com.snapcontext.jetbrains")
        self.assertEqual(self.raiz.findtext("name"), "SnapContext")

    def test_depende_de_platform(self):
        depende = [d.text for d in self.raiz.findall("depends")]
        self.assertIn("com.intellij.modules.platform", depende)

    def test_toolwindow_registrada(self):
        extensiones = self.raiz.find("extensions")
        tws = [e for e in extensiones.findall("toolWindow")
               if e.get("id") == "SnapContext"]
        self.assertEqual(len(tws), 1)
        self.assertEqual(tws[0].get("factoryClass"),
                         "com.snapcontext.jetbrains.SnapContextToolWindowFactory")

    def test_configurable_registrado(self):
        extensiones = self.raiz.find("extensions")
        configs = [e for e in extensiones.findall("applicationConfigurable")
                   if e.get("instance") == "com.snapcontext.jetbrains."
                                         "SnapContextConfigurable"]
        self.assertEqual(len(configs), 1)

    def test_acciones_principales(self):
        ids = {a.get("id") for a in self.raiz.iter("action")}
        for esperado in ("SnapContext.EjecutarConsulta", "SnapContext.Planificar",
                         "SnapContext.TestLoop", "SnapContext.AbrirWeb",
                         "SnapContext.AnadirAlContexto",
                         "SnapContext.LimpiarContexto"):
            self.assertIn(esperado, ids)

    def test_clases_de_acciones_existen_en_kotlin(self):
        """Cada clase referenciada en plugin.xml debe declararse en algún .kt."""
        clases = {a.get("class") for a in self.raiz.iter("action") if a.get("class")}
        fuentes = "".join(
            p.read_text(encoding="utf-8")
            for p in (JB / "src/main/kotlin").rglob("*.kt"))
        for clase in clases:
            nombre = clase.rsplit(".", 1)[-1]
            self.assertRegex(fuentes, rf"(class|object) {nombre}\b",
                             f"Clase {nombre} no encontrada en el código Kotlin")


class TestGradleYKotlin(unittest.TestCase):
    def test_gradle_configura_intellij(self):
        gradle = (JB / "build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn("org.jetbrains.intellij", gradle)
        self.assertIn('version = "2.2.0"', gradle)
        self.assertIn("patchPluginXml", gradle)

    def test_kotlin_usa_processbuilder(self):
        servicio = (JB / "src/main/kotlin/com/snapcontext/jetbrains/"
                    "SnapContextService.kt").read_text(encoding="utf-8")
        self.assertIn("ProcessBuilder", servicio)
        self.assertIn("--directorio", servicio)          # cwd del proyecto
        self.assertIn("--no-confirmar", servicio)        # igual que VS Code
        self.assertIn("GEMINI_API_KEY", servicio)        # clave opcional

    def test_contexto_como_vscode(self):
        # Mismo mecanismo de contexto visual que la extensión de VS Code.
        vscode = (RAIZ / "vscode" / "extension.js").read_text(encoding="utf-8")
        kotlin = (JB / "src/main/kotlin/com/snapcontext/jetbrains/"
                  "SnapContextService.kt").read_text(encoding="utf-8")
        self.assertIn("Revisa especialmente estos archivos:", vscode)
        self.assertIn("Revisa especialmente estos archivos:", kotlin)



if __name__ == "__main__":
    unittest.main()
