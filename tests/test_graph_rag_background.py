"""Tests de la Fase 16: indexación en background del Graph RAG.

Verifica que el CLI arranca sin esperar (cargar_grafo devuelve un grafo
parcial/vacío al instante mientras un hilo demonio lo puebla), que un cache
válido se carga de forma síncrona y que las consultas son seguras antes de
que termine la indexación (sin condiciones de carrera).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph_rag as gr


def _proyecto(tmp: tempfile.TemporaryDirectory) -> Path:
    """Mini proyecto Python para indexar."""
    raiz = Path(tmp.name)
    (raiz / "main.py").write_text(
        "import servicios.pagos\n\nmain()\n\ndef main():\n    pagos.procesar()\n",
        encoding="utf-8",
    )
    (raiz / "servicios").mkdir()
    (raiz / "servicios").joinpath("pagos.py").write_text(
        "def procesar():\n    return 0\n", encoding="utf-8"
    )
    return raiz


class TestCargarGrafoNoBloqueante(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = _proyecto(self.tmp)
        self.cache = str(Path(self.tmp.name) / "cache.pkl")
        self.addCleanup(self.tmp.cleanup)

    def test_cargar_grafo_devuelve_parcial_inmediato_sin_cache(self):
        """Sin cache válido, cargar_grafo NO bloquea: devuelve grafo parcial."""
        t0 = time.monotonic()
        grafo = gr.cargar_grafo(str(self.raiz), ruta_cache=self.cache)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0, "El arranque no debe bloquear indexando")
        self.assertIsInstance(grafo, dict)
        self.assertIn("aristas", grafo)
        self.assertIn("nodos", grafo)
        self.assertIs(grafo.get("indexado"), False, "Debe estar en modo degradado")

    def test_cache_valido_se_carga_inmediato_y_completo(self):
        """Con cache válido, cargar_grafo lo entrega síncrono y completo."""
        gr.construir_grafo(str(self.raiz), ruta_cache=self.cache)
        t0 = time.monotonic()
        grafo = gr.cargar_grafo(str(self.raiz), ruta_cache=self.cache)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0)
        self.assertIs(grafo.get("indexado"), True)
        self.assertIn("main.py", grafo.get("nodos", {}))

    def test_consultas_funcionan_con_grafo_parcial(self):
        """expandir_contexto tolera el grafo parcial/vacío (modo degradado)."""
        grafo = gr.cargar_grafo(str(self.raiz), ruta_cache=self.cache)
        self.assertIs(grafo.get("indexado"), False)
        # No debe lanzar ni perder los archivos originales.
        resultado = gr.expandir_contexto(["main.py"], grafo, max_adicionales=3, notificar=False)
        self.assertIn("main.py", resultado)


class TestIndexarEnBackground(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = _proyecto(self.tmp)
        self.cache = str(Path(self.tmp.name) / "cache.pkl")
        self.addCleanup(self.tmp.cleanup)

    def test_background_construye_y_el_gestor_termina(self):
        """El hilo demonio puebla el grafo y marca listo."""
        gestor = gr.indexar_en_background(str(self.raiz), ruta_cache=self.cache, notificar=False)
        self.assertIsInstance(gestor, gr.GrafoIndexable)
        self.assertTrue(gestor.listo.wait(timeout=10), "La indexación debe terminar")
        self.assertTrue(gestor.indexado)
        grafo = gestor.obtener()
        self.assertIs(grafo.get("indexado"), True)
        self.assertIn("main.py", grafo.get("nodos", {}))

    def test_cargar_grafo_recoge_el_grafo_ya_indexado(self):
        """Tras terminar, cargar_grafo devuelve el grafo completo (no el parcial)."""
        gr.indexar_en_background(str(self.raiz), ruta_cache=self.cache, notificar=False)
        # Esperamos a que termine.
        gestor = gr.grafo_indexable(str(self.raiz))
        if gestor is not None:
            gestor.listo.wait(timeout=10)
        grafo = gr.cargar_grafo(str(self.raiz), ruta_cache=self.cache)
        self.assertIs(grafo.get("indexado"), True)

    def test_se_reutiliza_la_misma_indexacion(self):
        """Dos llamadas devuelven el mismo gestor (sin duplicar el trabajo)."""
        g1 = gr.indexar_en_background(str(self.raiz), ruta_cache=self.cache, notificar=False)
        g2 = gr.indexar_en_background(str(self.raiz), ruta_cache=self.cache, notificar=False)
        self.assertIs(g1, g2)

    def test_se_arranca_sin_esperar_a_que_termine(self):
        """Si la construcción es lenta, cargar_grafo devuelve antes de terminar."""
        original = gr.construir_grafo

        def _lento(*args, **kwargs):
            time.sleep(0.5)  # simula un proyecto grande
            return original(*args, **kwargs)

        with mock.patch.object(gr, "construir_grafo", side_effect=_lento):
            t0 = time.monotonic()
            grafo = gr.cargar_grafo(str(self.raiz), forzar=True, ruta_cache=self.cache)
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 0.4, "El CLI no debe esperar a la indexación")
            self.assertIs(grafo.get("indexado"), False)
            gestor = gr.grafo_indexable(str(self.raiz))
            if gestor is not None:
                gestor.listo.wait(timeout=10)

    def test_error_silencioso_en_background(self):
        """Si la construcción falla, no cruje y queda el grafo parcial."""
        with mock.patch.object(gr, "construir_grafo", side_effect=RuntimeError("boom")):
            gestor = gr.indexar_en_background(
                str(self.raiz), forzar=True, ruta_cache=self.cache, notificar=False
            )
            gestor.listo.wait(timeout=10)
            grafo = gestor.obtener()
            self.assertIn("aristas", grafo)  # siempre un dict usable
            self.assertIs(grafo.get("indexado"), False)


if __name__ == "__main__":
    unittest.main()
