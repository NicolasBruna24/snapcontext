"""Tests de las funciones puras del planificador (contexto y condiciones).

Fase 1 de cobertura: normalización de pasos/dependencias, evaluación de
condiciones, resolución de marcadores y utilidades de contexto. No invoca
proveedores ni comandos reales (se mockea la fachada ``snapcontext``).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

import planificador
from planificador import (
    _DESCONOCIDO,
    _contexto_plan_reiniciar,
    _contexto_plan_variable,
    _evaluar_condicion,
    _mostrar_plan_resumido,
    _normalizar_comparacion,
    _normalizar_dependencias,
    _normalizar_pasos,
    _partir_argumentos,
    _refs_de_condicion,
    _registrar_resultado_plan,
    _resolver_marcadores,
    _resolver_marcadores_args,
    _resolver_operando_condicion,
)


def _resetear_contexto():
    _contexto_plan_reiniciar()


class TestContextoPlan:
    def test_reiniciar_limpia(self):
        _contexto_plan_variable("clave", "valor")
        _registrar_resultado_plan(1, True, "ok")
        _contexto_plan_reiniciar()
        assert planificador._CONTEXTO_PLAN["variables"] == {}
        assert planificador._CONTEXTO_PLAN["pasos"] == {}

    def test_variable_guarda_y_actualiza_resultado(self):
        _resetear_contexto()
        _contexto_plan_variable("mi_var", 42)
        assert planificador._CONTEXTO_PLAN["variables"]["mi_var"] == 42
        assert planificador._CONTEXTO_PLAN["variables"]["resultado"] == 42

    def test_variable_vacia_ignorada(self):
        _resetear_contexto()
        _contexto_plan_variable("", "x")
        assert planificador._CONTEXTO_PLAN["variables"] == {}

    def test_registrar_resultado_ok_y_fallo(self):
        _resetear_contexto()
        _registrar_resultado_plan(2, True, "detalle ok")
        _registrar_resultado_plan(3, False, "detalle fallo")
        assert planificador._CONTEXTO_PLAN["pasos"]["2"]["resultado"] == "ok"
        assert planificador._CONTEXTO_PLAN["pasos"]["2"]["ok"] is True
        assert planificador._CONTEXTO_PLAN["pasos"]["3"]["resultado"] == "fallo"
        assert planificador._CONTEXTO_PLAN["pasos"]["3"]["ok"] is False


class TestMostrarPlanResumido:
    def test_plan_vacio(self):
        assert _mostrar_plan_resumido(None) == ""
        assert _mostrar_plan_resumido([]) == ""

    def test_pasos_con_dict_y_string(self):
        plan = [
            {"descripcion": "leer login"},
            {"comando": "pytest"},
            "paso suelto",
        ]
        resumen = _mostrar_plan_resumido(plan)
        assert resumen.startswith("Voy a:")
        assert "1) leer login" in resumen
        assert "2) pytest" in resumen

    def test_mas_de_cinco_pasos_anade_resto(self):
        plan = [{"descripcion": f"pasa {i}"} for i in range(8)]
        resumen = _mostrar_plan_resumido(plan)
        assert "3 más" in resumen


class TestNormalizarPasos:
    def test_dict_con_pasos(self):
        datos = {"pasos": [{"descripcion": "a", "accion": "editar", "archivos": ["x.py"]}]}
        pasos = _normalizar_pasos(datos)
        assert len(pasos) == 1
        assert pasos[0]["accion"] == "editar"

    def test_descarta_paso_sin_descripcion_o_accion_invalida(self):
        datos = [
            {"descripcion": "", "accion": "editar"},
            {"descripcion": "ok", "accion": "no_existe"},
            {"descripcion": "valido", "accion": "consultar"},
        ]
        pasos = _normalizar_pasos(datos)
        assert len(pasos) == 1
        assert pasos[0]["descripcion"] == "valido"

    def test_lista_o_unico_paso(self):
        solo = _normalizar_pasos({"descripcion": "unico", "accion": "ejecutar"})
        assert solo == []
        assert _normalizar_pasos("no-lista") == []

    def test_no_dict_ignorado(self):
        resultado = _normalizar_pasos([{"descripcion": "a", "accion": "editar"}, 42])
        assert len(resultado) == 1

    def test_archivos_no_lista_se_normalizan(self):
        pasos = _normalizar_pasos(
            {"pasos": [{"descripcion": "a", "accion": "editar", "archivos": "x.py"}]}
        )
        assert pasos[0]["archivos"] == []


class TestNormalizarDependencias:
    def test_none_o_vacio(self):
        assert _normalizar_dependencias(None) == []
        assert _normalizar_dependencias("") == []

    def test_valores_mezclados(self):
        assert _normalizar_dependencias([1, "2", "abc", -1, 3.0]) == [1, 2, 3]

    def test_valor_unico(self):
        assert _normalizar_dependencias("4") == [4]


class TestNormalizarComparacion:
    def test_bool_y_numeros(self):
        assert _normalizar_comparacion(True) == "ok"
        assert _normalizar_comparacion(False) == "fallo"
        assert _normalizar_comparacion(5) == "5"

    def test_dict_lista(self):
        assert _normalizar_comparacion({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
        assert _normalizar_comparacion([1, 2]) == "[1, 2]"

    def test_otro(self):
        assert _normalizar_comparacion("texto") == "texto"


class TestPartirArgumentos:
    def test_argumentos_simples(self):
        assert _partir_argumentos("a, b, c") == ["a", "b", "c"]

    def test_respeta_comillas(self):
        assert _partir_argumentos("'a, b', 'c'") == ["a, b", "c"]

    def test_comillas_sin_cerrar_lanza(self):
        with pytest.raises(ValueError):
            _partir_argumentos("'a")


class TestResolverMarcadores:
    def test_no_es_string_devuelve_tal_cual(self):
        assert _resolver_marcadores(123) == 123
        assert _resolver_marcadores("sin llaves") == "sin llaves"

    def test_sustituye_variable(self):
        _resetear_contexto()
        _contexto_plan_variable("clave", "valor")
        assert _resolver_marcadores("hola {{clave}}") == "hola valor"

    def test_clave_desconocida_se_deja(self):
        _resetear_contexto()
        assert _resolver_marcadores("hola {{no_existe}}") == "hola {{no_existe}}"

    def test_valor_dict_se_serializa(self):
        _resetear_contexto()
        _contexto_plan_variable("obj", {"k": 1})
        assert _resolver_marcadores("{{obj}}") == '{"k": 1}'


class TestResolverMarcadoresArgs:
    def test_aplica_a_strings_y_listas(self):
        _resetear_contexto()
        _contexto_plan_variable("v", "9")
        args = {"a": "x-{{v}}", "b": ["p-{{v}}"], "c": 5, "d": None}
        resuelto = _resolver_marcadores_args(args)
        assert resuelto["a"] == "x-9"
        assert resuelto["b"] == ["p-9"]
        assert resuelto["c"] == 5
        assert resuelto["d"] is None

    def test_vacio(self):
        assert _resolver_marcadores_args({}) == {}
        assert _resolver_marcadores_args(None) == {}


class TestResolverOperandoCondicion:
    def test_literales(self):
        assert _resolver_operando_condicion("'hola'", {}) == "hola"
        assert _resolver_operando_condicion('"hola"', {}) == "hola"
        assert _resolver_operando_condicion("true", {}) is True
        assert _resolver_operando_condicion("false", {}) is False
        assert _resolver_operando_condicion("null", {}) is None
        assert _resolver_operando_condicion("42", {}) == 42
        assert _resolver_operando_condicion("4.5", {}) == 4.5

    def test_referencia_variable_contexto(self):
        _resetear_contexto()
        _contexto_plan_variable("mi_var", "listo")
        contexto = {"variables": planificador._CONTEXTO_PLAN["variables"]}
        assert _resolver_operando_condicion("mi_var", contexto) == "listo"
        assert _resolver_operando_condicion("desconocida", contexto) is _DESCONOCIDO

    def test_paso_ctx(self):
        _resetear_contexto()
        planificador._CONTEXTO_PLAN["pasos"]["1"] = {"resultado": "ok", "ok": True}
        contexto = {"pasos": planificador._CONTEXTO_PLAN["pasos"]}
        assert _resolver_operando_condicion("pasos[1].resultado", contexto) == "ok"
        assert _resolver_operando_condicion("pasos[1].inexistente", contexto) is _DESCONOCIDO

    def test_otro_desconocido(self):
        assert _resolver_operando_condicion("a b c", {}) is _DESCONOCIDO


class TestEvaluarCondicion:
    def test_condicion_vacia_true(self):
        assert _evaluar_condicion("") is True
        assert _evaluar_condicion(None) is True

    def test_archivo_existe(self, tmp_path: Path):
        (tmp_path / "x.py").write_text("x")
        assert _evaluar_condicion("archivo_existe('x.py')", raiz=str(tmp_path)) is True
        assert _evaluar_condicion("archivo_existe('no.py')", raiz=str(tmp_path)) is False

    def test_variable_existe(self):
        _resetear_contexto()
        _contexto_plan_variable("mi_var", 1)
        contexto = {"variables": planificador._CONTEXTO_PLAN["variables"]}
        assert _evaluar_condicion("variable_existe('mi_var')", contexto=contexto) is True
        assert _evaluar_condicion("variable_existe('otra')", contexto=contexto) is False

    def test_mal_formada_devuelve_false(self):
        with mock.patch("snapcontext.aviso"):
            assert _evaluar_condicion("esto no es valida", ".", {}) is False

    def test_comparacion_dinamica(self):
        contexto = {"variables": {"a": 1}}
        with mock.patch("snapcontext.aviso"):
            assert _evaluar_condicion("a == 1", ".", contexto) is True
            assert _evaluar_condicion("a != 1", ".", contexto) is False


class TestRefsDeCondicion:
    def test_extrae_indices_y_variables(self):
        indices, nombres = _refs_de_condicion("pasos[1].resultado == 'ok' && resultados.x != ''")
        assert 0 in indices
        assert "x" in nombres
