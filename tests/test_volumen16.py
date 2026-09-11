"""Tests Fase 1d: seleccion_archivos_con_openai y asesor automaticas."""

from unittest import mock

import pytest

import snapcontext as sc


class TestSeleccionarArchivosConOpenAI:
    @mock.patch("snapcontext._importar_openai", return_value=None)
    def test_sin_sdk_error(self, mi):
        with pytest.raises(RuntimeError):
            sc.seleccionar_archivos_con_openai("q", ["a.py"], "groq", "m")

    @mock.patch("snapcontext._importar_openai", return_value=mock.Mock())
    @mock.patch.dict("os.environ", {}, clear=True)
    def test_falta_clave(self, mi):
        with pytest.raises(RuntimeError):
            sc.seleccionar_archivos_con_openai("q", ["a.py"], "groq", "m")

    def test_devuelve_normalizado(self):
        cliente_api = mock.Mock()
        contenido = mock.Mock()
        contenido.message.content = '{"archivos": ["a.py"]}'
        cliente_api.chat.completions.create.return_value.choices = [contenido]
        openai_mock = mock.Mock()
        openai_mock.OpenAI.return_value = cliente_api
        with (
            mock.patch("snapcontext._importar_openai", return_value=mock.Mock()),
            mock.patch.dict("os.environ", {"GROQ_API_KEY": "gk"}),
            mock.patch.object(sc, "_resolver_url_openai", return_value="https://x/"),
            mock.patch("snapcontext.openai", openai_mock),
        ):
            res = sc.seleccionar_archivos_con_openai(
                "lista a.py", ["a.py", "b.py"], "groq", "llama"
            )
        assert res == ["a.py"]

    def test_error_llamada(self):
        cliente_api = mock.Mock()
        cliente_api.chat.completions.create.side_effect = RuntimeError("red caida")
        openai_mock = mock.Mock()
        openai_mock.OpenAI.return_value = cliente_api
        with (
            mock.patch("snapcontext._importar_openai", return_value=mock.Mock()),
            mock.patch.dict("os.environ", {"GROQ_API_KEY": "gk"}),
            mock.patch.object(sc, "_resolver_url_openai", return_value="https://x/"),
            mock.patch("snapcontext.openai", openai_mock),
        ):
            with pytest.raises(RuntimeError):
                sc.seleccionar_archivos_con_openai("q", ["a.py"], "groq", "m")

    def test_respuesta_sin_choices(self):
        cliente_api = mock.Mock()
        cliente_api.chat.completions.create.return_value.choices = []
        openai_mock = mock.Mock()
        openai_mock.OpenAI.return_value = cliente_api
        with (
            mock.patch("snapcontext._importar_openai", return_value=mock.Mock()),
            mock.patch.dict("os.environ", {"GROQ_API_KEY": "gk"}),
            mock.patch.object(sc, "_resolver_url_openai", return_value="https://x/"),
            mock.patch("snapcontext.openai", openai_mock),
        ):
            res = sc.seleccionar_archivos_con_openai("q", ["a.py"], "groq", "m")
        assert res == []


class TestAsesorAplicarAutomaticas:
    def test_sin_sugerencias_auto(self, tmp_path):
        r = sc._asesor_aplicar_automaticas([{"archivo": "x", "auto": False}], str(tmp_path))
        assert r == 0

    def test_aplica_y_valida(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    pass\n")
        sugg = [
            {
                "archivo": "a.py",
                "auto": True,
                "linea": 1,
                "solucion": "cambia",
                "operaciones": [{"tipo": "renombrar", "nombre": "foo", "nuevo": "bar"}],
            }
        ]
        with (
            mock.patch.object(
                sc, "_aplicar_operaciones_ast", side_effect=lambda c, o: c.replace("foo", "bar")
            ),
            mock.patch.object(sc, "_validar_sintaxis", return_value=(True, "")),
        ):
            r = sc._asesor_aplicar_automaticas(sugg, str(tmp_path))
        assert r == 1
        assert "bar" in f.read_text()

    def test_validacion_falla_descarta(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x")
        sugg = [
            {
                "archivo": "a.py",
                "auto": True,
                "linea": 1,
                "solucion": "s",
                "operaciones": [{"tipo": "renombrar", "nombre": "x", "nuevo": "y"}],
            }
        ]
        with (
            mock.patch.object(sc, "_aplicar_operaciones_ast", return_value="nuevo"),
            mock.patch.object(sc, "_validar_sintaxis", return_value=(False, "err")),
        ):
            r = sc._asesor_aplicar_automaticas(sugg, str(tmp_path))
        assert r == 0

    def test_sin_cambio_neto(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x")
        sugg = [
            {
                "archivo": "a.py",
                "auto": True,
                "linea": 1,
                "solucion": "s",
                "operaciones": [{"tipo": "renombrar", "nombre": "x", "nuevo": "y"}],
            }
        ]
        with mock.patch.object(sc, "_aplicar_operaciones_ast", return_value=None):
            r = sc._asesor_aplicar_automaticas(sugg, str(tmp_path))
        assert r == 0
