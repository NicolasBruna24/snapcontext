#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v1.6.0: mejoras de la interfaz web (Monaco, D3, UX)."""

import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import snapcontext as sc  # noqa: E402

INDEX = RAIZ / "web" / "static" / "index.html"


class TestVersion(unittest.TestCase):
    def test_version_160_coherente(self):
        self.assertEqual(sc.VERSION, "6.31.0")


class TestFrontendV160(unittest.TestCase):
    """El index.html debe contener las funcionalidades nuevas."""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_confirmacion_al_guardar(self):
        self.assertIn("confirm(", self.html)
        self.assertIn("Guardado cancelado", self.html)

    def test_notificaciones_toast(self):
        self.assertIn("function notificar(", self.html)
        self.assertIn('id="toasts"', self.html)
        for clase in ("toast.ok", "toast.error", "toast.aviso", "toast.info"):
            self.assertIn(clase, self.html)

    def test_historial_de_sesion(self):
        self.assertIn("agregarHistorial(", self.html)
        self.assertIn('id="historial"', self.html)

    def test_atajos_de_teclado(self):
        self.assertIn("document.addEventListener('keydown'", self.html)
        # Ctrl+Enter ejecuta; Ctrl+S guarda.
        self.assertIn("'Enter'", self.html)
        self.assertIn("guardarArchivo(); return;", self.html)

    def test_grafo_zoom(self):
        self.assertIn("d3.zoom()", self.html)
        self.assertIn("scaleExtent([0.2,4])", self.html)

    def test_grafo_filtros(self):
        self.assertIn('id="filtroLenguaje"', self.html)
        self.assertIn('id="filtroNivel"', self.html)
        self.assertIn("refrescarGrafo()", self.html)

    def test_grafo_resaltado_de_rutas(self):
        self.assertIn("resaltarRutas", self.html)
        self.assertIn("classed('atenuado'", self.html)

    def test_resultados_con_boton_abrir(self):
        self.assertIn("Abrir", self.html)          # botón por archivo
        self.assertIn("className='ruta'", self.html)

    def test_accion_generar_tests(self):
        self.assertIn("abrirRun('flutter test')", self.html)
        self.assertIn("🧪 Tests", self.html)

    def test_descripciones_en_acciones(self):
        # Cada botón de acción rápida lleva tooltip descriptivo (title=).
        self.assertGreaterEqual(self.html.count("<button class=\"accion\" title=\""), 7)


class TestGrafoParaFiltros(unittest.TestCase):
    """Los nodos del grafo exponen 'lenguaje' (lo usa el filtro del frontend)."""

    def test_nodos_incluyen_lenguaje(self):
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "app.py"
            js = Path(tmp) / "main.js"
            py.write_text("from utils import h\n", encoding="utf-8")
            js.write_text("import './cliente.js';\n", encoding="utf-8")
            (Path(tmp) / "utils.py").write_text("# u\n", encoding="utf-8")
            (Path(tmp) / "cliente.js").write_text("export default {};\n",
                                                  encoding="utf-8")
            grafo = sc._grafo_dependencias(tmp)
            lenguajes = {n["id"]: n.get("lenguaje") for n in grafo["nodos"]}
            self.assertEqual(lenguajes["app.py"], "python")
            self.assertEqual(lenguajes["main.js"], "javascript")


if __name__ == "__main__":
    unittest.main()
