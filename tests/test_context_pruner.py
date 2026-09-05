#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v6.32.0: Pruning proactivo de contexto.

Cubre:
  - Detección de resultados extensos (stdout, stderr, contenido, diff).
  - Resultados cortos (sin cambios).
  - Resumen con LLM (mockeado) y heurística simple.
  - Extracción de metadatos clave.
  - Poda de resultados (prune_resultado).
  - Configuración personalizada.
  - Integración con flags CLI.
  - Compatibilidad (sin activar = sin cambios).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import context_pruner as cp  # noqa: E402
import snapcontext as sc     # noqa: E402


# 1) Detección de resultados extensos
class TestEsResultadoExtenso(unittest.TestCase):
    def test_stdout_extenso(self):
        resultado = {"stdout": "linea\n" * 15}
        self.assertTrue(cp.es_resultado_extenso(resultado, umbral_lineas=10))

    def test_stdout_corto_no_es_extenso(self):
        resultado = {"stdout": "hola\nmundo"}
        self.assertFalse(cp.es_resultado_extenso(resultado, umbral_lineas=10))

    def test_stderr_extenso(self):
        resultado = {"stderr": "error\n" * 20}
        self.assertTrue(cp.es_resultado_extenso(resultado, umbral_lineas=10))

    def test_contenido_extenso(self):
        resultado = {"contenido": list(range(50))}
        self.assertTrue(cp.es_resultado_extenso(resultado, umbral_lineas=10))

    def test_diff_extenso(self):
        resultado = {"diff": "+linea\n" * 20}
        self.assertTrue(cp.es_resultado_extenso(resultado, umbral_lineas=10))

    def test_no_dict_nunca_es_extenso(self):
        self.assertFalse(cp.es_resultado_extenso("texto plano"))
        self.assertFalse(cp.es_resultado_extenso(None))
        self.assertFalse(cp.es_resultado_extenso(123))

    def test_diccionario_vacio(self):
        self.assertFalse(cp.es_resultado_extenso({}))

    def test_umbral_personalizado(self):
        resultado = {"stdout": "a\nb\nc\nd\ne"}
        self.assertTrue(cp.es_resultado_extenso(resultado, umbral_lineas=3))
        self.assertFalse(cp.es_resultado_extenso(resultado, umbral_lineas=10))

    def test_tipos_podables_personalizados(self):
        resultado = {"stdout": "a\n" * 20, "otro": "b\n" * 3}
        self.assertTrue(cp.es_resultado_extenso(
            resultado, umbral_lineas=10, tipos_podables=["stdout"]))
        self.assertFalse(cp.es_resultado_extenso(
            resultado, umbral_lineas=10, tipos_podables=["otro"]))


# 2) Resumen de una línea
class TestResumirLinea(unittest.TestCase):
    def test_texto_corto_sin_cambios(self):
        self.assertEqual(cp.resumir_linea("hola mundo"), "hola mundo")

    def test_texto_vacio(self):
        self.assertEqual(cp.resumir_linea(""), "")

    def test_texto_multilinea_heuristica(self):
        texto = "primera línea\nsegunda línea\ntercera línea"
        resultado = cp.resumir_linea(texto)
        self.assertIn("primera línea", resultado)
        self.assertIn("2 líneas más", resultado)

    def test_texto_con_lineas_vacias(self):
        texto = "\n\nprimera\n\nsegunda\n"
        resultado = cp.resumir_linea(texto)
        self.assertIn("primera", resultado)

    def test_usar_llm(self):
        def fake_llm(pedido):
            return "Resumen generado por LLM"
        resultado = cp.resumir_linea("a\nb\nc\nd\ne", usar_llm=True,
                                     proveedor_llm=fake_llm)
        self.assertEqual(resultado, "Resumen generado por LLM")

    def test_llm_falla_degradacion_heuristica(self):
        def fake_llm_fallo(pedido):
            raise RuntimeError("API caída")
        texto = "primera\nsegunda\ntercera"
        resultado = cp.resumir_linea(texto, usar_llm=True,
                                     proveedor_llm=fake_llm_fallo)
        self.assertIn("primera", resultado)

    def test_max_lineas_1(self):
        texto = "linea1\nlinea2"
        resultado = cp.resumir_linea(texto, max_lineas=1)
        self.assertIn("linea1", resultado)
        self.assertIn("1 líneas más", resultado)


# 3) Metadatos clave
class TestObtenerMetadatosClave(unittest.TestCase):
    def test_preserva_codigo_retorno(self):
        resultado = {"ok": False, "codigo": 1, "stdout": "error\n" * 100}
        metadatos = cp.obtener_metadatos_clave(resultado, "ejecutar_comando")
        self.assertEqual(metadatos["ok"], False)
        self.assertEqual(metadatos["codigo"], 1)

    def test_preserva_archivo_y_error(self):
        resultado = {"ruta": "main.py", "error": "SyntaxError", "lineas": 42}
        metadatos = cp.obtener_metadatos_clave(resultado)
        self.assertEqual(metadatos["ruta"], "main.py")
        self.assertEqual(metadatos["error"], "SyntaxError")

    def test_no_dict_devuelve_vacio(self):
        self.assertEqual(cp.obtener_metadatos_clave("texto"), {})

    def test_tipo_herramienta_se_incluye(self):
        resultado = {"stdout": "data"}
        metadatos = cp.obtener_metadatos_clave(resultado, "leer_archivo")
        self.assertEqual(metadatos["accion"], "leer_archivo")

    def test_accion_existente_no_sobrescribe(self):
        resultado = {"accion": "editar_archivo", "ruta": "test.py"}
        metadatos = cp.obtener_metadatos_clave(resultado, "otra")
        self.assertEqual(metadatos["accion"], "editar_archivo")


# 4) Poda de resultados
class TestPruneResultado(unittest.TestCase):
    def test_resultado_corto_sin_cambios(self):
        resultado = {"ok": True, "stdout": "hola"}
        podado = cp.prune_resultado(resultado, umbral_lineas=10)
        self.assertIs(podado, resultado)

    def test_resultado_extenso_es_podado(self):
        resultado = {"ok": True, "stdout": "linea\n" * 20}
        podado = cp.prune_resultado(resultado, umbral_lineas=10)
        self.assertTrue(podado.get("_pruned"))
        self.assertIn("19 líneas más", str(podado["stdout"]))

    def test_no_mutacion_original(self):
        resultado = {"stdout": "a\n" * 20}
        podado = cp.prune_resultado(resultado, umbral_lineas=10)
        self.assertNotEqual(resultado["stdout"], podado["stdout"])

    def test_preserva_metadatos_en_prune(self):
        resultado = {"ok": False, "codigo": 1, "stderr": "error\n" * 15}
        podado = cp.prune_resultado(resultado, umbral_lineas=10)
        self.assertEqual(podado["_metadatos"]["ok"], False)
        self.assertEqual(podado["_metadatos"]["codigo"], 1)

    def test_resumen_contiene_info(self):
        resultado = {"stdout": "primera\n" + "otra\n" * 15}
        podado = cp.prune_resultado(resultado, umbral_lineas=10)
        self.assertIn("primera", podado["_resumen"])

    def test_prune_con_llm(self):
        def fake_llm(pedido):
            return "Error de sintaxis en línea 42"
        resultado = {"stdout": "SyntaxError\n  File 'main.py', line 42\n" + "..\n" * 20}
        podado = cp.prune_resultado(resultado, umbral_lineas=10,
                                    usar_llm=True, proveedor_llm=fake_llm)
        self.assertTrue(podado.get("_pruned"))
        self.assertEqual(podado["stdout"], "Error de sintaxis en línea 42")

    def test_no_dict_devuelve_igual(self):
        self.assertEqual(cp.prune_resultado("texto"), "texto")

    def test_umbral_personalizado(self):
        resultado = {"stdout": "a\nb\nc\nd\ne\nf"}
        podado = cp.prune_resultado(resultado, umbral_lineas=3)
        self.assertTrue(podado.get("_pruned"))
        podado2 = cp.prune_resultado(resultado, umbral_lineas=10)
        self.assertIs(podado2, resultado)

    def test_campos_multiples_podables(self):
        resultado = {
            "stdout": "salida\n" * 20,
            "stderr": "error\n" * 20,
            "ok": True,
        }
        podado = cp.prune_resultado(resultado, umbral_lineas=10)
        self.assertTrue(podado.get("_pruned"))
        self.assertIn("19 líneas más", podado["stdout"])
        self.assertIn("19 líneas más", podado["stderr"])
        self.assertEqual(podado["ok"], True)


# 5) Configuración
class TestConfiguracionPruning(unittest.TestCase):
    def test_config_defecto(self):
        cfg = cp.configuracion_pruning()
        self.assertTrue(cfg["activo"])
        self.assertEqual(cfg["umbral_lineas"], 10)
        self.assertTrue(cfg["usar_llm"])

    def test_config_personalizada(self):
        cfg = cp.configuracion_pruning({
            "pruning": {"umbral_lineas": 25, "usar_llm": False}
        })
        self.assertEqual(cfg["umbral_lineas"], 25)
        self.assertFalse(cfg["usar_llm"])

    def test_config_activo_false(self):
        cfg = cp.configuracion_pruning({"pruning": {"activo": False}})
        self.assertFalse(cfg["activo"])

    def test_config_umbral_invalido_usa_defecto(self):
        cfg = cp.configuracion_pruning({"pruning": {"umbral_lineas": "abc"}})
        self.assertEqual(cfg["umbral_lineas"], 10)

    def test_config_sin_pruning_section(self):
        cfg = cp.configuracion_pruning({"otra": "cosa"})
        self.assertEqual(cfg["umbral_lineas"], 10)

    def test_config_tipos_podables_personalizados(self):
        cfg = cp.configuracion_pruning({
            "pruning": {"tipos_podables": ["stdout"]}
        })
        self.assertEqual(cfg["tipos_podables"], ["stdout"])


# 6) Flags CLI
class TestFlagsCLI(unittest.TestCase):
    def test_prune_context_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--prune-context"])
        self.assertTrue(args.prune_context)

    def test_no_prune_context_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--no-prune-context"])
        self.assertFalse(args.prune_context)

    def test_prune_umbral_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--prune-umbral", "25"])
        self.assertEqual(args.prune_umbral, 25)

    def test_prune_umbral_defecto_none(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.prune_umbral)

    def test_por_defecto_none(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.prune_context)


# 7) Integración (helpers snapcontext)
class TestIntegracionPruning(unittest.TestCase):
    def test_resolver_pruning_activo_por_defecto(self):
        self.assertTrue(sc._resolver_pruning(None))

    def test_resolver_pruning_explicito(self):
        self.assertTrue(sc._resolver_pruning(True))
        self.assertFalse(sc._resolver_pruning(False))

    def test_podar_si_extenso_con_helper(self):
        sc._configurar_pruning(True)
        resultado = {"stdout": "linea\n" * 20, "ok": True}
        podado = sc.podar_si_extenso(resultado, "ejecutar_comando", umbral_lineas=10)
        self.assertTrue(podado.get("_pruned"))
        self.assertEqual(podado["ok"], True)
        sc._configurar_pruning(None)

    def test_podar_si_extenso_desactivado(self):
        sc._configurar_pruning(False)
        resultado = {"stdout": "linea\n" * 20}
        podado = sc.podar_si_extenso(resultado, "ejecutar_comando", umbral_lineas=10)
        self.assertIs(podado, resultado)
        sc._configurar_pruning(None)

    def test_podar_resultado_corto_sin_cambios(self):
        sc._configurar_pruning(True)
        resultado = {"stdout": "hola"}
        podado = sc.podar_si_extenso(resultado, umbral_lineas=10)
        self.assertIs(podado, resultado)
        sc._configurar_pruning(None)


# 6) Flags CLI
class TestFlagsCLI(unittest.TestCase):
    def test_prune_context_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--prune-context"])
        self.assertTrue(args.prune_context)

    def test_no_prune_context_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--no-prune-context"])
        self.assertFalse(args.prune_context)

    def test_prune_umbral_flag(self):
        args = sc.crear_parser().parse_args(["consulta", "--prune-umbral", "25"])
        self.assertEqual(args.prune_umbral, 25)

    def test_prune_umbral_defecto_none(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.prune_umbral)

    def test_por_defecto_none(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.prune_context)


# 7) Integración (helpers snapcontext)
class TestIntegracionPruning(unittest.TestCase):
    def test_resolver_pruning_activo_por_defecto(self):
        self.assertTrue(sc._resolver_pruning(None))

    def test_resolver_pruning_explicito(self):
        self.assertTrue(sc._resolver_pruning(True))
        self.assertFalse(sc._resolver_pruning(False))

    def test_podar_si_extenso_con_helper(self):
        sc._configurar_pruning(True)
        resultado = {"stdout": "linea\n" * 20, "ok": True}
        podado = sc.podar_si_extenso(resultado, "ejecutar_comando", umbral_lineas=10)
        self.assertTrue(podado.get("_pruned"))
        self.assertEqual(podado["ok"], True)

    def test_podar_si_extenso_desactivado(self):
        sc._configurar_pruning(False)
        resultado = {"stdout": "linea\n" * 20}
        podado = sc.podar_si_extenso(resultado, "ejecutar_comando", umbral_lineas=10)
        self.assertIs(podado, resultado)
        sc._configurar_pruning(None)

    def test_podar_resultado_corto_sin_cambios(self):
        sc._configurar_pruning(True)
        resultado = {"stdout": "hola"}
        podado = sc.podar_si_extenso(resultado, umbral_lineas=10)
        self.assertIs(podado, resultado)
        sc._configurar_pruning(None)