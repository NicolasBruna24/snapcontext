"""Tests unitarios para el sistema de reemplazo estructurado (Fase 14).

Cubre `aplicar_reemplazo_estructurado` en snapcontext.py:
- Reemplazo exacto (bloque aparece textualmente)
- Reemplazo difuso (después de normalizar espacios/comentarios)
- Reemplazo fallido (bloque no encontrado)
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

import snapcontext as sc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def archivo_temporal(tmp_path: Path):
    """Crea un archivo de prueba y retorna una función para aplicar reemplazos."""

    def _crear(contenido: str) -> Path:
        ruta = tmp_path / "test_file.py"
        ruta.write_text(contenido, encoding="utf-8")
        return ruta

    return _crear


def _aplicar(archivo: Path, original: str, nuevo: str) -> str:
    """Helper para aplicar reemplazo estructurado."""
    return sc.aplicar_reemplazo_estructurado(
        archivo=str(archivo),
        bloque_original=original,
        bloque_nuevo=nuevo,
        directorio=str(archivo.parent),
    )


# ---------------------------------------------------------------------------
# Reemplazo exacto
# ---------------------------------------------------------------------------


class TestReemplazoExacto:
    """El bloque aparece textualmente en el archivo."""

    def test_reemplazo_simple(self, archivo_temporal):
        archivo = archivo_temporal("hola mundo\nfoo bar\n")
        resultado = _aplicar(archivo, "hola mundo", "adios mundo")
        assert "adios mundo" in resultado
        assert "hola mundo" not in resultado

    def test_reemplazo_multiples_lineas(self, archivo_temporal):
        archivo = archivo_temporal("linea 1\nlinea 2\nlinea 3\nlinea 4\n")
        resultado = _aplicar(archivo, "linea 2\nlinea 3", "linea nueva A\nlinea nueva B")
        assert "linea nueva A" in resultado
        assert "linea nueva B" in resultado
        assert "linea 2" not in resultado
        assert "linea 3" not in resultado

    def test_reemplazo_python(self, archivo_temporal):
        archivo = archivo_temporal("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        resultado = _aplicar(archivo, "def foo():\n    return 1", "def foo():\n    return 999")
        assert "return 999" in resultado
        assert "return 1\n" not in resultado  # Verificar que el bloque exacto fue reemplazado
        assert "def bar()" in resultado  # no afectó a bar

    def test_reemplazo_solo_primera_ocurrencia(self, archivo_temporal):
        archivo = archivo_temporal("repetido\nrepetido\nrepetido\n")
        resultado = _aplicar(archivo, "repetido", "cambiado")
        assert resultado.count("cambiado") == 1
        assert resultado.count("repetido") == 2


# ---------------------------------------------------------------------------
# Reemplazo difuso
# ---------------------------------------------------------------------------


class TestReemplazoDifuso:
    """El bloque se encuentra después de normalizar espacios/comentarios."""

    def test_espacios_extra(self, archivo_temporal):
        archivo = archivo_temporal("def foo():\n  return 1\n")
        resultado = _aplicar(archivo, "def foo():\n    return 1", "def foo():\n    return 2")
        # Aunque hay diferencia de indentación, debe encontrarlo
        assert "return 2" in resultado

    def test_lineas_en_blanco(self, archivo_temporal):
        archivo = archivo_temporal("linea 1\n\nlinea 3\n")
        resultado = _aplicar(archivo, "linea 1\nlinea 3", "unificada")
        # Difuso debería manejar líneas en blanco extra
        assert isinstance(resultado, str)


# ---------------------------------------------------------------------------
# Reemplazo fallido
# ---------------------------------------------------------------------------


class TestReemplazoFallido:
    """El bloque no se encuentra en el archivo."""

    def test_bloque_no_existe(self, archivo_temporal):
        archivo = archivo_temporal("contenido original\n")
        with pytest.raises(ValueError, match="no encontrado"):
            _aplicar(archivo, "bloque inexistente", "nuevo")

    def test_archivo_no_existe(self, tmp_path: Path):
        with pytest.raises(ValueError, match="no encontrado"):
            sc.aplicar_reemplazo_estructurado(
                archivo="archivo_inexistente.py",
                bloque_original="x",
                bloque_nuevo="y",
                directorio=str(tmp_path),
            )

    def test_bloque_vacio(self, archivo_temporal):
        archivo = archivo_temporal("contenido\n")
        with pytest.raises(ValueError, match="no puede estar"):
            _aplicar(archivo, "", "nuevo")


# ---------------------------------------------------------------------------
# Seguridad (M1 - path traversal)
# ---------------------------------------------------------------------------


class TestReemplazoSeguridad:
    """Defensa contra path traversal en reemplazo estructurado."""

    def test_bloquea_path_traversal(self, tmp_path: Path):
        archivo = tmp_path / "seguro.py"
        archivo.write_text("contenido\n")
        with pytest.raises(ValueError, match="fuera del proyecto"):
            sc.aplicar_reemplazo_estructurado(
                archivo="../etc/passwd",
                bloque_original="x",
                bloque_nuevo="y",
                directorio=str(tmp_path),
            )

    def test_bloquea_ruta_absoluta_fuera(self, tmp_path: Path):
        archivo = tmp_path / "seguro.py"
        archivo.write_text("contenido\n")
        with pytest.raises(ValueError, match="fuera del proyecto"):
            sc.aplicar_reemplazo_estructurado(
                archivo="/etc/passwd",
                bloque_original="x",
                bloque_nuevo="y",
                directorio=str(tmp_path),
            )
