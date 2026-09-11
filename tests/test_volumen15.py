"""Tests Fase 1d: mcp_tools.py helpers de presentacion/contexto."""

from unittest import mock

import mcp_tools as mt


class TestFormatearResultadoMCP:
    def test_fallo(self):
        r = mt._formatear_resultado_mcp({"ok": False, "herramienta": "db", "error": "boom"})
        assert "boom" in r

    def test_vacio_ok(self):
        assert mt._formatear_resultado_mcp({"ok": True, "resultado": {}}) == "(sin datos)"

    def test_contenido_multilinea(self):
        llamada = {"ok": True, "resultado": {"contenido": "a\nb\nc"}}
        r = mt._formatear_resultado_mcp(llamada, max_lineas=2)
        assert "a" in r and "c" in r

    def test_contenido_texto(self):
        llamada = {"ok": True, "resultado": {"contenido": "linea_unica"}}
        r = mt._formatear_resultado_mcp(llamada)
        assert "linea_unica" in r

    def test_valor_lista(self):
        llamada = {"ok": True, "resultado": {"items": [1, 2, 3]}}
        r = mt._formatear_resultado_mcp(llamada)
        assert "items (3)" in r

    def test_valor_escalar(self):
        llamada = {"ok": True, "resultado": {"clave": "valor"}}
        assert mt._formatear_resultado_mcp(llamada) == "clave: valor"


class TestContextoAutomaticoMCP:
    @mock.patch(
        "snapcontext._ejecutar_herramienta_mcp",
        return_value={"ok": True, "resultado": {"clave": "v"}},
    )
    def test_buscar(self, me):
        r = mt._contexto_automatico_mcp("busca el token en main.py")
        assert "[grep]" in r
        me.assert_called()

    @mock.patch(
        "snapcontext._ejecutar_herramienta_mcp",
        return_value={"ok": True, "resultado": {"clave": "v"}},
    )
    def test_git_status(self, me):
        r = mt._contexto_automatico_mcp("muéstrame el estado de git")
        assert "[git_status]" in r

    @mock.patch(
        "snapcontext._ejecutar_herramienta_mcp",
        return_value={"ok": True, "resultado": {"clave": "v"}},
    )
    def test_list_files(self, me):
        r = mt._contexto_automatico_mcp("lista los archivos del proyecto")
        assert "[list_files]" in r

    @mock.patch(
        "snapcontext._ejecutar_herramienta_mcp",
        return_value={"ok": True, "resultado": {"clave": "v"}},
    )
    def test_sin_disparo(self, me):
        r = mt._contexto_automatico_mcp("hola que tal")
        assert r == ""
        me.assert_not_called()
