"""Tests presentacion."""

import io
from unittest import mock

import presentacion as pres


class TestPresentacion:
    def test_texto(self):
        assert pres._texto_seguro("x") == "x"
        assert pres._texto_seguro(None) in ("", None)

    def test_emitir(self):
        b = io.StringIO()
        pres._emitir(b, "h")
        assert "h" in b.getvalue()

    def test_pintar(self):
        assert "x" in pres._pintar("x", "verde")
        with mock.patch.object(pres, "_colores_activos", return_value=False):
            assert pres._pintar("x", "rojo") == "x"

    def test_msgs(self, capsys):
        pres.info("i")
        pres.exito("e")
        pres.aviso("a")
        pres.error("r")

    def test_depurar(self, capsys):
        pres.depurar("d")
        assert capsys.readouterr().out == ""

    def test_cb(self):
        pres.fijar_evento_callback(None)
        assert pres.EVENTO_CALLBACK is None
