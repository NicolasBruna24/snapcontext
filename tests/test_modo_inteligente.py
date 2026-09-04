#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del modo inteligente por defecto (v6.23.0).

Cubre: detección de complejidad (chat/plan/react/react_paralelo),
configuración de defaults (--local, --mostrar-razonamiento, --auto,
--paralelo), compatibilidad con flags explícitos, resumen del plan,
reducción de confirmaciones, variable de entorno ``SNAPCONTEXT_MODO_DEFAULT``
y mensajes de usuario (🧠 / 💭 / 📋).
"""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snapcontext as sc       # noqa: E402

ENV_MODO = "SNAPCONTEXT_MODO_DEFAULT"


def _args_ns(**kw):
    """Namespace mínimo para las funciones de la capa de entrada."""
    return argparse.Namespace(**kw)


class TestDetectarModo(unittest.TestCase):
    """``_detectar_modo_operacion`` elige el modo según la consulta."""

    def _detectar(self, consulta, **flags):
        return sc._detectar_modo_operacion(consulta, _args_ns(**flags))

    def test_consulta_corta_sin_edicion_chat(self):
        self.assertEqual(self._detectar("hola")["modo"], "chat")
        self.assertEqual(self._detectar("explica que hace")["modo"], "chat")

    def test_editar_detecta_plan(self):
        for q in ("arregla el botón de pago",
                  "corrige el error del login",
                  "refactorizar el modulo de cobros",
                  "añadir índice a la tabla pedidos",
                  "cambiar el color del boton",
                  "eliminar la función obsoleta"):
            self.assertEqual(self._detectar(q)["modo"], "plan", q)

    def test_leer_detecta_react(self):
        for q in ("analiza el rendimiento del login",
                  "revisar el codigo",
                  "leer el archivo de config",
                  "auditar la seguridad de la api"):
            self.assertEqual(self._detectar(q)["modo"], "react", q)

    def test_consulta_larga_react_paralelo(self):
        q = " ".join(["paso"] * 51)          # >50 palabras
        self.assertEqual(self._detectar(q)["modo"], "react_paralelo")

    def test_multipaso_react_paralelo(self):
        q = "primero lee el archivo y luego ejecuta las pruebas"
        self.assertEqual(self._detectar(q)["modo"], "react_paralelo")

    def test_respeta_flags_explicitos(self):
        for flag in ("plan", "react", "auto"):
            det = self._detectar("arregla el botón", **{flag: True})
            self.assertIsNone(det["modo"], flag)

    def test_respeta_other_flag_pipeline(self):
        det = self._detectar("arregla el botón", vista_previa=True)
        self.assertIsNone(det["modo"])

    def test_sin_consulta_none(self):
        self.assertIsNone(self._detectar(None)["modo"])
        self.assertIsNone(self._detectar("  ")["modo"])

    def test_devuelve_razon_y_flags_extra(self):
        det = self._detectar("arregla el botón")
        self.assertTrue(det["razon"])
        self.assertEqual(det["flags_extra"]["plan"], True)


class TestConfigurarDefaults(unittest.TestCase):
    """``_configurar_comportamiento_por_defecto`` aplica defaults."""

    def _config(self, consulta, api=True, **flags):
        base = dict(consulta=consulta, local=False,
                    mostrar_razonamiento=False, auto=False, plan=False,
                    paralelo=1)
        base.update(flags)
        args = _args_ns(**base)
        with mock.patch.object(sc, "hay_api_key_configurada",
                               return_value=api):
            return sc._configurar_comportamiento_por_defecto(args)

    def test_forza_local_sin_api_key(self):
        self.assertTrue(self._config("hola", api=False).local)

    def test_no_sobrescribe_local_con_api(self):
        self.assertFalse(self._config("hola", api=True).local)

    def test_respeta_local_explicito_con_o_sin_api(self):
        self.assertTrue(self._config("hola", api=False, local=True).local)

    def test_mostrar_razonamiento_por_defecto(self):
        self.assertTrue(self._config("hola").mostrar_razonamiento)

    def test_plan_confirma_auto_y_flag_plan(self):
        args = self._config("arregla el botón")
        self.assertTrue(args.plan)
        self.assertTrue(args.auto)

    def test_react_paralelo_establece_paralelo_3(self):
        q = " ".join(["paso"] * 51)
        self.assertEqual(self._config(q).paralelo, 3)

    def test_respeta_flags_explicitos(self):
        args = self._config("arregla el botón", plan=True, auto=True)
        self.assertFalse(getattr(args, "_modo_inteligente", False))

    def test_marca_modo_detectado(self):
        args = self._config("revisar login")
        self.assertTrue(args._modo_inteligente)
        self.assertEqual(args._modo_detectado, "react")
class TestAplicarModo(unittest.TestCase):
    """``_aplicar_modo_inteligente``, entorno y mensajes."""

    def tearDown(self):
        os.environ.pop(ENV_MODO, None)

    def test_entorno_manual_no_aplica(self):
        os.environ[ENV_MODO] = "manual"
        args = sc.crear_parser().parse_args(["arregla el botón"])
        with mock.patch.object(sc, "hay_api_key_configurada",
                               return_value=True):
            args = sc._aplicar_modo_inteligente(args)
        self.assertFalse(getattr(args, "_modo_inteligente", False))
        self.assertFalse(args.plan)

    def test_entorno_inteligente_aplica(self):
        os.environ[ENV_MODO] = "inteligente"
        args = sc.crear_parser().parse_args(["arregla el botón"])
        with mock.patch.object(sc, "hay_api_key_configurada",
                               return_value=True):
            args = sc._aplicar_modo_inteligente(args)
        self.assertTrue(args._modo_inteligente)
        self.assertEqual(args._modo_detectado, "plan")

    def test_entorno_por_defecto_es_inteligente(self):
        os.environ.pop(ENV_MODO, None)      # default "inteligente"
        args = sc.crear_parser().parse_args(["arregla el botón"])
        with mock.patch.object(sc, "hay_api_key_configurada",
                               return_value=True):
            sc._aplicar_modo_inteligente(args)
        self.assertTrue(args._modo_inteligente)

    def test_flags_explicitos_respetados(self):
        args = sc.crear_parser().parse_args(["--plan", "tarea"])
        with mock.patch.object(sc, "hay_api_key_configurada",
                               return_value=True):
            args = sc._aplicar_modo_inteligente(args)
        self.assertFalse(getattr(args, "_modo_inteligente", False))

    def test_muestra_mensaje_modo_inteligente(self):
        args = sc.crear_parser().parse_args(["arregla el botón"])
        with mock.patch.object(sc, "info") as info, \
                mock.patch.object(sc, "hay_api_key_configurada",
                                  return_value=True):
            sc._aplicar_modo_inteligente(args)
        textos = [(c.args[0] if c.args else "") for c in info.call_args_list]
        unidos = " ".join(textos)
        self.assertIn("🧠 Modo inteligente activado", unidos)
        self.assertIn("💭", unidos)

    def test_sin_consulta_no_aplica(self):
        args = sc.crear_parser().parse_args([])
        with mock.patch.object(sc, "hay_api_key_configurada",
                               return_value=True):
            args = sc._aplicar_modo_inteligente(args)
        self.assertFalse(getattr(args, "_modo_inteligente", False))
class TestPlanResumido(unittest.TestCase):
    """``_mostrar_plan_resumido`` condensa el plan."""

    def test_vacio(self):
        self.assertEqual(sc._mostrar_plan_resumido([]), "")
        self.assertEqual(sc._mostrar_plan_resumido(None), "")

    def test_plan_corto(self):
        plan = [{"descripcion": "leer login"}, {"descripcion": "corregir"},
                {"descripcion": "ejecutar pruebas"}]
        resumen = sc._mostrar_plan_resumido(plan)
        self.assertTrue(resumen.startswith("Voy a:"))
        self.assertIn("1) leer login", resumen)
        self.assertIn("3) ejecutar pruebas", resumen)

    def test_limita_a_cinco(self):
        plan = [{"descripcion": f"paso {i}"} for i in range(7)]
        resumen = sc._mostrar_plan_resumido(plan)
        self.assertIn("5) paso 4", resumen)
        self.assertIn("y 2 más", resumen)
        self.assertNotIn("6)", resumen)


class TestReduccionConfirmaciones(unittest.TestCase):
    """El modo inteligente ejecuta el plan con --auto y muestra el 📋 resumen."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        for attr, nombre in (("CONFIG_DIR", "d"),
                             ("HISTORIAL_PATH", "historial.json")):
            p = mock.patch.object(sc, attr, self.dir_tmp / nombre)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _pasos(self):
        return [{"descripcion": "a", "accion": "consultar"},
                {"descripcion": "b", "accion": "consultar"}]

    def _args(self, inteligente=True, auto=True, paralelo=1):
        base = dict(consulta="arregla el botón", depurar=False, provider=None,
                    modelo=None, git_commit=False, branch=None,
                    directorio=".", test_loop=False, aider_opciones="",
                    comando_test="", max_iteraciones=1, confirmar=True,
                    auto=auto, paralelo=paralelo)
        if inteligente:
            base["_modo_inteligente"] = True
            base["_modo_detectado"] = "plan"
        return sc.argparse.Namespace(**base)

    def test_plan_auto_muestra_resumen_y_no_confirmacion(self):
        pasos = self._pasos()
        with mock.patch.object(sc, "_generar_plan", return_value=pasos), \
                mock.patch.object(sc, "_preguntar_si",
                                  side_effect=AssertionError(
                                      "no debe confirmar paso a paso")), \
                mock.patch.object(sc, "_ejecutar_paso_plan",
                                  return_value=(True, "ok")), \
                mock.patch.object(sc, "_guardar_historial",
                                  return_value=True), \
                mock.patch.object(sc, "_aprender_de_tarea",
                                  return_value=None), \
                mock.patch.object(sc, "info") as info:
            codigo = sc._ejecutar_planificador(self._args(inteligente=True))
        self.assertEqual(codigo, 0)
        textos = [(c.args[0] if c.args else "") for c in info.call_args_list]
        self.assertTrue(any("📋 Plan:" in t for t in textos))

    def test_sin_modo_inteligente_no_muestra_resumen(self):
        pasos = self._pasos()
        with mock.patch.object(sc, "_generar_plan", return_value=pasos), \
                mock.patch.object(sc, "_preguntar_si",
                                  side_effect=AssertionError(
                                      "no debe confirmar paso a paso")), \
                mock.patch.object(sc, "_ejecutar_paso_plan",
                                  return_value=(True, "ok")), \
                mock.patch.object(sc, "_guardar_historial",
                                  return_value=True), \
                mock.patch.object(sc, "_aprender_de_tarea",
                                  return_value=None), \
                mock.patch.object(sc, "info") as info:
            codigo = sc._ejecutar_planificador(self._args(inteligente=False))
        self.assertEqual(codigo, 0)
        textos = [(c.args[0] if c.args else "") for c in info.call_args_list]
        self.assertFalse(any("📋 Plan:" in t for t in textos))


if __name__ == "__main__":
    unittest.main()