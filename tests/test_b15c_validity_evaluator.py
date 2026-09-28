"""Tests B15-C — Evaluador puro de validez de verdicts.

Cierra la semántica definida en B15-A §7 sobre las entradas de B15-B. La
matriz de §13 se cubre de forma exhaustiva. No se crea ningún productor
artificial de `Verdict`: los tests construyen el dato directamente, que es
justamente lo que permite probar toda la semántica sin repositorio real.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import work_verdict
from work_verdict import (
    OBSOLETE,
    VALID,
    CurrentGitState,
    GitAnchor,
    Verdict,
    ValidityResult,
    evaluar_validez_verdict,
    leer_estado_git,
)

X = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"   # commit
Y = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"   # commit
T = "1111111111111111111111111111111111111111"   # tree (contenido probado)
U = "2222222222222222222222222222222222222222"   # tree (contenido distinto)


def _verdict(commit: str, limpio: bool, tree: str = T) -> Verdict:
    return Verdict(
        resultado="pasa",
        comando="python3 -m pytest tests/ -q",
        scope="proyecto completo",
        git_anchor=GitAnchor(tree_sha=tree, commit=commit, working_tree_clean=limpio),
        autor="Agente B",
        instante="2026-09-27T01:00:00Z",
    )


def _estado(commit: str, limpio: bool, tree: str = T) -> CurrentGitState:
    return CurrentGitState(tree_sha=tree, commit=commit, working_tree_clean=limpio)


# ===========================================================================
# §13 / §15 — Matriz basada en tree SHA (B15-E)
# ===========================================================================
# (anchor_commit, anchor_limpio, anchor_tree, actual_commit, actual_limpio,
#  actual_tree, esperado)
MATRIZ = [
    # Mismo contenido: VALID, sea cual sea la historia Git.
    (X, True,  T, X, True,  T, VALID),
    (X, False, T, X, False, T, VALID),
    (X, False, T, Y, True,  T, VALID),   # caso C/D: commit y clean cambian
    (Y, True,  T, X, False, T, VALID),   # caso F: historia al revés
    # Contenido distinto: OBSOLETE, sea cual sea el resto.
    (X, True,  T, X, True,  U, OBSOLETE),
    (X, False, T, X, False, U, OBSOLETE),  # caso B: el falso positivo de B15-E
    (X, False, T, Y, True,  U, OBSOLETE),
    (X, True,  T, Y, False, U, OBSOLETE),
]


class TestMatrizDeValidez(unittest.TestCase):
    def test_matriz_completa(self):
        for ac, al, at, cc, cl, ct, esperado in MATRIZ:
            with self.subTest(
                anchor=f"({ac[:1]},{al},{at[0]})", actual=f"({cc[:1]},{cl},{ct[0]})"
            ):
                resultado = evaluar_validez_verdict(
                    _verdict(ac, al, at), _estado(cc, cl, ct)
                )
                self.assertEqual(resultado.status, esperado)

    def test_la_regla_es_unicamente_la_igualdad_de_contenido(self):
        """VALID ⇔ tree_sha igual. `commit` y `working_tree_clean` no participan."""
        for ac, al, at, cc, cl, ct, _ in MATRIZ:
            with self.subTest(anchor=at[0], actual=ct[0]):
                esperado = VALID if at == ct else OBSOLETE
                self.assertEqual(
                    evaluar_validez_verdict(
                        _verdict(ac, al, at), _estado(cc, cl, ct)
                    ).status,
                    esperado,
                )

    def test_commit_distinto_con_mismo_tree_sigue_valid(self):
        """B15-F §15: el commit es metadato."""
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, False, T), _estado(Y, True, T)
            ).status,
            VALID,
        )

    def test_commit_igual_con_tree_distinto_es_obsolete(self):
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, False, T), _estado(X, False, U)
            ).status,
            OBSOLETE,
        )

    def test_clean_distinto_con_tree_igual_sigue_valid(self):
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, True, T), _estado(X, False, T)
            ).status,
            VALID,
        )

    def test_clean_igual_con_tree_distinto_es_obsolete(self):
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, False, T), _estado(X, False, U)
            ).status,
            OBSOLETE,
        )

    def test_ninguna_combinacion_produce_undetermined(self):
        for ac, al, at, cc, cl, ct, _ in MATRIZ:
            with self.subTest(anchor=at[0], actual=ct[0]):
                self.assertNotEqual(
                    evaluar_validez_verdict(
                        _verdict(ac, al, at), _estado(cc, cl, ct)
                    ).status,
                    "UNDETERMINED",
                )


# ===========================================================================
# §7 — `ValidityResult`
# ===========================================================================
class TestValidityResult(unittest.TestCase):
    def test_solo_lleva_status_y_motivo(self):
        nombres = {campo.name for campo in dataclasses.fields(ValidityResult)}
        self.assertEqual(nombres, {"status", "motivo"})

    def test_no_arrastra_datos_del_entorno(self):
        """No incluye Verdict, CurrentGitState, WorkState ni salida de Git."""
        nombres = {campo.name for campo in dataclasses.fields(ValidityResult)}
        for prohibido in ("verdict", "git_anchor", "current_git_state", "work_state",
                          "state_md", "git_status", "subprocess", "diff", "rama",
                          "tree_sha", "commit", "working_tree_clean"):
            with self.subTest(campo=prohibido):
                self.assertNotIn(prohibido, nombres)

    def test_es_inmutable(self):
        r = ValidityResult(VALID, "tree_coincidente")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            r.status = OBSOLETE  # type: ignore[misc]

    def test_es_igual_por_valor(self):
        a = evaluar_validez_verdict(_verdict(X, True, T), _estado(X, True, T))
        b = evaluar_validez_verdict(_verdict(X, True, T), _estado(X, True, T))
        self.assertEqual(a, b)

    def test_es_valido_es_atajo_de_lectura(self):
        valido = evaluar_validez_verdict(_verdict(X, True, T), _estado(X, True, T))
        obsoleto = evaluar_validez_verdict(_verdict(X, True, T), _estado(X, True, U))
        self.assertTrue(valido.es_valido)
        self.assertFalse(obsoleto.es_valido)


# ===========================================================================
# §4 / §5 — Igualdad estricta de contenido
# ===========================================================================
class TestSemanticaDeComparacion(unittest.TestCase):
    def test_la_igualdad_de_tree_es_estricta(self):
        self.assertEqual(
            evaluar_validez_verdict(_verdict(X, True, T), _estado(X, True, T)).status,
            VALID,
        )
        # Un solo carácter distinto ya es divergencia.
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, True, T), _estado(X, True, T[:-1] + "3")
            ).status,
            OBSOLETE,
        )

    def test_los_motivos_reflejan_la_identidad_de_contenido(self):
        r_ok = evaluar_validez_verdict(_verdict(X, True, T), _estado(X, True, T))
        r_ko = evaluar_validez_verdict(_verdict(X, True, T), _estado(X, True, U))
        self.assertEqual(r_ok.motivo, work_verdict.MOTIVO_COINCIDE)
        self.assertEqual(r_ko.motivo, work_verdict.MOTIVO_TREE_DISTINTO)

    def test_no_existen_motivos_que_ya_no_participan(self):
        """B15-F §14: `working_tree_distinto` desapareció con la identidad antigua."""
        for simbolo in ("MOTIVO_COMMIT_DISTINTO", "MOTIVO_WORKING_TREE_DISTINTO"):
            with self.subTest(simbolo=simbolo):
                self.assertFalse(hasattr(work_verdict, simbolo))


# ===========================================================================
# §10 / §11 / §17 / §18 — Casos de B15-E
# ===========================================================================
class TestCasosCriticos(unittest.TestCase):
    def test_caso_b_contenido_distinto_mismo_commit_es_obsolete(self):
        """El falso positivo que B15-E descubrió: el contenido cambió."""
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, False, T), _estado(X, False, U)
            ).status,
            OBSOLETE,
        )

    def test_caso_c_mismo_contenido_commit_distinto_es_valid(self):
        """El falso negativo que B15-E descubrió: el contenido es idéntico."""
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, False, T), _estado(Y, True, T)
            ).status,
            VALID,
        )

    def test_revert_al_contenido_probado_recupera_valid(self):
        """§17: A → B → A. La historia Git no participa."""
        verificado = _verdict(X, False, T)
        self.assertEqual(
            evaluar_validez_verdict(verificado, _estado(X, False, U)).status, OBSOLETE
        )
        self.assertEqual(
            evaluar_validez_verdict(verificado, _estado(Y, True, T)).status, VALID
        )

    def test_head_avanzado_con_mismo_contenido_es_valid(self):
        """§18: consecuencia deliberada de B15-E."""
        self.assertEqual(
            evaluar_validez_verdict(
                _verdict(X, True, T), _estado(Y, True, T)
            ).status,
            VALID,
        )

    def test_no_se_asume_que_dirty_es_peor_que_clean(self):
        self.assertEqual(
            evaluar_validez_verdict(_verdict(X, False, T), _estado(X, False, T)).status,
            VALID,
        )


# ===========================================================================
# §12 — Pureza verificable
# ===========================================================================
class TestPureza(unittest.TestCase):
    def _arbol(self):
        import ast

        raiz = Path(__file__).resolve().parents[1]
        return ast.parse((raiz / "work_verdict.py").read_text(encoding="utf-8"))

    def test_el_evaluador_no_importa_workstate_ni_mcp_ni_agente(self):
        import ast

        arbol = self._arbol()
        importados = {
            (n.module or "") for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)
        } | {
            a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
        }
        for prohibido in ("work_context", "snapcontext", "mcp_tools", "react_agent",
                          "planificador", "agentes"):
            with self.subTest(modulo=prohibido):
                self.assertNotIn(prohibido, importados)

    def test_el_evaluador_no_toca_filesystem_ni_subprocess_ni_git(self):
        import ast

        arbol = self._arbol()
        funcion = next(
            n for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name == "evaluar_validez_verdict"
        )
        cuerpo = list(funcion.body)
        if cuerpo and isinstance(cuerpo[0], ast.Expr):
            cuerpo = cuerpo[1:]  # docstring
        constructions = {
            n.func.id
            for s in cuerpo
            for n in ast.walk(s)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        # Lo único que el evaluador puede invocar: comprobación de tipos y
        # construcción del resultado. Nada de E/S ni de Git.
        self.assertEqual(constructions, {"ValidityResult", "isinstance", "TypeError"})

    def test_es_determinista(self):
        for ac, al, at, cc, cl, ct, _ in MATRIZ:
            with self.subTest(anchor=at[0], actual=ct[0]):
                v, g = _verdict(ac, al, at), _estado(cc, cl, ct)
                primero = evaluar_validez_verdict(v, g)
                for _ in range(5):
                    self.assertEqual(evaluar_validez_verdict(v, g), primero)

    def test_no_muta_sus_entradas(self):
        v = _verdict(X, True)
        g = _estado(X, False)
        antes_v = dataclasses.replace(v)
        antes_g = dataclasses.replace(g)
        evaluar_validez_verdict(v, g)
        self.assertEqual(v, antes_v)
        self.assertEqual(g, antes_g)

    def test_rechaza_entradas_de_tipo_incorrecto(self):
        with self.assertRaises(TypeError):
            evaluar_validez_verdict("no-soy-verdict", _estado(X, True))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            evaluar_validez_verdict(_verdict(X, True), "no-soy-estado")  # type: ignore[arg-type]

    def test_el_evaluador_no_persiste_nada(self):
        """§9/§15: no escribe state.md, WorkState ni veredictos_obsoletos."""
        import ast

        arbol = self._arbol()
        funcion = next(
            n for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name == "evaluar_validez_verdict"
        )
        atributos = {
            n.attr for n in ast.walk(funcion) if isinstance(n, ast.Attribute)
        }
        for prohibido in ("write_text", "actualizar_estado", "escribir_documento_trabajo",
                          "leer_estado"):
            with self.subTest(atributo=prohibido):
                self.assertNotIn(prohibido, atributos)
        # El evaluador no menciona `veredictos_obsoletos` en absoluto.
        self.assertNotIn("veredictos_obsoletos", ast.dump(funcion))
        # Y el módulo no importa el canal WORK. La prosa del docstring sí lo
        # nombra para declarar la independencia; eso no es una dependencia.
        importados = {
            (n.module or "") for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)
        } | {
            a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
        }
        self.assertNotIn("work_context", importados)


# ===========================================================================
# §6 — `UNDETERMINED`: explícitamente descartado
# ===========================================================================
class TestUndeterminedDescartado(unittest.TestCase):
    def test_no_existe_el_simbolo_undetermined(self):
        for simbolo in ("UNDETERMINED", "INDETERMINADO"):
            with self.subTest(simbolo=simbolo):
                self.assertFalse(hasattr(work_verdict, simbolo))
                self.assertNotIn(simbolo, work_verdict.__all__)

    def test_no_se_emite_undetermined_por_ninguna_entrada_valida(self):
        """Las 3 condiciones de B15-A §7.1 son inalcanzables aquí."""
        for ac, al, at, cc, cl, ct, _ in MATRIZ:
            with self.subTest(anchor=at[0], actual=ct[0]):
                self.assertIn(
                    evaluar_validez_verdict(
                        _verdict(ac, al, at), _estado(cc, cl, ct)
                    ).status,
                    {VALID, OBSOLETE},
                )

    def test_sin_anchor_se_rechaza_al_construir_el_dato(self):
        """Condición 'verdict sin anchor' → imposible: el modelo lo impide."""
        with self.assertRaises(ValueError):
            GitAnchor("", "commit", True)  # tree_sha vacío
        with self.assertRaises(ValueError):
            GitAnchor(None, "commit", True)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Verdict("pasa", "pytest", "src", None)  # type: ignore[arg-type]

    def test_git_inobservable_falla_en_el_adapter_no_en_el_evaluador(self):
        """Condición 'no se puede observar' → error del adapter (B15-A §10)."""
        from exceptions import EstadoGitIndisponibleError

        with tempfile.TemporaryDirectory() as d:
            plano = Path(d) / "plano"
            plano.mkdir()
            with self.assertRaises(EstadoGitIndisponibleError):
                leer_estado_git(str(plano))  # falla ANTES de evaluar


# ===========================================================================
# §14 — No decide identidad entre verificaciones
# ===========================================================================
class TestIdentidadDeVerificacion(unittest.TestCase):
    def test_no_compara_comando_scope_autor_ni_instante(self):
        base = _verdict(X, True)
        otro = Verdict(
            resultado="falla",  # resultado textual DISTINTO
            comando="otro comando completamente distinto",
            scope="scope distinto",
            git_anchor=GitAnchor(T, X, True),  # mismo contenido
            autor="Otro Agente",
            instante="1999-01-01T00:00:00Z",
        )
        # Mismo anchor ⇒ mismo veredicto, aunque todo lo demás difiera.
        self.assertEqual(
            evaluar_validez_verdict(base, _estado(X, True)),
            evaluar_validez_verdict(otro, _estado(X, True)),
        )

    def test_esta_es_la_unica_pregunta_que_responde(self):
        """La única entrada considerada es `git_anchor`."""
        r = evaluar_validez_verdict(_verdict(X, True), _estado(X, True))
        # Nada del Verdict más allá del anchor aparece en el resultado.
        self.assertEqual(r, ValidityResult(VALID, work_verdict.MOTIVO_COINCIDE))


# ===========================================================================
# §17 — Flujo completo adapter → evaluador
# ===========================================================================
class TestFlujoCompleto(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.fichero = self.repo / "m.py"
        self.fichero.write_text("V = 1\n", encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "m.py")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i")
        self.head = self._git("rev-parse", "HEAD").strip()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True
        ).stdout

    def test_adapter_mas_evaluador_sobre_un_repo_real(self):
        # El adapter observa; el evaluador juzga. Separados.
        # El verdict se ancla sobre lo REALMENTE observado en ese instante.
        estado_limpio = leer_estado_git(str(self.repo))
        verdict = _verdict(
            estado_limpio.commit, estado_limpio.working_tree_clean, estado_limpio.tree_sha
        )
        self.assertEqual(
            evaluar_validez_verdict(verdict, estado_limpio).status, VALID
        )

        self.fichero.write_text("V = 2\n", encoding="utf-8")
        estado_sucio = leer_estado_git(str(self.repo))
        self.assertEqual(estado_sucio.commit, self.head)  # mismo commit
        self.assertEqual(
            evaluar_validez_verdict(verdict, estado_sucio).status, OBSOLETE
        )

        # Y con HEAD avanzado: el adapter solo reporta, el evaluador decide.
        self._git("add", "m.py")
        self._git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "2")
        estado_nuevo = leer_estado_git(str(self.repo))
        self.assertNotEqual(estado_nuevo.commit, self.head)
        self.assertEqual(
            evaluar_validez_verdict(verdict, estado_nuevo).status, OBSOLETE
        )


if __name__ == "__main__":
    unittest.main()
