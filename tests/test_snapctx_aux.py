"""Tests snapcontext aux."""
import snapcontext as sc
from pathlib import Path

class TestSnapctxAux:
    def test_es_ext_py(self):
        assert sc._es_extension_python("x.py") is True
        assert sc._es_extension_python("x.js") is False
        assert sc._es_extension_python("x.pyx") is True
    def test_contar_tokens(self):
        assert sc._contar_tokens("") == 0
        assert sc._contar_tokens("abcd") == 1
    def test_formatear_resumen(self):
        r = sc._formatear_resumen_ast({"lenguaje": "python", "funciones": [{"nombre": "f", "linea": 1}]}, "x.py")
        assert "python" in r and "f" in r
    def test_formatear_vacio(self):
        r = sc._formatear_resumen_ast({}, "x.py")
        assert isinstance(r, str)
    def test_detectar_lenguaje_contenido(self):
        assert sc._detectar_lenguaje_contenido("") is None
        assert sc._detectar_lenguaje_contenido("def f(): pass") is not None
        assert sc._detectar_lenguaje_contenido("function f() {}") is not None
    def test_es_proyecto_valido(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert sc._es_proyecto_valido(tmp_path) is True
        assert sc._es_proyecto_valido(tmp_path / "no") is False
    def test_extraer_bloques_ast(self):
        assert sc._extraer_bloques_ast("") == []
        b = sc._extraer_bloques_ast("def f(): pass")
        assert len(b) == 1
        assert sc._extraer_bloques_ast("def x(") == []
    def test_caching(self):
        m = sc._calcular_metricas_caching([{"role": "system", "content": "hola"}])
        assert isinstance(m, dict)
