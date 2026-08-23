#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del sistema de embeddings locales — v1.1.0.

No requieren sentence-transformers: se inyecta un modelo falso que genera
vectores deterministas a partir de recuentos de letras, suficiente para
verificar indexado, caché, búsqueda semántica y selección.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc

LETRAS = "pagosusr"          # dimensiones del modelo falso


class ModeloFalso:
    """Imita SentenceTransformer.encode con vectores de recuentos de letras."""

    def encode(self, textos, normalize_embeddings=True):
        vectores = []
        for texto in textos:
            bajo = texto.lower()
            vectores.append([float(bajo.count(c)) for c in LETRAS])
        return vectores


class _SentenceTransformerStub:
    """Stub que representa a la clase SentenceTransformer importada."""
    pass


class BaseEmbeddings(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz_tmp = Path(self.tmp.name)
        self.proyecto = self.raiz_tmp / "proy"
        self.proyecto.mkdir()
        parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.raiz_tmp),
            mock.patch.object(sc, "INDICE_DIR", self.raiz_tmp / "index"),
            mock.patch.object(sc, "_MODELO_EMBEDDINGS", ModeloFalso()),
            mock.patch.object(sc, "SentenceTransformer", _SentenceTransformerStub),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _proyecto_pago(self) -> Path:
        (self.proyecto / "pagos.py").write_text(
            "gestión de pagos\ndef procesar_pago():\n    return 'pago ok'\n",
            encoding="utf-8")
        (self.proyecto / "usuarios.py").write_text(
            "usuarios del sistema\ndef crear_usuario():\n    return 'user'\n",
            encoding="utf-8")
        return self.proyecto


class TestChunking(unittest.TestCase):
    def test_texto_corto_un_fragmento(self):
        frags = sc._dividir_en_fragmentos("linea\notra")
        self.assertEqual(len(frags), 1)
        self.assertEqual(frags[0]["linea_inicio"], 1)

    def test_texto_largo_varios_fragmentos(self):
        texto = "\n".join(f"linea {i} con contenido" for i in range(500))
        frags = sc._dividir_en_fragmentos(texto, max_caracteres=2000)
        self.assertGreater(len(frags), 1)
        inicios = [f["linea_inicio"] for f in frags]
        self.assertEqual(inicios, sorted(inicios))
        self.assertEqual(inicios[0], 1)

    def test_archivo_vacio(self):
        frags = sc._dividir_en_fragmentos("")
        self.assertEqual(len(frags), 1)


class TestGitignore(BaseEmbeddings):
    def test_patrones_ignorados(self):
        patrones = ["secreto.py", "build"]
        self.assertTrue(sc._es_ignorado("src/secreto.py", patrones))
        self.assertTrue(sc._es_ignorado("build/x.py", patrones))
        self.assertFalse(sc._es_ignorado("src/app.py", patrones))


class TestHashTexto(unittest.TestCase):
    def test_hash_deterministico(self):
        self.assertEqual(sc._hash_texto("hola"), sc._hash_texto("hola"))

    def test_hash_diferente_para_diferente_texto(self):
        self.assertNotEqual(sc._hash_texto("hola"), sc._hash_texto("chau"))

    def test_hash_16_chars(self):
        self.assertEqual(len(sc._hash_texto("test")), 16)


class TestSimilitudCoseno(unittest.TestCase):
    def test_identicos(self):
        v = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(sc._similitud_coseno(v, v), 1.0)

    def test_ortogonales(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(sc._similitud_coseno(a, b), 0.0)

    def test_zero_vector(self):
        self.assertAlmostEqual(sc._similitud_coseno([0, 0], [0, 0]), 0.0)

    def test_parcial(self):
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        expected = 1 / (2 ** 0.5)
        self.assertAlmostEqual(sc._similitud_coseno(a, b), expected)


class TestIndexarProyecto(BaseEmbeddings):
    def test_indexar_crea_indice_con_fragmentos(self):
        self._proyecto_pago()
        indice = sc._indexar_proyecto(str(self.proyecto))
        self.assertIn("fragmentos", indice)
        self.assertTrue(indice["fragmentos"])
        for frag in indice["fragmentos"]:
            self.assertIn("archivo", frag)
            self.assertIn("linea_inicio", frag)
            self.assertIn("hash_archivo", frag)
            self.assertIsNotNone(frag.get("embedding"))
        # Persiste en disco (~/.snapcontext/index/<hash>.json).
        self.assertTrue(sc._ruta_indice(str(self.proyecto)).is_file())
        self.assertIn("hash_proyecto", indice)

    def test_indexar_reutiliza_embeddings_sin_recalcular(self):
        self._proyecto_pago()
        sc._indexar_proyecto(str(self.proyecto))
        with mock.patch.object(
                sc, "_calcular_embeddings",
                side_effect=AssertionError("no debe recalcular embeddings")) as mocked:
            indice = sc._indexar_proyecto(str(self.proyecto))
        mocked.assert_not_called()
        self.assertTrue(
            all(f.get("embedding") is not None for f in indice["fragmentos"]))

    def test_indexar_respeta_gitignore(self):
        self._proyecto_pago()
        (self.proyecto / ".gitignore").write_text("secreto.py\n", encoding="utf-8")
        (self.proyecto / "secreto.py").write_text(
            "token = 'super secret'\n", encoding="utf-8")
        indice = sc._indexar_proyecto(str(self.proyecto))
        rutas = {f["archivo"] for f in indice["fragmentos"]}
        self.assertIn("pagos.py", rutas)
        self.assertNotIn("secreto.py", rutas)

    def test_indexar_falla_sin_archivos(self):
        with self.assertRaises(RuntimeError):
            sc._indexar_proyecto(str(self.proyecto))

    def test_cargar_indice_desde_disco(self):
        self._proyecto_pago()
        sc._indexar_proyecto(str(self.proyecto))
        cargado = sc._cargar_indice(str(self.proyecto))
        self.assertTrue(cargado.get("fragmentos"))


class TestCacheInvalidacion(BaseEmbeddings):
    def test_asegurar_indice_reutiliza_sin_reindexar(self):
        self._proyecto_pago()
        sc._indexar_proyecto(str(self.proyecto))
        with mock.patch.object(sc, "_indexar_proyecto",
                               return_value={}) as mocked:
            indice = sc._asegurar_indice(str(self.proyecto))
        mocked.assert_not_called()
        self.assertTrue(indice.get("fragmentos"))

    def test_asegurar_indice_reindexa_cuando_cambia(self):
        self._proyecto_pago()
        sc._indexar_proyecto(str(self.proyecto))
        (self.proyecto / "pagos.py").write_text(
            "gestión de pagos nueva línea\n", encoding="utf-8")
        with mock.patch.object(
                sc, "_indexar_proyecto",
                side_effect=sc._indexar_proyecto) as mocked:
            sc._asegurar_indice(str(self.proyecto))
        mocked.assert_called_once()


class TestBuscarSemantica(BaseEmbeddings):
    def test_buscar_devuelve_resultados_ordenados(self):
        self._proyecto_pago()
        sc._indexar_proyecto(str(self.proyecto))
        resultados = sc._buscar_semanticamente("gestión de pagos",
                                              directorio=str(self.proyecto))
        self.assertTrue(resultados)
        self.assertEqual(resultados[0]["archivo"], "pagos.py")
        for actual, siguiente in zip(resultados, resultados[1:]):
            self.assertGreaterEqual(actual["similitud"], siguiente["similitud"])

    def test_buscar_sin_modelo_lanza(self):
        self._proyecto_pago()
        with mock.patch.object(sc, "_MODELO_EMBEDDINGS", None), \
             mock.patch.object(sc, "SentenceTransformer", None):
            with self.assertRaises(RuntimeError):
                sc._buscar_semanticamente("cualquier cosa",
                                          directorio=str(self.proyecto))


class TestSeleccionarConEmbeddings(BaseEmbeddings):
    def test_seleccionar_prioriza_archivo_relevante(self):
        self._proyecto_pago()
        sc._indexar_proyecto(str(self.proyecto))
        seleccion = sc._seleccionar_archivos_con_embeddings(
            "gestión de pagos", directorio=str(self.proyecto),
            max_archivos=2, umbral=0.1)
        self.assertIn("pagos.py", seleccion)

    def test_seleccionar_umbral_alto_filtra_todo(self):
        self._proyecto_pago()
        sc._indexar_proyecto(str(self.proyecto))
        seleccion = sc._seleccionar_archivos_con_embeddings(
            "gestión de pagos", directorio=str(self.proyecto),
            max_archivos=3, umbral=2.0)
        self.assertEqual(seleccion, [])
