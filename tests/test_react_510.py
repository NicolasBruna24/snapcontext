#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v5.1.0: motor ReAct (razonamiento dinámico)."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc          # noqa: E402
import react_agent as ra          # noqa: E402


def _dec(pensamiento="", accion="finalizar", argumentos=None):
    return {"pensamiento": pensamiento, "accion": accion,
            "argumentos": argumentos or {}}


class BaseReact510(unittest.TestCase):
    """Aísla CONFIG_DIR/DB_PATH y crea un directorio temporal de trabajo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = self.tmp.name
        parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp),
            mock.patch.object(sc, "DB_PATH",
                              os.path.join(self.dir_tmp, "memoria.db")),
            mock.patch.object(sc, "_SANDBOX_ACTIVO", False),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _agente(self, **kw):
        kw.setdefault("auto", True)
        kw.setdefault("proveedor", "ollama")
        return ra.ReactAgent(directorio=self.dir_tmp, **kw)

    def _salida(self, funcion, *args, **kwargs):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            resultado = funcion(*args, **kwargs)
        return resultado, buffer.getvalue()


class TestBucleBasico(BaseReact510):
    def test_bucle_completo_observa_y_finaliza(self):
        agente = self._agente()
        decisiones = [
            _dec("busco el patrón", "buscar_codigo", {"patron": "login"}),
            _dec("leo", "leer_archivo", {"ruta": "main.py"}),
            _dec("listo", "finalizar", {"resumen": "Tarea completada"}),
        ]
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               side_effect=decisiones), \
             mock.patch.object(ra.ReactAgent, "_tool_buscar_codigo",
                               return_value={"ok": True, "total": 1,
                                             "coincidencias": ["m.py:1"]}) as b, \
             mock.patch.object(ra.ReactAgent, "_tool_leer_archivo",
                               return_value={"ok": True, "ruta": "main.py",
                                             "contenido": "x"}) as l:
            res, _ = self._salida(agente.ejecutar, "arregla login")
        self.assertTrue(res["ok"])
        self.assertEqual(res["iteraciones"], 3)
        self.assertFalse(res["abortado"])
        self.assertIn("Tarea completada", res["resultado"])
        b.assert_called_once()
        l.assert_called_once()
        obs = [m["content"] for m in agente.historial
               if m["role"] == "user" and m["content"].startswith("[OBSERVACIÓN")]
        self.assertEqual(len(obs), 2)

    def test_historial_inicial_sistema_y_tarea(self):
        agente = self._agente()
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               return_value=_dec(accion="finalizar")):
            self._salida(agente.ejecutar, "hacer algo")
        self.assertEqual(agente.historial[0]["role"], "system")
        self.assertIn("hacer algo", agente.historial[1]["content"])

    def test_finalizar_termina_limpio(self):
        agente = self._agente()
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               return_value=_dec(
                                   "acabamos", "finalizar",
                                   {"resumen": "hecho en 1 paso"})):
            res, salida = self._salida(agente.ejecutar, "t")
        self.assertTrue(res["ok"])
        self.assertEqual(res["iteraciones"], 1)
        self.assertIn("🏁", salida)


class TestLimitesYErrores(BaseReact510):
    def test_limite_de_iteraciones(self):
        agente = self._agente(max_iter=3)
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               return_value=_dec("sigo", "buscar_codigo",
                                                 {"patron": "x"})), \
             mock.patch.object(ra.ReactAgent, "_tool_buscar_codigo",
                               return_value={"ok": True}):
            res, _ = self._salida(agente.ejecutar, "bucle infinito")
        self.assertFalse(res["ok"])
        self.assertEqual(res["iteraciones"], 3)
        self.assertIn("Límite", res["resultado"])

    def test_json_invalido_reintenta_correctivo(self):
        agente = self._agente()
        with mock.patch.object(ra.ReactAgent, "_llamar_llm",
                               side_effect=["no es json", "tampoco", ""]) as llamar:
            decision = agente._pedir_decision([{"role": "user",
                                                "content": "TAREA: t"}])
        self.assertIsNone(decision)                     # 3 fallos → None
        self.assertEqual(llamar.call_count, ra.MAX_REINTENTOS_JSON)

    def test_json_valido_tras_reintentos(self):
        agente = self._agente()
        bruto = '```json\n{"accion": "finalizar", "argumentos": {}}\n```'
        with mock.patch.object(ra.ReactAgent, "_llamar_llm",
                               side_effect=["basura", bruto]):
            decision = agente._pedir_decision([{"role": "user",
                                                "content": "TAREA: t"}])
        self.assertIsNotNone(decision)
        self.assertEqual(decision["accion"], "finalizar")

    def test_accion_desconocida_se_reporta_al_llm(self):
        agente = self._agente()
        decisiones = [_dec("pruebo", "navegar_a_marte", {}),
                      _dec("finalizo", "finalizar", {"resumen": "ok"})]
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               side_effect=decisiones):
            res, salida = self._salida(agente.ejecutar, "t")
        self.assertTrue(res["ok"])                       # se recupera
        self.assertIn("⚠️", salida)                       # la acción falló

    def test_error_fatal_del_llm_aborta(self):
        agente = self._agente()
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               side_effect=RuntimeError("API caída")):
            res, _ = self._salida(agente.ejecutar, "t")
        self.assertFalse(res["ok"])
        self.assertTrue(res["abortado"])
        self.assertIn("error del LLM", res["resultado"])


class TestHerramientas(BaseReact510):
    def test_editar_archivo_aplica_y_devuelve_diff(self):
        agente = self._agente()
        destino = os.path.join(self.dir_tmp, "lib", "login.py")
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               side_effect=[
                                   _dec("creo", "editar_archivo",
                                        {"ruta": "lib/login.py",
                                         "contenido": "def login():\n"
                                                      "    return True\n"}),
                                   _dec("fin", "finalizar",
                                        {"resumen": "editado"})]):
            res, _ = self._salida(agente.ejecutar, "crear login")
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.exists(destino))
        with open(destino, encoding="utf-8") as fh:
            contenido = fh.read()
        self.assertIn("return True", contenido)
        # La observación del historial incluye el diff unificado.
        obs = [m["content"] for m in agente.historial
               if m["content"].startswith("[OBSERVACIÓN")]
        self.assertTrue(any("+def login():" in o for o in obs))
        self.assertTrue(any("@@" in o for o in obs))

    def test_editar_archivo_bloquea_rutas_fuera_del_proyecto(self):
        agente = self._agente()
        r = agente._ejecutar_accion(
            "editar_archivo", {"ruta": "../../etc/passwd",
                               "contenido": "malicioso"})
        self.assertFalse(r["ok"])
        self.assertIn("fuera del proyecto", r["error"])

    def test_ejecutar_pruebas_captura_el_fallo(self):
        agente = self._agente()
        fallo = (1, "", "AssertionError: esperaba 2, obtuve 3")
        decisiones = [
            _dec("testeo", "ejecutar_pruebas", {}),
            _dec("arreglo", "finalizar", {"resumen": "vi el fallo"}),
        ]
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               side_effect=decisiones), \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=fallo) as ejecutar:
            res, _ = self._salida(agente.ejecutar, "pasar los tests")
        self.assertTrue(res["ok"])
        ejecutar.assert_called_once()
        obs = [m["content"] for m in agente.historial
               if m["content"].startswith("[OBSERVACIÓN")]
        self.assertTrue(any("FALLO" in o for o in obs))
        self.assertTrue(any("AssertionError" in o for o in obs))

    def test_ejecutar_comando_usa_la_via_sandbox_aware(self):
        agente = self._agente()
        # El comando siempre pasa por sc._ejecutar_comando, la función que lo
        # envuelve con _envolver_sandbox cuando --sandbox está activo.
        with mock.patch.object(sc, "_SANDBOX_ACTIVO", False), \
             mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "hola\n", "")) as ej:
            r = agente._tool_ejecutar_comando({"comando": "echo hola"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["stdout"], "hola\n")
        self.assertEqual(ej.call_args[0][0], "echo hola")


class TestInteractivoYContexto(BaseReact510):
    def test_modo_interactivo_pregunta_antes_de_actuar(self):
        agente = self._agente(auto=False)
        decisiones = [
            _dec("busco", "buscar_codigo", {"patron": "x"}),
            _dec("fin", "finalizar", {"resumen": "listo"}),
        ]
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               side_effect=decisiones), \
             mock.patch.object(ra.ReactAgent, "_tool_buscar_codigo",
                               return_value={"ok": True}) as buscar, \
             mock.patch("ui.preguntar_interactivo",
                        return_value="c") as preguntar:
            res, _ = self._salida(agente.ejecutar, "tarea interactiva")
        self.assertTrue(res["ok"])
        preguntar.assert_called_once()
        buscar.assert_called_once()

    def test_respuesta_abortar_detiene_el_bucle(self):
        agente = self._agente(auto=False)
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               return_value=_dec("hago", "leer_archivo",
                                                 {"ruta": "main.py"})), \
             mock.patch("ui.preguntar_interactivo",
                        return_value="a") as preguntar:
            res, _ = self._salida(agente.ejecutar, "t")
        self.assertFalse(res["ok"])
        self.assertTrue(res["abortado"])
        preguntar.assert_called_once()

    def test_en_auto_nunca_pregunta(self):
        agente = self._agente(auto=True)
        decisiones = [_dec("paso", "buscar_codigo", {"patron": "x"}),
                      _dec("fin", "finalizar", {"resumen": "ok"})]
        with mock.patch.object(ra.ReactAgent, "_pedir_decision",
                               side_effect=decisiones), \
             mock.patch.object(ra.ReactAgent, "_tool_buscar_codigo",
                               return_value={"ok": True}), \
             mock.patch("ui.preguntar_interactivo") as preguntar:
            res, _ = self._salida(agente.ejecutar, "t auto")
        self.assertTrue(res["ok"])
        preguntar.assert_not_called()

    def test_resumen_automatico_comprime_el_historial(self):
        agente = self._agente()
        agente.historial = [
            {"role": "system", "content": "sistema"},
            {"role": "user", "content": "TAREA: mucha cosa " * 4000},
        ]
        with mock.patch.object(ra, "_umbral_resumen", return_value=100), \
             mock.patch.object(ra.ReactAgent, "_llamar_llm",
                               return_value="Resumen corto.") as llm:
            cambio = agente._resumir_si_hace_falta()
        self.assertTrue(cambio)
        llm.assert_called_once()
        self.assertEqual(len(agente.historial), 2)
        self.assertIn("[RESUMEN DEL TRABAJO PREVIO]",
                      agente.historial[1]["content"])
        self.assertNotIn("TAREA: mucha cosa",
                         agente.historial[1]["content"])

    def test_no_resume_por_debajo_del_umbral(self):
        agente = self._agente()
        agente.historial = [{"role": "system", "content": "sistema"},
                            {"role": "user", "content": "TAREA: corta"}]
        with mock.patch.object(ra, "_umbral_resumen", return_value=10 ** 9):
            self.assertFalse(agente._resumir_si_hace_falta())


class TestRouterModoPorDefecto(BaseReact510):
    """Desde v5.2.0: ReAct es el default; --plan mantiene el legacy."""

    def test_sin_flags_ejecuta_react(self):
        args = sc.crear_parser().parse_args([("\"arregla el login\"")])
        with mock.patch.object(sc, "_ejecutar_react") as react, \
                mock.patch.object(sc, "_ejecutar_planificador") as plan:
            react.return_value = 0
            sc._ejecutar_modo_tarea(args)
        react.assert_called_once_with(args)
        plan.assert_not_called()

    def test_plan_ejecuta_planificador_legacy(self):
        args = sc.crear_parser().parse_args(["--plan", "\"migrar a pytest\""])
        with mock.patch.object(sc, "_ejecutar_react") as react, \
                mock.patch.object(sc, "_ejecutar_planificador") as plan:
            plan.return_value = 0
            sc._ejecutar_modo_tarea(args)
        plan.assert_called_once_with(args)
        react.assert_not_called()

    def test_flag_react_equivale_a_default(self):
        args = sc.crear_parser().parse_args(
            ["--react", "\"refactoriza modulo.py\""])
        self.assertTrue(args.react)
        with mock.patch.object(sc, "_ejecutar_react") as react, \
                mock.patch.object(sc, "_ejecutar_planificador") as plan:
            react.return_value = 0
            sc._ejecutar_modo_tarea(args)
        react.assert_called_once_with(args)
        plan.assert_not_called()


if __name__ == "__main__":
    unittest.main()