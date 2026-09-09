"""Tests de la Fase 16: degradación elegante del LSP.

Verifica que si el servidor LSP no responde (timeout) o lanza una excepción,
el sistema cae silenciosamente a la búsqueda por regex (y embeddings
sintácticos ligeros) sin lanzar ninguna excepción al usuario.
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

import graph_lsp_integrator as gli
import snapcontext as sc


def _proyecto(tmp: tempfile.TemporaryDirectory) -> Path:
    """Crea un mini proyecto de Python con directorio raíz del proyecto."""
    raiz = Path(tmp.name)
    (raiz / "main.py").write_text("def saludo():\n    return 42\n\nsaludo()\n", encoding="utf-8")
    (raiz / "utils.py").write_text("def ayuda():\n    return 1\n\nsaludo()\n", encoding="utf-8")
    # Marcador para que _raiz_proyecto no suba a /.
    (raiz / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    return raiz


class TestFallbackLSP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = _proyecto(self.tmp)
        self.addCleanup(self.tmp.cleanup)

    def _integrador_con_lsp_que_falla(self):
        class _LspFallido:
            def obtener_simbolos(self, archivo, linea):
                raise RuntimeError("servidor LSP caido")

        return gli.GraphLSPIntegrator(grafo={}, proveedor_lsp=_LspFallido())

    def test_no_lanza_excepcion_cuando_lsp_falla(self):
        """Si el LSP lanza, _obtener_simbolos_lsp no propaga y cae a regex."""
        integ = self._integrador_con_lsp_que_falla()
        resultado = integ._obtener_simbolos_lsp(str(self.raiz / "main.py"), 1)
        # Nunca debe lanzar; y debe haber caído a la búsqueda por regex.
        self.assertIsInstance(resultado, list)
        self.assertTrue(resultado, "El fallback por regex debería devolver símbolos")
        self.assertEqual(resultado[0]["tipo"], "referencia")

    def test_lsp_que_no_responde_usa_timeout_y_cae_a_regex(self):
        """Un LSP que tarda más que el timeout degrada sin bloquear."""
        integ = gli.GraphLSPIntegrator(
            grafo={},
            proveedor_lsp=mock.MagicMock(
                obtener_simbolos=mock.MagicMock(side_effect=lambda *a, **k: time.sleep(10))
            ),
        )
        t0 = time.monotonic()
        resultado = integ._obtener_simbolos_lsp(str(self.raiz / "main.py"), 1)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 5.0, "El timeout debe cortar la llamada (≈2 s)")
        self.assertTrue(resultado)

    def test_embeddings_sintacticos_devuelven_definiciones(self):
        """AST devuelve la función/definición del archivo (embeddings ligeros)."""
        integ = gli.GraphLSPIntegrator(grafo={}, proveedor_lsp=None)
        # Sin línea: embeddings ligero (AST) para el propio archivo.
        resultado = integ._buscar_por_embeddings_ligeros(str(self.raiz / "main.py"), None)
        nombres = {s.get("nombre") for s in resultado}
        self.assertIn("saludo", nombres)

    def test_sc_obtener_simbolos_lsp_no_lanza(self):
        """La función pública de snapcontext nunca lanza aunque el módulo falle."""
        with mock.patch(
            "graph_lsp_integrator.obtener_contexto_preciso",
            side_effect=RuntimeError("boom"),
        ):
            resultado = sc.obtener_simbolos_lsp("main.py", 10, "funcion", {})
        self.assertIsInstance(resultado, list)

    def test_aviso_degradacion_una_sola_vez_por_archivo(self):
        """El aviso 'LSP no disponible' se emite una única vez por archivo."""
        integ = self._integrador_con_lsp_que_falla()
        with mock.patch.object(gli, "_aviso") as aviso_mock:
            integ._obtener_simbolos_lsp(str(self.raiz / "main.py"), 1)
            integ._obtener_simbolos_lsp(str(self.raiz / "main.py"), 1)
        avisos = [c[0][0] for c in aviso_mock.call_args_list]
        self.assertEqual(avisos.count("LSP no disponible, usando búsqueda por regex."), 1)


class TestRegexFallback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = _proyecto(self.tmp)
        self.addCleanup(self.tmp.cleanup)
        self.integ = gli.GraphLSPIntegrator(grafo={})

    def test_busca_referencias_del_simbolo_en_la_linea(self):
        resultado = self.integ._buscar_por_regex(str(self.raiz / "main.py"), 1)
        self.assertTrue(resultado)
        self.assertTrue(all(s.get("archivo") for s in resultado))
        self.assertTrue(all(isinstance(s.get("linea"), int) for s in resultado))

    def test_devuelve_definiciones_sin_linea(self):
        resultado = self.integ._buscar_por_regex(str(self.raiz / "main.py"), None)
        self.assertTrue(any(s["nombre"] == "saludo" for s in resultado))


if __name__ == "__main__":
    unittest.main()
