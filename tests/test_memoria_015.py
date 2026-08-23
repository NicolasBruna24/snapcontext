#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la memoria de proyecto (CLAUDE.md) — v0.15.0."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc


class BaseMemoria(unittest.TestCase):
    """Aísla CONFIG_DIR y el directorio del proyecto en temporales."""

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
            mock.patch.object(sc, "CONFIRMAR_ACCIONES", True),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def _proyecto(self, con_memoria: bool = True,
                  nombre: str = "CLAUDE.md") -> Path:
        proyecto = self.dir_tmp / "proy"
        proyecto.mkdir(exist_ok=True)
        if con_memoria:
            (proyecto / nombre).write_text(
                "# Proyecto demo\n\n## Convenciones\n- Commits en español\n",
                encoding="utf-8")
        return proyecto


class TestBuscarYCargar(BaseMemoria):
    def test_sin_memoria_devuelve_none_y_vacio(self):
        proyecto = self._proyecto(con_memoria=False)
        self.assertIsNone(sc._buscar_claude_md(str(proyecto)))
        self.assertEqual(sc._cargar_claude_md(str(proyecto)), "")

    def test_busca_claude_md(self):
        proyecto = self._proyecto(nombre="CLAUDE.md")
        camino = sc._buscar_claude_md(str(proyecto))
        self.assertIsNotNone(camino)
        self.assertEqual(camino.name, "CLAUDE.md")

    def test_alternativa_snapcontext_md(self):
        proyecto = self._proyecto(nombre="SNAPCONTEXT.md")
        camino = sc._buscar_claude_md(str(proyecto))
        self.assertIsNotNone(camino)
        self.assertEqual(camino.name, "SNAPCONTEXT.md")

    def test_cargar_contenido(self):
        proyecto = self._proyecto()
        contenido = sc._cargar_claude_md(str(proyecto))
        self.assertIn("Commits en español", contenido)

    def test_recorte_por_maximo_caracteres(self):
        proyecto = self._proyecto()
        (proyecto / "CLAUDE.md").write_text("x" * 5000, encoding="utf-8")
        corto = sc._cargar_claude_md(str(proyecto), max_caracteres=100)
        self.assertLessEqual(len(corto), 120)
        self.assertIn("recortado", corto)


class TestPlantillaBasica(BaseMemoria):
    def test_plantilla_sin_ia_contiene_secciones(self):
        proyecto = self._proyecto(con_memoria=False)
        (proyecto / "app.py").write_text("print('hola')", encoding="utf-8")
        plantilla = sc._plantilla_claude_md_basica(str(proyecto))
        for seccion in ("## Objetivo", "## Tecnologías", "## Estructura",
                        "## Convenciones", "## Comandos útiles"):
            self.assertIn(seccion, plantilla)


class TestGenerarClaudeMd(BaseMemoria):
    def test_fallback_sin_proveedor_crea_archivo(self):
        """Sin clave de API, --init-claude cae a la plantilla offline."""
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=RuntimeError("sin conexión")):
            destino = sc._generar_claude_md(directorio=str(self.dir_tmp))
        self.assertTrue(destino.exists())
        contenido = destino.read_text(encoding="utf-8")
        self.assertIn("## Objetivo", contenido)

    def test_generacion_con_proveedor_mock(self):
        with mock.patch.object(
                sc, "_enviar_al_proveedor",
                return_value="# Proyecto\n## Objetivo\nAutomatizar.") as enviar:
            destino = sc._generar_claude_md(directorio=str(self.dir_tmp))
        self.assertIn("Automatizar.", destino.read_text(encoding="utf-8"))
        enviar.assert_called_once()

    def test_sobreescribe_solo_con_confirmacion(self):
        destino = self.dir_tmp / "CLAUDE.md"
        destino.write_text("original", encoding="utf-8")
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="nuevo contenido"), \
             mock.patch.object(sc, "_confirmar_accion", return_value=False):
            sc._generar_claude_md(directorio=str(self.dir_tmp))
        self.assertEqual(destino.read_text(encoding="utf-8"), "original")
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="nuevo contenido"), \
             mock.patch.object(sc, "_confirmar_accion", return_value=True):
            sc._generar_claude_md(directorio=str(self.dir_tmp))
        self.assertIn("nuevo contenido", destino.read_text(encoding="utf-8"))


class TestActualizarAutomatico(BaseMemoria):
    def test_sin_memoria_no_hace_nada(self):
        self.assertFalse(sc._actualizar_claude_md_automatico(
            "resumen", str(self.dir_tmp)))

    def test_denegado_no_modifica(self):
        proyecto = self._proyecto()
        original = (proyecto / "CLAUDE.md").read_text(encoding="utf-8")
        with mock.patch.object(sc, "_confirmar_accion", return_value=False), \
             mock.patch.object(sc, "_enviar_al_proveedor") as enviar:
            self.assertFalse(sc._actualizar_claude_md_automatico(
                "aprendimos X", str(proyecto)))
        enviar.assert_not_called()
        self.assertEqual((proyecto / "CLAUDE.md").read_text("utf-8"), original)

    def test_actualizacion_con_proveedor_mock(self):
        proyecto = self._proyecto()
        with mock.patch.object(sc, "_confirmar_accion", return_value=True), \
             mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="# memoria v2"):
            self.assertTrue(sc._actualizar_claude_md_automatico(
                "aprendimos Y", str(proyecto)))
        self.assertIn("memoria v2", (proyecto / "CLAUDE.md").read_text("utf-8"))


class TestPlanIncluyeMemoria(BaseMemoria):
    def test_plan_incluye_memoria_en_prompt(self):
        """_generar_plan añade la memoria al prompt cuando existe."""
        proyecto = self._proyecto()
        sc.MEMORIA_PROYECTO = sc._cargar_claude_md(str(proyecto))
        captura = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captura["prompt"] = kwargs["messages"][0]["content"]
                return mock.MagicMock(choices=[mock.MagicMock(
                    message=mock.MagicMock(content='{"pasos": []}'))])

        class FakeChat:
            completions = FakeCompletions()

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.chat = FakeChat()

        original_openai = sc.openai
        sc.openai = mock.MagicMock(OpenAI=FakeOpenAI)
        try:
            sc._generar_plan("tarea", "ollama")
        finally:
            sc.openai = original_openai
        self.assertIn("MEMORIA DEL PROYECTO", captura["prompt"])
        self.assertIn("Commits en español", captura["prompt"])


class TestFlagsMemoriaCli(BaseMemoria):
    def test_flag_init_claude(self):
        self.assertTrue(sc.crear_parser().parse_args(
            ["--init-claude"]).init_claude)

    def test_version_es_1_2_0(self):
        self.assertEqual(sc.VERSION, "1.6.0")


if __name__ == "__main__":
    unittest.main()
