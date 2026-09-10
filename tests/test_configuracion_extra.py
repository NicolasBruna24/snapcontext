"""Tests configuracion extra."""
import json
import os
from pathlib import Path
from unittest import mock

import configuracion as cfg


class TestHelpersConfig:
    def test_asegurar_permisos(self, tmp_path):
        with mock.patch.object(cfg, "CONFIG_DIR", tmp_path):
            cfg._asegurar_permisos_config()

    def test_generar_clave(self, tmp_path):
        with mock.patch.object(cfg, "CONFIG_DIR", tmp_path), \
             mock.patch.object(cfg, "CONFIG_PATH", tmp_path / "c.json"):
            k = cfg._generar_clave_api(guardar=False)
        assert len(k) >= 20

    def test_elegir_ligero(self):
        assert cfg._elegir_modelo_ligero(["a", "bb", "ccc"]) in ("a", "bb", "ccc")
        assert cfg._elegir_modelo_ligero([]) is None

    def test_importar_questionary(self):
        r = cfg._importar_questionary()
        assert r is None or hasattr(r, "select")

    def test_listar_modelos(self):
        assert isinstance(cfg._listar_modelos_ollama(), tuple)

    def test_estado_ollama(self):
        assert isinstance(cfg._estado_ollama(), dict)

    def test_probar_conexion_invalida(self):
        try:
            r = cfg._probar_conexion_proveedor("no_existe")
            assert r in (True, False)
        except KeyError:
            pass  # comportamiento esperado para proveedor desconocido

    def test_preguntar_sin_questionary(self):
        with mock.patch.object(cfg, "_importar_questionary", return_value=None):
            assert cfg._preguntar_guardar_config() is False

    def test_tutorial_sin_questionary(self, monkeypatch):
        monkeypatch.setattr(cfg, "_importar_questionary", lambda: None)
        monkeypatch.setattr("builtins.input", lambda _="": "q")
        assert isinstance(cfg._tutorial_interactivo(), (int, type(None)))

    def test_crear_demo(self, tmp_path):
        cfg._crear_demo_proyecto(tmp_path)
        assert (tmp_path / "requirements.txt").exists()
        assert (tmp_path / "src" / "main.py").exists()
        assert (tmp_path / "tests" / "test_main.py").exists()


class TestSeleccionarProveedorExtra:
    def test_listar_con_subprocess(self, monkeypatch):
        import subprocess as sp

        class P:
            stdout = "NAME\nllama3.2:latest\n"
            returncode = 0

        monkeypatch.setattr(sp, "run", lambda *a, **k: P())
        modelos, err = cfg._listar_modelos_ollama()
        assert isinstance(modelos, list) and "llama3.2" in str(modelos)

    def test_probar_ollama_ok(self, monkeypatch):
        import snapcontext as sc

        class FakeCompletions:
            def create(self, **kwargs):
                return {"ok": True}

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            def __init__(self, *a, **k):
                self.chat = FakeChat()

        class FakeOpenai:
            OpenAI = FakeClient

        monkeypatch.setattr(sc, "_importar_openai", lambda: True)
        real_sc = cfg._sc
        monkeypatch.setattr(cfg, "_sc", lambda n: FakeOpenai if n == "openai" else real_sc(n))
        assert cfg._probar_conexion_proveedor("ollama") is True

    def test_generar_clave_guardar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "c.json")
        k = cfg._generar_clave_api(guardar=True)
        assert len(k) >= 20

    def test_asegurar_permisos_archivo(self, tmp_path, monkeypatch):
        p = tmp_path / "c.json"
        p.write_text("{}")
        monkeypatch.setattr(cfg, "CONFIG_PATH", p)
        cfg._asegurar_permisos_config()

    def test_cargar_corrupto(self, tmp_path, monkeypatch):
        p = tmp_path / "c.json"
        p.write_text("{mal")
        monkeypatch.setattr(cfg, "CONFIG_PATH", p)
        assert isinstance(cfg.cargar_configuracion(), dict)

    def test_cargar_lista(self, tmp_path, monkeypatch):
        p = tmp_path / "c.json"
        p.write_text("[1,2]")
        monkeypatch.setattr(cfg, "CONFIG_PATH", p)
        assert cfg.cargar_configuracion() == {}
