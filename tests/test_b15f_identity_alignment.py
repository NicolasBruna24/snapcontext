"""Tests B15-F — Alineación del contrato de identidad del verdict.

B15-E decidió que la identidad de un verdict es el **contenido efectivo** del
árbol verificado (tree SHA), no el par histórico `(commit, working_tree_clean)`.
Este archivo cubre lo que B15-E demostró y lo que exige el nuevo mecanismo de
observación: índice temporal, `.gitignore`, untracked y no-mutación.

Todos los experimentos corren sobre repositorios temporales: el working tree
del proyecto nunca se toca.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from work_verdict import (
    OBSOLETE,
    VALID,
    GitAnchor,
    Verdict,
    evaluar_validez_verdict,
    leer_estado_git,
)


class _RepoTemporal(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.fichero = self.repo / "modulo.py"
        self.fichero.write_text("V = 1\n", encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "modulo.py")
        self._commit("inicial")

    def _git(self, *args: str, check: bool = True) -> str:
        res = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=check,
        )
        return res.stdout.strip()

    def _commit(self, mensaje: str) -> str:
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", mensaje)
        return self._git("rev-parse", "HEAD")

    def _estado(self):
        return leer_estado_git(str(self.repo))

    def _verdict(self, estado=None):
        e = estado or self._estado()
        return Verdict(
            resultado="pasa",
            comando="pytest -q",
            scope="proyecto",
            git_anchor=GitAnchor(e.tree_sha, e.commit, e.working_tree_clean),
        )


# ===========================================================================
# §4 / §8 — El tree SHA representa contenido, no historia
# ===========================================================================
class TestTreeShaRepresentaContenido(_RepoTemporal):
    def test_modificar_un_archivo_tracked_cambia_el_tree(self):
        antes = self._estado().tree_sha
        self.fichero.write_text("V = 2\n", encoding="utf-8")
        self.assertNotEqual(self._estado().tree_sha, antes)

    def test_volver_al_contenido_anterior_restaura_el_tree(self):
        """§8: identidad = contenido, no historia."""
        original = self._estado().tree_sha
        self.fichero.write_text("V = 2\n", encoding="utf-8")
        self.fichero.write_text("V = 1\n", encoding="utf-8")
        self.assertEqual(self._estado().tree_sha, original)

    def test_es_el_working_tree_y_no_el_head(self):
        """El tree del working tree con cambios sin commitear es el del contenido."""
        original = self._estado().tree_sha
        self.fichero.write_text("V = 99\n", encoding="utf-8")
        estado = self._estado()
        self.assertNotEqual(estado.tree_sha, original)
        # Y coincide con el tree del commit que luego capture ese contenido.
        self._git("add", "modulo.py")
        self._commit("con V=99")
        self.assertEqual(self._estado().tree_sha, self._git("rev-parse", "HEAD^{tree}"))
        self.assertNotEqual(self._estado().tree_sha, original)


# ===========================================================================
# §16 / §17 / §18 — Casos críticos de B15-E sobre un repo real
# ===========================================================================
class TestCasosCriticosReales(_RepoTemporal):
    def test_caso_a_nada_cambia_es_valid(self):
        verdict = self._verdict()
        self.assertEqual(evaluar_validez_verdict(verdict, self._estado()).status, VALID)

    def test_caso_b_misma_historia_contenido_distinto_es_obsolete(self):
        """El falso positivo de B15-E: el contenido cambió, Git no lo ve."""
        verdict = self._verdict()
        self.fichero.write_text("V = 2\n", encoding="utf-8")
        despues = self._estado()
        # Commit y limpieza siguen siendo los mismos: solo cambia el contenido.
        self.assertEqual(despues.commit, verdict.git_anchor.commit)
        self.assertFalse(despues.working_tree_clean)
        self.assertEqual(evaluar_validez_verdict(verdict, despues).status, OBSOLETE)

    def test_caso_c_contenido_identico_historia_distinta_es_valid(self):
        """El falso negativo de B15-E: el contenido es idéntico."""
        self.fichero.write_text("V = 2\n", encoding="utf-8")
        verdict = self._verdict()
        commit_previo = verdict.git_anchor.commit
        self._git("add", "modulo.py")
        self._commit("con V=2")
        despues = self._estado()
        self.assertNotEqual(despues.commit, commit_previo)
        self.assertTrue(despues.working_tree_clean)
        self.assertEqual(evaluar_validez_verdict(verdict, despues).status, VALID)

    def test_revert_al_contenido_probado_recupera_valid(self):
        self.fichero.write_text("V = 2\n", encoding="utf-8")
        verdict = self._verdict()
        self.fichero.write_text("V = 3\n", encoding="utf-8")
        self.assertEqual(evaluar_validez_verdict(verdict, self._estado()).status, OBSOLETE)
        self.fichero.write_text("V = 2\n", encoding="utf-8")
        self.assertEqual(evaluar_validez_verdict(verdict, self._estado()).status, VALID)

    def test_head_avanzado_con_mismo_contenido_es_valid(self):
        """§18: consecuencia deliberada de B15-E."""
        verdict = self._verdict()
        self.fichero.write_text("V = 1\n", encoding="utf-8")
        self._git("add", "modulo.py")
        self._git(
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "vacio",
        )
        despues = self._estado()
        self.assertNotEqual(despues.commit, verdict.git_anchor.commit)
        self.assertEqual(evaluar_validez_verdict(verdict, despues).status, VALID)


# ===========================================================================
# §5 / §9 — El índice real nunca se toca; tree = working tree efectivo
# ===========================================================================
class TestIndiceRealIntacto(_RepoTemporal):
    def test_indice_real_intacto_ante_un_staged_change(self):
        """§9 completo: staged change previo + adapter → índice y disco intactos."""
        # 1-3: se genera un staged change y se registra el estado del índice.
        (self.repo / "otro.py").write_text("NUEVO = 1\n", encoding="utf-8")
        self._git("add", "otro.py")
        self.assertIn("otro.py", self._git("diff", "--cached", "--name-only"))
        indice_antes = self._git("ls-files", "--stage")

        # 4: se invoca el adapter.
        estado = self._estado()

        # 5: el índice real conserva exactamente su estado.
        self.assertEqual(self._git("ls-files", "--stage"), indice_antes)
        self.assertIn("otro.py", self._git("diff", "--cached", "--name-only"))
        # 6: el tree_sha corresponde al working tree efectivo (incluye lo staged,
        #    porque ahí coincide con el disco), no a un índice separado.
        self.assertIn("otro.py", self._git("ls-tree", "--name-only", estado.tree_sha))

    def test_el_working_tree_tampoco_se_modifica(self):
        antes = sorted(p.name for p in self.repo.iterdir())
        contenido = self.fichero.read_text(encoding="utf-8")
        self._estado()
        self.assertEqual(sorted(p.name for p in self.repo.iterdir()), antes)
        self.assertEqual(self.fichero.read_text(encoding="utf-8"), contenido)

    def test_el_tree_refleja_el_working_tree_y_no_solo_el_indice(self):
        """Archivo tracked modificado en disco pero NO staged."""
        self._git("add", "modulo.py")
        indice_previo = self._git("write-tree")
        self.fichero.write_text("V = 7\n", encoding="utf-8")  # sin git add
        estado = self._estado()
        self.assertFalse(estado.working_tree_clean)
        self.assertNotEqual(estado.tree_sha, indice_previo)  # ve el cambio en disco


# ===========================================================================
# §6 / §7 — .gitignore y archivos no ignorados
# ===========================================================================
class TestGitignoreYUntracked(_RepoTemporal):
    def test_archivo_nuevo_no_ignorado_cambia_el_tree(self):
        antes = self._estado().tree_sha
        (self.repo / "nuevo.py").write_text("NUEVO = 1\n", encoding="utf-8")
        self.assertNotEqual(self._estado().tree_sha, antes)

    def test_archivo_ignorado_no_cambia_el_tree(self):
        (self.repo / ".gitignore").write_text("ignorado/\n*.log\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._commit("gitignore")
        antes = self._estado().tree_sha
        (self.repo / "debug.log").write_text("ruido\n", encoding="utf-8")
        (self.repo / "ignorado").mkdir()
        (self.repo / "ignorado" / "basura.py").write_text("X = 1\n", encoding="utf-8")
        self.assertEqual(self._estado().tree_sha, antes)

    def test_archivo_ignorado_una_vez_dejado_de_ignorarse_si_afecta(self):
        """Git es la autoridad: si deja de estar ignorado, entra al árbol."""
        (self.repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
        self._git("add", ".gitignore")
        self._commit("gitignore")
        (self.repo / "notas.log").write_text("x\n", encoding="utf-8")
        antes = self._estado().tree_sha
        (self.repo / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        self.assertNotEqual(self._estado().tree_sha, antes)


# ===========================================================================
# §19 — El evaluador sigue puro
# ===========================================================================
class TestPurezaDelEvaluador(unittest.TestCase):
    def test_no_toca_git_ni_filesystem(self):
        import ast

        raiz = Path(__file__).resolve().parents[1]
        arbol = ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))
        funcion = next(
            n
            for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name == "evaluar_validez_verdict"
        )
        cuerpo = list(funcion.body)
        if cuerpo and isinstance(cuerpo[0], ast.Expr):
            cuerpo = cuerpo[1:]  # docstring
        called = {
            n.func.id
            for s in cuerpo
            for n in ast.walk(s)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        # Solo comprobación de tipos y construcción del resultado.
        self.assertEqual(called, {"ValidityResult", "isinstance", "TypeError"})
        for prohibido in (
            "leer_estado_git",
            "_tree_sha_working_tree",
            "_correr_git",
            "open",
            "read_text",
            "subprocess",
            "run",
        ):
            with self.subTest(llamada=prohibido):
                self.assertNotIn(prohibido, called)

    def test_no_consulta_metadatos_para_juzgar(self):
        """B15-F §13: el evaluador solo lee `tree_sha` del anchor."""
        import ast

        raiz = Path(__file__).resolve().parents[1]
        arbol = ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))
        funcion = next(
            n
            for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name == "evaluar_validez_verdict"
        )
        cuerpo = ast.dump(funcion)
        # `.tree_sha` debe aparecer; los metadatos no se consultan para decidir.
        self.assertIn("tree_sha", cuerpo)
        for metadato in ("working_tree_clean", "commit"):
            with self.subTest(campo=metadato):
                self.assertNotIn(f"attr='{metadato}'", cuerpo)
