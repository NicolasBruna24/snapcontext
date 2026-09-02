#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del parser universal multi-lenguaje (v5.6.0) — Tree-sitter.

Cubre: detección de lenguaje por extensión/contenido, parseo de JS/TS/Go/Rust/
Java, extracción de funciones/clases/imports, aplicación de parches sobre el
AST, validación de sintaxis, integración con el editor propio (cadena de
estrategias vía ``sc._ast_disponible``) y contexto selectivo multi-lenguaje.

Si no hay backend tree-sitter instalado, los tests funcionales se saltan
(``backend_disponible()`` False) pero las pruebas de detección siguen pasando.
"""

import os
import unittest
from unittest import mock

import parser_universal as pu

try:
    import snapcontext as sc
except Exception:                                   # pragma: no cover
    sc = None


def _tiene_backend() -> bool:
    try:
        return pu.backend_disponible()
    except Exception:                               # noqa: BLE001
        return False


# --- muestras de código -----------------------------------------------------
PY = "def hola():\n    return 1\n\n\nclass Saludo:\n    def chao(self):\n        return 2\n"
JS = ("import { a } from './mod.js';\n"
      "function sumar(x, y) {\n  return x + y;\n}\n"
      "class Punto {\n  mover(dx) {\n    this.x += dx;\n  }\n}\n")
TS = ("interface Punto { x: number; }\n"
      "export function distancia(p: Punto): number {\n"
      "  return Math.abs(p.x);\n}\n")
GO = ("package main\n\n"
      "import \"fmt\"\n\n"
      "func main() {\n"
      "\tfmt.Println(\"hola\")\n"
      "}\n\n"
      "type Rect struct {\n\tAncho int\n}\n")
RUST = ("fn main() {\n    let x = 1;\n    println!(\"{}\", x);\n}\n\n"
        "struct Punto {\n    x: i32,\n}\n")
JAVA = ("public class Hola {\n"
        "    public static void main(String[] args) {\n"
        "        System.out.println(\"hola\");\n"
        "    }\n}\n")


class TestDeteccionLenguaje(unittest.TestCase):
    """Detección por extensión y por contenido."""

    def test_extensiones_basicas(self):
        casos = {
            "a.py": "python", "b.js": "javascript", "c.ts": "typescript",
            "d.go": "go", "e.rs": "rust", "f.java": "java",
            "g.cpp": "cpp", "h.cs": "c_sharp", "i.rb": "ruby",
            "j.dart": "dart",
        }
        for archivo, esperado in casos.items():
            self.assertEqual(pu.detectar_lenguaje_por_extension(archivo),
                             esperado, archivo)

    def test_extension_desconocida_y_vacia(self):
        self.assertIsNone(pu.detectar_lenguaje_por_extension("a.xyz"))
        self.assertIsNone(pu.detectar_lenguaje_por_extension(""))
        self.assertIsNone(pu.detectar_lenguaje_por_extension(None))

    def test_deteccion_por_contenido_shebang(self):
        self.assertEqual(pu.detectar_lenguaje("#!/usr/bin/env python3\nx=1"),
                         "python")
        self.assertEqual(pu.detectar_lenguaje("#!/bin/bash\necho hola"),
                         "bash")

    def test_deteccion_por_contenido_marcadores(self):
        self.assertEqual(pu.detectar_lenguaje("func main() {}"), "go")
        self.assertEqual(pu.detectar_lenguaje("fn main() {}"), "rust")
        self.assertEqual(pu.detectar_lenguaje("function f() {}"),
                         "javascript")

    def test_extension_tiene_prioridad_sobre_contenido(self):
        # Contenido de Go con extensión .py → python.
        self.assertEqual(pu.detectar_lenguaje("func main() {}", "a.py"),
                         "python")


class TestParseo(unittest.TestCase):
    """Parseo con tree-sitter (requiere backend)."""

    def setUp(self):
        if not _tiene_backend():
            self.skipTest("tree-sitter (language pack) no está instalado")

    def test_backend_disponible(self):
        self.assertTrue(pu.backend_disponible())
        self.assertIn(pu.backend_activo(),
                      ("tree_sitter_language_pack", "tree_sitter_languages"))

    def test_parseo_lenguajes(self):
        for codigo, lenguaje in ((PY, "python"), (JS, "javascript"),
                                 (TS, "typescript"), (GO, "go"),
                                 (RUST, "rust"), (JAVA, "java")):
            arbol = pu.parsear_archivo(codigo, lenguaje)
            self.assertIsNotNone(arbol, lenguaje)
            self.assertFalse(arbol.root_node.has_error, lenguaje)

    def test_parseo_sintaxis_invalida_sin_excepcion(self):
        arbol = pu.parsear_archivo("def f(:\n    break", "python")
        self.assertIsNotNone(arbol)
        self.assertTrue(arbol.root_node.has_error)

    def test_parseo_vacio_o_sin_lenguaje(self):
        self.assertIsNone(pu.parsear_archivo("", "python"))
        self.assertIsNone(pu.parsear_archivo(PY, None))
        self.assertIsNone(pu.parsear_archivo(None, "python"))


class TestExtraccionNodos(unittest.TestCase):
    """extraer_nodos: funciones, clases e imports."""

    def setUp(self):
        if not _tiene_backend():
            self.skipTest("tree-sitter (language pack) no está instalado")

    def test_extraer_python(self):
        nodos = pu.extraer_nodos("m.py", PY)
        self.assertIsNotNone(nodos)
        self.assertEqual(nodos["lenguaje"], "python")
        self.assertIn("hola", {f["nombre"] for f in nodos["funciones"]})
        self.assertIn("Saludo", {c["nombre"] for c in nodos["clases"]})

    def test_extraer_javascript(self):
        nodos = pu.extraer_nodos("app.js", JS)
        self.assertIsNotNone(nodos)
        self.assertEqual(nodos["lenguaje"], "javascript")
        self.assertIn("sumar", {f["nombre"] for f in nodos["funciones"]})
        self.assertIn("Punto", {c["nombre"] for c in nodos["clases"]})
        self.assertTrue(nodos["imports"])

    def test_extraer_go(self):
        nodos = pu.extraer_nodos("main.go", GO)
        self.assertIsNotNone(nodos)
        self.assertIn("main", {f["nombre"] for f in nodos["funciones"]})
        self.assertIn("Rect", {c["nombre"] for c in nodos["clases"]})

    def test_extraer_rust(self):
        nodos = pu.extraer_nodos("lib.rs", RUST)
        self.assertIsNotNone(nodos)
        self.assertIn("main", {f["nombre"] for f in nodos["funciones"]})
        self.assertIn("Punto", {c["nombre"] for c in nodos["clases"]})

    def test_extraer_java(self):
        nodos = pu.extraer_nodos("Hola.java", JAVA)
        self.assertIsNotNone(nodos)
        self.assertIn("Hola", {c["nombre"] for c in nodos["clases"]})
        self.assertIn("main", {f["nombre"] for f in nodos["funciones"]})

    def test_filtro_tipo_nodo(self):
        solo_f = pu.extraer_nodos("m.py", PY, tipo_nodo="funciones")
        self.assertIn("funciones", solo_f)
        self.assertNotIn("clases", solo_f)

    def test_extension_no_soportada_devuelve_none(self):
        self.assertIsNone(pu.extraer_nodos("a.xyz", "cualquier cosa"))

    def test_lineas_coherentes(self):
        nodos = pu.extraer_nodos("m.py", PY)
        for simbolo in nodos["funciones"] + nodos["clases"]:
            self.assertGreaterEqual(simbolo["inicio"], 1)
            self.assertGreaterEqual(simbolo["fin"], simbolo["inicio"])


class TestBloquesYParches(unittest.TestCase):
    """extraer_bloques y aplicar_parche_arbol."""

    def setUp(self):
        if not _tiene_backend():
            self.skipTest("tree-sitter (language pack) no está instalado")

    def test_bloques_formato_compatibles(self):
        bloques = pu.extraer_bloques("m.py", PY)
        self.assertTrue(bloques)
        for bloque in bloques:
            self.assertIn(bloque["tipo"], ("Funcion", "Clase"))
            self.assertIn("nombre", bloque)
            self.assertIn("inicio", bloque)
            self.assertIn("fin", bloque)

    def test_parche_reemplaza_nodo(self):
        nuevo = pu.aplicar_parche_arbol(
            PY, "def hola():\n    return 1", "def hola():\n    return 42",
            archivo="m.py")
        self.assertIsNotNone(nuevo)
        self.assertIn("return 42", nuevo)
        self.assertNotIn("return 1\n", nuevo)

    def test_parche_nodo_inexistente_devuelve_none(self):
        self.assertIsNone(pu.aplicar_parche_arbol(
            PY, "def no_existe(): pass", "x", archivo="m.py"))

    def test_parche_nuevo_invalido_devuelve_none(self):
        # El reemplazo no parsea → la operación no es válida (transacción).
        self.assertIsNone(pu.aplicar_parche_arbol(
            PY, "def hola():\n    return 1", "def hola(:\n  break",
            archivo="m.py"))

    def test_validar_sintaxis(self):
        self.assertTrue(pu.validar_sintaxis("m.py", PY))
        self.assertFalse(pu.validar_sintaxis("m.py", "def f(:\n  pass"))
        self.assertIsNone(pu.validar_sintaxis("a.xyz", PY))


@unittest.skipUnless(sc is not None, "snapcontext no disponible")
class TestIntegracionEditor(unittest.TestCase):
    """Integración con el editor propio y el contexto selectivo."""

    def setUp(self):
        if not _tiene_backend():
            self.skipTest("tree-sitter (language pack) no está instalado")

    def test_ast_disponible_python_y_no_python(self):
        self.assertTrue(sc._ast_disponible("m.py"))
        self.assertTrue(sc._ast_disponible("app.js"))
        self.assertTrue(sc._ast_disponible("main.go"))
        self.assertFalse(sc._ast_disponible(""))
        self.assertFalse(sc._ast_disponible("a.xyz"))

    def test_resumen_ast_usa_parser_universal(self):
        resumen = sc._resumen_ast(JS, "app.js")
        self.assertTrue(resumen.get("ok"))
        self.assertEqual(resumen.get("motor"), "tree-sitter")
        self.assertEqual(resumen.get("lenguaje"), "javascript")

    def test_resumen_ast_python_sigue_usando_stdlib(self):
        resumen = sc._resumen_ast(PY, "m.py")
        self.assertTrue(resumen.get("ok"))
        self.assertEqual(resumen.get("motor"), "ast")

    def test_bloques_ast_multi_lenguaje(self):
        bloques = sc._extraer_bloques_ast(JS, "app.js")
        self.assertTrue(bloques)
        self.assertIn("sumar", {b["nombre"] for b in bloques})

    def test_cadena_estrategias_con_tree_sitter(self):
        import agentes as ag
        editor = ag.AgenteEditorPropio()
        # Go es estructural con tree-sitter → AST disponible en 'auto'.
        self.assertEqual(
            editor._cadena_modos("main.go", "añade un campo al struct",
                                 "auto"),
            ["ast", "parche", "sobrescribir"])
        # Tarea no estructural → parche primero.
        self.assertEqual(
            editor._cadena_modos("main.go", "cambia el texto del log",
                                 "auto"),
            ["parche", "sobrescribir"])

    def test_contexto_selectivo_sin_backend_no_rompe(self):
        with mock.patch.object(pu, "extraer_nodos", return_value=None):
            bloques = sc._extraer_bloques_ast(JS, "app.js")
        self.assertEqual(bloques, [])


class TestEnvVersion(unittest.TestCase):
    """Versionado y packaging del módulo."""

    def test_version_560(self):
        self.assertEqual(sc.VERSION, "6.16.0")

    def test_pyproject_incluye_tree_sitter(self):
        texto = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "pyproject.toml"),
            encoding="utf-8").read()
        self.assertIn("tree-sitter", texto)
        self.assertIn("parser_universal", texto)


if __name__ == "__main__":
    unittest.main()
