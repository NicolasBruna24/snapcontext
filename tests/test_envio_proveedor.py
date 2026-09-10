"""Tests de _enviar_al_proveedor / _enviar_al_proveedor_unico (snapcontext.py).

Todos los SDK (gemini/anthropic/openai/xpu) y el enrutador de modelos están
mockeados: no hay llamadas de red ni claves reales.
"""

from __future__ import annotations

import types
import unittest
from contextlib import ExitStack
from unittest import mock

import pytest

import snapcontext as sc


MENSAJES = [
    {"role": "system", "content": "Eres un asistente."},
    {"role": "user", "content": "hola"},
]


def _fake_genai(texto="respuesta-gemini"):
    mod = types.SimpleNamespace()
    mod.configure = mock.Mock()

    class _Model:
        def __init__(self, model_name=None):
            self.model_name = model_name

        def generate_content(self, contenido, **kwargs):
            self.contenido = contenido
            self.kwargs = kwargs
            return types.SimpleNamespace(text=texto)

    mod.GenerativeModel = _Model
    return mod


def _fake_anthropic(texto="respuesta-anthropic"):
    mod = types.SimpleNamespace()

    class _Messages:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return types.SimpleNamespace(
                content=[
                    types.SimpleNamespace(type="text", text=texto),
                    types.SimpleNamespace(type="tool_use", text="no-debe-contarse"),
                ]
            )

    class _Anthropic:
        def __init__(self, api_key=None, **kw):
            self.messages = _Messages()

    mod.Anthropic = _Anthropic
    return mod


def _fake_openai(texto="respuesta-openai"):
    mod = types.SimpleNamespace()

    class _Completions:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=texto))]
            )

    class _Chat:
        completions = _Completions()

    class _OpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = _Chat()

    mod.OpenAI = _OpenAI
    return mod


def _fake_model_router(orden=None, local=None, seleccionado=(None, None), error=False):
    mod = types.SimpleNamespace()

    def es_tarea_compleja(*a, **k):
        return bool(orden)

    mod.es_tarea_compleja = es_tarea_compleja
    mod.obtener_orden_prioridad = mock.Mock(return_value=orden or [])
    mod.es_proveedor_local = mock.Mock(return_value=False)

    def seleccionar_modelo(*a, **k):
        if error:
            raise RuntimeError("enrutador roto")
        return seleccionado

    mod.seleccionar_modelo = seleccionar_modelo
    return mod


class TestUnicoProveedorDesconocido(unittest.TestCase):
    def test_runtime_error(self):
        with pytest.raises(RuntimeError, match="desconocido"):
            sc._enviar_al_proveedor_unico("no-existe", None, MENSAJES)


class TestGemini(unittest.TestCase):
    def test_happy_path_con_system(self):
        fake = _fake_genai()
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "clave-test"}),
            mock.patch.object(sc, "genai", fake),
        ):
            resultado = sc._enviar_al_proveedor_unico("gemini", "gemini-x", MENSAJES)
        self.assertEqual(resultado, "respuesta-gemini")
        fake.configure.assert_called_once_with(api_key="clave-test")

    def test_sin_api_key_falla(self):
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""}),
            mock.patch.object(sc, "_importar_genai", return_value=_fake_genai()),
            pytest.raises(RuntimeError),
        ):
            sc._enviar_al_proveedor_unico("gemini", None, MENSAJES)

    def test_sdk_ausente_falla(self):
        with (
            mock.patch.object(sc, "genai", None),
            pytest.raises(RuntimeError, match="google"),
        ):
            sc._enviar_al_proveedor_unico("gemini", None, MENSAJES)


class TestAnthropic(unittest.TestCase):
    def test_happy_path_extrae_system(self):
        fake = _fake_anthropic()
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "clave-test"}),
            mock.patch.object(sc, "anthropic", fake),
        ):
            resultado = sc._enviar_al_proveedor_unico("anthropic", "claude-x", MENSAJES)
        self.assertEqual(resultado, "respuesta-anthropic")

    def test_sin_api_key_falla(self):
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}),
            mock.patch.object(sc, "_importar_anthropic", return_value=_fake_anthropic()),
            pytest.raises(RuntimeError),
        ):
            sc._enviar_al_proveedor_unico("anthropic", None, MENSAJES)

    def test_sdk_ausente_falla(self):
        with (
            mock.patch.object(sc, "anthropic", None),
            pytest.raises(RuntimeError, match="anthropic"),
        ):
            sc._enviar_al_proveedor_unico("anthropic", None, MENSAJES)


class TestOpenaiCompatible(unittest.TestCase):
    def test_groq_happy_path(self):
        fake = _fake_openai()
        with (
            mock.patch.dict("os.environ", {"GROQ_API_KEY": "clave-groq"}),
            mock.patch.object(sc, "openai", fake),
        ):
            resultado = sc._enviar_al_proveedor_unico("groq", "llama-9", MENSAJES)
        self.assertEqual(resultado, "respuesta-openai")

    def test_sin_clave_falla(self):
        with (
            mock.patch.dict("os.environ", {"GROQ_API_KEY": ""}),
            mock.patch.object(sc, "_importar_openai", return_value=_fake_openai()),
            pytest.raises(RuntimeError, match="GROQ"),
        ):
            sc._enviar_al_proveedor_unico("groq", None, MENSAJES)

    def test_ollama_sin_clave_usa_placeholder(self):
        fake = _fake_openai()
        with (
            mock.patch.dict("os.environ", {"OLLAMA_API_KEY": ""}),
            mock.patch.object(sc, "openai", fake),
        ):
            resultado = sc._enviar_al_proveedor_unico("ollama", None, MENSAJES)
        self.assertEqual(resultado, "respuesta-openai")

    def test_sdk_ausente_falla(self):
        with (
            mock.patch.object(sc, "openai", None),
            pytest.raises(RuntimeError),
        ):
            sc._enviar_al_proveedor_unico("groq", None, MENSAJES)


class TestXpu(unittest.TestCase):
    def _backend(self, disponible=True):
        motor = types.SimpleNamespace(generate=mock.Mock(return_value="salida-xpu"))
        return types.SimpleNamespace(
            xpu_disponible=mock.Mock(return_value=disponible),
            cargar_modelo_xpu=mock.Mock(return_value=motor),
        )

    def test_happy_path(self):
        backend = self._backend()
        with (
            mock.patch.dict("sys.modules", {"backend_xpu": backend}),
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
            mock.patch.object(sc, "_ARGS_CLI", None),
        ):
            resultado = sc._enviar_al_proveedor_unico("xpu", None, MENSAJES)
        self.assertEqual(resultado, "salida-xpu")
        backend.cargar_modelo_xpu.assert_called_once()

    def test_sin_xpu_disponible(self):
        with (
            mock.patch.dict("sys.modules", {"backend_xpu": self._backend(False)}),
            mock.patch.object(sc, "_ARGS_CLI", None),
            pytest.raises(RuntimeError, match="XPU"),
        ):
            sc._enviar_al_proveedor_unico("xpu", None, MENSAJES)

    def test_backend_ausente(self):
        with (
            mock.patch.dict("sys.modules", {"backend_xpu": None}),
            pytest.raises(RuntimeError, match="backend_xpu"),
        ):
            sc._enviar_al_proveedor_unico("xpu", None, MENSAJES)


class TestCadenaFallback(unittest.TestCase):
    """_enviar_al_proveedor: enrutamiento y cadena local<->nube (v6.30.0)."""

    def _en_routing(self, router, unico, fallback=True):
        pila = ExitStack()
        pila.enter_context(mock.patch.dict("sys.modules", {"model_router": router}))
        pila.enter_context(mock.patch.object(sc, "_MODEL_ROUTING_ACTIVO", True))
        pila.enter_context(mock.patch.object(sc, "_MODELO_EXPLICITO", False))
        pila.enter_context(mock.patch.object(sc, "_MODEL_FALLBACK_ACTIVO", fallback))
        pila.enter_context(mock.patch.object(sc, "_enviar_al_proveedor_unico", unico))
        return pila

    def test_sin_categoria_usa_el_proveedor_directo(self):
        unico = mock.Mock(return_value="ok")
        with mock.patch.object(sc, "_enviar_al_proveedor_unico", unico) as u:
            resultado = sc._enviar_al_proveedor("gemini", "m1", MENSAJES)
        self.assertEqual(resultado, "ok")
        u.assert_called_once()
        self.assertEqual(u.call_args.args[0], "gemini")

    def test_fallback_pasa_al_siguiente_modelo(self):
        router = _fake_model_router(orden=[("gemini", "g1"), ("groq", "q1")])
        unico = mock.Mock(side_effect=[RuntimeError("timeout"), "ok-groq"])
        with self._en_routing(router, unico):
            resultado = sc._enviar_al_proveedor("gemini", "g1", MENSAJES, categoria="chat")
        self.assertEqual(resultado, "ok-groq")
        self.assertEqual(unico.call_count, 2)

    def test_error_de_autenticacion_aborta(self):
        router = _fake_model_router(orden=[("gemini", "g1"), ("groq", "q1")])
        error = RuntimeError("401 Unauthorized")
        error.status_code = 401
        unico = mock.Mock(side_effect=error)
        with self._en_routing(router, unico), pytest.raises(RuntimeError, match="401"):
            sc._enviar_al_proveedor("gemini", "g1", MENSAJES, categoria="chat")
        unico.assert_called_once()  # no reintenta con el siguiente

    def test_todos_fallan_lanza_runtime(self):
        router = _fake_model_router(orden=[("gemini", "g1"), ("groq", "q1")])
        unico = mock.Mock(side_effect=[RuntimeError("t1"), RuntimeError("t2")])
        with (
            self._en_routing(router, unico),
            pytest.raises(RuntimeError, match="Todos los modelos"),
        ):
            sc._enviar_al_proveedor("gemini", "g1", MENSAJES, categoria="chat")

    def test_enrutado_por_categoria_sin_cadena(self):
        router = _fake_model_router(seleccionado=("groq", "q9"))
        unico = mock.Mock(return_value="ok")
        with self._en_routing(router, unico, fallback=False):
            sc._enviar_al_proveedor("gemini", None, MENSAJES, categoria="chat")
        self.assertEqual(unico.call_args.args[0], "groq")
        self.assertEqual(unico.call_args.args[1], "q9")

    def test_enrutador_roto_cae_en_proveedor_original(self):
        router = _fake_model_router(error=True)
        unico = mock.Mock(return_value="ok")
        with self._en_routing(router, unico, fallback=False):
            sc._enviar_al_proveedor("gemini", "g1", MENSAJES, categoria="chat")
        self.assertEqual(unico.call_args.args[0], "gemini")


if __name__ == "__main__":
    unittest.main()
