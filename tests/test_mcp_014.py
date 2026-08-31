#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de las herramientas MCP — v0.14.0."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc


class BaseMCP(unittest.TestCase):
    """Aísla CONFIG_DIR y rutas de configuración en un directorio temporal."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = Path(self.tmp.name)
        parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp),
            mock.patch.object(sc, "PERMISOS_PATH",
                              self.dir_tmp / "permisos.json"),
            mock.patch.object(sc, "MCP_TOOLS_PATH",
                              self.dir_tmp / "mcp_tools.json"),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        restaurar = mock.patch.object(sc, "CONFIRMAR_ACCIONES", True)
        restaurar.start()
        self.addCleanup(restaurar.stop)

    def _proyecto(self) -> Path:
        proyecto = self.dir_tmp / "proyecto"
        proyecto.mkdir(exist_ok=True)
        (proyecto / "saludo.py").write_text(
            "import os\n\n\nclass Saludo:\n"
            "    def hola(self, nombre):\n        return nombre\n",
            encoding="utf-8")
        return proyecto


class TestCargaHerramientas(BaseMCP):
    def test_predefinidas_presentes(self):
        herramientas = sc._cargar_herramientas_mcp()
        for esperada in ("grep", "read_file", "list_files", "ast",
                         "git_status", "git_diff", "execute_command"):
            self.assertIn(esperada, herramientas)
        self.assertTrue(herramientas["execute_command"]["requiere_permiso"])
        self.assertFalse(herramientas["grep"]["requiere_permiso"])

    def test_herramientas_de_usuario(self):
        (self.dir_tmp / "mcp_tools.json").write_text(json.dumps({"tools": [
            {"nombre": "build", "descripcion": "Compilar",
             "comando": "npm run build", "requiere_permiso": True},
            {"nombre": "", "comando": "invalida"},          # se ignora
            {"nombre": "sin_comando"},                       # se ignora
        ]}), encoding="utf-8")
        herramientas = sc._cargar_herramientas_mcp()
        self.assertIn("build", herramientas)
        self.assertEqual(herramientas["build"]["comando"], "npm run build")
        self.assertNotIn("sin_comando", herramientas)

    def test_archivo_corrupto_tolerado(self):
        (self.dir_tmp / "mcp_tools.json").write_text("{roto", encoding="utf-8")
        herramientas = sc._cargar_herramientas_mcp()
        self.assertIn("grep", herramientas)


class TestHerramientasLectura(BaseMCP):
    def test_grep_encuentra(self):
        proyecto = self._proyecto()
        resultado = sc._tool_grep("Saludo", str(proyecto))
        self.assertTrue(resultado["ok"])
        self.assertGreaterEqual(resultado["total"], 1)
        self.assertTrue(any("saludo.py" in c for c in resultado["coincidencias"]))

    def test_read_file_rango(self):
        proyecto = self._proyecto()
        res = sc._tool_read_file(str(proyecto / "saludo.py"), 4, 5)
        self.assertTrue(res["ok"])
        self.assertIn("def hola", res["contenido"])
        self.assertEqual(res["linea_inicio"], 4)

    def test_read_file_inexistente(self):
        self.assertFalse(sc._tool_read_file("no_existe.py")["ok"])

    def test_list_files_filtro_extension(self):
        proyecto = self._proyecto()
        (proyecto / "nota.txt").write_text("x", encoding="utf-8")
        res = sc._tool_list_files(str(proyecto), extensiones=[".py"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["archivos"], ["saludo.py"])
        res2 = sc._tool_list_files(str(proyecto))
        self.assertEqual(res2["total"], 2)

    def test_ast_extrae_simbolos(self):
        proyecto = self._proyecto()
        res = sc._tool_ast(str(proyecto / "saludo.py"))
        self.assertTrue(res["ok"])
        self.assertIn("os", res["imports"])
        self.assertEqual(res["clases"][0]["nombre"], "Saludo")
        self.assertIn("hola", [f["nombre"] for f in res["funciones"]]
                      + res["clases"][0]["metodos"])

    def test_ast_sintaxis_invalida(self):
        malo = self.dir_tmp / "malo.py"
        malo.write_text("def (:", encoding="utf-8")
        res = sc._tool_ast(str(malo))
        self.assertFalse(res["ok"])
        self.assertIn("sintaxis", res["error"])

    def test_git_status_y_diff_sin_repo(self):
        # El home del usuario puede ser un repo; simulamos no-repo.
        with mock.patch.object(sc, "_es_repo_git", return_value=False):
            self.assertFalse(sc._tool_git_status(str(self.dir_tmp))["ok"])
            self.assertFalse(sc._tool_git_diff(str(self.dir_tmp))["ok"])


class TestExecuteCommand(BaseMCP):
    def test_ejecucion_correcta_y_fallo(self):
        eco = "cmd /c echo hola" if sys.platform.startswith("win") else "echo hola"
        res = sc._tool_execute_command(eco, ".")
        self.assertTrue(res["ok"])
        self.assertEqual(res["codigo_retorno"], 0)
        fallo = ("cmd /c exit 7" if sys.platform.startswith("win")
                 else "exit 7")
        res2 = sc._tool_execute_command(fallo, ".")
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["codigo_retorno"], 7)


class TestDispatcherMCP(BaseMCP):
    def setUp(self):
        super().setUp()
        self.proyecto = self._proyecto()

    def test_herramienta_desconocida(self):
        llamada = sc._ejecutar_herramienta_mcp("volar", {})
        self.assertFalse(llamada["ok"])
        self.assertIn("desconocida", llamada["error"])

    def test_lectura_no_pide_permiso(self):
        with mock.patch.object(sc, "_confirmar_accion") as conf:
            llamada = sc._ejecutar_herramienta_mcp(
                "read_file", {"ruta": str(self.proyecto / "saludo.py")})
        conf.assert_not_called()
        self.assertTrue(llamada["ok"])

    def test_execute_command_requiere_permiso_denegado(self):
        with mock.patch.object(sc, "_confirmar_accion", return_value=False), \
             mock.patch.object(sc, "_ejecutar_comando") as ej:
            llamada = sc._ejecutar_herramienta_mcp(
                "execute_command", {"comando": "cmd /c echo x"})
        ej.assert_not_called()
        self.assertFalse(llamada["ok"])
        self.assertIn("denegado", llamada["error"])

    def test_execute_command_con_permiso(self):
        with mock.patch.object(sc, "_confirmar_accion", return_value=True):
            llamada = sc._ejecutar_herramienta_mcp(
                "execute_command",
                {"comando": "cmd /c echo ok" if sys.platform.startswith("win")
                 else "echo ok"})
        self.assertTrue(llamada["ok"])

    def test_herramienta_de_usuario_se_ejecuta(self):
        (self.dir_tmp / "mcp_tools.json").write_text(json.dumps({"tools": [
            {"nombre": "saluda", "comando":
             "cmd /c echo hola-mcp" if sys.platform.startswith("win")
             else "echo hola-mcp"}]}), encoding="utf-8")
        with mock.patch.object(sc, "_confirmar_accion", return_value=True):
            llamada = sc._ejecutar_herramienta_mcp("saluda", {})
        self.assertTrue(llamada["ok"])
        self.assertIn("hola-mcp", llamada["resultado"]["stdout"])

    def test_excepcion_blinde_resultado(self):
        with mock.patch.object(sc, "_tool_grep",
                               side_effect=RuntimeError("boom")):
            llamada = sc._ejecutar_herramienta_mcp("grep", {"patron": "x"})
        self.assertFalse(llamada["ok"])
        self.assertIn("boom", llamada["resultado"]["error"])


class TestFormatoYAutoContexto(BaseMCP):
    def test_formatear_ok_y_error(self):
        error = sc._formatear_resultado_mcp(
            {"ok": False, "herramienta": "grep", "error": "nada"})
        self.assertIn("✖ grep", error)
        bien = sc._formatear_resultado_mcp(
            {"ok": True, "herramienta": "git_status",
             "resultado": {"rama": "main", "total_cambios": 0,
                           "cambios": []}})
        self.assertIn("rama: main", bien)

    def test_auto_contexto_dispara_grep(self):
        with mock.patch.object(sc, "_ejecutar_herramienta_mcp",
                               return_value={"ok": True, "herramienta": "grep",
                                             "resultado": {"total": 0}}) as ej:
            contexto = sc._contexto_automatico_mcp(
                "busca donde se usa checkout")
        self.assertTrue(contexto)
        ej.assert_called_once()
        self.assertEqual(ej.call_args[0][0], "grep")

    def test_auto_contexto_vacio_en_mensaje_normal(self):
        with mock.patch.object(sc, "_ejecutar_herramienta_mcp") as ej:
            contexto = sc._contexto_automatico_mcp("hola que tal")
        self.assertEqual(contexto, "")
        ej.assert_not_called()


class TestVersionMCPCli(BaseMCP):
    def test_version_es_1_2_0(self):
        self.assertEqual(sc.VERSION, "6.8.0")


if __name__ == "__main__":
    unittest.main()

