"""Fixtures compartidas para los tests del nucleo de SnapContext."""

from __future__ import annotations

import argparse
import types
from unittest import mock

import pytest


def _args(**overrides: object) -> argparse.Namespace:
    """Construye un Namespace con valores por defecto razonables para tests."""
    base = dict(
        consulta=None,
        directorio=".",
        proveedor=None,
        modelo=None,
        depurar=False,
        confirmar=True,
        auto=False,
        local=False,
        sandbox=False,
        lsp=False,
        web=False,
        tui=False,
        api=False,
        benchmark=False,
        diagnostico=False,
        reparacion=False,
        demo=False,
        init=False,
        version=False,
        ayuda=False,
        max_tokens=None,
        temperature=None,
        timeout=None,
        idioma="es",
        streams=1,
        paralelo=False,
        grafos=False,
        hooks=True,
        habilidades=True,
        verbose=False,
        quiet=False,
        accion=None,
        nombre=None,
        url=None,
        token=None,
        descripcion=None,
        estado=False,
        forzar=False,
        mensaje=None,
        archivo=None,
        hook=None,
        comando=None,
        subcomando=None,
        plugin=None,
        config=None,
        clave=None,
        valor=None,
        modulo=None,
        destino=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def args_base() -> argparse.Namespace:
    """Args minimo para invocar funciones del nucleo."""
    return _args()


@pytest.fixture
def subprocess_mockeado():
    """Mockea subprocess.run para evitar ejecutar comandos reales."""
    with mock.patch("subprocess.run") as run_mock:
        run_mock.return_value = types.CompletedProcess(
            args=["fake"],
            returncode=0,
            stdout="ok",
            stderr="",
        )
        yield run_mock
