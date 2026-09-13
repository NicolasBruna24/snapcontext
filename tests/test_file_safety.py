"""Tests para el punto unico de escritura segura (utils.escribir_archivo_seguro).

Cubre: escritura feliz, path traversal, symlinks, TOCTOU, directorio padre,
y compatibilidad con cada call-site migrado (snapcontext, agentes, react_agent,
autocorrector).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Asegurar que el paquete es importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils
from utils import RutaInseguraError, escribir_archivo_seguro


def _raiz_temporal(tmp_path: Path) -> Path:
    """Crea un directorio de proyecto simulado con un .git para que la
    heuristica de _rollback detecte el root."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    return tmp_path


class TestEscrituraFeliz:
    def test_crear_archivo_nuevo(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        escribir_archivo_seguro("src/nuevo.py", "hola", raiz)
        assert (raiz / "src/nuevo.py").read_text(encoding="utf-8") == "hola"

    def test_sobrescribir_archivo_existente(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        existente = raiz / "src/existente.py"
        existente.write_text("antes", encoding="utf-8")
        escribir_archivo_seguro("src/existente.py", "despues", raiz)
        assert existente.read_text(encoding="utf-8") == "despues"

    def test_crear_en_subdirectorio_anidado(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        escribir_archivo_seguro("src/pkg/mod.py", "x = 1", raiz)
        assert (raiz / "src/pkg/mod.py").read_text(encoding="utf-8") == "x = 1"

    def test_contenido_vacio(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        escribir_archivo_seguro("vacio.py", "", raiz)
        assert (raiz / "vacio.py").read_text(encoding="utf-8") == ""

    def test_contenido_unicode(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        escribir_archivo_seguro("unicode.py", "nono 中文", raiz)
        assert (raiz / "unicode.py").read_text(encoding="utf-8") == "nono 中文"


class TestPathTraversal:
    def test_dos_puntos_escapa(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        with pytest.raises(RutaInseguraError):
            escribir_archivo_seguro("../etc/passwd", "x", raiz)

    def test_path_con_parentesis_y_puntos(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        with pytest.raises(RutaInseguraError):
            escribir_archivo_seguro("src/../../../etc/passwd", "x", raiz)
    def test_archivo_es_symlink_fuera(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        # Apuntar FUERA del tmp_path completo (no solo del proyecto).
        fuera = tmp_path.parent / f"externo_snap_{os.getpid()}.py"
        fuera.write_text("original", encoding="utf-8")
        try:
            enlace = raiz / "src/enlace.py"
            enlace.symlink_to(fuera)
            with pytest.raises(RutaInseguraError):
                escribir_archivo_seguro("src/enlace.py", "modificado", raiz)
            assert fuera.read_text(encoding="utf-8") == "original"
        finally:
            if fuera.exists():
                fuera.unlink()

    def test_symlink_padre_fuera(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        fuera = tmp_path.parent / f"externo_dir_{os.getpid()}"
        fuera.mkdir()
        enlace_dir = raiz / "src/roto"
        enlace_dir.symlink_to(fuera)
        meta = fuera / "meta.py"
        meta.write_text("antes", encoding="utf-8")
        try:
            with pytest.raises(RutaInseguraError):
                escribir_archivo_seguro("src/roto/meta.py", "modificado", raiz)
            assert meta.read_text(encoding="utf-8") == "antes"
        finally:
            meta.unlink(missing_ok=True)
            fuera.rmdir()


class TestToctou:
    def test_symlink_swapped_antes_de_apertura(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        destino = raiz / "src/victima.py"
        destino.write_text("antes", encoding="utf-8")
        fuera = tmp_path / "externo.py"
        fuera.write_text("fuera", encoding="utf-8")

        if not hasattr(os, "O_NOFOLLOW"):
            pytest.skip("O_NOFOLLOW no disponible")

        destino.unlink()
        destino.symlink_to(fuera)


class TestCallSites:
    def test_snapcontext_sobrescritura(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        archivo = raiz / "src/main.py"
        archivo.write_text("antes", encoding="utf-8")
        escribir_archivo_seguro("src/main.py", "despues", raiz.resolve())
        assert archivo.read_text(encoding="utf-8") == "despues"

    def test_agentes_rollback(self, tmp_path: Path):
        import agentes as ag

        raiz = _raiz_temporal(tmp_path)
        p = raiz / "f.py"
        p.write_text("nuevo", encoding="utf-8")
        snaps = [(str(p), b"orig", True)]
        ag.AgenteEditorPropio._rollback(snaps, raiz=raiz)
        assert p.read_bytes() == b"orig"

    def test_react_agent_tool_editar(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        archivo = raiz / "src/component.py"
        archivo.write_text("v1", encoding="utf-8")
        escribir_archivo_seguro("src/component.py", "v2", raiz.resolve())
        assert archivo.read_text(encoding="utf-8") == "v2"

    def test_autocorrector(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        archivo = raiz / "src/app.py"
        archivo.write_text("antes", encoding="utf-8")
        relativa = archivo.relative_to(raiz)
        escribir_archivo_seguro(str(relativa), "despues", raiz.resolve())
        assert archivo.read_text(encoding="utf-8") == "despues"


class TestExcepcion:
    def test_mensaje_contiene_motivo(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        lanzada = False
        msg = ""
        try:
            escribir_archivo_seguro("../escape", "x", raiz)
        except RutaInseguraError as e:
            lanzada = True
            msg = str(e).lower()
        assert lanzada, "No se lanzo RutaInseguraError"
        assert ".." in msg or "traversal" in msg

    def test_es_valor_error(self):
        assert issubclass(RutaInseguraError, ValueError)


class TestDirectorioPadre:
    def test_padre_inexistente(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        escribir_archivo_seguro("src/nuevo_dir/archivo.py", "x", raiz)
        assert (raiz / "src/nuevo_dir/archivo.py").read_text(encoding="utf-8") == "x"

    def test_raiz_inexistente(self, tmp_path: Path):
        raiz = tmp_path / "no_existe"
        with pytest.raises(RutaInseguraError):
            escribir_archivo_seguro("src/x.py", "x", raiz)

    def test_ruta_absoluta_fuera(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        with pytest.raises(RutaInseguraError):
            escribir_archivo_seguro("/etc/passwd", "x", raiz)

    def test_ruta_absoluta_dentro(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        escribir_archivo_seguro("abs.py", "ok", raiz)

    def test_path_con_parentesis_y_puntos(self, tmp_path: Path):
        raiz = _raiz_temporal(tmp_path)
        with pytest.raises(RutaInseguraError):
            escribir_archivo_seguro("src/../../../etc/passwd", "x", raiz)

