"""Tests Fase 1d: _ejecutar_comando, _daemon_tick, curador, gateways,
modo_experto, _plugin_instalar."""

import argparse
import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import snapcontext as sc


def _proc(code=0, out="", err=""):
    p = mock.MagicMock()
    p.returncode = code
    p.stdout = out
    p.stderr = err
    return p


class TestEjecutarComando:
    def test_directo_ok(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(sc, "_ejecutar_con_politica", return_value=_proc(0, "hola", "")),
        ):
            code, out, err = sc._ejecutar_comando("echo hola", str(tmp_path))
        assert (code, out, err) == (0, "hola", "")

    def test_directo_sin_captura(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(sc, "_ejecutar_con_politica", return_value=_proc(3)),
        ):
            code, out, err = sc._ejecutar_comando("x", str(tmp_path), capture_output=False)
        assert (code, out, err) == (3, "", "")

    def test_directorio_invalido(self):
        code, out, err = sc._ejecutar_comando("x", "/no/existe/xyz")
        assert code == -1
        assert "no existe" in err

    def test_sandbox_abortar(self, tmp_path):
        with mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_ABORTAR):
            code, out, err = sc._ejecutar_comando("x", str(tmp_path))
        assert code == -1
        assert "abortado" in err

    def test_sandbox_contenedor(self, tmp_path):
        with (
            mock.patch.object(
                sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_CONTENEDOR
            ),
            mock.patch.object(sc, "_SESION_DOCKER_SOLICITADA", False),
            mock.patch.object(sc, "_envolver_sandbox", side_effect=lambda c, d: c),
            mock.patch.object(sc, "_ejecutar_con_politica", return_value=_proc(0, "ok", "")),
        ):
            code, out, err = sc._ejecutar_comando("x", str(tmp_path))
        assert code == 0

    def test_sandbox_con_sesion(self, tmp_path):
        fake_ss = types.SimpleNamespace(comando_en_sesion=lambda c: "sesion:" + c)
        with (
            mock.patch.object(
                sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_CONTENEDOR
            ),
            mock.patch.object(sc, "_SESION_DOCKER_SOLICITADA", True),
            mock.patch.dict(sys.modules, {"sandbox_session": fake_ss}),
            mock.patch.object(sc, "_asegurar_sesion_docker", return_value=True),
            mock.patch.object(sc, "_ejecutar_con_politica", return_value=_proc(0, "ok", "")),
        ):
            code, out, err = sc._ejecutar_comando("x", str(tmp_path))
        assert code == 0

    def test_sesion_falla_cae_a_contenedor(self, tmp_path):
        fake_ss = types.SimpleNamespace(comando_en_sesion=lambda c: c)
        with (
            mock.patch.object(
                sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_CONTENEDOR
            ),
            mock.patch.object(sc, "_SESION_DOCKER_SOLICITADA", True),
            mock.patch.dict(sys.modules, {"sandbox_session": fake_ss}),
            mock.patch.object(sc, "_asegurar_sesion_docker", return_value=False),
            mock.patch.object(sc, "_envolver_sandbox", side_effect=lambda c, d: c),
            mock.patch.object(sc, "_ejecutar_con_politica", return_value=_proc(0, "ok", "")),
        ):
            code, _, _ = sc._ejecutar_comando("x", str(tmp_path))
        assert code == 0

    def test_peligroso_auto(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(sc, "_es_comando_peligroso", return_value=True),
            mock.patch.object(sc, "_ui_es_auto", return_value=True),
        ):
            code, out, err = sc._ejecutar_comando("rm -rf /", str(tmp_path))
        assert code == -1
        assert "abortado" in err

    def test_peligroso_usuario_rechaza(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(sc, "_es_comando_peligroso", return_value=True),
            mock.patch.object(sc, "_ui_es_auto", return_value=False),
            mock.patch.object(sc, "_entrada_interactiva", return_value=True),
            mock.patch.object(sc, "_preguntar_si", return_value=False),
        ):
            code, out, err = sc._ejecutar_comando("rm -rf /", str(tmp_path))
        assert code == -1
        assert "rechazado" in err

    def test_peligroso_usuario_acepta(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(sc, "_es_comando_peligroso", return_value=True),
            mock.patch.object(sc, "_ui_es_auto", return_value=False),
            mock.patch.object(sc, "_entrada_interactiva", return_value=True),
            mock.patch.object(sc, "_preguntar_si", return_value=True),
            mock.patch.object(sc, "_ejecutar_con_politica", return_value=_proc(0, "ok", "")),
        ):
            code, _, _ = sc._ejecutar_comando("rm -rf /", str(tmp_path))
        assert code == 0

    def test_runtime_error(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(sc, "_ejecutar_con_politica", side_effect=RuntimeError("bloqueado")),
        ):
            code, out, err = sc._ejecutar_comando("x", str(tmp_path))
        assert code == -1 and "bloqueado" in err

    def test_timeout(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(
                sc, "_ejecutar_con_politica", side_effect=subprocess.TimeoutExpired("x", 1)
            ),
        ):
            code, out, err = sc._ejecutar_comando("x", str(tmp_path), timeout=1)
        assert code == -1 and "timeout" in err

    def test_oserror(self, tmp_path):
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_DIRECTO),
            mock.patch.object(sc, "_ejecutar_con_politica", side_effect=OSError("boom")),
        ):
            code, out, err = sc._ejecutar_comando("x", str(tmp_path))
        assert code == -1 and "boom" in err


class TestDaemonTick:
    def test_sin_pendientes(self):
        with (
            mock.patch.object(sc, "_db_init"),
            mock.patch.object(sc, "_kv_obtener", return_value=""),
            mock.patch.object(sc, "_db_query", return_value=[]),
            mock.patch.object(sc, "_curador_ejecutar") as cur,
        ):
            res = sc._daemon_tick(ahora=__import__("datetime").datetime.now())
        assert res["curador"] is True
        assert cur.called

    def test_no_vencido(self):
        import datetime as dt

        ahora = dt.datetime.now()
        with (
            mock.patch.object(sc, "_db_init"),
            mock.patch.object(sc, "_kv_obtener", return_value=ahora.isoformat()),
            mock.patch.object(sc, "_db_query", return_value=[]),
            mock.patch.object(sc, "_curador_ejecutar") as cur,
        ):
            res = sc._daemon_tick(intervalo_horas=10, ahora=ahora)
        assert res["curador"] is False
        assert not cur.called

    def test_ultima_invalida(self):
        with (
            mock.patch.object(sc, "_db_init"),
            mock.patch.object(sc, "_kv_obtener", return_value="no-fecha"),
            mock.patch.object(sc, "_db_query", return_value=[]),
            mock.patch.object(sc, "_curador_ejecutar"),
        ):
            res = sc._daemon_tick()
        assert res["curador"] is True

    def test_procesa_pendientes(self):
        tarea = {"id": 1, "skill_id": 7}
        skill = {"id": 7, "nombre": "s", "pasos": [], "archivado": False}
        with (
            mock.patch.object(sc, "_db_init"),
            mock.patch.object(sc, "_kv_obtener", return_value=""),
            mock.patch.object(sc, "_db_query", return_value=[tarea]),
            mock.patch.object(sc, "_db_ejecutar") as db_e,
            mock.patch.object(sc, "_skill_obtener", return_value=skill),
            mock.patch.object(sc, "_curador_ejecutar"),
        ):
            res = sc._daemon_tick()
        assert res["procesados"] == [7]
        assert db_e.call_count >= 2

    def test_skill_archivado_se_descarta(self):
        tarea = {"id": 2, "skill_id": 8}
        with (
            mock.patch.object(sc, "_db_init"),
            mock.patch.object(sc, "_kv_obtener", return_value=""),
            mock.patch.object(sc, "_db_query", return_value=[tarea]),
            mock.patch.object(sc, "_db_ejecutar") as db_e,
            mock.patch.object(sc, "_skill_obtener", return_value=None),
            mock.patch.object(sc, "_curador_ejecutar"),
        ):
            res = sc._daemon_tick()
        assert res["procesados"] == []
        assert any("descartado" in str(c.args[0]) for c in db_e.call_args_list)


class TestComandoCurador:
    def test_help(self):
        assert sc._ejecutar_comando_curador(["--help"]) == 0

    def test_estado(self):
        resumen = {
            "activo": True,
            "intervalo_horas": 6,
            "total_skills": 3,
            "activos": 2,
            "candidatos": 1,
            "ultima_pasada": None,
            "reinado_lista": [],
        }
        fake_cp = types.SimpleNamespace(estado_curador=lambda: resumen)
        with mock.patch.dict(sys.modules, {"curador_proactivo": fake_cp}):
            assert sc._ejecutar_comando_curador(["estado"]) == 0

    def test_ejecutar_none(self):
        fake_cp = types.SimpleNamespace(ejecutar_curador=lambda: None)
        with mock.patch.dict(sys.modules, {"curador_proactivo": fake_cp}):
            assert sc._ejecutar_comando_curador(["ejecutar"]) == 0

    def test_ejecutar_resultados(self):
        fake_cp = types.SimpleNamespace(
            ejecutar_curador=lambda: [{"mejorado": True}, {"mejorado": False}]
        )
        with mock.patch.dict(sys.modules, {"curador_proactivo": fake_cp}):
            assert sc._ejecutar_comando_curador(["ejecutar"]) == 0

    def test_activar_desactivar(self):
        fake_cp = types.SimpleNamespace(
            activar_curador=lambda: None, desactivar_curador=lambda: None
        )
        with mock.patch.dict(sys.modules, {"curador_proactivo": fake_cp}):
            assert sc._ejecutar_comando_curador(["activar"]) == 0
            assert sc._ejecutar_comando_curador(["desactivar"]) == 0

    def test_import_error(self):
        with mock.patch.dict(sys.modules, {"curador_proactivo": None}):
            assert sc._ejecutar_comando_curador(["estado"]) == 1


class TestComandoTelegram:
    def test_help(self):
        assert sc._ejecutar_comando_telegram([]) == 0

    def test_estado(self):
        fake_tg = types.SimpleNamespace(
            obtener_token=lambda: "1234567890", obtener_webhook_url=lambda: "https://x"
        )
        with mock.patch.dict(sys.modules, {"telegram_gateway": fake_tg}):
            assert sc._ejecutar_comando_telegram(["estado"]) == 0

    def test_estado_sin_token(self):
        fake_tg = types.SimpleNamespace(
            obtener_token=lambda: None, obtener_webhook_url=lambda: None
        )
        with mock.patch.dict(sys.modules, {"telegram_gateway": fake_tg}):
            assert sc._ejecutar_comando_telegram(["estado"]) == 1

    def test_setup(self):
        fake_tg = types.SimpleNamespace(
            guardar_configuracion_telegram=lambda t, u: {
                "bot_token": "1234567890",
                "webhook_url": "https://x",
            },
            registrar_webhook=lambda: (True, "ok"),
        )
        with mock.patch.dict(sys.modules, {"telegram_gateway": fake_tg}):
            assert sc._ejecutar_comando_telegram(["setup", "--token", "1234567890"]) == 0

    def test_webhook_registrar(self):
        fake_tg = types.SimpleNamespace(registrar_webhook=lambda: (False, "sin url"))
        with mock.patch.dict(sys.modules, {"telegram_gateway": fake_tg}):
            assert sc._ejecutar_comando_telegram(["webhook-registrar"]) == 1

    def test_import_error(self):
        with mock.patch.dict(sys.modules, {"telegram_gateway": None}):
            assert sc._ejecutar_comando_telegram(["estado"]) == 1


class TestComandoDiscord:
    def test_help(self):
        assert sc._ejecutar_comando_discord([]) == 0

    def test_estado(self):
        fake_dg = types.SimpleNamespace(
            obtener_public_key=lambda: "abcd1234",
            obtener_application_id=lambda: "42",
            obtener_bot_token=lambda: "tok12345",
            obtener_webhook_url=lambda: "https://x",
        )
        with mock.patch.dict(sys.modules, {"discord_gateway": fake_dg}):
            assert sc._ejecutar_comando_discord(["estado"]) == 0

    def test_estado_sin_key(self):
        fake_dg = types.SimpleNamespace(
            obtener_public_key=lambda: None,
            obtener_application_id=lambda: None,
            obtener_bot_token=lambda: None,
            obtener_webhook_url=lambda: None,
        )
        with mock.patch.dict(sys.modules, {"discord_gateway": fake_dg}):
            assert sc._ejecutar_comando_discord(["estado"]) == 1

    def test_setup(self):
        fake_dg = types.SimpleNamespace(
            guardar_configuracion_discord=lambda pk, ai, tk, wu: {
                "public_key": "abcd",
                "application_id": "1",
                "bot_token": "tok",
                "webhook_url": "https://x",
            },
        )
        with mock.patch.dict(sys.modules, {"discord_gateway": fake_dg}):
            assert sc._ejecutar_comando_discord(["setup", "--token", "t"]) == 0

    def test_import_error(self):
        with mock.patch.dict(sys.modules, {"discord_gateway": None}):
            assert sc._ejecutar_comando_discord(["estado"]) == 1


class TestModoExperto:
    def test_continuar(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "c")
        res = sc.modo_experto(["a.py"], Path("."))
        assert res == ["a.py"]

    def test_continuar_vacia_agrega_primero(self, monkeypatch):
        # Con la lista vacía no se puede continuar: se agrega un archivo y luego 'c'.
        entradas = iter(["a", "c"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        with mock.patch.object(sc, "_pedir_archivo_para_agregar", return_value="nuevo.py"):
            res = sc.modo_experto([], Path("."))
        assert res == ["nuevo.py"]

    def test_agregar_sin_ruta_valida(self, monkeypatch):
        entradas = iter(["a", "c"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        with mock.patch.object(sc, "_pedir_archivo_para_agregar", return_value=None):
            res = sc.modo_experto(["a.py"], Path("."))
        assert res == ["a.py"]

    def test_eliminar(self, monkeypatch):
        entradas = iter(["e", "c"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        with mock.patch.object(sc, "_eliminar_por_indice", return_value=["b.py"]):
            res = sc.modo_experto(["a.py", "b.py"], Path("."))
        assert res == ["b.py"]

    def test_limpiar_confirmado(self, monkeypatch):
        # Tras vaciar, no se puede continuar: se agrega y luego 'c'.
        entradas = iter(["l", "a", "c"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        with (
            mock.patch.object(sc, "_preguntar_si", return_value=True),
            mock.patch.object(sc, "_pedir_archivo_para_agregar", return_value="n.py"),
        ):
            res = sc.modo_experto(["a.py"], Path("."))
        assert res == ["n.py"]

    def test_limpiar_rechazado(self, monkeypatch):
        entradas = iter(["l", "c"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        with mock.patch.object(sc, "_preguntar_si", return_value=False):
            res = sc.modo_experto(["a.py"], Path("."))
        assert res == ["a.py"]

    def test_opcion_invalida(self, monkeypatch):
        entradas = iter(["zz", "c"])
        monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
        res = sc.modo_experto(["a.py"], Path("."))
        assert res == ["a.py"]


class TestPluginInstalar:
    def test_instalar_local(self, tmp_path):
        origen = tmp_path / "mi_plugin"
        origen.mkdir()
        (origen / "plugin.json").write_text(
            '{"nombre": "mi_plugin", "version": "1.0.0", '
            '"autor": "yo", "permisos": [], "herramientas": [{"nombre": "h"}]}'
        )
        destino_raiz = tmp_path / "plugins"
        destino_raiz.mkdir()
        with (
            mock.patch.object(sc, "_plugins_directorio", return_value=destino_raiz),
            mock.patch.object(sc, "_confirmar_accion", return_value=True),
        ):
            code = sc._plugin_instalar(str(origen), confirmar=False)
        assert code == 0
        assert (destino_raiz / "mi_plugin" / "plugin.json").exists()

    def test_carpeta_sin_manifest(self, tmp_path):
        vacia = tmp_path / "vacia"
        vacia.mkdir()
        with mock.patch.object(sc, "_plugins_directorio", return_value=tmp_path):
            assert sc._plugin_instalar(str(vacia)) == 1

    def test_ya_instalado_sobrescribe(self, tmp_path):
        origen = tmp_path / "mi_plugin"
        origen.mkdir()
        (origen / "plugin.json").write_text(
            '{"nombre": "mi_plugin", "version": "1.0.0", "herramientas": [{"nombre": "h"}]}'
        )
        destino_raiz = tmp_path / "plugins"
        (destino_raiz / "mi_plugin").mkdir(parents=True)
        with mock.patch.object(sc, "_plugins_directorio", return_value=destino_raiz):
            assert sc._plugin_instalar(str(origen), confirmar=False) == 0

    def test_descarga_falla(self, tmp_path):
        with (
            mock.patch.object(sc, "_plugins_directorio", return_value=tmp_path),
            mock.patch.object(sc, "_plugin_descargar_zip", return_value=None),
        ):
            assert sc._plugin_instalar("user/repo") == 1

    def test_descarga_sin_manifest(self, tmp_path):
        carpeta = tmp_path / "descargado"
        carpeta.mkdir()
        with (
            mock.patch.object(sc, "_plugins_directorio", return_value=tmp_path),
            mock.patch.object(sc, "_plugin_descargar_zip", return_value=carpeta),
        ):
            assert sc._plugin_instalar("user/repo") == 1


class TestEjecutarTui:
    def test_sin_textual(self):
        with mock.patch.dict(sys.modules, {"tui_app": None}):
            assert sc._ejecutar_tui(argparse.Namespace(consulta="x")) == 2
