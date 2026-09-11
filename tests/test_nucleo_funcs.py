"""Tests standalone snapcontext (Fase 1d)."""

import argparse
import json
from pathlib import Path
from unittest import mock

import snapcontext as sc


class TestDetectarTipoProyecto:
    def test_detecta_python(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pytest")
        assert sc._detectar_tipo_proyecto(str(tmp_path)) == "python"

    def test_detecta_flutter(self, tmp_path):
        (tmp_path / "pubspec.yaml").write_text("name: demo")
        assert sc._detectar_tipo_proyecto(str(tmp_path)) == "flutter"

    def test_detecta_node(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        assert sc._detectar_tipo_proyecto(str(tmp_path)) == "node"

    def test_sin_tipo(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hola")
        assert sc._detectar_tipo_proyecto(str(tmp_path)) is None

    def test_no_existe(self, tmp_path):
        assert sc._detectar_tipo_proyecto(str(tmp_path / "no_existe")) is None
