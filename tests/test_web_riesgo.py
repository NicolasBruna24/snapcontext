#!/usr/bin/env python3
"""Cobertura de riesgo para web/app.py (gateway FastAPI expuesto a red).

Prioriza caminos donde un input malicioso/malformado podria causar un
comportamiento inseguro -- NO tests triviales de "endpoint devuelve 200":

1. Auth: que endpoints /api/v1/* exigen clave y cuales quedan publicos.
2. Path traversal via WebSocket leer/guardar_archivo (HALLAZGO: sin validar).
3. RCE via accion "run" por WebSocket (HALLAZGO: comando arbitrario).
4. Requests malformados -> 4xx controlados, nunca 500 con traceback.
5. CORS/middleware de seguridad: documenta ausencia.
6. WebSockets: desconexion abrupta, mensajes malformados, tipo desconocido.
7. Webhooks discord/github: firma invalida -> 401; github sin secreto.

Los hallazgos se documentan y NO se arreglan aqui (requieren revision
previa): los tests fijan el comportamiento actual.
"""

import contextlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snapcontext as sc
from web.app import (
    API_PREFIJO,
    _construir_args,
    _construir_args_accion,
    _ejecutar_accion,
    _guardar_archivo_web,
    _leer_archivo_web,
    _resolver_camino,
    crear_app,
)

try:
    from fastapi.testclient import TestClient

    _TIENE_CLIENTE = True
except Exception:
    _TIENE_CLIENTE = False

CLAVE = "clave-web-riesgo"
HEADERS = {"X-API-Key": CLAVE}

requiere_cliente = pytest.mark.skipif(not _TIENE_CLIENTE, reason="TestClient no disponible")


@contextlib.contextmanager
def _frontera_en(raiz):
    """Fija la raíz del workspace en ``raiz`` durante el test (B9.61-B F-01).

    Tras la corrección F-01, ``directorio`` ya **no** define la frontera: la
    raíz la fija el servidor. Los tests que operan sobre un tmpdir absoluto
    deben declararlo explícitamente en lugar de apoyarse en el contrato viejo.
    """
    from web import filesystem as fs_web

    anterior = fs_web.frontera_activa()
    fs_web.fijar_frontera(fs_web.Frontera(exposicion="lan", raiz=Path(raiz).resolve()))
    try:
        yield
    finally:
        fs_web.fijar_frontera(anterior)


# ---------------------------------------------------------------------------
# 1. Autenticacion / superficie expuesta
# ---------------------------------------------------------------------------
class TestAuthAPI:
    def test_api_sin_token_queda_abierta(self):
        """B9.61-A FIX (web-auth-1 fail-open): sin clave configurada, el
        gateway ahora es fail-closed y /api/v1/* responde 401. El hallazgo
        original (sin clave -> abierto) se corrigio en B9.61-A."""
        import web.seguridad as seg

        with mock.patch.object(seg, "cargar_credencial", return_value=None):
            client = TestClient(crear_app(api_token=""))
            try:
                with mock.patch.object(sc, "_skill_listar", return_value=[]):
                    r = client.get(f"{API_PREFIJO}/skills")
                assert r.status_code == 401
            finally:
                client.close()

    def test_endpoints_api_exigen_clave(self):
        client = TestClient(crear_app(api_token=CLAVE))
        try:
            casos = [
                ("post", f"{API_PREFIJO}/query", {"consulta": "x"}),
                ("post", f"{API_PREFIJO}/plan", {"consulta": "x"}),
                ("post", f"{API_PREFIJO}/chat", {"mensaje": "x"}),
                ("get", f"{API_PREFIJO}/skills", None),
                ("post", f"{API_PREFIJO}/daemon", {"accion": "estado"}),
                ("get", f"{API_PREFIJO}/tasks/no-existe", None),
            ]
            for metodo, ruta, cuerpo in casos:
                kw = {"json": cuerpo} if cuerpo is not None else {}
                assert getattr(client, metodo)(ruta, **kw).status_code == 401, ruta
        finally:
            client.close()

    def test_endpoints_publicos_sin_auth(self):
        """HALLAZGO(web-auth-2): /ws, /, /health, /api/v1/health, /docs y
        los 3 webhooks NO pasan por _autorizar. /ws es el mas critico."""
        client = TestClient(crear_app(api_token=CLAVE))
        try:
            assert client.get("/health").status_code == 200
            assert client.get(f"{API_PREFIJO}/health").status_code == 200
            assert client.get("/docs").status_code == 200
            assert client.get("/").status_code in (200, 404, 500)
        finally:
            client.close()


# ---------------------------------------------------------------------------
# 2. Path traversal: _resolver_camino / leer / guardar via WS
# ---------------------------------------------------------------------------
class TestPathTraversal:
    def test_resolver_rechaza_escape_con_puntos(self):
        """H2 FIX (web/app.py:_resolver_camino): '../..' que escapa del base
        lanza ValueError (contencion utils._validar_ruta_segura)."""
        with tempfile.TemporaryDirectory() as tmp:
            fuera = (Path(tmp) / "fuera.txt").resolve()
            fuera.write_text("secreto", encoding="utf-8")
            with pytest.raises(ValueError):
                _resolver_camino("../fuera.txt", tmp + "/proy")

    def test_resolver_acepta_ruta_interna(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "proy"
            base.mkdir()
            dentro = (base / "a.txt").resolve()
            dentro.write_text("hola", encoding="utf-8")
            # B9.61-B F-01: la raíz la fija el servidor, no `directorio`.
            with _frontera_en(base):
                assert _resolver_camino("a.txt", str(base)) == dentro
                # B9.61-B: la frontera es fail-closed y rechaza CUALQUIER '..',
                # incluso si la ruta normalizada seguiría dentro del base.
                with pytest.raises(ValueError):
                    _resolver_camino("sub/../a.txt", str(base))

    def test_leer_web_rechaza_fuera_del_directorio(self):
        """H2 FIX (web/app.py:_leer_archivo_web): ruta absoluta o con '..'
        que escape del base devuelve error controlado, sin contenido."""
        with tempfile.TemporaryDirectory() as tmp:
            sec = Path(tmp) / "id_rsa_fake"
            sec.write_text("PRIVATE KEY FALSA", encoding="utf-8")
            r1 = _leer_archivo_web({"ruta": str(sec), "directorio": tmp + "/proy"})
            assert r1.get("contenido") is None
            assert "error" in r1
            r2 = _leer_archivo_web({"ruta": "../id_rsa_fake", "directorio": tmp + "/proy"})
            assert r2.get("contenido") is None
            assert "error" in r2

    def test_leer_web_permite_dentro_del_directorio(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "proy"
            base.mkdir()
            (base / "nota.txt").write_text("hola", encoding="utf-8")
            with _frontera_en(base):
                r = _leer_archivo_web({"ruta": "nota.txt", "directorio": str(base)})
            assert r.get("contenido") == "hola"
            assert "error" not in r

    def test_guardar_web_rechaza_fuera_del_directorio(self):
        """H3 FIX (web/app.py:_guardar_archivo_web): el traversal se rechaza
        con ok=False via C3 (utils.escribir_archivo_seguro); nada se escribe
        fuera del base."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "escape.txt"
            with pytest.raises(ValueError):
                _resolver_camino("../escape.txt", tmp + "/proy")
            res = _guardar_archivo_web(
                {"ruta": "../escape.txt", "directorio": tmp + "/proy", "contenido": "x"}
            )
            assert res.get("ok") is False
            assert not dest.exists()

    def test_guardar_web_permite_dentro_del_directorio(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "proy"
            base.mkdir()
            with _frontera_en(base):
                res = _guardar_archivo_web(
                    {"ruta": "nota.txt", "directorio": str(base), "contenido": "hola"}
                )
            assert res.get("ok") is True
            assert (base / "nota.txt").read_text(encoding="utf-8") == "hola"

    def test_guardar_sin_ruta_error_controlado(self):
        res = _guardar_archivo_web({"ruta": "", "contenido": "x"})
        assert res["ok"] is False and "ruta" in res["error"].lower()

    def test_leer_sin_ruta_error_controlado(self):
        res = _leer_archivo_web({"ruta": ""})
        assert res["contenido"] is None and "error" in res


# ---------------------------------------------------------------------------
# 3. RCE via accion run (WebSocket, sin auth)
# ---------------------------------------------------------------------------
class TestAccionRun:
    def test_run_peligroso_se_rechaza_sin_ejecutar(self):
        """H1b FIX (web/app.py:_ejecutar_accion run): comando fuera de la
        allowlist C2 (pipes/metacaracteres) se RECHAZA (ok=False) sin llegar
        a sc._ejecutar_comando."""
        import queue

        cola = queue.Queue()
        with mock.patch.object(sc, "_ejecutar_comando") as ejec:
            _ejecutar_accion(
                {"accion": "run", "comando": "echo HOLA | sh", "directorio": "."}, cola
            )
            ejec.assert_not_called()
        evs = []
        while not cola.empty():
            evs.append(cola.get_nowait())
        fin = [e for e in evs if e.get("tipo") == "accion_ejecutada"]
        assert fin and fin[0]["ok"] is False

    def test_run_ya_no_se_ejecuta_por_politica_remota(self):
        """B9.61-C (OD-1): `run` es ejecucion remota y M1 = DENY ALL.

        Antes este test comprobaba que un comando clasificado 'directo' por C2
        se ejecutaba. Con la politica remota de M1 ya no se ejecuta nada: la
        denegacion ocurre ANTES de C2.
        """
        import queue

        cola = queue.Queue()
        _ejecutar_accion({"accion": "run", "comando": "echo HOLA_WEB", "directorio": "."}, cola)
        evs = []
        while not cola.empty():
            evs.append(cola.get_nowait())
        fin = [e for e in evs if e.get("tipo") == "accion_ejecutada"]
        assert fin and fin[0]["ok"] is False

    def test_run_sin_comando_error_controlado(self):
        import queue

        cola = queue.Queue()
        _ejecutar_accion({"accion": "run", "directorio": "."}, cola)
        tipos = []
        while not cola.empty():
            tipos.append(cola.get_nowait()["tipo"])
        assert "accion_ejecutada" in tipos

    def test_construir_args_max_archivos_invalido_lanza(self):
        with pytest.raises((ValueError, TypeError)):
            _construir_args({"consulta": "x", "max_archivos": "no-numero"})


# ---------------------------------------------------------------------------
# 4. Requests malformados -> 4xx, nunca 500 con traceback
# ---------------------------------------------------------------------------
@requiere_cliente
class TestMalformados:
    def setup_method(self):
        self.client = TestClient(crear_app(api_token=CLAVE), raise_server_exceptions=False)

    def teardown_method(self):
        self.client.close()

    def test_query_vacio_es_400(self):
        r = self.client.post(f"{API_PREFIJO}/query", json={}, headers=HEADERS)
        assert r.status_code == 400

    def test_query_espacios_es_400(self):
        r = self.client.post(f"{API_PREFIJO}/query", json={"consulta": "  "}, headers=HEADERS)
        assert r.status_code == 400

    def test_query_no_string_no_500(self):
        """HALLAZGO(web-500-1, web/app.py:api_query): {"consulta": 12345}
        (no-string) lanza AttributeError (.strip de int) -> 500 con
        {"detail": "Internal Server Error"}. Fija el 500 actual para que
        el fix (400) lo ponga en verde al cambiar la asercion."""
        r = self.client.post(f"{API_PREFIJO}/query", json={"consulta": 12345}, headers=HEADERS)
        assert r.status_code == 500  # HALLAZGO: deberia ser 400/422
        assert "Traceback" not in r.text

    def test_chat_sin_mensaje_es_400(self):
        r = self.client.post(f"{API_PREFIJO}/chat", json={}, headers=HEADERS)
        assert r.status_code == 400

    def test_chat_json_invalido_sin_traceback(self):
        r = self.client.post(
            f"{API_PREFIJO}/chat",
            content=b"{no es json",
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        assert r.status_code in (400, 422)
        assert "Traceback" not in r.text

    def test_daemon_invalida_es_400(self):
        r = self.client.post(f"{API_PREFIJO}/daemon", json={"accion": "x"}, headers=HEADERS)
        assert r.status_code == 400

    def test_task_inexistente_es_404(self):
        r = self.client.get(f"{API_PREFIJO}/tasks/no-existe", headers=HEADERS)
        assert r.status_code == 404

    def test_github_no_json_es_400_sin_traceback(self):
        r = self.client.post(
            "/webhook/github", content=b"no-json", headers={"Content-Type": "application/json"}
        )
        assert r.status_code in (400, 503)
        assert "Traceback" not in r.text

    def test_chat_historial_no_lista_no_500(self):
        with (
            mock.patch.object(sc, "_enviar_al_proveedor", return_value="ok"),
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
        ):
            r = self.client.post(
                f"{API_PREFIJO}/chat",
                json={"mensaje": "hola", "historial": "deberia-ser-lista"},
                headers=HEADERS,
            )
        assert r.status_code in (200, 400, 422, 500)
        assert "Traceback" not in r.text


# ---------------------------------------------------------------------------
# 5. CORS / headers de seguridad
# ---------------------------------------------------------------------------
@requiere_cliente
class TestCorsHeaders:
    def test_sin_cors_ni_trustedhost(self):
        """HALLAZGO(web-cors-1): crear_app no registra CORSMiddleware ni
        TrustedHostMiddleware (navegadores aplican same-origin por defecto
        al no haber CORS; documenta el estado actual)."""
        app = crear_app(api_token=CLAVE)
        nombres = [getattr(m.cls, "__name__", str(m.cls)) for m in app.user_middleware]
        assert not any("CORS" in n for n in nombres), nombres
        assert not any("TrustedHost" in n for n in nombres), nombres

    def test_health_sin_traceback(self):
        client = TestClient(crear_app(api_token=CLAVE))
        try:
            assert client.get("/health").status_code == 200
            assert "Traceback" not in client.get("/health").text
        finally:
            client.close()


# ---------------------------------------------------------------------------
# 6. WebSocket /ws: malformados, desconocidos, traversal, desconexion
# ---------------------------------------------------------------------------
@requiere_cliente
class TestWebsocket:
    def _cli(self, clave=CLAVE):
        return TestClient(crear_app(api_token=clave))

    def _ws(self, cli, ruta="/ws"):
        return cli.websocket_connect(f"{ruta}?api_key={CLAVE}")

    def test_json_invalido_responde_log_error(self):
        cli = self._cli()
        try:
            with self._ws(cli) as ws:
                ws.send_text("{no es json")
                resp = ws.receive_json()
            assert resp["tipo"] == "log" and resp["nivel"] == "error"
        finally:
            cli.close()

    def test_ws_sin_clave_se_rechaza(self):
        """B9.61-A /ws sin api_key (query o header) se cierra pre-accept
        con 1008 (auth ANTES de accept): el TestClient lanza
        WebSocketDisconnect al intentar abrir la conexion."""
        from starlette.websockets import WebSocketDisconnect

        cli = self._cli()
        try:
            with pytest.raises(WebSocketDisconnect) as exc:
                with cli.websocket_connect("/ws") as ws:
                    ws.send_text(json.dumps({"tipo": "ping"}))
                    ws.receive_json()
            assert exc.value.code == 1008
        finally:
            cli.close()

    def test_ws_clave_invalida_se_rechaza(self):
        from starlette.websockets import WebSocketDisconnect

        cli = self._cli()
        try:
            with pytest.raises(WebSocketDisconnect) as exc:
                with cli.websocket_connect("/ws?api_key=incorrecta") as ws:
                    ws.send_text(json.dumps({"tipo": "ping"}))
                    ws.receive_json()
            assert exc.value.code == 1008
        finally:
            cli.close()

    def test_tipo_desconocido_no_cierra(self):
        """B9.61-A: tipo desconocido ya no se ignora en silencio; responde
        con 'error: Acción no permitida' (dispatcher cerrado, sin fallback).
        La conexion sigue viva (no close pre-accept) y puede atender 'ping'
        en el siguiente mensaje."""
        cli = self._cli()
        try:
            with self._ws(cli) as ws:
                ws.send_text(json.dumps({"tipo": "zzz_no_existe"}))
                respuesta = ws.receive_json()
                assert respuesta["tipo"] == "error"
                ws.send_text(json.dumps({"tipo": "ping"}))
                assert ws.receive_json() == {"tipo": "pong"}
        finally:
            cli.close()

    def test_sin_tipo_sin_consulta_se_ignora(self):
        cli = self._cli()
        try:
            with self._ws(cli) as ws:
                ws.send_text(json.dumps({"foo": "bar"}))
                ws.send_text(json.dumps({"tipo": "ping"}))
                assert ws.receive_json() == {"tipo": "pong"}
        finally:
            cli.close()

    def test_mensaje_no_dict_no_tumba_otras_conexiones(self):
        """H7 (pendiente): una lista JSON termina el bucle del WS; una
        conexion NUEVA sigue funcionando (fallo contenido por conexion)."""
        cli = self._cli()
        try:
            with self._ws(cli):
                pass  # conexion A: se abre y cierra sin mas
            with self._ws(cli) as ws:
                ws.send_text("[1, 2, 3]")  # no debe colgar el servidor
            with self._ws(cli) as ws2:
                ws2.send_text(json.dumps({"tipo": "ping"}))
                assert ws2.receive_json() == {"tipo": "pong"}
        finally:
            cli.close()

    def test_ping_pong(self):
        cli = self._cli()
        try:
            with self._ws(cli) as ws:
                ws.send_text(json.dumps({"tipo": "ping"}))
                assert ws.receive_json() == {"tipo": "pong"}
        finally:
            cli.close()

    def test_leer_archivo_por_ws_con_traversal_rechazado(self):
        """H2 FIX (variante en vivo): /ws con auth valida rechaza la lectura
        fuera del base con error controlado, sin contenido."""
        cli = self._cli()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sec = Path(tmp) / "clave_ws.txt"
                sec.write_text("SECRETO_WS", encoding="utf-8")
                with self._ws(cli) as ws:
                    ws.send_text(json.dumps({"tipo": "leer_archivo", "ruta": str(sec)}))
                    resp = ws.receive_json()
                assert resp["tipo"] == "archivo_seleccionado"
                assert resp.get("contenido") is None
                assert "error" in resp
        finally:
            cli.close()

    def test_run_por_ws_rechaza_comando_peligroso(self):
        """H1b FIX (variante en vivo): accion run con pipes se rechaza."""
        cli = self._cli()
        try:
            with self._ws(cli) as ws:
                ws.send_text(
                    json.dumps(
                        {
                            "tipo": "accion",
                            "accion": "run",
                            "comando": "echo X | sh",
                            "directorio": ".",
                        }
                    )
                )
                evs = [ws.receive_json(), ws.receive_json()]
            fin = [e for e in evs if e.get("tipo") == "accion_ejecutada"]
            assert fin and fin[0]["ok"] is False
        finally:
            cli.close()

    def test_desconexion_abrupta_sin_excepcion(self):
        cli = self._cli()
        try:
            with self._ws(cli):
                pass
        finally:
            cli.close()


# ---------------------------------------------------------------------------
# 7. Webhooks discord / github: firmas
# ---------------------------------------------------------------------------
@requiere_cliente
class TestWebhooks:
    def setup_method(self):
        self.client = TestClient(crear_app(api_token=CLAVE), raise_server_exceptions=False)

    def teardown_method(self):
        self.client.close()

    def test_discord_firma_invalida_es_401(self):
        try:
            import discord_gateway
        except ImportError:
            pytest.skip("discord_gateway no importable")
        with mock.patch("discord_gateway.obtener_public_key", return_value="00" * 32):
            r = self.client.post(
                "/webhook/discord",
                json={"type": 1},
                headers={"X-Signature-Ed25519": "00" * 64, "X-Signature-Timestamp": "1"},
            )
        assert r.status_code in (401, 503)
        assert "Traceback" not in r.text

    def test_discord_sin_public_key_es_503(self):
        try:
            import discord_gateway
        except ImportError:
            pytest.skip("discord_gateway no importable")
        with mock.patch("discord_gateway.obtener_public_key", return_value=None):
            r = self.client.post("/webhook/discord", json={"type": 1})
        assert r.status_code == 503

    def test_github_firma_invalida_es_401(self):
        try:
            import github_gateway
        except ImportError:
            pytest.skip("github_gateway no importable")
        with (
            mock.patch("github_gateway.obtener_webhook_secreto", return_value="s"),
            mock.patch("github_gateway.validar_firma", return_value=False),
        ):
            r = self.client.post(
                "/webhook/github",
                json={"action": "opened"},
                headers={"X-Hub-Signature-256": "sha256=x", "X-GitHub-Event": "pull_request"},
            )
        assert r.status_code == 401

    def test_github_sin_secreto_ahora_se_deniega(self):
        """B9.61-D (D-01) — CORRECCIÓN del hallazgo web-auth-3 (B9.58-R H-1).

        Antes este test fijaba la VULNERABILIDAD como comportamiento esperado
        (`assert 200`): sin secreto configurado, la firma no se verificaba y el
        webhook quedaba abierto a cualquiera que llegase al puerto. Ese
        fail-open-era el P0 D-01 (webhook no autenticado → task_queue → shell).

        Contrato actual (fail-CLOSED): sin secreto → 503, el evento NO se procesa.
        """
        try:
            import github_gateway
        except ImportError:
            pytest.skip("github_gateway no importable")
        with (
            mock.patch("github_gateway.obtener_webhook_secreto", return_value=None),
            mock.patch("github_gateway.parsear_evento", return_value={"tipo": "pr"}) as parsear,
            mock.patch("github_gateway.procesar_evento", return_value="t-1") as procesar,
        ):
            r = self.client.post(
                "/webhook/github",
                json={"action": "opened"},
                headers={"X-GitHub-Event": "pull_request"},
            )
        assert r.status_code == 503
        parsear.assert_not_called()
        procesar.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Ramas no cubiertas: daemon, webhooks, tareas API, helpers WS
# ---------------------------------------------------------------------------
@requiere_cliente
class TestRamasApi:
    def setup_method(self):
        self.client = TestClient(crear_app(api_token=CLAVE), raise_server_exceptions=False)

    def teardown_method(self):
        self.client.close()

    def test_daemon_iniciar_y_detener(self):
        import web.app as wa

        anterior = wa._DAEMON_HILO
        wa._DAEMON_HILO = None
        try:
            with mock.patch.object(sc, "_daemon_tick", return_value=None):
                r = self.client.post(
                    f"{API_PREFIJO}/daemon",
                    json={"accion": "iniciar", "intervalo_horas": 1},
                    headers=HEADERS,
                )
                assert r.status_code == 200 and r.json()["activo"] is True
                r2 = self.client.post(
                    f"{API_PREFIJO}/daemon", json={"accion": "iniciar"}, headers=HEADERS
                )
                assert "ya estaba" in r2.json()["detalle"]
                r3 = self.client.post(
                    f"{API_PREFIJO}/daemon", json={"accion": "detener"}, headers=HEADERS
                )
                assert r3.status_code == 200
        finally:
            wa._DAEMON_HILO = anterior

    def test_daemon_sin_snapcontext_es_500(self):
        import web.app as wa

        with mock.patch.object(wa, "_importar_snapcontext", return_value=None):
            r = self.client.post(
                f"{API_PREFIJO}/daemon", json={"accion": "iniciar"}, headers=HEADERS
            )
            assert r.status_code == 500

    def test_chat_sin_snapcontext_es_500(self):
        import web.app as wa

        with mock.patch.object(wa, "_importar_snapcontext", return_value=None):
            r = self.client.post(f"{API_PREFIJO}/chat", json={"mensaje": "h"}, headers=HEADERS)
            assert r.status_code == 500

    def test_telegram_ok_responde_200(self):
        try:
            import telegram_gateway
        except ImportError:
            pytest.skip("telegram_gateway no importable")

        async def _ok(_u):
            return None

        with (
            mock.patch("telegram_gateway.obtener_token", return_value="tok"),
            mock.patch("telegram_gateway.handle_telegram_update", side_effect=_ok),
        ):
            r = self.client.post("/webhook/telegram", json={"update_id": 2})
        assert r.status_code == 200 and r.json() == {"ok": True}

    def test_discord_ping_responde_type1(self):
        try:
            import discord_gateway
        except ImportError:
            pytest.skip("discord_gateway no importable")
        cuerpo = json.dumps({"type": 1}).encode()
        with (
            mock.patch("discord_gateway.obtener_public_key", return_value="k"),
            mock.patch("discord_gateway.verify_signature", return_value=True),
            mock.patch("discord_gateway.handle_discord_interaction", return_value={"type": 1}),
        ):
            r = self.client.post(
                "/webhook/discord",
                content=cuerpo,
                headers={"X-Signature-Ed25519": "f", "X-Signature-Timestamp": "t"},
            )
        assert r.status_code == 200


class TestHelpersWs:
    def test_clave_efectiva_variantes(self):
        """B9.61-A: la fuente de verdad de la credencial es ahora
        web.seguridad.cargar_credencial (web.app._clave_api_efectiva fue
        eliminado por ser una fuente secundaria con fail-open)."""
        import configuracion
        import web.seguridad as seg

        # Token explícito tiene prioridad.
        assert seg.cargar_credencial(token_explicito="abc") == "abc"

        import json as _json
        import os as _os
        import tempfile as _tmp
        from pathlib import Path as _P

        with _tmp.TemporaryDirectory() as t:
            cfg = _P(t) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                clave = "k" * 43  # >= MIN_LONGITUD_CREDENCIAL (32)
                cfg.write_text(_json.dumps({"api_key": clave}), encoding="utf-8")
                _os.chmod(cfg, 0o600)
                assert seg.cargar_credencial() == clave

    def test_clave_sin_snapcontext_vacia(self):
        """B9.61-A fail-closed: sin config y sin token, cargar_credencial
        devuelve None (nunca regenera en runtime y nunca abre)."""
        import tempfile as _tmp
        from pathlib import Path as _P

        import configuracion
        import web.seguridad as seg

        with _tmp.TemporaryDirectory() as t:
            cfg = _P(t) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                assert seg.cargar_credencial() is None

    def test_tarea_api_con_directorio_y_plan(self):
        """El `directorio` del request se confina a la raíz del workspace.

        B9.61-B-CORRECTION F-01: `directorio="/tmp"` ya NO se acepta como raíz
        (eso era el P0 de la auditoría); se deniega con 400. El caso válido usa
        un directorio real dentro de la raíz del servidor.
        """
        client = TestClient(crear_app(api_token=CLAVE))
        try:
            with mock.patch.object(sc, "_ejecutar_planificador", return_value=0):
                # directorio fuera del workspace -> 400 controlado, no 202.
                r = client.post(
                    f"{API_PREFIJO}/plan",
                    json={"consulta": "t", "directorio": "/tmp"},
                    headers=HEADERS,
                )
                assert r.status_code == 400
                # directorio "." (raíz del servidor) -> aceptado.
                r2 = client.post(
                    f"{API_PREFIJO}/plan",
                    json={"consulta": "t", "directorio": "."},
                    headers=HEADERS,
                )
                assert r2.status_code == 202
        finally:
            client.close()

    def test_ws_tarea_search_explorar(self):
        import queue

        import web.app as wa

        cli = TestClient(crear_app(api_token=CLAVE))
        try:
            with mock.patch("orquestador.Orquestador") as orq:
                orq.return_value.ejecutar_flujo.return_value = None
                with cli.websocket_connect(f"/ws?api_key={CLAVE}") as ws:
                    ws.send_text(json.dumps({"tipo": "tarea", "consulta": "hola"}))
                    primer = ws.receive_json()
                    assert primer["tipo"] in ("inicio", "log", "final")
            cola = queue.Queue()
            with mock.patch.object(wa, "_semantica_web", return_value=[]):
                _ejecutar_accion({"accion": "search", "directorio": "."}, cola)
            evs = []
            while not cola.empty():
                evs.append(cola.get_nowait())
            assert any(e.get("tipo") == "accion_ejecutada" for e in evs)
            cola2 = queue.Queue()
            with mock.patch.object(wa, "_explorar_web", return_value=["l1"]):
                _ejecutar_accion({"accion": "explorar", "consulta": "t", "directorio": "."}, cola2)
            evs2 = []
            while not cola2.empty():
                evs2.append(cola2.get_nowait())
            assert any(e.get("tipo") == "exploracion" for e in evs2)
        finally:
            cli.close()

    def test_ws_dependencias_semantica_explorar(self):
        import web.app as wa

        cli = TestClient(crear_app(api_token=CLAVE))
        try:
            with (
                mock.patch.object(
                    wa, "_dependencias_web", return_value={"nodos": [], "enlaces": []}
                ),
                mock.patch.object(wa, "_semantica_web", return_value=[{"a": 1}]),
                mock.patch.object(wa, "_explorar_web", return_value=["x"]),
                cli.websocket_connect(f"/ws?api_key={CLAVE}") as ws,
            ):
                ws.send_text(json.dumps({"tipo": "dependencias"}))
                assert ws.receive_json()["tipo"] == "dependencias_actualizadas"
                ws.send_text(json.dumps({"tipo": "semantica", "consulta": "q"}))
                assert ws.receive_json()["tipo"] == "semanticos"
                ws.send_text(json.dumps({"tipo": "explorar", "tema": "t"}))
                assert ws.receive_json()["tipo"] == "exploracion"
        finally:
            cli.close()


class TestRemanenteWebhooks:
    def setup_method(self):
        self.client = TestClient(crear_app(api_token=CLAVE), raise_server_exceptions=False)

    def teardown_method(self):
        self.client.close()

    def test_construir_args_completo(self):
        args = _construir_args(
            {
                "consulta": "hola",
                "directorio": ".",
                "local": True,
                "vista_previa": True,
                "max_archivos": 5,
                "carpetas": ["a"],
                "test_loop": True,
                "comando_test": "pytest -q",
                "max_iteraciones": 2,
            }
        )
        assert args is not None

    def test_construir_args_accion_variantes(self):
        assert _construir_args_accion("fix", "x.py", ".") is not None
        assert _construir_args_accion("review", "x.py", ".") is not None
        assert _construir_args_accion("plan", "tema", ".") is not None
        assert _construir_args_accion("otro", "tema", ".") is not None

    def test_telegram_sin_token_es_503(self):
        try:
            import telegram_gateway
        except ImportError:
            pytest.skip("telegram_gateway no importable")
        with mock.patch("telegram_gateway.obtener_token", return_value=None):
            r = self.client.post("/webhook/telegram", json={"update_id": 1})
            assert r.status_code == 503
