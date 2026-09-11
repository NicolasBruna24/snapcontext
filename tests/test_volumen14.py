"""Tests Fase 1d: configuracion.py _probar_conexion y interaccion."""

from unittest import mock

import configuracion as cfg


class TestSc:
    def test_devuelve_atributo_de_snapcontext(self):
        import snapcontext as sc

        assert cfg._sc("VERSION") == sc.VERSION


class TestProbarConexion:
    def test_gemini_sin_sdk(self):
        def fake():
            return None  # import devuelve None

        with (
            mock.patch.object(cfg, "_sc", return_value=fake) as msc,
            mock.patch.object(cfg, "cargar_configuracion", return_value={}),
        ):
            ok = cfg._probar_conexion_proveedor("gemini")
        assert ok is False

    def test_gemini_falta_clave(self):
        genai = mock.Mock()
        with (
            mock.patch.object(
                cfg, "_sc", side_effect=lambda n: genai if n == "genai" else mock.Mock()
            ) as msc,
            mock.patch.object(cfg, "cargar_configuracion", return_value={"api_keys": {}}),
            mock.patch.dict("os.environ", {}, clear=True),
        ):
            ok = cfg._probar_conexion_proveedor("gemini")
        assert ok is False

    def test_gemini_ok(self):
        import snapcontext as sc

        genai = mock.Mock()
        genai.configure.return_value = genai
        genai.GenerativeModel.return_value.generate_content.return_value = None
        with (
            mock.patch.object(
                cfg, "_sc", side_effect=lambda n: genai if n == "genai" else mock.Mock()
            ) as msc,
            mock.patch.object(
                cfg, "cargar_configuracion", return_value={"api_keys": {"gemini": "clave123"}}
            ),
        ):
            ok = cfg._probar_conexion_proveedor("gemini")
        assert ok is True

    def test_anthropic_sin_sdk(self):
        def fake():
            return None

        with (
            mock.patch.object(cfg, "_sc", return_value=fake),
            mock.patch.object(cfg, "cargar_configuracion", return_value={}),
        ):
            assert cfg._probar_conexion_proveedor("anthropic") is False

    def test_anthropic_ok(self):
        anthropic = mock.Mock()
        anthropic.Anthropic.return_value.messages.create.return_value = None

        def _sc(name):
            if name == "_importar_anthropic":
                return lambda: anthropic
            return anthropic  # "anthropic" → SDK

        with (
            mock.patch.object(cfg, "_sc", side_effect=_sc),
            mock.patch.object(
                cfg, "cargar_configuracion", return_value={"api_keys": {"anthropic": "ak"}}
            ),
        ):
            assert cfg._probar_conexion_proveedor("anthropic") is True

    def test_openai_sin_sdk(self):
        def fake():
            return None

        with (
            mock.patch.object(cfg, "_sc", return_value=fake),
            mock.patch.object(cfg, "cargar_configuracion", return_value={}),
        ):
            assert cfg._probar_conexion_proveedor("groq") is False

    def test_openai_ok(self):
        openai = mock.Mock()
        openai.OpenAI.return_value.chat.completions.create.return_value = None

        def _sc(name):
            if name == "_importar_openai":
                return lambda: openai
            if name == "_resolver_url_openai":
                return lambda cfg: "https://api.groq.com/"
            return openai  # "openai" → SDK

        with (
            mock.patch.object(cfg, "_sc", side_effect=_sc),
            mock.patch.object(
                cfg, "cargar_configuracion", return_value={"api_keys": {"groq": "gk"}}
            ),
        ):
            assert cfg._probar_conexion_proveedor("groq") is True


class TestSeleccionarProveedorInteractive:
    @mock.patch.object(cfg, "_importar_questionary", return_value=None)
    def test_sin_questionary_usa_defecto(self, mq):
        prov, model = cfg.seleccionar_proveedor_interactivo()
        assert model is None

    @mock.patch.object(cfg, "_importar_questionary", return_value=None)
    def test_sin_questionary_proveedor_defecto(self, mq):
        prov, model = cfg.seleccionar_proveedor_interactivo()
        assert prov == cfg.PROVEEDOR_DEFECTO

    def test_confirmacion_no(self):
        q = mock.Mock()
        q.confirm.return_value.ask.return_value = False
        with mock.patch.object(cfg, "_importar_questionary", return_value=q):
            prov, model = cfg.seleccionar_proveedor_interactivo()
        assert prov == cfg.PROVEEDOR_DEFECTO and model is None

    def test_elige_gemini(self):
        q = mock.Mock()
        q.confirm.return_value.ask.return_value = True
        q.select.return_value.ask.return_value = "gemini"
        with mock.patch.object(cfg, "_importar_questionary", return_value=q):
            prov, model = cfg.seleccionar_proveedor_interactivo()
        assert (prov, model) == ("gemini", None)

    def test_ollama_con_modelos(self):
        q = mock.Mock()
        q.confirm.return_value.ask.return_value = True
        q.select.return_value.ask.side_effect = ["ollama", "llama3.2"]
        with (
            mock.patch.object(cfg, "_importar_questionary", return_value=q),
            mock.patch.object(
                cfg, "_listar_modelos_ollama", return_value=(["llama3.2", "mistral"], None)
            ),
        ):
            prov, model = cfg.seleccionar_proveedor_interactivo()
        assert (prov, model) == ("ollama", "llama3.2")
