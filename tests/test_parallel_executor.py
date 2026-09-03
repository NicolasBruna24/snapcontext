#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del ejecutor genérico en paralelo (v6.20.0)."""

import argparse
import io
import os
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parallel_executor as pe            # noqa: E402
import snapcontext as sc                  # noqa: E402
import ui                                 # noqa: E402


def _silencio():
    return redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())


def _tarea(nombre, funcion=None, dependencias=None, **kwargs):
    return {"nombre": nombre, "funcion": funcion or (lambda: "ok"),
            "dependencias": dependencias or [], **kwargs}


class TestResolverWorkers(unittest.TestCase):
    def test_paralelo_1_es_secuencial(self):
        self.assertEqual(pe.resolver_workers(1), 1)

    def test_paralelo_cero_usa_nucleos(self):
        self.assertGreaterEqual(pe.resolver_workers(0), 2)

    def test_paralelo_negativo_es_secuencial(self):
        self.assertEqual(pe.resolver_workers(-5), 1)

    def test_paralelo_invalido_es_secuencial(self):
        self.assertEqual(pe.resolver_workers("abc"), 1)

    def test_paralelo_positivo_se_respeta(self):
        self.assertEqual(pe.resolver_workers(4), 4)


class TestEjecutarTarea(unittest.TestCase):
    def test_ok_devuelve_resultado(self):
        res = pe._ejecutar_tarea(_tarea("a", lambda: 42))
        self.assertTrue(res["ok"])
        self.assertEqual(res["resultado"], 42)

    def test_excepcion_capturada(self):
        def explota():
            raise ValueError("boom")
        res = pe._ejecutar_tarea(_tarea("a", explota))
        self.assertFalse(res["ok"])
        self.assertIn("boom", res["error"])

    def test_args_y_kwargs(self):
        res = pe._ejecutar_tarea(
            _tarea("a", lambda x, y=0: x + y, args=(2,), kwargs={"y": 3}))
        self.assertEqual(res["resultado"], 5)


class TestParaleloSimple(unittest.TestCase):
    def test_lista_vacia(self):
        self.assertEqual(
            pe.ParallelExecutor(4).ejecutar_paralelo([]), [])

    def test_orden_de_resultados_preservado(self):
        tareas = [_tarea(f"t{i}", lambda i=i: i) for i in range(6)]
        res = pe.ParallelExecutor(4).ejecutar_paralelo(tareas)
        self.assertEqual([r["nombre"] for r in res],
                         [f"t{i}" for i in range(6)])
        self.assertEqual([r["resultado"] for r in res], list(range(6)))

    def test_un_solo_trabajador_es_secuencial(self):
        tareas = [_tarea(f"t{i}", lambda i=i: i) for i in range(3)]
        res = pe.ParallelExecutor(1).ejecutar_paralelo(tareas)
        self.assertEqual(len(res), 3)
        self.assertTrue(all(r["ok"] for r in res))

    def test_excepcion_no_aborta_al_resto(self):
        def explota():
            raise RuntimeError("kaboom")
        tareas = [_tarea("mala", explota), _tarea("buena", lambda: 1)]
        res = pe.ParallelExecutor(4).ejecutar_paralelo(tareas)
        self.assertFalse(res[0]["ok"])
        self.assertTrue(res[1]["ok"])

    def test_concurrencia_limitada_por_workers(self):
        en_ejecucion = {"actual": 0, "max": 0}
        candado = threading.Lock()

        def trabajoso():
            with candado:
                en_ejecucion["actual"] += 1
                en_ejecucion["max"] = max(en_ejecucion["max"],
                                          en_ejecucion["actual"])
            time.sleep(0.05)
            with candado:
                en_ejecucion["actual"] -= 1
            return True

        tareas = [_tarea(f"t{i}", trabajoso) for i in range(6)]
        pe.ParallelExecutor(2).ejecutar_paralelo(tareas)
        self.assertLessEqual(en_ejecucion["max"], 2)

    def test_paralelismo_real_mide_tiempo(self):
        def lento():
            time.sleep(0.15)
            return True
        tareas = [_tarea(f"t{i}", lento) for i in range(4)]
        inicio = time.monotonic()
        res = pe.ParallelExecutor(4).ejecutar_paralelo(tareas)
        duracion = time.monotonic() - inicio
        self.assertTrue(all(r["ok"] for r in res))
        self.assertLess(duracion, 0.55)   # secuencial sería ~0.6


class TestDependencias(unittest.TestCase):
    def test_dependencia_bloquea_inicio(self):
        orden = []
        tareas = [
            _tarea("primera", lambda: orden.append("a") or "A"),
            _tarea("segunda", lambda: orden.append("b") or "B",
                   dependencias=["primera"]),
        ]
        res = pe.ParallelExecutor(4).ejecutar_paralelo(tareas)
        self.assertEqual(orden, ["a", "b"])
        self.assertTrue(all(r["ok"] for r in res))

    def test_dependencia_fallida_omite_dependiente(self):
        def explota():
            raise RuntimeError("fallo base")
        ejecutada = []
        tareas = [
            _tarea("base", explota),
            _tarea("dependiente", lambda: ejecutada.append(1),
                   dependencias=["base"]),
        ]
        res = pe.ParallelExecutor(4).ejecutar_paralelo(tareas)
        self.assertEqual(ejecutada, [])
        self.assertFalse(res[0]["ok"])
        self.assertFalse(res[1]["ok"])
        self.assertEqual(res[1].get("estado"), "omitida")

    def test_fallo_transitivo_omite_nietos(self):
        def explota():
            raise RuntimeError("raiz")
        tareas = [
            _tarea("raiz", explota),
            _tarea("media", lambda: "ok", dependencias=["raiz"]),
            _tarea("hoja", lambda: "ok", dependencias=["media"]),
        ]
        res = pe.ParallelExecutor(4).ejecutar_paralelo(tareas)
        self.assertEqual([r["ok"] for r in res], [False, False, False])

    def test_ciclo_de_dependencias_no_cuelga(self):
        tareas = [
            _tarea("a", lambda: 1, dependencias=["b"]),
            _tarea("b", lambda: 2, dependencias=["a"]),
        ]
        res = pe.ParallelExecutor(4).ejecutar_paralelo(tareas)
        self.assertEqual(len(res), 2)
        self.assertFalse(all(r["ok"] for r in res))

    def test_dependencia_secuencial_tambien_funciona(self):
        orden = []
        tareas = [
            _tarea("p", lambda: orden.append("p")),
            _tarea("s", lambda: orden.append("s"), dependencias=["p"]),
        ]
        res = pe.ParallelExecutor(1).ejecutar_paralelo(tareas)
        self.assertEqual(orden, ["p", "s"])
        self.assertTrue(all(r["ok"] for r in res))


class TestMensajesUsuario(unittest.TestCase):
    def test_mensaje_inicio_paralelo(self):
        buf = io.StringIO()
        with mock.patch.object(ui, "mostrar_estado",
                               side_effect=lambda m, emoji="": buf.write(m)):
            pe.ParallelExecutor(4).ejecutar_paralelo(
                [_tarea("a"), _tarea("b")])
        self.assertIn("🚀 Ejecutando 2 tareas en paralelo", buf.getvalue())

    def test_mensaje_tarea_completada(self):
        buf = io.StringIO()
        with mock.patch.object(ui, "mostrar_estado",
                               side_effect=lambda m, emoji="": buf.write(m)):
            pe.ParallelExecutor(4).ejecutar_paralelo([_tarea("scout")])
        self.assertIn("✅ Tarea scout completada", buf.getvalue())

    def test_mensaje_tarea_fallo(self):
        buf = io.StringIO()

        def explota():
            raise RuntimeError("boom")
        with mock.patch.object(ui, "mostrar_error",
                               side_effect=lambda m: buf.write(m)):
            pe.ParallelExecutor(4).ejecutar_paralelo(
                [_tarea("debugger", explota)])
        self.assertIn("❌ Tarea debugger falló", buf.getvalue())

    def test_modo_secuencial_anuncia_tarea_a_tarea(self):
        buf = io.StringIO()
        with mock.patch.object(ui, "mostrar_estado",
                               side_effect=lambda m, emoji="": buf.write(m)):
            pe.ParallelExecutor(1).ejecutar_paralelo([_tarea("a")])
        self.assertIn("✅ Tarea a completada", buf.getvalue())


class TestIntegracionSupervisorYCLI(unittest.TestCase):
    def _supervisor(self, max_parallel):
        import multi_agent as ma
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"provider": "mock"}):
            return ma.Supervisor(tarea="t", auto=True,
                                 max_parallel=max_parallel)

    def test_supervisor_delega_en_parallel_executor(self):
        sup = self._supervisor(max_parallel=3)
        with mock.patch.object(pe.ParallelExecutor, "ejecutar_paralelo",
                               return_value=[{"nombre": "x", "ok": True}]) \
                as llamado:
            res = sup.ejecutar_tareas_paralelo([_tarea("x")])
        llamado.assert_called_once()
        self.assertEqual(res[0]["nombre"], "x")

    def test_supervisor_respeta_max_parallel(self):
        sup = self._supervisor(max_parallel=2)
        with mock.patch.object(pe, "ParallelExecutor",
                               wraps=pe.ParallelExecutor) as cls:
            sup.ejecutar_tareas_paralelo([_tarea("x")])
            cls.assert_called_once_with(max_workers=2)

    def test_supervisor_import_roto_no_lanza(self):
        sup = self._supervisor(max_parallel=2)
        with mock.patch.dict(sys.modules, {"parallel_executor": None}):
            res = sup.ejecutar_tareas_paralelo([_tarea("x")])
        self.assertFalse(res[0]["ok"])

    def test_flag_paralelo_por_defecto_1(self):
        args = sc.crear_parser().parse_args([])
        self.assertEqual(args.paralelo, 1)

    def test_flag_paralelo_valor_personalizado(self):
        args = sc.crear_parser().parse_args(["--paralelo", "8"])
        self.assertEqual(args.paralelo, 8)

    def test_plan_paralelo_cero_resuelve_a_nucleos(self):
        args = argparse.Namespace(
            paralelo=0, auto=True, git_commit=False, git_mensaje=None,
            react=False, tui=False, consulta="tarea", mostrar_razonamiento=False,
            sandbox_session=False, web_interactive=False, browser=False,
            browser_headed=False, lsp=False, branch=None, directorio=".",
            provider=None, modelo=None, plan_confirmar=False, sub_agents=False)
        captured = {}

        def falso_plan(pasos, args_, raiz, max_hilos):
            captured["max_hilos"] = max_hilos
            return []

        pasos = [{"paso": 1, "descripcion": "x", "accion": "leer",
                  "argumentos": {"ruta": "a"}}]
        with mock.patch.object(sc, "_generar_plan", return_value=pasos), \
                mock.patch.object(sc, "_ejecutar_plan_en_paralelo",
                                  side_effect=falso_plan), \
                mock.patch.object(sc, "_destruir_sesion_si_aplica"), \
                mock.patch.object(sc, "_graph_rag_activo",
                                  return_value=False), \
                mock.patch.object(sc, "_contexto_plan_reiniciar"):
            sc._ejecutar_planificador(args)
        self.assertGreaterEqual(captured.get("max_hilos", 0), 2)


if __name__ == "__main__":
    unittest.main()

