#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para memory_store (v6.26.0) — memoria a largo plazo."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMemoryStore(unittest.TestCase):
    """Tests para memory_store.py."""

    def setUp(self):
        import memory_store as ms
        ms.configurar_memoria(True, 100)
        ms.limpiar_historial()

    def tearDown(self):
        import memory_store as ms
        ms.limpiar_historial()

    def test_configurar_memoria(self):
        import memory_store as ms
        ms.configurar_memoria(False, 50)
        self.assertFalse(ms.esta_activa())
        ms.configurar_memoria(True, 50)
        self.assertTrue(ms.esta_activa())

    def test_guardar_decision_basica(self):
        import memory_store as ms
        resultado = ms.guardar_decision(
            tarea="arreglar login",
            archivos_afectados=["auth.py"],
            descripcion="Usar JWT para autenticacion",
            resultado="exito",
        )
        self.assertTrue(resultado)
        self.assertEqual(ms.contar_decisiones(), 1)

    def test_guardar_decision_duplicada(self):
        import memory_store as ms
        ms.guardar_decision(tarea="test", archivos_afectados=["a.py"])
        ms.guardar_decision(tarea="test", archivos_afectados=["a.py"])
        # No debe duplicar
        self.assertEqual(ms.contar_decisiones(), 1)

    def test_guardar_decision_sin_tarea(self):
        import memory_store as ms
        resultado = ms.guardar_decision(tarea="")
        self.assertFalse(resultado)

    def test_guardar_decision_memoria_desactivada(self):
        import memory_store as ms
        ms.configurar_memoria(False)
        resultado = ms.guardar_decision(tarea="test")
        self.assertFalse(resultado)
        self.assertEqual(ms.contar_decisiones(), 0)

    def test_buscar_decisiones(self):
        import memory_store as ms
        ms.guardar_decision(
            tarea="arreglar login con JWT",
            descripcion="Implementar autenticacion",
        )
        ms.guardar_decision(
            tarea="optimizar consultas SQL",
            descripcion="Agregar indices",
        )
        resultados = ms.buscar_decisiones("login")
        self.assertGreater(len(resultados), 0)
        self.assertIn("login", resultados[0]["tarea"].lower())

    def test_buscar_decisiones_vacia(self):
        import memory_store as ms
        resultados = ms.buscar_decisiones("inexistente")
        self.assertEqual(resultados, [])

    def test_obtener_contexto_memoria(self):
        import memory_store as ms
        ms.guardar_decision(
            tarea="arreglar login",
            descripcion="Usar JWT para autenticacion",
            resultado="exito",
        )
        contexto = ms.obtener_contexto_memoria("login")
        self.assertIn("Decisiones previas similares", contexto)
        self.assertIn("exito", contexto)

    def test_obtener_contexto_memoria_vacio(self):
        import memory_store as ms
        contexto = ms.obtener_contexto_memoria("inexistente")
        self.assertEqual(contexto, "")

    def test_listar_decisiones(self):
        import memory_store as ms
        ms.guardar_decision(tarea="tarea1")
        ms.guardar_decision(tarea="tarea2")
        decisiones = ms.listar_decisiones(limite=10)
        self.assertEqual(len(decisiones), 2)

    def test_listar_decisiones_limite(self):
        import memory_store as ms
        for i in range(5):
            ms.guardar_decision(tarea=f"tarea{i}", archivos_afectados=[f"f{i}.py"])
        decisiones = ms.listar_decisiones(limite=3)
        self.assertEqual(len(decisiones), 3)

    def test_limitar_historial(self):
        import memory_store as ms
        ms.configurar_memoria(True, 5)
        for i in range(10):
            ms.guardar_decision(tarea=f"tarea{i}", archivos_afectados=[f"f{i}.py"])
        self.assertLessEqual(ms.contar_decisiones(), 5)

    def test_limpiar_historial(self):
        import memory_store as ms
        ms.guardar_decision(tarea="test1")
        ms.guardar_decision(tarea="test2")
        eliminados = ms.limpiar_historial()
        self.assertEqual(eliminados, 2)
        self.assertEqual(ms.contar_decisiones(), 0)

    def test_generar_hash(self):
        import memory_store as ms
        hash1 = ms._generar_hash("tarea", ["a.py", "b.py"])
        hash2 = ms._generar_hash("tarea", ["a.py", "b.py"])
        hash3 = ms._generar_hash("tarea", ["b.py", "a.py"])  # Mismo hash (orden no importa)
        self.assertEqual(hash1, hash2)
        self.assertEqual(hash1, hash3)

    def test_metadatos_json(self):
        import memory_store as ms
        ms.guardar_decision(
            tarea="test",
            metadatos={"tiempo": 100, "tokens": 50},
        )
        resultados = ms.buscar_decisiones("test")
        self.assertEqual(len(resultados), 1)


class TestMemoriaFlags(unittest.TestCase):
    """Tests para flags CLI de memoria."""

    def test_flag_memoria_existe(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["test"])
        self.assertTrue(hasattr(args, "memoria"))
        self.assertTrue(args.memoria)

    def test_flag_no_memoria(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--no-memoria", "test"])
        self.assertFalse(args.memoria)

    def test_flag_memoria_limite(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--memoria-limite", "50", "test"])
        self.assertEqual(args.memoria_limite, 50)

    def test_flag_memoria_ver(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--memoria-ver"])
        self.assertTrue(args.memoria_ver)


if __name__ == "__main__":
    unittest.main()
