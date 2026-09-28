"""Tests B15-B — `GitAnchor`, `Verdict`, `CurrentGitState` y adapter de Git.

Materializa las dos entradas definidas en B15-A. NO se prueba (aún) ninguna
evaluación de validez: `VALID`/`OBSOLETE`/`UNDETERMINED` pertenecen a B15-C.

Los experimentos Git corren SIEMPRE contra repositorios temporales creados por
el test. Nunca se toca el working tree del proyecto.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exceptions import EstadoGitIndisponibleError
from work_verdict import CurrentGitState, GitAnchor, Verdict, leer_estado_git

COMMIT_EJEMPLO = "0a3201b00ee251aaf4c5d73df409bb370c5741a6"
TREE_EJEMPLO = "56f83fd60ab3c9d0e1f2a3b4c5d6e7f8091a2b3c"
COMMIT_EJEMPLO_2 = "357faa00ee251aaf4c5d73df409bb370c5741a6"


# ===========================================================================
# Modelos — datos puros
# ===========================================================================
class TestGitAnchor(unittest.TestCase):
    def test_creacion_valida(self):
        anchor = GitAnchor(
            tree_sha=TREE_EJEMPLO, commit=COMMIT_EJEMPLO, working_tree_clean=True
        )
        self.assertEqual(anchor.tree_sha, TREE_EJEMPLO)
        self.assertEqual(anchor.commit, COMMIT_EJEMPLO)
        self.assertTrue(anchor.working_tree_clean)

    def test_es_inmutable(self):
        anchor = GitAnchor(TREE_EJEMPLO, COMMIT_EJEMPLO, True)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            anchor.commit = "otro"  # type: ignore[misc]

    def test_rechaza_identificadores_vacios(self):
        """B15-F: `tree_sha` es obligatorio y `commit` conserva su validación."""
        for vacio in ("", "   ", None):
            with self.subTest(valor=vacio):
                with self.assertRaises(ValueError):
                    GitAnchor(vacio, COMMIT_EJEMPLO, True)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    GitAnchor(TREE_EJEMPLO, vacio, True)  # type: ignore[arg-type]

    def test_representacion_estable(self):
        a = GitAnchor(TREE_EJEMPLO, COMMIT_EJEMPLO, True)
        b = GitAnchor(TREE_EJEMPLO, COMMIT_EJEMPLO, True)
        self.assertEqual(a, b)
        self.assertEqual(repr(a), repr(b))
        # Ambos identificadores se normalizan (sin espacios envolventes).
        self.assertEqual(
            GitAnchor(f"  {TREE_EJEMPLO}  ", f"  {COMMIT_EJEMPLO}  ", True).tree_sha,
            TREE_EJEMPLO,
        )

    def test_campos_esperados(self):
        """B15-E: `tree_sha` identidad; `commit` y `clean` metadatos."""
        nombres = {campo.name for campo in dataclasses.fields(GitAnchor)}
        self.assertEqual(nombres, {"tree_sha", "commit", "working_tree_clean"})
        for prohibido in ("rama", "branch", "git_base", "git_actual", "git_pr_issue"):
            with self.subTest(campo=prohibido):
                self.assertNotIn(prohibido, nombres)


class TestCurrentGitState(unittest.TestCase):
    def test_creacion_valida(self):
        estado = CurrentGitState(
            tree_sha=TREE_EJEMPLO, commit=COMMIT_EJEMPLO, working_tree_clean=True
        )
        self.assertEqual(estado.tree_sha, TREE_EJEMPLO)
        self.assertEqual(estado.commit, COMMIT_EJEMPLO)
        self.assertTrue(estado.working_tree_clean)

    def test_es_inmutable(self):
        estado = CurrentGitState(TREE_EJEMPLO, COMMIT_EJEMPLO, True)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            estado.working_tree_clean = False  # type: ignore[misc]

    def test_rechaza_identificadores_vacios(self):
        for vacio in ("", "  ", None):
            with self.subTest(valor=vacio):
                with self.assertRaises(ValueError):
                    CurrentGitState(vacio, COMMIT_EJEMPLO, False)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    CurrentGitState(TREE_EJEMPLO, vacio, False)  # type: ignore[arg-type]

    def test_no_contiene_semantica_de_validity(self):
        nombres = {campo.name for campo in dataclasses.fields(CurrentGitState)}
        self.assertEqual(nombres, {"tree_sha", "commit", "working_tree_clean"})
        for prohibido in ("verdict", "validity", "valid", "obsolete", "obsoleto", "rama"):
            with self.subTest(campo=prohibido):
                self.assertNotIn(prohibido, nombres)


class TestVerdict(unittest.TestCase):
    def _verdict(self, **extra):
        datos = {
            "resultado": "pasa",
            "comando": "python3 -m pytest tests/ -q",
            "scope": "proyecto completo",
            "git_anchor": GitAnchor(TREE_EJEMPLO, COMMIT_EJEMPLO, True),
            "autor": "Agente B",
            "instante": "2026-09-27T01:00:00Z",
        }
        datos.update(extra)
        return Verdict(**datos)  # type: ignore[arg-type]

    def test_creacion_valida(self):
        v = self._verdict()
        self.assertEqual(v.resultado, "pasa")
        self.assertEqual(v.comando, "python3 -m pytest tests/ -q")
        self.assertEqual(v.scope, "proyecto completo")
        self.assertIsInstance(v.git_anchor, GitAnchor)
        self.assertEqual(v.git_anchor.tree_sha, TREE_EJEMPLO)

    def test_contiene_anchor_estructurado(self):
        """B15-F: el anchor lleva identidad (`tree_sha`) y metadatos."""
        v = self._verdict()
        self.assertEqual(v.git_anchor.tree_sha, TREE_EJEMPLO)
        self.assertEqual(v.git_anchor.commit, COMMIT_EJEMPLO)
        self.assertTrue(v.git_anchor.working_tree_clean)

    def test_autor_e_instante_son_opcionales(self):
        v = Verdict("pasa", "pytest", "src", GitAnchor(TREE_EJEMPLO, COMMIT_EJEMPLO, True))
        self.assertIsNone(v.autor)
        self.assertIsNone(v.instante)

    def test_es_inmutable(self):
        v = self._verdict()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            v.resultado = "falla"  # type: ignore[misc]

    def test_no_contiene_validity_ni_obsolete(self):
        nombres = {campo.name for campo in dataclasses.fields(Verdict)}
        for prohibido in ("validity", "valid", "obsolete", "obsoleto", "estado",
                          "current_git_state", "git_state"):
            with self.subTest(campo=prohibido):
                self.assertNotIn(prohibido, nombres)

    def test_rechaza_campos_vacios(self):
        for campo in ("resultado", "comando", "scope"):
            with self.subTest(campo=campo):
                with self.assertRaises(ValueError):
                    self._verdict(**{campo: "   "})

    def test_exige_un_gitanchor(self):
        with self.assertRaises(TypeError):
            Verdict("pasa", "pytest", "src", "no-soy-anchor")  # type: ignore[arg-type]

    def test_no_depende_de_state_md(self):
        """El Verdict se construye con datos, no parseando Markdown (§6)."""
        import ast

        raiz = Path(__file__).resolve().parents[1]
        arbol = ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))
        nombres = {
            n.id for n in ast.walk(arbol) if isinstance(n, ast.Name)
        } | {n.attr for n in ast.walk(arbol) if isinstance(n, ast.Attribute)}
        importados = {
            (n.module or "")
            for n in ast.walk(arbol)
            if isinstance(n, ast.ImportFrom)
        } | {
            a.name for n in ast.walk(arbol) if isinstance(n, ast.Import)
            for a in n.names
        }
        # No importa el canal WORK ni toca documentos.
        self.assertNotIn("work_context", importados)
        self.assertNotIn("read_text", nombres)
        self.assertNotIn("state.md", nombres)


# ===========================================================================
# Adapter de Git — repositorio temporal controlado
# ===========================================================================
class _RepoTemporal(unittest.TestCase):
    """Repositorio Git aislado. El test NUNCA toca el proyecto real."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.fichero = self.repo / "modulo.py"
        self.fichero.write_text("VERSION = 1\n", encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "modulo.py")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "inicial")
        self.head = self._git("rev-parse", "HEAD").strip()

    def _git(self, *args: str) -> str:
        res = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout

    def _estado(self):
        return leer_estado_git(str(self.repo))


class TestAdapterGit(_RepoTemporal):
    def test_1_obtiene_head(self):
        estado = self._estado()
        self.assertEqual(estado.commit, self.head)

    def test_2_head_coincide_con_el_commit_esperado(self):
        self.assertEqual(self._estado().commit, self._git("rev-parse", "HEAD").strip())

    def test_3_working_tree_limpio(self):
        self.assertTrue(self._estado().working_tree_clean)

    def test_4_5_6_modificar_y_volver_a_consultar(self):
        antes = self._estado()
        self.fichero.write_text("VERSION = 2\n", encoding="utf-8")
        despues = self._estado()
        self.assertEqual(despues.commit, antes.commit)      # mismo commit
        self.assertFalse(despues.working_tree_clean)        # ahora sucio

    def test_7_8_restaurar_sin_comandos_destructivos(self):
        """Se restaura el fichero con Python, no con `git checkout`/`restore`."""
        original = self.fichero.read_text(encoding="utf-8")
        self.fichero.write_text("VERSION = 99\n", encoding="utf-8")
        self.assertFalse(self._estado().working_tree_clean)
        # Restauración por escritura directa del contenido original.
        self.fichero.write_text(original, encoding="utf-8")
        self.assertTrue(self._estado().working_tree_clean)

    def test_repositorio_sin_commits_reporta_head_no_disponible(self):
        vacio = Path(self._tmp.name) / "vacio"
        vacio.mkdir()
        subprocess.run(["git", "-C", str(vacio), "init", "-q"], check=True)
        with self.assertRaises(EstadoGitIndisponibleError) as ctx:
            leer_estado_git(str(vacio))
        self.assertEqual(ctx.exception.motivo, "head_no_disponible")


class TestErroresAdapter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_directorio_inexistente(self):
        with self.assertRaises(EstadoGitIndisponibleError) as ctx:
            leer_estado_git(str(self.base / "no-existe"))
        self.assertEqual(ctx.exception.motivo, "directorio_inexistente")

    def test_no_es_repositorio(self):
        plano = self.base / "plano"
        plano.mkdir()
        with self.assertRaises(EstadoGitIndisponibleError) as ctx:
            leer_estado_git(str(plano))
        self.assertEqual(ctx.exception.motivo, "sin_repositorio")

    def test_git_no_disponible(self):
        repo = self.base / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        (repo / "a.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
             "commit", "-q", "-m", "i"],
            check=True,
        )
        import unittest.mock as mock

        import work_verdict

        with mock.patch.object(
            work_verdict, "_correr_git", side_effect=EstadoGitIndisponibleError("x", "git_no_disponible")
        ):
            with self.assertRaises(EstadoGitIndisponibleError):
                leer_estado_git(str(repo))

    def test_es_runtime_y_no_valueerror(self):
        """ distinguishable de entrada inválida / documento inválido."""
        self.assertTrue(issubclass(EstadoGitIndisponibleError, RuntimeError))
        self.assertFalse(issubclass(EstadoGitIndisponibleError, ValueError))


# ===========================================================================
# B13-B reproducido — el adapter observa; NO juzga
# ===========================================================================
class TestEvidenciaB13B(_RepoTemporal):
    def test_mismo_commit_y_cambio_de_limpieza(self):
        """(X, True) → (X, False). B13-B, reproducido automáticamente."""
        estado_a = self._estado()
        self.fichero.write_text("VERSION = 2\n", encoding="utf-8")
        estado_b = self._estado()

        self.assertEqual(estado_a.commit, estado_b.commit)
        self.assertNotEqual(estado_a.working_tree_clean, estado_b.working_tree_clean)
        self.assertTrue(estado_a.working_tree_clean)
        self.assertFalse(estado_b.working_tree_clean)

    def test_el_anchor_se_puede_construir_desde_la_observacion(self):
        """El anchor de B13-B es (commit, limpieza) — se comprueba, no se juzga."""
        estado = self._estado()
        anchor = GitAnchor(estado.tree_sha, estado.commit, estado.working_tree_clean)
        self.assertEqual(anchor.tree_sha, estado.tree_sha)
        self.assertEqual(anchor.commit, self.head)
        self.assertTrue(anchor.working_tree_clean)

    def test_no_se_declara_validez(self):
        """B15-B no juzga: los únicos datos son commit y limpieza."""
        for nombre in ("validity", "valid", "obsolete", "obsoleto", "estado_validez"):
            with self.subTest(campo=nombre):
                self.assertFalse(hasattr(self._estado(), nombre))

    def test_head_avanzado_se_reporta_sin_juzgar(self):
        """§13: si HEAD avanza, el adapter solo reporta; no decide nada."""
        antes = self._estado()
        self.fichero.write_text("VERSION = 3\n", encoding="utf-8")
        self._git("add", "modulo.py")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "segundo")
        despues = self._estado()
        # El commit cambió: se reporta. Ningún juicio de validez aquí.
        self.assertNotEqual(despues.commit, antes.commit)
        self.assertEqual(despues.commit, self._git("rev-parse", "HEAD").strip())
        self.assertTrue(despues.working_tree_clean)


# ===========================================================================
# Fronteras
# ===========================================================================
class TestFronteras(unittest.TestCase):
    def test_el_adapter_no_ejecuta_comandos_mutantes_del_repositorio(self):
        """B15-F: `git add -A` sí se usa, pero SOLO sobre un índice temporal.

        Ningún otro comando mutante se permite. `add` queda exceptuado porque
        B15-F §4 lo exige para calcular el tree SHA del working tree; que no
        toque el índice real se verifica en `tests/test_b15f_identity_alignment.py`.
        """
        import ast

        raiz = Path(__file__).resolve().parents[1]
        arbol = ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))
        mutantes = ("commit", "reset", "restore", "checkout", "clean", "rm", "push", "stash")
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Tuple):
                elementos = [e.value for e in nodo.elts if isinstance(e, ast.Constant)]
                if elementos and elementos[0] == "git":
                    with self.subTest(comando=elementos):
                        self.assertNotIn(elementos[1], mutantes)

    def test_los_modelos_no_ejecutan_git(self):
        """`GitAnchor`, `Verdict` y `CurrentGitState` son datos puros."""
        import ast

        raiz = Path(__file__).resolve().parents[1]
        arbol = ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))
        clases = {
            n.name: n for n in arbol.body if isinstance(n, ast.ClassDef)
        }
        for nombre in ("GitAnchor", "Verdict", "CurrentGitState"):
            cuerpo = clases[nombre].body
            metodos = [n for n in cuerpo if isinstance(n, ast.FunctionDef)]
            # Solo `__post_init__` (validación); ningún método con lógica de E/S.
            for metodo in metodos:
                with self.subTest(clase=nombre, metodo=metodo.name):
                    self.assertIn(metodo.name, {"__post_init__", "__init__", "__repr__",
                                               "__eq__", "__hash__"})

    def test_el_modulo_no_importa_work_context_ni_snapcontext(self):
        import ast

        raiz = Path(__file__).resolve().parents[1]
        arbol = ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))
        importados = {
            (n.module or "") for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)
        } | {
            a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
        }
        for prohibido in ("work_context", "snapcontext", "mcp_tools", "react_agent"):
            with self.subTest(modulo=prohibido):
                self.assertNotIn(prohibido, importados)

    def test_el_adapter_no_evalua_verdicts_aunque_el_evaluador_exista(self):
        """B15-C añadió el evaluador; el adapter sigue sin evaluar nada.

        Antes de B15-C este test comprobaba que el evaluador no existía. Ese
        invariante quedó **sustituido deliberadamente** por B15-C, que lo añade
        como función pura separada. Lo que este test sigue garantizando —su
        intención real— es que `leer_estado_git()` **no** decide validez.
        """
        import ast

        import work_verdict

        # El adapter no llama al evaluador ni menciona estados de validez.
        arbol = ast.parse((Path(__file__).resolve().parents[1] / "work_verdict.py")
                          .read_text(encoding="utf-8"))
        adapter = next(
            n for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name == "leer_estado_git"
        )
        cuerpo = ast.dump(adapter)
        for prohibido in ("evaluar_validez_verdict", "ValidityResult", "VALID", "OBSOLETE"):
            with self.subTest(simbolo=prohibido):
                self.assertNotIn(prohibido, cuerpo)
        # Y lo que devuelve sigue siendo solo el hecho observado.
        estado = work_verdict.leer_estado_git
        self.assertTrue(callable(estado))

    def test_el_adapter_no_escribe_documentos_del_canal(self):
        import ast

        raiz = Path(__file__).resolve().parents[1]
        arbol = ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))
        atributos = {
            n.attr for n in ast.walk(arbol) if isinstance(n, ast.Attribute)
        }
        for prohibido in ("write_text", "actualizar_estado", "escribir_documento_trabajo"):
            with self.subTest(atributo=prohibido):
                self.assertNotIn(prohibido, atributos)


if __name__ == "__main__":
    unittest.main()
