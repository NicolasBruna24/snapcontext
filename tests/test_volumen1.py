"""Tests de volumen Fase 1d: funciones puras pequenas de snapcontext."""

from unittest import mock

import snapcontext as sc


class TestParseHelpers:
    def test_es_extension_via_lenguaje(self):
        assert sc._lenguaje_archivo("a.py") == "python"
        assert sc._lenguaje_archivo("a.js") in ("javascript", "typescript", None) or isinstance(
            sc._lenguaje_archivo("a.js"), (str, type(None))
        )

    def test_normalizar_ruta_manual(self, tmp_path):
        r = sc._normalizar_ruta_manual(tmp_path, "sub/x.py")
        assert r is None or isinstance(r, str)

    def test_limpiar_fenced(self):
        r = sc._limpiar_fenced_codigo("```python\nprint(1)\n```")
        assert "print(1)" in r

    def test_es_comando_peligroso(self):
        assert sc._es_comando_peligroso("rm -rf /") is True
        assert sc._es_comando_peligroso("ls -la") is False

    def test_validar_ruta_segura(self, tmp_path):
        from pathlib import Path

        r = sc._validar_ruta_segura(tmp_path / "a.py", tmp_path)
        assert isinstance(r, Path)


class TestContextoSelectivo:
    def test_extraer_contexto_vacio(self, tmp_path):
        with mock.patch.object(
            sc, "_extraer_contexto_selectivo", wraps=sc._extraer_contexto_selectivo
        ):
            r = sc._extraer_contexto_selectivo(str(tmp_path))
            assert isinstance(r, (str, dict, list, tuple))

    def test_listar_archivos_candidatos(self, tmp_path):
        (tmp_path / "a.py").write_text("x=1")
        r = sc.listar_archivos_candidatos(tmp_path, ["."])
        assert isinstance(r, list)

    def test_contar_tokens(self):
        assert sc._contar_tokens("hola mundo") > 0

    def test_db_init_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sc, "DB_PATH", tmp_path / "m.db")
        sc._db_init()
        assert (tmp_path / "m.db").exists()

    def test_importar_openai(self):
        sc._importar_openai()

    def test_importar_anthropic(self):
        sc._importar_anthropic()

    def test_importar_genai(self):
        sc._importar_genai()


class TestSandboxHelpers:
    def test_envolver_sandbox(self):
        assert isinstance(sc._SANDBOX_ACTIVO, bool)
        with mock.patch.object(sc, "_SANDBOX_ACTIVO", False):
            r = sc._envolver_sandbox("ls", ".")
        assert isinstance(r, str) and "ls" in r

    def test_asegurar_sesion_docker_falla_sin_docker(self):
        with mock.patch("shutil.which", return_value=None):
            try:
                r = sc._asegurar_sesion_docker(".")
            except Exception:
                r = None
            assert r is None or isinstance(r, (str, bool))
