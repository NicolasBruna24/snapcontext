#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v0.10.0: Claude (Anthropic), --chat, historial persistente,
lectura de archivos y ejecución de comandos genéricos."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc


class TestProveedorAnthropic(unittest.TestCase):
    def test_anthropic_registrado(self):
        self.assertIn("anthropic", sc.PROVEEDORES)
        cfg = sc.PROVEEDORES["anthropic"]
        self.assertEqual(cfg["tipo"], "anthropic")
        self.assertEqual(cfg["clave_env"], "ANTHROPIC_API_KEY")
        self.assertEqual(cfg["modelo_default"], "claude-3-5-sonnet-20241022")

    def test_despacho_tipo_anthropic(self):
        """seleccionar_archivos() redirige a seleccionar_archivos_con_anthropic."""
        with mock.patch.object(
            sc, "seleccionar_archivos_con_anthropic",
            return_value=["a.py"],
        ) as fab:
            resultado = sc.seleccionar_archivos(
                "consulta", ["a.py", "b.py"], proveedor="anthropic")
        self.assertEqual(resultado, ["a.py"])
        fab.assert_called_once()

    def test_falta_clave_lanza_runtimeerror(self):
        if sc.anthropic is None:
            self.skipTest("librería 'anthropic' no instalada")
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                sc.seleccionar_archivos_con_anthropic("c", ["a.py"])
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_libreria_faltante_mensaje_instalacion(self):
        """Sin librería instalada, se sugiere pip install snapcontext[anthropic]."""
        original = sc.anthropic
        sc.anthropic = None
        try:
            with self.assertRaises(RuntimeError) as ctx:
                sc.seleccionar_archivos_con_anthropic("c", ["a.py"])
            self.assertIn("snapcontext[anthropic]", str(ctx.exception))
        finally:
            sc.anthropic = original


class TestHistorialPersistente(unittest.TestCase):
    def setUp(self):
        # Historial aislado en un directorio temporal para no tocar ~/.snapcontext.
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        parche = mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp)
        parche.start()
        self.addCleanup(parche.stop)
        self.addCleanup(self.tmp.cleanup)
        # HISTORIAL_PATH se calculó al importar; lo re-apuntamos al tmp.
        parche2 = mock.patch.object(
            sc, "HISTORIAL_PATH", self.dir_tmp / "historial.json")
        parche2.start()
        self.addCleanup(parche2.stop)

    def test_cargar_vacio_si_no_existe(self):
        self.assertEqual(sc._cargar_historial(), [])

    def test_guardar_y_cargar(self):
        entrada = {
            "fecha": "2026-08-22T10:00:00",
            "consulta": "arreglar login",
            "archivos": ["lib/login.dart"],
            "resultado": "éxito",
            "duracion": 12.5,
        }
        self.assertTrue(sc._guardar_historial(entrada))
        datos = json.loads((self.dir_tmp / "historial.json").read_text(
            encoding="utf-8"))
        self.assertEqual(datos, [entrada])
        self.assertEqual(sc._cargar_historial(), [entrada])

    def test_limpiar_borra_el_archivo(self):
        sc._guardar_historial({"consulta": "x", "resultado": "éxito"})
        self.assertTrue((self.dir_tmp / "historial.json").exists())
        self.assertTrue(sc._limpiar_historial())
        self.assertFalse((self.dir_tmp / "historial.json").exists())
        self.assertTrue(sc._limpiar_historial())  # sin archivo también ok

    def test_mostrar_historial_devuelve_conteo(self):
        for i in range(3):
            sc._guardar_historial({"consulta": f"tarea {i}", "resultado": "éxito"})
        self.assertEqual(sc._mostrar_historial(), 3)

    def test_recorte_del_historial(self):
        limite = getattr(sc, "MAX_HISTORIAL_ENTRADAS", 200)
        for i in range(limite + 10):
            sc._guardar_historial({"consulta": f"t{i}", "resultado": "éxito"})
        self.assertLessEqual(len(sc._cargar_historial()), limite)


class TestUtilidadesAgente(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        (self.dir_tmp / "hola.txt").write_text("contenido", encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def test_leer_archivo_relativa(self):
        import os
        anterior = os.getcwd()
        os.chdir(self.dir_tmp)
        try:
            self.assertEqual(sc._leer_archivo("hola.txt"), "contenido")
        finally:
            os.chdir(anterior)

    def test_leer_archivo_absoluta_e_inexistente(self):
        ruta = self.dir_tmp / "hola.txt"
        self.assertEqual(sc._leer_archivo(str(ruta)), "contenido")
        self.assertIsNone(sc._leer_archivo(str(self.dir_tmp / "no.txt")))
        self.assertIsNone(sc._leer_archivo(str(self.dir_tmp)))  # directorio

    def test_ejecutar_comando_listado(self):
        comando = "dir" if sys.platform.startswith("win") else "ls"
        codigo, stdout, _ = sc._ejecutar_comando(comando, str(self.dir_tmp))
        self.assertEqual(codigo, 0)
        self.assertIn("hola.txt", stdout)

    def test_ejecutar_comando_codigo_error(self):
        comando = "cmd /c exit 3" if sys.platform.startswith("win") else "exit 3"
        codigo, _, _ = sc._ejecutar_comando(comando, str(self.dir_tmp))
        self.assertEqual(codigo, 3)

    def test_ejecutar_comando_directorio_invalido(self):
        codigo, _, stderr = sc._ejecutar_comando(
            "dir" if sys.platform.startswith("win") else "ls",
            str(self.dir_tmp / "no_existe"))
        self.assertEqual(codigo, -1)
        self.assertTrue(stderr)


class TestFlagsCLI(unittest.TestCase):
    def _parse(self, argv):
        return sc.crear_parser().parse_args(argv)

    def test_flag_chat(self):
        args = self._parse(["--chat"])
        self.assertTrue(args.chat)

    def test_flag_historial(self):
        self.assertTrue(self._parse(["--historial"]).historial)

    def test_flag_historial_limpiar(self):
        self.assertTrue(self._parse(["--historial-limpiar"]).historial_limpiar)

    def test_provider_anthropic_aceptado_por_argparse(self):
        args = self._parse(
            ["--provider", "anthropic", "--vista-previa", "consulta"])
        self.assertEqual(args.provider, "anthropic")
        self.assertTrue(args.vista_previa)

    def test_version_es_0_10_0(self):
        self.assertEqual(sc.VERSION, "0.10.0")


if __name__ == "__main__":
    unittest.main()
