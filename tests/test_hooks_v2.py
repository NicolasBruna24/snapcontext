"""Tests para hooks.py (HookManager, lifecycle)."""

from pathlib import Path
from unittest import mock

import hooks as hk


class TestEstado:
    def test_activar_desactivar(self):
        hk.activar()
        assert hk.activo() is True
        hk.desactivar()
        assert hk.activo() is False
        hk.activar()


class TestManager:
    def test_registrar_y_ejecutar(self):
        hk.activar()
        recibidos = []
        hk.MANAGER.registrar("before_tool_use", lambda c: recibidos.append(c))
        hk.MANAGER.ejecutar("before_tool_use", {"dato": 1})
        assert recibidos == [{"dato": 1}]

    def test_ejecutar_sin_hooks(self):
        hk.activar()
        assert hk.MANAGER.ejecutar("after_tool_use", {}) == (False, {})

    def test_evento_invalido(self):
        hk.activar()
        assert hk.MANAGER.registrar("evento_invalido", lambda c: None) is False


class TestRegistrarHook:
    def test_registrar_simple(self):
        hk.activar()
        assert hk.registrar_hook("session_start", lambda c: None) is True


class TestEjecutarHook:
    def test_ejecutar_vacio(self):
        hk.activar()
        abortado, ctx = hk.ejecutar_hook("session_end")
        assert abortado is False
        assert isinstance(ctx, dict)

    def test_ejecutar_con_contexto(self):
        hk.activar()
        abortado, ctx = hk.ejecutar_hook("session_end", {"k": "v"})
        assert abortado is False
        assert ctx.get("k") == "v"


class TestEjecutarHookShell:
    def test_script_inexistente(self, tmp_path):
        assert hk._ejecutar_script_shell(tmp_path / "no_existe.sh", {}) is None


class TestListarHooks:
    def test_listar(self):
        assert isinstance(hk._listar_hooks_texto(), str)


class TestWrappers:
    def test_hooks_inicializar(self):
        with mock.patch.object(hk, "cargar_todos_los_hooks", return_value=0):
            hk._hooks_inicializar()

    def test_hooks_ejecutar_ok(self):
        with mock.patch.object(hk, "ejecutar_hook", return_value=(False, {"k": "v"})):
            abortado, ctx = hk._hooks_ejecutar("session_end")
        assert abortado is False
        assert ctx == {"k": "v"}

    def test_hooks_ejecutar_fallo(self):
        with mock.patch.object(hk, "ejecutar_hook", side_effect=Exception("boom")):
            abortado, ctx = hk._hooks_ejecutar("session_end")
        assert abortado is False
