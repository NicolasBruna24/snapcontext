#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del ecosistema de plugins (v4.0.0).

Cubre el manifest, el registro de herramientas MCP, la ejecución por
subproceso, instalación local, remove, create y el comando de chat.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapcontext as sc


class BasePlugins(unittest.TestCase):
    """Aísla ~/.snapcontext/plugins en un directorio temporal."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sc400_"))
        self._directorio_original = sc.PLUGINS_DIR
        sc.PLUGINS_DIR = self.tmp / "plugins"
        sc.PLUGINS_DIR.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        sc.PLUGINS_DIR = self._directorio_original

    def _crear_plugin(self, nombre="saludos", habilitado=True,
                      requiere_permiso=False):
        carpeta = sc.PLUGINS_DIR / nombre
        carpeta.mkdir(parents=True, exist_ok=True)
        manifest = {
            "nombre": nombre, "version": "1.0.0", "autor": "tester",
            "descripcion": f"Plugin {nombre}", "permisos": ["archivos"],
            "habilitado": habilitado,
            "herramientas": [{
                "nombre": f"{nombre}_hola",
                "descripcion": "saluda",
                "script": "saluda.py",
                "requiere_permiso": requiere_permiso,
            }],
        }
        (carpeta / "plugin.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        (carpeta / "saluda.py").write_text(
            "import json, sys\n"
            "datos = json.loads(sys.stdin.read() or '{}')\n"
            "print(json.dumps({'ok': True, 'saludo': 'hola ' + "
            "datos.get('nombre', '?')}))\n", encoding="utf-8")
        return carpeta


class TestManifest(BasePlugins):
    def test_manifest_valido(self):
        self._crear_plugin()
        instalados = sc._plugins_instalados()
        self.assertIn("saludos", instalados)
        manifest = instalados["saludos"]
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertTrue(manifest["habilitado"])
        self.assertIn("archivos", manifest["permisos"])

    def test_sin_plugin_json_se_ignora(self):
        (sc.PLUGINS_DIR / "vacío").mkdir()
        self.assertEqual(sc._plugins_instalados(), {})

    def test_manifest_corrupto_se_ignora(self):
        carpeta = sc.PLUGINS_DIR / "roto"
        carpeta.mkdir()
        (carpeta / "plugin.json").write_text("{roto", encoding="utf-8")
        self.assertEqual(sc._plugins_instalados(), {})

    def test_manifest_sin_herramientas_invalido(self):
        carpeta = sc.PLUGINS_DIR / "vacio"
        carpeta.mkdir()
        (carpeta / "plugin.json").write_text(
            json.dumps({"nombre": "vacio"}), encoding="utf-8")
        self.assertNotIn("vacio", sc._plugins_instalados())


class TestRegistroMCP(BasePlugins):
    def test_herramientas_registradas_en_mcp(self):
        self._crear_plugin("util")
        herramientas = sc._cargar_herramientas_mcp()
        cfg = herramientas["util_hola"]
        self.assertEqual(cfg["plugin"], "util")
        self.assertFalse(cfg["requiere_permiso"])
        self.assertIn("saluda.py", cfg["comando"])
        # Las predefinidas siguen ahí.
        self.assertIn("grep", herramientas)

    def test_plugin_deshabilitado_excluido(self):
        self._crear_plugin("apagado", habilitado=False)
        herramientas = sc._cargar_herramientas_mcp()
        self.assertNotIn("apagado_hola", herramientas)

    def test_no_sobreescribe_predefinidas(self):
        self._crear_plugin("grep")   # su herramienta se llamará grep_hola
        self.assertIn("grep_hola", sc._plugins_herramientas())

    def test_ejecucion_por_subproceso(self):
        self._crear_plugin("saludos")
        llamada = sc._ejecutar_herramienta_mcp(
            "saludos_hola", {"nombre": "Ada"}, confirmar=False)
        self.assertTrue(llamada["ok"], llamada)
        self.assertEqual(llamada["resultado"]["saludo"], "hola Ada")

    def test_orquestador_expone_herramientas_de_plugins(self):
        from orquestador import Orquestador
        self._crear_plugin("util")
        orch = Orquestador()
        self.assertIn("util_hola", orch.herramientas_plugins)


class TestInstalacion(BasePlugins):
    def _plugin_fuente(self):
        fuente = self.tmp / "fuente"
        carpeta = fuente / "miplug"
        carpeta.mkdir(parents=True)
        manifest = {
            "nombre": "miplug", "version": "0.2.0", "autor": "autor",
            "descripcion": "de prueba", "permisos": [],
            "herramientas": [{"nombre": "miplug_ping", "script": "ping.py",
                              "requiere_permiso": False}],
        }
        (carpeta / "plugin.json").write_text(json.dumps(manifest),
                                             encoding="utf-8")
        (carpeta / "ping.py").write_text("print('{\"ok\": true}')\n",
                                         encoding="utf-8")
        return fuente / "miplug"

    def test_instalar_desde_carpeta_local(self):
        codigo = sc._plugin_instalar(str(self._plugin_fuente()),
                                     auto=True)
        self.assertEqual(codigo, 0)
        self.assertIn("miplug", sc._plugins_instalados())

    def test_instalar_origen_inexistente_falla(self):
        self.assertEqual(sc._plugin_instalar("/no/existe", auto=True), 1)

    def test_instalacion_externa_cancelada_no_copia(self):
        with mock.patch.object(sc, "_confirmar_accion", return_value=False), \
                mock.patch.object(sc, "_plugin_descargar_zip") as descarga:
            descarga.return_value = None
            self.assertEqual(
                sc._plugin_instalar("usuario/plugin"), 1)

    def test_remove_con_confirmar_false(self):
        self._crear_plugin("borrable")
        self.assertEqual(sc._plugin_remove("borrable", confirmar=False), 0)
        self.assertNotIn("borrable", sc._plugins_instalados())

    def test_remove_inexistente_falla(self):
        self.assertEqual(sc._plugin_remove("fantasma", confirmar=False), 1)

    def test_create_y_update(self):
        self.assertEqual(0, sc._plugin_create("nuevoplug"))
        instalados = sc._plugins_instalados()
        self.assertIn("nuevoplug", instalados)
        self.assertTrue((Path(instalados["nuevoplug"]["ruta"])
                         / "saluda.py").is_file())
        # update sin origen remoto avisa y devuelve 1.
        self.assertEqual(sc._plugin_update("nuevoplug"), 1)
        # duplicado falla.
        self.assertEqual(sc._plugin_create("nuevoplug"), 1)


class TestEstadoYChat(BasePlugins):
    def test_enable_disable(self):
        self._crear_plugin("conmutable")
        self.assertEqual(sc._plugin_cambiar_estado("conmutable", False), 0)
        self.assertNotIn("conmutable_hola", sc._plugins_herramientas())
        self.assertEqual(sc._plugin_cambiar_estado("conmutable", True), 0)
        self.assertIn("conmutable_hola", sc._plugins_herramientas())

    def test_estado_plugin_inexistente_falla(self):
        self.assertEqual(sc._plugin_cambiar_estado("no", True), 1)

    def test_chat_ayuda_incluye_plugin(self):
        self.assertIn("/plugin", sc.AYUDA_CHAT)

    def test_mostrar_sin_plugins_no_explota(self):
        sc._plugin_mostrar()      # solo debe loguear

    def test_comando_plugin_accion_invalida(self):
        self.assertEqual(sc._ejecutar_comando_plugin(["volar"]), 1)


if __name__ == "__main__":
    unittest.main()
