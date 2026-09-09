"""Tests del nucleo de agentes (Fase 13)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

import agentes
import snapcontext as sc


class TestAgenteContexto:
    """AgenteContexto selecciona archivos relevantes para la consulta."""

    def test_directorio_vacio(self, tmp_path: Path):
        agente = agentes.AgenteContexto()
        assert isinstance(agente.escanear_candidatos("x", str(tmp_path)), list)

    def test_con_archivos(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("x")
        (tmp_path / "utils.py").write_text("def f(): pass")
        agente = agentes.AgenteContexto()
        with mock.patch("snapcontext._enviar_al_proveedor", return_value="[]"):
            r = agente.escanear_candidatos("test", str(tmp_path))
        assert isinstance(r, list)


class TestAgenteTester:
    """AgenteTester ejecuta y analiza pruebas."""

    def test_analizar_error_proc(self):
        proc = subprocess.CompletedProcess(["pytest"], 1, stdout="fail", stderr="")
        r = agentes.AgenteTester().analizar_error(proc)
        assert isinstance(r, str)
        assert len(r) > 0

    def test_analizar_error_string(self):
        r = agentes.AgenteTester().analizar_error("Error" + chr(10))
        assert isinstance(r, str)

    def test_recorta_largo(self):
        r = agentes.AgenteTester().analizar_error("x" * 10000)
        assert len(r) < 10000

    def test_limpia_ansi(self):
        r = agentes.AgenteTester().analizar_error("[31mError[0m")
        assert "" not in r


class TestAgenteEditor:
    """AgenteEditor coordina la edicion de archivos."""

    def test_instanciacion(self):
        """Se instancia sin argumentos."""
        agente = agentes.AgenteEditor()
        assert agente is not None

    def test_metodo_ejecutar_aider_existe(self):
        """Tiene el metodo ejecutar_aider."""
        agente = agentes.AgenteEditor()
        assert hasattr(agente, "ejecutar_aider")
        assert callable(agente.ejecutar_aider)
