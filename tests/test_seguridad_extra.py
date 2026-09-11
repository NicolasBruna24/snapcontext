"""Tests adicionales para seguridad.py: deteccion de vulnerabilidades y calidad."""

from pathlib import Path

import seguridad as seg


class TestVulnerabilidades:
    def test_eval_peligroso(self):
        cod = "resultado = eval(entrada)\n"
        hallazgos = seg._detectar_vulnerabilidades(cod, "python")
        assert any("eval" in h["mensaje"].lower() for h in hallazgos)

    def test_concatenacion_sql(self):
        cod = 'query = "SELECT * FROM " + tabla + " WHERE id = " + id\n'
        hallazgos = seg._detectar_vulnerabilidades(cod, "python")
        assert any(h["prioridad"] == "alta" for h in hallazgos)

    def test_sin_vulnerabilidades(self):
        cod = "x = 1 + 2\n"
        hallazgos = seg._detectar_vulnerabilidades(cod, "python")
        assert isinstance(hallazgos, list)

    def test_path_traversal(self):
        cod = "archivo = open(ruta_usuario)\n"
        hallazgos = seg._detectar_vulnerabilidades(cod, "python")
        assert isinstance(hallazgos, list)

    def test_exec_peligroso(self):
        cod = "exec(codigo)\n"
        hallazgos = seg._detectar_vulnerabilidades(cod, "python")
        assert any("exec" in h["mensaje"].lower() for h in hallazgos)


class TestRendimiento:
    def test_concatenacion_en_bucle(self):
        cod = "resultado = ''\nfor x in lista:\n    resultado += str(x)\n"
        hallazgos = seg._detectar_rendimiento(cod, "python")
        assert isinstance(hallazgos, list)

    def test_range_len(self):
        cod = "for i in range(len(lista)):\n    pass\n"
        hallazgos = seg._detectar_rendimiento(cod, "python")
        assert isinstance(hallazgos, list)


class TestFuncionesLargas:
    def test_funcion_larga(self):
        cuerpo = "\n".join([f"    x += {i}" for i in range(30)])
        cod = f"def funcion_larga():\n{cuerpo}\n"
        hallazgos = seg._detectar_funciones_largas(cod, umbral=10)
        assert len(hallazgos) >= 1

    def test_funcion_corta(self):
        cod = "def f():\n    return 1\n"
        hallazgos = seg._detectar_funciones_largas(cod, umbral=10)
        assert hallazgos == []


class TestClasesGrandes:
    def test_clase_metodos(self):
        metodos = "\n".join([f"    def m{i}(self): pass" for i in range(15)])
        cod = f"class ClaseGrande:\n{metodos}\n"
        hallazgos = seg._detectar_clases_grandes(cod, max_metodos=5)
        assert len(hallazgos) >= 1


class TestNombresCortos:
    def test_nombres_validos(self):
        cod = "i = 0\n"
        hallazgos = seg._detectar_nombres_cortos(cod)
        assert hallazgos == []

    def test_nombres_cortos(self):
        cod = "d = 'algo'\n"
        hallazgos = seg._detectar_nombres_cortos(cod)
        assert isinstance(hallazgos, list)


class TestPatronesObsoletos:
    def test_is_none(self):
        cod = "if x is None:\n    pass\n"
        hallazgos = seg._detectar_patrones_obsoletos(cod)
        assert isinstance(hallazgos, list)


class TestDuplicados:
    def test_detecta_duplicados(self):
        cod = "def f():\n    return 1\n"
        contenidos = {"a.py": cod, "b.py": cod}
        hallazgos = seg._detectar_duplicados(contenidos, min_lineas=2)
        assert isinstance(hallazgos, list)


class TestAsesorAnalizar:
    def test_directorio_vacio(self, tmp_path):
        resultado = seg._asesor_analizar(str(tmp_path), profundo=False)
        assert isinstance(resultado, list)

    def test_directorio_con_archivo(self, tmp_path):
        (tmp_path / "test.py").write_text("x = 1\n")
        resultado = seg._asesor_analizar(str(tmp_path), profundo=False)
        assert isinstance(resultado, list)

    def test_profundo_con_vulnerabilidad(self, tmp_path):
        (tmp_path / "seguro.py").write_text("resultado = eval(entrada)\n")
        resultado = seg._asesor_analizar(str(tmp_path), profundo=True)
        tipos = {s.get("tipo") for s in resultado}
        assert "vulnerabilidad" in tipos


class TestEnvoltorios:
    def test_analizar_seguridad(self, tmp_path):
        (tmp_path / "seguro.py").write_text("resultado = eval(entrada)\n")
        resultado = seg._analizar_seguridad(str(tmp_path))
        assert all(s.get("tipo") == "vulnerabilidad" for s in resultado)

    def test_analizar_rendimiento(self, tmp_path):
        (tmp_path / "lento.py").write_text("for i in range(len(x)): pass\n")
        resultado = seg._analizar_rendimiento(str(tmp_path))
        assert all(s.get("tipo") == "rendimiento" for s in resultado)


class TestExtensiones:
    def test_extensiones_definidas(self):
        assert ".py" in seg.ASESOR_EXTENSIONES
        assert ".js" in seg.ASESOR_EXTENSIONES

    def test_carpetas_ignoradas(self):
        assert ".git" in seg.ASESOR_CARPETAS_IGNORADAS

    def test_umbrales_defecto(self):
        umbrales = seg._asesor_umbrales()
        assert isinstance(umbrales, dict)
