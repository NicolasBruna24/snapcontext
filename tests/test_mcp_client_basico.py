"""Tests de cobertura de riesgo para mcp_client.py.

Cubre los caminos que test_mcp_client.py (happy path + degradacion basica)
no alcanza, priorizando casos que en produccion causarian un crash silencioso
o un comportamiento inseguro:

  1. Errores de conexion y respuesta JSON-RPC malformada.
  2. Parsing de respuestas con tipos inesperados.
  3. Spawneo de proceso: fallo en spawn, muerte durante llamada.
  4. CLI: config corrupta, servidor duplicado, remove inexistente.
  5. Prefijo mcp_<servidor>_<herramienta>: colisiones y caracteres raros.
  6. Fix seguridad: validacion de tipo en list_tools().
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_client
from mcp_client import (
    MCPClient,
    _ejecutar_comando_mcp,
    _leer_config,
    ruta_config_servidores,
)


# --- Helpers ---


def _handshake_result():
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "fake-server", "version": "1.0.0"},
    }


def _respuesta_mcp(id_, result=None, error=None):
    r = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        r["error"] = error
    else:
        r["result"] = result if result is not None else {}
    return r


def _fake_stdin():
    class _Stdin:
        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

        def flush(self):
            pass

        def close(self):
            pass

    return _Stdin()


def _proceso_con_lineas(lineas_stdout, returncode=0, poll_da=None):
    stdin = _fake_stdin()
    stdout = MagicMock()
    it = iter(lineas_stdout)
    stdout.readline.side_effect = lambda: next(it, "")
    stdout.closed = False
    proceso = MagicMock()
    proceso.stdin = stdin
    proceso.stdout = stdout
    proceso.stderr = MagicMock()
    proceso.stderr.read.return_value = ""
    proceso.returncode = returncode
    proceso.poll.return_value = poll_da
    proceso.terminate = MagicMock()
    proceso.kill = MagicMock()
    proceso.wait = MagicMock()
    return proceso, stdin


def _cfg_para(nombre, comando="fake"):
    return {nombre: {"command": comando, "args": [], "env": {}, "enabled": True}}


# === 1. Errores de conexion y JSON-RPC malformado ===


class TestErroresConexion:
    def test_json_malo_y_luego_valido(self):
        lineas = ["esto no es json", "{mal json", json.dumps(_respuesta_mcp(1, _handshake_result()))]
        proceso, _ = _proceso_con_lineas(lineas)
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            assert cliente._asegurar_proceso("srv") is not None
        cliente.cerrar()

    def test_json_sin_id_se_descarta(self):
        lineas = [
            json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}),
            json.dumps(_respuesta_mcp(1, _handshake_result())),
        ]
        proceso, _ = _proceso_con_lineas(lineas)
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            assert cliente._asegurar_proceso("srv") is not None
        cliente.cerrar()

    def test_error_jsonrpc_propaga(self):
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        error_resp = json.dumps(_respuesta_mcp(2, error={"code": -32600, "message": "Invalid Request"}))
        proceso, _ = _proceso_con_lineas([handshake, error_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
            with pytest.raises(RuntimeError, match="fall"):
                cliente._peticion("srv", "tools/list")
        cliente.cerrar()



# === 2. Parsing de respuestas con tipos inesperados ===


class TestParsingRespuestas:
    def test_sin_clave_tools(self):
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(_respuesta_mcp(2, {}))
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": []}
        cliente.cerrar()

    def test_tools_no_es_lista(self):
        """Fix seguridad: tools que no es lista se ignora."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(_respuesta_mcp(2, {"tools": "no soy lista"}))
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": []}
        cliente.cerrar()

    def test_elementos_no_dict_se_filtran(self):
        """Fix seguridad: elementos no-dict se ignoran."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(
            _respuesta_mcp(2, {"tools": [{"name": "ok"}, "no soy dict", 42, None, {"name": "ok2"}]})
        )
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": [{"name": "ok"}, {"name": "ok2"}]}
        cliente.cerrar()

    def test_lista_vacia(self):
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(_respuesta_mcp(2, {"tools": []}))
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": []}


# === 3. Spawneo de proceso ===


class TestSpawnProceso:
    def test_popen_oserror(self):
        cfg = _cfg_para("srv", comando="no_existe_xyz")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.side_effect = OSError("No such file")
        with patch.object(mcp_client, "subprocess", mock_sub):
            assert cliente._asegurar_proceso("srv") is None
        assert "srv" in cliente._caidos
        cliente.cerrar()

    def test_enviar_oserror_marca_caido(self):
        proceso, _ = _proceso_con_lineas([])
        proceso.stdin = MagicMock()
        proceso.stdin.write.side_effect = OSError("broken pipe")
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
            with pytest.raises(RuntimeError, match="no acepta entrada"):
                cliente._enviar("srv", {"x": 1})
        assert "srv" in cliente._caidos
        cliente.cerrar()

    def test_list_caido_devuelve_vacio(self):
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        cliente._caidos.add("srv")
        assert cliente.list_tools() == {"srv": []}
        cliente.cerrar()

    def test_call_caido_devuelve_error(self):
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        cliente._caidos.add("srv")
        resultado = cliente.call_tool("srv", "x", {})
        assert resultado["ok"] is False
        assert "no disponible" in resultado["error"]
        cliente.cerrar()

    def test_cerrar_idempotente(self):
        proceso, _ = _proceso_con_lineas([])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso


# === 4. CLI: config corrupta ===


class TestCLI:
    def test_leer_config_no_dict(self):
        ruta = Path("/tmp/mcp_corrupto.json")
        ruta.write_text(json.dumps({"no_servers": True}), encoding="utf-8")
        try:
            assert _leer_config(ruta) == {"servers": {}}
        finally:
            ruta.unlink(missing_ok=True)

    def test_leer_config_servers_no_dict(self):
        ruta = Path("/tmp/mcp_corrupto2.json")
        ruta.write_text(json.dumps({"servers": "no soy dict"}), encoding="utf-8")
        try:
            assert _leer_config(ruta) == {"servers": {}}
        finally:
            ruta.unlink(missing_ok=True)

    def test_remove_inexistente(self):
        ruta = Path("/tmp/mcp_noexiste.json")
        ruta.write_text(json.dumps({"servers": {}}), encoding="utf-8")
        try:
            with patch("mcp_client.ruta_config_servidores", return_value=ruta):
                assert _ejecutar_comando_mcp(["remove", "fantasma"]) == 1
        finally:
            ruta.unlink(missing_ok=True)

    def test_add_duplicado_sobrescribe(self):
        ruta = Path("/tmp/mcp_dup.json")
        datos = {"servers": {"fs": {"command": "old", "args": [], "enabled": True}}}
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        try:
            with patch("mcp_client.ruta_config_servidores", return_value=ruta):
                _ejecutar_comando_mcp(["add", "fs", "new", "cmd"])
                resultado = json.loads(ruta.read_text(encoding="utf-8"))
                assert resultado["servers"]["fs"]["command"] == "new"
        finally:
            ruta.unlink(missing_ok=True)
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
        cliente.cerrar()
        cliente.cerrar()
        cliente.cerrar()

    def test_terminar_timeout(self):
        proceso, _ = _proceso_con_lineas([])
        proceso.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        cliente._procesos["srv"] = proceso
        cliente._terminar("srv")
        proceso.kill.assert_called_once()
        cliente.cerrar()
        cliente.cerrar()

    def test_call_tool_retorna_error(self):
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        call_resp = json.dumps(
            _respuesta_mcp(2, {"isError": True, "content": [{"type": "text", "text": "fallo"}]})
        )
        proceso, _ = _proceso_con_lineas([handshake, call_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
            resultado = cliente.call_tool("srv", "x", {})
        assert resultado["ok"] is False
        assert "fallo" in resultado["error"]
        cliente.cerrar()
    def test_crashea_medio_sesion(self):
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        proceso, _ = _proceso_con_lineas([handshake, ""])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
            with pytest.raises(RuntimeError, match="cerr"):
                cliente._peticion("srv", "tools/list")
        cliente.cerrar()


class TestSpawnProceso:
    pass


class TestSpawnProcesoFactico:
    pass


# --- Helpers -----------------------------------------------------------------


def _handshake_result():
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "fake-server", "version": "1.0.0"},
    }


def _respuesta_mcp(id_, result=None, error=None):
    r = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        r["error"] = error
    else:
        r["result"] = result if result is not None else {}
    return r


def _fake_stdin():
    class _Stdin:
        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

        def flush(self):
            pass

        def close(self):
            pass

    return _Stdin()


def _proceso_con_lineas(lineas_stdout, returncode=0, poll_da=None):
    """Mock de Popen con stdout predefinido."""
    stdin = _fake_stdin()
    stdout = MagicMock()
    it = iter(lineas_stdout)
    stdout.readline.side_effect = lambda: next(it, "")
    stdout.closed = False
    proceso = MagicMock()
    proceso.stdin = stdin
    proceso.stdout = stdout
    proceso.stderr = MagicMock()
    proceso.stderr.read.return_value = ""
    proceso.returncode = returncode
    proceso.poll.return_value = poll_da
    proceso.terminate = MagicMock()
    proceso.kill = MagicMock()
    proceso.wait = MagicMock()
    return proceso, stdin


def _cfg_para(nombre, comando="fake"):
    return {nombre: {"command": comando, "args": [], "env": {}, "enabled": True}}


# =============================================================================
# 1. Errores de conexion y JSON-RPC malformado
# =============================================================================


class TestErroresConexion:
    def test_servidor_devuelve_json_malo_y_luego_valido(self):
        """Lineas no-JSON se ignoran; la respuesta valida se parsea OK."""
        lineas = [
            "esto no es json",
            "{mal json",
            json.dumps(_respuesta_mcp(1, _handshake_result())),
        ]
        proceso, _ = _proceso_con_lineas(lineas)
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            assert cliente._asegurar_proceso("srv") is not None
        cliente.cerrar()

    def test_servidor_devuelve_json_sin_id_se_descarta(self):
        """Respuesta sin id (notificacion) se descarta; espera la correcta."""
        lineas = [
            json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"}),
            json.dumps(_respuesta_mcp(1, _handshake_result())),
        ]
        proceso, _ = _proceso_con_lineas(lineas)
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            assert cliente._asegurar_proceso("srv") is not None
        cliente.cerrar()

    def test_respuesta_con_error_jsonrpc_propaga_excepcion(self):
        """Si el servidor devuelve error en JSON-RPC, _peticion lanza RuntimeError."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        error_resp = json.dumps(
            _respuesta_mcp(2, error={"code": -32600, "message": "Invalid Request"})
        )
        proceso, _ = _proceso_con_lineas([handshake, error_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
            with pytest.raises(RuntimeError, match="fall"):
                cliente._peticion("srv", "tools/list")
        cliente.cerrar()

    def test_servidor_crashea_medio_sesion_stdout_vacio(self):
        """EOF en stdout despues de handshake = servidor caido durante llamada."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        proceso, _ = _proceso_con_lineas([handshake, ""])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
            with pytest.raises(RuntimeError, match="cerr"):
                cliente._peticion("srv", "tools/list")
        cliente.cerrar()


# =============================================================================
# 2. Parsing de respuestas con tipos inesperados
# =============================================================================


class TestParsingRespuestas:
    def test_list_tools_sin_clave_tools(self):
        """Respuesta sin tools se trata como lista vacia."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(_respuesta_mcp(2, {}))
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": []}
        cliente.cerrar()

    def test_list_tools_tools_no_es_lista(self):
        """tools que no es lista se ignora y devuelve lista vacia (fix seguridad)."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(_respuesta_mcp(2, {"tools": "no soy lista"}))
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": []}
        cliente.cerrar()

    def test_list_tools_elementos_no_dict_se_filtran(self):
        """Elementos no-dict en la lista de tools se ignoran (fix seguridad)."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(
            _respuesta_mcp(
                2,
                {"tools": [{"name": "ok"}, "no soy dict", 42, None, {"name": "tambien_ok"}]},
            )
        )
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": [{"name": "ok"}, {"name": "tambien_ok"}]}
        cliente.cerrar()

    def test_list_tools_lista_vacia(self):
        """Servidor con 0 herramientas devuelve lista vacia."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(_respuesta_mcp(2, {"tools": []}))
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            tools = cliente.list_tools()
        assert tools == {"srv": []}
        cliente.cerrar()

    def test_call_tool_herramienta_retorna_error(self):
        """isError=True se refleja en ok=False."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        call_resp = json.dumps(
            _respuesta_mcp(
                2,
                {
                    "isError": True,
                    "content": [{"type": "text", "text": "fallo interno"}],
                },
            )
        )
        proceso, _ = _proceso_con_lineas([handshake, call_resp])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
            resultado = cliente.call_tool("srv", "x", {})
        assert resultado["ok"] is False
        assert "fallo interno" in resultado["error"]
        cliente.cerrar()


# =============================================================================
# 3. Spawneo de proceso: fallo en spawn, muerte durante llamada
# =============================================================================


class TestSpawnProceso:
    def test_popen_lanza_oserror(self):
        """Popen con OSError se marca como caido, no lanza."""
        cfg = _cfg_para("srv", comando="comando_que_no_existe_xyz_123")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.side_effect = OSError("No such file")
        with patch.object(mcp_client, "subprocess", mock_sub):
            resultado = cliente._asegurar_proceso("srv")
        assert resultado is None
        assert "srv" in cliente._caidos
        cliente.cerrar()

    def test_enviar_fallo_oserror_marca_caido(self):
        """Escribir a stdin con error marca servidor como caido."""
        # El handshake debe COMPLETARSE para aislar el fallo en el _enviar
        # posterior: stdout devuelve la respuesta al initialize (id=1), y
        # stdin acepta las 2 escrituras del handshake (initialize +
        # notifications/initialized) pero falla en la 3ª (la del test).
        # Sin estas líneas, el handshake ya fallaría y _asegurar_proceso
        # devolvería None (el bug original delatado por el KeyError).
        lineas = [json.dumps(_respuesta_mcp(1, _handshake_result()))]
        proceso, _ = _proceso_con_lineas(lineas)
        proceso.stdin = MagicMock()
        proceso.stdin.write.side_effect = [None, None, OSError("broken pipe")]
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            assert cliente._asegurar_proceso("srv") is not None
            with pytest.raises(RuntimeError, match="no acepta entrada"):
                cliente._enviar("srv", {"x": 1})
        assert "srv" in cliente._caidos
        assert "srv" not in cliente._procesos  # proceso muerto, no reintentable
        cliente.cerrar()

    def test_list_tools_con_servidor_caido_devuelve_vacio(self):
        """list_tools con servidor caido devuelve [] sin lanzar."""
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        cliente._caidos.add("srv")
        tools = cliente.list_tools()
        assert tools == {"srv": []}
        cliente.cerrar()

    def test_call_tool_con_servidor_caido_devuelve_error(self):
        """call_tool con servidor caido devuelve ok=False."""
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        cliente._caidos.add("srv")
        resultado = cliente.call_tool("srv", "x", {})
        assert resultado["ok"] is False
        assert "no disponible" in resultado["error"]
        cliente.cerrar()

    def test_cerrar_varias_veces_no_lanza(self):
        """cerrar() es idempotent: llamable multiples veces sin error."""
        proceso, _ = _proceso_con_lineas([])
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            cliente._asegurar_proceso("srv")
        cliente.cerrar()
        cliente.cerrar()
        cliente.cerrar()

    def test_terminar_con_timeout_en_wait(self):
        """_terminar maneja TimeoutExpired en wait() y hace kill()."""
        proceso, _ = _proceso_con_lineas([])
        proceso.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        cliente._procesos["srv"] = proceso
        cliente._terminar("srv")
        proceso.kill.assert_called_once()
        cliente.cerrar()


# =============================================================================
# 4. CLI: config corrupta, duplicados, remove inexistente
# =============================================================================


class TestCLI:
    def test_leer_config_no_es_dict(self):
        """Config que no es dict con servers se ignora."""
        ruta = Path("/tmp/mcp_test_corrupto.json")
        ruta.write_text(json.dumps({"no_servers": True}), encoding="utf-8")
        try:
            assert _leer_config(ruta) == {"servers": {}}
        finally:
            ruta.unlink(missing_ok=True)

    def test_leer_config_servers_no_es_dict(self):
        """servers que no es dict se ignora."""
        ruta = Path("/tmp/mcp_test_corrupto2.json")
        ruta.write_text(json.dumps({"servers": "no soy dict"}), encoding="utf-8")
        try:
            assert _leer_config(ruta) == {"servers": {}}
        finally:
            ruta.unlink(missing_ok=True)

    def test_cli_remove_servidor_inexistente(self):
        """Remove de servidor que no existe devuelve 1 con mensaje."""
        ruta = Path("/tmp/mcp_noexiste.json")
        ruta.write_text(json.dumps({"servers": {}}), encoding="utf-8")
        try:
            with patch("mcp_client.ruta_config_servidores", return_value=ruta):
                codigo = _ejecutar_comando_mcp(["remove", "fantasma"])
                assert codigo == 1
        finally:
            ruta.unlink(missing_ok=True)

    def test_cli_add_servidor_duplicado_sobrescribe(self):
        """Agregar servidor con nombre existente sobrescribe la config."""
        ruta = Path("/tmp/mcp_dup.json")
        datos = {"servers": {"fs": {"command": "old", "args": [], "enabled": True}}}
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        try:
            with patch("mcp_client.ruta_config_servidores", return_value=ruta):
                _ejecutar_comando_mcp(["add", "fs", "new", "cmd"])
                resultado = json.loads(ruta.read_text(encoding="utf-8"))
                assert resultado["servers"]["fs"]["command"] == "new"
        finally:
            ruta.unlink(missing_ok=True)


# =============================================================================
# 5. Prefijo mcp_<servidor>_<herramienta>: colisiones y caracteres raros
# =============================================================================


class TestPrefijosHerramientas:
    def test_herramienta_con_nombre_vacio(self):
        """Herramienta con nombre vacio se excluye del registro."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(
            _respuesta_mcp(2, {"tools": [{"name": "", "description": "vacio"}]})
        )
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = {"srv": {"command": "fake"}}
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            registro = cliente.herramientas_aplanadas()
        assert len(registro) == 0
        cliente.cerrar()

    def test_herramienta_sin_descripcion(self):
        """Herramienta sin descripcion recibe descripcion por defecto."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(
            _respuesta_mcp(2, {"tools": [{"name": "tool"}]})
        )
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = {"srv": {"command": "fake"}}
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            registro = cliente.herramientas_aplanadas()
        assert registro["mcp_srv_tool"]["descripcion"] == "[mcp:srv] tool"
        cliente.cerrar()

    def test_herramienta_con_caracteres_raros(self):
        """Nombres de herramienta con caracteres especiales se registran."""
        handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
        tools_resp = json.dumps(
            _respuesta_mcp(2, {"tools": [{"name": "read-file_v2.0"}]})
        )
        proceso, _ = _proceso_con_lineas([handshake, tools_resp])
        cfg = {"srv": {"command": "fake"}}
        cliente = MCPClient(config=cfg)
        mock_sub = MagicMock()
        mock_sub.Popen.return_value = proceso
        with patch.object(mcp_client, "subprocess", mock_sub):
            registro = cliente.herramientas_aplanadas()
        assert "mcp_srv_read-file_v2.0" in registro
        cliente.cerrar()

    def test_ejecutar_herramienta_externa_prefijo_invalido(self):
        """Herramienta sin prefijo mcp_ devuelve error."""
        resultado = mcp_client.ejecutar_herramienta_externa("no_prefijo", {})
        assert resultado["ok"] is False
        assert "prefijo inv" in resultado["error"]


# =============================================================================
# 6. Edge cases de estado interno
# =============================================================================


class TestEstadoInterno:
    def test_asegurar_proceso_servidor_en_caidos_devuelve_none(self):
        """Servidor en _caidos no reintenta spawn."""
        cfg = _cfg_para("srv")
        cliente = MCPClient(config=cfg)
        cliente._caidos.add("srv")
        resultado = cliente._asegurar_proceso("srv")
        assert resultado is None

    def test_asegurar_proceso_servidor_sin_config_devuelve_none(self):
        """Servidor no presente en config devuelve None."""
        cliente = MCPClient(config={})
        resultado = cliente._asegurar_proceso("inexistente")
        assert resultado is None

    def test_cliente_con_config_none(self):
        """MCPClient(config=None) lee de fichero, no crashea."""
        cliente = MCPClient(config=None)
        assert cliente._config == {}

    def test_cerrar_cliente_compartido_idempotent(self):
        """cerrar_cliente es seguro llamarlo multiples veces."""
        mcp_client._CLIENTE = None
        mcp_client.cerrar_cliente()
        mcp_client.cerrar_cliente()
        # Sin error: idempotente.
        assert mcp_client._CLIENTE is None

