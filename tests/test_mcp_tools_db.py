#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para mcp_tools_db.py — herramientas MCP de bases de datos (v6.7.0).

Ejecuta con:
    python -m pytest tests/test_mcp_tools_db.py -v
"""

import sqlite3
import unittest

import mcp_tools_db as dbt


class TestEsConsultaSoloLectura(unittest.TestCase):
    """Validación estricta de consultas SQL de solo lectura."""

    # ── Consultas válidas ──────────────────────────────────────────────

    def test_select_simple(self):
        self.assertTrue(dbt.es_consulta_solo_lectura("SELECT 1"))

    def test_select_con_where(self):
        self.assertTrue(dbt.es_consulta_solo_lectura(
            "SELECT id, nombre FROM usuarios WHERE activo = 1"))

    def test_show_tables(self):
        self.assertTrue(dbt.es_consulta_solo_lectura("SHOW TABLES"))

    def test_describe_tabla(self):
        self.assertTrue(dbt.es_consulta_solo_lectura("DESCRIBE usuarios"))

    def test_explain_select(self):
        self.assertTrue(dbt.es_consulta_solo_lectura(
            "EXPLAIN SELECT * FROM pedidos"))

    def test_pragma_table_info(self):
        self.assertTrue(dbt.es_consulta_solo_lectura(
            "PRAGMA table_info('usuarios')"))

    def test_select_con_espacios(self):
        self.assertTrue(dbt.es_consulta_solo_lectura("   SELECT 1  "))

    def test_select_con_punto_y_coma_final(self):
        self.assertTrue(dbt.es_consulta_solo_lectura("SELECT 1;"))

    # ── Consultas rechazadas ───────────────────────────────────────────

    def test_rechaza_insert(self):
        self.assertFalse(dbt.es_consulta_solo_lectura(
            "INSERT INTO usuarios (nombre) VALUES ('x')"))

    def test_rechaza_update(self):
        self.assertFalse(dbt.es_consulta_solo_lectura(
            "UPDATE usuarios SET nombre = 'x'"))

    def test_rechaza_delete(self):
        self.assertFalse(dbt.es_consulta_solo_lectura(
            "DELETE FROM usuarios WHERE id = 1"))

    def test_rechaza_drop(self):
        self.assertFalse(dbt.es_consulta_solo_lectura("DROP TABLE usuarios"))

    def test_rechaza_multiples_sentencias(self):
        self.assertFalse(dbt.es_consulta_solo_lectura(
            "SELECT 1; DROP TABLE usuarios"))

    def test_rechaza_comentario_truco(self):
        self.assertFalse(dbt.es_consulta_solo_lectura(
            "/* DROP TABLE usuarios */ SELECT 1; DROP TABLE x"))

    def test_rechaza_cadena_vacia(self):
        self.assertFalse(dbt.es_consulta_solo_lectura(""))

    def test_rechaza_none(self):
        self.assertFalse(dbt.es_consulta_solo_lectura(None))

    def test_rechaza_solo_espacios(self):
        self.assertFalse(dbt.es_consulta_solo_lectura("   "))


class TestDetectarDriver(unittest.TestCase):
    """Detección automática de driver por prefijo de URL."""

    def test_sqlite(self):
        self.assertEqual(
            dbt._detectar_driver("sqlite:///mi.db"), "sqlite")

    def test_postgresql(self):
        self.assertEqual(
            dbt._detectar_driver("postgresql://user:pass@host/db"),
            "postgresql")

    def test_postgres(self):
        self.assertEqual(
            dbt._detectar_driver("postgres://user:pass@host/db"),
            "postgresql")

    def test_mysql(self):
        self.assertEqual(
            dbt._detectar_driver("mysql://user:pass@host/db"), "mysql")

    def test_driver_forzado(self):
        self.assertEqual(
            dbt._detectar_driver("algo://x", driver="sqlite"), "sqlite")

    def test_url_desconocida_sin_driver(self):
        with self.assertRaises(ValueError):
            dbt._detectar_driver("ftp://host/db")


class TestDbConnectSqlite(unittest.TestCase):
    """Conexión y desconexión con SQLite en memoria."""

    def setUp(self):
        dbt.reiniciar()

    def tearDown(self):
        dbt.reiniciar()

    def test_connect_memory(self):
        resultado = dbt.db_connect("sqlite:///:memory:")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["driver"], "sqlite")
        self.assertIn("Conectado", resultado["mensaje"])

    def test_connect_url_vacia(self):
        resultado = dbt.db_connect("")
        self.assertFalse(resultado["ok"])
        self.assertIn("URL", resultado.get("error", ""))

    def test_connect_url_none(self):
        resultado = dbt.db_connect(None)
        self.assertFalse(resultado["ok"])

    def test_disconnect(self):
        dbt.db_connect("sqlite:///:memory:")
        resultado = dbt.db_disconnect()
        self.assertTrue(resultado["ok"])


class TestDbQuerySqlite(unittest.TestCase):
    """Ejecución de consultas sobre SQLite en memoria."""

    def setUp(self):
        dbt.reiniciar()
        # Confirmador que siempre acepta (para tests).
        dbt.fijar_confirmador(lambda _consulta: True)
        dbt.db_connect("sqlite:///:memory:")
        # Crear tabla de prueba directamente en la conexión.
        conn = dbt._ESTADO["conexion"]
        conn.execute(
            "CREATE TABLE productos (id INTEGER PRIMARY KEY, nombre TEXT, "
            "precio REAL)")
        conn.execute(
            "INSERT INTO productos (nombre, precio) VALUES ('Widget', 9.99)")
        conn.execute(
            "INSERT INTO productos (nombre, precio) VALUES ('Gadget', 24.50)")
        conn.commit()

    def tearDown(self):
        dbt.reiniciar()

    def test_query_select(self):
        resultado = dbt.db_query("SELECT * FROM productos")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["filas"], 2)
        self.assertIn("nombre", resultado["columnas"])
        self.assertEqual(len(resultado["resultados"]), 2)

    def test_query_select_auto(self):
        """Con auto=True no pide confirmación."""
        dbt.fijar_confirmador(None)  # Sin confirmador → usaría input()
        resultado = dbt.db_query("SELECT 1", auto=True)
        self.assertTrue(resultado["ok"])

    def test_query_bloqueada(self):
        resultado = dbt.db_query("INSERT INTO productos VALUES (3, 'x', 1.0)",
                                 auto=True)
        self.assertFalse(resultado["ok"])
        self.assertIn("lectura", resultado["error"].lower())

    def test_query_sin_conexion(self):
        dbt.db_disconnect()
        resultado = dbt.db_query("SELECT 1", auto=True)
        self.assertFalse(resultado["ok"])
        self.assertIn("conexión", resultado["error"].lower())

    def test_query_vacia(self):
        resultado = dbt.db_query("", auto=True)
        self.assertFalse(resultado["ok"])

    def test_query_cancelada_por_usuario(self):
        """Confirmador que rechaza → cancelada."""
        dbt.fijar_confirmador(lambda _: False)
        resultado = dbt.db_query("SELECT 1")
        self.assertFalse(resultado["ok"])
        self.assertIn("cancelada", resultado["error"].lower())


class TestDbSchemaSqlite(unittest.TestCase):
    """Obtención de esquema sobre SQLite en memoria."""

    def setUp(self):
        dbt.reiniciar()
        dbt.db_connect("sqlite:///:memory:")
        conn = dbt._ESTADO["conexion"]
        conn.execute(
            "CREATE TABLE clientes (id INTEGER PRIMARY KEY, nombre TEXT, "
            "email TEXT)")
        conn.execute(
            "CREATE TABLE pedidos (id INTEGER PRIMARY KEY, "
            "cliente_id INTEGER, total REAL)")
        conn.commit()

    def tearDown(self):
        dbt.reiniciar()

    def test_schema_lista_tablas(self):
        resultado = dbt.db_schema()
        self.assertTrue(resultado["ok"])
        nombres = [t["nombre"] for t in resultado["tablas"]]
        self.assertIn("clientes", nombres)
        self.assertIn("pedidos", nombres)

    def test_schema_columnas(self):
        resultado = dbt.db_schema()
        self.assertTrue(resultado["ok"])
        clientes = [t for t in resultado["tablas"]
                     if t["nombre"] == "clientes"][0]
        cols = [c["nombre"] for c in clientes["columnas"]]
        self.assertIn("id", cols)
        self.assertIn("nombre", cols)
        self.assertIn("email", cols)

    def test_schema_sin_conexion(self):
        dbt.db_disconnect()
        resultado = dbt.db_schema()
        self.assertFalse(resultado["ok"])


class TestReiniciar(unittest.TestCase):
    """reiniciar() cierra conexión y limpia confirmador."""

    def test_reiniciar_limpia_todo(self):
        dbt.db_connect("sqlite:///:memory:")
        dbt.fijar_confirmador(lambda _: True)
        dbt.reiniciar()
        self.assertIsNone(dbt._ESTADO.get("conexion"))
        self.assertIsNone(dbt._CONFIRMAR)


if __name__ == "__main__":
    unittest.main()
