#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del editor propio como modo por defecto (v4.1.0).

Cubre: flag --editor por defecto, heurística de estrategias (AST → parche →
sobrescritura), prompts concisos para Ollama/--modelo-ligero, resolución
interactiva de conflictos de parche y mensajes de error con registro.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapcontext as sc
from agentes import AgenteEditorPropio, _prompts_concisos


class TestFlagPorDefecto(unittest.TestCase):
    def test_editor_por_defecto_es_propio(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertEqual(args.editor, "propio")

    def test_aider_sigue_disponible(self):
        args = sc.crear_parser().parse_args(
            ["--editor", "aider", "consulta"])
        self.assertEqual(args.editor, "aider")

    def test_flag_modelo_ligero_existe(self):
        args = sc.crear_parser().parse_args(["consulta", "--modelo-ligero"])
        self.assertTrue(args.modelo_ligero)
        defecto = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(defecto.modelo_ligero)


class TestPromptsConcisos(unittest.TestCase):
    def test_ollama_activa_conciso(self):
        self.assertTrue(_prompts_concisos("ollama", False))

    def test_proveedor_remoto_no_conciso(self):
        self.assertFalse(_prompts_concisos("gemini", False))
        self.assertFalse(_prompts_concisos(None, False))

    def test_modelo_ligero_fuerza_conciso(self):
        self.assertTrue(_prompts_concisos("gemini", True))


class TestHeuristicaEstrategias(unittest.TestCase):
    def setUp(self):
        self.agente = AgenteEditorPropio()

    def test_cadena_auto_estructural_incluye_ast_primero(self):
        with mock.patch.object(sc, "_ast_disponible", return_value=True):
            cadena = self.agente._cadena_modos(
                "modulo.py", "renombra la función procesar a manejar",
                "auto")
        self.assertEqual(cadena[0], "ast")
        self.assertIn("parche", cadena)
        self.assertEqual(cadena[-1], "sobrescribir")

    def test_cadena_sin_ast_empieza_por_parche(self):
        with mock.patch.object(sc, "_ast_disponible", return_value=False):
            cadena = self.agente._cadena_modos(
                "app.js", "añadir validación al formulario", "auto")
        self.assertEqual(cadena[0], "parche")
        self.assertNotIn("ast", cadena)

    def test_ejecutar_intenta_estrategias_en_orden_y_falla_claro(self):
        agente = AgenteEditorPropio()
        tmp = tempfile.mkdtemp(prefix="sc410_")
        try:
            llamadas = []
            with mock.patch.object(sc, "_ast_disponible",
                                   return_value=True), \
                    mock.patch.object(agente, "editar_ast",
                                      side_effect=lambda *a, **k:
                                      llamadas.append("ast") or False), \
                    mock.patch.object(agente, "_aplicar_modo_parche",
                                      side_effect=lambda *a, **k:
                                      llamadas.append("parche") or False), \
                    mock.patch.object(agente, "_aplicar_modo_sobrescribir",
                                      side_effect=lambda *a, **k:
                                      llamadas.append("sobrescribir")
                                      or False):
                ok = agente.ejecutar(["m.py"], "renombra foo a bar",
                                     directorio=tmp, auto=True)
            self.assertFalse(ok)
            # Orden AST → parche → sobrescritura.
            self.assertEqual(llamadas, ["ast", "parche", "sobrescribir"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestConflictosYErrores(unittest.TestCase):
    def setUp(self):
        self.agente = AgenteEditorPropio()
        self.tmp = tempfile.mkdtemp(prefix="sc410c_")
        (Path(self.tmp) / "m.py").write_text("x = 1\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_menu_conflicto_aplicar_de_todas_formas(self):
        with mock.patch.object(sc, "_menu_conflicto_parche",
                               return_value="a"), \
                mock.patch.object(self.agente, "aplicar_parche",
                                  return_value=False), \
                mock.patch.object(self.agente, "sobrescribir",
                                  return_value=True) as sob:
            resultado = self.agente._aplicar_con_conflicto(
                "m.py", "--- diff ---", self.tmp, "x = 1\n",
                preview="y = 2\n")
        self.assertEqual(resultado, "ok")
        sob.assert_called_once_with("m.py", "y = 2\n", self.tmp)

    def test_menu_conflicto_cancelar_conserva_original(self):
        with mock.patch.object(sc, "_menu_conflicto_parche",
                               return_value="c"), \
                mock.patch.object(self.agente, "aplicar_parche",
                                  return_value=False) as aplicar:
            resultado = self.agente._aplicar_con_conflicto(
                "m.py", "--- diff ---", self.tmp, "x = 1\n")
        self.assertEqual(resultado, "cancelar")
        # Solo se intentó una vez: la original.
        self.assertEqual(aplicar.call_count, 1)
        self.assertEqual((Path(self.tmp) / "m.py").read_text(
            encoding="utf-8"), "x = 1\n")

    def test_auto_no_muestra_menu_y_devuelve_reintentar(self):
        with mock.patch.object(sc, "_menu_conflicto_parche") as menu, \
                mock.patch.object(self.agente, "aplicar_parche",
                                  return_value=False):
            resultado = self.agente._aplicar_con_conflicto(
                "m.py", "--- diff ---", self.tmp, "x = 1\n", auto=True)
        self.assertEqual(resultado, "reintentar")
        menu.assert_not_called()

    def test_fallo_registra_en_logs(self):
        logs_tmp = Path(tempfile.mkdtemp(prefix="sc410logs_"))
        original_config = sc.CONFIG_DIR
        sc.CONFIG_DIR = logs_tmp
        try:
            sc._registrar_fallo_editor("m.py", "tarea imposible",
                                       ["parche", "sobrescribir"],
                                       "sin cambio válido")
            log = logs_tmp / "logs" / "editor_fallos.log"
            self.assertTrue(log.is_file())
            contenido = log.read_text(encoding="utf-8")
            self.assertIn("parche → sobrescribir", contenido)
            self.assertIn("tarea imposible", contenido)
        finally:
            sc.CONFIG_DIR = original_config
            shutil.rmtree(logs_tmp, ignore_errors=True)

    def test_mensaje_error_contiene_sugerencia_aider(self):
        agente = AgenteEditorPropio()
        tmp = tempfile.mkdtemp(prefix="sc410_")
        try:
            capturado = {}
            with mock.patch.object(sc, "_ast_disponible",
                                   return_value=False), \
                    mock.patch.object(agente, "_aplicar_modo_parche",
                                      return_value=False), \
                    mock.patch.object(agente, "_aplicar_modo_sobrescribir",
                                      return_value=False), \
                    mock.patch.object(sc, "error",
                                      side_effect=lambda t:
                                      capturado.setdefault("texto", t)):
                agente.ejecutar(["m.py"], "tarea", directorio=tmp, auto=True)
            texto = capturado.get("texto", "")
            self.assertIn("no pudo completar la edición", texto)
            self.assertIn("--editor aider", texto)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
