#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de integración para Omnicanalidad Avanzada (v6.8.0).

Verifica la integración de comandos asíncronos en Telegram y Discord,
la configuración de flags CLI y la coherencia del sistema.

Ejecuta con:
    python -m unittest tests/test_omnicanalidad_avanzada.py -v
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import snapcontext as sc
import task_queue as tq


class TestTelegramAsyncCommands(unittest.TestCase):
    """Comandos asíncronos en Telegram Gateway."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_tg.db"
        tq.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_telegram_comando_pr(self):
        import telegram_gateway as tg

        with patch("task_queue.DB_PATH", self.db_path):
            resp = asyncio.run(tg.run_agent_async("/pr 42", chat_id="12345"))
            self.assertIn("🔔 Tarea encolada", resp)
            self.assertIn("42", resp)

    def test_telegram_comando_tests(self):
        import telegram_gateway as tg

        with patch("task_queue.DB_PATH", self.db_path):
            resp = asyncio.run(tg.run_agent_async("/tests feature-x", chat_id="12345"))
            self.assertIn("🔔 Tarea encolada", resp)
            self.assertIn("feature-x", resp)

    def test_telegram_comando_status(self):
        import telegram_gateway as tg

        with patch("task_queue.DB_PATH", self.db_path):
            tq.encolar_tarea("tests", {"rama": "main"}, db_path=self.db_path)
            resp = asyncio.run(tg.run_agent_async("/status", chat_id="12345"))
            self.assertIn("Estado de Tareas", resp)

    def test_telegram_comando_cancel(self):
        import telegram_gateway as tg

        with patch("task_queue.DB_PATH", self.db_path):
            tid = tq.encolar_tarea("tests", {"rama": "main"}, db_path=self.db_path)
            resp = asyncio.run(tg.run_agent_async(f"/cancel {tid}", chat_id="12345"))
            self.assertIn("cancelada", resp.lower())


class TestDiscordAsyncCommands(unittest.TestCase):
    """Comandos asíncronos en Discord Gateway."""

    def test_discord_extraer_comando_pr(self):
        import discord_gateway as dg

        data = {
            "type": 2,
            "data": {"name": "pr", "options": [{"value": "101"}]},
        }
        cmd, consulta, argv = dg._extraer_consulta(data)
        self.assertEqual(cmd, "pr")
        self.assertEqual(consulta, "101")

    def test_discord_extraer_comando_tests(self):
        import discord_gateway as dg

        data = {
            "type": 2,
            "data": {"name": "tests", "options": [{"value": "staging"}]},
        }
        cmd, consulta, argv = dg._extraer_consulta(data)
        self.assertEqual(cmd, "tests")
        self.assertEqual(consulta, "staging")

    def test_discord_extraer_comando_status(self):
        import discord_gateway as dg

        data = {
            "type": 2,
            "data": {"name": "status"},
        }
        cmd, consulta, argv = dg._extraer_consulta(data)
        self.assertEqual(cmd, "status")

    def test_discord_extraer_comando_cancel(self):
        import discord_gateway as dg

        data = {
            "type": 2,
            "data": {"name": "cancel", "options": [{"value": "5"}]},
        }
        cmd, consulta, argv = dg._extraer_consulta(data)
        self.assertEqual(cmd, "cancel")
        self.assertEqual(consulta, "5")


class TestCliFlagsYVersion(unittest.TestCase):
    """Flags CLI y versión 6.8.0."""

    def test_cli_args_github(self):
        parser = sc.crear_parser()
        args = parser.parse_args([
            "--github-webhook-secreto", "mi-secreto",
            "--github-token", "ghp_123456",
            "--webhook-url", "https://hook.ejemplo.com",
            "tarea",
        ])
        self.assertEqual(args.github_webhook_secreto, "mi-secreto")
        self.assertEqual(args.github_token, "ghp_123456")
        self.assertEqual(args.webhook_url, "https://hook.ejemplo.com")

    def test_version_6_8_0(self):
        self.assertEqual(sc.VERSION, "6.17.0")


if __name__ == "__main__":
    unittest.main()
