"""Tests simplificados para hooks.py."""

import hooks as hk


class TestEstadoHooks:
    def test_activar_desactivar(self):
        hk.activar()
        assert hk.activo() is True
        hk.desactivar()
        assert hk.activo() is False
        hk.activar()

    def test_listar_hooks_texto(self):
        assert isinstance(hk._listar_hooks_texto(), str)


class TestManager:
    def test_manager_existe(self):
        assert hk.MANAGER is not None
