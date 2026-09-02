#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la integración LSP (v6.14.0). Todo mockeado: no se lanza ningún
servidor real ni se requiere uno instalado."""

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import lsp_client as lc
from lsp_client import (CacheLSP, LSPClient, _comando_servidor,
                        _detectar_lenguaje_por_extension, cliente_lsp_activo,
                        lsp_disponible)


def _silencio():
    return redirect_stdout(io.StringIO())


class TestDeteccion(unittest.TestCase):
    """Detección de lenguaje y comando del servidor LSP."""

    def test_extensiones_basicas(self):
        self.assertEqual(_detectar_lenguaje_por_extension("a/b.py"), "python")
        self.assertEqual(_detectar_lenguaje_por_extension("x.ts"),
                         "typescript")
        self.assertEqual(_detectar_lenguaje_por_extension("x.jsx"),
                         "javascript")
        self.assertEqual(_detectar_lenguaje_por_extension("main.go"), "go")
        self.assertEqual(_detectar_lenguaje_por_extension("lib.rs"), "rust")

    def test_extension_desconocida(self):
        self.assertIsNone(_detectar_lenguaje_por_extension("datos.csv"))
        self.assertIsNone(_detectar_lenguaje_por_extension("sinextension"))

    def test_comando_servidor_desconocido(self):
        self.assertIsNone(_comando_servidor("brainfuck"))

    def test_comando_servidor_instalado(self):
        # python siempre "existe" en los tests: mockeamos shutil.which.
        with mock.patch.object(lc.shutil, "which",
                               return_value="/usr/bin/pyright-langserver"):
            cmd = _comando_servidor("python")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], "pyright-langserver")

    def test_comando_servidor_no_instalado(self):
        with mock.patch.object(lc.shutil, "which", return_value=None):
            self.assertIsNone(_comando_servidor("python"))
            self.assertFalse(lsp_disponible("python"))

    def test_lsp_disponible(self):
        with mock.patch.object(lc.shutil, "which", return_value="x"):
            self.assertTrue(lsp_disponible("go"))


class TestCache(unittest.TestCase):
    """Caché en memoria y persistente (SQLite)."""

    def _cache(self, tmp):
        return CacheLSP(db_path=Path(tmp) / "lsp_cache.db")

    def test_guardar_y_obtener_memoria(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            archivo = Path(tmp) / "mod.py"
            archivo.write_text("x = 1\n", encoding="utf-8")
            cache.guardar(str(archivo), 1, 1, "definicion",
                          {"archivo": "mod.py", "linea": 3})
            r = cache.obtener(str(archivo), 1, 1, "definicion")
            self.assertEqual(r, {"archivo": "mod.py", "linea": 3})

    def test_persistente_entre_instancias(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "mod.py"
            archivo.write_text("x = 1\n", encoding="utf-8")
            CacheLSP(db_path=Path(tmp) / "c.db").guardar(
                str(archivo), 2, 3, "tipo", {"tipo": "int"})
            # Nueva instancia (memoria vacía) recupera de SQLite.
            r = CacheLSP(db_path=Path(tmp) / "c.db").obtener(
                str(archivo), 2, 3, "tipo")
            self.assertEqual(r, {"tipo": "int"})

    def test_invalidacion_por_cambio_de_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            archivo = Path(tmp) / "mod.py"
            archivo.write_text("x = 1\n", encoding="utf-8")
            cache.guardar(str(archivo), 1, 1, "definicion", {"a": 1})
            archivo.write_text("x = 2\n", encoding="utf-8")  # cambia hash
            self.assertIsNone(cache.obtener(str(archivo), 1, 1, "definicion"))

    def test_invalidar_y_limpiar(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._cache(tmp)
            archivo = Path(tmp) / "m.py"
            archivo.write_text("y\n", encoding="utf-8")
            cache.guardar(str(archivo), 1, 1, "tipo", {"t": "str"})
            cache.invalidar(str(archivo), 1, 1, "tipo")
            self.assertIsNone(cache.obtener(str(archivo), 1, 1, "tipo"))
            cache.guardar(str(archivo), 1, 1, "tipo", {"t": "str"})
            self.assertGreaterEqual(cache.limpiar(), 1)


class _ProcesoFalso:
    """Simula un servidor LSP: responde a cada petición JSON-RPC."""

    def __init__(self, respuestas=None):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.respuestas = respuestas or {}
        self.poll = mock.MagicMock(return_value=None)


class TestCliente(unittest.TestCase):
    """Ciclo de vida y consultas del cliente (proceso mockeado)."""

    def _cliente(self, tmp, timeout=2.0):
        cliente = LSPClient(cache=CacheLSP(db_path=Path(tmp) / "c.db"),
                            timeout=timeout)
        cliente.proceso = _ProcesoFalso()
        cliente.lenguaje = "python"
        cliente._abierto = True
        return cliente

    def test_iniciar_sin_servidor_devuelve_false(self):
        cliente = LSPClient()
        with _silencio(), mock.patch.object(lc, "_comando_servidor",
                                            return_value=None):
            self.assertFalse(cliente.iniciar("python", "."))

    def test_iniciar_fallo_al_arrancar(self):
        cliente = LSPClient()
        with _silencio(), mock.patch.object(
                lc, "_comando_servidor", return_value=["falso-servidor"]), \
                mock.patch.object(cliente, "_arrancar_proceso",
                                  side_effect=OSError("no such file")):
            self.assertFalse(cliente.iniciar("python", "."))

    def test_enviar_peticion_timeout_sin_respuesta(self):
        cliente = self._cliente(tempfile.mkdtemp(), timeout=0.15)
        self.assertIsNone(cliente.enviar_peticion("initialize", {}))
        cliente.cerrar()

    def test_enviar_peticion_error_rpc(self):
        cliente = self._cliente(tempfile.mkdtemp())
        cliente._cola.put({"jsonrpc": "2.0", "id": 1,
                           "error": {"code": -32600, "message": "mal"}})
        self.assertIsNone(cliente.enviar_peticion("initialize", {}))
        cliente.cerrar()

    def test_obtener_definicion_mockeada(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "mod.py"
            archivo.write_text("def hola():\n    pass\n", encoding="utf-8")
            cliente = self._cliente(tmp)
            cruda = {"uri": archivo.as_uri(),
                     "range": {"start": {"line": 0, "character": 4}}}
            with _silencio(), mock.patch.object(
                    cliente, "enviar_peticion", return_value=cruda):
                r = cliente.obtener_definicion(str(archivo), 2, 5)
            self.assertIsNotNone(r)
            self.assertEqual(r["linea"], 1)
            self.assertIn("mod.py", r["archivo"])
            cliente.cerrar()

    def test_obtener_referencias_mockeada(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "app.py"
            archivo.write_text("hola()\n", encoding="utf-8")
            cliente = self._cliente(tmp)
            ubicaciones = [{"uri": archivo.as_uri(),
                            "range": {"start": {"line": 0, "character": 0}}},
                           {"uri": archivo.as_uri(),
                            "range": {"start": {"line": 3, "character": 4}}}]
            with _silencio(), mock.patch.object(
                    cliente, "enviar_peticion", return_value=ubicaciones):
                r = cliente.obtener_referencias(str(archivo), 1, 1)
            self.assertEqual(r["total"], 2)
            self.assertEqual(r["referencias"][0]["linea"], 1)
            self.assertEqual(r["referencias"][1]["linea"], 4)
            cliente.cerrar()

    def test_obtener_tipo_mockeado(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "m.py"
            archivo.write_text("x: int = 1\n", encoding="utf-8")
            cliente = self._cliente(tmp)
            hover = {"contents": {"kind": "markdown",
                                  "value": "```python\nx: int\n```"}}
            with _silencio(), mock.patch.object(
                    cliente, "enviar_peticion", return_value=hover):
                r = cliente.obtener_tipo(str(archivo), 1, 1)
            self.assertIsNotNone(r)
            self.assertIn("int", r["tipo"])
            cliente.cerrar()

    def test_definicion_de_archivo_inexistente(self):
        cliente = LSPClient()
        with _silencio():
            self.assertIsNone(cliente.obtener_definicion(
                "no_existe_xyz.py", 1, 1))

    def test_respuesta_vacia_devuelve_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "m.py"
            archivo.write_text("a\n", encoding="utf-8")
            cliente = self._cliente(tmp)
            with _silencio(), mock.patch.object(
                    cliente, "enviar_peticion", return_value=None):
                self.assertIsNone(cliente.obtener_definicion(str(archivo),
                                                             1, 1))
            cliente.cerrar()


    def test_cache_del_cliente_evita_segunda_peticion(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "m.py"
            archivo.write_text("a\n", encoding="utf-8")
            cliente = self._cliente(tmp)
            cruda = {"uri": archivo.as_uri(),
                     "range": {"start": {"line": 0, "character": 0}}}
            with _silencio(), mock.patch.object(
                    cliente, "enviar_peticion",
                    side_effect=lambda *a, **k: cruda) as peticion:
                r1 = cliente.obtener_definicion(str(archivo), 1, 1)
                r2 = cliente.obtener_definicion(str(archivo), 1, 1)
            self.assertEqual(r1, r2)
            self.assertEqual(peticion.call_count, 1)  # 2a desde cache
            cliente.cerrar()


class TestIntegracionReAct(unittest.TestCase):
    """Herramientas LSP en el agente ReAct."""

    def test_herramientas_lsp_inactivas_error_claro(self):
        from react_agent import ReactAgent
        agente = ReactAgent(directorio=".", lsp=False)
        r = agente.herramientas["lsp_definicion"](
            {"archivo": "a.py", "linea": 1, "columna": 1})
        self.assertFalse(r["ok"])
        self.assertIn("--lsp", r["error"])

    def test_herramientas_registradas_con_lsp_activo(self):
        from react_agent import ReactAgent
        agente = ReactAgent(directorio=".", lsp=True)
        for nombre in ("lsp_definicion", "lsp_referencias", "lsp_tipo"):
            self.assertIn(nombre, agente.herramientas)
        self.assertIn("lsp_definicion", agente.ACCIONES_VALIDAS)

    def test_lsp_definicion_via_cliente_mockeado(self):
        from react_agent import ReactAgent
        agente = ReactAgent(directorio=".", lsp=True)
        fake = mock.MagicMock()
        fake.obtener_definicion.return_value = {"archivo": "x.py",
                                                "linea": 3, "columna": 1}
        with _silencio(), mock.patch.object(
                lc, "obtener_cliente_lsp", return_value=fake):
            r = agente.herramientas["lsp_definicion"](
                {"archivo": "x.py", "linea": 2, "columna": 4})
        self.assertTrue(r["ok"])
        self.assertEqual(r["linea"], 3)
        fake.obtener_definicion.assert_called_once_with("x.py", 2, 4)

    def test_lsp_no_disponible_error_claro(self):
        from react_agent import ReactAgent
        agente = ReactAgent(directorio=".", lsp=True)
        with _silencio(), mock.patch.object(
                lc, "obtener_cliente_lsp", return_value=None):
            r = agente.herramientas["lsp_referencias"](
                {"archivo": "x.py", "linea": 1, "columna": 1})
        self.assertFalse(r["ok"])
        self.assertIn("no disponible", r["error"])

    def test_argumentos_invalidos(self):
        from react_agent import ReactAgent
        agente = ReactAgent(directorio=".", lsp=True)
        with _silencio(), mock.patch.object(
                lc, "obtener_cliente_lsp", return_value=mock.MagicMock()):
            r = agente.herramientas["lsp_tipo"]({"archivo": "x.py",
                                                 "linea": "uno",
                                                 "columna": 1})
            self.assertFalse(r["ok"])
            r2 = agente.herramientas["lsp_tipo"]({"linea": 1, "columna": 1})
            self.assertFalse(r2["ok"])
