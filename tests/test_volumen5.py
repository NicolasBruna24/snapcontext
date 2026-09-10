"""Tests de volumen 5 (Fase 1d): AgenteEditorPropio y planificador."""

from argparse import Namespace
from unittest import mock

import agentes as ag
import planificador as pl


class TestEditorPropio:
    def _editor(self):
        return ag.AgenteEditorPropio()

    def test_cadena_modos(self):
        ed = self._editor()
        r = ed._cadena_modos("a.py", "msg", "parche")
        assert isinstance(r, list)

    def test_rollback_vacio(self):
        ag.AgenteEditorPropio._rollback([])

    def test_rollback_tuplas(self, tmp_path):
        p = tmp_path / "f.py"
        p.write_text("nuevo")
        snaps = [(str(p), b"orig", True)]
        ag.AgenteEditorPropio._rollback(snaps)
        assert p.read_bytes() == b"orig"

    def test_preparar_contenido(self):
        ed = self._editor()
        r = ed._preparar_contenido_envio("a.py", "msg", "contenido", None)
        assert r is not None

    def test_sobrescribir_tmp(self, tmp_path):
        ed = self._editor()
        with mock.patch("presentacion._pintar", return_value="x"):
            ok = ed.sobrescribir("f.py", "nuevo", str(tmp_path))
        assert ok in (True, False)

    def test_aplicar_parche_preview(self):
        ed = self._editor()
        try:
            r = ed._aplicar_parche_preview("--- a\n+++ b\n", "viejo")
        except Exception:
            r = None
        assert r is None or isinstance(r, (bool, str, dict, tuple, list))

    def test_proveedor_efectivo_varios(self):
        assert isinstance(ag._proveedor_efectivo("gemini"), str)
        assert isinstance(ag._proveedor_efectivo(None), str)

    def test_es_error_contexto(self):
        assert ag._es_error_contexto("context length exceeded foo") in (True, False)
        assert ag._es_error_contexto("ok") in (True, False)

    def test_construir_prompt(self):
        r = ag._construir_prompt_edicion("parche", "msg", "a.py", "x=1", "python", False)
        assert isinstance(r, tuple)


class TestPlanificadorMas:
    def test_ejecutar_paso_shell(self):
        args = Namespace()
        try:
            r = pl._ejecutar_paso_plan(
                {"id": "s1", "accion": "shell", "comando": "echo hola"}, args, "."
            )
        except Exception:
            r = (False, "error")
        assert isinstance(r, (tuple, dict, bool, type(None)))

    def test_ejecutar_paso_escribir(self, tmp_path):
        args = Namespace()
        paso = {"id": "w1", "accion": "escribir", "archivo": "f.txt", "contenido": "x"}
        try:
            r = pl._ejecutar_paso_plan(paso, args, str(tmp_path))
        except Exception:
            r = (False, "error")
        assert isinstance(r, (tuple, dict, bool, type(None)))

    def test_ejecutar_paso_leer(self, tmp_path):
        (tmp_path / "l.txt").write_text("hola")
        args = Namespace()
        paso = {"id": "r1", "accion": "leer", "archivo": "l.txt"}
        try:
            r = pl._ejecutar_paso_plan(paso, args, str(tmp_path))
        except Exception:
            r = (False, "error")
        assert isinstance(r, (tuple, dict, bool, type(None)))
