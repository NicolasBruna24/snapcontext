#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v5.3.0: detección automática de pruebas (detector_tests.py).

Cubre ``detectar_lenguaje``, ``detectar_comando_test``,
``detectar_estructura_tests``, ``detectar_automaticamente`` y el resolver
``resolver_comando_test`` para todos los lenguajes soportados, además de casos
límite (directorios vacíos, múltiples lenguajes, archivos sin tests).

Se ejecuta con:

    python -m pytest tests -v
    python -m unittest tests.test_detector_tests -v
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

sys_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_dir not in __import__("sys").path:
    __import__("sys").path.insert(0, sys_dir)

import detector_tests as det           # noqa: E402


def _crear_directorio(archivos):
    """Crea un directorio temporal con los archivos/carpetas indicados.

    ``archivos`` es un dict {ruta_relativa: contenido|None}. Si el valor es
    ``None`` se crea una carpeta; si no, un archivo de texto.
    """
    tmp = tempfile.mkdtemp()
    for nombre, contenido in archivos.items():
        ruta = os.path.join(tmp, nombre)
        if contenido is None:
            os.makedirs(ruta, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(ruta) or tmp, exist_ok=True)
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write(contenido)
    return tmp


class BaseDetector(unittest.TestCase):
    """Crea un directorio temporal por test y lo limpia al terminar."""

    def tearDown(self):
        if hasattr(self, "_tmp") and self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
        self._tmp = None

    def _detectar(self, archivos):
        self._tmp = _crear_directorio(archivos)
        return det.detectar_lenguaje(self._tmp)

    def _comando(self, archivos):
        self._tmp = _crear_directorio(archivos)
        return det.detectar_automaticamente(self._tmp)


class TestDetectarLenguaje(BaseDetector):
    """Detección correcta del lenguaje por cada archivo identificador."""

    def test_go(self):
        self.assertEqual(self._detectar({"go.mod": ""}), "go")

    def test_rust_cargo(self):
        self.assertEqual(self._detectar({"Cargo.toml": ""}), "rust")

    def test_java_maven(self):
        self.assertEqual(self._detectar({"pom.xml": ""}), "java-maven")

    def test_java_gradle(self):
        self.assertEqual(self._detectar({"build.gradle": ""}), "java-gradle")

    def test_flutter(self):
        self.assertEqual(self._detectar({"pubspec.yaml": "name: demo"}),
                         "flutter")

    def test_ruby(self):
        self.assertEqual(self._detectar({"Gemfile": "source :rubygems"}),
                         "ruby")

    def test_elixir(self):
        self.assertEqual(self._detectar({"mix.exs": "defmodule Demo"}),
                         "elixir")

    def test_dotnet_csproj(self):
        self.assertEqual(self._detectar({"App.csproj": "<Project/>"}),
                         "dotnet")

    def test_requirements_txt_pytest(self):
        self.assertEqual(self._detectar({"requirements.txt": "requests"}),
                         "python-pytest")

    def test_pyproject_con_pytest(self):
        self.assertEqual(
            self._detectar({"pyproject.toml":
                            "[project]\nname='x'\n[tool.pytest.ini_options]"}),
            "python-pytest")

    def test_pyproject_sin_pytest_unittest(self):
        self.assertEqual(
            self._detectar({"pyproject.toml": "[project]\nname='x'"}),
            "python-unittest")

    def test_setup_py_unittest(self):
        self.assertEqual(self._detectar({"setup.py": "from setuptools import"
                                                        " setup"}),
                         "python-unittest")

    def test_node_npm(self):
        self.assertEqual(self._detectar({"package.json": "{}"}), "node-npm")

    def test_node_yarn(self):
        self.assertEqual(self._detectar({"package.json": "{}",
                                         "yarn.lock": ""}), "node-yarn")


class TestDetectarComando(BaseDetector):
    def test_comando_go(self):
        res = self._comando({"go.mod": ""})
        self.assertEqual(res["comando"], "go test ./...")

    def test_comando_rust(self):
        res = self._comando({"Cargo.toml": ""})
        self.assertEqual(res["comando"], "cargo test")

    def test_comando_java_maven(self):
        res = self._comando({"pom.xml": ""})
        self.assertEqual(res["comando"], "mvn test")

    def test_comando_java_gradle(self):
        res = self._comando({"build.gradle": ""})
        self.assertEqual(res["comando"], "gradle test")

    def test_comando_python_pytest(self):
        res = self._comando({"requirements.txt": ""})
        self.assertEqual(res["comando"], "pytest")

    def test_comando_python_unittest(self):
        res = self._comando({"setup.py": ""})
        self.assertEqual(res["comando"], "python -m unittest discover")

    def test_comando_node_npm(self):
        res = self._comando({"package.json": "{}"})
        self.assertEqual(res["comando"], "npm test")

    def test_comando_node_yarn(self):
        res = self._comando({"package.json": "{}", "yarn.lock": ""})
        self.assertEqual(res["comando"], "yarn test")

    def test_comando_flutter(self):
        res = self._comando({"pubspec.yaml": ""})
        self.assertEqual(res["comando"], "flutter test")

    def test_comando_dotnet(self):
        res = self._comando({"Api.Tests.csproj": "<Project/>"})
        self.assertEqual(res["comando"], "dotnet test")

    def test_comando_ruby(self):
        res = self._comando({"Gemfile": ""})
        self.assertEqual(res["comando"], "bundle exec rspec")

    def test_comando_elixir(self):
        res = self._comando({"mix.exs": ""})
        self.assertEqual(res["comando"], "mix test")

    def test_detectar_automaticamente_flag(self):
        res = self._comando({"go.mod": ""})
        self.assertTrue(res["detectado"])
        self.assertEqual(res["lenguaje"], "go")

    def test_comando_lenguaje_no_soportado(self):
        self.assertIsNone(det.detectar_comando_test(".", "lisp-inexistente"))


class TestCasosLimite(BaseDetector):
    def test_directorio_vacio(self):
        res = self._comando({})
        self.assertFalse(res["detectado"])
        self.assertIsNone(res["lenguaje"])
        self.assertIsNone(res["comando"])
        self.assertEqual(res["estructura"], {})

    def test_directorio_inexistente(self):
        self.assertIsNone(det.detectar_lenguaje(
            os.path.join(os.getcwd(), "_no_existe_xyz")))

    def test_multiples_lenguajes_prioriza(self):
        # go.mod gana sobre package.json (prioridad de archivos identificadores).
        res = self._comando({"go.mod": "", "package.json": "{}"})
        self.assertEqual(res["lenguaje"], "go")

    def test_yarn_gana_a_npm(self):
        res = self._comando({"package.json": "{}", "yarn.lock": ""})
        self.assertEqual(res["lenguaje"], "node-yarn")

    def test_archivo_sin_identificador(self):
        # Un archivo .py aislado sin archivos identificadores -> no detectado.
        res = self._comando({"test_principal.py": ""})
        self.assertFalse(res["detectado"])


class TestEstructuraYResolver(BaseDetector):
    def test_estructura_python(self):
        self._tmp = _crear_directorio({"requirements.txt": ""})
        estructura = det.detectar_estructura_tests(self._tmp)
        self.assertEqual(estructura.get("patron"), "test_*.py")
        self.assertEqual(estructura.get("carpeta"), "tests/")

    def test_estructura_vacia_sin_lenguaje(self):
        self.assertEqual(det.detectar_estructura_tests(
            os.path.join(os.getcwd(), "_no_existe_xyz")), {})

    def test_resolver_explicito_gana(self):
        self.assertEqual(det.resolver_comando_test(".", "mi test"),
                         "mi test")

    def test_resolver_detecta(self):
        self._tmp = _crear_directorio({"go.mod": ""})
        self.assertEqual(det.resolver_comando_test(self._tmp),
                         "go test ./...")

    def test_resolver_sin_deteccion_es_none(self):
        self._tmp = _crear_directorio({"_vacio.txt": ""})
        self.assertIsNone(det.resolver_comando_test(self._tmp))


if __name__ == "__main__":
    unittest.main()
