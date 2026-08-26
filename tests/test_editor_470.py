#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests v4.7.0: análisis de impacto por dependencias (M2) y contexto
selectivo para archivos grandes (M3)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agentes as ag
import snapcontext as sc


def _contenido_grande(num_funcs: int = 120, lineas_por_func: int = 10) -> str:
    """Genera un archivo Python sintético con muchas funciones pequeñas."""
    partes = ["# archivo grande sintético"]
    for i in range(num_funcs):
        partes.append(f"def funcion_{i:03d}():")
        for _ in range(lineas_por_func - 2):
            partes.append(f"    MARCA_{i:03d} = {i}")
        partes.append(f"    return MARCA_{i:03d}")
        partes.append("")
    return "\n".join(partes) + "\n"


class TestAnalisisImpacto(unittest.TestCase):
    """M2 (v4.7.0): advertencia de dependientes antes de editar."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc470imp_")
        # main.py depende de utils.py → editar utils.py afecta a main.py.
        (Path(self.tmp) / "main.py").write_text(
            "from utils import helper\n\nhelper()\n", encoding="utf-8")
        (Path(self.tmp) / "utils.py").write_text(
            "# utilidades\ndef helper():\n    pass\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_advierte_de_dependientes_en_modo_auto(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(sc, "aviso") as avisos:
            resultado = editor._analizar_impacto_previo(
                ["utils.py"], self.tmp, auto=True)
        self.assertEqual(resultado, ["utils.py"])
        mensajes = " ".join(str(c.args[0]) for c in avisos.call_args_list)
        self.assertIn("main.py", mensajes)
        self.assertIn("utils.py", mensajes)

    def test_sin_dependientes_no_avisa(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(sc, "aviso") as avisos:
            resultado = editor._analizar_impacto_previo(
                ["main.py"], self.tmp, auto=True)
        self.assertEqual(resultado, ["main.py"])
        self.assertEqual(avisos.call_count, 0)

    def test_menu_anadir_dependientes(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch("builtins.input", return_value="s"):
            resultado = editor._analizar_impacto_previo(
                ["utils.py"], self.tmp, auto=False)
        self.assertIn("utils.py", resultado)
        self.assertIn("main.py", resultado)

    def test_menu_abortar_devuelve_none(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch("builtins.input", return_value="a"):
            resultado = editor._analizar_impacto_previo(
                ["utils.py"], self.tmp, auto=False)
        self.assertIsNone(resultado)

    def test_auto_nunca_pregunta_por_input(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch("builtins.input",
                        side_effect=AssertionError("input() en modo auto")):
            resultado = editor._analizar_impacto_previo(
                ["utils.py"], self.tmp, auto=True)
        self.assertEqual(resultado, ["utils.py"])

    def test_ejecutar_cancela_si_el_usuario_aborta(self):
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(ag.AgenteEditorPropio,
                               "_analizar_impacto_previo", return_value=None):
            ok = editor.ejecutar(["utils.py"], "tarea", directorio=self.tmp)
        self.assertFalse(ok)


class TestContextoSelectivo(unittest.TestCase):
    """M3 (v4.7.0): archivos grandes → resumen AST + bloques relevantes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc470ctx_")
        self.grande = _contenido_grande()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archivo_pequeno_se_inyecta_completo(self):
        contenido = "def pequena():\n    return 1\n"
        prompt, truncado = ag._construir_prompt_edicion(
            "parche", "mejora pequena", "p.py", contenido, "python", False)
        self.assertFalse(truncado)
        self.assertIn("def pequena():", prompt)

    def test_archivo_grande_usa_resumen_ast_y_no_el_contenido_completo(self):
        self.assertGreater(self.grande.count("\n"), 600)
        prompt, truncado = ag._construir_prompt_edicion(
            "parche", "mejora la funcion_017", "grande.py", self.grande,
            "python", False)
        self.assertTrue(truncado)
        # Resumen AST y restricción presentes.
        self.assertIn("[RESUMEN DEL ARCHIVO (AST)]:", prompt)
        self.assertIn("[RESTRICCIÓN]", prompt)
        self.assertIn("funcion_017", prompt)
        # El bloque relevante está, pero el resto del archivo NO.
        self.assertIn("MARCA_017", prompt)
        self.assertNotIn("MARCA_555", prompt)
        self.assertNotIn("def funcion_555():", prompt)

    def test_sin_funcion_mencionada_usa_proximidad(self):
        prompt, truncado = ag._construir_prompt_edicion(
            "parche", "arregla lo que falla", "grande.py", self.grande,
            "python", False)
        self.assertTrue(truncado)
        # Fallback por proximidad: primeros bloques dentro del presupuesto.
        self.assertIn("MARCA_000", prompt)
        self.assertNotIn("MARCA_900", prompt)

    def test_splicear_bloque_reemplaza_y_conserva(self):
        original = ("a = 1\n"
                    "def objetivo():\n"
                    "    return 2\n"
                    "b = 3\n")
        resultado = sc._splicear_bloque(
            original,
            "def objetivo():\n    return 2\n",
            "def objetivo():\n    return 42\n")
        self.assertIsNotNone(resultado)
        self.assertIn("return 42", resultado)
        self.assertIn("a = 1", resultado)
        self.assertIn("b = 3", resultado)
        # Bloque ajeno → None (sin confianza suficiente).
        self.assertIsNone(sc._splicear_bloque(
            original, "def inexistente():\n    pass\n", "x\n"))

    def test_sobrescribir_truncado_reensambla_el_archivo(self):
        archivo = Path(self.tmp) / "grande.py"
        archivo.write_text(self.grande, encoding="utf-8")
        antes = ("def funcion_003():\n"
                 + "    MARCA_003 = 3\n" * 8
                 + "    return MARCA_003")
        despues = antes.replace("return MARCA_003",
                                "return MARCA_003 + 1000")
        respuesta = f"<<<ANTES>>>\n{antes}\n<<<DESPUES>>>\n{despues}\n<<<FIN>>>"
        backups = Path(self.tmp) / "_backups"
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(sc, "BACKUPS_DIR", backups), \
                mock.patch.object(sc, "_enviar_al_proveedor",
                                  return_value=respuesta), \
                mock.patch.object(sc, "MAX_CONTEXT_LINES", 30):
            ok = editor._aplicar_modo_sobrescribir(
                "grande.py", "modifica funcion_003", self.grande, None,
                self.tmp, validar=False)
        self.assertTrue(ok)
        final = archivo.read_text(encoding="utf-8")
        self.assertIn("return MARCA_003 + 1000", final)
        # El resto del archivo permanece intacto.
        self.assertIn("MARCA_099", final)
        self.assertIn("def funcion_110():", final)

    def test_respuesta_vacia_no_borra_el_archivo(self):
        """v4.7.0: si el reensamblado falla (respuesta vacía), NUNCA se escribe."""
        archivo = Path(self.tmp) / "grande.py"
        archivo.write_text(self.grande, encoding="utf-8")
        backups = Path(self.tmp) / "_backups"
        editor = ag.AgenteEditorPropio()
        with mock.patch.object(sc, "BACKUPS_DIR", backups), \
                mock.patch.object(sc, "_enviar_al_proveedor",
                                  return_value="sin marcadores"), \
                mock.patch.object(sc, "MAX_CONTEXT_LINES", 30):
            ok = editor._aplicar_modo_sobrescribir(
                "grande.py", "modifica funcion_003", self.grande, None,
                self.tmp, validar=False)
        self.assertFalse(ok)
        # El archivo conserva TODO su contenido original.
        self.assertEqual(archivo.read_text(encoding="utf-8"), self.grande)

    def test_extraer_bloques_ast_detecta_primer_nivel(self):
        bloques = sc._extraer_bloques_ast(self.grande)
        nombres = [b["nombre"] for b in bloques]
        self.assertEqual(len(bloques), 120)
        self.assertEqual(nombres[0], "funcion_000")
        self.assertEqual(nombres[-1], "funcion_119")


if __name__ == "__main__":
    unittest.main()
