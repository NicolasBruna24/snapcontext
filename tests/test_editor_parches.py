#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests v6.3.0: mejora del editor de parches (fuzzy matching, resincronización
de bloques, flag --mostrar-diff y mensajes de error claros)."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agentes as ag          # noqa: E402
import snapcontext as sc      # noqa: E402
import ui                     # noqa: E402


class TestVariantesYFuzzy(unittest.TestCase):
    """Emparejamiento por variantes y difuso (v6.3.0)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc630fz_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _escribir(self, nombre, contenido):
        (Path(self.tmp) / nombre).write_text(contenido, encoding="utf-8")
        return nombre

    def _leer(self, nombre):
        return (Path(self.tmp) / nombre).read_text(encoding="utf-8")

    def test_espacios_e_indentacion_cambiada_aplica(self):
        base = ("def calcular(items):\n"
                "    total = 0\n"
                "    for it in items:\n"
                "        total += it\n"
                "    return total\n")
        nuevo = base.replace("    return total\n", "    return total + 1\n")
        # El usuario reindentó una línea de contexto (más espacios): la
        # coincidencia exacta de v4.6.0 fallaba; la variante por espacios OK.
        real = base.replace("        total += it\n", "            total += it\n")
        nombre = self._escribir("f.py", real)
        parche = sc._generar_parche(base, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = self._leer(nombre)
        self.assertIn("    return total + 1\n", resultado)
        # La reindentación local del usuario se conserva (contexto no escrito).
        self.assertIn("            total += it\n", resultado)

    def test_comentario_anadido_en_contexto_aplica(self):
        base = ("def carga():\n"
                "    datos = leer()\n"
                "    return procesar(datos)\n")
        nuevo = base.replace("    return procesar(datos)\n",
                             "    return procesar(datos) or None\n")
        real = base.replace("    datos = leer()\n",
                            "    datos = leer()  # ahora con caché\n")
        nombre = self._escribir("g.py", real)
        parche = sc._generar_parche(base, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = self._leer(nombre)
        self.assertIn("return procesar(datos) or None", resultado)
        self.assertIn("# ahora con caché", resultado)

    def test_comentario_en_linea_eliminada_aplica(self):
        # La línea que el parche elimina ('-') ganó un comentario en el
        # archivo: con la igualdad estricta de v4.6.0 el hunk abortaba.
        base = "a = 1\nb = viejo\nc = 3\n"
        nuevo = base.replace("b = viejo\n", "b = nuevo\n")
        real = base.replace("b = viejo\n", "b = viejo  # TODO: revisar\n")
        nombre = self._escribir("h.py", real)
        parche = sc._generar_parche(base, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = self._leer(nombre)
        self.assertIn("b = nuevo\n", resultado)
        self.assertNotIn("viejo", resultado)

    def test_variable_renombrada_en_contexto_aplica(self):
        base = ("def totalizar(precios):\n"
                "    suma = sum(precios)\n"
                "    return redondear(suma)\n")
        nuevo = base.replace("    return redondear(suma)\n", "    return redondear(suma, 2)\n")
        # El usuario renombró una variable de contexto; el emparejamiento
        # difuso (etapa 3) lo tolera y el bloque se conserva del archivo.
        real = base.replace("    suma = sum(precios)\n",
                            "    suma = sum(precios_brutos)\n")
        nombre = self._escribir("i.py", real)
        parche = sc._generar_parche(base, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = self._leer(nombre)
        self.assertIn("redondear(suma, 2)", resultado)
        self.assertIn("precios_brutos", resultado)

    def test_hunk_de_anadido_puro_con_contexto(self):
        original = "uno\ndos\ntres\n"
        nuevo = "uno\ndos\ninsertada\ntres\n"
        nombre = self._escribir("j.py", original)
        parche = sc._generar_parche(original, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        self.assertEqual(self._leer(nombre), nuevo)

    def test_varios_hunks_con_desplazamiento_acumulativo(self):
        lineas = [f"linea {n}\n" for n in range(1, 10)]
        original = "".join(lineas)
        # hunk1 inserta una línea tras la 1; hunk2 cambia la última. El
        # segundo hunk se declara contra la numeración ORIGINAL y debe
        # reajustarse gracias al desplazamiento acumulado.
        nuevo = (lineas[0] + "insertada\n" + "".join(lineas[1:8])
                 + "FINAL\n")
        nombre = self._escribir("k.py", original)
        parche = sc._generar_parche(original, nuevo, nombre)
        self.assertEqual(len(sc._parsear_hunks(parche)), 2)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        self.assertEqual(self._leer(nombre), nuevo)

    def test_resincronizacion_de_bloque_desplazado(self):
        # El contexto del hunk contiene una línea reescrita: la búsqueda
        # difusa línea a línea falla (ratio ~0.74 < 0.90) pero el bloque
        # completo es muy similar (>= 0.80), por lo que la resincronización
        # a nivel de bloque (etapa 4) recoloca el hunk conservando el resto.
        bloque = ("def exportar():\n"
                  "    filas = recoger()\n"
                  "    escribir(filas)\n"
                  "    return True\n")
        base = "import os\n" + bloque
        nuevo = base.replace("    return True\n", "    return False\n")
        real = base.replace("    filas = recoger()\n",
                            "    filas = recolectar()\n")
        nombre = self._escribir("exp.py", real)
        parche = sc._generar_parche(base, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = self._leer(nombre)
        self.assertIn("    return False\n", resultado)
        # El cambio local del usuario en el contexto se conserva.
        self.assertIn("    filas = recolectar()\n", resultado)

    def test_resincronizacion_bajo_umbral_falla_limpio(self):
        # Si ni el bloque se parece (ratio < 0.80), se aborta con el mensaje
        # claro y el archivo queda intacto.
        original = ("class Uno:\n"
                    "    pass\n"
                    "\n"
                    "class Dos:\n"
                    "    pass\n")
        nombre = self._escribir("bl.py", original)
        parche = ("--- a/bl.py\n+++ b/bl.py\n"
                  "@@ -1,3 +1,3 @@\n"
                  " class Uno:\n"
                  "-    metodo_inexistente_absoluto()\n"
                  "+    otro_metodo()\n"
                  "     pass\n")
        with mock.patch.object(sc, "error"):
            ok = sc._aplicar_hunks_incremental(parche, self.tmp)
        self.assertFalse(ok)
        self.assertEqual(self._leer(nombre), original)

    def test_variantes_de_linea_y_url_no_rompida(self):
        cruda, norm, sincom = sc._variantes_linea("  x = 1   # nota  \n")
        self.assertEqual(cruda, "  x = 1   # nota  ")
        self.assertEqual(norm, "x = 1 # nota")
        self.assertEqual(sincom, "x = 1")
        # Las URLs con '//' no se rompen al quitar comentarios.
        self.assertEqual(sc._variantes_linea("url = 'https://x.y'")[2],
                         "url = 'https://x.y'")

    def test_lineas_equivalentes(self):
        self.assertTrue(sc._lineas_equivalentes("a = 1", "  a = 1  "))
        self.assertTrue(sc._lineas_equivalentes("a = 1  # nota", "a = 1"))
        self.assertFalse(sc._lineas_equivalentes("a = 1", "a = 2"))

    def test_contar_cambios_parche(self):
        parche = ("--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n"
                  "-a\n+A\n b\n")
        self.assertEqual(sc._contar_cambios_parche(parche), (1, 1))

    def test_umbral_difuso_expuesto(self):
        self.assertEqual(sc.UMBRAL_DIFUSO_HUNKS, 0.85)
        self.assertEqual(sc.UMBRAL_DIFUSO_LINEA, 0.90)
        self.assertEqual(sc.UMBRAL_DIFUSO_BLOQUE, 0.80)

class TestMostrarDiffInteractivo(unittest.TestCase):
    """Flag --mostrar-diff y preview interactivo (v6.3.0)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc630md_")
        self.nombre = "app.py"
        self.path = Path(self.tmp) / self.nombre
        self.base = "x = 1\ny = 2\nz = x + y\n"
        self.nuevo = self.base + "r = z * 2\n"
        self.path.write_text(self.base, encoding="utf-8")
        self.parche = sc._generar_parche(self.base, self.nuevo, self.nombre)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _entrada(self, *respuestas):
        it = iter(respuestas)
        return lambda *a, **k: next(it)

    def test_mostrar_diff_aplica(self):
        # --mostrar-diff + preguntar "a" → el parche se aplica.
        parche = self.parche
        self.assertTrue(sc._aplicar_parche_con_resolucion(
            parche, self.tmp, mostrar_diff=True,
            preguntar=self._entrada("a")))
        self.assertIn("r = z * 2\n", self.path.read_text(encoding="utf-8"))

    def test_mostrar_diff_cancela(self):
        parche = self.parche
        ok = sc._aplicar_parche_con_resolucion(
            parche, self.tmp, mostrar_diff=True,
            preguntar=self._entrada("c"))
        self.assertFalse(ok)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.base)

    def test_mostrar_diff_edita_manualmente(self):
        parche = self.parche
        ok = sc._aplicar_parche_con_resolucion(
            parche, self.tmp, mostrar_diff=True,
            preguntar=self._entrada("e"))
        self.assertFalse(ok)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.base)

    def test_mostrar_diff_con_fallo_muestra_diff(self):
        # Un parche que NO coincide con el archivo hace fallar la resolución
        # incremental; con --mostrar-diff se muestra el diff y un error claro,
        # dejando el archivo intacto (todo-o-nada).
        base = "def foo():\n    return 1\n"
        path = Path(self.tmp) / "b.py"
        path.write_text(base, encoding="utf-8")
        parche = ("--- a/b.py\n+++ b/b.py\n@@ -1,2 +1,2 @@\n"
                  " class Otro:\n"
                  "-    metodo_inexistente_absoluto()\n"
                  "+    otro_metodo()\n")
        with mock.patch.object(sc, "_mostrar_diff_parche") as m, \
             mock.patch.object(sc, "error") as e:
            ok = sc._aplicar_hunks_incremental(
                parche, self.tmp, mostrar_diff=True)
            self.assertFalse(ok)
            self.assertTrue(m.called)
            self.assertTrue(e.called)
        self.assertEqual(path.read_text(encoding="utf-8"), base)

    def test_sin_mostrar_diff_no_pregunta_y_aplica(self):
        parche = sc._generar_parche(self.base, self.nuevo, self.nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        self.assertIn("r = z * 2\n", self.path.read_text(encoding="utf-8"))