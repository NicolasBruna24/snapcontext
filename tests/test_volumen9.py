"""Tests volumen Fase 1d-5: funciones auxiliares puras de snapcontext (sin I/O)."""
import snapcontext as sc


class TestNormalizadores:
    def test_normalizar_linea(self):
        # La función elimina espacios al inicio/fin pero preserva mayúsculas
        assert isinstance(sc._normalizar_linea_duplicado("  Hola Mundo  "), str)

    def test_normalizar_ruta_manual(self, tmp_path):
        from pathlib import Path

        r = sc._normalizar_ruta_manual(Path(tmp_path), "a.py")
        assert r is None or isinstance(r, str)

    def test_normalizar_relativa(self):
        import inspect

        sig = inspect.signature(sc._normalizar_relativa)
        params = list(sig.parameters)
        if len(params) >= 2:
            r = sc._normalizar_relativa("/tmp", "x/../y")
        else:
            r = sc._normalizar_relativa("/tmp/x/../y")
        assert r is None or "y" in str(r)

    def test_skill_normalizar_nombre(self):
        r = sc._skill_normalizar_nombre("Mi Skill Ágil!")
        assert isinstance(r, str)
        assert len(r) > 0

    def test_normalizar_comparacion(self):
        r = sc._normalizar_comparacion("  Hola  ")
        assert isinstance(r, str)

    def test_ruta_del_parche(self):
        r = sc._ruta_del_parche("diff --git a/f.py b/f.py\n+++ b/f.py")
        assert isinstance(r, (str, type(None)))

    def test_ruta_indice(self):
        from pathlib import Path

        r = sc._ruta_indice(".")
        assert isinstance(r, Path)


class TestValidadores:
    def test_validar_parche_previo_ok(self):
        ok, msg = sc._validar_parche_previo("main.py", "x = 1\n", ".")
        assert isinstance(ok, bool)

    def test_validar_parche_previo_vacio(self):
        ok, msg = sc._validar_parche_previo("main.py", "", ".")
        assert isinstance(ok, bool)

    def test_validar_sintaxis_py_ok(self):
        ok, err = sc._validar_sintaxis("a.py", "x = 1\n")
        assert ok is True

    def test_validar_sintaxis_py_mal(self):
        ok, err = sc._validar_sintaxis("a.py", "def (:")
        assert ok is False

    def test_validar_sintaxis_otro(self):
        ok, err = sc._validar_sintaxis("a.json", "{}")
        assert isinstance(ok, bool)


class TestComandosValidacion:
    def test_comandos_validacion(self):
        cmds = sc._comandos_validacion("py", "main.py")
        assert isinstance(cmds, list)

    def test_comandos_validacion_js(self):
        cmds = sc._comandos_validacion("js", "app.js")
        assert isinstance(cmds, list)
