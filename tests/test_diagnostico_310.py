#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v3.1.0: --diagnostico, --reparar y modo offline con Ollama."""

import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc


def _args(**extra) -> argparse.Namespace:
    base = {"consulta": None, "depurar": False}
    base.update(extra)
    return argparse.Namespace(**base)


class TestFlagsCli310(unittest.TestCase):
    """Los nuevos flags se registran correctamente en el parser."""

    def test_flag_diagnostico(self):
        self.assertTrue(sc.crear_parser().parse_args(["--diagnostico"]
                                                     ).diagnostico)

    def test_flag_reparar(self):
        self.assertTrue(sc.crear_parser().parse_args(["--reparar"]).reparar)

    def test_flag_bienvenida(self):
        self.assertTrue(sc.crear_parser().parse_args(["--bienvenida"]
                                                     ).bienvenida)

    def test_version_es_3_1_0(self):
        self.assertEqual(sc.VERSION, "3.1.0")


class TestApiKeyDetection(unittest.TestCase):
    """hay_api_key_configurada: entorno y config.json."""

    def setUp(self):
        for env in sc.CLAVES_API_CONOCIDAS:
            os.environ.pop(env, None)
        self._patcher = mock.patch.object(
            sc, "cargar_configuracion", return_value={})
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        for env in sc.CLAVES_API_CONOCIDAS:
            os.environ.pop(env, None)

    def test_sin_clave_devuelve_false(self):
        self.assertFalse(sc.hay_api_key_configurada())

    def test_clave_en_entorno(self):
        os.environ["GEMINI_API_KEY"] = "prueba"
        self.assertTrue(sc.hay_api_key_configurada())

    def test_clave_en_config(self):
        self._patcher.stop()
        with mock.patch.object(sc, "cargar_configuracion",
                               return_value={"api_keys": {"gemini": "x"}}):
            self.assertTrue(sc.hay_api_key_configurada())


class TestEleccionModeloLigero(unittest.TestCase):
    """_elegir_modelo_ligero prioriza los modelos ligeros."""

    def test_lista_vacia(self):
        self.assertIsNone(sc._elegir_modelo_ligero([]))

    def test_prefiere_llama32(self):
        self.assertEqual(sc._elegir_modelo_ligero(
            ["llama3.1:70b", "llama3.2", "phi3"]), "llama3.2")

    def test_coincidencia_parcial(self):
        self.assertEqual(sc._elegir_modelo_ligero(
            ["llama3.2:latest", "codellama"]), "llama3.2:latest")

    def test_fallback_primer_modelo(self):
        self.assertEqual(sc._elegir_modelo_ligero(["mistral"]), "mistral")


class TestEstadoOllama(unittest.TestCase):
    """_estado_ollama interpreta la salida de `ollama list`."""

    def test_ollama_no_instalado(self):
        with mock.patch.object(sc, "_listar_modelos_ollama",
                               return_value=([], "No se encontró 'ollama' en "
                                             "el PATH. ¿Está instalado?")):
            estado = sc._estado_ollama()
        self.assertFalse(estado["instalado"])
        self.assertEqual(estado["modelos"], [])

    def test_con_modelos(self):
        with mock.patch.object(sc, "_listar_modelos_ollama",
                               return_value=(["phi3", "llama3.2"], None)):
            estado = sc._estado_ollama()
        self.assertTrue(estado["instalado"])
        self.assertEqual(estado["modelos"], ["phi3", "llama3.2"])


class TestProveedorOffline(unittest.TestCase):
    """Fallback offline de _determinar_proveedor."""

    def setUp(self):
        for env in sc.CLAVES_API_CONOCIDAS:
            os.environ.pop(env, None)

    def tearDown(self):
        for env in sc.CLAVES_API_CONOCIDAS:
            os.environ.pop(env, None)

    def _sin_guardado(self):
        p1 = mock.patch.object(sc, "cargar_configuracion", return_value={})
        p2 = mock.patch.object(sc, "guardar_configuracion", return_value=True)
        p3 = mock.patch.object(sc, "_preguntar_guardar_config",
                               return_value=False)
        return p1, p2, p3

    def test_sin_nada_lanza_mensaje_claro(self):
        p1, p2, p3 = self._sin_guardado()
        with p1, p2, p3, \
                mock.patch.object(sc, "_proveedor_offline", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                sc._determinar_proveedor(_args())
        self.assertIn("No se encontró una API key ni Ollama",
                      str(ctx.exception))
        self.assertIn("https://ollama.com", str(ctx.exception))

    def test_con_ollama_usa_offline(self):
        p1, p2, p3 = self._sin_guardado()
        with p1, p2, p3, \
                mock.patch.object(sc, "_proveedor_offline",
                                  return_value={"provider": "ollama",
                                                "model": "llama3.2"}):
            res = sc._determinar_proveedor(_args())
        self.assertEqual(res, {"provider": "ollama", "model": "llama3.2"})

    def test_proveedor_offline_sin_modelos(self):
        with mock.patch.object(sc, "_estado_ollama",
                               return_value={"instalado": False,
                                             "modelos": [], "error": "x"}):
            self.assertIsNone(sc._proveedor_offline())


class TestDiagnostico(unittest.TestCase):
    """--diagnostico: resumen sin excepciones y código de salida válido."""

    def test_diagnostico_devuelve_codigo_valido(self):
        with mock.patch.object(sys, "stdout"), \
                mock.patch.object(sys, "stderr"):
            codigo = sc._ejecutar_diagnostico(_args(diagnostico=True))
        self.assertIn(codigo, (0, 1, 2))

    def test_estado_memoria_inexistente(self):
        with mock.patch.object(sc, "DB_PATH",
                               Path(os.devnull).parent / "no-existe.db"):
            estado = sc._estado_memoria()
        self.assertFalse(estado["ok"])

    def test_estado_memoria_corrupta(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "memoria.db"
            ruta.write_bytes(b"esto no es sqlite")
            with mock.patch.object(sc, "DB_PATH", ruta):
                estado = sc._estado_memoria()
        self.assertFalse(estado["ok"])
        self.assertIn("corrupta", estado["error"])


if __name__ == "__main__":
    unittest.main()
