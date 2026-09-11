"""Tests volumen Fase 1d-4: agentes (clases pequeñas), permisos, presentacion."""

from pathlib import Path
from unittest import mock

import agentes as ag


class TestEsErrorContexto:
    def test_context_length(self):
        assert ag._es_error_contexto(ValueError("context length exceeded")) is True

    def test_otro_error(self):
        assert ag._es_error_contexto(ValueError("otra cosa")) is False


class TestAgenteTesterVol:
    def test_analizar_error_str(self):
        t = ag.AgenteTester()
        assert "fallo" in t.analizar_error("fallo \x1b[31mrojo\x1b[0m")

    def test_analizar_error_largo(self):
        t = ag.AgenteTester()
        assert "recortada" in t.analizar_error("x" * 7000)

    def test_analizar_error_vacio(self):
        t = ag.AgenteTester()
        assert isinstance(t.analizar_error(""), str)

    def test_ejecutar_pruebas(self, tmp_path):
        t = ag.AgenteTester()
        m = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch("subprocess.run", return_value=m):
            r = t.ejecutar_pruebas(["pytest"], str(tmp_path))
        assert r.returncode == 0


class TestAgenteAprendizajeVol:
    def test_inicializar(self, tmp_path):
        import snapcontext as sc

        a = ag.AgenteAprendizaje()
        with mock.patch.object(sc, "_db_init", return_value=str(tmp_path / "m.db")):
            assert "m.db" in a.inicializar()

    def test_buscar_skill(self):
        import snapcontext as sc

        a = ag.AgenteAprendizaje()
        with mock.patch.object(sc, "_skill_buscar", return_value=None):
            assert a.buscar_skill("x") is None

    def test_registrar_exito_fallo(self):
        import snapcontext as sc

        a = ag.AgenteAprendizaje()
        with mock.patch.object(sc, "_skill_registrar_exito", return_value=1.0):
            assert a.registrar_exito(1) == 1.0
        with mock.patch.object(sc, "_skill_registrar_fallo", return_value=0.5):
            assert a.registrar_fallo(1) == 0.5

    def test_listar_skills(self):
        import snapcontext as sc

        a = ag.AgenteAprendizaje()
        with mock.patch.object(sc, "_skill_listar", return_value=[{"id": 1}]):
            assert len(a.listar_skills()) == 1

    def test_aprender_y_encolar(self):
        import snapcontext as sc

        a = ag.AgenteAprendizaje()
        with mock.patch.object(sc, "_aprender_de_tarea", return_value=3):
            assert a.aprender_de_tarea("q", True, []) == 3
        with mock.patch.object(sc, "_cola_encolar", return_value=7):
            assert a.encolar_skill(1) == 7

    def test_generar_y_curar(self):
        import snapcontext as sc

        a = ag.AgenteAprendizaje()
        with mock.patch.object(sc, "_skill_generar", return_value=2):
            assert a.generar_skill("q", []) == 2
        with mock.patch.object(sc, "_curador_ejecutar", return_value={"ok": True}):
            assert a.curar()["ok"] is True


class TestAgenteAsesorVol:
    def test_init(self):
        a = ag.AgenteAsesor()
        assert a is not None

    def test_asesorar_basico(self, tmp_path):
        import snapcontext as sc

        a = ag.AgenteAsesor()
        with mock.patch.object(sc, "_asesor_analizar", return_value=[{"sugerencia": "x"}]):
            r = a.analizar(str(tmp_path))
        assert isinstance(r, list)
        with mock.patch.object(sc, "_analizar_seguridad", return_value=[]):
            assert a.analizar_seguridad(str(tmp_path)) == []


class TestPermisosConfirmar:
    def test_confirmar_false_devuelve_true(self):
        import permisos

        assert permisos._confirmar_accion("comando", "ls", confirmar=False) is True

    def test_recordado_nunca_deniega(self):
        import permisos

        with (
            mock.patch.object(permisos, "_cargar_permisos", return_value={"comando": "nunca"}),
            mock.patch("builtins.input", return_value="n"),
        ):
            assert permisos._confirmar_accion("hacer ls", "comando", confirmar=True) is False


class TestPresentacionVol:
    def test_pintar(self):
        import presentacion

        assert isinstance(presentacion._pintar("hola", presentacion._VERDE), str)

    def test_emitir(self):
        import io

        import presentacion

        buf = io.StringIO()
        presentacion._emitir(buf, "hola")
        assert "hola" in buf.getvalue()

    def test_aviso_no_falla(self, capsys):
        import presentacion

        presentacion.aviso("msg")
        out, _ = capsys.readouterr()
        assert "msg" in out
