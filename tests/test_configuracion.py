"""Tests para configuracion.py."""
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import configuracion as cfg


class TestCargarConfiguracion:
    def test_devuelve_dict_aunque_no_existe(self, tmp_path):
        with mock.patch.object(cfg, "CONFIG_PATH", tmp_path / "config.json"):
            resultado = cfg.cargar_configuracion()
        assert isinstance(resultado, dict)

    def test_carga_archivo_existente(self, tmp_path):
        data = {"provider": "ollama", "custom": "valor"}
        ruta = tmp_path / "config.json"
        ruta.write_text(json.dumps(data))
        with mock.patch.object(cfg, "CONFIG_PATH", ruta):
            resultado = cfg.cargar_configuracion()
        assert resultado.get("provider") == "ollama"
        assert resultado.get("custom") == "valor"

    def test_archivo_corrupto_devuelve_vacio(self, tmp_path):
        ruta = tmp_path / "config.json"
        ruta.write_text("{esto no es json")
        with mock.patch.object(cfg, "CONFIG_PATH", ruta):
            resultado = cfg.cargar_configuracion()
        assert isinstance(resultado, dict)


class TestGuardarConfiguracion:
    def test_guarda_y_recupera(self, tmp_path):
        ruta = tmp_path / "config.json"
        with mock.patch.object(cfg, "CONFIG_DIR", tmp_path), \
             mock.patch.object(cfg, "CONFIG_PATH", ruta):
            ok = cfg.guardar_configuracion("gemini", "flash")
        assert ok is True
        assert ruta.exists()
        data = json.loads(ruta.read_text())
        assert data["provider"] == "gemini"

    def test_falla_si_no_puede_crear_directorio(self, tmp_path, monkeypatch):
        import pathlib
        monkeypatch.setattr(pathlib.Path, "mkdir", lambda self, *a, **k: (_ for _ in ()).throw(OSError("denegado")))
        ok = cfg.guardar_configuracion("x")
        assert ok is False
        assert ok is False


class TestActualizarClave:
    def test_actualiza_clave_existente(self, tmp_path):
        ruta = tmp_path / "config.json"
        ruta.write_text(json.dumps({"provider": "gemini"}))
        with mock.patch.object(cfg, "CONFIG_DIR", tmp_path), \
             mock.patch.object(cfg, "CONFIG_PATH", ruta):
            ok = cfg._actualizar_clave_configuracion("provider", "ollama")
        assert ok is True
        assert json.loads(ruta.read_text())["provider"] == "ollama"


class TestGenerarClave:
    def test_genera_y_guarda(self, tmp_path):
        ruta = tmp_path / "config.json"
        with mock.patch.object(cfg, "CONFIG_DIR", tmp_path), \
             mock.patch.object(cfg, "CONFIG_PATH", ruta):
            clave = cfg._generar_clave_api(guardar=True)
        assert isinstance(clave, str)
        assert len(clave) > 10


class TestProveedores:
    def test_proveedores_definidos(self):
        assert "gemini" in cfg.PROVEEDORES
        assert "ollama" in cfg.PROVEEDORES
        assert "anthropic" in cfg.PROVEEDORES

    def test_proveedor_gemini_tiene_clave(self):
        gemini = cfg.PROVEEDORES["gemini"]
        assert gemini["requiere_clave"] is True
        assert gemini["clave_env"] == "GEMINI_API_KEY"

    def test_proveedor_ollama_sin_clave(self):
        ollama = cfg.PROVEEDORES["ollama"]
        assert ollama["requiere_clave"] is False


class TestHayApiKey:
    def test_detecta_en_config(self, tmp_path):
        ruta = tmp_path / "config.json"
        ruta.write_text(json.dumps({"api_keys": {"gemini": "abc12345"}}))
        with mock.patch.object(cfg, "CONFIG_PATH", ruta):
            assert cfg.hay_api_key_configurada() is True

    def test_detecta_en_entorno(self, tmp_path):
        ruta = tmp_path / "config.json"
        ruta.write_text("{}")
        with mock.patch.object(cfg, "CONFIG_PATH", ruta), \
             mock.patch.dict(os.environ, {"GEMINI_API_KEY": "abc"}):
            assert cfg.hay_api_key_configurada() is True

    def test_no_hay_clave(self, tmp_path):
        ruta = tmp_path / "config.json"
        ruta.write_text("{}")
        env_limpio = {k: v for k, v in os.environ.items()
                      if not k.endswith("_API_KEY")}
        with mock.patch.object(cfg, "CONFIG_PATH", ruta), \
             mock.patch.dict(os.environ, env_limpio, clear=True):
            assert cfg.hay_api_key_configurada() is False


class TestElegirModeloLigero:
    def test_con_modelos(self):
        resultado = cfg._elegir_modelo_ligero(["llama3.2", "gpt-4o-mini", "gemini-flash"])
        assert resultado is not None
        assert isinstance(resultado, str)

    def test_lista_vacia(self):
        assert cfg._elegir_modelo_ligero([]) is None


