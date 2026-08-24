#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la migración a TypeScript de la extensión VS Code — v3.2.0.

Validan la configuración TypeScript, el entry point compilado y que los
comandos se registran en ``src/extension.ts``. Si hay un compilador
disponible (node_modules o npx), también ejecutan ``tsc --noEmit``.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VSCODE = RAIZ / "vscode"
TSCONFIG_OBLIGATORIO = {
    "target": "ES2020",
    "module": "commonjs",
    "strict": True,
    "esModuleInterop": True,
    "skipLibCheck": True,
    "forceConsistentCasingInFileNames": True,
    "outDir": "./out",
    "rootDir": "./src",
    "sourceMap": True,
    "declaration": True,
}


def _leer_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text("utf-8"))


class TestTsConfig(unittest.TestCase):
    def test_tsconfig_existe_y_tiene_opciones_obligatorias(self):
        opciones = _leer_json(VSCODE / "tsconfig.json")["compilerOptions"]
        for clave, valor in TSCONFIG_OBLIGATORIO.items():
            self.assertEqual(opciones.get(clave), valor,
                             f"compilerOptions['{clave}']")

    def test_tsconfig_include_exclude(self):
        config = _leer_json(VSCODE / "tsconfig.json")
        self.assertIn("src/**/*", config["include"])
        for excluido in ("node_modules", "out", "webview"):
            self.assertIn(excluido, config["exclude"])


class TestPackageJsonTypeScript(unittest.TestCase):
    def setUp(self):
        self.manifiesto = _leer_json(VSCODE / "package.json")

    def test_main_apunta_a_out(self):
        self.assertEqual(self.manifiesto["main"], "./out/extension.js")

    def test_scripts_de_compilacion(self):
        scripts = self.manifiesto["scripts"]
        self.assertEqual(scripts["compile"], "tsc -p ./")
        self.assertEqual(scripts["watch"], "tsc -watch -p ./")
        self.assertEqual(scripts["package"], "vsce package")
        # El empaquetado compila antes de generar el .vsix.
        self.assertIn("compile", scripts["vscode:prepublish"])

    def test_dev_dependencies_typescript(self):
        dev = self.manifiesto["devDependencies"]
        for paquete in ("typescript", "@types/vscode", "@types/node"):
            self.assertIn(paquete, dev)


class TestExtensionTs(unittest.TestCase):
    """El código TypeScript mantiene la funcionalidad de la v0.16.0."""

    @classmethod
    def setUpClass(cls):
        cls.codigo = (VSCODE / "src" / "extension.ts").read_text("utf-8")

    def test_imports_modulo_vscode(self):
        self.assertIn('import * as vscode from "vscode"', self.codigo)

    def test_activate_tipado(self):
        self.assertIn("export function activate("
                      "context: vscode.ExtensionContext): void",
                      self.codigo)
        self.assertIn("export function deactivate(): void", self.codigo)

    def test_comandos_registrados(self):
        for comando in ("snapcontext.abrirChat", "snapcontext.ejecutarConsulta",
                        "snapcontext.planificar", "snapcontext.configurarApiKey",
                        "snapcontext.anadirAlContexto",
                        "snapcontext.limpiarSeleccion"):
            self.assertIn(f'"{comando}"', self.codigo)
        # Los registros pasan por registerCommand (disposables en subscriptions).
        self.assertIn("registerCommand", self.codigo)
        self.assertIn("subscriptions.push(disposable)", self.codigo)

    def test_webview_sigue_sirviendo_el_servidor_web(self):
        self.assertIn("createWebviewPanel", self.codigo)
        self.assertIn("http://localhost:${puerto}", self.codigo)
        self.assertIn("esperarPuerto(puerto, 10000)", self.codigo)
        # Puerto dinámico: parte en 8765 y busca uno libre.
        self.assertIn("let puerto = 8765", self.codigo)

    def test_tipos_completos_en_firmas(self):
        self.assertIn(": vscode.OutputChannel | null", self.codigo)
        self.assertIn(": vscode.StatusBarItem | null", self.codigo)
        self.assertIn("uri?: vscode.Uri", self.codigo)
        self.assertIn("Promise<number>", self.codigo)
        self.assertIn("ChildProcessWithoutNullStreams | null", self.codigo)


class TestCompilacionReal(unittest.TestCase):
    """Ejecuta `tsc --noEmit` si hay un compilador disponible."""

    def test_tsc_no_emit_pasa_si_hay_compilador(self):
        tsc_local = VSCODE / "node_modules" / ".bin" / ("tsc.cmd"
                     if shutil.which("node") and Path(VSCODE /
                       "node_modules/.bin/tsc.cmd").exists() else "tsc")
        if not tsc_local.exists():
            self.skipTest("TypeScript no está instalado (npm install)")
        proc = subprocess.run(
            [str(tsc_local), "--noEmit", "-p", str(VSCODE / "tsconfig.json")],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0,
                         f"tsc --noEmit falló:\n{proc.stdout}\n{proc.stderr}")

    def test_out_extension_js_generado_tras_compilar(self):
        compilado = VSCODE / "out" / "extension.js"
        if not compilado.exists():
            self.skipTest("La extensión aún no se ha compilado "
                          "(ejecuta npm run compile)")
        codigo = compilado.read_text("utf-8")
        for comando in ("snapcontext.abrirChat", "snapcontext.planificar"):
            self.assertIn(comando, codigo)


if __name__ == "__main__":
    unittest.main()
