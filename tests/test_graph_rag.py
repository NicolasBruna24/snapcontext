#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del Grafo de Conocimiento (Graph RAG) — v5.5.0.

Cubre:
- Construcción del grafo sobre un proyecto pequeño de prueba
  (nodos: archivos/funciones/clases; aristas: import/llamada/herencia).
- Expansión de contexto con dependencias entrantes/salientes.
- Cache (reutilización vs. recreación con ``forzar`` y con cambios).
- Integración con el agente ReAct (mockeada).
- Flag CLI ``--graph-rag`` y variable de entorno ``SNAPCONTEXT_GRAPH_RAG``.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph_rag as gr                                    # noqa: E402
import snapcontext as sc                                  # noqa: E402


def _proyecto(base: str) -> str:
    """Proyecto de prueba: main.py usa servicios.pagos, modelo base Animal."""
    raiz = Path(base)
    (raiz / "servicios").mkdir()
    (raiz / "servicios" / "__init__.py").write_text("", encoding="utf-8")
    (raiz / "servicios" / "pagos.py").write_text(
        "def procesar(monto):\n"
        "    return monto * 2\n", encoding="utf-8")
    (raiz / "animales.py").write_text(
        "class Animal:\n"
        "    def hablar(self):\n"
        "        return '...'\n", encoding="utf-8")
    (raiz / "main.py").write_text(
        "from servicios.pagos import procesar\n"
        "from animales import Animal\n\n\n"
        "class Perro(Animal):\n"
        "    def hablar(self):\n"
        "        return 'guau'\n\n\n"
        "def cobrar(monto):\n"
        "    return procesar(monto)\n\n\n"
        "def main():\n"
        "    print(Perro().hablar(), cobrar(3))\n",
        encoding="utf-8")
    return str(raiz)


class TestConstruccionGrafo(unittest.TestCase):
    """_extraer_nodos_y_aristas sobre un proyecto pequeño."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = _proyecto(self.tmp)

    def test_nodos_archivos(self):
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        for rel in ("main.py", "animales.py", "servicios/pagos.py",
                    "servicios/__init__.py"):
            self.assertIn(rel, grafo["nodos"])
            self.assertEqual(grafo["nodos"][rel]["tipo"], "archivo")

    def test_nodos_funciones_y_clases(self):
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        self.assertEqual(grafo["nodos"]["main.py::cobrar"]["tipo"], "funcion")
        self.assertEqual(grafo["nodos"]["animales.py::Animal"]["tipo"],
                         "clase")
        self.assertEqual(grafo["nodos"]["main.py::Perro"]["tipo"], "clase")

    def test_arista_import(self):
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        imports = {(a["origen"], a["destino"]) for a in grafo["aristas"]
                   if a["tipo"] == "import"}
        self.assertIn(("main.py", "servicios/pagos.py"), imports)
        self.assertIn(("main.py", "animales.py"), imports)

    def test_arista_llamada(self):
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        llamadas = {(a["origen"], a["destino"]) for a in grafo["aristas"]
                    if a["tipo"] == "llamada"}
        self.assertIn(("main.py::cobrar", "servicios/pagos.py::procesar"),
                      llamadas)

    def test_arista_herencia(self):
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        herencias = {(a["origen"], a["destino"]) for a in grafo["aristas"]
                     if a["tipo"] == "herencia"}
        self.assertIn(("main.py::Perro", "animales.py::Animal"), herencias)

    def test_sintaxis_invalida_no_rompe(self):
        (Path(self.dir) / "roto.py").write_text("def f(:\n", encoding="utf-8")
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        self.assertIn("roto.py", grafo["nodos"])

    def test_directorio_ignorado(self):
        (Path(self.dir) / "__pycache__").mkdir()
        (Path(self.dir) / "__pycache__" / "x.py").write_text(
            "x = 1\n", encoding="utf-8")
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        self.assertNotIn("__pycache__/x.py", grafo["nodos"])


class TestExpandirContexto(unittest.TestCase):
    """expandir_contexto: vecinos entrantes y salientes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = _proyecto(self.tmp)
        self.grafo = gr._extraer_nodos_y_aristas(self.dir)

    def test_entrantes_primero(self):
        # main.py importa pagos → "main.py" es entrante de pagos.py.
        res = gr.expandir_contexto(["servicios/pagos.py"], self.grafo,
                                   notificar=False)
        self.assertEqual(res[0], "servicios/pagos.py")
        self.assertIn("main.py", res)

    def test_salientes(self):
        res = gr.expandir_contexto(["animales.py"], self.grafo,
                                   notificar=False)
        # animales.py no importa nada del proyecto, pero main.py lo usa.
        self.assertIn("main.py", res)

    def test_max_adicionales(self):
        res = gr.expandir_contexto(["servicios/pagos.py", "animales.py"],
                                   self.grafo, max_adicionales=1,
                                   notificar=False)
        self.assertEqual(len(res), 3)            # 2 originales + 1

    def test_sin_grafo_o_vacio(self):
        self.assertEqual(gr.expandir_contexto(["a.py"], {}, notificar=False),
                         ["a.py"])
        self.assertEqual(gr.expandir_contexto([], self.grafo), [])

    def test_no_duplica_y_mantiene_originales(self):
        res = gr.expandir_contexto(["main.py", "servicios/pagos.py"],
                                   self.grafo, max_adicionales=5,
                                   notificar=False)
        self.assertEqual(res[:2], ["main.py", "servicios/pagos.py"])
        self.assertEqual(len(res), len(set(res)))


class TestCache(unittest.TestCase):
    """construir_grafo: cache pickle con fingerprint mtime+tamaño."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = _proyecto(self.tmp)
        self.cache = os.path.join(self.tmp, "cache.pkl")
        os.environ.pop("SNAPCONTEXT_GRAPH_RAG", None)

    def tearDown(self):
        os.environ.pop("SNAPCONTEXT_GRAPH_RAG", None)

    def test_crea_y_reutiliza_cache(self):
        g1 = gr.construir_grafo(self.dir, ruta_cache=self.cache)
        self.assertTrue(Path(self.cache).is_file())
        with mock.patch.object(gr, "_extraer_nodos_y_aristas",
                               side_effect=AssertionError("no rebuild")):
            g2 = gr.construir_grafo(self.dir, ruta_cache=self.cache)
        self.assertEqual(g1["nodos"], g2["nodos"])

    def test_forzar_reconstruye(self):
        gr.construir_grafo(self.dir, ruta_cache=self.cache)
        llamadas = []
        with mock.patch.object(gr, "_extraer_nodos_y_aristas",
                               side_effect=lambda d: (
                                   llamadas.append(d),
                                   {"nodos": {}, "aristas": []})[1]):
            gr.construir_grafo(self.dir, forzar=True, ruta_cache=self.cache)
        self.assertEqual(len(llamadas), 1)

    def test_reconstruye_si_cambia_codigo(self):
        g1 = gr.construir_grafo(self.dir, ruta_cache=self.cache)
        (Path(self.dir) / "nuevo.py").write_text("x = 1\n", encoding="utf-8")
        g2 = gr.construir_grafo(self.dir, ruta_cache=self.cache)
        self.assertNotIn("nuevo.py", g1["nodos"])
        self.assertIn("nuevo.py", g2["nodos"])

    def test_cache_corrupto_se_reconstruye(self):
        Path(self.cache).write_bytes(b"no-es-pickle")
        grafo = gr.construir_grafo(self.dir, ruta_cache=self.cache)
        self.assertIn("main.py", grafo["nodos"])


class TestIntegracionReactAgent(unittest.TestCase):
    """Integración con el agente ReAct (mockeada)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = _proyecto(self.tmp)

    def _agente(self, graph_rag):
        import react_agent as ra
        with mock.patch.object(ra.sc, "cargar_configuracion",
                               return_value={}):
            return ra.ReactAgent(self.dir, graph_rag=graph_rag)

    def test_flag_off_no_expande(self):
        agente = self._agente(False)
        res = agente._tool_buscar_codigo({"patron": "procesar"})
        self.assertTrue(res["ok"])
        self.assertNotIn("archivos_relacionados", res)

    def test_flag_on_expande(self):
        agente = self._agente(True)
        grafo = gr._extraer_nodos_y_aristas(self.dir)
        with mock.patch.object(agente, "_grafo_del_proyecto",
                               return_value=grafo):
            res = agente._tool_buscar_codigo({"patron": "procesar"})
        self.assertTrue(res["ok"])
        self.assertIn("main.py", res["archivos_relacionados"])

    def test_grafo_en_cache_tras_primera_construccion(self):
        agente = self._agente(True)
        with mock.patch.object(gr, "construir_grafo",
                               return_value={"nodos": {},
                                             "aristas": []}) as c:
            agente._grafo_del_proyecto()
            agente._grafo_del_proyecto()
        self.assertEqual(c.call_count, 1)


class TestFlagsYEntorno(unittest.TestCase):
    """Flag --graph-rag y variable SNAPCONTEXT_GRAPH_RAG."""

    def setUp(self):
        os.environ.pop("SNAPCONTEXT_GRAPH_RAG", None)

    def tearDown(self):
        os.environ.pop("SNAPCONTEXT_GRAPH_RAG", None)

    def test_flag_cli_parseado(self):
        args = sc.crear_parser().parse_args(["--graph-rag", "consulta"])
        self.assertTrue(args.graph_rag)
        args2 = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(args2.graph_rag)

    def test_env_activa(self):
        os.environ["SNAPCONTEXT_GRAPH_RAG"] = "1"
        self.assertTrue(gr.graph_rag_activo(None))
        self.assertTrue(sc._graph_rag_activo(
            sc.crear_parser().parse_args(["consulta"])))

    def test_flag_gana_sobre_entorno(self):
        os.environ["SNAPCONTEXT_GRAPH_RAG"] = "0"
        self.assertTrue(gr.graph_rag_activo(True))

    def test_sin_flag_ni_env_desactivado(self):
        self.assertFalse(gr.graph_rag_activo(None))
        self.assertFalse(sc._graph_rag_activo(
            sc.crear_parser().parse_args(["consulta"])))


if __name__ == "__main__":
    unittest.main()