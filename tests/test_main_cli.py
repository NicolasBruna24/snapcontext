"""Tests del dispatcher main() y del flujo principal (snapcontext.py).

Cada rama de la CLI se ejercita parcheando la función delegada; nunca se
arranca un servidor real ni se habla con proveedores.
"""

from __future__ import annotations

import argparse
import types
import unittest
from unittest import mock

import pytest

import snapcontext as sc


def _args(**kw):
    base = dict(consulta=None, directorio=".", depurar=False, auto=False)
    base.update(kw)
    return argparse.Namespace(**base)


class TestMainPuertasPrevias(unittest.TestCase):
    """Subcomandos que se resuelven antes del parser principal."""

    def test_sin_argumentos_muestra_ayuda(self):
        with mock.patch.object(sc, "_mostrar_ayuda_resumida") as ayuda:
            self.assertEqual(sc.main([]), 0)
        ayuda.assert_called_once()

    def test_puerta_plugin(self):
        with mock.patch.object(sc, "_ejecutar_comando_plugin", return_value=0) as fn:
            self.assertEqual(sc.main(["plugin", "list"]), 0)
        fn.assert_called_once_with(["list"])

    def test_puerta_telegram(self):
        with mock.patch.object(sc, "_ejecutar_comando_telegram", return_value=0) as fn:
            self.assertEqual(sc.main(["telegram", "estado"]), 0)
        fn.assert_called_once_with(["estado"])

    def test_puerta_discord(self):
        with mock.patch.object(sc, "_ejecutar_comando_discord", return_value=0) as fn:
            self.assertEqual(sc.main(["discord", "estado"]), 0)
        fn.assert_called_once_with(["estado"])

    def test_puerta_github(self):
        with mock.patch.object(sc, "_ejecutar_comando_github", return_value=0) as fn:
            self.assertEqual(sc.main(["github", "estado"]), 0)
        fn.assert_called_once_with(["estado"])

    def test_puerta_curador(self):
        with mock.patch.object(sc, "_ejecutar_comando_curador", return_value=0) as fn:
            self.assertEqual(sc.main(["curador", "estado"]), 0)
        fn.assert_called_once_with(["estado"])

    def test_puerta_revert(self):
        with mock.patch.object(sc, "_ejecutar_revert", return_value=0) as fn:
            self.assertEqual(sc.main(["revert", "3"]), 0)
        fn.assert_called_once_with("3")

    def test_puerta_revert_sin_paso(self):
        with mock.patch.object(sc, "_ejecutar_revert", return_value=0) as fn:
            self.assertEqual(sc.main(["revert"]), 0)
        fn.assert_called_once_with(None)

    def test_puerta_hook(self):
        with mock.patch.object(sc, "_ejecutar_comando_hook", return_value=0) as fn:
            self.assertEqual(sc.main(["hook", "list"]), 0)
        fn.assert_called_once_with(["list"])

    def test_puerta_hooks_plural(self):
        with mock.patch.object(sc, "_ejecutar_comando_hook", return_value=0) as fn:
            self.assertEqual(sc.main(["hooks"]), 0)
        fn.assert_called_once_with([])


class TestMainHelpYVersion(unittest.TestCase):
    def test_help_sale_con_systemexit_0(self):
        with pytest.raises(SystemExit) as exc:
            sc.main(["--help"])
        assert exc.value.code == 0

    def test_parser_se_construye_y_parsea(self):
        # Crea el parser completo y parsea un argv válido simple.
        parser = sc.crear_parser()
        args = parser.parse_args(sc._preparar_argv_aliases(["hola"]))
        assert args.consulta == "hola"


class TestMainRamasDeFlags(unittest.TestCase):
    """Cada flag independiente de main() con su función delegada parcheada."""

    def _rama(self, argv, objetivo, retorno=0, **extra_argv):
        with mock.patch.object(sc, objetivo, return_value=retorno) as fn:
            codigo = sc.main(argv)
        self.assertEqual(codigo, retorno)
        fn.assert_called_once()
        return fn

    def test_benchmark(self):
        self._rama(["--benchmark"], "_ejecutar_benchmark")

    def test_init(self):
        self._rama(["--init"], "asistente_configuracion_inicial")

    def test_setup_path(self):
        self._rama(["--setup-path"], "configurar_path")

    def test_diagnostico(self):
        self._rama(["--diagnostico"], "_ejecutar_diagnostico")

    def test_reparar(self):
        self._rama(["--reparar"], "_ejecutar_reparacion")

    def test_web(self):
        self._rama(["--web"], "iniciar_servidor_web")

    def test_tui(self):
        self._rama(["--tui"], "_ejecutar_tui")

    def test_demo(self):
        self._rama(["--demo"], "_ejecutar_demo")

    def test_historial(self):
        with mock.patch.object(sc, "_mostrar_historial") as fn:
            self.assertEqual(sc.main(["--historial"]), 0)
        fn.assert_called_once()

    def test_historial_limpiar_ok(self):
        with mock.patch.object(sc, "_limpiar_historial", return_value=True):
            self.assertEqual(sc.main(["--historial-limpiar"]), 0)

    def test_historial_limpiar_fallo(self):
        with mock.patch.object(sc, "_limpiar_historial", return_value=False):
            self.assertEqual(sc.main(["--historial-limpiar"]), 1)

    def test_daemon(self):
        self._rama(["--daemon"], "_daemon_bucle")

    def test_curador_unico(self):
        with mock.patch.object(sc, "_curador_ejecutar") as fn:
            self.assertEqual(sc.main(["--curador"]), 0)
        fn.assert_called_once()

    def test_inyectar_reglas(self):
        fake_sa = types.SimpleNamespace(inyectar_todas_las_reglas=mock.Mock(return_value=2))
        with mock.patch.dict("sys.modules", {"skill_abstraction": fake_sa}):
            self.assertEqual(sc.main(["--inyectar-reglas"]), 0)
        fake_sa.inyectar_todas_las_reglas.assert_called_once()

    def test_asesor(self):
        self._rama(["--asesor"], "_ejecutar_asesor")

    def test_api_generate_key(self):
        with mock.patch.object(sc, "_generar_clave_api", return_value="clave-nueva"):
            self.assertEqual(sc.main(["--api-generate-key"]), 0)

    def test_api(self):
        self._rama(["--api"], "iniciar_api")

    def test_chat(self):
        self._rama(["--chat"], "_ejecutar_chat")

    def test_sandbox_session_clean(self):
        self._rama(["--sandbox-session-clean"], "_limpiar_sesiones_huérfanas")

    def test_sub_agente_listar(self):
        self._rama(["--sub-agente-listar"], "_ejecutar_listar_sub_agentes")

    def test_git_revert(self):
        self._rama(["--git-revert"], "_ejecutar_revert")

    def test_skills_sin_datos(self):
        with mock.patch.object(sc, "_skill_listar", return_value=[]):
            self.assertEqual(sc.main(["--skills"]), 0)

    def test_skills_con_datos(self):
        fila = {
            "id": 1,
            "nombre": "flutter-test",
            "archivado": False,
            "confiabilidad": 0.9,
            "usos": 5,
            "fallos": 1,
            "descripcion": "cómo testear",
        }
        with mock.patch.object(sc, "_skill_listar", return_value=[fila]):
            self.assertEqual(sc.main(["--skills"]), 0)

    def test_modo_tarea_por_defecto(self):
        with (
            mock.patch.object(sc, "_aplicar_modo_inteligente", side_effect=lambda a: a),
            mock.patch.object(sc, "_ejecutar_modo_tarea", return_value=0) as fn,
        ):
            self.assertEqual(sc.main(["hola mundo"]), 0)
        fn.assert_called_once()

    def test_keyboardinterrupt_devuelve_130(self):
        with (
            mock.patch.object(sc, "_aplicar_modo_inteligente", side_effect=lambda a: a),
            mock.patch.object(sc, "_ejecutar_modo_tarea", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(sc.main(["hola"]), 130)

    def test_runtime_error_devuelve_1(self):
        with mock.patch.object(sc, "_aplicar_modo_inteligente", side_effect=RuntimeError("boom")):
            self.assertEqual(sc.main(["hola"]), 1)


class TestFlujoPrincipal(unittest.TestCase):
    """flujo_principal: orquesta Orquestador y registra historial."""

    def _ejecuta(self, codigo=0, memoria=True):
        fake_orq = types.SimpleNamespace(ejecutar_flujo=mock.Mock(return_value=codigo))
        with (
            mock.patch("orquestador.Orquestador", return_value=fake_orq),
            mock.patch.object(sc, "_sugerir_xpu") as xpu,
            mock.patch.object(sc, "_registrar_historial_async") as historial,
            mock.patch.object(sc, "MEMORIA_PROYECTO", memoria),
            mock.patch.object(sc, "_actualizar_claude_md_automatico") as claude,
        ):
            resultado = sc.flujo_principal(_args(consulta="tarea", depurar=False))
        xpu.assert_called_once()
        historial.assert_called_once()
        return resultado, claude, fake_orq

    def test_flujo_exitoso_actualiza_memoria_proyecto(self):
        codigo, claude, _orq = self._ejecuta(codigo=0, memoria=True)
        self.assertEqual(codigo, 0)
        claude.assert_called_once()

    def test_flujo_fallido_no_actualiza_memoria(self):
        codigo, claude, _orq = self._ejecuta(codigo=1, memoria=True)
        self.assertEqual(codigo, 1)
        claude.assert_not_called()

    def test_excepcion_se_propaga_y_registra_historial(self):
        fake_orq = types.SimpleNamespace()
        fake_orq.ejecutar_flujo = mock.Mock(side_effect=ValueError("catástrofe"))
        with (
            mock.patch("orquestador.Orquestador", return_value=fake_orq),
            mock.patch.object(sc, "_sugerir_xpu"),
            mock.patch.object(sc, "_registrar_historial_async") as historial,
            mock.patch.object(sc, "MEMORIA_PROYECTO", False),
            pytest.raises(ValueError),
        ):
            sc.flujo_principal(_args(consulta="x", depurar=False))
        historial.assert_called_once()

    def test_actualizar_claude_md_fallido_no_rompe(self):
        fake_orq = types.SimpleNamespace()
        fake_orq.ejecutar_flujo = mock.Mock(return_value=0)
        with (
            mock.patch("orquestador.Orquestador", return_value=fake_orq),
            mock.patch.object(sc, "_sugerir_xpu"),
            mock.patch.object(sc, "_registrar_historial_async"),
            mock.patch.object(sc, "MEMORIA_PROYECTO", True),
            mock.patch.object(
                sc, "_actualizar_claude_md_automatico", side_effect=RuntimeError("x")
            ),
            mock.patch.object(sc, "depurar") as depurar,
        ):
            self.assertEqual(sc.flujo_principal(_args(consulta="x", depurar=False)), 0)
        depurar.assert_called()


class TestConectarDbInicial(unittest.TestCase):
    def test_sin_url_no_hace_nada(self):
        self.assertEqual(sc.conectar_db_inicial(_args(db_url=None)), 0)

    def test_driver_ausente_devuelve_2(self):
        with mock.patch.dict("sys.modules", {"mcp_tools_db": None}):
            self.assertEqual(sc.conectar_db_inicial(_args(db_url="sqlite://x")), 2)

    def test_conexion_ok(self):
        fake_db = types.SimpleNamespace(
            db_connect=mock.Mock(return_value={"ok": True, "motor": "postgres"})
        )
        with mock.patch.dict("sys.modules", {"mcp_tools_db": fake_db}):
            self.assertEqual(sc.conectar_db_inicial(_args(db_url="url")), 0)

    def test_conexion_error(self):
        fake_db = types.SimpleNamespace(
            db_connect=mock.Mock(side_effect=RuntimeError("credenciales"))
        )
        with mock.patch.dict("sys.modules", {"mcp_tools_db": fake_db}):
            self.assertEqual(sc.conectar_db_inicial(_args(db_url="url")), 1)

    def test_conexion_rechazada(self):
        fake_db = types.SimpleNamespace(
            db_connect=mock.Mock(return_value={"ok": False, "error": "timeout"})
        )
        with mock.patch.dict("sys.modules", {"mcp_tools_db": fake_db}):
            self.assertEqual(sc.conectar_db_inicial(_args(db_url="url")), 1)


if __name__ == "__main__":
    unittest.main()
