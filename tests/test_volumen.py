"""Tests mcp_tools: dispatcher y carga de herramientas."""

import json
from pathlib import Path

import mcp_tools as mcp


class TestMcpTools:
    def test_ruta_respeta_parche(self, tmp_path, monkeypatch):
        import snapcontext as sc

        monkeypatch.setattr(sc, "MCP_TOOLS_PATH", str(tmp_path / "m.json"), raising=False)
        assert mcp._ruta_mcp_tools() == Path(tmp_path / "m.json")

    def test_cargar_predefinidas(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mcp, "_ruta_mcp_tools", lambda: tmp_path / "no.json")
        h = mcp._cargar_herramientas_mcp()
        assert "grep" in h and "execute_command" in h

    def test_cargar_usuario(self, tmp_path, monkeypatch):
        p = tmp_path / "m.json"
        p.write_text(
            json.dumps(
                {
                    "tools": [
                        {
                            "nombre": "buildx",
                            "descripcion": "b",
                            "comando": "npm run build",
                            "requiere_permiso": False,
                        },
                        {"nombre": "", "comando": ""},
                    ]
                }
            )
        )
        monkeypatch.setattr(mcp, "_ruta_mcp_tools", lambda: p)
        assert "buildx" in mcp._cargar_herramientas_mcp()

    def test_cargar_corrupto(self, tmp_path, monkeypatch):
        p = tmp_path / "m.json"
        p.write_text("{mal")
        monkeypatch.setattr(mcp, "_ruta_mcp_tools", lambda: p)
        assert "grep" in mcp._cargar_herramientas_mcp()

    def test_ejecutar_read_file(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("l1\nl2\nl3\n")
        r = mcp._ejecutar_herramienta_mcp("read_file", {"ruta": str(f)}, confirmar=False)
        assert r["ok"] is True

    def test_ejecutar_list_files(self, tmp_path):
        r = mcp._ejecutar_herramienta_mcp(
            "list_files", {"directorio": str(tmp_path)}, confirmar=False
        )
        assert r["ok"] is True

    def test_ejecutar_desconocida(self):
        assert mcp._ejecutar_herramienta_mcp("no_xyz", {}, confirmar=False)["ok"] is False

    def test_entero_opcional(self):
        assert mcp._entero_opcional("5") == 5
        assert mcp._entero_opcional("x") is None
        assert mcp._entero_opcional(None) is None

    def test_formatear_y_contexto(self, tmp_path, monkeypatch):
        t = mcp._formatear_resultado_mcp({"ok": True, "resultado": {"contenido": "hola"}})
        assert isinstance(t, str)
        monkeypatch.setattr(mcp, "_ruta_mcp_tools", lambda: tmp_path / "n.json")
        assert isinstance(mcp._contexto_automatico_mcp("lee", max_llamadas=1), str)
