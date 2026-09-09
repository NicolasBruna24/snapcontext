#!/usr/bin/env python3
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

import backend_xpu as xpu


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
            model_name="test-model", device="xpu", max_tokens=1000, temperature=0.5
        )
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

        with mock.patch.dict(
            sys.modules,
            {
                "torch": fake_torch,
                "ipex_llm": None,
                "intel_extension_for_pytorch": fake_ipex,
                "transformers": mock.MagicMock(
                    AutoTokenizer=fake_tokenizer_cls,
                    AutoModelForCausalLM=fake_model_cls,
                ),
            },
        ):
            motor._cargar_modelo()
            self.assertTrue(motor.cargado)
            self.assertEqual(motor.backend, "ipex")
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


# --- Fase 18: detección moderna, IPEX-LLM (low-bit) y serie B (Battlemage) ---


class TestDetectarGpuIntel(unittest.TestCase):
    def test_estructura_sin_hardware(self):
        """Sin torch ni IPEX: dict con disponible=False y nunca lanza."""
        vacio = {"torch": None, "intel_extension_for_pytorch": None}
        with mock.patch.dict(sys.modules, vacio):
            det = xpu._detectar_gpu_intel()
        self.assertFalse(det["disponible"])
        self.assertIn("nombre", det)
        self.assertIn("memoria_total", det)
        self.assertIn("backend", det)

    def test_torch_xpu_disponible_con_propiedades(self):
        """Ruta moderna: torch.xpu disponible devuelve nombre y memoria."""
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = True
        fake_torch.xpu.get_device_name.return_value = "Intel(R) Arc(TM) B70"
        fake_torch.xpu.get_device_properties.return_value.total_memory = 32 * 1024**3
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            det = xpu._detectar_gpu_intel()
        self.assertTrue(det["disponible"])
        self.assertEqual(det["nombre"], "Intel(R) Arc(TM) B70")
        self.assertEqual(det["memoria_total"], 32 * 1024**3)
        self.assertEqual(det["backend"], "ipex")

    def test_torch_xpu_disponible_sin_propiedades(self):
        """Si get_device_properties falla, sigue disponible con memoria 0."""
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = True
        fake_torch.xpu.get_device_properties.side_effect = RuntimeError("boom")
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            det = xpu._detectar_gpu_intel()
        self.assertTrue(det["disponible"])
        self.assertEqual(det["memoria_total"], 0)

    def test_fallback_ipex_clasico(self):
        """Sin torch.xpu, cae a intel_extension_for_pytorch.xpu."""
        fake_torch = mock.MagicMock(spec=[])  # sin atributo xpu
        fake_ipex = mock.MagicMock()
        fake_ipex.xpu.is_available.return_value = True
        mods = {"torch": fake_torch, "intel_extension_for_pytorch": fake_ipex}
        with mock.patch.dict(sys.modules, mods):
            det = xpu._detectar_gpu_intel()
        self.assertTrue(det["disponible"])
        self.assertEqual(det["nombre"], "Intel XPU (IPEX)")

    def test_torch_xpu_lanza_excepcion_no_propaga(self):
        """Si is_available lanza, _detectar_gpu_intel no propaga."""
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.side_effect = RuntimeError("driver roto")
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertFalse(xpu._detectar_gpu_intel()["disponible"])


class TestCargaIPEXLLM(unittest.TestCase):
    """Ruta moderna de carga: ipex_llm.transformers con low-bit."""

    def _mods_ipex_llm(self):
        """Dict de sys.modules que simula ipex_llm + transformers + torch."""
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = True
        fake_torch.float16 = "float16"
        fake_tokenizer_cls = mock.MagicMock()
        fake_model_cls = mock.MagicMock()
        fake_model = mock.MagicMock()
        fake_model.to.return_value = fake_model
        fake_model_cls.from_pretrained.return_value = fake_model
        fake_submod = mock.MagicMock(AutoModelForCausalLM=fake_model_cls)
        fake_ipex_llm = mock.MagicMock(transformers=fake_submod)
        mods = {
            "torch": fake_torch,
            "ipex_llm": fake_ipex_llm,
            "ipex_llm.transformers": fake_submod,
            "transformers": mock.MagicMock(AutoTokenizer=fake_tokenizer_cls),
        }
        return mods, fake_model_cls, fake_model

    def test_carga_preferente_con_low_bit(self):
        """Con ipex_llm instalado, se usa la ruta IPEX-LLM (no ipex.optimize)."""
        motor = xpu.XPUInference()
        mods, fake_cls, fake_model = self._mods_ipex_llm()
        with mock.patch.dict(sys.modules, mods):
            motor._cargar_modelo()
        self.assertTrue(motor.cargado)
        self.assertEqual(motor.backend, "ipex-llm")
        _, kwargs = fake_cls.from_pretrained.call_args
        self.assertEqual(kwargs["load_in_low_bit"], xpu.LOW_BIT_DEFECTO)
        fake_model.to.assert_called_once_with("xpu")
        fake_model.eval.assert_called_once()

    def test_low_bit_personalizado(self):
        """low_bit se pasa tal cual a from_pretrained."""
        motor = xpu.XPUInference(low_bit="sym_int8")
        mods, fake_cls, _ = self._mods_ipex_llm()
        with mock.patch.dict(sys.modules, mods):
            motor._cargar_modelo()
        _, kwargs = fake_cls.from_pretrained.call_args
        self.assertEqual(kwargs["load_in_low_bit"], "sym_int8")

    def test_sin_ipex_llm_cae_a_ipex_clasico(self):
        """Sin ipex_llm, el fallback usa ipex.optimize (ruta legacy)."""
        motor = xpu.XPUInference()
        fake_torch = mock.MagicMock()
        fake_torch.xpu.is_available.return_value = True
        fake_torch.float16 = "float16"
        fake_ipex = mock.MagicMock()
        fake_tokenizer_cls = mock.MagicMock()
        fake_model_cls = mock.MagicMock()
        fake_model = mock.MagicMock()
        fake_model.to.return_value = fake_model
        fake_model_cls.from_pretrained.return_value = fake_model
        mods = {
            "torch": fake_torch,
            "ipex_llm": None,
            "intel_extension_for_pytorch": fake_ipex,
            "transformers": mock.MagicMock(
                AutoTokenizer=fake_tokenizer_cls,
                AutoModelForCausalLM=fake_model_cls,
            ),
        }
        with mock.patch.dict(sys.modules, mods):
            motor._cargar_modelo()
        self.assertTrue(motor.cargado)
        self.assertEqual(motor.backend, "ipex")
        fake_ipex.optimize.assert_called_once()

    def test_sin_dependencias_mensaje_guiado(self):
        """Sin ipex_llm ni ipex, el mensaje indica cómo instalar (Fase 18)."""
        motor = xpu.XPUInference()
        vacio = {
            "torch": None,
            "ipex_llm": None,
            "intel_extension_for_pytorch": None,
            "transformers": None,
        }
        with mock.patch.dict(sys.modules, vacio):
            with self.assertRaises(RuntimeError) as ctx:
                motor._cargar_modelo()
        self.assertIn("snapcontext[xpu]", str(ctx.exception))

    def test_sin_hardware_mensaje_guiado(self):
        """Sin GPU, el mensaje guía a docs/XPU.md (no traceback)."""
        motor = xpu.XPUInference()
        mods, _, _ = self._mods_ipex_llm()
        mods["torch"].xpu.is_available.return_value = False
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(RuntimeError) as ctx:
                motor._cargar_modelo()
        self.assertIn("docs/XPU.md", str(ctx.exception))


class TestFase18Extras(unittest.TestCase):
    def test_alias_backend_xpu(self):
        """BackendXPU es alias de XPUInference (API Fase 18)."""
        self.assertIs(xpu.BackendXPU, xpu.XPUInference)

    def test_generar_alias(self):
        """generar() delega en generate() con overrides."""
        motor = xpu.XPUInference(max_tokens=10, temperature=0.5)
        motor._cargado = True
        motor._tokenizer = mock.MagicMock()
        motor._tokenizer.eos_token_id = 0
        fake_entradas = {"input_ids": mock.MagicMock(shape=[1, 3])}
        motor._tokenizer.return_value = fake_entradas
        motor._tokenizer.decode.return_value = "hola"
        motor._model = mock.MagicMock()
        motor._model.generate.return_value = [[1] * 8]
        fake_torch = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            self.assertEqual(motor.generar("p", max_tokens=7, temperature=0.1), "hola")
            _, kwargs = motor._model.generate.call_args
            self.assertEqual(kwargs["max_new_tokens"], 7)
            self.assertEqual(kwargs["temperature"], 0.1)

    def test_generate_overrides(self):
        """generate() acepta max_tokens/temperature puntuales."""
        motor = xpu.XPUInference(max_tokens=10, temperature=0.5)
        motor._cargado = True
        motor._tokenizer = mock.MagicMock()
        motor._tokenizer.eos_token_id = 0
        fake_entradas = {"input_ids": mock.MagicMock(shape=[1, 3])}
        motor._tokenizer.return_value = fake_entradas
        motor._tokenizer.decode.return_value = "ok"
        motor._model = mock.MagicMock()
        motor._model.generate.return_value = [[1] * 6]
        fake_torch = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            motor.generate("p")
            _, kwargs = motor._model.generate.call_args
            self.assertEqual(kwargs["max_new_tokens"], 10)
            self.assertEqual(kwargs["temperature"], 0.5)
            self.assertTrue(kwargs["do_sample"])
            motor.generate("p", max_tokens=3, temperature=0.0)
            _, kwargs = motor._model.generate.call_args
            self.assertEqual(kwargs["max_new_tokens"], 3)
            self.assertFalse(kwargs["do_sample"])

    def test_cache_incluye_low_bit(self):
        """La caché distingue modelos por low_bit (Fase 18)."""
        xpu.limpiar_cache_xpu()
        try:
            m1 = xpu.cargar_modelo_xpu("m", config={"xpu": {"low_bit": "sym_int4"}})
            m2 = xpu.cargar_modelo_xpu("m", config={"xpu": {"low_bit": "sym_int8"}})
            m3 = xpu.cargar_modelo_xpu("m", config={"xpu": {"low_bit": "sym_int4"}})
            self.assertIsNot(m1, m2)
            self.assertIs(m1, m3)
            self.assertEqual(m1.low_bit, "sym_int4")
            self.assertEqual(m2.low_bit, "sym_int8")
        finally:
            xpu.limpiar_cache_xpu()

    def test_configuracion_proveedor_xpu(self):
        """La entrada 'xpu' de PROVEEDORES documenta requisitos de serie B."""
        from configuracion import PROVEEDORES

        entry = PROVEEDORES["xpu"]
        self.assertEqual(entry["tipo"], "xpu")
        self.assertFalse(entry["requiere_clave"])
        self.assertTrue(any("ipex-llm" in r for r in entry["requisitos"]))
        self.assertIn("Battlemage", entry["nota"])
        self.assertIn("snapcontext[xpu]", entry["nota"])
