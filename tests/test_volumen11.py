"""Tests Fase 1d: AgenteEditorPropio (agentes.py) metodos unitarios."""

from unittest import mock

import agentes as ag


def _editor():
    return ag.AgenteEditorPropio()


class TestCadenaModos:
    def test_sobrescribir(self):
        assert _editor()._cadena_modos("a.py", "x", "sobrescribir") == ["sobrescribir"]

    def test_parche(self):
        assert _editor()._cadena_modos("a.py", "x", "parche") == ["parche"]

    def test_ast(self):
        assert _editor()._cadena_modos("a.py", "x", "ast") == ["ast", "sobrescribir"]

    def test_auto_con_ast(self):
        with (
            mock.patch("snapcontext._ast_disponible", return_value=True),
            mock.patch.object(ag, "_tarea_estructura", return_value=True),
        ):
            cadena = _editor()._cadena_modos("a.py", "renombra x a y", "auto")
        assert cadena == ["ast", "parche", "sobrescribir"]

    def test_auto_sin_ast(self):
        with mock.patch("snapcontext._ast_disponible", return_value=False):
            cadena = _editor()._cadena_modos("a.py", "x", "auto")
        assert cadena == ["parche", "sobrescribir"]


class TestPrepararContenidoEnvio:
    def test_envio_completo_si_pequeno(self):
        ed = _editor()
        envio, truncado, obj, n = ed._preparar_contenido_envio("a.py", "tarea", "codigo corto", 100)
        assert truncado is False
        assert envio == "codigo corto"

    def test_envio_truncado_si_grande(self):
        ed = _editor()
        with (
            mock.patch("context_utils.estimar_tokens", return_value=500),
            mock.patch("snapcontext._lenguaje_archivo", return_value="python"),
            mock.patch("context_utils.objetivo_en_mensaje", return_value="foo"),
            mock.patch("context_utils.seleccionar_contexto", return_value="resumido") as msel,
        ):
            envio, truncado, obj, n = ed._preparar_contenido_envio(
                "a.py", "cambia foo", "x" * 100, 10
            )
        assert truncado is True
        assert envio == "resumido"
        assert obj == "foo"
        assert msel.called


class TestEjecutarConAider:
    def test_sin_archivos(self):
        assert _editor()._ejecutar_con_aider([], "msg", ".") is True

    @mock.patch("shutil.which", return_value=None)
    def test_aider_no_instalado(self, mw):
        assert _editor()._ejecutar_con_aider(["a.py"], "msg", ".") is False

    @mock.patch("shutil.which", return_value="/usr/local/bin/aider")
    @mock.patch("snapcontext.ejecutar_aider", return_value=True)
    def test_aider_ok(self, me, mw):
        assert _editor()._ejecutar_con_aider(["a.py"], "msg", ".") is True

    @mock.patch("shutil.which", return_value="/usr/local/bin/aider")
    @mock.patch("snapcontext.ejecutar_aider", side_effect=RuntimeError("boom"))
    def test_aider_falla(self, me, mw):
        assert _editor()._ejecutar_con_aider(["a.py"], "msg", ".") is False


class TestAplicarParchePreview:
    def test_aplica_parche_simple(self):
        parche = "@@ -1,3 +1,3 @@\n hola\n-mundo\n+mundo2\n fin\n"
        resultado, n = ag.AgenteEditorPropio._aplicar_parche_preview(parche, "hola\nmundo\nfin\n")
        assert n >= 1
        assert "mundo2" in resultado

    def test_parche_no_aplicable(self):
        parche = "@@ -9,2 +9,2 @@\n-x\n+y\n"
        resultado, n = ag.AgenteEditorPropio._aplicar_parche_preview(parche, "hola\n")
        assert n == 0

    def test_contenido_vacio(self):
        resultado, n = ag.AgenteEditorPropio._aplicar_parche_preview("", "")
        assert n == 0


class TestEditarAst:
    @mock.patch("snapcontext.cargar_configuracion", return_value={"provider": "gemini"})
    @mock.patch("snapcontext._editor_ast", return_value=True)
    def test_delega_en_editor_ast(self, med, mcfg):
        assert _editor().editar_ast("a.py", "tarea", ".") is True
        med.assert_called_once()
        args, _ = med.call_args
        assert args[0] == "a.py"
