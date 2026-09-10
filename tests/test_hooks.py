"""Tests para hooks.py: sistema de hooks/plugins."""
import tempfile
from pathlib import Path

import pytest

import hooks as hk

EVENTO_TEST = "before_tool_use"


@pytest.fixture(autouse=True)
def _reset_hooks():
    """Activa hooks y limpia antes de cada test."""
    hk.activar()
    hk.MANAGER.limpiar()
    yield
    hk.MANAGER.limpiar()


class TestEstadoHooks:
    def test_activar_desactivar(self):
        hk.activar()
        assert hk.activo() is True
        hk.desactivar()
        assert hk.activo() is False
        hk.activar()

    def test_activo_inicial(self):
        hk.activar()
        assert hk.activo() is True


class TestManagerBasico:
    def test_manager_existe(self):
        assert hk.MANAGER is not None

    def test_registrar_y_ejecutar(self):
        llamados = []
        hk.MANAGER.registrar(EVENTO_TEST, lambda c: llamados.append(c))
        hk.MANAGER.ejecutar(EVENTO_TEST, {"k": "v"})
        assert llamados == [{"k": "v"}]

    def test_sin_managers_no_falla(self):
        hk.MANAGER.ejecutar("sin_managers")

    def test_listar_vacio(self):
        assert hk.MANAGER.listar() == {}

    def test_hooks_de_inexistente(self):
        assert hk.MANAGER.hooks_de("no_existe") == []

    def test_total_inicial(self):
        assert hk.MANAGER.total() == 0

    def test_desregistrar(self):
        f = lambda c: None
        hk.MANAGER.registrar(EVENTO_TEST, f)
        ok = hk.MANAGER.desregistrar(EVENTO_TEST, f)
        assert ok is True

    def test_ejecutar_con_contexto_vacio(self):
        llamados = []
        hk.MANAGER.registrar(EVENTO_TEST, lambda c: llamados.append("x"))
        hk.MANAGER.ejecutar(EVENTO_TEST)
        assert "x" in llamados

    def test_ejecutar_abort(self):
        def aborta(ctx):
            return {"abort": True, "razon": "no"}
        hk.MANAGER.registrar(EVENTO_TEST, aborta)
        abortado, _ = hk.MANAGER.ejecutar(EVENTO_TEST, {})
        assert abortado is True

    def test_ejecutar_modifica_contexto(self):
        def mod(ctx):
            return {"nuevo": "valor"}
        hk.MANAGER.registrar(EVENTO_TEST, mod)
        _, ctx = hk.MANAGER.ejecutar(EVENTO_TEST, {})
        assert ctx.get("nuevo") == "valor"


class TestEjecutarHook:
    def test_ejecutar_hook_global(self):
        recibidos = []
        hk.registrar_hook(EVENTO_TEST, lambda ctx: recibidos.append(ctx))
        hk.ejecutar_hook(EVENTO_TEST, {"dato": 1})
        assert recibidos == [{"dato": 1}]

    def test_ejecutar_sin_hooks_no_falla(self):
        hk.ejecutar_hook("evento_inexistente")


class TestListarHooks:
    def test_listar_devuelve_string(self):
        texto = hk._listar_hooks_texto()
        assert isinstance(texto, str)


class TestEjecutarScript:
    def test_script_inexistente(self, tmp_path):
        resultado = hk._ejecutar_script_shell(tmp_path / "no.sh", {})
        assert resultado is None


class TestCargarModulo:
    def test_modulo_inexistente(self, tmp_path):
        resultado = hk._cargar_modulo_python(tmp_path / "no.py")
        assert resultado is None


class TestCargarHooks:
    def test_sin_archivo(self, tmp_path):
        resultado = hk.cargar_hooks_desde_archivos(tmp_path)
        assert isinstance(resultado, int)

    def test_sin_directorio(self):
        resultado = hk.cargar_hooks_desde_archivos()
        assert isinstance(resultado, int)


class TestDesactivar:
    def test_desactivar_no_ejecuta(self):
        hk.desactivar()
        recibidos = []
        hk.registrar_hook(EVENTO_TEST, lambda ctx: recibidos.append(ctx))
        hk.ejecutar_hook(EVENTO_TEST, {})
        assert recibidos == []
        hk.activar()


