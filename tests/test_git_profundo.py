#!/usr/bin/env python3
"""Tests de Git profundo (v6.20.0): commits atómicos por paso, mensajes
generados con IA, tabla ``pasos`` en la BD y revert nativo."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc


def _dir_tmp():
    return tempfile.mkdtemp(prefix="sc_git_profundo_")


def _args(extra=None):
    base = {"git_commit": True, "git_mensaje": None}
    base.update(extra or {})
    return argparse.Namespace(**base)


def _git(*cmd):
    """Ejecuta un comando git en un directorio y devuelve (codigo, out, err)."""
    import subprocess

    p = subprocess.run(["git"] + list(cmd), capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class _BDAislada:
    """Re-apunta DB_PATH a un SQLite temporal (patron documentado en _db_cerrar)."""

    def __init__(self):
        self.ruta = Path(tempfile.mkdtemp(prefix="sc_git_db_")) / "memoria.db"

    def __enter__(self):
        self._vieja = sc.DB_PATH
        sc._db_cerrar()
        sc.DB_PATH = self.ruta
        sc._db_migrar_pasos()
        return self.ruta

    def __exit__(self, *exc):
        sc._db_cerrar()
        sc.DB_PATH = self._vieja


class TestSanearMensajeCommit(unittest.TestCase):
    def test_elimina_clave_openai(self):
        self.assertNotIn("sk-abcdef123456", sc._sanear_mensaje_commit("feat: usar sk-abcdef123456"))

    def test_elimina_asignacion_api_key(self):
        limpio = sc._sanear_mensaje_commit("config api_key = abc123")
        self.assertNotIn("abc123", limpio)
        self.assertIn("[REDACTADO]", limpio)

    def test_mensaje_normal_intacto(self):
        self.assertEqual(sc._sanear_mensaje_commit("feat: login"), "feat: login")


class TestGenerarMensajeCommit(unittest.TestCase):
    def test_fallback_sin_proveedor(self):
        with mock.patch.object(sc, "cargar_configuracion", side_effect=RuntimeError("sin config")):
            self.assertEqual(
                sc._generar_mensaje_commit("+ linea", "arreglar login"), "paso: arreglar login"
            )

    def test_mensaje_con_ia_mockeada(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={"provider": "ollama"}),
            mock.patch.object(sc, "_enviar_al_proveedor", return_value="feat: añadir login"),
        ):
            self.assertEqual(sc._generar_mensaje_commit("+ login()", "login"), "feat: añadir login")

    def test_mensaje_ia_se_sanea(self):
        with (
            mock.patch.object(sc, "cargar_configuracion", return_value={"provider": "ollama"}),
            mock.patch.object(
                sc, "_enviar_al_proveedor", return_value='feat: "usar sk-abcdefgh123"'
            ),
        ):
            self.assertNotIn("sk-abcdefgh123", sc._generar_mensaje_commit("", "x"))


class TestCommitPaso(unittest.TestCase):
    def setUp(self):
        self.dir = _dir_tmp()
        self.paso = {"accion": "editar", "descripcion": "crear modulo"}
        Path(self.dir, "nuevo.py").write_text("x = 1\n", encoding="utf-8")
        # B15-CI: identidad Git **local** al repo temporal. Antes el test
        # dependía de la identidad global del runner (CI la define en el paso
        # "Configurar git"), lo que lo hacía frágil e irreproducible fuera de
        # ese entorno. Se usa una identidad ficticia.
        _git("-C", self.dir, "init", "-q")
        _git("-C", self.dir, "config", "user.email", "test@b15ci.local")
        _git("-C", self.dir, "config", "user.name", "B15CI Test")
        self._bd = _BDAislada()
        self._bd.__enter__()
        # Evita llamadas reales al proveedor: sin config, el generador usa
        # el mensaje de respaldo "paso: <descripcion>".
        self._mock_cfg = mock.patch.object(
            sc, "cargar_configuracion", side_effect=RuntimeError("offline")
        )
        self._mock_cfg.start()

    def tearDown(self):
        self._mock_cfg.stop()
        self._bd.__exit__()
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def test_commit_devuelve_hash(self):
        h = sc._commit_paso(self.paso, _args(), self.dir)
        self.assertTrue(h and len(h) >= 7)

    def test_commit_registrado_en_bd(self):
        h = sc._commit_paso(self.paso, _args(), self.dir)
        filas = sc._db_query("SELECT * FROM pasos WHERE commit_hash = ?", (h,))
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["descripcion"], "crear modulo")

    def test_sin_cambios_devuelve_none(self):
        primer = sc._commit_paso(self.paso, _args(), self.dir)
        self.assertTrue(primer)  # commit inicial
        self.assertIsNone(sc._commit_paso(self.paso, _args(), self.dir))

    def test_git_mensaje_manual_tiene_prioridad(self):
        sc._commit_paso(self.paso, _args({"git_mensaje": "manual: x"}), self.dir)
        _, out, _ = _git("-C", self.dir, "log", "-1", "--pretty=%s")
        self.assertEqual(out.strip(), "manual: x")

    def test_inicializa_repo_si_no_existe(self):
        # B15-CI: `setUp` deja el repo listo (identidad local) para que el test
        # no dependa de la config global del runner. Este caso parte de "no es
        # repo", así que se parte de ese estado sin quitar el `assertTrue`
        # final, que es la aserción que verifica la inicialización.
        shutil.rmtree(Path(self.dir, ".git"), ignore_errors=True)
        self.assertFalse(Path(self.dir, ".git").exists())
        # Fuerza el caso "no es repo" aunque el entorno tenga un repo ancestro.
        with mock.patch.object(sc, "_es_repo_git", return_value=False):
            sc._commit_paso(self.paso, _args(), self.dir)
        self.assertTrue(Path(self.dir, ".git").exists())

    def test_mensaje_generado_fallback_formato(self):
        with mock.patch.object(sc, "cargar_configuracion", side_effect=RuntimeError("offline")):
            h = sc._commit_paso(self.paso, _args(), self.dir)
            self.assertTrue(h)
        _, out, _ = _git("-C", self.dir, "log", "-1", "--pretty=%s")
        self.assertTrue(out.strip().startswith("paso: crear modulo"))


class TestStagePathspecB15CI(unittest.TestCase):
    """B15-CI — el staging por pathspec no debe activar la frontera CONTAINER.

    El pathspec ``:(exclude).work`` lleva un ``(`` que, escrito como cadena de
    shell, el detector global de metacaracteres tomaba por sintaxis y mandaba
    ``git add`` al contenedor, partiendo la transacción Git entre el
    contenedor y el host. Expresado como argv el ``(`` es un literal de Git.
    """

    def setUp(self):
        self.dir = _dir_tmp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        _git("-C", self.dir, "init", "-q")
        _git("-C", self.dir, "config", "user.email", "test@b15ci.local")
        _git("-C", self.dir, "config", "user.name", "B15CI Test")

    def _staged(self) -> list[str]:
        out = subprocess.run(
            ["git", "-C", self.dir, "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return out.split()

    # --- A. Clasificación -------------------------------------------------
    def test_a1_el_stage_por_string_seria_sandbox(self):
        """Documenta el defecto: la forma string activa CONTAINER."""
        import sandbox_utils as su

        nivel, _ = su.clasificar_comando(sc._comando_stage_paso())
        self.assertEqual(nivel, "sandbox")

    def test_a2_el_stage_por_argv_es_directo(self):
        """A — la representación argv se clasifica DIRECT."""
        import sandbox_utils as su

        nivel, motivo = su.clasificar_comando_argv(sc._argv_stage_paso())
        self.assertEqual(nivel, "directo", motivo)

    def test_a3_el_stage_por_argv_usa_shell_false(self):
        """No se introduce shell=True ni escaping manual."""
        argv = sc._argv_stage_paso()
        self.assertEqual(argv[0], "git")
        self.assertIn(":(exclude).work", argv)
        self.assertNotIn('":(exclude).work"', argv)

    def test_a4_la_politica_global_no_se_relaja(self):
        """La corrección NO toca el clasificador de strings."""
        import sandbox_utils as su

        self.assertIn("|", su._METACARACTERES_AMPLIOS.pattern)
        nivel, _ = su.clasificar_comando("ls && rm -rf /")
        self.assertEqual(nivel, "sandbox")

    # --- B. Exclusión funcional ------------------------------------------
    def test_b1_stage_excluye_work_y_stagea_el_resto(self):
        """B — el archivo normal se stagea y `.work` sigue excluido."""
        Path(self.dir, "nuevo.py").write_text("x = 1\n", encoding="utf-8")
        work = Path(self.dir, ".work")
        work.mkdir()
        (work / "state.md").write_text("contenido work\n", encoding="utf-8")

        rc, _, err = sc._ejecutar_stage_paso(self.dir)
        self.assertEqual(rc, 0, err)

        staged = self._staged()
        self.assertIn("nuevo.py", staged)
        self.assertNotIn(".work/state.md", staged)

    # --- C. _commit_paso: transacción completa ----------------------------
    def test_c1_transaccion_git_completa(self):
        """C — init/add/commit/rev-parse producen un commit válido."""
        Path(self.dir, "nuevo.py").write_text("x = 1\n", encoding="utf-8")
        paso = {"accion": "editar", "descripcion": "crear modulo"}
        with (
            _BDAislada(),
            mock.patch.object(sc, "cargar_configuracion", side_effect=RuntimeError("offline")),
        ):
            h = sc._commit_paso(paso, _args(), self.dir)
        self.assertTrue(h and len(h) >= 7)
        _, out, _ = _git("-C", self.dir, "log", "-1", "--pretty=%H")
        self.assertEqual(out.strip(), h)


class TestStageFalloB15CI(unittest.TestCase):
    """B15-CI — ruta latente y fallos de staging correctamente reportados."""

    def setUp(self):
        self.dir = _dir_tmp()
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        _git("-C", self.dir, "init", "-q")
        _git("-C", self.dir, "config", "user.email", "test@b15ci.local")
        _git("-C", self.dir, "config", "user.name", "B15CI Test")
        Path(self.dir, "nuevo.py").write_text("x = 1\n", encoding="utf-8")

    # --- D. _git_commit_paso ---------------------------------------------
    def test_d1_git_commit_paso_usa_la_misma_frontera(self):
        """D — la ruta latente tampoco manda el staging a CONTAINER."""
        with mock.patch.object(sc, "_ejecutar_stage_paso", wraps=sc._ejecutar_stage_paso) as stage:
            self.assertTrue(sc._git_commit_paso("crear modulo", self.dir))
        stage.assert_called_once()

    def test_d2_git_commit_paso_stagea_de_verdad(self):
        """D — el commit contiene el archivo y excluye `.work`."""
        work = Path(self.dir, ".work")
        work.mkdir()
        (work / "state.md").write_text("contenido work\n", encoding="utf-8")

        self.assertTrue(sc._git_commit_paso("crear modulo", self.dir))
        _, out, _ = _git("-C", self.dir, "show", "--name-only", "--pretty=", "HEAD")
        self.assertIn("nuevo.py", out.split())
        self.assertNotIn(".work/state.md", out.split())

    # --- E. Fallo de staging ---------------------------------------------
    def test_e1_no_se_reporta_como_fallo_de_commit(self):
        """E — el commit NO se intenta y el diagnóstico dice 'staging'."""

        # `_ejecutar_comando` se mantiene funcional: solo se rompe el staging,
        # de modo que las sondas previas (`_es_repo_git`, `git status`) siguen
        # devolviendo un resultado válido y el flujo llega al staging.
        def runner(comando, *a, **k):
            if "commit" in str(comando):
                raise AssertionError("no debía intentar el commit")
            if "status" in str(comando):
                return (0, "nuevo.py\n", "")
            return (0, "true", "")

        with (
            mock.patch.object(
                sc, "_ejecutar_stage_paso", return_value=(128, "", "fatal: index error")
            ),
            mock.patch.object(sc, "_ejecutar_comando", side_effect=runner),
            mock.patch.object(sc, "aviso") as aviso_mock,
        ):
            resultado = sc._commit_paso({"accion": "editar", "descripcion": "x"}, _args(), self.dir)

        self.assertIsNone(resultado)
        texto = " ".join(str(c) for c in aviso_mock.call_args_list)
        self.assertIn("staging", texto.lower())
        self.assertNotIn("El commit del paso falló", texto)

    def test_e2_git_commit_paso_no_continua_al_commit(self):
        """E — `_git_commit_paso` devuelve False sin intentar el commit."""

        def runner(comando, *a, **k):
            if "commit" in str(comando):
                raise AssertionError("no debía intentar el commit")
            return (0, "true", "")

        with (
            mock.patch.object(
                sc, "_ejecutar_stage_paso", return_value=(128, "", "fatal: index error")
            ),
            mock.patch.object(sc, "_ejecutar_comando", side_effect=runner),
        ):
            self.assertFalse(sc._git_commit_paso("crear modulo", self.dir))

    def test_e3_stage_rechazado_por_politica_no_se_ejecuta(self):
        """E — si la política marcara el argv como sandbox, no se ejecuta."""
        import sandbox_utils as su

        with mock.patch.object(
            su, "clasificar_comando_argv", return_value=("sandbox", "fuera de allowlist")
        ):
            rc, _, err = sc._ejecutar_stage_paso(self.dir)
        self.assertEqual(rc, -1)
        self.assertIn("staging rechazado", err)


class TestRevertPaso(unittest.TestCase):
    def setUp(self):
        self.dir = _dir_tmp()
        self.old = os.getcwd()
        os.chdir(self.dir)
        self._bd = _BDAislada()
        self._bd.__enter__()
        self._mock_cfg = mock.patch.object(
            sc, "cargar_configuracion", side_effect=RuntimeError("offline")
        )
        self._mock_cfg.start()
        _git("init", "-q")
        _git("config", "user.email", "t@t.local")
        _git("config", "user.name", "Test")

    def tearDown(self):
        self._mock_cfg.stop()
        self._bd.__exit__()
        os.chdir(self.old)
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def _commit_paso_en_bd(self):
        Path(self.dir, "a.py").write_text("a = 1\n", encoding="utf-8")
        return sc._commit_paso({"accion": "editar", "descripcion": "paso uno"}, _args(), self.dir)

    def test_revert_exitoso(self):
        h = self._commit_paso_en_bd()
        filas = sc._db_query("SELECT id FROM pasos WHERE commit_hash = ?", (h,))
        step_id = filas[0]["id"]
        self.assertTrue(sc._revertir_paso(step_id))
        _, out, _ = _git("log", "-1", "--pretty=%s")
        self.assertEqual(out.strip(), f"revert: paso {step_id}")

    def test_revert_paso_inexistente(self):
        self.assertFalse(sc._revertir_paso(99999))

    def test_revert_sin_commit_asociado(self):
        sc._db_registrar_paso("sin hash", None)
        filas = sc._db_query("SELECT id FROM pasos WHERE commit_hash IS NULL")
        self.assertFalse(sc._revertir_paso(filas[0]["id"]))

    def test_mensaje_revert_mostrado(self):
        import io as _io
        from contextlib import redirect_stdout

        h = self._commit_paso_en_bd()
        filas = sc._db_query("SELECT id FROM pasos WHERE commit_hash = ?", (h,))
        buf = _io.StringIO()
        with redirect_stdout(buf):
            sc._revertir_paso(filas[0]["id"])
        self.assertIn("Revertido paso", buf.getvalue())

    def test_conflicto_sugerencia_mergetool(self):
        h = self._commit_paso_en_bd()
        filas = sc._db_query("SELECT id FROM pasos WHERE commit_hash = ?", (h,))
        step_id = filas[0]["id"]
        with mock.patch.object(
            sc, "_ejecutar_comando", return_value=(1, "", "CONFLICT (content): x.py")
        ):
            self.assertFalse(sc._revertir_paso(step_id))


class TestComandoRevertCLI(unittest.TestCase):
    def test_revert_paso_invalido(self):
        self.assertEqual(sc._ejecutar_revert("abc"), 1)

    def test_revert_paso_no_existe(self):
        self.assertEqual(sc._ejecutar_revert("424242"), 1)

    def test_gateway_revert_en_main(self):
        # `snapcontext revert <N>` no llega al parser principal (gateway).
        with mock.patch.object(sc, "_ejecutar_revert", return_value=0) as rev:
            sc.main(["revert", "3"])
            rev.assert_called_once_with("3")

    def test_flag_git_revert_se_parsea(self):
        args = sc.crear_parser().parse_args(["--git-revert", "5"])
        self.assertEqual(args.git_revert, 5)
        args2 = sc.crear_parser().parse_args(["--git-revert"])
        self.assertEqual(args2.git_revert, -1)
        args3 = sc.crear_parser().parse_args([])
        self.assertIsNone(args3.git_revert)

    def test_flag_git_mensaje_se_parsea(self):
        args = sc.crear_parser().parse_args(["--git-mensaje", "feat: x"])
        self.assertEqual(args.git_mensaje, "feat: x")


class TestIntegracionReAct(unittest.TestCase):
    def test_react_commit_tras_editar_archivo(self):
        import react_agent as ra

        dir_tmp = _dir_tmp()
        agente = ra.ReactAgent(directorio=dir_tmp, auto=True, proveedor="mock", git_commit=True)
        self.assertEqual(agente.git_commit, True)
        with (
            mock.patch.object(sc, "_commit_paso", return_value="abc1234") as commit,
            mock.patch.object(sc, "cargar_configuracion", return_value={"provider": "mock"}),
            mock.patch.object(
                ra.ReactAgent,
                "_pedir_decision",
                side_effect=[
                    {
                        "pensamiento": "editar",
                        "accion": "editar_archivo",
                        "argumentos": {"ruta": "f.txt", "contenido": "hola"},
                    },
                    {"pensamiento": "fin", "accion": "finalizar", "argumentos": {"resumen": "ok"}},
                ],
            ),
        ):
            r = agente.ejecutar("crear archivo")
        self.assertTrue(r["ok"])
        commit.assert_called_once()
        contenido_historial = " ".join(m["content"] for m in agente.historial)
        self.assertIn("[COMMIT] abc1234", contenido_historial)


class TestVersionYBD(unittest.TestCase):
    def test_version_6_19_0(self):
        self.assertRegex(
            str(sc.VERSION),
            r"^\d+\.\d+\.\d+$",
            "VERSION debe tener formato X.Y.Z; obtenido: %r" % sc.VERSION,
        )

    def test_tabla_pasos_existe_tras_inicializar(self):
        sc._db_migrar_pasos()
        filas = sc._db_query("SELECT name FROM sqlite_master WHERE type='table' AND name='pasos'")
        self.assertEqual(len(filas), 1)

    def test_migracion_idempotente(self):
        sc._db_migrar_pasos()
        sc._db_migrar_pasos()  # no debe lanzar


if __name__ == "__main__":
    unittest.main()
