#!/usr/bin/env python3
"""Tests para el cliente MCP estándar (Fase 3).

Verifica handshake JSON-RPC, list_tools, call_tool y degradación elegante
cuando un servidor MCP no responde. Los tests mockean ``subprocess.Popen``
para no depender de servidores MCP externos instalados.
"""

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
    _guardar_config,
    _leer_config,
    ruta_config_servidores,
    servers_configurados,
)


def _respuesta_mcp(id_, result=None, error=None):
    """Construye una respuesta JSON-RPC válida."""
    r = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        r["error"] = error
    else:
        r["result"] = result or {}
    return r


def _handshake_result():
    """Respuesta realista al handshake ``initialize``."""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "fake-server", "version": "1.0.0"},
    }


def _herramientas_ejemplo():
    """Herramientas de ejemplo para ``tools/list``."""
    return [
        {
            "name": "read_file",
            "description": "Lee un archivo del disco.",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "list_dir",
            "description": "Lista el directorio.",
            "inputSchema": {"type": "object", "properties": {"dir": {"type": "string"}}},
        },
    ]


class _FakeStdin:
    """Simula el stdin de un proceso: registra lo escrito."""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        pass


def _construir_proceso_fake(lineas_stdout, returncode=0):
    """Crea un mock de ``subprocess.Popen`` con stdout predefinido."""
    fake_stdin = _FakeStdin()
    stdout = MagicMock()
    readline_iter = iter(lineas_stdout)
    stdout.readline.side_effect = lambda: next(readline_iter, "")
    stdout.closed = False
    proceso = MagicMock()
    proceso.stdin = fake_stdin
    proceso.stdout = stdout
    proceso.stderr = MagicMock()
    proceso.stderr.read.return_value = ""
    proceso.returncode = returncode
    proceso.poll.return_value = None
    proceso.terminate = MagicMock()
    proceso.kill = MagicMock()
    proceso.wait = MagicMock()
    return proceso, fake_stdin


# --- Tests de configuración -------------------------------------------------


def test_leer_config_inexistente(tmp_path):
    """Si no existe el fichero, devuelve ``{servers: {}}`` sin error."""
    assert _leer_config(tmp_path / "no_existe.json") == {"servers": {}}


def test_leer_config_valida(tmp_path):
    """Lee un fichero bien formado."""
    ruta = tmp_path / "mcp_servers.json"
    datos = {"servers": {"fs": {"command": "npx", "args": ["-y", "srv"], "enabled": True}}}
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    assert _leer_config(ruta) == datos


def test_leer_config_corrupta(tmp_path):
    """Un fichero corrupto se ignora con aviso (no lanza)."""
    ruta = tmp_path / "mcp_servers.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    assert _leer_config(ruta) == {"servers": {}}


def test_guardar_y_leer_roundtrip(tmp_path):
    """Escribir y leer de vuelta produce los mismos datos."""
    datos = {"servers": {"git": {"command": "uvx", "args": ["mcp-server-git"], "enabled": True}}}
    _guardar_config(datos, tmp_path / "mcp_servers.json")
    assert _leer_config(tmp_path / "mcp_servers.json") == datos


def test_ruta_config_proyecto_sobre_global(tmp_path):
    """Si existe ``mcp_servers.json`` en el proyecto, tiene precedencia."""
    (tmp_path / "mcp_servers.json").write_text(
        json.dumps({"servers": {}}), encoding="utf-8"
    )
    assert ruta_config_servidores(tmp_path) == tmp_path / "mcp_servers.json"


def test_servers_configurados_filtra_deshabilitados(tmp_path):
    """Solo devuelve los servidores con ``enabled=true``."""
    datos = {
        "servers": {
            "activo": {"command": "x", "args": [], "enabled": True},
            "inactivo": {"command": "y", "args": [], "enabled": False},
        }
    }
    ruta = tmp_path / "mcp_servers.json"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    resultado = servers_configurados(raiz=tmp_path)
    assert "activo" in resultado
    assert "inactivo" not in resultado
# --- Tests de handshake -----------------------------------------------------


def test_handshake_envia_initialize_y_notificacion(tmp_path):
    """El handshake envía ``initialize`` y ``notifications/initialized``."""
    lineas = [json.dumps(_respuesta_mcp(1, _handshake_result()))]
    proceso, fake_stdin = _construir_proceso_fake(lineas)
    cfg = {"srv": {"command": "fake", "args": [], "env": {}, "enabled": True}}
    cliente = MCPClient(config=cfg)
    mock_subprocess = MagicMock()
    mock_subprocess.Popen.return_value = proceso
    with patch.object(mcp_client, "subprocess", mock_subprocess):
        proceso_devuelto = cliente._asegurar_proceso("srv")
    assert proceso_devuelto is not None
    assert "srv" in cliente._procesos
    assert len(fake_stdin.written) == 2
    initialize = json.loads(fake_stdin.written[0])
    assert initialize["method"] == "initialize"
    assert initialize["params"]["protocolVersion"] == "2024-11-05"
    notif = json.loads(fake_stdin.written[1])
    assert notif["method"] == "notifications/initialized"
    cliente.cerrar()


def test_handshake_sin_respuesta_marca_caido(tmp_path):
    """Un servidor que no responde queda marcado como caído (no lanza)."""
    proceso, _ = _construir_proceso_fake([])  # EOF inmediato
    cfg = {"roto": {"command": "nope", "args": [], "env": {}, "enabled": True}}
    cliente = MCPClient(config=cfg)
    mock_subprocess = MagicMock()
    mock_subprocess.Popen.return_value = proceso
    with patch.object(mcp_client, "subprocess", mock_subprocess):
        proceso_devuelto = cliente._asegurar_proceso("roto")
    assert proceso_devuelto is None
    assert "roto" in cliente._caidos
    cliente.cerrar()


def test_handshake_servidor_no_existente(tmp_path):
    """Un comando inexistente no rompe el cliente (FileNotFoundError)."""
    cfg = {"fantasma": {"command": "comando_que_no_existe_xyz", "args": [], "enabled": True}}
    cliente = MCPClient(config=cfg)
    proceso_devuelto = cliente._asegurar_proceso("fantasma")
    assert proceso_devuelto is None
    assert "fantasma" in cliente._caidos
    cliente.cerrar()


# --- Tests de list_tools ----------------------------------------------------


def test_list_tools_parsea_respuesta(tmp_path):
    """``list_tools`` parsea ``tools/list`` y devuelve las herramientas."""
    handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
    tools_resp = json.dumps(_respuesta_mcp(2, {"tools": _herramientas_ejemplo()}))
    proceso, _ = _construir_proceso_fake([handshake, tools_resp])
    cfg = {"srv": {"command": "fake", "args": [], "env": {}, "enabled": True}}
    mock_subprocess = MagicMock()
    mock_subprocess.Popen.return_value = proceso
    with patch.object(mcp_client, "subprocess", mock_subprocess):
        cliente = MCPClient(config=cfg)
        tools = cliente.list_tools()
    assert "srv" in tools
    nombres = [h["name"] for h in tools["srv"]]
    assert nombres == ["read_file", "list_dir"]
    cliente.cerrar()


def test_list_tools_servidor_caido_devuelve_vacio(tmp_path):
    """Un servidor sin handshake devuelve lista vacía (no lanza)."""
    proceso, _ = _construir_proceso_fake([])
    cfg = {"roto": {"command": "nope", "args": [], "env": {}, "enabled": True}}
    mock_subprocess = MagicMock()
    mock_subprocess.Popen.return_value = proceso
    with patch.object(mcp_client, "subprocess", mock_subprocess):
        cliente = MCPClient(config=cfg)
    assert cliente.list_tools() == {"roto": []}
    cliente.cerrar()


# --- Tests de call_tool -----------------------------------------------------


def test_call_tool_devuelve_resultado(tmp_path):
    """``call_tool`` envía ``tools/call`` y parsea el contenido."""
    handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
    call_resp = json.dumps(
        _respuesta_mcp(2, {"content": [{"type": "text", "text": "contenido fake"}]})
    )
    proceso, _ = _construir_proceso_fake([handshake, call_resp])
    cfg = {"srv": {"command": "fake", "args": [], "env": {}, "enabled": True}}
    mock_subprocess = MagicMock()
    mock_subprocess.Popen.return_value = proceso
    with patch.object(mcp_client, "subprocess", mock_subprocess):
        cliente = MCPClient(config=cfg)
        resultado = cliente.call_tool("srv", "read_file", {"path": "/tmp/x"})
    assert resultado.get("ok") is True
    assert resultado.get("contenido") == "contenido fake"
    cliente.cerrar()


def test_call_tool_servidor_caido(tmp_path):
    """Llamar a un servidor caído devuelve ``ok=False`` sin lanzar."""
    proceso, _ = _construir_proceso_fake([])
    cfg = {"roto": {"command": "nope", "args": [], "env": {}, "enabled": True}}
    mock_subprocess = MagicMock()
    mock_subprocess.Popen.return_value = proceso
    with patch.object(mcp_client, "subprocess", mock_subprocess):
        cliente = MCPClient(config=cfg)
    resultado = cliente.call_tool("roto", "x", {})
    assert resultado.get("ok") is False
    assert "no disponible" in resultado.get("error", "")
    cliente.cerrar()


# --- Tests de integración con mcp_tools -------------------------------------


def test_cargar_externas_registra_con_prefijo(tmp_path):
    """``_cargar_herramientas_mcp_externas`` registra con prefijo ``mcp_``."""
    handshake = json.dumps(_respuesta_mcp(1, _handshake_result()))
    tools_resp = json.dumps(_respuesta_mcp(2, {"tools": _herramientas_ejemplo()}))
    proceso, _ = _construir_proceso_fake([handshake, tools_resp])
    cfg = {"srv": {"command": "fake", "args": [], "env": {}, "enabled": True}}
    # Limpiamos el singleton para que se cree uno nuevo con nuestra config.
    import mcp_client as _mc
    _mc._CLIENTE = None
    mock_subprocess = MagicMock()
    mock_subprocess.Popen.return_value = proceso
    with patch.object(mcp_client, "subprocess", mock_subprocess), \
         patch("mcp_client.servers_configurados", return_value=cfg):
        from mcp_tools import _cargar_herramientas_mcp_externas
        externas = _cargar_herramientas_mcp_externas()
    _mc._CLIENTE = None  # cleanup
    assert len(externas) >= 1
    for nombre, meta in externas.items():
        assert nombre.startswith("mcp_")
        assert meta["mcp_externo"] is True
        assert meta["requiere_permiso"] is True


def test_cargar_externas_sin_mcp_client(tmp_path, monkeypatch):
    """Si mcp_client no se puede importar, devuelve vacío (degradación)."""
    monkeypatch.setitem(sys.modules, "mcp_client", None)
    from mcp_tools import _cargar_herramientas_mcp_externas
    assert _cargar_herramientas_mcp_externas() == {}


# --- Tests de CLI -----------------------------------------------------------


def test_cli_list_sin_servidores(tmp_path):
    """``mcp list`` sin servidores muestra mensaje amigable."""
    with patch("mcp_client.ruta_config_servidores", return_value=tmp_path / "mcp.json"):
        assert _ejecutar_comando_mcp(["list"]) == 0


def test_cli_add_y_list(tmp_path):
    """``mcp add`` añade un servidor y queda persistido."""
    ruta = tmp_path / "mcp_servers.json"
    with patch("mcp_client.ruta_config_servidores", return_value=ruta):
        assert _ejecutar_comando_mcp(["add", "fs", "npx", "-y", "srv-filesystem"]) == 0
        assert ruta.is_file()
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        assert "fs" in datos["servers"]
        assert datos["servers"]["fs"]["command"] == "npx"


def test_cli_remove(tmp_path):
    """``mcp remove`` elimina un servidor."""
    ruta = tmp_path / "mcp_servers.json"
    datos = {"servers": {"git": {"command": "uvx", "args": ["mcp-server-git"], "enabled": True}}}
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    with patch("mcp_client.ruta_config_servidores", return_value=ruta):
        assert _ejecutar_comando_mcp(["remove", "git"]) == 0
        assert json.loads(ruta.read_text(encoding="utf-8")) == {"servers": {}}


def test_cli_add_faltan_args(tmp_path):
    """``mcp add`` sin suficientes args devuelve código 1."""
    with patch("mcp_client.ruta_config_servidores", return_value=tmp_path / "mcp.json"):
        assert _ejecutar_comando_mcp(["add", "solo_nombre"]) == 1


def test_cli_accion_desconocida(tmp_path):
    """Acción desconocida devuelve código 1."""
    with patch("mcp_client.ruta_config_servidores", return_value=tmp_path / "mcp.json"):
        assert _ejecutar_comando_mcp(["patear"]) == 1


def test_cli_help(tmp_path):
    """``mcp help`` devuelve 0."""
    with patch("mcp_client.ruta_config_servidores", return_value=tmp_path / "mcp.json"):
        assert _ejecutar_comando_mcp(["help"]) == 0


# --- Test de integración opcional con servidor real -------------------------


@pytest.mark.skipif(
    subprocess.run(["npx", "--version"], capture_output=True).returncode != 0,
    reason="npx no está disponible en el entorno",
)
@pytest.mark.skip(reason="requiere servidor MCP externo y red; ejecutar manualmente")
def test_integracion_server_filesystem_real(tmp_path):
    """Integración: conecta con ``@modelcontextprotocol/server-filesystem``.

    Se marca skip por defecto porque necesita npx + descarga del paquete.
    Desmarcar para ejecutar manualmente.
    """
    cfg = {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", str(tmp_path)], "enabled": True}}
    cliente = MCPClient(config=cfg)
    tools = cliente.list_tools()
    assert any("filesystem" in s for s in tools)
    cliente.cerrar()

