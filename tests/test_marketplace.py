#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del Marketplace MCP (v6.21.0): índice remoto con caché, búsqueda,
instalación/desinstalación, dependencias pip y comandos CLI."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import marketplace as mp  # noqa: E402
import snapcontext as sc  # noqa: E402

INDICE_EJEMPLO = [
    {"nombre": "slack-notifier", "descripcion": "Enviar mensajes a Slack",
     "autor": "NicolasBruna24", "repositorio": "NicolasBruna24/slack",
     "tags": ["slack", "notificaciones"]},
    {"nombre": "jira-link", "descripcion": "Integración con Jira",
     "autor": "acme", "repositorio": "acme/jira-link", "tags": ["jira"]},
]


def _cache_tmp():
    """Re-apunta la caché del marketplace a un archivo temporal."""
    ruta = Path(tempfile.mkdtemp(prefix="sc_mk_")) / "marketplace_cache.json"
    vieja = mp.RUTA_CACHE
    mp.RUTA_CACHE = ruta
    return ruta, vieja


class TestObtenerIndex(unittest.TestCase):
    def setUp(self):
        self.ruta, self.vieja = _cache_tmp()

    def tearDown(self):
        mp.RUTA_CACHE = self.vieja

    def test_descarga_index(self):
        with mock.patch.object(mp.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__ = lambda s: mock.Mock(
                read=lambda: json.dumps(INDICE_EJEMPLO).encode())
            urlopen.return_value.__exit__ = lambda s, *a: None
            indice = mp.obtener_index(forzar=True)
        self.assertEqual(len(indice), 2)
        self.assertTrue(self.ruta.exists())

    def test_cache_se_usa_sin_red(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta.write_text(json.dumps(
            {"descargado": "2100-01-01T00:00:00",
             "index": INDICE_EJEMPLO}), encoding="utf-8")
        with mock.patch.object(mp.urllib.request, "urlopen") as urlopen:
            self.assertEqual(mp.obtener_index(), INDICE_EJEMPLO)
            urlopen.assert_not_called()

    def test_red_caida_usa_cache_caducada(self):
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta.write_text(json.dumps(
            {"descargado": "2000-01-01T00:00:00",
             "index": INDICE_EJEMPLO}), encoding="utf-8")
        with mock.patch.object(mp.urllib.request, "urlopen",
                               side_effect=OSError("sin red")):
            self.assertEqual(mp.obtener_index(forzar=True), INDICE_EJEMPLO)

    def test_red_caida_sin_cache_devuelve_vacio(self):
        with mock.patch.object(mp.urllib.request, "urlopen",
                               side_effect=OSError("sin red")):
            self.assertEqual(mp.obtener_index(forzar=True), [])

    def test_formato_objeto_con_plugins(self):
        with mock.patch.object(mp.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__ = lambda s: mock.Mock(
                read=lambda: json.dumps({"plugins": INDICE_EJEMPLO}).encode())
            urlopen.return_value.__exit__ = lambda s, *a: None
            self.assertEqual(len(mp.obtener_index(forzar=True)), 2)

    def test_mensaje_descargando(self):
        import io
        from contextlib import redirect_stdout
        with mock.patch.object(mp, "_info") as info:
            with mock.patch.object(mp.urllib.request, "urlopen",
                                   side_effect=OSError("x")):
                mp.obtener_index(forzar=True)
        info.assert_any_call("📦 Descargando índice de plugins...")


class TestBuscarPlugins(unittest.TestCase):
    def test_busqueda_por_nombre(self):
        r = mp.buscar_plugins("slack", index=INDICE_EJEMPLO)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["nombre"], "slack-notifier")

    def test_busqueda_por_descripcion(self):
        r = mp.buscar_plugins("jira", index=INDICE_EJEMPLO)
        self.assertEqual(r[0]["nombre"], "jira-link")

    def test_busqueda_por_autor(self):
        r = mp.buscar_plugins("acme", index=INDICE_EJEMPLO)
        self.assertEqual(r[0]["nombre"], "jira-link")

    def test_busqueda_por_tag(self):
        r = mp.buscar_plugins("notificaciones", index=INDICE_EJEMPLO)
        self.assertEqual(r[0]["nombre"], "slack-notifier")

    def test_sin_termino_devuelve_todo(self):
        self.assertEqual(len(mp.buscar_plugins("", index=INDICE_EJEMPLO)), 2)

    def test_sin_resultados(self):
        self.assertEqual(mp.buscar_plugins("zzz", index=INDICE_EJEMPLO), [])

    def test_resolver_plugin_encontrado(self):
        e = mp.resolver_plugin("Jira-Link", index=INDICE_EJEMPLO)
        self.assertIsNotNone(e)
        self.assertEqual(e["repositorio"], "acme/jira-link")

    def test_resolver_plugin_inexistente(self):
        self.assertIsNone(mp.resolver_plugin("nope", index=INDICE_EJEMPLO))


class TestInstalarPlugin(unittest.TestCase):
    def setUp(self):
        self.ruta, self.vieja = _cache_tmp()

    def tearDown(self):
        mp.RUTA_CACHE = self.vieja

    def test_nombre_resuelto_desde_index(self):
        with mock.patch.object(mp, "obtener_index",
                               return_value=INDICE_EJEMPLO), \
                mock.patch.object(mp, "_sc") as fake_sc, \
                mock.patch.object(mp, "_leer_manifest", return_value=None):
            fake_sc.return_value._plugin_instalar.return_value = 0
            codigo = mp.instalar_plugin("jira-link")
        self.assertEqual(codigo, 0)
        fake_sc.return_value._plugin_instalar.assert_called_once_with(
            "acme/jira-link")

    def test_nombre_no_encontrado(self):
        with mock.patch.object(mp, "obtener_index",
                               return_value=INDICE_EJEMPLO):
            self.assertEqual(mp.instalar_plugin("nope"), 1)

    def test_url_directa_delega(self):
        with mock.patch.object(mp, "_sc") as fake_sc:
            fake_sc.return_value._plugin_instalar.return_value = 0
            mp.instalar_plugin("https://github.com/x/y")
        fake_sc.return_value._plugin_instalar.assert_called_once_with(
            "https://github.com/x/y")

    def test_instalacion_fallida(self):
        with mock.patch.object(mp, "obtener_index",
                               return_value=INDICE_EJEMPLO), \
                mock.patch.object(mp, "_sc") as fake_sc:
            fake_sc.return_value._plugin_instalar.return_value = 1
            self.assertEqual(mp.instalar_plugin("jira-link"), 1)

    def test_sin_argumento(self):
        self.assertEqual(mp.instalar_plugin(""), 1)

    def test_mensaje_exito(self):
        with mock.patch.object(mp, "obtener_index",
                               return_value=INDICE_EJEMPLO), \
                mock.patch.object(mp, "_sc") as fake_sc, \
                mock.patch.object(mp, "_leer_manifest", return_value=None), \
                mock.patch.object(mp, "_exito") as exito:
            fake_sc.return_value._plugin_instalar.return_value = 0
            mp.instalar_plugin("jira-link")
        exito.assert_any_call("✅ Plugin jira-link instalado correctamente.")


class TestDependencias(unittest.TestCase):
    def test_sin_dependencias_ok(self):
        self.assertTrue(mp.instalar_dependencias({"name": "x"}, nombre="x"))

    def test_dependencia_instalada(self):
        with mock.patch.object(mp.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(mp.instalar_dependencias(
                {"dependencies": ["pako>=1.0"]}, nombre="pako"))
        args = run.call_args[0][0]
        self.assertIn("--user", args)

    def test_dependencia_fallida_deshabilita_plugin(self):
        with mock.patch.object(mp.subprocess, "run") as run, \
                mock.patch.object(mp, "_sc") as fake_sc, \
                mock.patch.object(mp, "_error"):
            run.return_value.returncode = 1
            run.return_value.stderr = "boom"
            self.assertFalse(mp.instalar_dependencias(
                {"dependencies": ["paquete-roto"]}, nombre="roto"))
        fake_sc.return_value._plugin_cambiar_estado.assert_called_once_with(
            "roto", habilitar=False)


class TestGestionPlugins(unittest.TestCase):
    def test_listar_plugins(self):
        with mock.patch.object(mp, "_sc") as fake_sc:
            fake_sc.return_value._plugins_instalados.return_value = {
                "slack": {"version": "1.0.0", "enabled": True,
                          "tools": [{"name": "enviar"}],
                          "description": "Slack"},
            }
            r = mp.listar_plugins()
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0]["habilitado"])
        self.assertEqual(r[0]["herramientas"], ["enviar"])

    def test_habilitar_y_deshabilitar(self):
        with mock.patch.object(mp, "_sc") as fake_sc:
            fake_sc.return_value._plugin_cambiar_estado.return_value = 0
            self.assertEqual(mp.habilitar_plugin("x"), 0)
            self.assertEqual(mp.deshabilitar_plugin("x"), 0)
        fake_sc.return_value._plugin_cambiar_estado.assert_any_call(
            "x", habilitar=True)
        fake_sc.return_value._plugin_cambiar_estado.assert_any_call(
            "x", habilitar=False)

    def test_desinstalar_delega(self):
        with mock.patch.object(mp, "_sc") as fake_sc:
            fake_sc.return_value._plugin_remove.return_value = 0
            self.assertEqual(mp.desinstalar_plugin("x"), 0)
        fake_sc.return_value._plugin_remove.assert_called_once_with("x")

    def test_actualizar_todos(self):
        with mock.patch.object(mp, "_sc") as fake_sc:
            fake_sc.return_value._plugins_instalados.return_value = {
                "a": {}, "b": {}}
            fake_sc.return_value._plugin_update.return_value = 0
            self.assertEqual(mp.actualizar_plugin(), 0)
        self.assertEqual(fake_sc.return_value._plugin_update.call_count, 2)


class TestCargarPlugins(unittest.TestCase):
    def test_carga_solo_habilitados(self):
        with mock.patch.object(mp, "_sc") as fake_sc, \
                mock.patch.object(mp, "instalar_dependencias",
                                  return_value=True):
            fake_sc.return_value._plugins_instalados.return_value = {
                "on": {"enabled": True,
                       "tools": [{"name": "h1", "description": "d"}]},
                "off": {"enabled": False, "tools": [{"name": "h2"}]},
            }
            r = mp.cargar_plugins_instalados()
        self.assertIn("on", r)
        self.assertNotIn("off", r)

    def test_sin_plugins_devuelve_vacio(self):
        with mock.patch.object(mp, "_sc") as fake_sc:
            fake_sc.return_value._plugins_instalados.return_value = {}
            self.assertEqual(mp.cargar_plugins_instalados(), {})

    def test_idempotente(self):
        with mock.patch.object(mp, "_sc") as fake_sc, \
                mock.patch.object(mp, "instalar_dependencias",
                                  return_value=True):
            fake_sc.return_value._plugins_instalados.return_value = {
                "a": {"enabled": True, "tools": [{"name": "h"}]}}
            r1 = mp.cargar_plugins_instalados()
            r2 = mp.cargar_plugins_instalados()
        self.assertEqual(r1, r2)


class TestComandosCLI(unittest.TestCase):
    def test_gateway_search(self):
        with mock.patch.object(sc, "_plugin_search", return_value=0) as s:
            self.assertEqual(sc._ejecutar_comando_plugin(["search", "slack"]),
                             0)
        s.assert_called_once_with("slack")

    def test_gateway_uninstall(self):
        with mock.patch.object(sc, "_plugin_remove", return_value=0) as r:
            self.assertEqual(sc._ejecutar_comando_plugin(["uninstall", "x"]),
                             0)
        r.assert_called_once_with("x")

    def test_gateway_install_usa_marketplace(self):
        with mock.patch("marketplace.instalar_plugin", return_value=0) as i:
            self.assertEqual(sc._ejecutar_comando_plugin(["install", "x"]), 0)
        i.assert_called_once_with("x")

    def test_search_sin_termino_error(self):
        self.assertEqual(sc._ejecutar_comando_plugin(["search"]), 1)

    def test_accion_desconocida(self):
        self.assertEqual(sc._ejecutar_comando_plugin(["volar"]), 1)


class TestVersion(unittest.TestCase):
    def test_version_6_21_0(self):
        self.assertEqual(sc.VERSION, "6.32.0")


if __name__ == "__main__":
    unittest.main()


