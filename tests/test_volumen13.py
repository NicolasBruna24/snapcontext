"""Tests Fase 1d: instalador.py (path setup)."""

import os
from unittest import mock

import instalador as ins


class TestCandidatosCarpetas:
    def test_devuelve_lista_no_vacia(self):
        cand = ins._candidatos_carpetas_scripts()
        assert isinstance(cand, list)
        assert len(cand) >= 2
        # sin duplicados
        assert len(cand) == len(set(cand))

    def test_incluye_scripts_de_python(self):
        cand = ins._candidatos_carpetas_scripts()
        any("scripts" in c.lower() for c in cand) or any("Scripts" in c for c in cand)
        assert all(c for c in cand)  # sin vacios

    @mock.patch("os.environ", {"APPDATA": "/appdata", "LOCALAPPDATA": "/local"})
    def test_con_variables_entorno(self):
        cand = ins._candidatos_carpetas_scripts()
        assert any("/appdata" in c for c in cand)

    def test_sin_duplicados_con_entorno(self):
        with mock.patch.dict(os.environ, {"APPDATA": "/x"}, clear=False):
            cand = ins._candidatos_carpetas_scripts()
        assert len(cand) == len(set(cand))


class TestLocalizarCarpeta:
    @mock.patch.object(ins, "_candidatos_carpetas_scripts", return_value=["/tmp/scripts_ok"])
    @mock.patch("os.path.isdir", return_value=True)
    def test_devuelve_primera_existente(self, misdir, mcan):
        assert ins._localizar_carpeta_scripts() == "/tmp/scripts_ok"

    @mock.patch.object(ins, "_candidatos_carpetas_scripts", return_value=["/nope"])
    @mock.patch("os.path.isdir", return_value=False)
    def test_devuelve_none_si_no_existe(self, misdir, mcan):
        assert ins._localizar_carpeta_scripts() is None


class TestSnapcontextEnPath:
    @mock.patch("shutil.which", return_value="/usr/bin/snapcontext")
    def test_en_path(self, mw):
        assert ins.snapcontext_en_path() is True

    @mock.patch("shutil.which", return_value=None)
    def test_no_en_path(self, mw):
        assert ins.snapcontext_en_path() is False


class TestGuardarPathWindows:
    @mock.patch("subprocess.run")
    def test_setx_ok(self, mrun):
        import subprocess

        mrun.return_value = subprocess.CompletedProcess(["setx"], 0, "", "")
        assert ins._guardar_path_windows("C:\\xx") is True

    @mock.patch("subprocess.run", side_effect=OSError("no setx"))
    def test_sin_setx_y_sin_winreg(self, mrun):
        with mock.patch.dict("sys.modules", {"winreg": None}):
            assert ins._guardar_path_windows("C:\\xx") is False


class TestConfigurarPath:
    def test_no_windows_devuelve_1(self):
        with mock.patch("sys.platform", "linux"):
            assert ins.configurar_path() == 1

    def test_windows_ok(self):
        with (
            mock.patch("sys.platform", "win32"),
            mock.patch.object(ins, "_localizar_carpeta_scripts", return_value="C:\\Scripts"),
            mock.patch.dict("os.environ", {"PATH": "C:\\Other"}),
            mock.patch.object(ins, "_guardar_path_windows", return_value=True),
            mock.patch("snapcontext._preguntar_si", return_value=False),
        ):
            assert ins.configurar_path() == 0

    def test_windows_fallo_al_guardar(self):
        with (
            mock.patch("sys.platform", "win32"),
            mock.patch.object(ins, "_localizar_carpeta_scripts", return_value="C:\\Scripts"),
            mock.patch.dict("os.environ", {"PATH": "C:\\Other"}),
            mock.patch.object(ins, "_guardar_path_windows", return_value=False),
            mock.patch("snapcontext._preguntar_si", return_value=False),
        ):
            assert ins.configurar_path() == 1

    def test_windows_no_localiza(self):
        with (
            mock.patch("sys.platform", "win32"),
            mock.patch.object(ins, "_localizar_carpeta_scripts", return_value=None),
            mock.patch.object(ins, "_candidatos_carpetas_scripts", return_value=["C:\\Nope"]),
            mock.patch("snapcontext._preguntar_si", return_value=False),
        ):
            assert ins.configurar_path() == 1
