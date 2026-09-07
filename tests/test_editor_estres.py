"""Tests de estrés para el motor de edición (Fase 14)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import snapcontext as sc


def _generar_python_grande(num_clases: int = 10, metodos_por_clase: int = 5) -> str:
    """Genera un archivo Python sintácticamente válido."""
    lineas = ['"""Archivo de prueba."""', '', 'import os', '']
    for i in range(num_clases):
        lineas.append(f'class Clase{i}:')
        for j in range(metodos_por_clase):
            lineas.append(f'    def metodo_{i}_{j}(self) -> int:')
            lineas.append(f'        return {i} * {j}')
            lineas.append('')
    return '\n'.join(lineas)


def _generar_archivo_grande(num_lineas: int) -> str:
    lineas = []
    for i in range(num_lineas):
        lineas.append(f'valor_{i} = {i} * 2')
    return '\n'.join(lineas)


def _es_python_valido(codigo: str) -> bool:
    try:
        ast.parse(codigo)
        return True
    except SyntaxError:
        return False


class TestEditorEstresTamano:
    """Pruebas con archivos de diferentes tamaños."""

    @pytest.mark.parametrize("num_lineas", [1000, 5000])
    def test_archivo_mediano(self, tmp_path: Path, num_lineas: int):
        archivo = tmp_path / f"medio_{num_lineas}.py"
        archivo.write_text(_generar_archivo_grande(num_lineas), encoding="utf-8")

        resultado = sc.aplicar_reemplazo_estructurado(
            archivo=str(archivo),
            bloque_original="valor_500 = 500 * 2",
            bloque_nuevo="valor_500 = 999",
            directorio=str(tmp_path),
        )
        assert "valor_500 = 999" in resultado

    def test_archivo_10000_lineas(self, tmp_path: Path):
        archivo = tmp_path / "grande.py"
        archivo.write_text(_generar_archivo_grande(10000), encoding="utf-8")

        resultado = sc.aplicar_reemplazo_estructurado(
            archivo=str(archivo),
            bloque_original="valor_9999 = 9999 * 2",
            bloque_nuevo="valor_9999 = -1",
            directorio=str(tmp_path),
        )
        assert "valor_9999 = -1" in resultado



class TestEditorEstresMultiplesBloques:
    """Pruebas con múltiples bloques de búsqueda/reemplazo."""

    def test_multiples_reemplazos_secuenciales(self, tmp_path: Path):
        """Aplicar múltiples reemplazos secuencialmente sin corromper."""
        lineas = []
        for i in range(10):
            lineas.append(f'class Clase{i}:')
            lineas.append('    def metodo(self):')
            lineas.append(f'        return {i}')
            lineas.append('')
        archivo = tmp_path / "multiples.py"
        archivo.write_text('\n'.join(lineas), encoding="utf-8")

        for i in range(5):
            nuevo = sc.aplicar_reemplazo_estructurado(
                archivo=str(archivo),
                bloque_original=f'return {i}',
                bloque_nuevo=f'return {i} * 10',
                directorio=str(tmp_path),
            )
            archivo.write_text(nuevo, encoding="utf-8")

        final = archivo.read_text(encoding="utf-8")
        for i in range(5):
            assert f'return {i} * 10' in final

    def test_reemplazos_no_afectan_demas_codigo(self, tmp_path: Path):
        """Los reemplazos no modifican partes no objetivo."""
        original = '\n'.join([
            'import os', '', 'CONSTANTE = 42', '',
            'def foo():', '    return 1', '',
            'def bar():', '    return 2', '',
        ])
        archivo = tmp_path / "selectivo.py"
        archivo.write_text(original, encoding="utf-8")

        resultado = sc.aplicar_reemplazo_estructurado(
            archivo=str(archivo),
            bloque_original='def foo():\n    return 1',
            bloque_nuevo='def foo():\n    return 100',
            directorio=str(tmp_path),
        )

        assert 'CONSTANTE = 42' in resultado
        assert 'return 2' in resultado
        assert 'import os' in resultado


class TestEditorEstresPythonValido:
    """Pruebas que verifican validez del resultado."""

    def test_python_grande_parseable(self, tmp_path: Path):
        """Archivo Python grande modificado sigue siendo válido."""
        contenido = _generar_python_grande(num_clases=5, metodos_por_clase=3)
        assert _es_python_valido(contenido)

        archivo = tmp_path / "valido.py"
        archivo.write_text(contenido, encoding="utf-8")

        bloque_orig = 'def metodo_0_0(self) -> int:'
        bloque_nuevo = 'def metodo_0_0(self) -> str:'

        resultado = sc.aplicar_reemplazo_estructurado(
            archivo=str(archivo),
            bloque_original=bloque_orig,
            bloque_nuevo=bloque_nuevo,
            directorio=str(tmp_path),
        )

        assert _es_python_valido(resultado)


class TestEditorEstresRendimiento:
    """Pruebas de rendimiento básicas."""

    def test_rendimiento_archivo_mediano(self, tmp_path: Path):
        """Reemplazo en archivo de 5000 líneas toma menos de 2 segundos."""
        import time

        archivo = tmp_path / "rendimiento.py"
        archivo.write_text(_generar_archivo_grande(5000), encoding="utf-8")

        inicio = time.time()
        resultado = sc.aplicar_reemplazo_estructurado(
            archivo=str(archivo),
            bloque_original="valor_2500 = 2500 * 2",
            bloque_nuevo="valor_2500 = -1",
            directorio=str(tmp_path),
        )
        duracion = time.time() - inicio

        assert duracion < 2.0, f"Tomó {duracion:.2f}s"
        assert "valor_2500 = -1" in resultado

