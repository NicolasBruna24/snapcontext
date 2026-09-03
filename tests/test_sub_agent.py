#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de Sub-agentes dinámicos (v6.13.0)."""

import io
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import sub_agent
from sub_agent import (ROLES, ROLES_VALIDOS, SubAgente, SubAgentRegistry,
                       REGISTRO_SUB_AGENTES, listar_roles,
                       rol_valido, ejecutar_sub_agentes_paralelo,
                       ejecutar_tarea_sub_agente)
from sub_agent_prompts import PROMPTS, ROLES_DEFECTO


def _silencio():
    return redirect_stdout(io.StringIO())


class TestRoles(unittest.TestCase):
    """Roles predefinidos y registro."""

    def test_hay_6_roles(self):
        self.assertEqual(set(ROLES.keys()),
                         {"scout", "debugger", "frontender", "tester",
                          "documentador", "reviewer"})

    def test_roles_tienen_prompt_y_herramientas(self):
        for rol, cfg in ROLES.items():
            self.assertTrue(cfg["prompt"].strip(), rol)
            self.assertTrue(cfg["herramientas"], rol)
            self.assertIn("finalizar", cfg["herramientas"], rol)
            self.assertGreaterEqual(cfg["max_iter"], 1, rol)

    def test_rol_valido(self):
        self.assertTrue(rol_valido("scout"))
        self.assertTrue(rol_valido("  TESTER "))
        self.assertFalse(rol_valido("inexistente"))

    def test_listar_roles(self):
        self.assertEqual(listar_roles(), sorted(ROLES_VALIDOS))


class TestSubAgente(unittest.TestCase):
    """Instanciación, aislamiento y comunicación."""

    def test_rol_invalido_lanza_error(self):
        with self.assertRaises(ValueError):
            SubAgente("no-existe")

    def test_creacion_y_atributos(self):
        with _silencio():
            sub = SubAgente("scout", nombre="scout-1")
        self.assertEqual(sub.rol, "scout")
        self.assertEqual(sub.nombre, "scout-1")
        self.assertEqual(sub.max_iteraciones, ROLES["scout"]["max_iter"])
        self.assertIsNotNone(sub.agente)

    def test_herramientas_restringidas(self):
        with _silencio():
            sub = SubAgente("scout")
        # Scout no puede editar archivos (mínimo privilegio).
        self.assertNotIn("editar_archivo", sub.herramientas)
        self.assertIn("leer_archivo", sub.herramientas)
        # El agente interno usa la misma restricción.
        self.assertEqual(set(sub.agente.herramientas),
                         set(sub.herramientas))
        # 'finalizar' es una acción del bucle ReAct, no una herramienta.
        self.assertIn("finalizar", sub.agente.ACCIONES_VALIDAS)

    def test_prompt_sistema_incluye_rol(self):
        with _silencio():
            sub = SubAgente("debugger")
        prompt = sub._prompt_sistema()
        self.assertIn("debugger", prompt)
        self.assertIn("Debugger", prompt)
        self.assertIn("Herramientas permitidas", prompt)
        self.assertIn("agente", prompt.lower())


class TestEjecucionAislada(unittest.TestCase):
    """Ejecución del bucle ReAct (mockeada) y aislamiento de contexto."""

    def _sub_mock(self, rol="scout", resultado=None, buzon=None):
        sub = SubAgente(rol, buzon=buzon)
        base = {"ok": True, "resultado": "hecho", "iteraciones": 2,
                "abortado": False}
        base.update(resultado or {})
        sub.agente.ejecutar = mock.MagicMock(return_value=dict(base))
        return sub

    def test_ejecutar_devuelve_rol_y_nombre(self):
        with _silencio():
            sub = self._sub_mock()
            r = sub.ejecutar("investiga la API")
        self.assertTrue(r["ok"])
        self.assertEqual(r["rol"], "scout")
        self.assertTrue(r["nombre"].startswith("scout-"))
        sub.agente.ejecutar.assert_called_once()

    def test_ejecutar_consulta_aislada(self):
        with _silencio():
            sub = self._sub_mock()
            sub.ejecutar("lee la documentación")
        consulta = sub.agente.ejecutar.call_args[0][0]
        self.assertIn("lee la documentación", consulta)
        self.assertIsNotNone(sub.agente.historial)

    def test_ejecutar_inyecta_mensajes_recibidos(self):
        with _silencio():
            sub = self._sub_mock()
            sub.enviar_mensaje("usa la clave X")
            sub.ejecutar("revisa el endpoint")
        consulta = sub.agente.ejecutar.call_args[0][0]
        self.assertIn("MENSAJES RECIBIDOS", consulta)
        self.assertIn("usa la clave X", consulta)

    def test_ejecutar_publica_en_buzon(self):
        from multi_agent import Buzon
        buzon = Buzon()
        with _silencio():
            sub = self._sub_mock(buzon=buzon)
            sub.ejecutar("tarea")
        mensajes = buzon.historial()
        self.assertEqual(len(mensajes), 1)
        self.assertEqual(mensajes[0]["tipo"], "resultado_sub_agente")
        self.assertEqual(mensajes[0]["contenido"]["rol"], "scout")

    def test_ejecutar_captura_excepciones(self):
        with _silencio():
            sub = SubAgente("scout")
            sub.agente.ejecutar = mock.MagicMock(
                side_effect=RuntimeError("LLM caido"))
            r = sub.ejecutar("tarea")
        self.assertFalse(r["ok"])
        self.assertIn("LLM caido", r["resultado"])
        self.assertEqual(r["rol"], "scout")


class TestParalelismo(unittest.TestCase):
    """Ejecución en paralelo con límite de concurrencia."""

    def _patch_subagente(self, resultado=None):
        """Parchea sub_agent.SubAgente para no llamar al LLM."""
        resultado = resultado or {"ok": True, "resultado": "ok",
                                  "iteraciones": 1, "abortado": False}
        creados = []

        class _FakeSub:
            def __init__(self, rol, **kwargs):
                if rol not in ROLES:
                    raise ValueError(f"Rol desconocido: {rol}")
                self.rol = rol
                self.kwargs = kwargs
                creados.append(self)

            def ejecutar(self, consulta):
                r = dict(resultado)
                r["consulta"] = consulta
                r["rol"] = self.rol
                return r

        return _FakeSub, creados

    def test_lista_vacia_devuelve_vacia(self):
        self.assertEqual(ejecutar_sub_agentes_paralelo([]), [])

    def test_resultados_en_orden(self):
        fake, creados = self._patch_subagente()
        especs = [{"rol": "scout", "consulta": "a"},
                  {"rol": "tester", "consulta": "b"},
                  {"rol": "debugger", "consulta": "c"}]
        with _silencio(), mock.patch.object(sub_agent, "SubAgente", fake):
            resultados = ejecutar_sub_agentes_paralelo(especs)
        self.assertEqual(len(resultados), 3)
        self.assertEqual([r["rol"] for r in resultados],
                         ["scout", "tester", "debugger"])
        self.assertEqual([r["consulta"] for r in resultados],
                         ["a", "b", "c"])
        self.assertTrue(all(r["ok"] for r in resultados))
        self.assertEqual(len(creados), 3)

    def test_respeta_max_parallel(self):
        en_ejecucion = {"n": 0, "max": 0}
        candado = threading.Lock()

        class _FakeSub:
            def __init__(self, rol, **kwargs):
                self.rol = rol

            def ejecutar(self, consulta):
                with candado:
                    en_ejecucion["n"] += 1
                    en_ejecucion["max"] = max(en_ejecucion["max"],
                                              en_ejecucion["n"])
                time.sleep(0.08)
                with candado:
                    en_ejecucion["n"] -= 1
                return {"ok": True, "resultado": consulta}

        especs = [{"rol": "scout", "consulta": str(i)} for i in range(8)]
        with _silencio(), mock.patch.object(sub_agent, "SubAgente", _FakeSub):
            resultados = ejecutar_sub_agentes_paralelo(especs, max_parallel=2)
        self.assertEqual(len(resultados), 8)
        self.assertLessEqual(en_ejecucion["max"], 2)

    def test_rol_invalido_no_aborta_el_resto(self):
        fake, _ = self._patch_subagente()
        especs = [{"rol": "no-existe", "consulta": "x"},
                  {"rol": "scout", "consulta": "y"}]
        with _silencio(), mock.patch.object(sub_agent, "SubAgente", fake):
            resultados = ejecutar_sub_agentes_paralelo(especs)
        self.assertFalse(resultados[0]["ok"])
        self.assertTrue(resultados[1]["ok"])

    def test_publica_en_buzon_compartido(self):
        from multi_agent import Buzon
        buzon = Buzon()

        class _FakeSub:
            def __init__(self, rol, buzon=None, **kwargs):
                self.rol = rol
                self.buzon = buzon

            def ejecutar(self, consulta):
                if self.buzon is not None:
                    self.buzon.publicar(
                        self.rol, "resultado_sub_agente",
                        {"rol": self.rol, "ok": True})
                return {"ok": True, "rol": self.rol}

        especs = [{"rol": "scout", "consulta": "a"},
                  {"rol": "tester", "consulta": "b"}]
        with _silencio(), mock.patch.object(sub_agent, "SubAgente", _FakeSub):
            ejecutar_sub_agentes_paralelo(especs, buzon=buzon)
        tipos = [m["tipo"] for m in buzon.historial()]
        self.assertEqual(tipos.count("resultado_sub_agente"), 2)


class TestColaTareas(unittest.TestCase):
    """Integración con la cola de tareas (v6.8.0)."""

    def test_encolar_sub_agente(self):
        import task_queue as tq
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "memoria.db"
            with _silencio():
                tarea_id = sub_agent.encolar_sub_agente(
                    "scout", "investiga", db_path=db)
            tarea = tq.obtener_tarea(tarea_id, db_path=db)
            self.assertEqual(tarea["tipo"], "sub_agente")
            self.assertEqual(tarea["estado"], "pendiente")
            self.assertEqual(tarea["datos"]["rol"], "scout")
            self.assertEqual(tarea["datos"]["consulta"], "investiga")

    def test_ejecutar_tarea_sub_agente_mockeada(self):
        with _silencio(), mock.patch.object(
                sub_agent, "SubAgente") as fake_cls:
            fake_cls.return_value.ejecutar.return_value = {"ok": True}
            r = ejecutar_tarea_sub_agente(
                {"rol": "tester", "consulta": "corre tests"})
        self.assertTrue(r["ok"])
        fake_cls.assert_called_once_with(
            "tester", directorio=".", proveedor=None, modelo=None)
        fake_cls.return_value.ejecutar.assert_called_once_with("corre tests")


class TestSupervisor(unittest.TestCase):
    """Integración del Supervisor con sub-agentes."""

    def _supervisor(self, **kwargs):
        from multi_agent import Supervisor
        with tempfile.TemporaryDirectory() as tmp:
            kwargs.setdefault("directorio", tmp)
            return Supervisor(**kwargs)

    def test_supervisor_por_defecto_sin_sub_agentes(self):
        sup = self._supervisor()
        self.assertFalse(sup.sub_agents)
        self.assertEqual(sup.max_parallel, 3)
        self.assertEqual(sup.sub_agentes, [])

    def test_crear_sub_agente_registra(self):
        sup = self._supervisor()
        with _silencio():
            sub = sup.crear_sub_agente("scout", consulta="investiga")
        self.assertIn(sub, sup.sub_agentes)
        self.assertEqual(sub.rol, "scout")
        # La consulta pasa como mensaje al sub-agente.
        self.assertEqual(len(sub.recibir_mensajes()), 1)

    def test_crear_sub_agente_rol_invalido(self):
        sup = self._supervisor()
        with _silencio():
            with self.assertRaises(ValueError):
                sup.crear_sub_agente("fantasma")

    def test_detectar_sub_tareas_por_palabras_clave(self):
        plan = {"objetivo": "mejorar el modulo",
                "pasos": [
                    {"descripcion": "Leer documentación de la API"},
                    {"descripcion": "analizar el error del parser"},
                    {"descripcion": "refactorizar editando codigo"},
                ]}
        especs = self._supervisor()._detectar_sub_tareas(plan)
        roles = [e["rol"] for e in especs]
        self.assertIn("scout", roles)
        self.assertIn("debugger", roles)
        self.assertNotIn("tester", roles)

    def test_detectar_sub_tareas_sin_delegables(self):
        plan = {"objetivo": "cambiar nombre de variable",
                "pasos": [{"descripcion": "renombrar en el modulo"}]}
        self.assertEqual(self._supervisor()._detectar_sub_tareas(plan), [])

    def test_ejecutar_sub_tareas_inactivo_devuelve_vacio(self):
        sup = self._supervisor()
        with _silencio():
            self.assertEqual(sup.ejecutar_sub_tareas(
                {"objetivo": "leer documentacion", "pasos": []}), [])
        self.assertEqual(sup.sub_agentes, [])

    def test_ejecutar_sub_tareas_activo_paralelo_mockeado(self):
        sup = self._supervisor(sub_agents=True, max_parallel=2)
        fake, creados = TestParalelismo._patch_subagente(None)
        plan = {"objetivo": "revisar",
                "pasos": [{"descripcion": "leer documentacion de la api"},
                          {"descripcion": "ejecutar pruebas"}]}
        with _silencio(), mock.patch.object(sub_agent, "SubAgente", fake):
            resultados = sup.ejecutar_sub_tareas(plan)
        # "leer documentacion de la api" activa scout y documentador;
        # "ejecutar pruebas" activa tester → 3 sub-tareas.
        self.assertEqual(len(resultados), 3)
        self.assertEqual(len(sup.sub_agentes), 0)  # creados vía parche
        # El Supervisor publica cada resultado en su buzón.
        tipos = [m["tipo"] for m in sup.buzon.historial()]
        self.assertIn("sub_tarea_completada", tipos)


class TestFlagsCLI(unittest.TestCase):
    """Flags --sub-agents y --max-parallel."""

    def test_flags_por_defecto(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["hola"])
        self.assertFalse(args.sub_agents)
        self.assertEqual(args.max_parallel, 3)

    def test_flags_activados(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(
            ["--multi-agent", "--sub-agents", "--max-parallel", "5", "hola"])
        self.assertTrue(args.sub_agents)
        self.assertEqual(args.max_parallel, 5)


class TestFlagsNuevos(unittest.TestCase):
    """Flags --sub-agente-nuevo / --sub-agente-listar (v6.18.0)."""

    def test_flag_nuevo_parsea_dos_args(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(
            ["--sub-agente-nuevo", "miPlugin", "revisa el front"])
        self.assertEqual(args.sub_agente_nuevo, ["miPlugin", "revisa el front"])

    def test_flag_listar_por_defecto_false(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["hola"])
        self.assertFalse(args.sub_agente_listar)

    def test_flag_listar_true(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["--sub-agente-listar"])
        self.assertTrue(args.sub_agente_listar)

    def test_registrar_sub_agente_cli(self):
        import snapcontext as sc
        with mock.patch("sub_agent.REGISTRO_SUB_AGENTES") as reg:
            reg.registrar.side_effect = lambda n, c: None
            r = sc._registrar_sub_agente_cli("auditor", "revisa seguridad")
        self.assertEqual(r, 0)
        reg.registrar.assert_called_once()
        _, cfg = reg.registrar.call_args[0]
        self.assertEqual(cfg["descripcion"], "revisa seguridad")
        self.assertIn("leer_archivo", cfg["herramientas"])

    def test_listar_sub_agentes_ok(self):
        import snapcontext as sc
        with mock.patch("sub_agent.REGISTRO_SUB_AGENTES") as reg:
            reg.listar.return_value = ["scout", "reviewer"]
            reg.obtener.side_effect = lambda n: {"descripcion": "d",
                                                 "herramientas": ["leer"],
                                                 "max_iter": 8}
            self.assertEqual(sc._ejecutar_listar_sub_agentes(), 0)


class TestSubAgentRegistry(unittest.TestCase):
    """Registro consultable y extensible (v6.18.0)."""

    def test_registro_por_defecto(self):
        reg = SubAgentRegistry()
        self.assertEqual(reg.listar(), ["debugger", "documentador",
                                        "reviewer", "scout"])

    def test_obtener_devuelve_config(self):
        reg = SubAgentRegistry()
        cfg = reg.obtener("scout")
        self.assertEqual(cfg["rol"], "scout")
        self.assertTrue(cfg["prompt"])
        self.assertIn("finalizar", cfg["herramientas"])
        self.assertIn("max_iter", cfg)

    def test_obtener_desconocido_lanza_keyerror(self):
        reg = SubAgentRegistry()
        with self.assertRaises(KeyError):
            reg.obtener("fantasma")

    def test_registrar_rol_nuevo(self):
        reg = SubAgentRegistry(predefinidos=False)
        reg.registrar("auditor", {"descripcion": "revisa seguridad",
                                  "prompt": "Eres Auditor.",
                                  "herramientas": ["leer_archivo", "finalizar"],
                                  "max_iter": 5})
        self.assertIn("auditor", reg.listar())
        cfg = reg.obtener("auditor")
        self.assertEqual(cfg["rol"], "auditor")
        self.assertEqual(cfg["max_iter"], 5)

    def test_registrar_sobrescribe(self):
        reg = SubAgentRegistry(predefinidos=False)
        reg.registrar("auditor", {"prompt": "v1", "herramientas": [],
                                  "max_iter": 3})
        reg.registrar("auditor", {"prompt": "v2", "herramientas": [],
                                  "max_iter": 6})
        self.assertEqual(reg.obtener("auditor")["prompt"], "v2")
        self.assertEqual(reg.obtener("auditor")["max_iter"], 6)

    def test_contiene(self):
        reg = SubAgentRegistry()
        self.assertIn("reviewer", reg)
        self.assertNotIn("fantasma", reg)

    def test_subagente_con_config_dinamico(self):
        # Un rol nuevo (no en ROLES) se instancia si se pasa su configuración.
        cfg = {"prompt": "Eres Auditor.", "herramientas": ["leer_archivo",
                                                            "finalizar"],
               "max_iter": 5}
        with _silencio():
            sub = SubAgente("auditor", config=cfg)
        self.assertEqual(sub.rol, "auditor")
        self.assertEqual(sub.max_iteraciones, 5)
        self.assertIn("leer_archivo", sub.herramientas)
        self.assertNotIn("editar_archivo", sub.herramientas)


class TestPrompts(unittest.TestCase):
    """Módulo sub_agent_prompts.py (v6.18.0)."""

    def test_prompts_por_defecto(self):
        for rol in ("scout", "debugger", "reviewer", "documentador"):
            self.assertIn(rol, PROMPTS)
            self.assertTrue(PROMPTS[rol].strip())

    def test_roles_defecto(self):
        self.assertEqual(set(ROLES_DEFECTO),
                         {"scout", "debugger", "reviewer", "documentador"})

    def test_roles_usan_prompts_canonicos(self):
        for rol in PROMPTS:
            self.assertIn(rol, ROLES)
            self.assertEqual(ROLES[rol]["prompt"], PROMPTS[rol])


class TestInvocacion(unittest.TestCase):
    """Invocación bajo demanda desde Supervisor y ReAct (v6.18.0)."""

    def _supervisor(self, **kwargs):
        from multi_agent import Supervisor
        with tempfile.TemporaryDirectory() as tmp:
            kwargs.setdefault("directorio", tmp)
            return Supervisor(**kwargs)

    def test_supervisor_invoca_sub_agente(self):
        sup = self._supervisor()
        fake = mock.MagicMock()
        fake.ejecutar.return_value = {"ok": True, "resultado": "ok rescate",
                                      "iteraciones": 1, "abortado": False,
                                      "rol": "scout", "nombre": "scout"}
        with _silencio(), mock.patch("sub_agent.SubAgente",
                                     return_value=fake) as cls:
            r = sup.invocar_sub_agente("scout", "investiga")
        self.assertTrue(r["ok"])
        self.assertIn(fake, sup.sub_agentes)
        cfg = cls.call_args.kwargs["config"]
        self.assertEqual(cfg["rol"], "scout")

    def test_supervisor_invoca_desconocido_error(self):
        sup = self._supervisor()
        with mock.patch("sub_agent.SubAgente"):
            with self.assertRaises(KeyError):
                sup.invocar_sub_agente("fantasma")

    def test_react_tool_registrada_por_defecto(self):
        import react_agent
        agente = react_agent.ReactAgent(directorio=".", auto=True)
        self.assertIn("invocar_sub_agente", agente.herramientas)
        self.assertIn("invocar_sub_agente",
                      react_agent.ReactAgent.ACCIONES_VALIDAS)

    def test_react_tool_desactivable(self):
        import react_agent
        agente = react_agent.ReactAgent(directorio=".", auto=True,
                                        sub_agents=False)
        self.assertNotIn("invocar_sub_agente", agente.herramientas)

    def test_react_tool_ejecuta_sub_agente(self):
        import react_agent
        agente = react_agent.ReactAgent(directorio=".", auto=True)
        fake = mock.MagicMock()
        fake.ejecutar.return_value = {"ok": True, "resultado": "ok rescate",
                                      "iteraciones": 2, "abortado": False}
        with _silencio(), mock.patch("sub_agent.SubAgente",
                                     return_value=fake):
            r = agente._tool_invocar_sub_agente(
                {"nombre": "debugger", "consulta": "analiza el log"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["rol"], "debugger")
        self.assertEqual(r["resultado"], "ok rescate")

    def test_react_tool_sin_nombre_error(self):
        import react_agent
        agente = react_agent.ReactAgent(directorio=".", auto=True)
        r = agente._tool_invocar_sub_agente({"consulta": "x"})
        self.assertFalse(r["ok"])
        self.assertIn("nombre", r["error"])


class TestVersion618(unittest.TestCase):
    """La versión se actualiza a 6.18.0 en snapcontext y pyproject."""

    def test_version_snapcontext(self):
        import snapcontext as sc
        self.assertEqual(sc.VERSION, "6.19.0")

    def test_version_pyproject(self):
        import os
        ruta = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "pyproject.toml")
        with open(ruta, encoding="utf-8") as fh:
            self.assertIn('version = "6.19.0"', fh.read())
        # El módulo de sub-agentes se empaqueta en el .whl.
        with open(ruta, encoding="utf-8") as fh2:
            self.assertIn("sub_agent_prompts", fh2.read())


if __name__ == "__main__":
    unittest.main()
