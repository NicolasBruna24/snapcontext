#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v6.34.0: Soporte para Intel XPU (GPU Intel Arc).

Cubre:
  - Detección de XPU (mockeando torch.xpu).
  - Carga de modelo (mockeando transformers e ipex).
  - Generación de inferencia.
  - Caché de modelos.
  - Configuración desde config.json.
  - Flags CLI.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend_xpu as xpu  # noqa: E402


class TestXpuDisponible(unittest.TestCase):
    def test_xpu_no_disponible_sin_torch(self):
        """Si torch no se puede importar, xpu_disponible es False."""
        with mock.patch.dict(sys.modules, {"torch": None}):
            self.assertFalse(xpu.xpu_disponible())

    def test_xpu_no_disponible_sin_atributo(self):
        """Si torch no tiene xpu, xpu_disponible es False."""
        fake_torch = mock.MagicMock(spec=[])
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertFalse(xpu.xpu_disponible())

    def test_xpu_disponible(self):
        """Si torch.xpu.is_available() es True, xpu_disponible es True."""
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = True
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertTrue(xpu.xpu_disponible())

    def test_xpu_disponible_false(self):
        """Si torch.xpu.is_available() es False, xpu_disponible es False."""
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = False
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertFalse(xpu.xpu_disponible())


class TestNombreGPU(unittest.TestCase):
    def test_nombre_gpu_generico(self):
        """Si no se puede obtener el nombre, devuelve 'Intel XPU'."""
        with mock.patch.dict(sys.modules, {"torch": None}):
            self.assertEqual(xpu._nombre_gpu(), "Intel XPU")

    def test_nombre_gpu_real(self):
        """Si torch.xpu.get_device_name funciona, devuelve el nombre."""
        fake_torch = mock.MagicMock()
        fake_torch.xpu.get_device_name.return_value = "Intel Arc B70 Pro"
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertEqual(xpu._nombre_gpu(), "Intel Arc B70 Pro")


class TestXPUInference(unittest.TestCase):
    def test_init_valores_defecto(self):
        """Los valores por defecto son correctos."""
        motor = xpu.XPUInference()
        self.assertEqual(motor.model_name, xpu.MODELO_XPU_DEFECTO)
        self.assertEqual(motor.device, xpu.DEVICE_DEFECTO)
        self.assertEqual(motor.max_tokens, xpu.MAX_TOKENS_DEFECTO)
        self.assertEqual(motor.temperature, xpu.TEMPERATURE_DEFECTO)
        self.assertFalse(motor.cargado)

    def test_init_valores_custom(self):
        """Los valores personalizados se guardan."""
        motor = xpu.XPUInference(
            model_name="test-model", device="xpu",
            max_tokens=1000, temperature=0.5)
        self.assertEqual(motor.model_name, "test-model")
        self.assertEqual(motor.max_tokens, 1000)
        self.assertEqual(motor.temperature, 0.5)

    def test_cargar_modelo_sin_torch(self):
        """Sin torch, lanza RuntimeError."""
        motor = xpu.XPUInference()
        with mock.patch.dict(sys.modules, {"torch": None, "ipex": None}):
            with self.assertRaises(RuntimeError):
                motor._cargar_modelo()

    def test_cargar_modelo_sin_xpu(self):
        """Sin XPU disponible, lanza RuntimeError."""
        motor = xpu.XPUInference()
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = False
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            with self.assertRaises(RuntimeError):
                motor._cargar_modelo()

    def test_cargar_modelo_exitoso(self):
        """Carga exitosa del modelo."""
        motor = xpu.XPUInference()
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = True
        fake_torch.float16 = "float16"

        fake_ipex = mock.MagicMock()
        fake_tokenizer_cls = mock.MagicMock()
        fake_model_cls = mock.MagicMock()
        fake_model = mock.MagicMock()
        fake_model_cls.from_pretrained.return_value = fake_model

        with mock.patch.dict(sys.modules, {
            "torch": fake_torch,
            "ipex": fake_ipex,
            "transformers": mock.MagicMock(
                AutoTokenizer=fake_tokenizer_cls,
                AutoModelForCausalLM=fake_model_cls,
            ),
        }):
            motor._cargar_modelo()
            self.assertTrue(motor.cargado)
            fake_ipex.optimize.assert_called_once()

    def test_generate(self):
        """Generación de inferencia."""
        motor = xpu.XPUInference()
        motor._cargado = True
        motor._tokenizer = mock.MagicMock()
        motor._tokenizer.eos_token_id = 0
        fake_entradas = {"input_ids": mock.MagicMock(shape=[1, 5])}
        motor._tokenizer.return_value = fake_entradas
        motor._model = mock.MagicMock()
        motor._model.generate.return_value = [[1, 2, 3, 4, 5, 6, 7]]
        fake_torch = mock.MagicMock()
        fake_torch.no_grad.return_value.__enter__ = mock.MagicMock()
        fake_torch.no_grad.return_value.__exit__ = mock.MagicMock()
        motor._tokenizer.decode.return_value = "Respuesta generada"
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            resultado = motor.generate("Prompt de prueba")
            self.assertEqual(resultado, "Respuesta generada")


class TestCargarModeloXpu(unittest.TestCase):
    def tearDown(self):
        xpu.limpiar_cache_xpu()

    def test_carga_basica(self):
        """Carga básica sin config."""
        motor = xpu.cargar_modelo_xpu()
        self.assertIsInstance(motor, xpu.XPUInference)

    def test_carga_con_config(self):
        """Carga con config personalizada."""
        config = {"xpu": {"model": "test-model", "max_tokens": 2000}}
        motor = xpu.cargar_modelo_xpu(config=config)
        self.assertEqual(motor.model_name, "test-model")
        self.assertEqual(motor.max_tokens, 2000)

    def test_cache_mismo_modelo(self):
        """El mismo modelo se reutiliza desde caché."""
        motor1 = xpu.cargar_modelo_xpu("test-model")
        motor2 = xpu.cargar_modelo_xpu("test-model")
        self.assertIs(motor1, motor2)

    def test_cache_modelos_diferentes(self):
        """Modelos diferentes son instancias diferentes."""
        motor1 = xpu.cargar_modelo_xpu("modelo-a")
        motor2 = xpu.cargar_modelo_xpu("modelo-b")
        self.assertIsNot(motor1, motor2)


class TestLimpiarCache(unittest.TestCase):
    def test_limpiar_cache(self):
        """Limpiar caché elimina los modelos."""
        xpu.cargar_modelo_xpu("test-model")
        self.assertGreater(len(xpu._MODELOS_CACHE), 0)
        xpu.limpiar_cache_xpu()
        self.assertEqual(len(xpu._MODELOS_CACHE), 0)


class TestFlagsCLI(unittest.TestCase):
    def test_xpu_flag_provider(self):
        """El flag --provider xpu es reconocido."""
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["consulta", "--provider", "xpu"])
        self.assertEqual(args.provider, "xpu")

    def test_xpu_model_flag(self):
        """El flag --xpu-model es reconocido."""
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["consulta", "--xpu-model", "test"])
        self.assertEqual(args.xpu_model, "test")

    def test_xpu_max_tokens_flag(self):
        """El flag --xpu-max-tokens es reconocido."""
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["consulta", "--xpu-max-tokens", "1000"])
        self.assertEqual(args.xpu_max_tokens, 1000)

    def test_xpu_temperature_flag(self):
        """El flag --xpu-temperature es reconocido."""
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["consulta", "--xpu-temperature", "0.5"])
        self.assertEqual(args.xpu_temperature, 0.5)