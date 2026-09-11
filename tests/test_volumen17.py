"""Tests Fase 1d: REPL _ejecutar_chat (chat interactivo)."""

from unittest import mock

import snapcontext as sc


def _entradas(*vals):
    return mock.patch("builtins.input", side_effect=list(vals))


class TestEjecutarChat:
    def test_salir_inmediato(self):
        with mock.patch.object(sc, "cargar_configuracion", return_value={}), _entradas("/salir"):
            assert sc._ejecutar_chat(proveedor="gemini") == 0

    def test_ayuda_y_salir(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
            _entradas("/ayuda", "/salir"),
            mock.patch.object(sc, "_emitir"),
        ):
            assert sc._ejecutar_chat(proveedor="gemini") == 0

    def test_limpiar_y_salir(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
            _entradas("/limpiar", "/salir"),
            mock.patch.object(sc, "_emitir"),
            mock.patch.object(sc, "exito"),
            mock.patch.object(sc, "info"),
        ):
            assert sc._ejecutar_chat(proveedor="gemini") == 0

    def test_eof_espera_hilos(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
            mock.patch("builtins.input", side_effect=EOFError()),
            mock.patch.object(sc, "info"),
            mock.patch.object(sc, "_emitir"),
        ):
            assert sc._ejecutar_chat(proveedor="gemini") == 0

    def test_consulta_normal(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
            _entradas("hola", "/salir"),
            mock.patch.object(sc, "_enviar_al_proveedor", return_value="respuesta"),
            mock.patch.object(sc, "_procesar_razonamiento", return_value=("respuesta", None)),
            mock.patch.object(sc, "_razonamiento_activo", return_value=False),
            mock.patch.object(sc, "_contexto_automatico_mcp", return_value=""),
            mock.patch.object(sc, "_emitir"),
            mock.patch.object(sc, "info"),
        ):
            assert sc._ejecutar_chat(proveedor="gemini") == 0

    def test_consulta_error_runtime(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
            _entradas("hola", "/salir"),
            mock.patch.object(sc, "_enviar_al_proveedor", side_effect=RuntimeError("boom")),
            mock.patch.object(sc, "_razonamiento_activo", return_value=False),
            mock.patch.object(sc, "_contexto_automatico_mcp", return_value=""),
            mock.patch.object(sc, "_emitir"),
            mock.patch.object(sc, "error"),
            mock.patch.object(sc, "info"),
        ):
            assert sc._ejecutar_chat(proveedor="gemini") == 0

    def test_sin_proveedor_fallback(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={}),
            mock.patch.dict("os.environ", {}, clear=True),
            _entradas("/salir"),
            mock.patch.object(sc, "_emitir"),
            mock.patch.object(sc, "aviso"),
            mock.patch.object(sc, "info"),
        ):
            assert sc._ejecutar_chat() == 0
