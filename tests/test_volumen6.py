"""Tests de volumen Fase 1d-2: mcp_tools, plugins, AST, historial, hash."""

from pathlib import Path
from unittest import mock

import snapcontext as sc


class TestMcpToolsVol2:
    def test_entero_opcional(self):
        assert sc._entero_opcional("5") == 5
        assert sc._entero_opcional(None) is None
        assert sc._entero_opcional("x") is None

    def test_formatear_resultado(self):
        r = sc._formatear_resultado_mcp({"ok": True, "salida": "hola"}, 40)
        assert isinstance(r, str)

    def test_ruta_mcp_tools(self):
        import mcp_tools

        assert isinstance(mcp_tools._ruta_mcp_tools(), Path)

    def test_contexto_automatico(self):
        assert isinstance(sc._contexto_automatico_mcp("hola"), str)


class TestResumenAst:
    def test_python(self):
        r = sc._resumen_ast("def f():\n    pass\n", "a.py")
        assert isinstance(r, dict)

    def test_no_python_sin_parser(self):
        with mock.patch.object(sc, "_lenguaje_archivo", return_value="js"):
            r = sc._resumen_ast("var x=1;", "a.js")
        assert isinstance(r, dict)


class TestHistorialVol:
    def test_mostrar_vacio(self):
        with mock.patch.object(sc, "_cargar_historial", return_value=[]):
            assert sc._mostrar_historial() == 0

    def test_mostrar_con_entradas(self):
        with mock.patch.object(
            sc, "_cargar_historial", return_value=[{"consulta": "a"}, {"consulta": "b"}]
        ):
            assert sc._mostrar_historial() == 2


class TestRevertirPaso:
    def test_sin_filas(self):
        with mock.patch.object(sc, "_db_query", return_value=[]):
            assert sc._revertir_paso(1) is False


class TestPluginVol:
    def test_sin_subargv(self):
        with mock.patch.object(sc, "_plugin_mostrar", return_value=None):
            assert sc._ejecutar_comando_plugin([]) == 0

    def test_list(self):
        with mock.patch.object(sc, "_plugin_mostrar", return_value=None):
            assert sc._ejecutar_comando_plugin(["list"]) == 0

    def test_install_sin_nombre(self):
        with mock.patch.object(sc, "error"):
            assert sc._ejecutar_comando_plugin(["install"]) == 1


class TestHashProyecto:
    def test_dir_vacio(self, tmp_path):
        assert isinstance(sc._hash_proyecto(tmp_path), str)


class TestExtraerDependencias:
    def test_python(self):
        r = sc._extraer_dependencias("import os\nfrom x import y\n", "python")
        assert "os" in r

    def test_vacio(self):
        assert sc._extraer_dependencias("", "python") == []


class TestEditorAstVacio:
    def test_archivo_vacio(self):
        with mock.patch.object(sc, "error"):
            assert sc._editor_ast("", "tarea") is False
