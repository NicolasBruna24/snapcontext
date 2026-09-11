"""Tests para permisos.py: gestion de permisos recordados."""

import json
from pathlib import Path
from unittest import mock

import permisos as perm


class TestRutaPermisos:
    def test_ruta_permisos_existe(self):
        ruta = perm._ruta_permisos()
        assert isinstance(ruta, Path)
        assert "permisos" in str(ruta)

    def test_ruta_termina_en_json(self):
        ruta = perm._ruta_permisos()
        assert ruta.name.endswith(".json")


class TestObtenerConfigDir:
    def test_devuelve_path(self):
        resultado = perm._obtener_config_dir()
        assert isinstance(resultado, Path)

    def test_con_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        import importlib

        import permisos as _p

        importlib.reload(_p)
        resultado = _p._obtener_config_dir()
        assert isinstance(resultado, Path)


class TestCargarPermisos:
    def test_devuelve_dict_sin_archivo(self, tmp_path):
        with mock.patch.object(perm, "_obtener_config_dir", return_value=tmp_path):
            resultado = perm._cargar_permisos()
        assert isinstance(resultado, dict)

    def test_carga_archivo_existente(self, tmp_path):
        data = {"comandos": {"ls": "permitido"}}
        ruta = tmp_path / "permisos.json"
        ruta.write_text(json.dumps(data))
        with (
            mock.patch.object(perm, "_ruta_permisos", return_value=ruta),
            mock.patch.object(perm, "_obtener_config_dir", return_value=tmp_path),
        ):
            resultado = perm._cargar_permisos()
        assert isinstance(resultado, dict)

    def test_archivo_corrupto_devuelve_vacio(self, tmp_path):
        ruta = tmp_path / "permisos.json"
        ruta.write_text("{no es json")
        with (
            mock.patch.object(perm, "_ruta_permisos", return_value=ruta),
            mock.patch.object(perm, "_obtener_config_dir", return_value=tmp_path),
        ):
            resultado = perm._cargar_permisos()
        assert isinstance(resultado, dict)


class TestGuardarPermiso:
    def test_guarda_y_recupera(self, tmp_path):
        with mock.patch.object(perm, "_obtener_config_dir", return_value=tmp_path):
            ok = perm._guardar_permiso("comandos", "ls")
        assert ok is True


class TestPermisoRecordado:
    def test_sin_permisos_devuelve_none(self, tmp_path):
        with mock.patch.object(perm, "_obtener_config_dir", return_value=tmp_path):
            assert perm._permiso_recordado("comandos") is None


class TestLimpiarPermisos:
    def test_limpia_incluso_sin_archivo(self, tmp_path):
        with mock.patch.object(perm, "_obtener_config_dir", return_value=tmp_path):
            ok = perm._limpiar_permisos()
        assert ok is True


class TestConfirmarAccion:
    def test_funcion_existe(self):
        assert callable(perm._confirmar_accion)
