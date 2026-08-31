#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de integración para la expansión MCP v6.7.0.

Verifica que las herramientas de bases de datos y APIs estén
correctamente registradas en el sistema MCP, el agente ReAct
y la CLI.

Ejecuta con:
    python -m pytest tests/test_mcp_expansion.py -v
"""

import argparse
import unittest


class TestHerramientasPredefinidas(unittest.TestCase):
    """Las 4 herramientas de v6.7.0 están en HERRAMIENTAS_PREDEFINIDAS."""

    def test_herramientas_predefinidas_incluyen_db_api(self):
        import snapcontext as sc
        herramientas = sc.HERRAMIENTAS_PREDEFINIDAS
        for nombre in ("db_query", "db_schema", "api_request", "api_inspect"):
            with self.subTest(herramienta=nombre):
                self.assertIn(nombre, herramientas,
                              f"{nombre} no está en HERRAMIENTAS_PREDEFINIDAS")
                self.assertIn("descripcion", herramientas[nombre])


class TestReactAgentIntegracion(unittest.TestCase):
    """El agente ReAct tiene las 4 herramientas v6.7.0."""

    def test_acciones_validas(self):
        from react_agent import ReactAgent
        esperadas = ("db_query", "db_schema", "api_request", "api_inspect")
        for accion in esperadas:
            with self.subTest(accion=accion):
                self.assertIn(accion, ReactAgent.ACCIONES_VALIDAS)

    def test_herramientas_registradas(self):
        from react_agent import ReactAgent
        agente = ReactAgent(directorio=".", auto=True, max_iter=1)
        for nombre in ("db_query", "db_schema", "api_request", "api_inspect"):
            with self.subTest(herramienta=nombre):
                self.assertIn(nombre, agente.herramientas)
                self.assertTrue(callable(agente.herramientas[nombre]))


class TestCliArgs(unittest.TestCase):
    """Los flags --db-url y --db-driver están definidos en el parser CLI."""

    def test_cli_args_db(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args([
            "--db-url", "sqlite:///test.db",
            "--db-driver", "sqlite",
            "tarea de ejemplo",
        ])
        self.assertEqual(args.db_url, "sqlite:///test.db")
        self.assertEqual(args.db_driver, "sqlite")


class TestConectarDbInicial(unittest.TestCase):
    """conectar_db_inicial devuelve 0 si no hay URL."""

    def test_sin_url_devuelve_cero(self):
        import snapcontext as sc
        args = argparse.Namespace(db_url=None, db_driver=None)
        self.assertEqual(sc.conectar_db_inicial(args), 0)

    def test_url_vacia_devuelve_cero(self):
        import snapcontext as sc
        args = argparse.Namespace(db_url="", db_driver=None)
        self.assertEqual(sc.conectar_db_inicial(args), 0)


if __name__ == "__main__":
    unittest.main()
