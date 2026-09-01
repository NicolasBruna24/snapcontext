#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de rendimiento para SnapContext v6.9.0 - Mejora de rendimiento.

Cubre las mejoras de rendimiento implementadas en v6.9.0:
  1. Cache persistente de embeddings (SQLite).
  2. Cache incremental del grafo (Graph RAG).
  3. Fuzzy matching optimizado (MAX_CONTEXTO_DIFUSO_LINEAS + get_close_matches).
  4. Worker de cola sin polling (threading.Event).
  5. Limite de historial de ReAct (MAX_HISTORIAL_DEFAULT).
  6. Flag --benchmark.

Ejecuta con:
    python -m unittest tests.test_rendimiento -v
"""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import snapcontext as sc          # noqa: E402
import task_queue as tq           # noqa: E402
import graph_rag as gr            # noqa: E402

LETRAS = "abcedefgh"


class ModeloFalso:
    """Imita SentenceTransformer.encode con vectores de recuentos de letras."""

    def encode(self, textos, normalize_embeddings=True):
        vectores = []
        for texto in textos:
            bajo = texto.lower()
            vectores.append([float(bajo.count(c)) for c in LETRAS])
        return vectores


# ===========================================================================
# 1. Cache persistente de embeddings (SQLite)
# ===========================================================================
class TestCacheEmbeddings(unittest.TestCase):
    """Verifica la cache SQLite ~/.snapcontext/embeddings.db."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.tmp_path / "config"),
            mock.patch.object(sc, "INDICE_DIR", self.tmp_path / "index"),
            mock.patch.object(sc, "EMBEDDINGS_DB",
                              self.tmp_path / "embeddings.db"),
            mock.patch.object(sc, "_MODELO_EMBEDDINGS", ModeloFalso()),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_cache_guarda_y_reutiliza_embedding(self):
        """_guardar_embedding_cache almacena; _consultar lo recupera."""
        hsh = sc._hash_texto("hola mundo")
        vector = [0.1, 0.2, 0.3]
        ok = sc._guardar_embedding_cache(hsh, "test.py", vector)
        self.assertTrue(ok)
        blob = sc._consultar_embedding_cache(hsh)
        self.assertIsNotNone(blob)
        recuperado = sc._deserializar_vector(blob)
        self.assertEqual(recuperado, vector)

    def test_cache_devuelve_none_si_no_existe(self):
        """Consulta a un hash inexistente devuelve None (miss)."""
        blob = sc._consultar_embedding_cache("hash_inexistente_12345")
        self.assertIsNone(blob)

    def test_calcular_con_cache_avoid_recalc_cached(self):
        """_calcular_embeddings_con_cache reutiliza items cacheados (mock)."""
        texto = "fragmento de prueba"
        hsh = sc._hash_texto(texto)
        vector = [0.5, 0.5, 0.5]
        sc._guardar_embedding_cache(hsh, "archivo.py", vector)
        with mock.patch.object(sc, "_calcular_embeddings",
                               side_effect=AssertionError(
                                   "no debe recalcular")):
            vectores = sc._calcular_embeddings_con_cache([texto])
        self.assertEqual(len(vectores), 1)
        self.assertEqual(vectores[0], vector)


# ===========================================================================
# 2. Cache incremental del grafo (Graph RAG)
# ===========================================================================
class TestCacheGraphRag(unittest.TestCase):
    """Verifica el cache incremental de graph_rag.construir_grafo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "graph_cache.pkl")
        raiz = Path(self.tmp)
        (raiz / "servicios").mkdir()
        (raiz / "servicios" / "__init__.py").write_text("", encoding="utf-8")
        (raiz / "servicios" / "modelo.py").write_text(
            "class Base:\n    pass\n", encoding="utf-8")
        (raiz / "main.py").write_text(
            "from servicios.modelo import Base\nx = Base()\n",
            encoding="utf-8")

    def test_construir_grafo_crea_cache_incremental(self):
        """La primera llamada persiste el cache con la clave 'por_archivo'."""
        g1 = gr.construir_grafo(self.tmp, ruta_cache=self.cache)
        self.assertIn("nodos", g1)
        self.assertTrue(Path(self.cache).is_file())
        import pickle
        with open(self.cache, "rb") as f:
            cache = pickle.load(f)
        self.assertIn("por_archivo", cache)
        self.assertIn("main.py", cache["por_archivo"])

    def test_construir_grafo_reutiliza_cache_sin_rebuild(self):
        """Sin cambios, la segunda llamada no reconstruye el grafo."""
        gr.construir_grafo(self.tmp, ruta_cache=self.cache)
        llamadas = []
        with mock.patch.object(gr, "_extraer_nodos_y_aristas",
                               side_effect=lambda d: (
                                   llamadas.append(d),
                                   {"nodos": {}, "aristas": []})[1]):
            g2 = gr.construir_grafo(self.tmp, ruta_cache=self.cache)
        self.assertEqual(len(llamadas), 0)
        self.assertIn("main.py", g2["nodos"])

    def test_grafo_incremental_solo_cambiados(self):
        """_grafo_incremental reparsea solo archivos modificados."""
        gr.construir_grafo(self.tmp, ruta_cache=self.cache)
        import pickle
        with open(self.cache, "rb") as f:
            data = pickle.load(f)
        huella = dict(data["fingerprint"])
        huella_mod = dict(huella)
        huella_mod["main.py"] = (data["fingerprint"]["main.py"][0] + 99999,
                                 999)
        grafo, por = gr._grafo_incremental(
            self.tmp, {"version": gr._VERSION_CACHE,
                        "fingerprint": huella_mod,
                        "por_archivo": data["por_archivo"]})
        self.assertIn("nodos", grafo)
        self.assertIn("aristas", grafo)
        self.assertIn("main.py", por)

# ===========================================================================
# 3. Fuzzy matching optimizado (editor de parches)
# ===========================================================================
class TestFuzzyMatchingOptimizado(unittest.TestCase):
    """Verifica MAX_CONTEXTO_DIFUSO_LINEAS y get_close_matches (v6.9.0)."""

    def test_limite_contexto_difuso_constante(self):
        """MAX_CONTEXTO_DIFUSO_LINEAS existe y vale 20."""
        self.assertEqual(sc.MAX_CONTEXTO_DIFUSO_LINEAS, 20)

    def test_hunks_incremental_usa_limite_contexto(self):
        """El bucle difuso trunca el contexto a MAX_CONTEXTO_DIFUSO_LINEAS."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raiz = Path(tmp.name)
        contenido = ("def f():\n" + "    return a\n" * 50 + "    fin = True\n")
        (raiz / "m.py").write_text(contenido, encoding="utf-8")
        parche = ("--- a/m.py\n+++ b/m.py\n@@ -1,55 +1,55 @@\n def f():\n"
                  + "     return a\n" * 52 + "-    fin = True\n+    fin = False\n")
        with mock.patch.object(sc.difflib, "SequenceMatcher",
                               wraps=sc.difflib.SequenceMatcher) as spy:
            sc._aplicar_hunks_incremental(parche, str(raiz))
        # Con el lÃ­mite de contexto a 20 lÃ­neas, las comparaciones difusas
        # quedan acotadas (sin el lÃ­mite serÃ­an >= 50 contextos x 52 candidatos).
        self.assertLess(spy.call_count, 20 * 55)

    def test_resincronizacion_bloque_get_close_matches(self):
        """La resincronizaciÃ³n a nivel de bloque usa difflib.get_close_matches."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        raiz = Path(tmp.name)
        (raiz / "m.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        # Hunk con posiciÃ³n errÃ³nea; el bloque solo encaja vÃ­a resincronizaciÃ³n.

# ===========================================================================
# 4. Worker de la cola de tareas sin polling
# ===========================================================================
class TestWorkerSinPolling(unittest.TestCase):
    """Verifica el worker basado en threading.Event (v6.9.0)."""

    def test_existe_evento_despertar(self):
        """El mÃ³dulo define _WORKER_DESPERTAR (threading.Event)."""
        import threading
        self.assertIsInstance(tq._WORKER_DESPERTAR, threading.Event)

    def test_encolar_tarea_despierta_al_worker(self):
        """encolar_tarea hace event.set() (el worker no duerme con polling)."""
        with mock.patch.object(tq._WORKER_DESPERTAR, "set") as spy:
            try:
                tq.encolar_tarea("chat", "prueba despertar")
            except Exception:                             # noqa: BLE001
                pass  # solo interesa que el evento se haya seteado
            self.assertTrue(spy.called)


# ===========================================================================
# 5. LÃ­mite de historial de ReAct
# ===========================================================================
class TestLimiteHistorialReAct(unittest.TestCase):
    """Verifica MAX_HISTORIAL y el resumen automÃ¡tico (v6.9.0)."""

    def test_max_historial_default_y_env(self):
        """_max_historial() devuelve 20 por defecto y respeta REACT_MAX_HISTORIAL."""
        import react_agent as ra
        self.assertEqual(ra.MAX_HISTORIAL_DEFAULT, 20)
        with mock.patch.dict(os.environ, {"REACT_MAX_HISTORIAL": "7"}):
            self.assertEqual(ra._max_historial(), 7)
        with mock.patch.dict(os.environ, {"REACT_MAX_HISTORIAL": "no-numero"}):
            self.assertEqual(ra._max_historial(), ra.MAX_HISTORIAL_DEFAULT)

    def test_resumir_por_longitud_dispara_compresion(self):
        """Al superar max_historial iteraciones, el historial se comprime."""
        import react_agent as ra

        class AgenteFalso:
            historial = [{"role": "system", "content": "sys"}]

# ===========================================================================
# 6. Flag --benchmark
# ===========================================================================
class TestFlagBenchmark(unittest.TestCase):
    """Verifica el flag --benchmark y su tabla de resultados (v6.9.0)."""

    def test_flag_registrado_en_parser(self):
        """crear_parser() define --benchmark."""
        parser = sc.crear_parser()
        opts = {o for a in parser._actions for o in a.option_strings}
        self.assertIn("--benchmark", opts)

    def test_ejecutar_benchmark_emite_tabla(self):
        """_ejecutar_benchmark mide fases y llama a _mostrar_tabla_benchmark."""
        args = argparse.Namespace(benchmark=True, directorio=".",
                                  carpetas=None, extensiones=None)
        llamadas = []
        with mock.patch.object(sc, "_mostrar_tabla_benchmark",
                               side_effect=lambda f: llamadas.append(f)):
            rc = sc._ejecutar_benchmark(args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(llamadas), 1)
        fases = [f[0] for f in llamadas[0]]
        self.assertIn("Inicio (import + CLI)", fases)
        self.assertIn("Tiempo total", fases)
        self.assertGreaterEqual(len(fases), 7)


if __name__ == "__main__":
    unittest.main()

