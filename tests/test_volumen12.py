"""Tests Fase 1d: aplicar_reemplazo_estructurado + _parsear_hunks."""

import pytest

import editor as ed
import snapcontext as sc


class TestAplicarReemplazoEstructurado:
    def test_reemplazo_exacto(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    return 1\n")
        resultado = sc.aplicar_reemplazo_estructurado(
            "a.py", "def foo():\n    return 1\n", "def foo():\n    return 2\n", str(tmp_path)
        )
        assert "return 2" in resultado
        assert "return 1" not in resultado

    def test_archivo_no_encontrado(self, tmp_path):
        with pytest.raises(ValueError):
            sc.aplicar_reemplazo_estructurado("no.py", "x", "y", str(tmp_path))

    def test_bloque_vacio(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("hola")
        with pytest.raises(ValueError):
            sc.aplicar_reemplazo_estructurado("a.py", "   ", "y", str(tmp_path))

    def test_path_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            sc.aplicar_reemplazo_estructurado("../../etc/passwd", "x", "y", str(tmp_path))

    def test_normalizado_espacios(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("def foo():\n    return 1\n")
        resultado = sc.aplicar_reemplazo_estructurado(
            "a.py", "def foo():    return 1", "def foo():    return 2", str(tmp_path)
        )
        assert "return 2" in resultado

    def test_bloque_no_encontrado(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("otra cosa\n")
        with pytest.raises(ValueError):
            sc.aplicar_reemplazo_estructurado("a.py", "AAAAlgoInexistente", "y", str(tmp_path))


class TestParsearHunks:
    def test_parche_vacio(self):
        assert ed._parsear_hunks("") == []

    def test_hunk_simple(self):
        parche = "@@ -1,2 +1,2 @@\n-viejo\n+nuevo\n"
        hunks = ed._parsear_hunks(parche)
        assert hunks
        inicio, cambios = hunks[0]
        assert inicio >= 0
        assert any(marca == "-" for marca, _ in cambios)

    def test_sin_heads_hunk(self):
        assert ed._parsear_hunks("--- a/x\n+++ b/x\n") == []

    def test_hunk_solo_contexto_se_omite(self):
        parche = "@@ -1,1 +1,1 @@\n contexto\n"
        hunks = ed._parsear_hunks(parche)
        assert hunks == []
