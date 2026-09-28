"""Tests B11 — Work Context: atomicidad, creación, staging y PROJECT boundary.

Cada clase cubre una sección obligatoria del enunciado B11 §9, con
evidencia verificable (resultado Git real, discovery real) y sin tocar el
repositorio real: todo corre sobre directorios temporales.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc
from exceptions import RutaInseguraError
from work_context import (
    DOCUMENTOS_CANONICOS,
    EXCLUSION_STAGING_GIT,
    WORK_CONTAINER,
    crear_trabajo,
    escribir_documento_trabajo,
    esta_dentro_de_trabajo,
    ruta_contenedor,
)


def _raiz(tmp: Path, nombre: str = "proj") -> Path:
    raiz = tmp / nombre
    raiz.mkdir(parents=True)
    return raiz


class TestDefinicionUnica(unittest.TestCase):
    def test_literal_unico_y_exclusion_coherente(self):
        self.assertEqual(WORK_CONTAINER, ".work")
        self.assertIn(WORK_CONTAINER, EXCLUSION_STAGING_GIT)
        self.assertIn(WORK_CONTAINER, sc.DIRS_IGNORADOS)
        self.assertIn(WORK_CONTAINER, sc.CARPETAS_IGNORADAS)
        # La misma constante alimenta el staging y las listas de discovery.
        self.assertEqual(sc._comando_stage_paso(), f"git add . {EXCLUSION_STAGING_GIT}")

    def test_helpers_de_pertenencia(self):
        self.assertTrue(esta_dentro_de_trabajo(".work/demo/state.md"))
        self.assertTrue(esta_dentro_de_trabajo("x/.work/y"))
        self.assertFalse(esta_dentro_de_trabajo("src/main.py"))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ruta_contenedor(d).name, WORK_CONTAINER)


class TestEscritorAtomico(unittest.TestCase):
    def test_escritura_normal_y_reemplazo(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            ruta = escribir_documento_trabajo(".work/a/state.md", "v1", raiz)
            self.assertTrue(ruta.is_file())
            self.assertEqual(ruta.read_text(encoding="utf-8"), "v1")
            escribir_documento_trabajo(".work/a/state.md", "v2", raiz)
            self.assertEqual(ruta.read_text(encoding="utf-8"), "v2")

class TestEscritorAtomicoFallo(unittest.TestCase):
    def test_fallo_antes_del_replace_conserva_contenido(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            ruta = escribir_documento_trabajo(".work/a/state.md", "previo", raiz)
            import work_context as wc

            real = wc._reemplazar

            def _romper(temporal, destino):
                raise OSError("fallo simulado antes del reemplazo")

            wc._reemplazar = _romper  # type: ignore[method-assign]
            try:
                with self.assertRaises(OSError):
                    escribir_documento_trabajo(".work/a/state.md", "nuevo", raiz)
            finally:
                wc._reemplazar = real  # type: ignore[method-assign]
            self.assertEqual(ruta.read_text(encoding="utf-8"), "previo")
            residuos = [p for p in ruta.parent.iterdir() if p.name.startswith(".tmp-work-")]
            self.assertEqual(residuos, [])

    def test_interrupcion_conserva_contenido(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            ruta = escribir_documento_trabajo(".work/a/state.md", "previo", raiz)
            import work_context as wc

            real = wc._reemplazar

            def _interrumpir(temporal, destino):
                raise KeyboardInterrupt()

            wc._reemplazar = _interrumpir  # type: ignore[method-assign]
            try:
                with self.assertRaises(KeyboardInterrupt):
                    escribir_documento_trabajo(".work/a/state.md", "nuevo", raiz)
            finally:
                wc._reemplazar = real  # type: ignore[method-assign]
            self.assertEqual(ruta.read_text(encoding="utf-8"), "previo")

    def test_rechazos_de_ruta(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            with self.assertRaises(RutaInseguraError):
                escribir_documento_trabajo("/abs/state.md", "x", raiz)
            with self.assertRaises(RutaInseguraError):
                escribir_documento_trabajo(".work/../fuera.md", "x", raiz)
            with self.assertRaises(RutaInseguraError):
                escribir_documento_trabajo("src/main.py", "x", raiz)
            with self.assertRaises(RutaInseguraError):
                escribir_documento_trabajo(".work/a/state.md", "x", raiz / "no-existe")

    def test_destino_symlink_rechazado(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            real = raiz / WORK_CONTAINER / "a" / "real.md"
            real.parent.mkdir(parents=True)
            real.write_text("real", encoding="utf-8")
            enlace = real.parent / "state.md"
            try:
                enlace.symlink_to(real)
            except OSError:
                self.skipTest("el sistema no permite crear symlinks")
            with self.assertRaises(RutaInseguraError):
                escribir_documento_trabajo(".work/a/state.md", "nuevo", raiz)
            self.assertEqual(real.read_text(encoding="utf-8"), "real")


class TestCreacionMinima(unittest.TestCase):
    def test_creacion_y_documentos_canonicos(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            base = crear_trabajo(
                "demo", raiz, titulo="Demostracion",
                objetivo="Probar el canal WORK.", criterios=["criterio 1"],
            )
            self.assertEqual(base, raiz / WORK_CONTAINER / "demo")
            for nombre in (*DOCUMENTOS_CANONICOS, "README.md"):
                self.assertTrue((base / nombre).is_file(), nombre)
            estado = (base / "state.md").read_text(encoding="utf-8")
            self.assertIn("Kind: work-state", estado)
            self.assertIn("Work: demo", estado)
            self.assertIn("Probar el canal WORK.", estado)
            self.assertIn("Kind: work-decisions", (base / "decisions.md").read_text(encoding="utf-8"))
            self.assertIn("Kind: work-assertions", (base / "assertions.md").read_text(encoding="utf-8"))

    def test_contrato_decisiones_append_only_b13_c_r3(self):
        """Verifica que decisions.md declara el núcleo semántico ratificado en B13-C-R3-001."""
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            base = crear_trabajo("demo", raiz, objetivo="o")
            decisiones_txt = (base / "decisions.md").read_text(encoding="utf-8")
            self.assertIn("Documento **append-only**: las entradas no se editan.", decisiones_txt)
            self.assertIn("Para cambiar una\ndecisión se añade otra que indique expresamente qué sustituye mediante\nel campo `Sustituye a:`.", decisiones_txt)
            self.assertIn("La entrada original permanece inmutable y no se\nmodifica ni se elimina su contenido.", decisiones_txt)
            self.assertNotIn("la anterior pasa a figurar como sustituida", decisiones_txt)

    def test_duplicado_traversal_y_escape_rechazados(self):
        with tempfile.TemporaryDirectory() as d:
            raiz = _raiz(Path(d))
            crear_trabajo("demo", raiz, objetivo="o")
            with self.assertRaises(FileExistsError):
                crear_trabajo("demo", raiz, objetivo="o")
            for malo in ("../fuera", "a/b", "/abs", "con espacios", "MAYUS"):
                with self.assertRaises((RutaInseguraError, ValueError, FileExistsError), msg=malo):
                    crear_trabajo(malo, raiz, objetivo="o")


class TestStagingExcluyeWork(unittest.TestCase):
    @staticmethod
    def _git(args, cwd):
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60
        )

    def _repo(self, base):
        repo = base / "repo"
        repo.mkdir()
        self._git(["init", "-q"], cwd=repo)
        self._git(["config", "user.email", "b11@test"], cwd=repo)
        self._git(["config", "user.name", "b11"], cwd=repo)
        return repo

    def test_comando_de_staging_excluye_work(self):
        self.assertEqual(sc._comando_stage_paso(), 'git add . ":(exclude).work"')

    def test_camino_vivo_commit_paso(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(Path(d))
            (repo / "normal.py").write_text("print(1)\n", encoding="utf-8")
            crear_trabajo("demo", repo, objetivo="probar staging")
            args = type("A", (), {"git_mensaje": "paso b11"})()
            commit = sc._commit_paso({"descripcion": "paso b11"}, args, str(repo))
            self.assertIsNotNone(commit)
            en_commit = self._git(
                ["show", "--name-only", "--pretty=format:", "HEAD"], cwd=repo
            ).stdout.split()
            self.assertIn("normal.py", en_commit)
            self.assertFalse([f for f in en_commit if f.startswith(f"{WORK_CONTAINER}/")])

    def test_camino_latente_git_commit_paso(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(Path(d))
            (repo / "normal.py").write_text("print(1)\n", encoding="utf-8")
            crear_trabajo("demo", repo, objetivo="probar staging")
            self.assertTrue(sc._git_commit_paso("paso de prueba", str(repo)))
            staged = self._git(["diff", "--cached", "--name-only"], cwd=repo).stdout.split()
            if staged:
                self.assertIn("normal.py", staged)
                self.assertFalse(
                    [f for f in staged if f.startswith(f"{WORK_CONTAINER}/")],
                    f"WORK fue stageado: {staged}",
                )
            sin_stage = self._git(["status", "--short"], cwd=repo).stdout
            self.assertIn(WORK_CONTAINER, sin_stage)


class TestProjectBoundary(unittest.TestCase):
    def _proyecto(self, base):
        proj = base / "proj"
        (proj / "src").mkdir(parents=True)
        (proj / "README.md").write_text("# Proyecto\n", encoding="utf-8")
        (proj / "CLAUDE.md").write_text("# Memoria\n", encoding="utf-8")
        (proj / "src" / "example.py").write_text("x = 1\n", encoding="utf-8")
        crear_trabajo("example", proj, objetivo="objetivo del trabajo")
        return proj

    def test_discovery_no_incluye_work(self):
        with tempfile.TemporaryDirectory() as d:
            proj = self._proyecto(Path(d))
            res = sc._tool_list_files(str(proj))
            self.assertTrue(res["ok"])
            rels = [a.replace("\\", "/") for a in res["archivos"]]
            self.assertFalse([a for a in rels if a.split("/")[0] == WORK_CONTAINER])
            self.assertIn("README.md", rels)
            self.assertIn("src/example.py", rels)

    def test_candidatos_e_indice_no_incluyen_work(self):
        with tempfile.TemporaryDirectory() as d:
            proj = self._proyecto(Path(d))
            candidatos = sc.listar_archivos_candidatos(proj, ["src", "README.md"])
            self.assertFalse([c for c in candidatos if WORK_CONTAINER in Path(c).parts])
            self.assertTrue(any(Path(c).name == "example.py" for c in candidatos))
            usados = [
                c for c in sorted(proj.rglob("*"))
                if c.is_file() and not any(p in sc.CARPETAS_IGNORADAS for p in c.parts)
            ]
            self.assertFalse([u for u in usados if WORK_CONTAINER in u.parts])
            self.assertTrue(any(u.name == "example.py" for u in usados))

    def test_lectura_explicita_sigue_funcionando(self):
        with tempfile.TemporaryDirectory() as d:
            proj = self._proyecto(Path(d))
            res = sc._tool_read_file(str(proj / WORK_CONTAINER / "example" / "state.md"))
            self.assertTrue(res["ok"])
            self.assertIn("objetivo del trabajo", res["contenido"])

    def test_plantilla_init_claude_no_incorpora_work(self):
        # B11-F: la plantilla offline de --init-claude se alimenta de
        # _tool_list_files, ya protegido; se verifica el resultado real.
        with tempfile.TemporaryDirectory() as d:
            proj = self._proyecto(Path(d))
            contenido = sc._plantilla_claude_md_basica(str(proj))
            self.assertNotIn(WORK_CONTAINER, contenido)
            self.assertNotIn("objetivo del trabajo", contenido)


if __name__ == "__main__":
    unittest.main()

