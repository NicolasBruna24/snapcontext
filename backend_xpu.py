#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backend de inferencia para GPUs Intel XPU (v6.34.0).

Permite ejecutar modelos de Hugging Face en tarjetas Intel Arc usando
``intel-extension-for-pytorch`` (IPEX). El módulo es completamente opcional:
si ``torch``/``ipex`` no están instalados o no hay XPU disponible, el
proveedor lanza un error claro y SnapContext sigue funcionando con los
 demás proveedores.

Configuración en ``config.json``::

    "xpu": {
      "model": "Qwen/Qwen3.5-35B-A3B",
      "max_tokens": 500,
      "temperature": 0.7,
      "device": "xpu"
    }

Dependencias (instalar con ``pip install snapcontext[xpu]``):
    torch>=2.5.0, intel-extension-for-pytorch>=2.5.0,
    transformers>=4.40.0, accelerate>=0.30.0
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

__all__ = [
    "XPUInference",
    "cargar_modelo_xpu",
    "xpu_disponible",
    "MODELO_XPU_DEFECTO",
    "MAX_TOKENS_DEFECTO",
    "TEMPERATURE_DEFECTO",
]

logger = logging.getLogger(__name__)

MODELO_XPU_DEFECTO: str = "Qwen/Qwen3.5-35B-A3B"
MAX_TOKENS_DEFECTO: int = 500
TEMPERATURE_DEFECTO: float = 0.7
DEVICE_DEFECTO: str = "xpu"

# Caché global de modelos cargados: {modelo_id: XPUInference}
_MODELOS_CACHE: Dict[str, "XPUInference"] = {}


def xpu_disponible() -> bool:
    """Comprueba si hay una Intel XPU disponible (v6.34.0)."""
    try:
        import torch
        return hasattr(torch, "xpu") and torch.xpu.is_available()
    except Exception:
        return False


def _nombre_gpu() -> str:
    """Devuelve el nombre de la GPU Intel (o 'Intel XPU' genérico)."""
    try:
        import torch
        if hasattr(torch.xpu, "get_device_name"):
            return torch.xpu.get_device_name(0)
    except Exception:
        pass
    return "Intel XPU"


class XPUInference:
    """Motor de inferencia local para GPUs Intel XPU (v6.34.0)."""

    def __init__(
        self,
        model_name: str = MODELO_XPU_DEFECTO,
        device: str = DEVICE_DEFECTO,
        max_tokens: int = MAX_TOKENS_DEFECTO,
        temperature: float = TEMPERATURE_DEFECTO,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._tokenizer = None
        self._model = None
        self._cargado = False

    def _cargar_modelo(self) -> None:
        """Carga el tokenizador, el modelo y optimiza para XPU (v6.34.0).

        Se invoca la primera vez que se llama a ``generate``. Si ya fue
        cargado es un noop.
        """
        if self._cargado:
            return
        try:
            import torch
            import ipex
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                f"Para usar XPU se necesitan torch, ipex y transformers: {exc}"
            ) from exc

        if not torch.xpu.is_available():
            raise RuntimeError(
                "Intel XPU no detectado. Revisa la instalación de IPEX y drivers."
            )

        logger.info("Cargando tokenizador de %s ...", self.model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_name, trust_remote_code=True
        )
        logger.info("Cargando modelo %s en %s ...", self.model_name, self.device)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=torch.float16,
            trust_remote_code=True,
        )
        self._model = self._model.to(self.device)
        self._model = ipex.optimize(self._model, dtype=torch.float16)
        self._model.eval()
        self._cargado = True

    def generate(self, prompt: str) -> str:
        """Genera una respuesta para *prompt* usando el modelo en XPU."""
        self._cargar_modelo()
        import torch

        entradas = self._tokenizer(prompt, return_tensors="pt")
        entradas = {k: v.to(self.device) for k, v in entradas.items()}

        with torch.no_grad():
            salida = self._model.generate(
                **entradas,
                max_new_tokens=self.max_tokens,
                temperature=self.temperature,
                do_sample=self.temperature > 0,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        generados = salida[0][entradas["input_ids"].shape[1]:]
        return self._tokenizer.decode(generados, skip_special_tokens=True)

    @property
    def cargado(self) -> bool:
        """True si el modelo ya fue cargado en memoria."""
        return self._cargado


def cargar_modelo_xpu(
    model_name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> XPUInference:
    """Carga (o reutiliza) un modelo XPU desde la caché global (v6.34.0).

    Los argumentos se toman de ``config["xpu"]`` si está presente; los valores
    por defecto cubren el resto.
    """
    cfg = {}
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
    device = cfg.get("device", DEVICE_DEFECTO)

    clave = f"{nombre}|{device}|{max_tokens}|{temperature}"
    if clave not in _MODELOS_CACHE:
        _MODELOS_CACHE[clave] = XPUInference(
            model_name=nombre,
            device=device,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    return _MODELOS_CACHE[clave]


def limpiar_cache_xpu() -> None:
    """Vacía la caché global de modelos XPU (útil en tests)."""
    _MODELOS_CACHE.clear()