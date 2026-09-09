"""Tests del núcleo del monolito (snapcontext.py) — Fase 1 de cobertura.

Cubre las funciones críticas sin llamadas externas: validación de rutas,
escritura segura del editor, aplicación de parches y extracción AST.
Todo lo externo (subprocess, shutil, sistema de archivos) va mockeado.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest import mock

import pytest

import snapcontext as sc


class TestValidarRutaSegura:
    def test_ruta_dentro_del_proyecto(self, tmp_path: Path):
        base = tmp_path / "proyecto"
        base.mkdir()
        interior = base / "src" / "mod.py"
        resultado = sc._validar_ruta_segura(interior, base)
        assert resultado == interior.resolve()

    def test_ruta_fuera_del_proyecto_lanza(self, tmp_path: Path):
        base = tmp_path / "proyecto"
        fuera = tmp_path / "outside.py"
        base.mkdir()
        with pytest.raises(ValueError):
            sc._validar_ruta_segura(fuera, base)

    def test_path_traversal_lanza(self, tmp_path: Path):
        base = tmp_path / "proyecto"
        base.mkdir()
        atacante = base / ".." / "outside.py"
        with pytest.raises(ValueError):
            sc._validar_ruta_segura(atacante, base)

    def test_acepta_str_y_path(self, tmp_path: Path):
        base = tmp_path / "p"
        base.mkdir()
        r = sc._validar_ruta_segura(str(base / "a.py"), str(base))
        assert r == (base / "a.py").resolve()


class TestEditorSobrescribir:
    def test_archivo_vacio_devuelve_false(self):
        assert sc._editor_sobrescribir("", "contenido") is False
        assert sc._editor_sobrescribir("   ", "contenido") is False

    def test_path_traversal_rechazado(self):
        assert sc._editor_sobrescribir("../escape.py", "x") is False

    def test_ruta_absoluta_rechazada(self):
        assert sc._editor_sobrescribir("/etc/passwd", "x") is False

    def test_escribe_archivo_nuevo(self, tmp_path: Path):
        local = tmp_path / "mod"
        ok = sc._editor_sobrescribir("mod/nuevo.py", "print('hola')\n", str(tmp_path))
        assert ok is True
        assert (local / "nuevo.py").read_text(encoding="utf-8") == "print('hola')\n"

    def test_ruta_fuera_del_proyecto_devuelve_false(self, tmp_path: Path):
        proyecto = tmp_path / "proyecto"
        proyecto.mkdir()
        fuera = tmp_path / "fuera.py"
        ok = sc._editor_sobrescribir(str(fuera), "x", str(proyecto))
        assert ok is False


class TestAplicarParche:
    def test_parche_vacio_devuelve_false(self):
        assert sc._aplicar_parche("", ".") is False
        assert sc._aplicar_parche("   \n", ".") is False

    def test_parche_malicioso_path_traversal_rechazado(self, tmp_path: Path):
        parche = "--- a/x.py\n+++ b/../../../outside.py\n+mal\n"
        with mock.patch.object(sc, "aviso") as aviso_mock:
            resultado = sc._aplicar_parche(parche, str(tmp_path))
        assert resultado is False
        aviso_mock.assert_called()

    def test_parche_valido_git_apply_ok(self, tmp_path: Path):
        parche = (
            "diff --git a/ejemplo.py b/ejemplo.py\n"
            "--- a/ejemplo.py\n"
            "+++ b/ejemplo.py\n"
            "@@ -1 +1 @@\n"
            " print('hola')\n"
        )
        fake = types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with (
            mock.patch.object(sc, "subprocess") as sp,
            mock.patch.object(sc, "shutil") as shutil_mock,
        ):
            shutil_mock.which.return_value = "/usr/bin/git"
            sp.run.return_value = fake
            sp.CREATE_NO_WINDOW = 0
            resultado = sc._aplicar_parche(parche, str(tmp_path))
        assert resultado is True
        sp.run.assert_called()

    def test_parche_falla_ambas_herramientas(self, tmp_path: Path):
        parche = "--- a/a.py\n+++ b/a.py\n+hola\n"
        fake = types.SimpleNamespace(returncode=1, stdout="", stderr="conflicto")
        with (
            mock.patch.object(sc, "subprocess") as sp,
            mock.patch.object(sc, "shutil") as shutil_mock,
        ):
            shutil_mock.which.side_effect = ["/usr/bin/git", "/usr/bin/patch"]
            sp.run.return_value = fake
            sp.CREATE_NO_WINDOW = 0
            resultado = sc._aplicar_parche(parche, str(tmp_path))
        assert resultado is False


class TestExtraerBloquesAst:
    def test_extrae_funciones_y_clases(self):
        contenido = "def hola():\n    return 1\n\nclass Foo:\n    def bar(self):\n        pass\n"
        bloques = sc._extraer_bloques_ast(contenido, "x.py")
        nombres = [b["nombre"] for b in bloques]
        assert "hola" in nombres
        assert "Foo" in nombres
        assert all(isinstance(b["fin"], int) for b in bloques)

    def test_sintaxis_invalida_devuelve_vacio(self):
        assert sc._extraer_bloques_ast("def ( no valid", "x.py") == []

    def test_sin_archivo_trata_como_python(self):
        bloques = sc._extraer_bloques_ast("def a():\n    pass\n", None)
        assert any(b["nombre"] == "a" for b in bloques)
