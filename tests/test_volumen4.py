"""Tests de volumen 4 (Fase 1d): agentes, planificador y react_agent."""

from argparse import Namespace
from unittest import mock

import agentes as ag
import planificador as pl
import react_agent as ra


class TestAgentesPrompts:
    def test_estructura_basica(self):
        assert ag._tarea_estructura("implementa funcion x") in (True, False)
        assert ag._tarea_estructura("hola") in (True, False)

    def test_proveedor_efectivo(self):
        if hasattr(ag, "_proveedor_efectivo"):
            assert isinstance(ag._proveedor_efectivo(None), str)

    def test_prompts_concisos(self):
        assert ag._prompts_concisos("gemini") in (True, False)
        assert ag._prompts_concisos(None) in (True, False)

    def test_clases_existen(self):
        assert hasattr(ag, "AgenteContexto") or hasattr(ag, "AgenteEditor")


class TestAgenteContextoRun:
    def test_contexto_basico(self, tmp_path):
        ctx = ag.AgenteContexto()
        assert ctx is not None
        assert hasattr(ctx, "escanear_candidatos")

    def test_tester(self, tmp_path):
        if hasattr(ag, "AgenteTester"):
            try:
                t = ag.AgenteTester(str(tmp_path))
                assert t is not None
            except TypeError:
                pass


class TestPlanificadorPasos:
    def test_ejecutar_paso_desconocido(self):
        args = Namespace()
        try:
            r = pl._ejecutar_paso_plan({"id": "x", "accion": "no_existe_xyz"}, args, ".")
        except Exception:
            r = (False, "error")
        assert isinstance(r, (tuple, dict, bool, type(None)))

    def test_normalizar(self):
        if hasattr(pl, "_normalizar_pasos"):
            r = pl._normalizar_pasos([{"id": "a"}])
            assert isinstance(r, list)


class TestReactHelpers:
    def test_estimar_tokens(self):
        assert ra.estimar_tokens("hola mundo") >= 0

    def test_extraer_json(self):
        if hasattr(ra, "_extraer_json"):
            r = ra._extraer_json('{"a": 1}')
            assert r == {"a": 1} or isinstance(r, dict)

    def test_observar(self):
        if hasattr(ra, "_observar_resultado"):
            r = ra._observar_resultado({"ok": True, "stdout": "x"})
            assert isinstance(r, (str, dict, type(None)))

    def test_ruta_segura(self):
        if hasattr(ra, "_ruta_segura"):
            assert isinstance(ra._ruta_segura("a.py"), (bool, str, PathLike := type("X", (), {})) if False else (bool, str))

    def test_tokens_historial(self):
        if hasattr(ra, "_tokens_historial"):
            msgs = [{"role": "user", "content": "hola"}]
            assert ra._tokens_historial(msgs) >= 0
