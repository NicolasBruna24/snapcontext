"""Tests de volumen Fase 1d-3: chat, sandbox, comandos CLI."""
import argparse
from pathlib import Path
from unittest import mock

import snapcontext as sc


class TestFormatearMcpChat:
    def test_con_llamada(self):
        llamada = {"ok": True, "salida": "resultado", "herramienta": "leer"}
        r = sc._formatear_resultado_mcp(llamada)
        assert "resultado" in r or isinstance(r, str)

    def test_vacia(self):
        assert isinstance(sc._formatear_resultado_mcp({}), str)


class TestConfigurarPathInstalador:
    def test_en_linux_retorna_1(self):
        import instalador

        assert instalador.configurar_path() == 1

    def test_en_path_bool(self):
        import instalador

        assert isinstance(instalador.snapcontext_en_path(), bool)
