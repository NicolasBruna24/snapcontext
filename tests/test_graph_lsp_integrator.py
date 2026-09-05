#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v6.33.0: Integración Agresiva Graph RAG + LSP."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph_lsp_integrator as gli  # noqa: E402
import snapcontext as sc            # noqa: E402


# 1) Configuración
class TestConfiguracionGraphLSP(unittest.TestCase):
    def test_config_defecto(self):
        cfg = gli.configuracion_graph_lsp()
        self.assertFalse(cfg["activo"])
        self.assertEqual(cfg["profundidad"], 2)
        self.assertEqual(cfg["simbolos_max"], 10)

    def test_config_personalizada(self):
        cfg = gli.configuracion_graph_lsp({
            "graph_lsp": {"activo": True, "profundidad": 3, "simbolos_max": 20}
        })
        self.assertTrue(cfg["activo"])
        self.assertEqual(cfg["profundidad"], 3)
        self.assertEqual(cfg["simbolos_max"], 20)

    def test_config_activo_true(self):
        cfg = gli.configuracion_graph_lsp({"graph_lsp": {"activo": True}})
        self.assertTrue(cfg["activo"])

    def test_config_sin_seccion(self):
        cfg = gli.configuracion_graph_lsp({"otra": "cosa"})
        self.assertEqual(cfg["profundidad"], 2)

    def test_config_profundidad_invalida(self):
        cfg = gli.configuracion_graph_lsp({"graph_lsp": {"profundidad": "abc"}})
        self.assertEqual(cfg["profundidad"], 2)

    def test_config_none(self):
        cfg = gli.configuracion_graph_lsp(None)
        self.assertFalse(cfg["activo"])


# 2) GraphLSPIntegrator - caché
class TestGraphLSPIntegratorCache(unittest.TestCase):
    def test_cache_llamadas_lsp(self):
        integ = gli.GraphLSPIntegrator()
        with mock.patch.object(integ, '_obtener_simbolos_lsp', return_value=[{"nombre": "foo"}]) as m:
            r1 = integ.obtener_contexto_preciso("main.py")
            r2 = integ.obtener_contexto_preciso("main.py")
            self.assertEqual(m.call_count, 1)
            self.assertEqual(r1, r2)

    def test_cache_diferentes_archivos(self):
        integ = gli.GraphLSPIntegrator()
        with mock.patch.object(integ, '_obtener_simbolos_lsp', return_value=[{"nombre": "foo"}]) as m:
            integ.obtener_contexto_preciso("a.py")
            integ.obtener_contexto_preciso("b.py")
            self.assertEqual(m.call_count, 2)


# 3) GraphLSPIntegrator - expansión con grafo
class TestGraphLSPIntegratorExpansion(unittest.TestCase):
    def test_expandir_con_grafo_vacio(self):
        integ = gli.GraphLSPIntegrator(grafo={})
        resultado = integ._expandir_con_grafo("main.py", 2, 10)
        self.assertEqual(resultado, [])

    def test_expandir_con_grafo_mock(self):
        integ = gli.GraphLSPIntegrator(grafo={"main.py": ["utils.py"]})
        with mock.patch("graph_rag.expandir_contexto", return_value=["utils.py", "helpers.py", "main.py"]):
            resultado = integ._expandir_con_grafo("main.py", 2, 10)
            self.assertEqual(len(resultado), 2)
            self.assertEqual(resultado[0]["tipo"], "dependencia")

    def test_expandir_sin_modulo_graph_rag(self):
        integ = gli.GraphLSPIntegrator(grafo={"main.py": ["utils.py"]})
        mock_gr = mock.MagicMock(spec=[])
        with mock.patch.dict('sys.modules', {'graph_rag': mock_gr}):
            resultado = integ._expandir_con_grafo("main.py", 2, 10)
            self.assertEqual(resultado, [])


# 4) GraphLSPIntegrator - obtener_contexto_preciso
class TestGraphLSPIntegratorObtener(unittest.TestCase):
    def test_obtener_contexto_preciso_basico(self):
        integ = gli.GraphLSPIntegrator()
        with mock.patch.object(integ, '_obtener_simbolos_lsp', return_value=[{"nombre": "func_a", "linea": 10}]):
            with mock.patch.object(integ, '_expandir_con_grafo', return_value=[]):
                resultado = integ.obtener_contexto_preciso("main.py", 10, "funcion")
                self.assertIsInstance(resultado, list)
                self.assertTrue(len(resultado) >= 1)

    def test_obtener_con_linea(self):
        integ = gli.GraphLSPIntegrator()
        with mock.patch.object(integ, '_obtener_simbolos_lsp', return_value=[{"nombre": "x", "linea": 42}]):
            resultado = integ.obtener_contexto_preciso("test.py", 42, "variable")
            self.assertTrue(len(resultado) >= 1)

    def test_obtener_max_simbolos_respeta_limite(self):
        integ = gli.GraphLSPIntegrator(config={"graph_lsp": {"simbolos_max": 3}})
        with mock.patch.object(integ, '_obtener_simbolos_lsp', return_value=[{"nombre": f"s{i}"} for i in range(10)]):
            with mock.patch.object(integ, '_expandir_con_grafo', return_value=[]):
                resultado = integ.obtener_contexto_preciso("main.py")
                self.assertLessEqual(len(resultado), 3)

    def test_obtener_sin_grafo_ni_lsp(self):
        integ = gli.GraphLSPIntegrator()
        with mock.patch.object(integ, '_obtener_simbolos_lsp', return_value=[]):
            with mock.patch.object(integ, '_expandir_con_grafo', return_value=[]):
                resultado = integ.obtener_contexto_preciso("main.py")
                self.assertEqual(resultado, [])


# 5) GraphLSPIntegrator - inyectar_contexto_preciso
class TestGraphLSPIntegratorInyectar(unittest.TestCase):
    def test_inyectar_simbolos_vacios(self):
        integ = gli.GraphLSPIntegrator()
        resultado = integ.inyectar_contexto_preciso("contexto previo", [])
        self.assertIn("contexto previo", resultado)

    def test_inyectar_simbolos_con_datos(self):
        integ = gli.GraphLSPIntegrator()
        simbolos = [
            {"nombre": "func_a", "archivo": "main.py", "linea": 10, "tipo": "funcion"},
            {"nombre": "ClaseB", "archivo": "utils.py", "linea": 5, "tipo": "clase"},
        ]
        resultado = integ.inyectar_contexto_preciso("", simbolos)
        self.assertIn("func_a", resultado)
        self.assertIn("main.py", resultado)
        self.assertIn("ClaseB", resultado)

    def test_inyectar_preserva_contexto_actual(self):
        integ = gli.GraphLSPIntegrator()
        simbolos = [{"nombre": "x", "archivo": "a.py", "linea": 1, "tipo": "variable"}]
        resultado = integ.inyectar_contexto_preciso("CONTEXTO_BASE", simbolos)
        self.assertIn("CONTEXTO_BASE", resultado)
        self.assertIn("x", resultado)


# 6) Funciones standalone
class TestFuncionesStandalone(unittest.TestCase):
    def test_obtener_contexto_preciso_standalone(self):
        with mock.patch("graph_lsp_integrator.GraphLSPIntegrator._obtener_simbolos_lsp", return_value=[{"nombre": "a"}]):
            resultado = gli.obtener_contexto_preciso("main.py", 10, "funcion")
            self.assertIsInstance(resultado, list)

    def test_inyectar_contexto_preciso_standalone(self):
        resultado = gli.inyectar_contexto_preciso("base", [{"nombre": "x", "archivo": "a.py", "linea": 1, "tipo": "v"}])
        self.assertIn("base", resultado)
        self.assertIn("x", resultado)


# 7) Flags CLI
class TestFlagsCLI(unittest.TestCase):
    def test_graph_rag_lsp_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--graph-rag-lsp"])
        self.assertTrue(args.graph_rag_lsp)

    def test_graph_rag_lsp_desactivado_por_defecto(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(args.graph_rag_lsp)

    def test_lsp_profundidad_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--lsp-profundidad", "3"])
        self.assertEqual(args.lsp_profundidad, 3)

    def test_lsp_simbolos_max_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--lsp-simbolos-max", "15"])
        self.assertEqual(args.lsp_simbolos_max, 15)

    def test_lsp_profundidad_defecto(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.lsp_profundidad)

    def test_lsp_simbolos_max_defecto(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.lsp_simbolos_max)


# 8) Integración con snapcontext.py
class TestIntegracionSnapcontext(unittest.TestCase):
    def test_resolver_graph_rag_lsp_con_graph_rag_y_lsp(self):
        args = sc.crear_parser().parse_args([
            "consulta", "--graph-rag", "--lsp", "--graph-rag-lsp"
        ])
        self.assertTrue(sc._graph_rag_lsp_activo(args))

    def test_resolver_graph_rag_lsp_sin_graph_rag(self):
        args = sc.crear_parser().parse_args(["consulta", "--lsp", "--graph-rag-lsp"])
        self.assertFalse(sc._graph_rag_lsp_activo(args))

    def test_resolver_graph_rag_lsp_sin_lsp(self):
        args = sc.crear_parser().parse_args(["consulta", "--graph-rag", "--graph-rag-lsp"])
        self.assertFalse(sc._graph_rag_lsp_activo(args))

    def test_resolver_graph_rag_lsp_por_entorno(self):
        import os
        os.environ["SNAPCONTEXT_GRAPH_RAG_LSP"] = "1"
        try:
            args = sc.crear_parser().parse_args(["consulta", "--graph-rag", "--lsp"])
            self.assertTrue(sc._graph_rag_lsp_activo(args))
        finally:
            os.environ.pop("SNAPCONTEXT_GRAPH_RAG_LSP", None)

    def test_obtener_simbolos_integrado(self):
        with mock.patch("graph_lsp_integrator.obtener_contexto_preciso", return_value=[{"nombre": "foo"}]):
            resultado = sc.obtener_simbolos_lsp("main.py", 10, "funcion", {})
            self.assertIsInstance(resultado, list)