#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para mcp_tools_api.py — herramientas MCP de APIs externas (v6.7.0).

Ejecuta con:
    python -m pytest tests/test_mcp_tools_api.py -v
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import mcp_tools_api as apit


# ── Helpers ────────────────────────────────────────────────────────────


def _mock_response(status_code=200, text="OK", json_data=None,
                   headers=None, url="https://api.example.com",
                   content=b"OK", history=None):
    """Crea un objeto respuesta mock compatible con httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = content or text.encode("utf-8", errors="replace")
    resp.url = url
    resp.history = history or []
    _headers = {
        "content-type": "application/json" if json_data is not None
        else "text/plain",
        "server": "MockServer/1.0",
    }
    if headers:
        _headers.update(headers)
    resp.headers = _headers

    def _json():
        if json_data is not None:
            return json_data
        raise ValueError("No JSON")
    resp.json = _json
    return resp


class TestValidarUrl(unittest.TestCase):
    """Validación de URLs HTTP/HTTPS."""

    def test_url_https_valida(self):
        self.assertEqual(
            apit._validar_url("https://api.example.com/v1"),
            "https://api.example.com/v1")

    def test_url_http_valida(self):
        self.assertEqual(
            apit._validar_url("http://localhost:8080"),
            "http://localhost:8080")

    def test_url_vacia(self):
        with self.assertRaises(ValueError):
            apit._validar_url("")

    def test_url_sin_esquema(self):
        with self.assertRaises(ValueError):
            apit._validar_url("ftp://files.example.com")

    def test_url_sin_host(self):
        with self.assertRaises(ValueError):
            apit._validar_url("http://")


class TestFiltrarCabeceras(unittest.TestCase):
    """Filtrado de cabeceras sensibles."""

    def test_oculta_authorization(self):
        resultado = apit._filtrar_cabeceras({
            "Authorization": "Bearer token123",
            "Content-Type": "application/json",
        })
        self.assertEqual(resultado["Authorization"], "***")
        self.assertEqual(resultado["Content-Type"], "application/json")

    def test_oculta_cookie(self):
        resultado = apit._filtrar_cabeceras({
            "Cookie": "session=abc123",
        })
        self.assertEqual(resultado["Cookie"], "***")

    def test_cabeceras_vacias(self):
        resultado = apit._filtrar_cabeceras({})
        self.assertEqual(resultado, {})


class TestApiRequest(unittest.TestCase):
    """Peticiones HTTP via api_request (con httpx mockeado)."""

    @patch.object(apit, "_httpx")
    def test_get_ok(self, mock_httpx_fn):
        mock_httpx = MagicMock()
        mock_httpx_fn.return_value = mock_httpx
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(
            return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(
            return_value=False)
        mock_client.request.return_value = _mock_response(
            200, '{"status":"ok"}',
            json_data={"status": "ok"})

        resultado = apit.api_request("https://api.example.com/v1")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["status"], 200)
        self.assertIn("body", resultado)

    @patch.object(apit, "_httpx")
    def test_post_ok(self, mock_httpx_fn):
        mock_httpx = MagicMock()
        mock_httpx_fn.return_value = mock_httpx
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(
            return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(
            return_value=False)
        mock_client.request.return_value = _mock_response(
            201, '{"id":42}', json_data={"id": 42})

        resultado = apit.api_request(
            "https://api.example.com/items", metodo="POST",
            body='{"nombre":"test"}')
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["status"], 201)

    def test_metodo_invalido(self):
        resultado = apit.api_request(
            "https://api.example.com", metodo="TRACE")
        self.assertFalse(resultado["ok"])
        self.assertIn("no soportado", resultado["error"].lower())

    def test_url_invalida(self):
        resultado = apit.api_request("no-es-una-url")
        self.assertFalse(resultado["ok"])

    @patch.object(apit, "_httpx")
    def test_timeout(self, mock_httpx_fn):
        mock_httpx = MagicMock()
        mock_httpx_fn.return_value = mock_httpx
        # Simular TimeoutException como una Exception genérica del mock
        mock_httpx.Client.return_value.__enter__ = MagicMock(
            side_effect=Exception("Timeout"))
        mock_httpx.Client.return_value.__exit__ = MagicMock(
            return_value=False)

        resultado = apit.api_request("https://api.example.com", timeout=1)
        self.assertFalse(resultado["ok"])

    @patch.object(apit, "_httpx")
    def test_json_parseado(self, mock_httpx_fn):
        mock_httpx = MagicMock()
        mock_httpx_fn.return_value = mock_httpx
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(
            return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(
            return_value=False)
        data = {"items": [1, 2, 3], "total": 3}
        mock_client.request.return_value = _mock_response(
            200, '{"items":[1,2,3],"total":3}', json_data=data)

        resultado = apit.api_request("https://api.example.com/items")
        self.assertTrue(resultado["ok"])
        self.assertIn("json", resultado)
        self.assertEqual(resultado["json"]["total"], 3)


class TestApiInspect(unittest.TestCase):
    """Inspección de API via api_inspect."""

    @patch.object(apit, "api_request")
    def test_inspect_ok(self, mock_req):
        mock_req.return_value = {
            "ok": True, "status": 200, "tiempo": 0.123,
            "body": "respuesta de ejemplo",
            "headers": {
                "content-type": "text/html",
                "server": "nginx/1.25",
            },
            "url": "https://example.com",
        }
        resultado = apit.api_inspect("https://example.com")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["status"], 200)
        self.assertIn("tiempo", resultado)
        self.assertIn("tamaño", resultado)
        self.assertIn("content_type", resultado)

    @patch.object(apit, "api_request")
    def test_inspect_error_propagado(self, mock_req):
        mock_req.return_value = {
            "ok": False, "error": "Error de conexión",
        }
        resultado = apit.api_inspect("https://down.example.com")
        self.assertFalse(resultado["ok"])
        self.assertIn("error", resultado)


class TestReiniciar(unittest.TestCase):
    """reiniciar() presente por simetría."""

    def test_reiniciar_no_falla(self):
        # No debe lanzar.
        apit.reiniciar()


if __name__ == "__main__":
    unittest.main()
