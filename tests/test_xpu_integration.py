#!/usr/bin/env python3
"""Test de integración XPU — solo se ejecuta con hardware Intel real (Fase 18).

Requiere:
  - GPU Intel Arc/Flex/Max visible por Level Zero.
  - pip install snapcontext[xpu]  (torch, ipex-llm>=2.2.0 o IPEX)

Si no hay hardware/dependencias, el módulo completo se salta (sin fallo),
de modo que es seguro ejecutarlo en CI.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend_xpu as xpu

_MODELO_REAL = "Qwen/Qwen2.5-0.5B-Instruct"  # pequeño, para tests de humo


def _hay_xpu() -> bool:
    try:
        return xpu.xpu_disponible() and xpu._comprobar_dependencias_xpu()
    except Exception:
        return False


@unittest.skipUnless(_hay_xpu(), "No hay GPU Intel XPU o faltan dependencias (snapcontext[xpu])")
class TestXPUHardware(unittest.TestCase):
    """Inferencia real en hardware Intel Arc (serie B incluida)."""

    def test_deteccion_reporta_gpu(self):
        det = xpu._detectar_gpu_intel()
        self.assertTrue(det["disponible"])
        self.assertTrue(det["nombre"])
        print(f"\nGPU detectada: {det['nombre']} "
              f"({det['memoria_total'] / 1024**3:.1f} GB, backend={det['backend']})")

    def test_inferencia_real(self):
        """Carga low-bit y genera texto con un modelo pequeño."""
        motor = xpu.XPUInference(model_name=_MODELO_REAL, max_tokens=32, temperature=0.0)
        salida = motor.generate("Di exactamente: hola")
        self.assertIsInstance(salida, str)
        self.assertGreater(len(salida.strip()), 0)
        print(f"\nBackend usado: {motor.backend} | salida: {salida!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)