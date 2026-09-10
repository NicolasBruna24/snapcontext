"""Tests para seguridad.py: asesor de codigo (v4.2.0).

Las funciones toman strings de codigo y devuelven hallazgos; no requieren
APIs, subprocess ni E/S, por lo que son faciles de testear con mocks minimos.
"""
import ast
from unittest import mock

import seguridad as seg


CODIGO_FUNCION_LARGA = '''
def funcion_larga():
    x = 1
    x = 2
    x = 3
    x = 4
    x = 5
    x = 6
    x = 7
    x = 8
    x = 9
    x = 10
    x = 11
    return x
'''


CODIGO_VULNERABLE = '''
import os
import subprocess

password = "mi_super_secreto_12345"

def mal():
    os.system("ls " + entrada)
    subprocess.call("cmd", shell=True)
    eval(x)
    exec(x)
    query = "SELECT * FROM users WHERE id = " + id
'''


class TestAsesorUmbrales:
    def test_defecto(self):
        u = seg._asesor_umbrales()
        assert isinstance(u, dict)
        assert "funcion_larga" in u

    def test_personalizado_via_config(self):
        fake = {"asesor": {"funcion_larga": 99}}
        with mock.patch("snapcontext.cargar_configuracion", return_value=fake):
            u = seg._asesor_umbrales()
        assert u["funcion_larga"] == 99

    def test_config_ignora_claves_invalidas(self):
        fake = {"asesor": {"funcion_larga": "no-es-int", "inexistente": 5}}
        with mock.patch("snapcontext.cargar_configuracion", return_value=fake):
            u = seg._asesor_umbrales()
        assert isinstance(u["funcion_larga"], int)

    def test_config_ignora_excepciones(self):
        with mock.patch("snapcontext.cargar_configuracion", side_effect=RuntimeError):
            u = seg._asesor_umbrales()
        assert isinstance(u, dict)


class TestFuncionesLargas:
    def test_detecta_por_encima_del_umbral(self):
        hallazgos = seg._detectar_funciones_largas(CODIGO_FUNCION_LARGA, umbral=5)
        assert any(h["nombre"] == "funcion_larga" for h in hallazgos)
        assert all(h["lineas"] > 5 for h in hallazgos)

    def test_no_detecta_si_baja_umbral(self):
        hallazgos = seg._detectar_funciones_largas(CODIGO_FUNCION_LARGA, umbral=999)
        assert hallazgos == []

    def test_devuelve_lista_vacia_con_sintaxis_invalida(self):
        assert seg._detectar_funciones_largas("def x(:\n", umbral=1) == []


class TestClasesGrandes:
    def test_detecta_clase_con_metodos(self):
        codigo = (
            "class Foo:\n"
            + "".join(f"    def m{i}(self): pass\n" for i in range(15))
        )
        hallazgos = seg._detectar_clases_grandes(codigo, max_metodos=10)
        assert any(h["nombre"] == "Foo" for h in hallazgos)

    def test_no_detecta_clase_chica(self):
        codigo = "class Bar:\n    def uno(self): pass\n"
        assert seg._detectar_clases_grandes(codigo, max_metodos=10) == []

    def test_sintaxis_invalida(self):
        assert seg._detectar_clases_grandes("class X(:\n", 1) == []


class TestNombresCortos:
    def test_detecta_nombres_cortos(self):
        codigo = "ab = 1\ncd = 2\nfor i in range(10):\n    x = i\n"
        hallazgos = seg._detectar_nombres_cortos(codigo)
        nombres = {h["nombre"] for h in hallazgos}
        assert "ab" in nombres or "cd" in nombres

    def test_no_detecta_nombres_validos(self):
        codigo = "index = 1\nfor item in lista: pass\n"
        hallazgos = seg._detectar_nombres_cortos(codigo)
        assert all(len(h["nombre"]) > 2 for h in hallazgos)

    def test_sintaxis_invalida(self):
        assert seg._detectar_nombres_cortos("def x(:\n") == []


class TestPatronesObsoletos:
    def test_except_desnudo(self):
        codigo = "try:\n    pass\nexcept:\n    pass\n"
        h = seg._detectar_patrones_obsoletos(codigo)
        assert any("except:" in x["mensaje"] for x in h)

    def test_igual_none(self):
        codigo = "if x == None: pass\n"
        h = seg._detectar_patrones_obsoletos(codigo)
        assert any("is None" in x["mensaje"] for x in h)

    def test_has_key(self):
        codigo = "if diccionario.has_key('a'): pass\n"
        h = seg._detectar_patrones_obsoletos(codigo)
        assert any("has_key" in x["mensaje"] for x in h)

    def test_sin_comentarios(self):
        codigo = "# except:\n# == None\n"
        h = seg._detectar_patrones_obsoletos(codigo)
        assert h == []


class TestDuplicados:
    def test_detecta_bloques_entre_archivos(self):
        cod = "linea uno\nlinea dos\nlinea tres\nlinea cuatro\ncinco\n"
        contenidos = {"a.py": cod, "b.py": cod}
        h = seg._detectar_duplicados(contenidos, min_lineas=4)
        assert len(h) >= 1
        assert h[0]["archivo"] in ("a.py", "b.py")

    def test_no_detecta_si_son_diferentes(self):
        c1 = "uno\ndos\ntres\ncuatro\n"
        c2 = "alpha\nbeta\ngamma\ndelta\n"
        h = seg._detectar_duplicados({"a.py": c1, "b.py": c2}, min_lineas=3)
        assert h == []

    def test_vacio(self):
        assert seg._detectar_duplicados({}, min_lineas=3) == []


class TestVulnerabilidades:
    def test_os_system(self):
        h = seg._detectar_vulnerabilidades("import os\nos.system(entrada)\n")
        assert any("os.system" in x["mensaje"] for x in h)
        assert all("prioridad" in x for x in h)

    def test_shell_true(self):
        h = seg._detectar_vulnerabilidades("subprocess.call(cmd, shell=True)\n")
        assert any("shell=True" in x["mensaje"] for x in h)

    def test_eval_exec(self):
        h = seg._detectar_vulnerabilidades(CODIGO_VULNERABLE)
        mensajes = " ".join(x["mensaje"] for x in h)
        assert "eval" in mensajes
        assert "exec" in mensajes

    def test_inyeccion_sql(self):
        h = seg._detectar_vulnerabilidades("query = 'SELECT * FROM t WHERE id = ' + id\n")
        assert any("SQL" in x["mensaje"] for x in h)

    def test_path_traversal(self):
        h = seg._detectar_vulnerabilidades('open(ruta + "../etc/passwd")\n')
        assert any("traversal" in x["mensaje"] for x in h)

    def test_secreto_embebido(self):
        h = seg._detectar_vulnerabilidades('API_KEY_MIA = "abcdefghijklmnop"\n')
        assert any("secreto" in x["mensaje"].lower() or "hardcode" in x["mensaje"].lower() for x in h)

    def test_sin_vulnerabilidades(self):
        h = seg._detectar_vulnerabilidades('x = 1\nprint(x)\n')
        assert h == []

    def test_codigo_vacio(self):
        assert seg._detectar_vulnerabilidades("") == []


class TestRendimiento:
    def test_range_len(self):
        h = seg._detectar_rendimiento("for i in range(len(lista)): pass\n")
        assert any("range(len" in x["mensaje"] for x in h)

    def test_bucles_anidados(self):
        codigo = "for i in lista:\n    for j in otra:\n        pass\n"
        h = seg._detectar_rendimiento(codigo)
        assert any("cuadratico" in x["mensaje"] or "O(n\u00b2)" in x["mensaje"] for x in h)

    def test_concatenacion_en_bucle(self):
        codigo = "acum = ''\nfor x in lista:\n    acum += str(x)\n"
        h = seg._detectar_rendimiento(codigo)
        any("copias" in x["mensaje"].lower() or "+=" in x["mensaje"] for x in h)

    def test_django_n1(self):
        h = seg._detectar_rendimiento("for x in items:\n    x.objects.get(id=1)\n")
        assert any("N+1" in x["mensaje"] for x in h)


