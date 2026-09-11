"""Tests para instalador.py."""

from pathlib import Path
from unittest import mock

import instalador as inst


class TestCarpetasScripts:
    def test_candidatos_no_vacio(self):
        r = inst._candidatos_carpetas_scripts()
        assert isinstance(r, list)
        assert len(r) > 0


class TestLocalizarScripts:
    def test_devuelve_none_sin_carpeta(self):
        with mock.patch.object(inst, "_candidatos_carpetas_scripts", return_value=[]):
            assert inst._localizar_carpeta_scripts() is None


class TestSnapcontextEnPath:
    def test_booleano(self):
        assert isinstance(inst.snapcontext_en_path(), bool)


class TestConfigurarPath:
    def test_retorna_entero(self):
        with mock.patch.object(inst, "_localizar_carpeta_scripts", return_value=None):
            r = inst.configurar_path()
        assert isinstance(r, int)

    def test_con_carpeta_scripts(self, tmp_path):
        with mock.patch.object(inst, "_localizar_carpeta_scripts", return_value=str(tmp_path)):
            r = inst.configurar_path()
        assert isinstance(r, int)
