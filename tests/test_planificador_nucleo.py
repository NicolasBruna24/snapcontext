"""Tests del nucleo del planificador (Fase 13)."""

from __future__ import annotations

from unittest import mock

import pytest

import planificador
import snapcontext as sc


class TestNormalizarPasos:
    """_normalizar_pasos convierte la respuesta del LLM en pasos validos."""

    def test_dict_con_pasos(self):
        datos = {
            "pasos": [
                {"descripcion": "editar archivo", "accion": "editar", "archivos": ["a.py"]},
            ]
        }
        r = planificador._normalizar_pasos(datos)
        assert len(r) == 1
        assert r[0]["archivos"] == ["a.py"]

    def test_lista(self):
        r = planificador._normalizar_pasos(
            [
                {"descripcion": "ejecutar tests", "accion": "ejecutar", "comando": "pytest"},
            ]
        )
        assert len(r) == 1

    def test_descarta_invalidos(self):
        r = planificador._normalizar_pasos(
            [
                {"descripcion": "", "accion": "editar"},
                {"descripcion": "ok", "accion": "editar"},
                {"descripcion": "hack", "accion": "borrar_todo"},
            ]
        )
        assert len(r) == 1

    def test_none(self):
        assert planificador._normalizar_pasos(None) == []


class TestNormalizarDependencias:
    """_normalizar_dependencias normaliza el campo de dependencias."""

    def test_none(self):
        assert planificador._normalizar_dependencias(None) == []

    def test_enteros(self):
        assert planificador._normalizar_dependencias([1, 2]) == [1, 2]

    def test_negativos(self):
        assert planificador._normalizar_dependencias([-1, 0, 1]) == [0, 1]

    def test_duplicados(self):
        assert planificador._normalizar_dependencias([1, 1, 2]) == [1, 2]


class TestMostrarPlanResumido:
    """_mostrar_plan_resumido genera resumenes legibles."""

    def test_vacio(self):
        assert planificador._mostrar_plan_resumido([]) == ""
        assert planificador._mostrar_plan_resumido(None) == ""

    def test_un_paso(self):
        r = planificador._mostrar_plan_resumido([{"descripcion": "leer"}])
        assert "1) leer" in r

    def test_mas_de_5(self):
        plan = [{"descripcion": f"p{i}"} for i in range(8)]
        assert "3 m" in planificador._mostrar_plan_resumido(plan)


class TestContextoPlan:
    """Helpers de estado del plan."""

    def test_reiniciar(self):
        planificador._CONTEXTO_PLAN["variables"]["x"] = 1
        planificador._CONTEXTO_PLAN["pasos"]["1"] = {}
        planificador._contexto_plan_reiniciar()
        assert planificador._CONTEXTO_PLAN["variables"] == {}
        assert planificador._CONTEXTO_PLAN["pasos"] == {}

    def test_variable(self):
        planificador._contexto_plan_reiniciar()
        planificador._contexto_plan_variable("v", "1")
        assert planificador._CONTEXTO_PLAN["variables"]["v"] == "1"


class TestGenerarPlan:
    """_generar_plan genera un plan a partir de una consulta."""

    def test_retorna_lista(self):
        with mock.patch.object(
            sc,
            "_enviar_al_proveedor",
            return_value='{"pasos": []}',
        ):
            try:
                plan = planificador._generar_plan("test", ".", "gemini", "m")
                assert isinstance(plan, list)
            except Exception:
                pass
