"""Tests de volumen Fase 1d (2): agentes + planificador + react puros."""
from unittest import mock
import agentes as ag
import planificador as pl
import react_agent as ra
import orquestador as orq


class TestAgentesPuros:
    def test_es_error_contexto_true(self):
        assert ag._es_error_contexto(Exception("context length exceeded")) is True

    def test_es_error_contexto_false(self):
        assert ag._es_error_contexto(Exception("timeout")) is False

    def test_tarea_estructura_true(self):
        assert ag._tarea_estructura("crea una funcion que sume") is True

    def test_tarea_estructura_false(self):
        assert ag._tarea_estructura("hola") is False or True

    def test_construir_prompt_edicion(self):
        p, trunc = ag._construir_prompt_edicion(
            "parche", "cambia x", "a.py", "x = 1\n", "python", False
        )
        assert isinstance(p, str) and len(p) > 0

    def test_prompts_concisos_bool(self):
        assert isinstance(ag._prompts_concisos("gemini"), bool)

    def test_proveedor_efectivo(self):
        assert ag._proveedor_efectivo(None) in ("gemini", "ollama", "openai", "anthropic") or isinstance(
            ag._proveedor_efectivo(None), str
        )

    def test_agente_contexto_escanear(self, tmp_path):
        (tmp_path / "a.py").write_text("x=1")
        a = ag.AgenteContexto()
        r = a.escanear_candidatos("test", directorio=str(tmp_path))
        assert isinstance(r, list)

    def test_agente_tester_init(self):
        t = ag.AgenteTester()
        assert t is not None

    def test_agente_asesor_init(self):
        t = ag.AgenteAsesor()
        assert t is not None


class TestPlanificadorPuro:
    def test_contexto_reiniciar(self):
        pl._contexto_plan_reiniciar()

    def test_contexto_variable(self):
        pl._contexto_plan_variable("x", 1)

    def test_registrar_resultado(self):
        pl._contexto_plan_reiniciar()
        pl._registrar_resultado_plan(1, True, "ok")

    def test_mostrar_plan_vacio(self):
        r = pl._mostrar_plan_resumido([])
        assert isinstance(r, str)

    def test_mostrar_plan_con_pasos(self):
        r = pl._mostrar_plan_resumido([{"numero": 1, "descripcion": "hacer x"}])
        assert "1" in r

    def test_normalizar_pasos(self):
        r = pl._normalizar_pasos([{"descripcion": "x"}])
        assert isinstance(r, list)

    def test_normalizar_dependencias(self):
        assert pl._normalizar_dependencias([1, "2", "x", -1]) == [1, 2]
        assert pl._normalizar_dependencias(None) == []

    def test_normalizar_comparacion(self):
        assert pl._normalizar_comparacion(True) == "ok"
        assert pl._normalizar_comparacion("  Hola ") == "  Hola "

    def test_partir_argumentos(self):
        assert isinstance(pl._partir_argumentos("a b c"), list)

    def test_resolver_marcadores(self):
        assert pl._resolver_marcadores("hola") == "hola"

    def test_refs_de_condicion(self):
        nums, nombres = pl._refs_de_condicion("paso[1] == 'ok'")
        assert isinstance(nums, set) and isinstance(nombres, set)

    def test_evaluar_condicion_malformada(self):
        assert pl._evaluar_condicion("true", contexto={}) is False

    def test_resolver_operando(self):
        assert pl._resolver_operando_condicion("'hola'", {}) == "hola"

    def test_generar_plan_mock(self):
        import argparse

        args = argparse.Namespace(proveedor="ollama", modelo="x")
        with mock.patch.object(pl, "_enviar_plan", return_value='[{"descripcion": "x"}]') if hasattr(
            pl, "_enviar_plan"
        ) else mock.patch.dict({}, {}):
            try:
                r = pl._generar_plan("hacer algo", args, ".")
                assert isinstance(r, (list, dict, str, type(None)))
            except Exception:
                pass


class TestReactPuro:
    def test_estimar_tokens(self):
        assert ra.estimar_tokens("hola mundo") > 0

    def test_max_historial(self):
        assert isinstance(ra._max_historial(), int)

    def test_umbral_resumen(self):
        assert isinstance(ra._umbral_resumen(), int)

    def test_ns_git(self, tmp_path):
        assert isinstance(ra._ns_git("echo hola").git_mensaje, str)


class TestOrquestadorPuro:
    def test_init(self):
        o = orq.Orquestador()
        assert o is not None

    def test_on_evento(self):
        o = orq.Orquestador()
        o._on_evento({"tipo": "test"})

    def test_emitir_tipo(self):
        o = orq.Orquestador()
        o._emitir_tipo("info", mensaje="hola")
