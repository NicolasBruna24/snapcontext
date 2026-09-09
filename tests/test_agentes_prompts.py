"""Tests de helpers puros de agentes.py — Fase 1 de cobertura.

Cubre heurísticas de tarea y construcción de prompts de edición sin invocar
proveedores ni herramientas externas. Las funciones que resuelven símbolos de
``snapcontext`` a tiempo de llamada se aislan con mocks.
"""

from __future__ import annotations

from unittest import mock

import agentes


class TestTareaEstructura:
    def test_vacia_devuelve_false(self):
        assert agentes._tarea_estructura("") is False
        assert agentes._tarea_estructura(None) is False

    def test_detecta_refactorizacion(self):
        assert agentes._tarea_estructura("refactoriza el login") is True
        assert agentes._tarea_estructura("extrae esa funcion") is True
        assert agentes._tarea_estructura("renombra la variable") is True

    def test_tolerancia_acentos(self):
        assert agentes._tarea_estructura("crear función nueva") is True

    def test_tarea_normal_false(self):
        assert agentes._tarea_estructura("corrige el mensaje de error") is False


class TestProveedorEfectivo:
    def test_none_devuelve_por_defecto(self):
        assert agentes._proveedor_efectivo(None) == "gemini"

    def test_valor_devuelto(self):
        assert agentes._proveedor_efectivo("openai") == "openai"


class TestPromptsConcisos:
    def test_ollama_devuelve_true(self):
        assert agentes._prompts_concisos("ollama") is True

    def test_modelo_ligero_fuerza_true(self):
        assert agentes._prompts_concisos(None, modelo_ligero=True) is True

    def test_proveedor_normal_sin_ligero_false(self):
        assert agentes._prompts_concisos("anthropic", modelo_ligero=False) is False


class TestConstruirPromptEdicion:
    def test_modo_parche_normal(self):
        prompt, truncado = agentes._construir_prompt_edicion(
            "parche", "tarea", "a.py", "x = 1\n", "python", conciso=False
        )
        assert truncado is False
        assert "tarea" in prompt
        assert "a.py" in prompt
        assert "diff" in prompt.lower()

    def test_modo_parche_conciso(self):
        prompt, truncado = agentes._construir_prompt_edicion(
            "parche", "tarea", "a.py", "x = 1\n", "python", conciso=True
        )
        assert truncado is False

    def test_modo_sobrescribir(self):
        prompt, truncado = agentes._construir_prompt_edicion(
            "sobrescribir", "tarea", "a.py", "x = 1\n", "python", conciso=False
        )
        assert truncado is False

    def test_truncado_parche_con_mock_contexto(self):
        with mock.patch("snapcontext._extraer_contexto_selectivo", return_value="ctx") as ctx:
            prompt, truncado = agentes._construir_prompt_edicion(
                "parche", "tarea", "a.py", "x = 1\n", "python", conciso=False, truncado=True
            )
        ctx.assert_called_once()
        assert truncado is True

    def test_error_anadido_al_prompt(self):
        prompt, _ = agentes._construir_prompt_edicion(
            "parche", "tarea", "a.py", "x\n", "python", conciso=False,
            error_msj="sintaxis invalida",
        )
        assert "sintaxis invalida" in prompt

    def test_truncado_sobrescribir_sin_contexto_exige_mock(self):
        with mock.patch("snapcontext._extraer_contexto_selectivo", return_value="ctx"):
            _, truncado = agentes._construir_prompt_edicion(
                "sobrescribir", "t", "a.py", "x\n", "py", conciso=False, truncado=True
            )
        assert truncado is True
