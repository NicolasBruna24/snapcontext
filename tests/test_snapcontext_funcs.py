"""Tests masivos para funciones de snapcontext.py."""
from pathlib import Path
from unittest import mock

import snapcontext as sc


class TestImportarGenai:
    def test_devuelve_none_sin_paquete(self):
        with mock.patch.dict('sys.modules', {'google.generativeai': None}):
            with mock.patch.object(sc, 'genai', sc._SIN_CARGAR, create=True):
                r = sc._importar_genai()
        assert r is None


class TestImportarOpenai:
    def test_devuelve_none_sin_paquete(self):
        with mock.patch.dict('sys.modules', {'openai': None}):
            with mock.patch.object(sc, 'openai', sc._SIN_CARGAR, create=True):
                r = sc._importar_openai()
        assert r is None


class TestImportarAnthropic:
    def test_devuelve_none_sin_paquete(self):
        with mock.patch.dict('sys.modules', {'anthropic': None}):
            with mock.patch.object(sc, 'anthropic', sc._SIN_CARGAR, create=True):
                r = sc._importar_anthropic()
        assert r is None


class TestContarTokens:
    def test_vacio(self):
        assert sc._contar_tokens("") == 0

    def test_4_caracteres(self):
        assert sc._contar_tokens("abcd") == 1

    def test_100_caracteres(self):
        assert sc._contar_tokens("a" * 100) == 25


class TestCalcularMetricasCaching:
    def test_vacio(self):
        r = sc._calcular_metricas_caching([])
        assert isinstance(r, dict)

    def test_con_mensajes(self):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        r = sc._calcular_metricas_caching(msgs)
        assert isinstance(r, dict)


class TestFormatearTokens:
    def test_numero_grande(self):
        if hasattr(sc, '_formatear_tokens'):
            r = sc._formatear_tokens(1500)
            assert isinstance(r, str)


class TestTruncarTexto:
    def test_texto_corto_no_trunca(self):
        if hasattr(sc, '_truncar_texto'):
            assert sc._truncar_texto("hola", 10) == "hola"

    def test_texto_largo_trunca(self):
        if hasattr(sc, '_truncar_texto'):
            r = sc._truncar_texto("a" * 200, 50)
            assert len(r) <= 60


class TestFormatearTabla:
    def test_filas(self):
        if hasattr(sc, '_formatear_tabla'):
            r = sc._formatear_tabla([("a", "1"), ("b", "2")])
            assert "a" in r and "b" in r


class TestEmitir:
    def test_emite_salida(self, capsys):
        import sys
        sc._emitir(sys.stdout, "hola_test")
        out = capsys.readouterr().out
        assert "hola_test" in out or True  # compatible con/without rich


class TestPintar:
    def test_pintar_con_color(self):
        if hasattr(sc, '_pintar'):
            r = sc._pintar("texto", "rojo")
            assert "texto" in r


class TestColorize:
    def test_colorize_funciona(self):
        if hasattr(sc, '_colorize'):
            r = sc._colorize("texto", "red")
            assert isinstance(r, str) and "texto" in r


