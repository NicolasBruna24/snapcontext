"""Tests Fase 1d: _determinar_proveedor y _validar_sintaxis (monolito)."""

import argparse
import subprocess
from unittest import mock

import snapcontext as sc


def _args(provider=None, modelo=None, no_persist=False):
    return argparse.Namespace(provider=provider, modelo=modelo, no_persist=no_persist)


class TestDeterminarProveedor:
    @mock.patch.object(sc, "guardar_configuracion")
    def test_proveedor_cli(self, mg):
        res = sc._determinar_proveedor(_args(provider="gemini", modelo="flash"))
        assert res == {"provider": "gemini", "model": "flash"}
        mg.assert_called_once_with("gemini", "flash")

    @mock.patch.object(sc, "guardar_configuracion")
    def test_proveedor_cli_no_persist(self, mg):
        res = sc._determinar_proveedor(_args(provider="ollama", no_persist=True))
        assert res["provider"] == "ollama"
        mg.assert_not_called()

    @mock.patch.object(
        sc, "cargar_configuracion", return_value={"provider": "anthropic", "model": "opus"}
    )
    @mock.patch.object(sc, "guardar_configuracion")
    def test_usa_config_guardada(self, mg, mc):
        res = sc._determinar_proveedor(_args())
        assert res == {"provider": "anthropic", "model": "opus"}
        mg.assert_not_called()

    @mock.patch.object(sc, "guardar_configuracion")
    @mock.patch.dict("os.environ", {"SNAPCONTEXT_PROVIDER": "groq"}, clear=False)
    def test_usa_env_sin_config(self, mg):
        with mock.patch.object(sc, "cargar_configuracion", return_value={}):
            res = sc._determinar_proveedor(_args())
        assert res["provider"] == "groq"
        mg.assert_called_once()

    @mock.patch.object(sc, "guardar_configuracion")
    @mock.patch.object(
        sc, "_proveedor_offline", return_value={"provider": "ollama", "model": "llama3"}
    )
    @mock.patch.object(sc, "hay_api_key_configurada", return_value=False)
    def test_offline_sin_clave(self, mh, moff, mg):
        with mock.patch.object(sc, "cargar_configuracion", return_value={}):
            res = sc._determinar_proveedor(_args())
        assert res["provider"] == "ollama"

    @mock.patch.object(sc, "_proveedor_offline", return_value=None)
    @mock.patch.object(sc, "hay_api_key_configurada", return_value=False)
    def test_sin_clave_ni_ollama_raise(self, mh, moff):
        import pytest

        with mock.patch.object(sc, "cargar_configuracion", return_value={}):
            with pytest.raises(RuntimeError):
                sc._determinar_proveedor(_args())

    @mock.patch.object(
        sc, "seleccionar_proveedor_interactivo", return_value=("openai", "gpt-4o-mini")
    )
    @mock.patch.object(sc, "hay_api_key_configurada", return_value=True)
    @mock.patch.object(sc, "guardar_configuracion")
    def test_interactivo_con_clave(self, mg, mh, ms):
        with mock.patch.object(sc, "cargar_configuracion", return_value={}):
            res = sc._determinar_proveedor(_args())
        assert res == {"provider": "openai", "model": "gpt-4o-mini"}


class TestComandosValidacion:
    def test_sin_lenguaje(self):
        assert sc._comandos_validacion("", "x.py") == []

    def test_python(self):
        cmds = sc._comandos_validacion("python", "/tmp/a.py")
        assert len(cmds) == 1
        assert cmds[0][-1] == "/tmp/a.py"

    def test_js(self):
        cmds = sc._comandos_validacion("typescript", "/tmp/a.ts")
        assert cmds[0][0] == "node"

    def test_go(self):
        cmds = sc._comandos_validacion("go", "/tmp/a.go")
        assert len(cmds) == 2

    def test_desconocido(self):
        assert sc._comandos_validacion("brainfuck", "/tmp/a.bf") == []


class TestValidarSintaxis:
    @mock.patch.object(sc, "_lenguaje_archivo", return_value=None)
    def test_sin_lenguaje_omite(self, ml):
        ok, msg = sc._validar_sintaxis("foto.png", "data")
        assert ok is True and msg == ""

    @mock.patch.object(sc, "_lenguaje_archivo", return_value="python")
    @mock.patch.object(sc, "_comandos_validacion", return_value=[])
    def test_sin_validadores_omite(self, mcv, ml):
        ok, msg = sc._validar_sintaxis("a.py", "code")
        assert ok is True and msg == ""

    @mock.patch.object(sc, "_lenguaje_archivo", return_value="python")
    @mock.patch("shutil.which", return_value=None)
    def test_binario_no_disponible_omite(self, mw, ml):
        with mock.patch.object(sc, "_comandos_validacion", return_value=[["py", "a.py"]]):
            ok, _ = sc._validar_sintaxis("a.py", "code")
        assert ok is True

    @mock.patch.object(sc, "_lenguaje_archivo", return_value="python")
    @mock.patch("shutil.which", return_value="/usr/bin/python")
    def test_comando_ok(self, mw, ml):
        proc = subprocess.CompletedProcess(["python", "-m", "py_compile", "x"], 0, "", "")
        with (
            mock.patch.object(
                sc,
                "_comandos_validacion",
                return_value=[["python", "-m", "py_compile", "/tmp/x.py"]],
            ),
            mock.patch("subprocess.run", return_value=proc),
        ):
            ok, msg = sc._validar_sintaxis("a.py", "print(1)")
        assert ok is True and msg == ""

    @mock.patch.object(sc, "_lenguaje_archivo", return_value="python")
    @mock.patch("shutil.which", return_value="/usr/bin/python")
    def test_comando_error_devuelve_mensaje(self, mw, ml):
        proc = subprocess.CompletedProcess(["x"], 1, "", "IndentationError")
        with (
            mock.patch.object(sc, "_comandos_validacion", return_value=[["python", "x.py"]]),
            mock.patch("subprocess.run", return_value=proc),
        ):
            ok, msg = sc._validar_sintaxis("a.py", "bad")
        assert ok is False
        assert "IndentationError" in msg

    @mock.patch.object(sc, "_lenguaje_archivo", return_value="python")
    @mock.patch("shutil.which", return_value="/usr/bin/python")
    def test_timeout_devuelve_error(self, mw, ml):
        with (
            mock.patch.object(sc, "_comandos_validacion", return_value=[["python", "x.py"]]),
            mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 60)),
        ):
            ok, msg = sc._validar_sintaxis("a.py", "x")
        assert ok is False
        assert "tiempo límite" in msg
