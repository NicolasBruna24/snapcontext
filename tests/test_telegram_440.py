#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v4.4.0: gateway de omnicanalidad (Telegram)."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import telegram_gateway as tg  # noqa: E402


class TestConfiguracion(unittest.TestCase):
    def setUp(self):
        self._limpiar_env()

    def tearDown(self):
        self._limpiar_env()

    def _limpiar_env(self):
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_WEBHOOK_URL", None)

    def test_token_desde_variable_de_entorno(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "tok-env"
        self.assertEqual(tg.obtener_token(), "tok-env")

    def test_token_desde_config_json(self):
        with mock.patch.object(tg, "_ruta_config",
                               return_value=self._cfg(
                                   {"telegram": {"bot_token": "tok-cfg"}})):
            self.assertEqual(tg.obtener_token(), "tok-cfg")

    def _cfg(self, datos):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(datos, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def test_sin_configuracion_devuelve_none(self):
        with mock.patch.object(tg, "_ruta_config",
                               return_value=self._cfg({})):
            self.assertIsNone(tg.obtener_token())
            self.assertIsNone(tg.obtener_webhook_url())

    def test_guardar_configuracion(self):
        destino = self._cfg({})
        with mock.patch.object(tg, "_ruta_config", return_value=destino):
            seccion = tg.guardar_configuracion_telegram("123:ABC",
                                                        "https://x.ngrok.io")
        guardado = json.loads(destino.read_text(encoding="utf-8"))
        self.assertEqual(guardado["telegram"]["bot_token"], "123:ABC")
        self.assertEqual(guardado["telegram"]["webhook_url"],
                         "https://x.ngrok.io")
        self.assertEqual(seccion["webhook_url"], "https://x.ngrok.io")


class TestLimpiezaComandos(unittest.TestCase):
    def test_consulta_simple(self):
        self.assertEqual(tg._limpiar_consulta("arregla el login"),
                         ("arregla el login", []))

    def test_fix_mapea_a_test_loop(self):
        consulta, extra = tg._limpiar_consulta("/fix arreglar el checkout")
        self.assertEqual((consulta, extra),
                         ("arreglar el checkout", ["--test-loop"]))

    def test_plan_mapea_a_planificador(self):
        consulta, extra = tg._limpiar_consulta("/plan migrar a pytest")
        self.assertEqual((consulta, extra),
                         ("migrar a pytest", ["--plan"]))

    def test_start_y_help_vacios(self):
        self.assertEqual(tg._limpiar_consulta("/start"), ("", []))
        self.assertEqual(tg._limpiar_consulta("/help"), ("", []))


class TestEnvioMensajes(unittest.TestCase):
    def test_send_message_ok(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"ok": True}
        cliente = mock.AsyncMock()
        cliente.post = mock.AsyncMock(return_value=resp)
        cliente.__aenter__ = mock.AsyncMock(return_value=cliente)
        cliente.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch.object(tg, "obtener_token", return_value="T"), \
                mock.patch.object(tg.httpx, "AsyncClient",
                                  return_value=cliente) as fake_cli:
            ok = asyncio.run(
                tg.send_telegram_message(42, "hola *mundo*"))
        self.assertTrue(ok)
        cuerpo = fake_cli.return_value.post.call_args  # AsyncClient(**kw)
        args = cliente.post.call_args
        self.assertIn("/sendMessage", args[0][0])
        self.assertEqual(args[1]["json"]["chat_id"], 42)

    def test_mensaje_largo_se_envia_como_documento(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"ok": True}
        cliente = mock.Mock()
        cliente.post = mock.AsyncMock(return_value=resp)
        cliente.__aenter__ = mock.AsyncMock(return_value=cliente)
        cliente.__aexit__ = mock.AsyncMock(return_value=False)
        texto = "x" * (tg.TELEGRAM_MAX_MENSAJE + 500)
        with mock.patch.object(tg, "obtener_token", return_value="T"), \
                mock.patch.object(tg.httpx, "AsyncClient",
                                  return_value=cliente):
            ok = asyncio.run(tg.send_telegram_message(42, texto))
        self.assertTrue(ok)
        llamadas = cliente.post.call_args_list
        rutas = [c.args[0] for c in llamadas]
        self.assertTrue(any(r.endswith("/sendDocument") for r in rutas))

    def test_error_de_red_no_lanza(self):
        cliente = mock.Mock()
        cliente.__aenter__ = mock.AsyncMock(side_effect=OSError("sin red"))
        cliente.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch.object(tg, "obtener_token", return_value="T"), \
                mock.patch.object(tg.httpx, "AsyncClient",
                                  return_value=cliente):
            ok = asyncio.run(tg.send_telegram_message(42, "hola"))
        self.assertFalse(ok)

    def test_sin_token_devuelve_false(self):
        with mock.patch.object(tg, "obtener_token", return_value=None):
            self.assertFalse(asyncio.run(tg.send_telegram_message(42, "x")))


class TestHandlerUpdates(unittest.TestCase):
    def _update(self, texto, chat_id=42):
        return {"update_id": 1,
                "message": {"message_id": 1,
                            "chat": {"id": chat_id},
                            "text": texto}}

    def test_start_responde_bienvenida(self):
        enviados = []
        async def fake_send(cid, txt, **kw):
            enviados.append((cid, txt))
        with mock.patch.object(tg, "send_telegram_message",
                               side_effect=fake_send):
            asyncio.run(tg.handle_telegram_update(self._update("/start")))
        self.assertEqual(len(enviados), 1)
        self.assertIn("SnapContext", enviados[0][1])

    def test_update_invalido_ignorado(self):
        # Sin texto ni chat no debe lanzar ni enviar nada.
        asyncio.run(tg.handle_telegram_update({"message": {}}))

    def test_mensaje_lanza_tarea_en_segundo_plano(self):
        enviados = []
        async def fake_send(cid, txt, **kw):
            enviados.append(txt)
        async def flujo():
            with mock.patch.object(tg, "send_telegram_message",
                                   side_effect=fake_send), \
                    mock.patch.object(tg, "run_agent_async",
                                      return_value="✅ listo"):
                await tg.handle_telegram_update(self._update("arregla login"))
                # La respuesta llega en background; esperamos tareas.
                pendientes = [t for t in asyncio.all_tasks()
                              if t is not asyncio.current_task()]
                await asyncio.gather(*pendientes)
        asyncio.run(flujo())
        self.assertTrue(any("Procesando" in t for t in enviados))
        self.assertTrue(any("listo" in t for t in enviados))


class TestEndpointWebhook(unittest.TestCase):
    def test_webhook_endpoint(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi/httpx TestClient no disponible")
        from web.app import crear_app
        import telegram_gateway as tgmod
        app = crear_app(api_token="")
        cliente_http = TestClient(app)

        # Sin token → 503.
        with mock.patch.object(tgmod, "obtener_token", return_value=None):
            r = cliente_http.post("/webhook/telegram",
                                  json=self._update("/start"))
            self.assertEqual(r.status_code, 503)

        # Con token → 200 inmediato.
        async def nada(*a, **k):
            pass
        with mock.patch.object(tgmod, "obtener_token", return_value="T"), \
                mock.patch.object(tgmod, "handle_telegram_update",
                                  side_effect=nada):
            r = cliente_http.post("/webhook/telegram",
                                  json=self._update("/start"))
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])

    def _update(self, texto, chat_id=42):
        return {"message": {"chat": {"id": chat_id}, "text": texto}}


if __name__ == "__main__":
    unittest.main()
