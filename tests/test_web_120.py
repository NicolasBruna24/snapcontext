#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v1.2.0: editor web (Monaco), grafo de dependencias y búsqueda.

Cubren las funciones nuevas de ``snapcontext`` que alimentan a la interfaz web
avanzada. No requieren FastAPI ni sentence-transformers.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc


class TestComandoParaMonaco(unittest.TestCase):
    def test_extensiones_comunes(self):
        casos = {
            "pagos.py": "python", "app.js": "javascript",
            "mod.ts": "typescript", "ui.dart": "dart",
            "main.go": "go", "lib.rs": "rust", "A.java": "java",
            "ui.cpp": "cpp", "README.md": "markdown", "conf.json": "json",
        }
        for archivo, esperado in casos.items():
            self.assertEqual(sc._comando_para_monaco(archivo), esperado)

    def test_desconocida_es_plaintext(self):
        self.assertEqual(sc._comando_para_monaco("imagen.png"), "plaintext")


class TestExtraerDependencias(unittest.TestCase):
    def test_python(self):
        codigo = "import os\nfrom pagos import PagoService\nfrom utils.helper import x\n"
        deps = sc._extraer_dependencias(codigo, "python")
        self.assertIn("pagos", deps)
        self.assertIn("utils", deps)
        self.assertIn("os", deps)

    def test_js_import_y_require(self):
        codigo = "import Cliente from './cliente.js';\nconst x = require('util');\n"
        deps = sc._extraer_dependencias(codigo, "javascript")
        self.assertIn("./cliente.js", deps)
        self.assertIn("util", deps)

    def test_dart(self):
        codigo = "import 'package:flutter/material.dart';\nimport './services.dart';\n"
        deps = sc._extraer_dependencias(codigo, "dart")
        self.assertIn("package:flutter/material.dart", deps)
        self.assertIn("./services.dart", deps)

    def test_go(self):
        codigo = 'import "fmt"\nimport "github.com/x/y"\n'
        deps = sc._extraer_dependencias(codigo, "go")
        self.assertIn("fmt", deps)
        self.assertIn("github.com/x/y", deps)

    def test_rust(self):
        codigo = "use crate::cliente;\nextern crate serde;\n"
        deps = sc._extraer_dependencias(codigo, "rust")
        self.assertIn("crate::cliente", deps)
        self.assertIn("serde", deps)

    def test_excluye_future(self):
        deps = sc._extraer_dependencias("from __future__ import annotations\n", "python")
        self.assertNotIn("__future__", deps)


class _GrafoBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name) / "proy"
        self.raiz.mkdir()
        self.addCleanup(self.tmp.cleanup)


class TestGrafoDependencias(_GrafoBase):
    def test_enlaza_imports_locales_python(self):
        (self.raiz / "app.py").write_text(
            "from utils import helper\n", encoding="utf-8")
        (self.raiz / "utils.py").write_text("# utilidades\n", encoding="utf-8")
        grafo = sc._grafo_dependencias(str(self.raiz))
        enlaces = {(e["origen"], e["destino"]) for e in grafo["enlaces"]}
        self.assertIn(("app.py", "utils.py"), enlaces)

    def test_resuelve_ruta_relativa_js(self):
        (self.raiz / "main.js").write_text(
            "import Cliente from './cliente.js';\n", encoding="utf-8")
        (self.raiz / "cliente.js").write_text("export default {};\n", encoding="utf-8")
        grafo = sc._grafo_dependencias(str(self.raiz))
        enlaces = {(e["origen"], e["destino"]) for e in grafo["enlaces"]}
        self.assertIn(("main.js", "cliente.js"), enlaces)

    def test_nodos_tienen_etiqueta_y_lenguaje(self):
        (self.raiz / "a.py").write_text("", encoding="utf-8")
        grafo = sc._grafo_dependencias(str(self.raiz))
        self.assertEqual(len(grafo["nodos"]), 1)
        self.assertEqual(grafo["nodos"][0]["id"], "a.py")
        self.assertEqual(grafo["nodos"][0]["lenguaje"], "python")

    def test_directorio_invalido_vacio(self):
        grafo = sc._grafo_dependencias("\\\\no\\\\existe\\\\ruta")
        self.assertEqual(grafo, {"nodos": [], "enlaces": []})


class TestBuscarEnCodigo(_GrafoBase):
    def test_sin_tema_devuelve_vacio(self):
        self.assertEqual(sc._buscar_en_codigo("", str(self.raiz)), [])

    def test_con_findstr_mockeado(self):
        (self.raiz / "a.py").write_text("def pago():\n    pass\n", encoding="utf-8")
        salida = "a.py:1:def pago():\n"
        with mock.patch.object(sc, "_herramienta_busqueda", return_value="findstr"), \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, salida, "")):
            resultado = sc._buscar_en_codigo("pago", str(self.raiz))
        self.assertEqual(resultado, ["a.py:1:def pago():"])

    def test_sin_buscador_devuelve_vacio(self):
        with mock.patch.object(sc, "_herramienta_busqueda", return_value=None):
            self.assertEqual(sc._buscar_en_codigo("x", str(self.raiz)), [])


def _se_puede_importar_web() -> bool:
    try:
        import web.app  # noqa: F401
        return True
    except Exception:
        return False


class TestWebProxy(unittest.TestCase):
    @unittest.skipUnless(_se_puede_importar_web(), "extra 'web' (fastapi) no instalado")
    def test_crear_app_disponible(self):
        import web.app as wa
        self.assertTrue(hasattr(wa, "crear_app"))
        self.assertTrue(hasattr(wa, "_dependencias_web"))
        self.assertTrue(hasattr(wa, "_guardar_archivo_web"))


if __name__ == "__main__":
    unittest.main()