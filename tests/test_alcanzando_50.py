import os
from pathlib import Path
from unittest import mock

import snapcontext as sc


class TestCasos:
    def t1(self):
        assert isinstance(sc.VERSION, str)

    def t2(self):
        assert isinstance(sc.CARPETAS_DEFECTO, tuple)

    def t3(self):
        assert isinstance(sc.CARPETAS_PROYECTO_VALIDAS, (tuple, list))

    def t4(self):
        assert sc._contar_tokens("") == 0

    def t5(self):
        assert sc._contar_tokens("hola") >= 0

    def t6(self):
        assert sc._es_extension_python("a.py") is True

    def t7(self):
        assert sc._es_extension_python("a.js") is False

    def t8(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert sc._es_proyecto_valido(tmp_path) is True

    def t9(self, tmp_path):
        assert sc._es_proyecto_valido(tmp_path) is False

    def t10(self, tmp_path):
        assert sc._es_proyecto_valido(tmp_path / "x") is False

    def t11(self):
        assert len(sc._extraer_bloques_ast("def f(): pass")) == 1

    def t12(self):
        assert len(sc._extraer_bloques_ast("class C: pass")) == 1

    def t13(self):
        assert sc._extraer_bloques_ast("") == []

    def t14(self):
        assert sc._extraer_bloques_ast("def x(") == []

    def t15(self):
        assert isinstance(sc._calcular_metricas_caching([]), dict)

    def t16(self):
        assert isinstance(sc._calcular_metricas_caching([{"role": "u", "content": "h"}]), dict)

    def t17(self):
        assert isinstance(sc._calcular_metricas_caching([{"role": "system", "content": "s"}]), dict)

    def t18(self):
        r = sc._formatear_tabla([])
        assert isinstance(r, str)

    def t19(self):
        r = sc._formatear_tabla([("a", "1"), ("b", "2")])
        assert "a" in r and "b" in r

    def t20(self):
        assert "x" in sc._pintar("x", sc._ROJO)

    def t21(self):
        assert "x" in sc._pintar("x", sc._VERDE)

    def t22(self):
        assert "x" in sc._pintar("x", sc._AMARILLO)

    def t23(self):
        assert "x" in sc._pintar("x", sc._CYAN)

    def t24(self):
        assert isinstance(sc._formatear_tiempo(0), str)

    def t25(self):
        assert isinstance(sc._formatear_tiempo(60), str)

    def t26(self):
        assert isinstance(sc._formatear_tiempo(3600), str)

    def t27(self):
        with mock.patch("configuracion.cargar_configuracion", return_value={"provider": "ollama"}):
            assert sc._obtener_proveedor() == "ollama"

    def t28(self):
        with mock.patch("configuracion.cargar_configuracion", return_value={"provider": "gemini"}):
            assert sc._obtener_proveedor() == "gemini"

    def t29(self):
        with mock.patch("configuracion.cargar_configuracion", return_value={}):
            sc._obtener_proveedor()

    def t30(self):
        with mock.patch("configuracion.cargar_configuracion", return_value={"modelo": "m"}):
            assert sc._obtener_modelo() == "m"

    def t31(self):
        with mock.patch("configuracion.cargar_configuracion", return_value={}):
            sc._obtener_modelo()

    def t32(self):
        assert len(sc._generar_id_tarea()) > 0

    def t33(self):
        assert sc._generar_id_tarea() != sc._generar_id_tarea()

    def t34(self):
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            sc._emitir("test", flujo=None)

    def t35(self, tmp_path):
        (tmp_path / "lib").mkdir()
        assert sc._es_proyecto_valido(tmp_path) is True

    def t36(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        assert sc._es_proyecto_valido(tmp_path) is True

    def t37(self, tmp_path):
        (tmp_path / "main.go").write_text("")
        assert sc._es_proyecto_valido(tmp_path) is True

    def t38(self):
        assert sc._contar_tokens("a" * 1000) == 250

    def t39(self):
        assert sc._es_extension_python("test.pyw") is True

    def t40(self):
        bloques = sc._extraer_bloques_ast("async def f(): pass")
        assert len(bloques) == 1
