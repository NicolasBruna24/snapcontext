"""B15-H — productor canónico de ``Verdict``.

Cubre el contrato decidido en
`docs/B15-G-canonical-verification-producer-decision.md`:

* construye ``Verdict`` desde un ``CurrentGitState`` ya observado;
* usa ``estado.tree_sha`` **tal cual** (no hay segunda captura del árbol);
* **no** ejecuta Git y **no** llama a ``leer_estado_git``;
* **no** evalúa validez (eso es ``evaluar_validez_verdict``);
* y compone correctamente con el evaluador de B15-C/B15-F.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import unittest
from unittest import mock

from work_verdict import (
    OBSOLETE,
    RESULTADO_FALLA,
    RESULTADO_PASA,
    SCOPE_NO_DEFINIDO,
    VALID,
    CurrentGitState,
    GitAnchor,
    ValidityResult,
    Verdict,
    evaluar_validez_verdict,
    producir_verdict,
)

TREE = "9f1c0d3ab7e2468510db92c8f3a4b5c6d7e8f90"
COMMIT = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
OTRO_TREE = "deadbeef00000000000000000000000000000000"
OTRO_COMMIT = "ffff0000ffff0000ffff0000ffff0000ffff0000"


def estado_arbol(tree_sha: str = TREE, commit: str = COMMIT, clean: bool = True) -> CurrentGitState:
    return CurrentGitState(tree_sha=tree_sha, commit=commit, working_tree_clean=clean)


class TestConstruccionBasica(unittest.TestCase):
    """§14.1 — construcción correcta."""

    def test_anchor_copia_los_tres_componentes(self):
        v = producir_verdict(True, "pytest", estado_arbol())
        self.assertIsInstance(v, Verdict)
        self.assertIsInstance(v.git_anchor, GitAnchor)
        self.assertEqual(v.git_anchor.tree_sha, TREE)
        self.assertEqual(v.git_anchor.commit, COMMIT)
        self.assertIs(v.git_anchor.working_tree_clean, True)

    def test_no_calcula_un_segundo_tree_sha(self):
        """§5 — el tree_sha es exactamente el recibido."""
        v = producir_verdict(True, "pytest", estado_arbol(tree_sha=OTRO_TREE))
        self.assertEqual(v.git_anchor.tree_sha, OTRO_TREE)

    def test_comando_se_conserva_sin_normalizar(self):
        v = producir_verdict(True, "pytest -q tests/unit", estado_arbol())
        self.assertEqual(v.comando, "pytest -q tests/unit")

    def test_exito_true_produce_pasa(self):
        v = producir_verdict(True, "pytest", estado_arbol())
        self.assertEqual(v.resultado, RESULTADO_PASA)
        self.assertEqual(v.resultado, "pasa")

    def test_orden_de_argumentos_y_devolucion(self):
        v = producir_verdict(False, "pytest", estado_arbol())
        self.assertIsInstance(v, Verdict)


class TestFalloDeVerificacion(unittest.TestCase):
    """§14.2 — ``exito=False``."""

    def test_fallo_preserva_anchor(self):
        v = producir_verdict(False, "pytest", estado_arbol())
        self.assertEqual(v.resultado, RESULTADO_FALLA)
        self.assertEqual(v.git_anchor.tree_sha, TREE)
        self.assertEqual(v.git_anchor.commit, COMMIT)
        self.assertIs(v.git_anchor.working_tree_clean, True)

    def test_fallo_no_es_obsoleto(self):
        """Un fallo es un resultado, no una obsolescencia."""
        v = producir_verdict(False, "pytest", estado_arbol())
        self.assertNotEqual(v.resultado, OBSOLETE)
        self.assertNotEqual(v.resultado, "OBSOLETE")

    def test_exito_se_convierte_con_bool(self):
        v = producir_verdict(1, "pytest", estado_arbol())
        self.assertEqual(v.resultado, RESULTADO_PASA)


class TestWorkingTreeSucio(unittest.TestCase):
    """§14.3 — metadata sucia, sin evaluar validez."""

    def test_conserva_working_tree_clean_false(self):
        v = producir_verdict(True, "pytest", estado_arbol(clean=False))
        self.assertIs(v.git_anchor.working_tree_clean, False)
        self.assertEqual(v.git_anchor.tree_sha, TREE)
        self.assertEqual(v.git_anchor.commit, COMMIT)

    def test_sucio_no_produce_obsoleto(self):
        v = producir_verdict(True, "pytest", estado_arbol(clean=False))
        self.assertNotIn("obsolet", v.resultado.lower())

    def test_anchor_de_sucio_sigue_siendo_igual_al_estado(self):
        e = estado_arbol(clean=False)
        v = producir_verdict(True, "pytest", e)
        self.assertEqual(v.git_anchor.tree_sha, e.tree_sha)
        self.assertEqual(v.git_anchor.commit, e.commit)
        self.assertEqual(v.git_anchor.working_tree_clean, e.working_tree_clean)


class TestCommitDistintoNoObsoleto(unittest.TestCase):
    """§14.4 / §14.5 — el productor NO es el evaluador."""

    def test_commit_distinto_se_conserva_como_metadata(self):
        e = estado_arbol(tree_sha=TREE, commit=OTRO_COMMIT, clean=True)
        v = producir_verdict(True, "pytest", e)
        self.assertEqual(v.git_anchor.commit, OTRO_COMMIT)
        self.assertEqual(v.git_anchor.tree_sha, TREE)

    def test_mismo_tree_distinta_historia_conserva_anchor(self):
        v = producir_verdict(
            True, "pytest", estado_arbol(tree_sha=TREE, commit=COMMIT, clean=False)
        )
        self.assertEqual(v.git_anchor.tree_sha, TREE)
        self.assertEqual(v.git_anchor.commit, COMMIT)
        self.assertIs(v.git_anchor.working_tree_clean, False)
        # La validez no se calcula aquí: no se recibe el estado actual.
        self.assertFalse(hasattr(v, "validity"))
        self.assertFalse(hasattr(v, "obsolete"))

    def test_no_consulta_el_estado_actual_para_juzgar(self):
        """§14.9 — el productor no recibe ni compara el estado actual."""
        parametros = list(inspect.signature(producir_verdict).parameters)
        self.assertNotIn("current_git_state", parametros)
        self.assertEqual(
            [p for p in parametros if not p.startswith("_")],
            ["exito", "comando", "estado", "scope", "autor", "instante"],
        )


class TestMetadataOpcional(unittest.TestCase):
    """§14.6 / §14.7 — metadata."""

    def test_sin_metadata_se_construye(self):
        v = producir_verdict(True, "pytest", estado_arbol())
        self.assertIsNone(v.autor)
        self.assertIsNone(v.instante)

    def test_scope_none_usa_constante_explicita(self):
        """``Verdict.scope`` es obligatorio y no vacío (B15-B)."""
        v = producir_verdict(True, "pytest", estado_arbol(), scope=None)
        self.assertEqual(v.scope, SCOPE_NO_DEFINIDO)
        self.assertTrue(v.scope.strip())

    def test_scope_explicito_llega_intacto(self):
        v = producir_verdict(True, "pytest", estado_arbol(), scope="proyecto completo")
        self.assertEqual(v.scope, "proyecto completo")

    def test_scope_se_recorta_como_hace_verdict(self):
        """No se inventa normalización: se delega en ``Verdict``."""
        v = producir_verdict(True, "pytest", estado_arbol(), scope="  src  ")
        self.assertEqual(v.scope, "src")

    def test_scope_vacio_rechazado_por_validacion_existente(self):
        with self.assertRaises(ValueError):
            producir_verdict(True, "pytest", estado_arbol(), scope="   ")

    def test_autor_e_instante_llegan_intactos(self):
        v = producir_verdict(
            True,
            "pytest",
            estado_arbol(),
            autor="agente-tester",
            instante="2026-09-27T10:00:00Z",
        )
        self.assertEqual(v.autor, "agente-tester")
        self.assertEqual(v.instante, "2026-09-27T10:00:00Z")

    def test_autor_e_instante_no_se_inventan(self):
        """§9 / §10 — no se infieren de Git, del sistema ni del reloj."""
        v = producir_verdict(True, "pytest", estado_arbol())
        self.assertIsNone(v.autor)
        self.assertIsNone(v.instante)

    def test_scope_no_se_infiere_del_comando(self):
        """§8 — B15-G dejó abierta la semántica de pytest/ruff/mypy."""
        v = producir_verdict(True, "pytest -q", estado_arbol())
        self.assertEqual(v.scope, SCOPE_NO_DEFINIDO)


class TestNoConsultaGit(unittest.TestCase):
    """§4 / §14.8 — el productor no habla con Git."""

    def test_leer_estado_git_no_se_llama(self):
        import work_verdict

        with mock.patch.object(
            work_verdict, "leer_estado_git", side_effect=AssertionError("Git")
        ) as falso:
            v = producir_verdict(True, "pytest", estado_arbol())
        falso.assert_not_called()
        self.assertIsInstance(v, Verdict)

    def test_correr_git_no_se_llama(self):
        """La capa baja de Git tampoco: ni directa ni indirectamente."""
        import work_verdict

        with mock.patch.object(
            work_verdict, "_correr_git", side_effect=AssertionError("Git")
        ) as falso:
            with mock.patch.object(
                work_verdict,
                "_tree_sha_working_tree",
                side_effect=AssertionError("Git"),
            ) as arbol:
                producir_verdict(True, "pytest", estado_arbol())
        falso.assert_not_called()
        arbol.assert_not_called()

    def test_ast_no_invoca_el_evaluador(self):
        """§13 — el productor no llama a ``evaluar_validez_verdict``."""
        arbol = ast.parse(inspect.getsource(producir_verdict))
        llamadas = {
            n.func.id
            for n in ast.walk(arbol)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        llamadas |= {
            n.func.attr
            for n in ast.walk(arbol)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        # El productor solo construye (GitAnchor, Verdict), comprueba el tipo
        # de entrada y lanza TypeError. Nada más.
        self.assertEqual(llamadas, {"GitAnchor", "Verdict", "isinstance", "TypeError"})

    def test_ast_no_consulta_observacion_de_git(self):
        """Inspección AST equivalente: ninguna llamada al adapter.

        Se analiza el **código**, no el texto: el docstring menciona esos
        nombres a propósito para explicar la frontera, y eso no es una llamada.
        """
        arbol = ast.parse(inspect.getsource(producir_verdict))
        # Se descarta el docstring: solo interesan los nodos ejecutables.
        cuerpo = [
            n
            for n in ast.walk(arbol)
            if not (isinstance(n, ast.Constant) and isinstance(n.value, str))
        ]
        nombres = (
            {n.id for n in cuerpo if isinstance(n, ast.Name)}
            | {n.attr for n in cuerpo if isinstance(n, ast.Attribute)}
            | {
                n.func.id
                for n in cuerpo
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
        )
        for prohibido in (
            "leer_estado_git",
            "_correr_git",
            "_tree_sha_working_tree",
            "subprocess",
            "evaluar_validez_verdict",
            "actualizar_estado",
            "leer_estado",
            "open",
            "os",
            "pathlib",
        ):
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, nombres)

    def test_no_abre_ficheros(self):
        with mock.patch("builtins.open", side_effect=AssertionError("fichero")):
            producir_verdict(True, "pytest", estado_arbol())


class TestInmutabilidad(unittest.TestCase):
    """§14.10 — no se introduce mutabilidad."""

    def test_verdict_anchor_y_estado_son_frozen(self):
        for dc in (Verdict, GitAnchor, CurrentGitState):
            with self.subTest(dc=dc.__name__):
                self.assertTrue(dc.__dataclass_params__.frozen)

    def test_el_verdict_producido_no_se_puede_mutar(self):
        v = producir_verdict(True, "pytest", estado_arbol())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            v.resultado = "falla"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            v.git_anchor.tree_sha = OTRO_TREE

    def test_anchor_construido_por_el_productor_es_frozen(self):
        v = producir_verdict(True, "pytest", estado_arbol())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            v.git_anchor.commit = OTRO_COMMIT

    def test_anchor_igual_auno_construido_a_mano(self):
        v = producir_verdict(True, "pytest", estado_arbol())
        self.assertEqual(v.git_anchor, GitAnchor(TREE, COMMIT, True))


class TestValidacionDeEntradas(unittest.TestCase):
    """§11 — se reutilizan las validaciones existentes."""

    def test_estado_debe_ser_current_git_state(self):
        for malo in (None, "T", {"tree_sha": TREE}, GitAnchor(TREE, COMMIT, True)):
            with self.subTest(malo=malo):
                with self.assertRaises(TypeError):
                    producir_verdict(True, "pytest", malo)

    def test_estado_sin_tree_sha_rechazado(self):
        """Un anchor sin identificador no produce un Verdict silenciosamente."""
        with self.assertRaises(ValueError):
            CurrentGitState(tree_sha="   ", commit=COMMIT, working_tree_clean=True)

    def test_estado_con_tree_sha_none_rechazado(self):
        with self.assertRaises(ValueError):
            CurrentGitState(tree_sha=None, commit=COMMIT, working_tree_clean=True)

    def test_comando_vacio_rechazado_por_verdict(self):
        with self.assertRaises(ValueError):
            producir_verdict(True, "   ", estado_arbol())

    def test_working_tree_clean_se_normaliza_a_bool(self):
        v = producir_verdict(True, "pytest", CurrentGitState(TREE, COMMIT, 1))
        self.assertIs(v.git_anchor.working_tree_clean, True)


class TestComposicionConElEvaluador(unittest.TestCase):
    """§15 — las dos piezas se componen; no se fusionan."""

    def test_mismo_tree_distinto_commit_es_valid(self):
        anchor = estado_arbol(tree_sha=TREE, commit=COMMIT, clean=False)
        v = producir_verdict(True, "pytest", anchor)
        actual = estado_arbol(tree_sha=TREE, commit=OTRO_COMMIT, clean=True)
        r = evaluar_validez_verdict(v, actual)
        self.assertIsInstance(r, ValidityResult)
        self.assertEqual(r.status, VALID)

    def test_tree_distinto_es_obsolete(self):
        v = producir_verdict(True, "pytest", estado_arbol(tree_sha=TREE))
        r = evaluar_validez_verdict(v, estado_arbol(tree_sha=OTRO_TREE, commit=COMMIT, clean=False))
        self.assertEqual(r.status, OBSOLETE)

    def test_verdict_fallido_tambien_es_valid_si_el_arbol_no_cambio(self):
        """La validez y el resultado son ortogonales."""
        v = producir_verdict(False, "pytest", estado_arbol())
        r = evaluar_validez_verdict(v, estado_arbol())
        self.assertEqual(r.status, VALID)
        self.assertEqual(v.resultado, RESULTADO_FALLA)

    def test_la_decision_proviene_del_evaluador(self):
        """El productor no conoce el evaluador: se le pasa por separado."""
        v = producir_verdict(True, "pytest", estado_arbol())
        nombres = {f.name for f in dataclasses.fields(v)}
        self.assertNotIn("validity", nombres)
        self.assertNotIn("obsolete", nombres)

    def test_ciclo_verificar_evaluar_repetido(self):
        """El tree SHA capturado manda: repetir la verificación es válido."""
        e = estado_arbol()
        for _ in range(3):
            v = producir_verdict(True, "pytest", e)
            self.assertEqual(evaluar_validez_verdict(v, e).status, VALID)


if __name__ == "__main__":
    unittest.main()
