#!/usr/bin/env python3
"""Tests para el modo demo (--demo): onboarding zero-config.

Verifica que la demo se ejecuta sin errores, crea y limpia el proyecto
temporal, detecta Ollama correctamente y muestra los mensajes esperados.
Todos los tests usan mocks para no llamar a APIs reales.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo import (
    _crear_proyecto_ejemplo,
    _demo_consulta_simulada,
    _emitir_linea,
    _mostrar_funcionalidades,
    _mostrar_paso,
    _verificar_ollama,
    ejecutar_demo,
)


# --- Tests de creación de proyecto ------------------------------------------


def test_crear_proyecto_ejemplo_crea_archivos(tmp_path):
    """Verifica que se crean todos los archivos del proyecto de ejemplo."""
    archivos = _crear_proyecto_ejemplo(tmp_path)
    assert "src/main.py" in archivos
    assert "src/utils.py" in archivos
    assert "tests/test_main.py" in archivos
    assert "README.md" in archivos
    assert "requirements.txt" in archivos
    # Verificar que los archivos existen en disco.
    assert (tmp_path / "src" / "main.py").is_file()
    assert (tmp_path / "tests" / "test_main.py").is_file()


def test_crear_proyecto_ejemplo_contenido_valido(tmp_path):
    """Verifica que el contenido de main.py es correcto."""
    _crear_proyecto_ejemplo(tmp_path)
    main = (tmp_path / "src" / "main.py").read_text(encoding="utf-8")
    assert "def saludar" in main
    assert "def suma" in main
    assert "Hola, {nombre}" in main


# --- Tests de consulta simulada ---------------------------------------------


def test_demo_consulta_simulada_respuesta_valida():
    """La consulta simulada devuelve una respuesta predefinida."""
    resultado = _demo_consulta_simulada("¿Qué hace este proyecto?")
    assert resultado.get("ok") is True
    assert "saludar" in resultado.get("respuesta", "")
    assert "suma" in resultado.get("respuesta", "")


# --- Tests de detección de Ollama ------------------------------------------


def test_verificar_ollama_no_disponible():
    """Si Ollama no responde, devuelve False sin lanzar."""
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError()):
        assert _verificar_ollama() is False


def test_verificar_ollama_disponible():
    """Si Ollama responde 200, devuelve True."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert _verificar_ollama() is True


# --- Tests de la demo completa ----------------------------------------------


def test_ejecutar_demo_offline():
    """La demo se completa correctamente sin Ollama (modo offline)."""
    with patch("demo._verificar_ollama", return_value=False):
        resultado = ejecutar_demo()
    assert resultado == 0


def test_ejecutar_demo_con_ollama():
    """La demo detecta Ollama y usa consulta real (mock exitoso)."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"response": "Respuesta real de Ollama"}'
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    with patch("demo._verificar_ollama", return_value=True), \
         patch("urllib.request.urlopen", return_value=mock_resp):
        resultado = ejecutar_demo()
    assert resultado == 0


def test_ejecutar_demo_limpia_temporal():
    """El directorio temporal se elimina al finalizar la demo."""
    import tempfile
    with patch("demo._verificar_ollama", return_value=False):
        ejecutar_demo()
    # Verificar que no quedaron directorios temporales de la demo.
    temp_root = Path(tempfile.gettempdir())
    restos = list(temp_root.glob("snapcontext-demo-*"))
    assert len(restos) == 0


# --- Tests de funciones auxiliares ------------------------------------------


def test_emitir_linea(capsys):
    """_emitir_linea escribe en stdout con salto de línea."""
    _emitir_linea("hola")
    captured = capsys.readouterr()
    assert "hola" in captured.out


def test_mostrar_paso(capsys):
    """_mostrar_paso muestra el formato [n/total] mensaje."""
    _mostrar_paso(1, 4, "Probando paso")
    captured = capsys.readouterr()
    assert "[1/4]" in captured.out
    assert "Probando paso" in captured.out


def test_mostrar_funcionalidades(capsys):
    """_mostrar_funcionalidades muestra las funcionalidades clave."""
    _mostrar_funcionalidades()
    captured = capsys.readouterr()
    assert "Sandbox Docker" in captured.out
    assert "Multi-proveedor" in captured.out
    assert "Cliente MCP" in captured.out


# --- Import necesario para MagicMock ---------------------------------------


from unittest.mock import MagicMock
