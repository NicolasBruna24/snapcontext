#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para task_queue.py — Cola de Tareas Asíncronas y Worker (v6.8.0).

Ejecuta con:
    python -m unittest tests/test_task_queue.py -v
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import task_queue as tq


class TestTaskQueueOperaciones(unittest.TestCase):
    """Operaciones básicas sobre la cola SQLite."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_cola.db"
        tq.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_encolar_tarea(self):
        tid = tq.encolar_tarea("tests", {"rama": "develop"}, chat_id="123", canal="telegram", db_path=self.db_path)
        self.assertIsInstance(tid, int)
        self.assertGreater(tid, 0)

    def test_consumir_tarea(self):
        tq.encolar_tarea("pr_review", {"numero": 99}, db_path=self.db_path)
        tarea = tq.consumir_tarea(db_path=self.db_path)
        self.assertIsNotNone(tarea)
        self.assertEqual(tarea["tipo"], "pr_review")
        self.assertEqual(tarea["estado"], "ejecutando")
        self.assertEqual(tarea["datos"]["numero"], 99)

    def test_consumir_cola_vacia(self):
        tarea = tq.consumir_tarea(db_path=self.db_path)
        self.assertIsNone(tarea)

    def test_actualizar_estado_tarea(self):
        tid = tq.encolar_tarea("plan", {"consulta": "refactor"}, db_path=self.db_path)
        ok = tq.actualizar_estado_tarea(tid, "completada", resultado={"ok": True}, db_path=self.db_path)
        self.assertTrue(ok)
        tarea = tq.obtener_tarea(tid, db_path=self.db_path)
        self.assertEqual(tarea["estado"], "completada")
        self.assertEqual(tarea["resultado"], {"ok": True})

    def test_obtener_tarea(self):
        tid = tq.encolar_tarea("tests", {"rama": "main"}, chat_id="chat99", canal="discord", db_path=self.db_path)
        tarea = tq.obtener_tarea(tid, db_path=self.db_path)
        self.assertIsNotNone(tarea)
        self.assertEqual(tarea["chat_id"], "chat99")
        self.assertEqual(tarea["canal"], "discord")

    def test_obtener_tarea_inexistente(self):
        tarea = tq.obtener_tarea(9999, db_path=self.db_path)
        self.assertIsNone(tarea)

    def test_listar_tareas_todas(self):
        tq.encolar_tarea("tests", {"n": 1}, db_path=self.db_path)
        tq.encolar_tarea("plan", {"n": 2}, db_path=self.db_path)
        tareas = tq.listar_tareas(db_path=self.db_path)
        self.assertEqual(len(tareas), 2)

    def test_listar_tareas_filtro_estado(self):
        t1 = tq.encolar_tarea("tests", {"n": 1}, db_path=self.db_path)
        t2 = tq.encolar_tarea("plan", {"n": 2}, db_path=self.db_path)
        tq.actualizar_estado_tarea(t1, "completada", db_path=self.db_path)

        pendientes = tq.listar_tareas(estados=["pendiente"], db_path=self.db_path)
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]["id"], t2)

        completadas = tq.listar_tareas(estados=["completada"], db_path=self.db_path)
        self.assertEqual(len(completadas), 1)
        self.assertEqual(completadas[0]["id"], t1)

    def test_cancelar_tarea_pendiente(self):
        tid = tq.encolar_tarea("tests", {}, db_path=self.db_path)
        ok = tq.cancelar_tarea(tid, db_path=self.db_path)
        self.assertTrue(ok)
        tarea = tq.obtener_tarea(tid, db_path=self.db_path)
        self.assertEqual(tarea["estado"], "cancelada")

    def test_cancelar_tarea_ya_ejecutada(self):
        tid = tq.encolar_tarea("tests", {}, db_path=self.db_path)
        tq.actualizar_estado_tarea(tid, "completada", db_path=self.db_path)
        ok = tq.cancelar_tarea(tid, db_path=self.db_path)
        self.assertFalse(ok)


class TestTaskExecution(unittest.TestCase):
    """Pruebas de ejecución de tareas y notificaciones."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_exec.db"
        tq.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("snapcontext._ejecutar_comando")
    def test_ejecutar_tarea_tests(self, mock_cmd):
        mock_cmd.return_value = (0, "10 passed", "")
        tarea = {"tipo": "tests", "datos": {"rama": "main"}}
        res = tq.ejecutar_tarea(tarea)
        self.assertTrue(res["ok"])
        self.assertEqual(res["codigo_salida"], 0)

    @patch("snapcontext._ejecutar_planificador")
    def test_ejecutar_tarea_plan(self, mock_plan):
        mock_plan.return_value = 0
        tarea = {"tipo": "plan", "datos": {"consulta": "refactorizar"}}
        res = tq.ejecutar_tarea(tarea)
        self.assertTrue(res["ok"])

    @patch("snapcontext.flujo_principal")
    def test_ejecutar_tarea_generica(self, mock_flujo):
        mock_flujo.return_value = 0
        tarea = {"tipo": "custom", "datos": {"instruccion": "hacer algo"}}
        res = tq.ejecutar_tarea(tarea)
        self.assertTrue(res["ok"])

    @patch("task_queue.enviar_notificacion")
    @patch("task_queue.ejecutar_tarea")
    def test_procesar_siguiente_tarea_completo(self, mock_ejecutar, mock_notificar):
        mock_ejecutar.return_value = {"ok": True, "mensaje": "Completado con éxito"}
        mock_notificar.return_value = True

        tid = tq.encolar_tarea("tests", {"rama": "main"}, chat_id="user123", canal="telegram", db_path=self.db_path)
        res = tq.procesar_siguiente_tarea(db_path=self.db_path)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], tid)
        self.assertEqual(res["estado"], "completada")
        mock_notificar.assert_called_once()

    @patch("telegram_gateway.send_telegram_message")
    def test_enviar_notificacion_telegram(self, mock_send):
        mock_send.return_value = True
        ok = tq.enviar_notificacion("chat123", "Hola Mundo", canal="telegram")
        # enviar_notificacion devuelve bool
        self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()
