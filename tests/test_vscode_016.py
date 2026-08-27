#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la estructura de la extensión VS Code — v0.16.0.

Validan que el paquete en ``vscode/`` es coherente (manifiesto JSON válido,
comandos registrados, entry point presente, webview copiada y scripts de
empaquetado) sin necesidad de Node.js.
"""

import json
import sys
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VSCODE = RAIZ / "vscode"

sys.path.insert(0, str(RAIZ))

import snapcontext as sc


class TestEstructuraVsCode(unittest.TestCase):
    def test_archivos_obligatorios_existen(self):
        for relativo in ("package.json", "tsconfig.json",
                         "src/extension.ts",
                         "webview/index.html", "webview/servidor_webview.py",
                         "scripts/empaquetar.ps1", "scripts/empaquetar.sh"):
            self.assertTrue((VSCODE / relativo).is_file(), relativo)

    def test_package_json_valido(self):
        manifiesto = json.loads((VSCODE / "package.json").read_text("utf-8"))
        self.assertEqual(manifiesto["name"], "snapcontext-vscode")
        self.assertEqual(manifiesto["version"], "3.2.0")
        self.assertEqual(manifiesto["main"], "./out/extension.js")

    def test_comandos_contribuidos(self):
        manifiesto = json.loads((VSCODE / "package.json").read_text("utf-8"))
        comandos = {c["command"]
                    for c in manifiesto["contributes"]["commands"]}
        for esperado in ("snapcontext.abrirChat", "snapcontext.ejecutarConsulta",
                         "snapcontext.planificar", "snapcontext.configurarApiKey",
                         "snapcontext.anadirAlContexto",
                         "snapcontext.limpiarSeleccion"):
            self.assertIn(esperado, comandos)

    def test_menu_del_explorador(self):
        manifiesto = json.loads((VSCODE / "package.json").read_text("utf-8"))
        menus = manifiesto["contributes"]["menus"]["explorer/context"]
        self.assertTrue(any(m["command"] == "snapcontext.anadirAlContexto"
                            for m in menus))

    def test_configuraciones(self):
        manifiesto = json.loads((VSCODE / "package.json").read_text("utf-8"))
        propiedades = manifiesto["contributes"]["configuration"]["properties"]
        for esperada in ("snapcontext.pythonPath", "snapcontext.provider",
                         "snapcontext.apiKey", "snapcontext.confirmar"):
            self.assertIn(esperada, propiedades)

    def test_extension_ts_registra_los_comandos(self):
        codigo = (VSCODE / "src" / "extension.ts").read_text(encoding="utf-8")
        for comando in ("abrirChat", "ejecutarConsulta", "planificar",
                        "configurarApiKey", "anadirAlContexto",
                        "limpiarSeleccion"):
            self.assertIn(comando, codigo)
        # Canal de salida dedicado.
        self.assertIn('"SnapContext Output"', codigo)
        # Usa la CLI/módulo python con el workspace como directorio.
        self.assertIn('"-m", "snapcontext"', codigo)
        self.assertIn("cwd: ws", codigo)
        self.assertIn("--no-confirmar", codigo)
        self.assertIn("--plan", codigo)
        # Migración a TypeScript (v3.2.0).
        self.assertIn("import * as vscode from \"vscode\"", codigo)
        self.assertIn("vscode.ExtensionContext", codigo)

    def test_webview_es_copia_de_la_interfaz_web(self):
        web = (RAIZ / "web" / "static" / "index.html").read_text(
            encoding="utf-8")
        vscode_web = (VSCODE / "webview" / "index.html").read_text(
            encoding="utf-8")
        self.assertEqual(web, vscode_web,
                         "vscode/webview/index.html debe copiar "
                         "web/static/index.html")

    def test_servidor_webview_reutiliza_web_app(self):
        codigo = (VSCODE / "webview" / "servidor_webview.py").read_text(
            encoding="utf-8")
        self.assertIn("from web.app import arrancar_servidor", codigo)


class TestVersionCli(unittest.TestCase):
    def test_version_es_1_2_0(self):
        self.assertEqual(sc.VERSION, "5.3.0")


if __name__ == "__main__":
    unittest.main()
