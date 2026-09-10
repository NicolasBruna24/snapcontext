"""Tests adicionales para snapcontext.py: funciones aisladas del monolito."""
from pathlib import Path
import snapcontext as sc


class TestEsProyectoValido:
    def test_carpeta_src(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert sc._es_proyecto_valido(tmp_path) is True

    def test_archivo_python_raiz(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hola')")
        assert sc._es_proyecto_valido(tmp_path) is True

    def test_archivo_config_raiz(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        assert sc._es_proyecto_valido(tmp_path) is True

    def test_carpeta_vacia_no_valida(self, tmp_path):
        assert sc._es_proyecto_valido(tmp_path) is False

    def test_ruta_no_existente(self, tmp_path):
        assert sc._es_proyecto_valido(tmp_path / "no_existe") is False


class TestExtraerBloquesAst:
    def test_funcion_python(self):
        cod = "def hola():\n    pass\n"
        bloques = sc._extraer_bloques_ast(cod)
        assert len(bloques) == 1

    def test_clase_python(self):
        cod = "class Foo:\n    pass\n"
        bloques = sc._extraer_bloques_ast(cod)
        assert len(bloques) >= 1

    def test_codigo_vacio(self):
        assert sc._extraer_bloques_ast("") == []

    def test_sintaxis_invalida(self):
        assert sc._extraer_bloques_ast("def x(:\n") == []

    def test_multiples_bloques(self):
        cod = "def a(): pass\nclass B: pass\nasync def c(): pass\n"
        bloques = sc._extraer_bloques_ast(cod)
        assert len(bloques) == 3


class TestContarTokens:
    def test_texto_vacio(self):
        assert sc._contar_tokens("") == 0

    def test_texto_corto(self):
        assert sc._contar_tokens("hola") == 1

    def test_texto_largo(self):
        texto = "a" * 100
        assert sc._contar_tokens(texto) == 25


class TestCalcularMetricasCaching:
    def test_mensajes_vacios(self):
        metricas = sc._calcular_metricas_caching([])
        assert isinstance(metricas, dict)

    def test_mensajes_con_sistema(self):
        msgs = [
            {"role": "system", "content": "Eres un asistente"},
            {"role": "user", "content": "Hola"},
        ]
        metricas = sc._calcular_metricas_caching(msgs)
        assert isinstance(metricas, dict)
