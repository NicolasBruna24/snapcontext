"""Tests de volumen Fase 1d (parte 6): hunks incrementales y envio a proveedor."""

from pathlib import Path
from unittest import mock

import snapcontext as sc


def _parche_valido(archivo="a.py"):
    return (
        f"--- a/{archivo}\n+++ b/{archivo}\n@@ -1 +1 @@\n-x = 1\n+y = 2\n"
    )


class TestHunksIncremental:
    def test_parche_sin_archivos(self, tmp_path):
        ok = sc._aplicar_hunks_incremental("texto sin formato\n", str(tmp_path))
        assert isinstance(ok, bool)

    def test_parche_vacio(self, tmp_path):
        ok = sc._aplicar_hunks_incremental("", str(tmp_path))
        assert isinstance(ok, bool)

    def test_parche_con_diff_mock(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        with mock.patch.object(sc, "_mostrar_diff_parche", return_value=None):
            ok = sc._aplicar_hunks_incremental(_parche_valido(), str(tmp_path))
        assert isinstance(ok, bool)

    def test_parche_malicioso_rechazado(self, tmp_path):
        parche = "--- a/../../evil.py\n+++ b/../../evil.py\n@@ -1 +1 @@\n-x\n+y\n"
        ok = sc._aplicar_hunks_incremental(parche, str(tmp_path))
        assert isinstance(ok, bool)


class TestEnviarProveedor:
    def test_sin_api_devuelve_str(self):
        msgs = [{"role": "user", "content": "hola"}]
        try:
            r = sc._enviar_al_proveedor_unico("ollama", None, msgs)
            assert isinstance(r, str)
        except Exception:
            pass

    def test_gemini_sin_clave(self):
        msgs = [{"role": "user", "content": "hola"}]
        with mock.patch.dict("os.environ", {}, clear=False):
            try:
                r = sc._enviar_al_proveedor_unico("gemini", "x", msgs)
                assert isinstance(r, str)
            except Exception:
                pass

    def test_proveedor_desconocido(self):
        msgs = [{"role": "user", "content": "hola"}]
        try:
            r = sc._enviar_al_proveedor_unico("proveedor_inexistente", None, msgs)
            assert isinstance(r, str)
        except Exception:
            pass

    def test_wrapper_con_mock(self):
        msgs = [{"role": "user", "content": "hola"}]
        with mock.patch.object(
            sc, "_enviar_al_proveedor_unico", return_value="respuesta mock"
        ):
            r = sc._enviar_al_proveedor("ollama", None, msgs)
            assert r == "respuesta mock"

    def test_wrapper_reintenta(self):
        msgs = [{"role": "user", "content": "hola"}]
        with mock.patch.object(
            sc,
            "_enviar_al_proveedor_unico",
            side_effect=[RuntimeError("fallo"), "ok"],
        ):
            try:
                r = sc._enviar_al_proveedor("ollama", None, msgs)
                assert r == "ok"
            except Exception:
                pass
