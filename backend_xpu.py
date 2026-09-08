#!/usr/bin/env python3
"""Backend de inferencia para GPUs Intel XPU (Arc, Flex, Max) — Fase 18.

Permite ejecutar modelos de Hugging Face en tarjetas Intel Arc usando
**IPEX-LLM** (ruta preferida, con optimización low-bit) o, como fallback,
``intel-extension-for-pytorch`` (IPEX) clásico. El módulo es completamente
opcional: si las dependencias no están instaladas o no hay XPU disponible,
el proveedor lanza un error claro (con instrucciones de instalación) y
SnapContext sigue funcionando con los demás proveedores.

v6.35.0 (Fase 18): soporte oficial para la **serie B (Battlemage)** —
p. ej. Arc B70 — mediante ``ipex-llm>=2.2.0``, la primera versión con
soporte estable de Battlemage. La carga preferida usa cuantización low-bit
(``sym_int4`` por defecto) para aprovechar la VRAM (p. ej. 32 GB en la B70).

Rutas de carga (en orden de preferencia):
  1. ``ipex_llm.transformers.AutoModelForCausalLM`` con low-bit (recomendada).
  2. ``transformers`` + ``ipex.optimize`` (IPEX clásico, legacy).

Configuración en ``config.json``::

    "xpu": {
      "model": "Qwen/Qwen3.5-35B-A3B",
      "max_tokens": 500,
      "temperature": 0.7,
      "device": "xpu",
      "low_bit": "sym_int4"
    }

Dependencias (instalar con ``pip install snapcontext[xpu]``):
    torch>=2.5.0, intel-extension-for-pytorch>=2.5.0, ipex-llm>=2.2.0,
    transformers>=4.40.0, accelerate>=0.30.0

Guía completa de instalación (oneAPI, drivers, Level Zero): ``docs/XPU.md``.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, cast

__all__ = [
    "LOW_BIT_DEFECTO",
    "MAX_TOKENS_DEFECTO",
    "MENSAJE_DEPS_XPU",
    "MENSAJE_SIN_HARDWARE",
    "MODELO_XPU_DEFECTO",
    "TEMPERATURE_DEFECTO",
    "BackendXPU",
    "XPUInference",
    "cargar_modelo_xpu",
    "limpiar_cache_xpu",
    "xpu_disponible",
]

logger = logging.getLogger(__name__)

MODELO_XPU_DEFECTO: str = "Qwen/Qwen3.5-35B-A3B"
MAX_TOKENS_DEFECTO: int = 500
TEMPERATURE_DEFECTO: float = 0.7
DEVICE_DEFECTO: str = "xpu"
# Fase 18: bits de cuantización low-bit por defecto (IPEX-LLM). "sym_int4"
# ofrece el mejor equilibrio calidad/VRAM para la serie B (Battlemage).
LOW_BIT_DEFECTO: str = "sym_int4"

MENSAJE_DEPS_XPU: str = (
    "Para usar Intel XPU se necesitan las dependencias opcionales "
    "(torch, transformers e ipex-llm>=2.2.0 o intel-extension-for-pytorch).\n"
    "  Instala con:  pip install snapcontext[xpu]\n"
    "  Guía completa (oneAPI, drivers Arc B-series, Level Zero): docs/XPU.md"
)
MENSAJE_SIN_HARDWARE: str = (
    "No se detectó ninguna GPU Intel XPU. Revisa que:\n"
    "  1) Los drivers de tu Arc (serie B/Battlemage incluida) están instalados.\n"
    "  2) Tienes Intel oneAPI Base Toolkit 2025+ y Level Zero.\n"
    "  3) torch ve la GPU (torch.xpu.is_available()).\n"
    "Guía paso a paso: docs/XPU.md"
)

# Caché global de modelos cargados: {modelo_id: XPUInference}
_MODELOS_CACHE: dict[str, "XPUInference"] = {}


def _detectar_gpu_intel() -> dict[str, Any]:
    """Detecta GPUs Intel disponibles (Fase 18).

    Prueba, en orden: ``torch.xpu`` (ruta moderna), IPEX clásico y por último
    nada. Devuelve un dict con las claves ``disponible``, ``nombre``,
    ``memoria_total`` (bytes; 0 si se desconoce) y ``backend``. **Nunca
    lanza**: ante cualquier error devuelve ``{'disponible': False}``.
    """
    # 1) Ruta moderna: torch.xpu (torch>=2.5 detecta Battlemage con drivers
    #    recientes de Level Zero).
    try:
        torch = importlib.import_module("torch")
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            nombre = "Intel XPU"
            memoria = 0
            try:
                nombre = cast(str, torch.xpu.get_device_name(0))
            except Exception:
                pass
            try:
                memoria = int(torch.xpu.get_device_properties(0).total_memory)
            except Exception:
                pass
            return {
                "disponible": True,
                "nombre": nombre,
                "memoria_total": memoria,
                "backend": "ipex",
            }
    except Exception:
        pass

    # 2) Fallback: IPEX directamente (sin forma sencilla de leer la VRAM).
    try:
        ipex = importlib.import_module("intel_extension_for_pytorch")
        if hasattr(ipex, "xpu") and ipex.xpu.is_available():
            return {
                "disponible": True,
                "nombre": "Intel XPU (IPEX)",
                "memoria_total": 0,
                "backend": "ipex",
            }
    except Exception:
        pass

    return {"disponible": False, "nombre": None, "memoria_total": 0, "backend": None}


def xpu_disponible() -> bool:
    """Comprueba si hay una Intel XPU disponible (v6.34.0; Fase 18)."""
    return bool(_detectar_gpu_intel().get("disponible"))


def _comprobar_dependencias_xpu() -> bool:
    """Comprueba (sin romper) si las dependencias XPU están instaladas.

    True si ``torch`` + ``transformers`` están presentes y hay backend de
    aceleración (``ipex_llm`` o ``intel_extension_for_pytorch``).
    """
    for mod in ("torch", "transformers"):
        try:
            importlib.import_module(mod)
        except Exception:
            return False
    for mod in ("ipex_llm", "intel_extension_for_pytorch"):
        try:
            importlib.import_module(mod)
            return True
        except Exception:
            continue
    return False


def _nombre_gpu() -> str:
    """Devuelve el nombre de la GPU Intel (o 'Intel XPU' genérico)."""
    return str(_detectar_gpu_intel().get("nombre") or "Intel XPU")


class XPUInference:
    """Motor de inferencia local para GPUs Intel XPU (v6.34.0)."""

    def __init__(
        self,
        model_name: str = MODELO_XPU_DEFECTO,
        device: str = DEVICE_DEFECTO,
        max_tokens: int = MAX_TOKENS_DEFECTO,
        temperature: float = TEMPERATURE_DEFECTO,
        low_bit: str = LOW_BIT_DEFECTO,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.low_bit = low_bit
        self._tokenizer = None
        self._model = None
        self._cargado = False
        self.backend: str | None = None  # "ipex-llm" | "ipex"

    def _cargar_modelo(self) -> None:
        """Carga el tokenizador y el modelo optimizado para XPU (Fase 18).

        Ruta 1 (preferida): IPEX-LLM con low-bit (serie B/Battlemage).
        Ruta 2 (fallback): IPEX clásico. Si faltan dependencias, lanza
        ``RuntimeError`` con ``MENSAJE_DEPS_XPU``. Se invoca la primera vez
        que se llama a ``generate``; si ya cargó, es un noop.
        """
        if self._cargado:
            return
        try:
            self._cargar_con_ipex_llm()
            return
        except ImportError:
            logger.debug("ipex-llm no disponible; probando IPEX clásico")
        except Exception as exc:
            # Fallo no relacionado con dependencias (p. ej. modelo inexistente):
            # no reintentamos con la ruta legacy (descargaría en fp16, pesado).
            raise RuntimeError(
                f"Error al cargar '{self.model_name}' con IPEX-LLM: {exc}\n{MENSAJE_DEPS_XPU}"
            ) from exc
        self._cargar_con_ipex()

    def _cargar_con_ipex_llm(self) -> None:
        """Ruta moderna: IPEX-LLM con cuantización low-bit (Fase 18)."""
        import torch
        from ipex_llm.transformers import AutoModelForCausalLM
        from transformers import AutoTokenizer

        if not torch.xpu.is_available():
            raise RuntimeError(MENSAJE_SIN_HARDWARE)

        logger.info("Cargando tokenizador de %s ...", self.model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        logger.info(
            "Cargando %s en %s (low-bit %s, ipex-llm) ...",
            self.model_name,
            self.device,
            self.low_bit,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            load_in_low_bit=self.low_bit,
            optimize_model=True,
            torch_dtype=torch.float16,
            trust_remote_code=True,
        )
        self._model = self._model.to(self.device)
        self._model.eval()
        self.backend = "ipex-llm"
        self._cargado = True

    def _cargar_con_ipex(self) -> None:
        """Ruta legacy: transformers + ``ipex.optimize`` (IPEX clásico)."""
        try:
            import intel_extension_for_pytorch as ipex  # type: ignore[no-redef]
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(MENSAJE_DEPS_XPU) from exc

        if not torch.xpu.is_available():
            raise RuntimeError(MENSAJE_SIN_HARDWARE)

        logger.info("Cargando tokenizador de %s ...", self.model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, trust_remote_code=True)
        logger.info("Cargando modelo %s en %s (IPEX clásico) ...", self.model_name, self.device)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=torch.float16,
            trust_remote_code=True,
        )
        self._model = ipex.optimize(self._model, dtype=torch.float16)  # type: ignore[assignment,operator]
        self._model = self._model.to(self.device)  # type: ignore[attr-defined]
        self._model.eval()  # type: ignore[attr-defined]
        self.backend = "ipex"
        self._cargado = True

    def generate(
        self,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Genera una respuesta para *prompt* (con overrides opcionales)."""
        self._cargar_modelo()
        import torch

        assert self._tokenizer is not None
        assert self._model is not None
        _max_tokens = int(max_tokens if max_tokens is not None else self.max_tokens)
        _temperature = float(temperature if temperature is not None else self.temperature)
        entradas = self._tokenizer(prompt, return_tensors="pt")
        entradas = {k: v.to(self.device) for k, v in entradas.items()}

        with torch.no_grad():
            salida = self._model.generate(
                **entradas,
                max_new_tokens=_max_tokens,
                temperature=_temperature,
                do_sample=_temperature > 0,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generados = salida[0][entradas["input_ids"].shape[1] :]
        return self._tokenizer.decode(generados, skip_special_tokens=True)

    def generar(self, prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str:
        """Alias de ``generate`` con la firma propuesta en la Fase 18."""
        return self.generate(prompt, max_tokens=max_tokens, temperature=temperature)

    @property
    def cargado(self) -> bool:
        """True si el modelo ya fue cargado en memoria."""
        return self._cargado


# Alias con el nombre de clase propuesto en la Fase 18.
BackendXPU = XPUInference


def cargar_modelo_xpu(
    model_name: str | None = None,
    config: dict[str, Any] | None = None,
) -> XPUInference:
    """Carga (o reutiliza) un modelo XPU desde la caché global (v6.34.0).

    Los argumentos se toman de ``config["xpu"]`` si está presente; los valores
    por defecto cubren el resto. Fase 18: admite ``low_bit`` (IPEX-LLM).
    """
    cfg: dict[str, Any] = {}
    if isinstance(config, dict):
        cfg = config.get("xpu") or {}

    nombre = (
        model_name
        or cfg.get("model")
        or os.environ.get("SNAPCONTEXT_XPU_MODEL")
        or MODELO_XPU_DEFECTO
    )
    max_tokens = int(cfg.get("max_tokens", MAX_TOKENS_DEFECTO))
    temperature = float(cfg.get("temperature", TEMPERATURE_DEFECTO))
    device = str(cfg.get("device", DEVICE_DEFECTO))
    low_bit = str(cfg.get("low_bit", LOW_BIT_DEFECTO))

    clave = f"{nombre}|{device}|{max_tokens}|{temperature}|{low_bit}"
    if clave not in _MODELOS_CACHE:
        _MODELOS_CACHE[clave] = XPUInference(
            model_name=nombre,
            device=device,
            max_tokens=max_tokens,
            temperature=temperature,
            low_bit=low_bit,
        )
    return _MODELOS_CACHE[clave]


def limpiar_cache_xpu() -> None:
    """Vacía la caché global de modelos XPU (útil en tests)."""
    _MODELOS_CACHE.clear()
