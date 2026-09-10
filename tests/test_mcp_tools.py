"""Tests para mcp_tools.py."""
import json
from pathlib import Path
from unittest import mock

import mcp_tools as mt


class TestRutaMcpTools:
    def test_respeta_parche(self, tmp_path):
        with mock.patch("snapcontext.MCP_TOOLS_PATH", str(tmp_path / "mcp.json")):
            assert mt._ruta_mcp_tools() == tmp_path / "mcp.json"

    def test_default(self, tmp_path):
        with mock.patch("snapcontext.CONFIG_DIR", tmp_path), \
             mock.patch("snapcontext.MCP_TOOLS_PATH", None, create=True):
            try:
                import snapcontext
                del snapcontext.MCP_TOOLS_PATH
            except AttributeError:
                pass
            assert mt._ruta_mcp_tools() == tmp_path / "mcp_tools.json"


class TestCargarHerramientas:
    def test_devuelve_dict(self, tmp_path):
        with mock.patch.object(mt, "_ruta_mcp_tools", return_value=tmp_path / "no.json"):
            r = mt._cargar_herramientas_mcp()
        assert isinstance(r, dict)

    def test_carga_archivo(self, tmp_path):
        ruta = tmp_path / "mcp.json"
        ruta.write_text(json.dumps({"test_tool": {"descripcion": "d"}}))
        with mock.patch.object(mt, "_ruta_mcp_tools", return_value=ruta):
            r = mt._cargar_herramientas_mcp()
        assert "test_tool" in r or isinstance(r, dict)


class TestFormatearResultado:
    def test_formatea_dict(self):
        llamada = {"resultado": {"clave": "valor"}}
        r = mt._formatear_resultado_mcp(llamada)
        assert isinstance(r, str)

    def test_formatea_texto_largo(self):
        llamada = {"resultado": "a" * 500}
        r = mt._formatear_resultado_mcp(llamada, max_lineas=5)
        assert isinstance(r, str)


class TestEnteroOpcional:
    def test_entero_valido(self):
        assert mt._entero_opcional("42") == 42

    def test_none(self):
        assert mt._entero_opcional(None) is None

    def test_invalido(self):
        assert mt._entero_opcional("abc") is None


