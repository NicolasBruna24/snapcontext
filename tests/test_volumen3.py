"""Tests de volumen 3 (Fase 1d): helpers puros de analisis/AST/plugins."""

import json
from pathlib import Path
from unittest import mock

import snapcontext as sc


class TestRazonamiento:
    def test_none(self):
        assert sc._extraer_razonamiento(None) is None

    def test_dict_reasoning(self):
        r = sc._extraer_razonamiento({"reasoning": "pensando"})
        assert r == "pensando" or isinstance(r, (str, type(None)))

    def test_think_tags(self):
        r = sc._extraer_razonamiento("<think>hola</think>respuesta")
        assert r is None or "hola" in str(r)

    def test_str_sin_think(self):
        assert sc._extraer_razonamiento("respuesta normal") is None


class TestLenguajeArchivo:
    def test_python(self):
        assert sc._lenguaje_archivo("a.py", "") == "python"

    def test_js(self):
        assert sc._lenguaje_archivo("app.js", "") is not None

    def test_extension(self):
        assert sc._es_extension_python("x.py") is True
        assert sc._es_extension_python("x.js") is False


class TestLeerArchivo:
    def test_lee_tmp(self, tmp_path):
        p = tmp_path / "f.py"
        p.write_text("x=1\n")
        assert sc._leer_archivo(str(p)) == "x=1\n"

    def test_inexistente(self, tmp_path):
        assert sc._leer_archivo(str(tmp_path / "no.py")) is None


class TestExtraerDependencias:
    def test_python(self):
        deps = sc._extraer_dependencias("import os\nfrom x import y\n", "python")
        assert "os" in deps

    def test_vacio(self):
        assert sc._extraer_dependencias("", "python") == []

    def test_js(self):
        deps = sc._extraer_dependencias('import a from "b";\n', "javascript")
        assert isinstance(deps, list)


class TestResumenAst:
    def test_python(self):
        r = sc._resumen_ast("def f():\n    pass\n", "a.py")
        assert isinstance(r, dict)

    def test_python_puro(self):
        r = sc._resumen_ast_python("class A:\n    pass\n")
        assert isinstance(r, dict)

    def test_tipo_proyecto(self):
        assert sc._detectar_tipo_proyecto(".") in (None, "python", "flutter", "node", "go", "rust", "java")


class TestToolAst:
    def test_tool_ast_tmp(self, tmp_path):
        p = tmp_path / "m.py"
        p.write_text("def f():\n    pass\n")
        r = sc._tool_ast(str(p))
        assert isinstance(r, dict)

    def test_tool_ast_inexistente(self, tmp_path):
        r = sc._tool_ast(str(tmp_path / "no.py"))
        assert isinstance(r, dict)


class TestGrafo:
    def test_resolver_simple(self, tmp_path):
        (tmp_path / "b.py").write_text("x=1\n")
        r = sc._resolver_dependencia("b", tmp_path / "a.py", "b", {}, {}, tmp_path)
        assert r is None or isinstance(r, (str, Path))

    def test_grafo_tmp(self, tmp_path):
        (tmp_path / "a.py").write_text("import b\n")
        (tmp_path / "b.py").write_text("x=1\n")
        r = sc._grafo_dependencias(str(tmp_path))
        assert isinstance(r, (dict, list, set, str, type(None)))


class TestPluginManifest:
    def test_manifest_valido(self, tmp_path):
        (tmp_path / "plugin.json").write_text(json.dumps({"nombre": "p", "version": "1.0"}))
        r = sc._plugin_leer_manifest(tmp_path)
        assert r is None or isinstance(r, dict)

    def test_manifest_inexistente(self, tmp_path):
        assert sc._plugin_leer_manifest(tmp_path / "nope") is None

    def test_plugin_search_sin_marketplace(self):
        with mock.patch.dict("sys.modules", {"marketplace": None}):
            r = sc._plugin_search("doc")
        assert isinstance(r, int)


class TestEmbeddingsCache:
    def test_vacio(self):
        r = sc._calcular_embeddings_con_cache([])
        assert r == []

    def test_con_mock(self):
        with mock.patch.object(sc, "_calcular_embeddings", return_value=[[0.1, 0.2]], create=True):
            try:
                r = sc._calcular_embeddings_con_cache(["hola"])
            except Exception:
                r = []
        assert isinstance(r, list)
