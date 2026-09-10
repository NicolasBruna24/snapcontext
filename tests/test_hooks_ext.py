"""Tests extra para hooks.py: carga de plugins y modulos."""
import json
from pathlib import Path
from unittest import mock

import hooks as hk


def _crear_plugin(tmp_path, nombre="demo", hooks=None, fail_exec=False):
    pdir = tmp_path / nombre
    pdir.mkdir()
    script = pdir / "hook.py"
    if fail_exec:
        script.write_text("raise RuntimeError('boom')")
    else:
        script.write_text("def ejecutar(ctx): return ctx")
    manifiesto = {"name": nombre, "hooks": hooks or {"after_tool_use": "hook.py"}}
    (pdir / "plugin.json").write_text(json.dumps(manifiesto))
    return pdir


class TestCargarModuloPython:
    def test_modulo_valido(self, tmp_path):
        ruta = tmp_path / "mod.py"
        ruta.write_text("def ejecutar(ctx): return ctx")
        mod = hk._cargar_modulo_python(ruta)
        assert mod is not None
        assert callable(mod.ejecutar)

    def test_archivo_inexistente(self, tmp_path):
        assert hk._cargar_modulo_python(tmp_path / "no_existe.py") is None

    def test_modulo_con_error(self, tmp_path):
        ruta = tmp_path / "malo.py"
        ruta.write_text("raise RuntimeError('boom')")
        assert hk._cargar_modulo_python(ruta) is None


class TestRegistrarDesdeManifiesto:
    def test_manifiesto_valido(self, tmp_path):
        hk.activar()
        _crear_plugin(tmp_path, "demo")
        n = hk._registrar_desde_manifiesto(tmp_path / "demo")
        assert n >= 1

    def test_manifiesto_inexistente(self, tmp_path):
        n = hk._registrar_desde_manifiesto(tmp_path / "no_existe")
        assert n == 0

    def test_manifiesto_malo(self, tmp_path):
        pdir = tmp_path / "malo"
        pdir.mkdir()
        (pdir / "plugin.json").write_text("{esto no es json")
        n = hk._registrar_desde_manifiesto(pdir)
        assert n == 0

    def test_evento_desconocido(self, tmp_path):
        hk.activar()
        pdir = tmp_path / "demo"
        pdir.mkdir()
        (pdir / "plugin.json").write_text(json.dumps({"name": "x", "hooks": {"evento_raro": "x.py"}}))
        n = hk._registrar_desde_manifiesto(pdir)
        assert n == 0

    def test_script_inexistente(self, tmp_path):
        hk.activar()
        pdir = tmp_path / "demo"
        pdir.mkdir()
        (pdir / "plugin.json").write_text(json.dumps({"name": "x", "hooks": {"after_tool_use": "no_existe.py"}}))
        n = hk._registrar_desde_manifiesto(pdir)
        assert n == 0

    def test_sin_funcion_ejecutar(self, tmp_path):
        hk.activar()
        pdir = tmp_path / "demo"
        pdir.mkdir()
        script = pdir / "hook.py"
        script.write_text("x = 1")
        (pdir / "plugin.json").write_text(json.dumps({"name": "x", "hooks": {"after_tool_use": "hook.py"}}))
        n = hk._registrar_desde_manifiesto(pdir)
        assert n == 0


class TestCargarHooksPlugins:
    def test_sin_directorio(self, tmp_path):
        hk.activar()
        with mock.patch.object(hk, "DIR_PLUGINS", tmp_path / "no_existe"):
            n = hk.cargar_hooks_desde_plugins()
        assert n == 0

    def test_con_plugin_valido(self, tmp_path):
        hk.activar()
        _crear_plugin(tmp_path, "demo")
        with mock.patch.object(hk, "DIR_PLUGINS", tmp_path):
            n = hk.cargar_hooks_desde_plugins()
        assert n >= 1
