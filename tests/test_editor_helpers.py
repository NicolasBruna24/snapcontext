"""Tests de los helpers puros del editor de parches (editor.py, Fase 2).

Funciones sin dependencias del estado global: se prueban con datos
sintéticos, sin E/S real ni llamadas a proveedores.
"""

from __future__ import annotations

import unittest

from editor import (
    _contar_cambios_parche,
    _lineas_equivalentes,
    _parsear_hunks,
    _quitar_comentario,
    _ratio_bloque,
    _ruta_del_parche,
    _validar_parche_previo,
    _variantes_linea,
)

PARCHE = """--- a/lib/core/app.dart
+++ b/lib/core/app.dart
@@ -1,3 +1,3 @@
 linea uno
-linea dos
+linea dos cambiada
 linea tres
"""


class TestRutaDelParche(unittest.TestCase):
    def test_encabezado_con_prefijo_b(self):
        self.assertEqual(_ruta_del_parche(PARCHE), "lib/core/app.dart")

    def test_encabezado_solo_menos(self):
        self.assertEqual(_ruta_del_parche("--- a/x.py\n@@\n-x\n+y\n"), "x.py")

    def test_diff_de_archivo_nuevo(self):
        # En diffs de archivo nuevo el '---' es /dev/null; manda el '+++'.
        self.assertEqual(_ruta_del_parche("--- /dev/null\n+++ b/nuevo.py\n@@\n+x\n"), "nuevo.py")
        # El encabezado '+++' sin prefijo b/ se devuelve tal cual.
        self.assertEqual(_ruta_del_parche("--- /dev/null\n+++ /dev/null\n"), "/dev/null")

    def test_parche_vacio(self):
        self.assertIsNone(_ruta_del_parche(""))
        self.assertIsNone(_ruta_del_parche(None))

    def test_ruta_con_tabulador(self):
        self.assertEqual(_ruta_del_parche("+++ b/a.py\t2024-01-01\n"), "a.py")


class TestValidarParchePrevio(unittest.TestCase):
    def test_sin_referencia_no_valida(self):
        ok, detalle = _validar_parche_previo(PARCHE, ".", None)
        self.assertTrue(ok)
        self.assertIn("sin validación", detalle)

    def test_parche_sin_encabezado_omite_validacion(self):
        ok, _ = _validar_parche_previo("texto suelto", ".", "contenido")
        self.assertTrue(ok)

    def test_archivo_coincide(self):
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.py").write_text("original\n", encoding="utf-8")
        parche = "--- a/a.py\n+++ b/a.py\n@@\n-x\n+y\n"
        ok, _ = _validar_parche_previo(parche, str(tmp), "original\n")
        self.assertTrue(ok)

    def test_archivo_cambio_concurrente(self):
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp())
        (tmp / "a.py").write_text("modificado\n", encoding="utf-8")
        parche = "--- a/a.py\n+++ b/a.py\n@@\n-x\n+y\n"
        ok, detalle = _validar_parche_previo(parche, str(tmp), "original\n")
        self.assertFalse(ok)
        self.assertIn("cambió", detalle)

    def test_archivo_ya_no_existe(self):
        ok, detalle = _validar_parche_previo("--- a/nada.py\n", ".", "x")
        self.assertFalse(ok)
        self.assertIn("no existe", detalle)


class TestParsearHunks(unittest.TestCase):
    def test_hunk_simple(self):
        hunks = _parsear_hunks(PARCHE)
        self.assertEqual(len(hunks), 1)
        inicio, cambios = hunks[0]
        self.assertEqual(inicio, 1)
        self.assertIn(("+", "linea dos cambiada"), cambios)
        self.assertIn(("-", "linea dos"), cambios)

    def test_hunks_sin_cambios_se_omiten(self):
        texto = "@@ -1,2 +1,2 @@\n igual\n igual2\n"
        self.assertEqual(_parsear_hunks(texto), [])

    def test_parche_vacio(self):
        self.assertEqual(_parsear_hunks(""), [])
        self.assertEqual(_parsear_hunks(None), [])

    def test_varios_hunks(self):
        texto = (
            "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-viejo\n+nuevo\n@@ -10 +10 @@\n-otro\n+otro2\n"
        )
        hunks = _parsear_hunks(texto)
        self.assertEqual(len(hunks), 2)
        self.assertEqual(hunks[1][0], 10)


class TestComentariosYVariantes(unittest.TestCase):
    def test_quitar_comentario_hash(self):
        self.assertEqual(_quitar_comentario("x = 1  # nota"), "x = 1")
        self.assertEqual(_quitar_comentario("# solo comentario"), "")

    def test_no_rompe_urls_ni_hashes_internos(self):
        self.assertEqual(_quitar_comentario('url = "https://x.com"'), 'url = "https://x.com"')
        self.assertEqual(_quitar_comentario("color = '#fff'"), "color = '#fff'")

    def test_quitar_comentario_doble_barra(self):
        self.assertEqual(_quitar_comentario("int x = 1; // nota"), "int x = 1;")
        self.assertEqual(_quitar_comentario("// solo"), "")

    def test_variantes_linea(self):
        cruda, norm, sin_com = _variantes_linea("  a = 1   # fin  ")
        self.assertEqual(cruda, "  a = 1   # fin  ")
        self.assertEqual(norm, "a = 1 # fin")
        self.assertEqual(sin_com, "a = 1")

    def test_lineas_equivalentes(self):
        self.assertTrue(_lineas_equivalentes("a = 1", " a = 1 "))
        self.assertTrue(_lineas_equivalentes("x  # c", "x"))
        self.assertFalse(_lineas_equivalentes("alpha", "beta"))
        self.assertFalse(_lineas_equivalentes("a=1", "a = 1"))


class TestRatioYConteo(unittest.TestCase):
    def test_ratio_bloque_identico(self):
        self.assertGreater(_ratio_bloque("línea de prueba", "línea de prueba"), 0.99)

    def test_ratio_bloque_dispar(self):
        self.assertEqual(_ratio_bloque("abc", "xyz"), 0.0)

    def test_contar_cambios(self):
        anadidas, eliminadas = _contar_cambios_parche(PARCHE)
        self.assertEqual((anadidas, eliminadas), (1, 1))

    def test_contar_cambios_vacio(self):
        self.assertEqual(_contar_cambios_parche(""), (0, 0))
        self.assertEqual(_contar_cambios_parche(None), (0, 0))

    def test_contar_ignora_contexto(self):
        texto = "@@ -1,3 +1,3 @@\n ctx\n+sumado\n-quitado\n"
        self.assertEqual(_contar_cambios_parche(texto), (1, 1))


if __name__ == "__main__":
    unittest.main()
