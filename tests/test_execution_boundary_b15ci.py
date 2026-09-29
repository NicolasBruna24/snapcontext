#!/usr/bin/env python3
"""B15-CI — Execution boundary: `CONTAINER` es vinculante y `directorio` es
el workspace del comando.

Cierra la frontera descrita en el diagnóstico B15-CI:

    _decidir_ejecucion_sandbox() -> CONTAINER
        ↓
    (antes) _envolver_sandbox() con _SANDBOX_ACTIVO=False -> NO-OP
        ↓
    (antes) raiz = Path.cwd()  -> workspace equivocado
        ↓
    (antes) ejecución directa sin sandbox en el host

Los tests comprueban el CONTRATO, no una implementación accidental, y
distinguen dos cosas que el defecto histórico confundía:

    * que se llame al wrapper  !=  que se ejecute Docker de verdad
    * que Docker se ejecute     !=  que opere sobre `directorio`

Se evita deliberadamente `mock.patch("_envolver_sandbox")` como prueba de que
el sandbox ocurre: ese mock solo demuestra que hubo una llamada y era
justamente lo que dejó pasar el bug. Aquí se intercepta el LÍMITE REAL
(`_ejecutar_con_politica` → `sandbox_utils.subprocess.run`), que es donde nace
el proceso, y se inspecciona el comando completo: imagen, mount, workspace y
comando.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import sandbox_utils
import snapcontext as sc

#: Comando fuera de la allowlist (intérprete + metacarácter `&&`).
CMD_FUERA_ALLOWLIST = "python3 -c \"print('ok')\""
#: El mismo, pero escribiendo un fichero: permite comprobar el workspace.
CMD_ESCRIBE = "python3 -c \"open('generado.txt','w').write('x')\" && python3 -c \"print('ok')\""

WORKSPACE = sc.SANDBOX_DIR_TRABAJO

#: Referencia REAL a subprocess.run, capturada antes de cualquier mock. El
#: doble del contenedor la usa para ejecutar de verdad DENTRO del workspace;
#: si se usara el módulo parcheado, la llamada volvería a entrar en el mock.
_RUN_REAL = subprocess.run


def _proc(rc: int = 0, out: str = "", err: str = "") -> mock.Mock:
    return mock.Mock(returncode=rc, stdout=out, stderr=err)


def _argv_ejecutado(limite: mock.Mock) -> str:
    """Devuelve la cadena que realmente se entregó al proceso."""
    args, kwargs = limite.call_args
    ejecutado = args[0] if args else kwargs.get("args")
    if isinstance(ejecutado, (list, tuple)):
        return " ".join(str(a) for a in ejecutado)
    return str(ejecutado)


class _BaseFrontera(unittest.TestCase):
    """Aisla el estado global de sandbox alrededor de cada test."""

    def setUp(self):
        self._prev_activo = sc._SANDBOX_ACTIVO
        self._prev_sesion = sc._SESION_DOCKER_SOLICITADA
        self._prev_no_sandbox = sc._NO_SANDBOX
        self._prev_imagen = sc._SANDBOX_IMAGEN
        self._prev_prep = sc._SANDBOX_COMANDO_PREP
        sc._SANDBOX_ACTIVO = False
        sc._SESION_DOCKER_SOLICITADA = False
        sc._configurar_no_sandbox(False)
        os.environ.pop("SNAPCONTEXT_SANDBOX", None)
        sc._SANDBOX_IMAGEN = "python:3.11-slim"
        sc._SANDBOX_COMANDO_PREP = None

    def tearDown(self):
        sc._SANDBOX_ACTIVO = self._prev_activo
        sc._SESION_DOCKER_SOLICITADA = self._prev_sesion
        sc._configurar_no_sandbox(self._prev_no_sandbox)
        sc._SANDBOX_IMAGEN = self._prev_imagen
        sc._SANDBOX_COMANDO_PREP = self._prev_prep
        os.environ.pop("SNAPCONTEXT_SANDBOX", None)

    def repo(self) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d

    def limite_real(self, retorno=_proc(0, "ok", "")):
        """Mockea el límite real de ejecución (nacimiento del proceso)."""
        return mock.patch.object(sandbox_utils.subprocess, "run", return_value=retorno)


class TestA_ContainerMontaElDirectorioSolicitado(_BaseFrontera):
    """Test A — CONTAINER monta `directorio` y corre con /workspace."""

    def test_el_wrapper_monta_el_directorio_y_el_workspace(self):
        repo = self.repo()
        cmd = sc._construir_comando_docker(CMD_FUERA_ALLOWLIST, str(repo))

        self.assertTrue(cmd.startswith("docker run"), cmd)
        self.assertIn("--rm", cmd)
        self.assertIn(f"-v {repo.resolve()}:{WORKSPACE}", cmd)
        self.assertIn(f"-w {WORKSPACE}", cmd)
        self.assertIn("python:3.11-slim", cmd)
        # shlex escapa el comando; se comprueba vía el argv resultante.
        import shlex

        self.assertIn(CMD_FUERA_ALLOWLIST, shlex.split(cmd))

    def test_el_limite_real_recibe_docker_no_el_comando_crudo(self):
        """No basta con que se llame al wrapper: el proceso debe ser Docker."""
        repo = self.repo()
        with (
            mock.patch.object(sc, "_docker_disponible", return_value=True),
            self.limite_real() as limite,
        ):
            codigo, _, _ = sc._ejecutar_comando(CMD_FUERA_ALLOWLIST, str(repo))

        self.assertEqual(codigo, 0)
        limite.assert_called_once()
        ejecutado = _argv_ejecutado(limite)
        self.assertIn("docker run", ejecutado)
        self.assertIn(f"{repo.resolve()}:{WORKSPACE}", ejecutado)
        self.assertIn(f"-w {WORKSPACE}", ejecutado)


class TestB_DockerPresenteYGlobalesInactivos(_BaseFrontera):
    """Test B — regresión crítica: Docker presente + sandbox global inactivo."""

    def test_container_nunca_cae_a_host(self):
        """_SANDBOX_ACTIVO=False ya NO anula una decisión CONTAINER."""
        repo = self.repo()
        self.assertFalse(sc._SANDBOX_ACTIVO)

        with (
            mock.patch.object(sc, "_docker_disponible", return_value=True),
            self.limite_real() as limite,
        ):
            codigo, _, _ = sc._ejecutar_comando(CMD_FUERA_ALLOWLIST, str(repo))

        self.assertEqual(codigo, 0)
        # Si esto fallara, el comando se habría ejecutado sin sandbox en el host.
        self.assertIn("docker run", _argv_ejecutado(limite))

    def test_la_politica_sigue_decidiendo(self):
        """El cambio es de ejecución, no de política."""
        repo = self.repo()
        with mock.patch.object(sc, "_docker_disponible", return_value=True):
            self.assertEqual(
                sc._decidir_ejecucion_sandbox(CMD_FUERA_ALLOWLIST, str(repo)),
                sc._SANDBOX_CONTENEDOR,
            )

    def test_el_cwd_del_proceso_no_sustituye_al_workspace(self):
        """El host `cwd` puede ser otro; el mount debe ser `directorio`."""
        repo = self.repo()
        cwd = Path.cwd()
        with (
            mock.patch.object(sc, "_docker_disponible", return_value=True),
            self.limite_real() as limite,
        ):
            sc._ejecutar_comando(CMD_FUERA_ALLOWLIST, str(repo))
        ejecutado = _argv_ejecutado(limite)
        self.assertIn(f"-v {repo.resolve()}:{WORKSPACE}", ejecutado)
        self.assertNotIn(f"-v {cwd}:", ejecutado)


class TestC_DirectorioDistintoDelCwd(_BaseFrontera):
    """Test C — `directorio` != cwd: el cambio aparece en el directorio pedido."""

    def test_el_workspace_montado_es_el_directorio_y_no_el_cwd(self):
        repo = self.repo()
        cwd = Path.cwd()
        cmd = sc._construir_comando_docker(CMD_ESCRIBE, str(repo))
        self.assertIn(f"-v {repo.resolve()}:{WORKSPACE}", cmd)
        self.assertNotIn(f"-v {cwd}:", cmd)


class TestD_FailClosed(_BaseFrontera):
    """Test D — CONTAINER nunca degrada a ejecución directa en el host.

    El fallo cerrado no se implementa con una segunda comprobación de
    Docker (eso duplicaría la política en la capa de ejecución), sino por
    construcción: cuando la decisión es CONTAINER el proceso que nace ES
    `docker run`, así que si el daemon no responde ese proceso falla y su
    código de error se propaga. Lo que NO puede ocurrir —y es lo que se
    comprueba aquí— es que el comando se ejecute en el host.
    """

    def test_si_docker_falla_el_error_se_propaga_sin_caer_al_host(self):
        repo = self.repo()
        with (
            mock.patch.object(
                sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_CONTENEDOR
            ),
            # El binario de Docker existe pero el daemon no responde.
            mock.patch.object(sc, "_docker_disponible", return_value=False),
            mock.patch.object(
                sandbox_utils.subprocess,
                "run",
                return_value=_proc(125, "", "docker: daemon no responde"),
            ) as limite,
        ):
            codigo, _, stderr = sc._ejecutar_comando(CMD_FUERA_ALLOWLIST, str(repo))

        limite.assert_called_once()
        # Lo que se lanza es Docker, nunca el comando crudo en el host.
        self.assertIn("docker run", _argv_ejecutado(limite))
        # El fallo del contenedor se propaga al llamador (fail-closed).
        self.assertEqual(codigo, 125)
        self.assertIn("daemon", stderr)


class TestE_DirectoYAbortar(_BaseFrontera):
    """Test E — DIRECT y ABORTAR conservan su semántica."""

    def test_directo_ejecuta_en_directorio_sin_docker(self):
        repo = self.repo()
        with (
            mock.patch.object(sc, "_docker_disponible", return_value=True),
            self.limite_real() as limite,
        ):
            codigo, _, _ = sc._ejecutar_comando("ls", str(repo))
        self.assertEqual(codigo, 0)
        self.assertNotIn("docker", _argv_ejecutado(limite))
        self.assertEqual(limite.call_args.kwargs.get("cwd"), str(repo))

    def test_abortar_no_ejecuta(self):
        repo = self.repo()
        with (
            mock.patch.object(sc, "_decidir_ejecucion_sandbox", return_value=sc._SANDBOX_ABORTAR),
            mock.patch.object(sandbox_utils.subprocess, "run") as limite,
        ):
            codigo, _, stderr = sc._ejecutar_comando("rm -rf /", str(repo))
        limite.assert_not_called()
        self.assertEqual(codigo, -1)
        self.assertIn("abortado", stderr)


class TestF_WrapperAislado(_BaseFrontera):
    """Test F — `_envolver_sandbox` conserva su contrato histórico (Invariante C)."""

    def test_sin_sandbox_devuelve_comando_intacto(self):
        self.assertFalse(sc._SANDBOX_ACTIVO)
        self.assertEqual(sc._envolver_sandbox("pytest", "."), "pytest")

    def test_con_sandbox_envuelve(self):
        repo = self.repo()
        sc._SANDBOX_ACTIVO = True
        envuelto = sc._envolver_sandbox("pytest -q", str(repo))
        self.assertIn("docker run --rm", envuelto)
        self.assertIn(f"-w {WORKSPACE}", envuelto)

    def test_el_constructor_puro_no_depende_de_globales(self):
        """`_construir_comando_docker` envuelve aunque el global esté inactivo."""
        repo = self.repo()
        self.assertFalse(sc._SANDBOX_ACTIVO)
        self.assertIn("docker run", sc._construir_comando_docker("ls", str(repo)))


class TestG_PreYPostSobreElMismoWorkspace(_BaseFrontera):
    """Test G — el escenario B15 real: PRE y POST sobre el MISMO árbol.

    Es el test que habría detectado el defecto original. El doble emula el
    contenedor de forma fiel: lee el mount de la invocación de Docker y
    ejecuta el comando DENTRO de ese workspace, igual que haría Docker con
    `-v <dir>:/workspace -w /workspace`.
    """

    def _git(self, repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _emular_contenedor(self, *args, **_kwargs):
        import shlex

        argv = shlex.split(args[0]) if args else []
        host = next((argv[i + 1].split(":")[0] for i, a in enumerate(argv) if a == "-v"), None)
        self.assertIsNotNone(host, f"no se encontró mount en: {argv}")
        self.assertIn("-w", argv)
        return _RUN_REAL(argv[-1], shell=True, cwd=host, capture_output=True, text=True)

    def test_pre_y_post_observan_el_mismo_arbol(self):
        import work_verdict

        repo = self.repo()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "b15ci@example.com")
        self._git(repo, "config", "user.name", "B15-CI")
        (repo / "modulo.py").write_text("OK = 1\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "inicial")
        (Path.cwd() / "generado.txt").unlink(missing_ok=True)

        pre = work_verdict.leer_estado_git(str(repo))
        with (
            mock.patch.object(sc, "_docker_disponible", return_value=True),
            mock.patch.object(sandbox_utils.subprocess, "run", side_effect=self._emular_contenedor),
        ):
            codigo, _, _ = sc._ejecutar_comando(CMD_ESCRIBE, str(repo))
        post = work_verdict.leer_estado_git(str(repo))

        self.assertEqual(codigo, 0)
        # El cambio aparece en el repositorio observado...
        self.assertTrue((repo / "generado.txt").exists())
        self.assertNotEqual(pre.tree_sha, post.tree_sha)
        # ...y NO en el checkout del proceso (el defecto original).
        self.assertFalse((Path.cwd() / "generado.txt").exists())
        # La identidad B15 conclude OBSOLETE, sin tocar la semántica.
        verdict = work_verdict.producir_verdict(True, CMD_ESCRIBE, pre)
        self.assertEqual(
            work_verdict.evaluar_validez_verdict(verdict, post).status,
            work_verdict.OBSOLETE,
        )


if __name__ == "__main__":
    unittest.main()
