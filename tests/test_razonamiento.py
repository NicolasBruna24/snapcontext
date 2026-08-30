#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del sistema de razonamiento del modelo (chain-of-thought) — v6.2.0.

Cubre: extracción de razonamiento (dicts anidados y bloques <think>),
limpieza del texto útil, flag ``--mostrar-razonamiento``, variable de
entorno ``SNAPCONTEXT_MOSTRAR_RAZONAMIENTO``, panel de ``ui``,
integración en chat/planificador/editor/ReAct y el modo de dos pasos.
"""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import rmtree
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snapcontext as sc       # noqa: E402
import agentes as ag           # noqa: E402
import ui                      # noqa: E402

ENV_CLAVE = "SNAPCONTEXT_MOSTRAR_RAZONAMIENTO"


class TestExtraerRazonamiento(unittest.TestCase):
    """``sc._extraer_razonamiento`` y ``sc._quitar_razonamiento``."""

    def test_dict_clave_reasoning(self):
        raz = sc._extraer_razonamiento({"reasoning": "Paso 1: analizar."})
        self.assertEqual(raz, "Paso 1: analizar.")

    def test_dict_claves_alternativas(self):
        for clave in ("thinking", "chain_of_thought", "reasoning_content",
                      "thoughts", "razonamiento"):
            self.assertEqual(
                sc._extraer_razonamiento({clave: "raz"}), "raz", clave)

    def test_dict_anidado_ollama(self):
        resp = {"message": {"content": "hola", "thinking": "pienso…"}}
        self.assertEqual(sc._extraer_razonamiento(resp), "pienso…")

    def test_dict_anidado_openai(self):
        resp = {"choices": [{"message": {"reasoning": "deep think"}}]}
        self.assertEqual(sc._extraer_razonamiento(resp), "deep think")

    def test_str_bloques_think(self):
        texto = "<think>primero</think>Respuesta <think>segundo</think>"
        self.assertEqual(sc._extraer_razonamiento(texto),
                         "primero\nsegundo")

    def test_sin_razonamiento_devuelve_none(self):
        self.assertIsNone(sc._extraer_razonamiento({"content": "hola"}))
        self.assertIsNone(sc._extraer_razonamiento("solo respuesta"))
        self.assertIsNone(sc._extraer_razonamiento(None))

    def test_quitar_razonamiento_limpia_texto(self):
        limpio = sc._quitar_razonamiento(
            "<think>oculto</think>Respuesta final")
        self.assertEqual(limpio, "Respuesta final")
        # Etiqueta suelta (sin cierre): se elimina la etiqueta, pero el
        # contenido posterior se conserva (no se puede saber dónde acababa).
        self.assertEqual(
            sc._quitar_razonamiento("<think>roto Respuesta"),
            "roto Respuesta")

    def test_quitar_razonamiento_respeta_texto_sin_razonamiento(self):
        # Compatibilidad: sin <think> el texto se devuelve tal cual, sin
        # recortar espacios/saltos finales del proveedor.
        original = "codigo\ncon salto final\n"
        self.assertIs(sc._quitar_razonamiento(original), original)


class TestActivacion(unittest.TestCase):
    """Activación: flag ``--mostrar-razonamiento`` y variable de entorno."""

    def setUp(self):
        os.environ.pop(ENV_CLAVE, None)

    def tearDown(self):
        os.environ.pop(ENV_CLAVE, None)

    def test_desactivado_por_defecto(self):
        self.assertFalse(sc._razonamiento_activo())
        self.assertFalse(sc._razonamiento_activo(argparse.Namespace()))

    def test_flag_cli_activa(self):
        args = argparse.Namespace(mostrar_razonamiento=True)
        self.assertTrue(sc._razonamiento_activo(args))

    def test_flag_cli_false_no_activa(self):
        args = argparse.Namespace(mostrar_razonamiento=False)
        self.assertFalse(sc._razonamiento_activo(args))

    def test_env_activa_valores_validos(self):
        for valor in ("1", "true", "yes", "SI", "on"):
            os.environ[ENV_CLAVE] = valor
            self.assertTrue(sc._razonamiento_activo(), valor)

    def test_env_valor_invalido_no_activa(self):
        for valor in ("0", "no", "falso", "", "  "):
            os.environ[ENV_CLAVE] = valor
            self.assertFalse(sc._razonamiento_activo(), repr(valor))

    def test_env_prioriza_sobre_flag_ausente(self):
        os.environ[ENV_CLAVE] = "1"
        self.assertTrue(sc._razonamiento_activo(argparse.Namespace()))

    def test_flag_funciona_con_env_desactivado(self):
        os.environ[ENV_CLAVE] = "0"
        args = argparse.Namespace(mostrar_razonamiento=True)
        self.assertTrue(sc._razonamiento_activo(args))


class TestProcesarYPasos(unittest.TestCase):
    """``_procesar_razonamiento`` y ``_razonamiento_dos_pasos``."""

    def test_procesar_inactivo_no_muestra_nada(self):
        with mock.patch.object(ui, "mostrar_razonamiento") as panel:
            limpio, raz = sc._procesar_razonamiento(
                "<think>x</think>hola", activo=False)
        panel.assert_not_called()
        self.assertEqual(limpio, "hola")
        self.assertEqual(raz, "x")

    def test_procesar_activo_muestra_panel(self):
        with mock.patch.object(ui, "mostrar_razonamiento") as panel:
            limpio, raz = sc._procesar_razonamiento(
                "<think>pienso</think>hola", activo=True)
        panel.assert_called_once()
        self.assertIn("pienso", panel.call_args.args[0])
        self.assertEqual(limpio, "hola")

    def test_procesar_activo_sin_razonamiento_avisa(self):
        with mock.patch.object(sc, "info") as inf:
            limpio, raz = sc._procesar_razonamiento("hola", activo=True)
        inf.assert_called_once()
        self.assertIn("no proporcionó razonamiento", str(inf.call_args))
        self.assertIsNone(raz)

    def test_procesar_avisar_false_no_avisa(self):
        with mock.patch.object(sc, "info") as inf:
            sc._procesar_razonamiento("hola", activo=True, avisar=False)
        inf.assert_not_called()

    def test_dos_pasos_devuelve_razonamiento(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="paso 1… paso 2…") as envio:
            raz = sc._razonamiento_dos_pasos("tarea X", "mockprov")
        self.assertEqual(raz, "paso 1… paso 2…")
        prompt = envio.call_args.args[2][0]["content"]
        self.assertIn("sin ejecutar ninguna acción", prompt)
        self.assertIn("tarea X", prompt)

    def test_dos_pasos_extrae_think_de_respuesta(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="<think>raz</think>final"):
            raz = sc._razonamiento_dos_pasos("t", "mockprov")
        self.assertEqual(raz, "raz")

    def test_dos_pasos_fallo_proveedor_devuelve_none(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=RuntimeError("boom")):
            self.assertIsNone(sc._razonamiento_dos_pasos("t", "mockprov"))


class TestPanelUI(unittest.TestCase):
    """Formato de salida de ``ui.mostrar_razonamiento``."""

    def test_texto_vacio_devuelve_false(self):
        self.assertFalse(ui.mostrar_razonamiento(""))
        self.assertFalse(ui.mostrar_razonamiento("   "))
        self.assertFalse(ui.mostrar_razonamiento(None))

    def test_texto_valido_devuelve_true(self):
        with mock.patch.object(ui, "RICH_DISPONIBLE", False), \
                mock.patch.object(ui, "_imprimir") as imp:
            self.assertTrue(ui.mostrar_razonamiento("pensando…"))
        imp.assert_called()

    def test_truncado_muestra_ver_mas(self):
        capturado = []

        def _capturar(*args, **kwargs):
            capturado.append(args[0] if args else "")

        with mock.patch.object(ui, "RICH_DISPONIBLE", False), \
                mock.patch.object(ui, "_imprimir", side_effect=_capturar):
            ui.mostrar_razonamiento("x" * 1200, max_caracteres=500)
        salida = "\n".join(str(c) for c in capturado)
        self.assertIn("[ver más]", salida)
        self.assertIn("…", salida)
        # Nunca se imprime el texto completo (solo los primeros 500 chars).
        self.assertNotIn("x" * 600, salida)

    def test_panel_rich_titulo_y_borde(self):
        with mock.patch.object(ui, "RICH_DISPONIBLE", True), \
                mock.patch.object(ui, "_console") as cons:
            ui.mostrar_razonamiento("raz profundo", titulo="🧠 Prueba")
        panel = cons.print.call_args.args[0]
        self.assertIn("raz profundo", panel.renderable.plain)
        self.assertIn("🧠 Prueba", str(panel.title))


class TestIntegracionEditor(unittest.TestCase):
    """El editor muestra el razonamiento antes de aplicar el cambio."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scraz_")
        self.archivo = Path(self.tmp) / "mini.py"
        self.contenido = "def saluda():\n    return 'hola'\n"
        self.archivo.write_text(self.contenido, encoding="utf-8")

    def tearDown(self):
        rmtree(self.tmp, ignore_errors=True)

    def _editor(self, activo):
        editor = ag.AgenteEditorPropio()
        editor._mostrar_razonamiento = activo
        return editor

    def test_activo_muestra_panel_y_limpia_respuesta(self):
        respuesta = ("<think>Analizo la función saluda.</think>"
                     "def saluda():\n    return 'adiós'\n")
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=respuesta), \
                mock.patch.object(ui, "mostrar_razonamiento") as panel, \
                mock.patch.object(sc, "info"), \
                mock.patch.object(sc, "exito"):
            ok = self._editor(True)._aplicar_modo_sobrescribir(
                "mini.py", "cambia el saludo", self.contenido,
                None, self.tmp, validar=False, max_intentos_validacion=1,
                conciso=False)
        self.assertTrue(ok)
        panel.assert_called_once()
        self.assertIn("Analizo la función", panel.call_args.args[0])
        resultado = self.archivo.read_text(encoding="utf-8")
        self.assertIn("adiós", resultado)
        self.assertNotIn("<think>", resultado)

    def test_inactivo_no_muestra_pero_limpia(self):
        respuesta = ("<think>raz oculto</think>"
                     "def saluda():\n    return 'chau'\n")
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=respuesta), \
                mock.patch.object(ui, "mostrar_razonamiento") as panel, \
                mock.patch.object(sc, "info"), \
                mock.patch.object(sc, "exito"):
            ok = self._editor(False)._aplicar_modo_sobrescribir(
                "mini.py", "cambia el saludo", self.contenido,
                None, self.tmp, validar=False, max_intentos_validacion=1,
                conciso=False)
        self.assertTrue(ok)
        panel.assert_not_called()
        resultado = self.archivo.read_text(encoding="utf-8")
        self.assertIn("chau", resultado)
        self.assertNotIn("<think>", resultado)

    def test_flag_en_parser_cli(self):
        args = sc.crear_parser().parse_args(["--mostrar-razonamiento"])
        self.assertTrue(args.mostrar_razonamiento)
        self.assertFalse(sc.crear_parser().parse_args([]).mostrar_razonamiento)


class TestIntegracionChat(unittest.TestCase):
    """El bucle de chat muestra el razonamiento antes de la respuesta."""

    def tearDown(self):
        os.environ.pop(ENV_CLAVE, None)

    def test_chat_activo_muestra_razonamiento(self):
        # El chat llama a _razonamiento_activo() sin args → se activa por
        # variable de entorno (equivalente al flag --mostrar-razonamiento).
        os.environ[ENV_CLAVE] = "1"
        respuestas = iter(["<think>Pienso la respuesta.</think>"
                           "Respuesta final del modelo"])
        entradas = iter(["hola", "/salir"])
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=lambda *a, **k: next(respuestas)), \
                mock.patch("builtins.input",
                           side_effect=lambda *a, **k: next(entradas)), \
                mock.patch.object(ui, "mostrar_razonamiento") as panel, \
                mock.patch.object(sc, "info"), \
                mock.patch.object(sc, "aviso"), \
                mock.patch.object(sc, "error"), \
                mock.patch.object(sc, "_emitir"):
            sc._ejecutar_chat(proveedor="ollama", modelo="test")
        panel.assert_called_once()
        self.assertIn("Pienso la respuesta", panel.call_args.args[0])

    def test_chat_inactivo_no_muestra_panel(self):
        os.environ.pop(ENV_CLAVE, None)
        respuestas = iter(["<think>raz oculto</think>respuesta"])
        entradas = iter(["hola", "/salir"])
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=lambda *a, **k: next(respuestas)), \
                mock.patch("builtins.input",
                           side_effect=lambda *a, **k: next(entradas)), \
                mock.patch.object(ui, "mostrar_razonamiento") as panel, \
                mock.patch.object(sc, "info"), \
                mock.patch.object(sc, "aviso"), \
                mock.patch.object(sc, "error"), \
                mock.patch.object(sc, "_emitir"):
            sc._ejecutar_chat(proveedor="ollama", modelo="test")
        panel.assert_not_called()


class TestIntegracionPlan(unittest.TestCase):
    """``_generar_plan`` limpia <think> antes de parsear el JSON del plan."""

    def test_plan_limpia_razonamiento_y_parsea_json(self):
        fake_resp = mock.MagicMock()
        fake_resp.choices = [mock.MagicMock()]
        fake_resp.choices[0].message.content = \
            "<think>razonando el plan</think>[{\"accion\": \"consultar\"}]"
        cliente = mock.MagicMock()
        cliente.chat.completions.create.return_value = fake_resp
        fake_openai = mock.MagicMock()
        fake_openai.OpenAI.return_value = cliente
        cfg = {"tipo": "openai", "nombre": "OpenAI de prueba",
               "modelo_default": "test-model", "clave_env": "SC_TEST_KEY",
               "requiere_clave": True,
               "base_url": "https://example.invalid/v1"}
        capturado = {}

        def _parsear(texto):
            capturado["texto"] = texto
            return []

        os.environ["SC_TEST_KEY"] = "prueba"
        try:
            with mock.patch.object(sc, "openai", fake_openai), \
                    mock.patch.dict(sc.PROVEEDORES, {"openai": cfg}), \
                    mock.patch.object(sc, "parsear_json", _parsear), \
                    mock.patch.object(sc, "_normalizar_pasos",
                                      side_effect=lambda p: p), \
                    mock.patch.object(sc, "info"), \
                    mock.patch.object(sc, "depurar"):
                sc._generar_plan("tarea", proveedor="openai",
                                 modelo="test-model")
        finally:
            os.environ.pop("SC_TEST_KEY", None)
        self.assertNotIn("<think>", capturado["texto"])
        self.assertIn('{"accion": "consultar"}', capturado["texto"])


class TestIntegracionReact(unittest.TestCase):
    """ReAct muestra el razonamiento COMPLETO (no solo 'pensamiento')."""

    def _agente(self, activo):
        import react_agent as react
        return react, react.ReactAgent(
            directorio=".", auto=True, max_iter=2, proveedor="ollama",
            mostrar_razonamiento=activo)

    def test_react_activo_muestra_razonamiento_completo(self):
        react, agente = self._agente(True)
        bruto = ("<think>Razonamiento paso a paso completo</think>"
                 '{"pensamiento": "breve", "accion": "finalizar", '
                 '"argumentos": {"resumen": "hecho"}}')
        with mock.patch.object(agente, "_llamar_llm", return_value=bruto), \
                mock.patch("ui.mostrar_razonamiento") as panel, \
                mock.patch.object(sc, "info"), \
                mock.patch.object(sc, "exito"):
            res = agente.ejecutar("tarea")
        self.assertTrue(res["ok"])
        panel.assert_called_once()
        self.assertIn("Razonamiento paso a paso completo",
                      panel.call_args.args[0])

    def test_react_inactivo_no_muestra_panel(self):
        react, agente = self._agente(False)
        bruto = ('{"pensamiento": "breve", "accion": "finalizar", '
                 '"argumentos": {"resumen": "hecho"}}')
        with mock.patch.object(agente, "_llamar_llm", return_value=bruto), \
                mock.patch("ui.mostrar_razonamiento") as panel, \
                mock.patch.object(sc, "info"), \
                mock.patch.object(sc, "exito"):
            res = agente.ejecutar("tarea")
        self.assertTrue(res["ok"])
        panel.assert_not_called()


if __name__ == "__main__":
    unittest.main()

