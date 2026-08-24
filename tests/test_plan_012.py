#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del planificador de tareas (--plan) — v0.12.0."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc


def _args_base(**extra):
    base = dict(
        consulta="tarea de prueba", depurar=False, provider=None, modelo=None,
        git_commit=False, branch=None, directorio=".", test_loop=False,
        aider_opciones="", comando_test="flutter test", max_iteraciones=1,
        confirmar=False,
    )
    base.update(extra)
    return sc.argparse.Namespace(**base)


class TestNormalizarPasos(unittest.TestCase):
    def test_formato_envuelto(self):
        pasos = sc._normalizar_pasos({"pasos": [
            {"descripcion": "editar a.py", "accion": "editar",
             "archivos": ["a.py"]},
            {"descripcion": "correr tests", "accion": "ejecutar",
             "comando": "flutter test"},
        ]})
        self.assertEqual(len(pasos), 2)
        self.assertEqual(pasos[0]["accion"], "editar")
        self.assertEqual(pasos[1]["comando"], "flutter test")

    def test_descarta_invalidos(self):
        pasos = sc._normalizar_pasos([
            {"descripcion": "", "accion": "editar"},          # sin descripción
            {"descripcion": "x", "accion": "destruir"},       # acción inválida
            "texto suelto",                                    # no dict
            {"descripcion": "ok", "accion": "CONSULTAR"},     # mayúsculas ok
        ])
        self.assertEqual(len(pasos), 1)
        self.assertEqual(pasos[0]["accion"], "consultar")

    def test_entrada_basura(self):
        self.assertEqual(sc._normalizar_pasos(None), [])
        self.assertEqual(sc._normalizar_pasos(42), [])


class TestGenerarPlan(unittest.TestCase):
    def test_sin_clave_lanza_runtimeerror(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                sc._generar_plan("tarea", "groq")

    def test_generar_plan_con_openai_mock(self):
        """Camino completo con un cliente OpenAI falso (Ollama, sin clave)."""
        plan_json = json.dumps({"pasos": [
            {"descripcion": "paso 1", "accion": "ejecutar",
             "comando": "dir"}]})

        class FakeCompletions:
            def create(self, **kwargs):
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=plan_json))])

        class FakeChat:
            completions = FakeCompletions()

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.chat = FakeChat()

        fake_openai = SimpleNamespace(OpenAI=FakeOpenAI)
        original = sc.openai
        sc.openai = fake_openai
        try:
            pasos = sc._generar_plan("mi tarea", "ollama")
        finally:
            sc.openai = original
        self.assertEqual(len(pasos), 1)
        self.assertEqual(pasos[0]["descripcion"], "paso 1")


class TestGitPlan(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_no_es_repo_git_con_mock(self):
        with mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(128, "", "fatal: not a git repository")):
            self.assertFalse(sc._es_repo_git(str(self.dir_tmp)))
        with mock.patch.object(sc, "_ejecutar_comando",
                               return_value=(0, "true\n", "")):
            self.assertTrue(sc._es_repo_git(str(self.dir_tmp)))

    def test_crear_rama_fuera_de_repo_falla(self):
        with mock.patch.object(sc, "_es_repo_git", return_value=False):
            self.assertFalse(sc._git_crear_rama("rama-x", str(self.dir_tmp)))

    def test_commit_paso_sin_repo_es_ok(self):
        """Fuera de un repo, el paso tiene éxito y no se intenta commitear."""
        with mock.patch.object(sc, "_es_repo_git", return_value=False), \
             mock.patch.object(sc, "_ejecutar_comando") as ej:
            self.assertTrue(sc._git_commit_paso("algo", str(self.dir_tmp)))
        ej.assert_not_called()

    def test_commit_paso_en_repo_mock(self):
        """En repo (mockeado): git add . + git commit -m 'paso: ...'."""
        llamadas = []

        def fake_ejecutar(comando, directorio=".", timeout=120):
            llamadas.append(comando)
            if comando.startswith("git commit"):
                # Simular que sí había cambios.
                return (0, "", "")
            return (0, "", "")

        with mock.patch.object(sc, "_es_repo_git", return_value=True), \
             mock.patch.object(sc, "_ejecutar_comando",
                               side_effect=fake_ejecutar):
            self.assertTrue(sc._git_commit_paso('arreglar "login"',
                                                str(self.dir_tmp)))
        self.assertEqual(len(llamadas), 2)
        self.assertEqual(llamadas[0], "git add .")
        self.assertIn('git commit -m "paso: arreglar \'login\'"', llamadas[1])

    def test_commit_paso_sin_cambios_es_ok(self):
        with mock.patch.object(sc, "_es_repo_git", return_value=True), \
             mock.patch.object(
                 sc, "_ejecutar_comando",
                 side_effect=lambda c, d=".", timeout=120: (
                     1, "", "nothing to commit, working tree clean")
                 if c.startswith("git commit") else (0, "", "")):
            self.assertTrue(sc._git_commit_paso("sin cambios", str(self.dir_tmp)))


class TestEjecutarPasoPlan(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_paso_ejecutar_ok_y_fallo(self):
        ok, _ = sc._ejecutar_paso_plan(
            {"accion": "ejecutar", "descripcion": "eco",
             "comando": "cmd /c echo hola" if sys.platform.startswith("win")
             else "echo hola"},
            _args_base(), str(self.dir_tmp))
        self.assertTrue(ok)
        ok2, detalle = sc._ejecutar_paso_plan(
            {"accion": "ejecutar", "descripcion": "fallo",
             "comando": "cmd /c exit 5" if sys.platform.startswith("win")
             else "exit 5"},
            _args_base(), str(self.dir_tmp))
        self.assertFalse(ok2)
        self.assertIn("5", detalle)
        ok3, _ = sc._ejecutar_paso_plan(          # sin comando indicado
            {"accion": "ejecutar", "descripcion": "sin cmd", "comando": ""},
            _args_base(), str(self.dir_tmp))
        self.assertFalse(ok3)

    def test_paso_consultar_con_proveedor_mock(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="respuesta IA"):
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "consultar", "descripcion": "¿qué lib uso?"},
                _args_base(), str(self.dir_tmp))
        self.assertTrue(ok)
        self.assertEqual(detalle, "respuesta mostrada")

    def test_paso_editar_usa_orquestador(self):
        paso = {"accion": "editar", "descripcion": "arreglar login",
                "archivos": ["lib/login.dart"], "comando": ""}
        args = _args_base()
        with mock.patch("orquestador.Orquestador") as fake_orch_cls:
            orch = fake_orch_cls.return_value
            orch._planificar.return_value = ("vista_previa", self.dir_tmp,
                                             ["lib"], ["lib/login.dart"])
            orch.agente_editor.ejecutar_aider.return_value = True
            ok, detalle = sc._ejecutar_paso_plan(paso, args, str(self.dir_tmp))
        self.assertTrue(ok)
        self.assertIn("Aider", detalle)
        # La descripción del paso se usó como consulta del pipeline.
        self.assertEqual(orch._planificar.call_args[0][0].consulta,
                         "arreglar login")


class TestEjecutarPlanificador(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp),
            mock.patch.object(sc, "HISTORIAL_PATH",
                              self.dir_tmp / "historial.json"),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_sin_consulta_devuelve_1(self):
        self.assertEqual(
            sc._ejecutar_planificador(_args_base(consulta=None)), 1)

    def test_flujo_completo_con_mocks(self):
        pasos = [
            {"descripcion": "paso uno", "accion": "ejecutar", "comando": "x"},
            {"descripcion": "paso dos", "accion": "consultar"},
        ]
        guardados = []
        with mock.patch.object(sc, "_generar_plan", return_value=pasos) as gp, \
             mock.patch.object(sc, "_preguntar_si", return_value=True), \
             mock.patch("builtins.input", return_value="c"), \
             mock.patch.object(sc, "_ejecutar_paso_plan",
                               side_effect=[(True, "ok"), (True, "ok")]) as ep, \
             mock.patch.object(sc, "_guardar_historial",
                               side_effect=lambda e: guardados.append(e)):
            codigo = sc._ejecutar_planificador(_args_base())
        self.assertEqual(codigo, 0)
        gp.assert_called_once()
        self.assertEqual(ep.call_count, 2)
        self.assertEqual(len(guardados), 1)
        self.assertEqual(guardados[0]["tipo"], "plan")
        self.assertEqual(guardados[0]["resultado"], "éxito")
        self.assertEqual(len(guardados[0]["pasos"]), 2)

    def test_cancelacion_por_el_usuario(self):
        with mock.patch.object(sc, "_generar_plan", return_value=[
                {"descripcion": "p", "accion": "consultar"}]), \
             mock.patch.object(sc, "_preguntar_si", return_value=False):
            codigo = sc._ejecutar_planificador(_args_base())
        self.assertEqual(codigo, 0)   # cancelado limpio

    def test_plan_vacio_tras_reintentos_devuelve_1(self):
        with mock.patch.object(sc, "_generar_plan", return_value=[]):
            codigo = sc._ejecutar_planificador(_args_base())
        self.assertEqual(codigo, 1)


class TestFlagsPlanCLI(unittest.TestCase):
    def _parse(self, argv):
        return sc.crear_parser().parse_args(argv)

    def test_flag_plan(self):
        args = self._parse(["--plan", "mi tarea"])
        self.assertTrue(args.plan)
        self.assertEqual(args.consulta, "mi tarea")

    def test_git_commit_por_defecto_true(self):
        self.assertTrue(self._parse(["--plan", "x"]).git_commit)
        self.assertFalse(
            self._parse(["--plan", "x", "--no-git-commit"]).git_commit)

    def test_branch_opcional(self):
        args = self._parse(["--plan", "x", "--branch", "fix/checkout"])
        self.assertEqual(args.branch, "fix/checkout")
        self.assertIsNone(self._parse(["--plan", "x"]).branch)

    def test_version_es_1_2_0(self):
        self.assertEqual(sc.VERSION, "3.1.0")


if __name__ == "__main__":
    unittest.main()



