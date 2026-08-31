#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests v6.4.0: persistencia de Docker por sesión (--sandbox-session).

Cubre el ciclo de vida del módulo ``sandbox_session`` (crear/reutilizar/
destruir/orfandad), la integración en ``snapcontext`` (``_ejecutar_comando``,
plan y ReAct) y la compatibilidad (sin ``--sandbox-session`` se usa
``docker run --rm``).
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import react_agent as ra           # noqa: E402
import sandbox_session as ss       # noqa: E402
import snapcontext as sc           # noqa: E402


def _completado(ret=0, out="", err=""):
    return mock.Mock(returncode=ret, stdout=out, stderr=err)


def _args_react(**extra) -> argparse.Namespace:
    base = {"consulta": "tarea", "auto": False, "react_max_iter": 3,
            "graph_rag": False, "mostrar_razonamiento": False,
            "sandbox_session": True}
    base.update(extra or {})
    return argparse.Namespace(**base)


class TestSesionModule(unittest.TestCase):
    """Primitivas de sandbox_session.py (mockeando docker)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sc640ss_"))
        self.archivo_id = self.tmp / "session_id.txt"
        self.dir_proy = self.tmp / "proy"
        self.dir_proy.mkdir()
        self._patches = [
            mock.patch.object(ss, "SESSION_ID_PATH", self.archivo_id),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._cleanup)

    def tearDown(self):
        ss._poner_nombre(None)

    def _cleanup(self):
        for p in self._patches:
            p.stop()

    def test_crear_sesion_lanza_docker_run_mantenido(self):
        with mock.patch.object(ss, "_run", return_value=_completado(0)) as run:
            nombre = ss.crear_sesion(str(self.dir_proy), "python:3.11-slim")
        self.assertTrue(nombre.startswith("snap-session-"))
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "docker")
        self.assertIn("run", argv)
        self.assertIn("-d", argv)
        self.assertIn("--name", argv)
        self.assertEqual(argv[argv.index("--name") + 1], nombre)
        # Mantiene el contenedor vivo y monta el proyecto en /workspace.
        self.assertIn("tail", argv)
        self.assertIn("-f", argv)
        self.assertIn("/dev/null", argv)
        self.assertIn(f"{str(self.dir_proy.resolve())}:/workspace", argv)
        self.assertTrue(ss.sesion_activa())
        self.assertEqual(ss.sesion_nombre(), nombre)

    def test_crear_sesion_guarda_id_en_archivo(self):
        with mock.patch.object(ss, "_run", return_value=_completado(0)):
            ss.crear_sesion(str(self.dir_proy), "imagen")
        self.assertTrue(self.archivo_id.exists())
        sid = self.archivo_id.read_text(encoding="utf-8").strip()
        self.assertEqual(ss.sesion_nombre(), f"snap-session-{sid}")

    def test_crear_sesion_ejecuta_comando_preparacion(self):
        llamadas = [_completado(0), _completado(0)]
        with mock.patch.object(ss, "_run",
                               side_effect=lambda *a, **k: llamadas.pop(0)) as run:
            ss.crear_sesion(str(self.dir_proy), "imagen",
                            comando_preparacion="pip install -r req.txt")
        self.assertEqual(run.call_count, 2)
        prep_argv = run.call_args_list[1][0][0]
        self.assertEqual(prep_argv[:3], ["docker", "exec", ss.sesion_nombre()])
        self.assertIn("pip install -r req.txt", prep_argv)

    def test_crear_sesion_sin_docker_lanza_error(self):
        with mock.patch.object(ss, "_run", side_effect=OSError("no docker")):
            with self.assertRaises(OSError):
                ss.crear_sesion(str(self.dir_proy), "imagen")
        self.assertFalse(ss.sesion_activa())

    def test_obtener_sesion_en_ejecucion(self):
        self.archivo_id.write_text("abc123", encoding="utf-8")
        with mock.patch.object(ss, "_run",
                               return_value=_completado(0, "true")):
            nombre = ss.obtener_sesion()
        self.assertEqual(nombre, "snap-session-abc123")
        self.assertTrue(ss.sesion_activa())

    def test_obtener_sesion_parado_devuelve_none(self):
        self.archivo_id.write_text("abc123", encoding="utf-8")
        with mock.patch.object(ss, "_run", return_value=_completado(0, "false")):
            self.assertIsNone(ss.obtener_sesion())
        self.assertFalse(ss.sesion_activa())

    def test_obtener_sesion_sin_archivo_devuelve_estado_memoria(self):
        ss._poner_nombre("snap-session-xyz")
        self.assertEqual(ss.obtener_sesion(), "snap-session-xyz")

    def test_comando_en_sesion_usa_docker_exec(self):
        ss._poner_nombre("snap-session-abc")
        with mock.patch.object(ss, "obtener_sesion", return_value=None):
            cmd = ss.comando_en_sesion("pytest -q")
        self.assertIn("docker", cmd)
        self.assertIn("exec", cmd)
        self.assertIn("snap-session-abc", cmd)
        self.assertIn("pytest -q", cmd)

    def test_ejecutar_en_sesion_devuelve_codigo_salida(self):
        ss._poner_nombre("snap-session-abc")
        with mock.patch.object(ss.subprocess, "run",
                               return_value=_completado(0, "hola", "")) as run:
            codigo, out, err = ss.ejecutar_en_sesion("echo hola")
        self.assertEqual((codigo, out, err), (0, "hola", ""))
        argv = run.call_args[0][0]
        self.assertIn("docker exec snap-session-abc", argv)
        self.assertIn("echo hola", argv)

    def test_ejecutar_en_sesion_timeout_devuelve_error(self):
        ss._poner_nombre("snap-session-abc")
        with mock.patch.object(ss.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 5)):
            codigo, _, err = ss.ejecutar_en_sesion("sleep 100", timeout=5)
        self.assertEqual(codigo, -1)
        self.assertIn("timeout", err.lower())

    def test_ejecutar_en_sesion_sin_sesion_devuelve_error(self):
        ss._poner_nombre(None)
        with mock.patch.object(ss, "SESSION_ID_PATH",
                               self.tmp / "no_existe.txt"):
            codigo, _, err = ss.ejecutar_en_sesion("ls")
        self.assertEqual(codigo, -1)
        self.assertIn("sesión", err.lower())

    def test_destruir_sesion_elimina_contenedor_y_archivo(self):
        ss._poner_nombre("snap-session-abc")
        self.archivo_id.write_text("abc", encoding="utf-8")
        with mock.patch.object(ss, "_run", return_value=_completado(0)) as run:
            self.assertTrue(ss.destruir_sesion())
        self.assertIn("rm", run.call_args[0][0])
        self.assertIn("-f", run.call_args[0][0])
        self.assertIn("snap-session-abc", run.call_args[0][0])
        self.assertFalse(self.archivo_id.exists())
        self.assertFalse(ss.sesion_activa())

    def test_destruir_sesion_idempotente(self):
        with mock.patch.object(ss, "SESSION_ID_PATH",
                               self.tmp / "no_existe.txt"):
            self.assertFalse(ss.destruir_sesion())

    def test_limpiar_huerfanos_auto_elimina_todos(self):
        with mock.patch.object(ss, "_listar_contenedores_sesion",
                               return_value=["snap-session-a", "snap-session-b"]), \
             mock.patch.object(ss, "_run", return_value=_completado(0)) as run:
            self.assertEqual(ss.limpiar_huérfanos(auto=True), 2)
        self.assertEqual(run.call_count, 2)

    def test_limpiar_huerfanos_interactivo_respeta_negativa(self):
        with mock.patch.object(ss, "_listar_contenedores_sesion",
                               return_value=["snap-session-a"]), \
             mock.patch("snapcontext._preguntar_si", return_value=False), \
             mock.patch.object(ss, "_run") as run:
            self.assertEqual(ss.limpiar_huérfanos(auto=False), 0)
        run.assert_not_called()

    def test_limpiar_huerfanos_sin_huerfanos(self):
        with mock.patch.object(ss, "_listar_contenedores_sesion",
                               return_value=[]):
            self.assertEqual(ss.limpiar_huérfanos(auto=True), 0)


class TestIntegracionSnapcontext(unittest.TestCase):
    """Integración en _ejecutar_comando / plan / señales (mockeando docker)."""

    def setUp(self):
        ss._poner_nombre(None)
        self._restore = (sc._SESION_DOCKER_SOLICITADA, sc._SANDBOX_IMAGEN)
        self.addCleanup(self._restore_estado)

    def _restore_estado(self):
        sc._SESION_DOCKER_SOLICITADA, sc._SANDBOX_IMAGEN = self._restore
        ss._poner_nombre(None)

    def test_comando_con_sesion_usa_docker_exec(self):
        sc._configurar_sesion_docker(True)
        ss._poner_nombre("snap-session-tst")
        with mock.patch.object(sc, "_decidir_ejecucion_sandbox",
                               return_value=sc._SANDBOX_CONTENEDOR), \
             mock.patch.object(subprocess, "run",
                               return_value=_completado(0, "ok", "")) as run:
            codigo, out, _ = sc._ejecutar_comando("pytest -q", str(RAIZ))
        self.assertEqual((codigo, out), (0, "ok"))
        self.assertIn("docker exec snap-session-tst", run.call_args[0][0])
        self.assertIn("pytest -q", run.call_args[0][0])

    def test_sesion_se_crea_de_forma_perezosa_y_se_reutiliza(self):
        sc._configurar_sesion_docker(True)
        with mock.patch.object(sc, "_decidir_ejecucion_sandbox",
                               return_value=sc._SANDBOX_CONTENEDOR), \
             mock.patch.object(ss, "crear_sesion",
                               side_effect=lambda *a, **k:
                               (ss._poner_nombre("snap-session-nuevo"),
                                "snap-session-nuevo")[1]) as crear, \
             mock.patch.object(subprocess, "run",
                               return_value=_completado(0, "", "")):
            sc._ejecutar_comando("cmd1", str(RAIZ))
            sc._ejecutar_comando("cmd2", str(RAIZ))
        crear.assert_called_once()          # un solo contenedor para toda la tarea
        self.assertEqual(ss.sesion_nombre(), "snap-session-nuevo")

    def test_comando_sin_flag_usa_docker_run_rm(self):
        sc._configurar_sesion_docker(False)
        with mock.patch.object(sc, "_decidir_ejecucion_sandbox",
                               return_value=sc._SANDBOX_CONTENEDOR), \
             mock.patch.object(sc, "_envolver_sandbox",
                               return_value="docker run --rm ENVOLTO"), \
             mock.patch.object(subprocess, "run",
                               return_value=_completado(0, "", "")) as run:
            sc._ejecutar_comando("pytest -q", str(RAIZ))
        cmd = run.call_args[0][0]
        self.assertIn("docker run", cmd)
        self.assertNotIn("docker exec", cmd)

    def test_asegurar_sesion_reutiliza_la_existente(self):
        ss._poner_nombre("snap-session-viva")
        with mock.patch.object(ss, "crear_sesion") as crear:
            self.assertEqual(sc._asegurar_sesion_docker(str(RAIZ)),
                             "snap-session-viva")
        crear.assert_not_called()

    def test_destruir_sesion_si_aplica_sin_flag_no_hace_nada(self):
        sc._configurar_sesion_docker(False)
        with mock.patch.object(ss, "destruir_sesion") as destr:
            sc._destruir_sesion_si_aplica()
        destr.assert_not_called()

    def test_destruir_sesion_si_aplica_con_flag_destruye(self):
        sc._configurar_sesion_docker(True)
        with mock.patch.object(ss, "destruir_sesion",
                               return_value=True) as destr:
            sc._destruir_sesion_si_aplica()
        destr.assert_called_once()

    def test_ctrl_c_destruye_la_sesion(self):
        # El manejador de SIGINT debe destruir la sesión antes de salir.
        manejadores = {}
        with mock.patch.object(signal, "signal",
                               side_effect=lambda s, h:
                               manejadores.__setitem__(s, h)):
            sc._registrar_manejadores_senales()
        with mock.patch.object(sc, "_destruir_sesion_si_aplica") as destr, \
             mock.patch.object(sc, "_apagar_subprocesos"), \
             mock.patch.object(sc, "error"):
            with self.assertRaises(SystemExit):
                manejadores[signal.SIGINT](signal.SIGINT, None)
        destr.assert_called_once()


class TestIntegracionReAct(unittest.TestCase):
    """Sesión Docker en el bucle ReAct (crear al empezar, destruir al final)."""

    def setUp(self):
        ss._poner_nombre(None)
        self._restore = (sc._SESION_DOCKER_SOLICITADA, sc._SANDBOX_ACTIVO)
        sc._SANDBOX_ACTIVO = True
        self.addCleanup(self._restore_estado)

    def _restore_estado(self):
        sc._SESION_DOCKER_SOLICITADA, sc._SANDBOX_ACTIVO = self._restore
        ss._poner_nombre(None)

    def _agente(self, sesion_docker):
        return ra.ReactAgent(directorio=str(RAIZ), auto=True, max_iter=1,
                             proveedor="mock", sesion_docker=sesion_docker)

    def test_react_crea_y_destruye_la_sesion(self):
        agente = self._agente(sesion_docker=True)
        decision = {"accion": "finalizar", "pensamiento": "hecho",
                    "argumentos": {"resumen": "listo"}}
        with mock.patch.object(sc, "_asegurar_sesion_docker",
                               return_value="snap-session-x") as crear, \
             mock.patch.object(agente, "_pedir_decision",
                               return_value=decision), \
             mock.patch.object(sc, "_destruir_sesion_si_aplica") as destr:
            resultado = agente.ejecutar("tarea")
        self.assertTrue(resultado["ok"])
        crear.assert_called_once()
        destr.assert_called_once()

    def test_react_destruye_la_sesion_aun_si_el_llm_falla(self):
        agente = self._agente(sesion_docker=True)
        with mock.patch.object(sc, "_asegurar_sesion_docker",
                               return_value="snap-session-x"), \
             mock.patch.object(agente, "_pedir_decision",
                               side_effect=RuntimeError("LLM caído")), \
             mock.patch.object(sc, "_destruir_sesion_si_aplica") as destr:
            resultado = agente.ejecutar("tarea")
        self.assertFalse(resultado["ok"])
        destr.assert_called_once()

    def test_react_sin_sesion_docker_no_toca_la_sesion(self):
        agente = self._agente(sesion_docker=False)
        decision = {"accion": "finalizar", "pensamiento": "ok",
                    "argumentos": {"resumen": "listo"}}
        with mock.patch.object(sc, "_asegurar_sesion_docker") as crear, \
             mock.patch.object(agente, "_pedir_decision",
                               return_value=decision), \
             mock.patch.object(sc, "_destruir_sesion_si_aplica") as destr:
            agente.ejecutar("tarea")
        crear.assert_not_called()
        destr.assert_not_called()


if __name__ == "__main__":
    unittest.main()