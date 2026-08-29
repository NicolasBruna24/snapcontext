#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del sistema multi-agente (v6.0.0) — multi_agent.py.

Cubre:
- Activación: flag ``--multi-agent`` y variable ``SNAPCONTEXT_MULTI_AGENT``.
- Buzón de mensajes entre agentes (``Buzon``).
- Arquitecto: genera un plan en JSON (y degrada si el LLM no responde JSON).
- Programador: aplica cambios con el editor propio.
- Tester: ejecuta pruebas (detección automática de v5.3.0).
- Supervisor: pipeline completo, bucle de realimentación, sin pruebas, aborto.
- Integración CLI: flag parseado, variable de entorno y enrutamiento en
  ``_ejecutar_modo_tarea``.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import multi_agent as ma        # noqa: E402
import snapcontext as sc        # noqa: E402
import agentes as ag            # noqa: E402
import detector_tests as det    # noqa: E402


def _plan():
    """Plan típico devuelto por el Arquitecto."""
    return {
        "objetivo": "añadir login",
        "descripcion": "Implementar autenticación básica",
        "modulos": ["auth"],
        "archivos": ["src/auth.py"],
        "pasos": [{"descripcion": "crear módulo", "accion": "editar",
                   "archivos": ["src/auth.py"]}],
        "dependencias": [],
    }


class BaseMulti(unittest.TestCase):
    """Directorio temporal limpio y sin variables de entorno de activación."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        os.environ.pop("SNAPCONTEXT_MULTI_AGENT", None)
        os.environ.pop("SNAPCONTEXT_COMANDO_TEST", None)
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        os.environ.pop("SNAPCONTEXT_MULTI_AGENT", None)
        os.environ.pop("SNAPCONTEXT_COMANDO_TEST", None)


class TestActivacion(BaseMulti):
    """multi_agent_activo: flag y variable de entorno."""

    def test_flag_true_y_false(self):
        self.assertTrue(ma.multi_agent_activo(True))
        self.assertFalse(ma.multi_agent_activo(False))

    def test_env_activa(self):
        os.environ["SNAPCONTEXT_MULTI_AGENT"] = "1"
        self.assertTrue(ma.multi_agent_activo(None))

    def test_flag_gana_sobre_entorno(self):
        os.environ["SNAPCONTEXT_MULTI_AGENT"] = "0"
        self.assertTrue(ma.multi_agent_activo(True))
        os.environ["SNAPCONTEXT_MULTI_AGENT"] = "1"
        self.assertFalse(ma.multi_agent_activo(False))

    def test_sin_flag_ni_env_desactivado(self):
        self.assertFalse(ma.multi_agent_activo(None))


class TestBuzon(BaseMulti):
    """Buzón de mensajes entre agentes."""

    def test_publica_y_recibe(self):
        buz = ma.Buzon()
        buz.publicar("arquitecto", "plan", {"objetivo": "x"})
        msg = buz.recibir()
        self.assertEqual(msg["remitente"], "arquitecto")
        self.assertEqual(msg["tipo"], "plan")
        self.assertEqual(msg["contenido"]["objetivo"], "x")

    def test_vacio_devuelve_none(self):
        buz = ma.Buzon()
        self.assertIsNone(buz.recibir())

    def test_historial_mantiene_mensajes(self):
        buz = ma.Buzon()
        buz.publicar("a", "plan", 1)
        buz.publicar("b", "resultado", 2)
        self.assertEqual(len(buz.historial()), 2)
        # recibir no borra el historial (solo la cola).
        buz.recibir()
        self.assertEqual(len(buz.historial()), 2)

    def test_vaciar_saca_pendientes(self):
        buz = ma.Buzon()
        buz.publicar("a", "plan", 1)
        buz.publicar("b", "resultado", 2)
        pendientes = buz.vaciar()
        self.assertEqual(len(pendientes), 2)
        self.assertIsNone(buz.recibir())


class TestArquitecto(BaseMulti):
    """El Arquitecto genera un plan en JSON con el LLM."""

    def test_genera_plan_json(self):
        plan_json = ('{"objetivo": "x", "modulos": ["m"], "archivos": ["a.py"],'
                     ' "pasos": [], "dependencias": []}')
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=plan_json):
            arqu = ma.Arquitecto(proveedor="ollama")
            plan = arqu.generar_plan("tarea", self.dir)
        self.assertEqual(plan["objetivo"], "x")
        self.assertEqual(plan["archivos"], ["a.py"])

    def test_plan_invalido_degenera(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="no es json"):
            arqu = ma.Arquitecto(proveedor="ollama")
            plan = arqu.generar_plan("mi tarea", self.dir)
        # Degradación ordenada: objetivo = tarea.
        self.assertEqual(plan["objetivo"], "mi tarea")
        self.assertIsInstance(plan["archivos"], list)

    def test_publica_plan_en_buzon(self):
        buz = ma.Buzon()
        plan_json = '{"objetivo": "x", "archivos": [], "pasos": [], "modulos": [], "dependencias": []}'
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=plan_json):
            arqu = ma.Arquitecto(proveedor="ollama", buzon=buz)
            arqu.generar_plan("t", self.dir)
        tipos = [m["tipo"] for m in buz.historial()]
        self.assertIn("plan", tipos)


class TestProgramador(BaseMulti):
    """El Programador aplica los cambios con el editor propio."""

    def _programador(self):
        return ma.Programador(proveedor="ollama")

    def test_implementa_ok(self):
        prog = self._programador()
        with mock.patch.object(ag.AgenteEditorPropio, "ejecutar",
                               return_value=True):
            res = prog.implementar("t", _plan(), ["src/auth.py"], self.dir)
        self.assertTrue(res["ok"])
        self.assertEqual(res["archivos"], ["src/auth.py"])

    def test_implementa_falla(self):
        prog = self._programador()
        with mock.patch.object(ag.AgenteEditorPropio, "ejecutar",
                               return_value=False):
            res = prog.implementar("t", _plan(), ["src/auth.py"], self.dir)
        self.assertFalse(res["ok"])

    def test_plan_sin_archivos_noop(self):
        prog = self._programador()
        res = prog.implementar("t", {"archivos": [], "objetivo": "x"}, [],
                               self.dir)
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("sin_archivos"))

    def test_publica_resultado_en_buzon(self):
        buz = ma.Buzon()
        prog = ma.Programador(proveedor="ollama", buzon=buz)
        with mock.patch.object(ag.AgenteEditorPropio, "ejecutar",
                               return_value=True):
            prog.implementar("t", _plan(), ["a.py"], self.dir)
        tipos = [m["tipo"] for m in buz.historial()]
        self.assertIn("resultado_edicion", tipos)

    def test_usa_archivos_del_plan_si_no_se_pasan(self):
        prog = self._programador()
        with mock.patch.object(ag.AgenteEditorPropio, "ejecutar",
                               return_value=True) as ejec:
            res = prog.implementar("t", _plan(), [], self.dir)
        self.assertTrue(res["ok"])
        # Los archivos se toman del plan (src/auth.py).
        self.assertIn("src/auth.py", ejec.call_args[0][0])


class TestTester(BaseMulti):
    """El Tester ejecuta pruebas con detección automática."""

    def test_ejecuta_pruebas_ok(self):
        tester = ma.Tester()
        with mock.patch.object(det, "resolver_comando_test",
                               return_value="pytest"), \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "OK", "")):
            res = tester.ejecutar(self.dir)
        self.assertTrue(res["ok"])
        self.assertEqual(res["comando"], "pytest")
        self.assertTrue(res["detectado"])

    def test_pruebas_fallan(self):
        tester = ma.Tester()
        with mock.patch.object(det, "resolver_comando_test",
                               return_value="pytest"), \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(1, "", "boom")):
            res = tester.ejecutar(self.dir)
        self.assertFalse(res["ok"])
        self.assertEqual(res["codigo"], 1)
        self.assertIn("boom", res["stderr"])

    def test_sin_comando_detectado(self):
        tester = ma.Tester()
        with mock.patch.object(det, "resolver_comando_test",
                               return_value=None):
            res = tester.ejecutar(self.dir)
        self.assertFalse(res["ok"])
        self.assertFalse(res["detectado"])

    def test_comando_explicito_gana(self):
        tester = ma.Tester()
        with mock.patch.object(det, "resolver_comando_test",
                               return_value="pytest") as resolver, \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "", "")) as ejec:
            res = tester.ejecutar(self.dir, comando="mi test")
        self.assertTrue(res["ok"])
        self.assertEqual(res["comando"], "mi test")
        resolver.assert_not_called()
        self.assertIn("mi test", ejec.call_args[0][0])

    def test_env_comando_test(self):
        os.environ["SNAPCONTEXT_COMANDO_TEST"] = "caracola"
        tester = ma.Tester()
        with mock.patch.object(det, "resolver_comando_test",
                               return_value=None) as resolver, \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "", "")):
            res = tester.ejecutar(self.dir)
        self.assertTrue(res["ok"])
        resolver.assert_not_called()


class TestSupervisor(BaseMulti):
    """El Supervisor orquesta el pipeline con realimentación."""

    def _sup(self, **kw):
        kw.setdefault("auto", True)
        kw.setdefault("max_reintentos", 3)
        return ma.Supervisor(directorio=self.dir, tarea="tarea", **kw)

    def test_flujo_completo_ok(self):
        sup = self._sup()
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               return_value=_plan()), \
             mock.patch.object(ma.Programador, "implementar",
                               return_value={"ok": True}), \
             mock.patch.object(ma.Tester, "ejecutar",
                               return_value={"ok": True, "detectado": True,
                                             "codigo": 0}):
            res = sup.ejecutar()
        self.assertTrue(res["ok"])
        self.assertEqual(res["reintentos"], 1)
        self.assertEqual(res["plan"]["objetivo"], "añadir login")

    def test_realimentacion_hasta_verde(self):
        sup = self._sup()
        pruebas = iter([
            {"ok": False, "detectado": True, "codigo": 1, "stderr": "boom"},
            {"ok": True, "detectado": True, "codigo": 0},
        ])
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               return_value=_plan()), \
             mock.patch.object(ma.Programador, "implementar",
                               return_value={"ok": True}) as imp, \
             mock.patch.object(ma.Tester, "ejecutar",
                               side_effect=lambda *a, **k: next(pruebas)):
            res = sup.ejecutar()
        self.assertTrue(res["ok"])
        self.assertEqual(res["reintentos"], 2)
        self.assertEqual(imp.call_count, 2)

    def test_sin_pruebas_se_da_por_terminado(self):
        sup = self._sup()
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               return_value=_plan()), \
             mock.patch.object(ma.Programador, "implementar",
                               return_value={"ok": True}), \
             mock.patch.object(ma.Tester, "ejecutar",
                               return_value={"ok": False, "detectado": False,
                                             "codigo": -1}):
            res = sup.ejecutar()
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("sin_pruebas"))

    def test_agota_reintentos(self):
        sup = self._sup()
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               return_value=_plan()), \
             mock.patch.object(ma.Programador, "implementar",
                               return_value={"ok": True}), \
             mock.patch.object(ma.Tester, "ejecutar",
                               return_value={"ok": False, "detectado": True,
                                             "codigo": 1, "stderr": "x"}):
            res = sup.ejecutar()
        self.assertFalse(res["ok"])
        self.assertEqual(res["reintentos"], 3)
        testers = [r for r in res["resultados"] if r["fase"] == "tester"]
        self.assertEqual(len(testers), 3)

    def test_arquitecto_falla_aborta(self):
        sup = self._sup()
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               side_effect=RuntimeError("boom de llm")):
            res = sup.ejecutar()
        self.assertFalse(res["ok"])
        self.assertIn("boom", res["error"])

    def test_programador_falla_no_llama_tester(self):
        sup = self._sup()
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               return_value=_plan()) as arq, \
             mock.patch.object(ma.Programador, "implementar",
                               return_value={"ok": False, "error": "no edit"}):
            res = sup.ejecutar()
        self.assertFalse(res["ok"])
        arq.assert_called_once()

    def test_interactivo_cancelacion(self):
        sup = self._sup(auto=False)
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               return_value=_plan()), \
             mock.patch.object(ma.Supervisor, "_confirmar_plan",
                               return_value=False):
            res = sup.ejecutar()
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "cancelado por el usuario")

    def test_auto_no_pregunta(self):
        sup = self._sup(auto=True)
        with mock.patch.object(ma.Arquitecto, "generar_plan",
                               return_value=_plan()), \
             mock.patch.object(ma.Programador, "implementar",
                               return_value={"ok": True}), \
             mock.patch.object(ma.Tester, "ejecutar",
                               return_value={"ok": True, "detectado": True,
                                             "codigo": 0}), \
             mock.patch("ui.preguntar_interactivo",
                        side_effect=AssertionError("no debe preguntar")):
            res = sup.ejecutar()
        self.assertTrue(res["ok"])


class TestIntegracionCli(BaseMulti):
    """Flag --multi-agent, variable de entorno y enrutamiento."""

    def test_flag_cli_parseado(self):
        args = sc.crear_parser().parse_args(["--multi-agent", "consulta"])
        self.assertTrue(args.multi_agent)
        args2 = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(args2.multi_agent)

    def test_env_activa_router(self):
        os.environ["SNAPCONTEXT_MULTI_AGENT"] = "1"
        self.assertTrue(sc._multi_agent_activo(None))

    def test_routing_multi_agent_por_flag(self):
        args = sc.crear_parser().parse_args(["--multi-agent", "task"])
        with mock.patch.object(sc, "_ejecutar_multi_agent") as multi, \
             mock.patch.object(sc, "_ejecutar_react") as react:
            multi.return_value = 0
            sc._ejecutar_modo_tarea(args)
        multi.assert_called_once_with(args)
        react.assert_not_called()

    def test_routing_multi_agent_por_env(self):
        os.environ["SNAPCONTEXT_MULTI_AGENT"] = "1"
        args = sc.crear_parser().parse_args(["task"])
        with mock.patch.object(sc, "_ejecutar_multi_agent") as multi, \
             mock.patch.object(sc, "_ejecutar_react") as react:
            multi.return_value = 0
            sc._ejecutar_modo_tarea(args)
        multi.assert_called_once_with(args)
        react.assert_not_called()

    def test_routing_sin_flag_ni_env_usa_react(self):
        args = sc.crear_parser().parse_args(["task"])
        with mock.patch.object(sc, "_ejecutar_multi_agent") as multi, \
             mock.patch.object(sc, "_ejecutar_react") as react:
            react.return_value = 0
            sc._ejecutar_modo_tarea(args)
        react.assert_called_once_with(args)
        multi.assert_not_called()

    def test_routing_plan_gana_a_env_legacy(self):
        # --plan es legacy explícito y tiene prioridad sobre multi-agente.
        os.environ["SNAPCONTEXT_MULTI_AGENT"] = "1"
        args = sc.crear_parser().parse_args(["--plan", "task"])
        with mock.patch.object(sc, "_ejecutar_planificador") as plan, \
             mock.patch.object(sc, "_ejecutar_multi_agent") as multi:
            plan.return_value = 0
            sc._ejecutar_modo_tarea(args)
        plan.assert_called_once_with(args)
        multi.assert_not_called()

    def test_ejecutar_multi_agent_sin_consulta_devuelve_error(self):
        args = sc.crear_parser().parse_args(["--multi-agent"])
        self.assertEqual(sc._ejecutar_multi_agent(args), 1)


class TestVersionPackaging(BaseMulti):
    """Versionado y packaging del módulo."""

    def test_version_600(self):
        self.assertEqual(sc.VERSION, "6.0.0")

    def test_pyproject_incluye_multi_agent(self):
        with open(ROOT / "pyproject.toml", encoding="utf-8") as fh:
            texto = fh.read()
        self.assertIn("multi_agent", texto)
        self.assertIn('version = "6.0.0"', texto)

    def test_funciones_publicas_disponibles(self):
        for clase in ("Supervisor", "Arquitecto", "Programador", "Tester",
                      "Buzon"):
            self.assertTrue(hasattr(ma, clase), clase)
        self.assertTrue(callable(ma.multi_agent_activo))


if __name__ == "__main__":
    unittest.main()