#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v1.4.0: MCP avanzado, planificador condicional y paralelo."""

import argparse
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import snapcontext as sc


def _args_plan(extra=None):
    base = {"plan": True, "consulta": "tarea", "auto": True,
            "paralelo": 1, "git_commit": False, "confirmar": False,
            "branch": None, "modelo": None, "depurar": False}
    base.update(extra or {})
    return argparse.Namespace(**base)


class TestCondicionesPlan(unittest.TestCase):
    """_evaluar_condicion(): condiciones del planificador v1.4.0."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_cond_")
        with open(os.path.join(self.tmp, "main.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("def main():\n    pass\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archivo_existe_verdadero(self):
        self.assertTrue(
            sc._evaluar_condicion("archivo_existe('main.py')", self.tmp))

    def test_archivo_existe_falso(self):
        self.assertFalse(
            sc._evaluar_condicion('archivo_existe("no.py")', self.tmp))

    def test_archivo_contiene(self):
        self.assertTrue(sc._evaluar_condicion(
            "archivo_contiene('main.py', 'def main')", self.tmp))
        self.assertFalse(sc._evaluar_condicion(
            "archivo_contiene('main.py', 'clase_inexistente')", self.tmp))

    def test_comando_exito_y_fallo(self):
        version = f'"{sys.executable}" --version'
        self.assertTrue(
            sc._evaluar_condicion(f"comando_exito('{version}')", self.tmp))
        self.assertFalse(
            sc._evaluar_condicion("comando_exito('cmd_que_no_existe_123')",
                                  self.tmp))

    def test_vacia_se_cumple(self):
        self.assertTrue(sc._evaluar_condicion("", self.tmp))
        self.assertTrue(sc._evaluar_condicion(None, self.tmp))

    def test_desconocida_devuelve_false_sin_excepcion(self):
        self.assertFalse(
            sc._evaluar_condicion("funcion_rara('x')", self.tmp))
        self.assertFalse(sc._evaluar_condicion("sin parentesis", self.tmp))


class TestNormalizarDependencias(unittest.TestCase):
    def test_lista_de_enteros(self):
        self.assertEqual(sc._normalizar_dependencias([1, 2]), [1, 2])

    def test_strings_numericos_y_duplicados(self):
        self.assertEqual(
            sc._normalizar_dependencias(["1", 1, "0"]), [0, 1])

    def test_invalidos_descartados(self):
        self.assertEqual(sc._normalizar_dependencias(["a", None, -1]), [])

    def test_vacio(self):
        self.assertEqual(sc._normalizar_dependencias(None), [])


class TestHerramientasMcpAvanzadas(unittest.TestCase):
    """Nuevas herramientas MCP v1.4.0: semantic_search y ast_avanzado."""

    def test_registradas_en_predefinidas(self):
        for nombre in ("semantic_search", "ast_avanzado"):
            self.assertIn(nombre, sc.HERRAMIENTAS_PREDEFINIDAS)
            self.assertFalse(
                sc.HERRAMIENTAS_PREDEFINIDAS[nombre]["requiere_permiso"])

    def test_ast_avanzado_python_fallback_ast(self):
        tmp = tempfile.mkdtemp(prefix="sc_ts_")
        try:
            ruta = os.path.join(tmp, "modulo.py")
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write("import os\n\n\nclass A:\n"
                         "    def metodo(self):\n        pass\n\n\n"
                         "def saluda(nombre):\n    print(os, nombre)\n")
            resultado = sc._tool_ast_avanzado(ruta)
            self.assertTrue(resultado["ok"])
            self.assertIn(resultado["motor"], ("tree-sitter", "ast"))
            nombres = {f["nombre"] for f in resultado["funciones"]}
            clases = {c["nombre"] for c in resultado["clases"]}
            self.assertTrue(nombres & {"saluda", "metodo"}, nombres)
            self.assertIn("A", clases)
            if resultado["motor"] == "ast":
                self.assertTrue(any("os" in i for i in resultado["imports"]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ast_avanzado_lenguaje_no_soportado_sin_tree_sitter(self):
        if sc.tree_sitter is not None and sc._ts_lang is not None:
            self.skipTest("tree-sitter instalado")
        tmp = tempfile.mkdtemp(prefix="sc_ts2_")
        try:
            ruta = os.path.join(tmp, "main.rb")
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write("puts 'hola'\n")
            resultado = sc._tool_ast_avanzado(ruta)
            self.assertFalse(resultado["ok"])
            self.assertIn("mcp_avanzado", resultado.get("error", ""))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_semantic_search_sin_consulta(self):
        self.assertFalse(sc._tool_semantic_search("   ")["ok"])

    def test_semantic_search_sin_embeddings_falla_elegante(self):
        with mock.patch.object(sc, "SentenceTransformer", None):
            resultado = sc._tool_semantic_search("gestión de pagos")
            self.assertFalse(resultado["ok"])
            self.assertIn("embeddings", resultado["error"])

    def test_semantic_search_integrada_en_dispatcher(self):
        with mock.patch.object(sc, "_tool_semantic_search",
                               return_value={"ok": True}) as herramienta:
            res = sc._ejecutar_herramienta_mcp(
                "semantic_search",
                {"consulta": "login", "directorio": ".", "max_resultados": 5},
                confirmar=False)
            self.assertTrue(res["ok"])
            herramienta.assert_called_once()
            self.assertEqual(herramienta.call_args[0][0], "login")

    def test_dispatcher_conoce_ast_avanzado(self):
        with mock.patch.object(sc, "_tool_ast_avanzado",
                               return_value={"ok": True}) as herramienta:
            res = sc._ejecutar_herramienta_mcp(
                "ast_avanzado", {"ruta": "x.py"}, confirmar=False)
            self.assertTrue(res["ok"])
            herramienta.assert_called_once_with("x.py")


class TestPlanParalelo(unittest.TestCase):
    """Ejecución en paralelo con dependencias (_ejecutar_plan_en_paralelo)."""

    def _pasos(self):
        return [
            {"descripcion": "A falla", "accion": "ejecutar",
             "comando": "echo a", "dependencias": [], "condicion": ""},
            {"descripcion": "B independiente", "accion": "ejecutar",
             "comando": "echo b", "dependencias": [], "condicion": ""},
            {"descripcion": "depende del paso 1 (falla)", "accion": "ejecutar",
             "comando": "echo c", "dependencias": [1], "condicion": ""},
            {"descripcion": "depende del paso 2 (éxito)", "accion": "ejecutar",
             "comando": "echo d", "dependencias": [2], "condicion": ""},
        ]

    def test_dependencias_respetadas(self):
        llamadas = []

        def falso_paso(paso, args, raiz):
            llamadas.append(paso["descripcion"])
            ok = paso["descripcion"] != "A falla"
            return (ok, "simulado")

        args = _args_plan()
        with mock.patch.object(sc, "_ejecutar_paso_plan",
                               side_effect=falso_paso), \
                mock.patch.object(sc, "_git_commit_paso"):
            resultados = sc._ejecutar_plan_en_paralelo(
                self._pasos(), args, ".", max_hilos=4)

        por_paso = {r["paso"]: r["resultado"] for r in resultados}
        self.assertEqual(por_paso.get(1), "fallo")
        self.assertEqual(por_paso.get(2), "éxito")
        self.assertEqual(por_paso.get(3), "saltado")   # dependía del fallo
        self.assertEqual(por_paso.get(4), "éxito")     # dependía del éxito
        self.assertNotIn("depende del paso 1 (falla)", llamadas)
        self.assertIn("depende del paso 2 (éxito)", llamadas)

    def test_condicion_salta_paso_en_paralelo(self):
        pasos = [{"descripcion": "condicional", "accion": "ejecutar",
                  "comando": "echo x", "dependencias": [],
                  "condicion": "archivo_existe('no_existe_nada.py')"}]
        args = _args_plan()
        with mock.patch.object(sc, "_ejecutar_paso_plan") as ejecutado:
            resultados = sc._ejecutar_plan_en_paralelo(pasos, args, ".",
                                                       max_hilos=2)
        ejecutado.assert_not_called()
        self.assertEqual(resultados[0]["resultado"], "saltado")


class TestFlagParalelo(unittest.TestCase):
    def test_por_defecto_1(self):
        args = sc.crear_parser().parse_args(["--plan", "tarea"])
        self.assertEqual(args.paralelo, 1)

    def test_valor_personalizado(self):
        args = sc.crear_parser().parse_args(
            ["--plan", "tarea", "--paralelo", "4"])
        self.assertEqual(args.paralelo, 4)


class TestChatComandosGrafo(unittest.TestCase):
    """/grafo y /dependencias usan _grafo_dependencias (reutiliza v1.2.0)."""

    def setUp(self):
        self.prev = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="sc_grafo_")
        with open(os.path.join(self.tmp, "app.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("from utils import helper\n")
        with open(os.path.join(self.tmp, "utils.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("# utilidades\n")
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.prev)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_grafo_tiene_enlace_app_utils(self):
        grafo = sc._grafo_dependencias(".")
        enlaces = {(e["origen"], e["destino"]) for e in grafo["enlaces"]}
        self.assertIn(("app.py", "utils.py"), enlaces)

    def test_dependencias_directas_e_inversas(self):
        grafo = sc._grafo_dependencias(".")
        directas = [e["destino"] for e in grafo["enlaces"]
                    if e["origen"] == "app.py"]
        inversas = [e["origen"] for e in grafo["enlaces"]
                    if e["destino"] == "utils.py"]
        self.assertEqual(directas, ["utils.py"])
        self.assertEqual(inversas, ["app.py"])


class TestVersion(unittest.TestCase):
    def test_version_140_coherente(self):
        self.assertEqual(sc.VERSION, "6.10.0")


if __name__ == "__main__":
    unittest.main()
