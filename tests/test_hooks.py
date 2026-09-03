#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de Hooks / lifecycle events (v6.22.0): registro, ejecución en orden,
aborto, modificación de contexto, carga desde plugins y archivos, flags
``--hooks``/``--hook-list`` e integración con ReAct/planificador/MCP."""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc  # noqa: E402


def _limpiar():
    """Vacía el registro global de hooks antes de cada test."""
    import hooks as _h
    _h.limpiar_hooks()
    _h.activar()
    return _h


class TestRegistroHooks(unittest.TestCase):
    def setUp(self):
        self.hooks = _limpiar()

    def test_registrar_y_recuperar(self):
        def f(ctx):
            return None
        self.assertTrue(self.hooks.registrar_hook("session_start", f))
        self.assertEqual(len(self.hooks.hooks_de("session_start")), 1)

    def test_evento_invalido_devuelve_false(self):
        def f(ctx):
            return None
        self.assertFalse(self.hooks.registrar_hook("no_existe", f))

    def test_orden_por_prioridad(self):
        def baja(ctx):
            return None
        def alta(ctx):
            return None
        self.hooks.registrar_hook("before_tool_use", baja, prioridad=0)
        self.hooks.registrar_hook("before_tool_use", alta, prioridad=10)
        ganchos = self.hooks.hooks_de("before_tool_use")
        self.assertEqual(ganchos[0]["prioridad"], 10)
        self.assertEqual(ganchos[1]["prioridad"], 0)

    def test_origen_se_registra(self):
        def f(ctx):
            return None
        self.hooks.registrar_hook("session_end", f, origen="plugin-x")
        g = self.hooks.hooks_de("session_end")
        self.assertEqual(g[0]["origen"], "plugin-x")

    def test_limpiar_especifico_y_global(self):
        def f(ctx):
            return None
        self.hooks.registrar_hook("session_start", f)
        self.hooks.registrar_hook("session_end", f)
        self.hooks.limpiar_hooks("session_start")
        self.assertEqual(len(self.hooks.hooks_de("session_start")), 0)
        self.assertEqual(len(self.hooks.hooks_de("session_end")), 1)
        self.hooks.limpiar_hooks()
        self.assertEqual(self.hooks.listar_hooks(), {})


class TestEjecucionHooks(unittest.TestCase):
    def setUp(self):
        self.hooks = _limpiar()

    def test_ejecutar_sin_hooks_no_aborta(self):
        abortado, ctx = self.hooks.ejecutar_hook("session_start", {"a": 1})
        self.assertFalse(abortado)
        self.assertEqual(ctx["a"], 1)

    def test_hook_modifica_contexto(self):
        def enriquecer(ctx):
            ctx["nuevo"] = 42
            return {"nuevo": 42}
        self.hooks.registrar_hook("session_start", enriquecer)
        _, ctx = self.hooks.ejecutar_hook("session_start", {})
        self.assertEqual(ctx.get("nuevo"), 42)

    def test_hook_abortar(self):
        def cancelar(ctx):
            return {"abort": True, "razon": "no permitido"}
        self.hooks.registrar_hook("before_tool_use", cancelar)
        abortado, _ = self.hooks.ejecutar_hook("before_tool_use",
                                              {"herramienta": "x"})
        self.assertTrue(abortado)

    def test_continua_tras_no_abortar(self):
        def escribir(ctx):
            ctx["contador"] = ctx.get("contador", 0) + 1
            return {"contador": ctx["contador"]}
        self.hooks.registrar_hook("session_start", escribir)
        _, ctx = self.hooks.ejecutar_hook("session_start", {})
        self.assertEqual(ctx["contador"], 1)

    def test_error_en_hook_no_rompe(self):
        def falla(ctx):
            raise RuntimeError("boom")
        def ok(ctx):
            ctx["ok"] = True
            return {"ok": True}
        self.hooks.registrar_hook("session_start", falla)
        self.hooks.registrar_hook("session_start", ok)
        _, ctx = self.hooks.ejecutar_hook("session_start", {})
        self.assertTrue(ctx.get("ok"))

    def test_hooks_multiples_todos_se_ejecutan(self):
        llamadas = []
        def h1(ctx):
            llamadas.append("h1")
        def h2(ctx):
            llamadas.append("h2")
        self.hooks.registrar_hook("session_start", h1)
        self.hooks.registrar_hook("session_start", h2)
        self.hooks.ejecutar_hook("session_start", {})
        self.assertIn("h1", llamadas)
        self.assertIn("h2", llamadas)

    def test_desactivar_no_ejecuta(self):
        def f(ctx):
            ctx["ejecutado"] = True
        self.hooks.registrar_hook("session_start", f)
        self.hooks.desactivar()
        _, ctx = self.hooks.ejecutar_hook("session_start", {})
        self.assertNotIn("ejecutado", ctx)
        self.hooks.activar()

    def test_activar_reactiva(self):
        def f(ctx):
            ctx["vivo"] = True
        self.hooks.registrar_hook("session_start", f)
        self.hooks.desactivar()
        self.hooks.activar()
        _, ctx = self.hooks.ejecutar_hook("session_start", {})
        self.assertTrue(ctx.get("vivo"))


class TestListarHooks(unittest.TestCase):
    def setUp(self):
        self.hooks = _limpiar()

    def test_listar_vacio(self):
        self.assertEqual(self.hooks.listar_hooks(), {})

    def test_listar_muestra_evento(self):
        def f(ctx):
            return None
        self.hooks.registrar_hook("session_start", f, prioridad=5,
                                  origen="test")
        registro = self.hooks.listar_hooks()
        self.assertIn("session_start", registro)
        self.assertEqual(registro["session_start"][0]["prioridad"], 5)

    def test_texto_listar_hooks(self):
        def f(ctx):
            return None
        self.hooks.registrar_hook("session_start", f)
        texto = self.hooks._listar_hooks_texto()
        self.assertIn("Hooks registrados", texto)


class TestEventosDisponibles(unittest.TestCase):
    def test_eventos_base_presentes(self):
        import hooks as _h
        for evento in ["before_tool_use", "after_tool_use",
                       "before_plan_step", "after_plan_step",
                       "session_start", "session_end",
                       "before_react_iteration", "after_react_iteration"]:
            self.assertIn(evento, _h.EVENTOS)


class TestCargaDesdeArchivos(unittest.TestCase):
    def setUp(self):
        self.hooks = _limpiar()
        self.dir = tempfile.mkdtemp(prefix="sc_hooks_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_cargar_archivo_python(self):
        ruta = Path(self.dir, "session_start.py")
        ruta.write_text(
            "def ejecutar(contexto):\n"
            "    contexto['desde_archivo'] = True\n"
            "    return {'desde_archivo': True}\n",
            encoding="utf-8")
        self.hooks.cargar_hooks_desde_archivos(self.dir)
        _, ctx = self.hooks.ejecutar_hook("session_start", {})
        self.assertTrue(ctx.get("desde_archivo"))

    def test_cargar_archivo_vacio_no_rompe(self):
        ruta = Path(self.dir, "vacio.py")
        ruta.write_text("# sin handler\n", encoding="utf-8")
        self.hooks.cargar_hooks_desde_archivos(self.dir)
        abortado, _ = self.hooks.ejecutar_hook("session_start", {})
        self.assertFalse(abortado)

