#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del modo autónomo (--auto) — v0.17.0."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc


def _args_base(**extra):
    base = dict(
        consulta="tarea", depurar=False, provider=None, modelo=None,
        git_commit=False, branch=None, directorio=".", test_loop=False,
        aider_opciones="", comando_test="flutter test", max_iteraciones=1,
        confirmar=True, auto=False,
    )
    base.update(extra)
    return sc.argparse.Namespace(**base)


class TestPermisoRecordado(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        for attr, nombre in (("CONFIG_DIR", "d"),
                             ("PERMISOS_PATH", "permisos.json")):
            p = mock.patch.object(sc, attr, self.dir_tmp / nombre)
            p.start()
            self.addCleanup(p.stop)

    def test_sin_preferencia_devuelve_none(self):
        self.assertIsNone(sc._permiso_recordado("ejecutar"))

    def test_preferencias_guardadas(self):
        sc._guardar_permiso("editar", "nunca")
        sc._guardar_permiso("ejecutar", "siempre")
        self.assertIs(sc._permiso_recordado("editar"), False)
        self.assertIs(sc._permiso_recordado("ejecutar"), True)


class TestPasoPlanAuto(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp),
            mock.patch.object(sc, "PERMISOS_PATH",
                              self.dir_tmp / "permisos.json"),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_auto_permite_sin_preguntar_si_no_hay_preferencia(self):
        args = _args_base(auto=True)
        comando = ("cmd /c echo hola" if sys.platform.startswith("win")
                   else "echo hola")
        with mock.patch.object(sc, "_confirmar_accion") as conf:
            ok, _ = sc._ejecutar_paso_plan(
                {"accion": "ejecutar", "descripcion": "eco",
                 "comando": comando}, args, str(self.dir_tmp))
        conf.assert_not_called()          # --auto no pregunta paso a paso
        self.assertTrue(ok)

    def test_auto_respeta_nunca_guardado(self):
        args = _args_base(auto=True)
        sc._guardar_permiso("ejecutar", "nunca")
        with mock.patch.object(sc, "_confirmar_accion") as conf, \
             mock.patch.object(sc, "_ejecutar_comando") as ej:
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "ejecutar", "descripcion": "prohibido",
                 "comando": "cmd /c echo x"}, args, str(self.dir_tmp))
        conf.assert_not_called()
        ej.assert_not_called()
        self.assertFalse(ok)
        self.assertEqual(detalle, "denegado por permisos guardados")

    def test_auto_respeta_siempre_guardado(self):
        args = _args_base(auto=True)
        sc._guardar_permiso("ejecutar", "siempre")
        comando = ("cmd /c echo hola" if sys.platform.startswith("win")
                   else "echo hola")
        with mock.patch.object(sc, "_confirmar_accion") as conf:
            ok, _ = sc._ejecutar_paso_plan(
                {"accion": "ejecutar", "descripcion": "permitido",
                 "comando": comando}, args, str(self.dir_tmp))
        conf.assert_not_called()
        self.assertTrue(ok)


class TestPlanificadorAuto(unittest.TestCase):
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

    PASOS = [
        {"descripcion": "paso uno", "accion": "consultar"},
        {"descripcion": "paso dos", "accion": "consultar"},
    ]

    def test_auto_salta_confirmaciones_y_menues(self):
        """Con --auto no se pide confirmación del plan ni menú por paso."""
        with mock.patch.object(sc, "_generar_plan", return_value=self.PASOS), \
             mock.patch.object(sc, "_preguntar_si",
                               side_effect=AssertionError("no debía preguntar")), \
             mock.patch.object(sc, "_ejecutar_paso_plan",
                               return_value=(True, "ok")) as ep, \
             mock.patch.object(sc, "_guardar_historial", return_value=True):
            codigo = sc._ejecutar_planificador(_args_base(auto=True))
        self.assertEqual(codigo, 0)
        self.assertEqual(ep.call_count, 2)      # ambos pasos sin menú

    def test_reintentos_automaticos_hasta_exito(self):
        """Un paso fallido se reintenta (hasta 3) y luego continúa."""
        efectos = [(False, "fallo1"), (False, "fallo2"), (True, "ok"),
                   (True, "ok")]
        with mock.patch.object(sc, "_generar_plan", return_value=self.PASOS), \
             mock.patch.object(sc, "_ejecutar_paso_plan",
                               side_effect=efectos) as ep:
            codigo = sc._ejecutar_planificador(_args_base(auto=True))
        self.assertEqual(codigo, 0)
        # Paso 1: 3 intentos (2 fallos + 1 éxito); paso 2: 1 intento.
        self.assertEqual(ep.call_count, 4)

    def test_agota_tres_intentos_y_continua(self):
        """Tras 3 fallos del paso 1 se pasa al paso 2 (que tiene éxito)."""
        efectos = [(False, f"fallo{i}") for i in range(3)] + [(True, "ok")]
        guardados = []
        with mock.patch.object(sc, "_generar_plan", return_value=self.PASOS), \
             mock.patch.object(sc, "_ejecutar_paso_plan", side_effect=efectos), \
             mock.patch.object(sc, "_guardar_historial",
                               side_effect=lambda e: guardados.append(e)):
            codigo = sc._ejecutar_planificador(_args_base(auto=True))
        # El plan acaba "parcial" (paso 1 falló) → código 1, pero avanzó.
        self.assertEqual(codigo, 1)
        entrada = guardados[-1]
        self.assertEqual(entrada["pasos"][0]["intentos"], 3)
        self.assertEqual(entrada["resultado"], "parcial")

    def test_sin_auto_se_mantiene_el_comportamiento_interactivo(self):
        """Sin --auto: confirmación inicial + menú por paso como en 0.12."""
        with mock.patch.object(sc, "_generar_plan", return_value=[
                self.PASOS[0]]), \
             mock.patch.object(sc, "_preguntar_si", return_value=True) as ps, \
             mock.patch("builtins.input", side_effect=["r", "r", "c"]), \
             mock.patch.object(sc, "_ejecutar_paso_plan",
                               side_effect=[(True, "ok"), (True, "ok2"),
                                            (True, "ok3")]) as ep:
            codigo = sc._ejecutar_planificador(_args_base(auto=False))
        self.assertEqual(codigo, 0)
        ps.assert_called_once()          # confirmación inicial presente
        self.assertEqual(ep.call_count, 3)   # r → reintento manual x2


class TestFlagsAutoCli(unittest.TestCase):
    def _parse(self, argv):
        return sc.crear_parser().parse_args(argv)

    def test_auto_por_defecto_false(self):
        self.assertFalse(self._parse(["--plan", "x"]).auto)

    def test_flag_auto_activo(self):
        args = self._parse(["--plan", "x", "--auto"])
        self.assertTrue(args.auto)
        self.assertTrue(args.plan)

    def test_version_es_1_2_0(self):
        self.assertEqual(sc.VERSION, "6.8.0")


if __name__ == "__main__":
    unittest.main()
