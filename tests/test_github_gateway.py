#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para github_gateway.py — Webhooks y API de GitHub (v6.8.0).

Ejecuta con:
    python -m unittest tests/test_github_gateway.py -v
"""

import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

import github_gateway as gh


class TestValidarFirma(unittest.TestCase):
    """Validación de firmas HMAC SHA-256 y SHA-1."""

    def setUp(self):
        self.secreto = "secreto-super-seguro-123"
        self.payload = b'{"action":"opened","number":42}'
        mac256 = hmac.new(self.secreto.encode("utf-8"), msg=self.payload, digestmod=hashlib.sha256).hexdigest()
        self.firma_sha256 = f"sha256={mac256}"
        mac1 = hmac.new(self.secreto.encode("utf-8"), msg=self.payload, digestmod=hashlib.sha1).hexdigest()
        self.firma_sha1 = f"sha1={mac1}"

    def test_firma_sha256_valida(self):
        self.assertTrue(gh.validar_firma(self.payload, self.firma_sha256, self.secreto))

    def test_firma_sha256_con_payload_str(self):
        self.assertTrue(gh.validar_firma(self.payload.decode("utf-8"), self.firma_sha256, self.secreto))

    def test_firma_sha1_valida(self):
        self.assertTrue(gh.validar_firma(self.payload, self.firma_sha1, self.secreto))

    def test_firma_invalida(self):
        self.assertFalse(gh.validar_firma(self.payload, "sha256=abcdef123456", self.secreto))

    def test_sin_secreto(self):
        self.assertFalse(gh.validar_firma(self.payload, self.firma_sha256, secreto=""))

    def test_cabecera_formato_invalido(self):
        self.assertFalse(gh.validar_firma(self.payload, "invalido", self.secreto))

    def test_algoritmo_no_soportado(self):
        self.assertFalse(gh.validar_firma(self.payload, "md5=1234", self.secreto))


class TestParsearEvento(unittest.TestCase):
    """Parseo de distintos eventos de webhook de GitHub."""

    def test_parsear_pull_request(self):
        payload = {
            "action": "opened",
            "number": 10,
            "repository": {"full_name": "owner/repo"},
            "sender": {"login": "octocat"},
            "pull_request": {
                "number": 10,
                "title": "Añadir nueva feature",
                "body": "Descripción del PR",
                "state": "open",
                "head": {"ref": "feature-branch", "sha": "abc1234"},
                "base": {"ref": "main"},
                "diff_url": "https://github.com/owner/repo/pull/10.diff",
                "user": {"login": "dev1"},
            },
        }
        res = gh.parsear_evento(payload, "pull_request")
        self.assertTrue(res["ok"])
        self.assertEqual(res["tipo_evento"], "pull_request")
        self.assertEqual(res["accion"], "opened")
        self.assertEqual(res["numero"], 10)
        self.assertEqual(res["titulo"], "Añadir nueva feature")
        self.assertEqual(res["rama_origen"], "feature-branch")

    def test_parsear_issues(self):
        payload = {
            "action": "opened",
            "issue": {
                "number": 5,
                "title": "Bug en el login",
                "body": "Pasos para reproducir...",
                "state": "open",
                "user": {"login": "reporter"},
                "labels": [{"name": "bug"}],
            },
            "repository": {"full_name": "owner/repo"},
        }
        res = gh.parsear_evento(payload, "issues")
        self.assertTrue(res["ok"])
        self.assertEqual(res["numero"], 5)
        self.assertEqual(res["titulo"], "Bug en el login")
        self.assertIn("bug", res["etiquetas"])

    def test_parsear_push(self):
        payload = {
            "ref": "refs/heads/main",
            "after": "deadbeef",
            "head_commit": {
                "message": "fix: resolver crash",
                "author": {"name": "Developer"},
                "modified": ["app.py"],
            },
            "commits": [{"id": "deadbeef"}],
        }
        res = gh.parsear_evento(payload, "push")
        self.assertTrue(res["ok"])
        self.assertEqual(res["rama"], "main")
        self.assertEqual(res["mensaje_commit"], "fix: resolver crash")

    def test_parsear_issue_comment(self):
        payload = {
            "action": "created",
            "issue": {"number": 12, "pull_request": {}},
            "comment": {
                "body": "/snap arreglar test fallido",
                "user": {"login": "reviewer"},
            },
        }
        res = gh.parsear_evento(payload, "issue_comment")
        self.assertTrue(res["ok"])
        self.assertTrue(res["es_pr"])
        self.assertEqual(res["cuerpo_comentario"], "/snap arreglar test fallido")

    def test_parsear_json_invalido(self):
        res = gh.parsear_evento("esto no es json", "pull_request")
        self.assertFalse(res["ok"])


class TestProcesarEvento(unittest.TestCase):
    """Encolado de tareas según el tipo de evento."""

    def test_procesar_evento_pr(self):
        evento = {
            "ok": True,
            "tipo_evento": "pull_request",
            "accion": "opened",
            "numero": 42,
            "titulo": "Nuevo endpoint",
            "repositorio": "org/proyecto",
        }
        tid = gh.procesar_evento(evento, chat_id="12345", canal="telegram", db_path=":memory:")
        self.assertIsNotNone(tid)

    def test_procesar_evento_push(self):
        evento = {
            "ok": True,
            "tipo_evento": "push",
            "rama": "main",
        }
        tid = gh.procesar_evento(evento, db_path=":memory:")
        self.assertIsNotNone(tid)

    def test_procesar_evento_invalido(self):
        tid = gh.procesar_evento({"ok": False})
        self.assertIsNone(tid)


class TestApiGithub(unittest.TestCase):
    """Llamadas mockeadas a la API de GitHub."""

    @patch("httpx.Client.get")
    def test_obtener_pr_diff(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
        mock_get.return_value = mock_resp

        diff = gh.obtener_pr_diff("owner/repo", 1, token="fake-token")
        self.assertIn("+new", diff)

    @patch("httpx.Client.post")
    def test_comentar_pr(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        ok = gh.comentar_pr("owner/repo", 1, "Excelente trabajo!", token="fake-token")
        self.assertTrue(ok)

    @patch("httpx.Client.post")
    def test_configurar_webhook(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_post.return_value = mock_resp

        ok, msg = gh.configurar_webhook(
            url="https://ejemplo.com",
            secreto="secreto",
            repo="owner/repo",
            token="fake-token",
        )
        self.assertTrue(ok)
        self.assertIn("exitosamente", msg)


if __name__ == "__main__":
    unittest.main()
