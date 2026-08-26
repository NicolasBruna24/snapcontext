#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v4.5.0: gateway de omnicanalidad (Discord)."""

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

import discord_gateway as dg  # noqa: E402


def _cfg(datos) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                      encoding="utf-8")
    json.dump(datos, tmp)
    tmp.close()
    return Path(tmp.name)


async def esperar_tareas(resultado):
    """Espera las tareas creadas con asyncio.create_task antes de volver."""
    pendientes = [t for t in asyncio.all_tasks()
                  if t is not asyncio.current_task()]
    if pendientes:
        await asyncio.gather(*pendientes)
    return resultado


class BaseDiscord(unittest.TestCase):
    def setUp(self):
        for var in ("DISCORD_PUBLIC_KEY", "DISCORD_APPLICATION_ID",
                    "DISCORD_BOT_TOKEN", "DISCORD_WEBHOOK_URL"):
            os.environ.pop(var, None)

    def tearDown(self):
        self.setUp()

    def _con_config(self, datos):
        ruta = _cfg(datos)
        self.addCleanup(os.unlink, ruta)
        return mock.patch.object(dg, "_ruta_config", return_value=ruta)

    @staticmethod
    def _interaccion(comando="snap", consulta=None, tipo=2):
        datos = {"type": tipo, "id": "999", "token": "TOK",
                 "application_id": "APP",
                 "channel": {"id": 42},
                 "data": {"name": comando}}
        if consulta is not None:
            datos["data"]["options"] = [{"name": "tarea",
                                         "type": 3, "value": consulta}]
        return datos


class TestConfiguracion(BaseDiscord):
    def test_valores_desde_variables_de_entorno(self):
        os.environ["DISCORD_PUBLIC_KEY"] = "pk-env"
        os.environ["DISCORD_BOT_TOKEN"] = "tok-env"
        with self._con_config({}):
            self.assertEqual(dg.obtener_public_key(), "pk-env")
            self.assertEqual(dg.obtener_bot_token(), "tok-env")

    def test_valores_desde_config_json(self):
        config = {"discord": {"public_key": "pk-cfg",
                              "application_id": "123", "bot_token": "tok"}}
        with self._con_config(config):
            self.assertEqual(dg.obtener_public_key(), "pk-cfg")
            self.assertEqual(dg.obtener_application_id(), "123")
            self.assertEqual(dg.obtener_bot_token(), "tok")

    def test_guardar_configuracion(self):
        destino = _cfg({})
        self.addCleanup(os.unlink, destino)
        with mock.patch.object(dg, "_ruta_config", return_value=destino):
            dg.guardar_configuracion_discord(
                "PK", "APPID", "TOKEN", "https://discord.com/api/webhooks/x")
        guardado = json.loads(destino.read_text(encoding="utf-8"))
        self.assertEqual(guardado["discord"]["public_key"], "PK")
        self.assertEqual(guardado["discord"]["application_id"], "APPID")
        self.assertEqual(guardado["discord"]["bot_token"], "TOKEN")


# Claves del vector de prueba RFC 8032 (Ed25519 #1).
_PRIVADA_HEX = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
_PUBLICA_HEX = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"


class TestVerificacionFirma(BaseDiscord):
    def test_firma_valida_aceptada(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
        privada = Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(_PRIVADA_HEX))
        cuerpo = b'{"type": 1}'
        timestamp = "1700000000"
        firma = privada.sign(timestamp.encode() + cuerpo).hex()
        with mock.patch.object(dg, "obtener_public_key",
                               return_value=_PUBLICA_HEX):
            self.assertTrue(dg.verify_signature(cuerpo, firma, timestamp))

    def test_firma_invalida_lanza_valueerror(self):
        with mock.patch.object(dg, "obtener_public_key",
                               return_value=_PUBLICA_HEX):
            with self.assertRaises(ValueError):
                dg.verify_signature(b'{"type": 1}', "ab" * 64, "1700000000")

    def test_firma_mal_formada_lanza_valueerror(self):
        with mock.patch.object(dg, "obtener_public_key",
                               return_value=_PUBLICA_HEX):
            with self.assertRaises(ValueError):
                dg.verify_signature(b"x", "no-es-hex", "123")

    def test_sin_public_key_lanza_valueerror(self):
        with mock.patch.object(dg, "obtener_public_key", return_value=None):
            with self.assertRaises(ValueError):
                dg.verify_signature(b"x", "ab" * 64, "123")


class TestHandlerInteracciones(BaseDiscord):
    def test_ping_responde_pong(self):
        self.assertEqual(asyncio.run(dg.handle_discord_interaction(
            self._interaccion(tipo=1))), {"type": 1})

    def test_comando_devuelve_respuesta_diferida(self):
        async def flujo():
            async def nada(*a, **k):
                pass
            with mock.patch.object(dg, "send_discord_message",
                                   side_effect=nada), \
                    mock.patch("telegram_gateway._ejecutar_pipeline",
                               return_value="✅ listo"):
                resultado = await dg.handle_discord_interaction(
                    self._interaccion("snap", "arregla login"))
                return await esperar_tareas(resultado)
        self.assertEqual(asyncio.run(flujo()), {"type": 5})

    def test_respuesta_se_envia_en_segundo_plano(self):
        enviados = []
        async def fake_send(url, contenido, **kw):
            enviados.append(contenido)
        async def flujo():
            with mock.patch.object(dg, "send_discord_message",
                                   side_effect=fake_send), \
                    mock.patch("telegram_gateway._ejecutar_pipeline",
                               return_value="✅ listo"):
                resultado = await dg.handle_discord_interaction(
                    self._interaccion("snap", "arregla login"))
                await esperar_tareas(resultado)
        asyncio.run(flujo())
        self.assertTrue(any("listo" in t for t in enviados))

    def test_start_envia_bienvenida(self):
        enviados = []
        async def fake_send(url, contenido, **kw):
            enviados.append(contenido)
        async def flujo():
            with mock.patch.object(dg, "send_discord_message",
                                   side_effect=fake_send):
                resultado = await dg.handle_discord_interaction(
                    self._interaccion("start"))
                await esperar_tareas(resultado)
        asyncio.run(flujo())
        self.assertTrue(any("SnapContext" in t for t in enviados))

    def test_extraer_consulta(self):
        comando, consulta, extra = dg._extraer_consulta(
            self._interaccion("fix", "arreglar el checkout"))
        self.assertEqual((comando, consulta, extra),
                         ("fix", "arreglar el checkout", ["--test-loop"]))
        comando, consulta, extra = dg._extraer_consulta(
            self._interaccion("plan", "migrar a pytest"))
        self.assertEqual((comando, consulta, extra),
                         ("plan", "migrar a pytest", ["--plan"]))
        # /start y /help se reconocen por nombre (respuesta directa).
        self.assertEqual(dg._extraer_consulta(
            self._interaccion("start")), ("start", "", []))
        self.assertEqual(dg._extraer_consulta(
            self._interaccion("help")), ("help", "", []))



class TestEnvioMensajes(BaseDiscord):
    @staticmethod
    def _cliente_mock(status=200):
        resp = mock.Mock(status_code=status)
        cliente = mock.Mock()
        cliente.post = mock.AsyncMock(return_value=resp)
        cliente.__aenter__ = mock.AsyncMock(return_value=cliente)
        cliente.__aexit__ = mock.AsyncMock(return_value=False)
        return cliente

    def test_mensaje_corto_por_webhook(self):
        cliente = self._cliente_mock()
        with mock.patch.object(dg.httpx, "AsyncClient", return_value=cliente):
            ok = asyncio.run(dg.send_discord_message("https://hook", "hola"))
        self.assertTrue(ok)
        self.assertIn("https://hook", cliente.post.call_args[0][0])

    def test_followup_usa_webhooks_de_interaccion(self):
        cliente = self._cliente_mock()
        os.environ["DISCORD_APPLICATION_ID"] = "APP"
        with mock.patch.object(dg.httpx, "AsyncClient", return_value=cliente):
            ok = asyncio.run(dg.send_discord_message(
                None, "hola", interaction_id="999",
                interaction_token="TOK"))
        self.assertTrue(ok)
        url = cliente.post.call_args[0][0]
        self.assertIn("/webhooks/APP/TOK", url)

    def test_mensaje_largo_se_envia_como_archivo(self):
        cliente = self._cliente_mock()
        with mock.patch.object(dg.httpx, "AsyncClient", return_value=cliente):
            ok = asyncio.run(dg.send_discord_message(
                "https://hook", "x" * (dg.DISCORD_MAX_MENSAJE + 300)))
        self.assertTrue(ok)
        llamadas = cliente.post.call_args_list
        self.assertGreaterEqual(len(llamadas), 2)   # resumen + .txt
        self.assertIsNotNone(llamadas[-1][1].get("files"))

    def test_error_de_red_no_lanza(self):
        cliente = mock.Mock()
        cliente.__aenter__ = mock.AsyncMock(side_effect=OSError("sin red"))
        cliente.__aexit__ = mock.AsyncMock(return_value=False)
        with mock.patch.object(dg.httpx, "AsyncClient", return_value=cliente):
            self.assertFalse(asyncio.run(
                dg.send_discord_message("https://hook", "x")))


class TestEndpointWebhook(unittest.TestCase):
    def test_endpoint_discord(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi/httpx TestClient no disponible")
        from web.app import crear_app
        http = TestClient(crear_app(api_token=""))

        # Sin public key → 503.
        with mock.patch.object(dg, "obtener_public_key", return_value=None):
            self.assertEqual(
                http.post("/webhook/discord", json={"type": 1}).status_code,
                503)

        # Con public key pero sin firma válida → 401.
        with mock.patch.object(dg, "obtener_public_key",
                               return_value=_PUBLICA_HEX):
            self.assertEqual(
                http.post("/webhook/discord", json={"type": 1}).status_code,
                401)

        # PING firmado correctamente → {"type": 1} inmediato.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
        privada = Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(_PRIVADA_HEX))
        cuerpo = b'{"type": 1}'
        timestamp = "1700000000"
        firma = privada.sign(timestamp.encode() + cuerpo).hex()
        async def nada(*a, **k):
            return {"type": 1}
        with mock.patch.object(dg, "obtener_public_key",
                               return_value=_PUBLICA_HEX), \
                mock.patch.object(dg, "handle_discord_interaction",
                                  side_effect=nada):
            r = http.post(
                "/webhook/discord", content=cuerpo,
                headers={"X-Signature-Ed25519": firma,
                         "X-Signature-Timestamp": timestamp,
                         "Content-Type": "application/json"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), {"type": 1})


if __name__ == "__main__":
    unittest.main()
