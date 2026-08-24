#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v3.1.1: ayuda sin argumentos y bienvenida en el primer uso."""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc


class _EstadoTemporal(unittest.TestCase):
    """Aísla ESTADO_PATH en un directorio temporal por test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.estado = Path(self._tmp.name) / "estado.json"
        patcher = mock.patch.object(sc, "ESTADO_PATH", self.estado)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)


class TestAyudaSinArgumentos(_EstadoTemporal):
    """`snapcontext` sin argumentos muestra la ayuda resumida (código 0)."""

    def test_main_sin_args_muestra_ayuda_y_devuelve_0(self):
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer), \
                mock.patch.object(sc, "_registrar_manejadores_senales"):
            codigo = sc.main([])
        self.assertEqual(codigo, 0)
        salida = buffer.getvalue()
        self.assertIn("snapcontext --bienvenida", salida)
        self.assertIn("snapcontext --diagnostico", salida)
        self.assertIn("Ejemplos:", salida)

    def test_mostrar_ayuda_resumida_contiene_comandos_clave(self):
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            sc._mostrar_ayuda_resumida()
        salida = buffer.getvalue()
        for fragmento in ("--init", "--reparar", "--demo", "--chat",
                          "--plan", "ollama"):
            self.assertIn(fragmento, salida)


class TestEstadoPrimerUso(_EstadoTemporal):
    """Helpers de estado: primer_uso en ~/.snapcontext/estado.json."""

    def test_estado_ausente_significa_primer_uso(self):
        self.assertFalse(self.estado.exists())
        self.assertTrue(sc._primer_uso_pendiente())

    def test_marcar_completado_crea_estado(self):
        sc._marcar_primer_uso_completado()
        self.assertTrue(self.estado.exists())
        datos = json.loads(self.estado.read_text(encoding="utf-8"))
        self.assertFalse(datos["primer_uso"])
        self.assertFalse(sc._primer_uso_pendiente())

    def test_primer_uso_false_en_archivo(self):
        self.estado.write_text(json.dumps({"primer_uso": False}),
                               encoding="utf-8")
        self.assertFalse(sc._primer_uso_pendiente())

    def test_estado_corrupto_se_trata_como_primer_uso(self):
        self.estado.write_text("{no es json", encoding="utf-8")
        self.assertTrue(sc._primer_uso_pendiente())


class TestBienvenidaAutomatica(_EstadoTemporal):
    """Primer uso → tutorial automático; después ya no se repite."""

    def _ejecutar_main_con_mocks(self):
        llamadas = []
        with mock.patch.object(sc, "_tutorial_interactivo",
                               side_effect=lambda: llamadas.append(1) or 0), \
                mock.patch.object(sc, "_entrada_interactiva",
                                  return_value=True), \
                mock.patch.object(sc, "_registrar_manejadores_senales"), \
                mock.patch.object(sc, "_limpiar_historial", return_value=True), \
                mock.patch.object(sys, "stdout", io.StringIO()), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            codigo = sc.main(["--historial-limpiar"])
        return codigo, llamadas

    def test_primera_ejecucion_lanza_bienvenida_y_marca_estado(self):
        codigo, llamadas = self._ejecutar_main_con_mocks()
        self.assertEqual(codigo, 0)
        self.assertEqual(len(llamadas), 1)
        self.assertFalse(sc._primer_uso_pendiente())

    def test_segunda_ejecucion_no_repite_bienvenida(self):
        self._ejecutar_main_con_mocks()
        _, llamadas = self._ejecutar_main_con_mocks()
        self.assertEqual(len(llamadas), 0)

    def test_entrada_no_interactiva_no_bloquea(self):
        with mock.patch.object(sc, "_tutorial_interactivo") as tutorial, \
                mock.patch.object(sc, "_entrada_interactiva",
                                  return_value=False), \
                mock.patch.object(sc, "_registrar_manejadores_senales"), \
                mock.patch.object(sc, "_limpiar_historial", return_value=True), \
                mock.patch.object(sys, "stdout", io.StringIO()), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            sc.main(["--historial-limpiar"])
        tutorial.assert_not_called()
        # El estado no se marca: se mostrará en una sesión interactiva real.
        self.assertTrue(sc._primer_uso_pendiente())


class TestBienvenidaExplicita(_EstadoTemporal):
    """--bienvenida explícito ejecuta el tutorial y marca el estado."""

    def test_flag_bienvenida_marca_primer_uso(self):
        with mock.patch.object(sc, "_tutorial_interactivo", return_value=0), \
                mock.patch.object(sc, "_registrar_manejadores_senales"), \
                mock.patch.object(sys, "stdout", io.StringIO()):
            codigo = sc.main(["--bienvenida"])
        self.assertEqual(codigo, 0)
        self.assertFalse(sc._primer_uso_pendiente())


if __name__ == "__main__":
    unittest.main()
