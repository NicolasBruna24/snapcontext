"""Smoke tests mínimos para CI (verifican que el paquete importa y el CLI responde).

Diseñados para consumir poca memoria: los runners gratuitos de GitHub Actions
(~7 GB) mueren con el OOM killer si se ejecuta la suite completa, que carga
dependencias pesadas (textual, playwright, tree-sitter, torch, etc.).
"""

import subprocess
import sys

import pytest


def test_import_snapcontext():
    import snapcontext as sc

    assert sc.VERSION is not None


def test_version_formato():
    import snapcontext as sc

    partes = sc.VERSION.split(".")
    assert len(partes) == 3, f"VERSION inesperada: {sc.VERSION}"
    for p in partes:
        assert p.isdigit(), f"VERSION con segmento no numérico: {sc.VERSION}"


@pytest.mark.skipif(sys.version_info < (3, 10), reason="requiere Python >= 3.10")
def test_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "snapcontext", "--help"],
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    # Decodificación tolerante: la ayuda contiene Unicode (—, ↔) y el locale
    # del runner puede no ser UTF-8.
    salida = result.stdout.decode("utf-8", errors="replace") + result.stderr.decode(
        "utf-8", errors="replace"
    )
    # El CLI imprime su ayuda personalizada en español ("Uso:") en lugar
    # del "usage:" estándar de argparse.
    assert "Uso:" in salida or "usage:" in salida.lower()
