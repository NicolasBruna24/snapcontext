#!/usr/bin/env python3
"""B15-J — Primer consumidor productivo de `ValidityResult` en F4 (ReAct).

Demuestra la cadena completa con Git real:

    PRE → ejecución F4 → Verdict → CURRENT → evaluar_validez_verdict → ValidityResult

y que ``resultado`` ("pasa"/"falla") es ortogonal a ``status``
(``VALID``/``OBSOLETE``): la identidad sigue siendo ``tree_sha`` (B15-E/F).

Fronteras (§7, §11, §12, §17, §18): el evaluador sigue puro, F4 no persiste
nada, no introduce transición de estado y F1/F2 no se tocan.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import react_agent
import snapcontext as sc
import work_verdict
from exceptions import EstadoGitIndisponibleError
from work_verdict import (
    MOTIVO_COINCIDE,
    MOTIVO_TREE_DISTINTO,
    OBSOLETE,
    VALID,
    ValidityResult,
    Verdict,
    evaluar_validez_verdict,
    leer_estado_git,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


#: Comando compuesto que NO toca el árbol.
OK_SIN_CAMBIOS = "python3 -c \"print('ok')\""
#: Comando compuesto que SÍ modifica el working tree y luego verifica.
OK_CON_CAMBIO = "python3 -c \"open('generado.txt','w').write('x')\" && python3 -c \"print('ok')\""
#: Verificación que falla sin tocar el árbol.
FALLA_SIN_CAMBIOS = 'python3 -c "raise SystemExit(1)"'


def _cuerpo_f4() -> str:
    fuente = Path(react_agent.__file__).read_text(encoding="utf-8")
    return fuente[
        fuente.index("def _tool_ejecutar_pruebas") : fuente.index("def _tool_leer_archivo")
    ]


def _cuerpo_verificacion() -> str:
    """F4 sin el bloque de persistencia de B15-K (que va en su propio método)."""
    cuerpo = _cuerpo_f4()
    return cuerpo[: cuerpo.index("def _persistir_verificacion")]


class BaseConsumidor(unittest.TestCase):
    """Repositorio Git real y temporal + agente ReAct real."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "b15j@example.com")
        _git(self.repo, "config", "user.name", "B15-J")
        (self.repo / "modulo.py").write_text("OK = 1\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "inicial")

    def ensuciar(self) -> None:
        """Deja el repositorio dirty ANTES de la verificación."""
        (self.repo / "sucio.txt").write_text("pendiente\n", encoding="utf-8")

    def agente(self) -> react_agent.ReactAgent:
        return react_agent.ReactAgent(str(self.repo), auto=True)

    def ejecutar(self, comando: str) -> dict:
        return self.agente()._tool_ejecutar_pruebas({"comando": comando})


class TestValidObsoleto(BaseConsumidor):
    """Tests 1-3 y 5: los dos escenarios fundamentales, con Git real."""

    def test_exitoso_sin_cambios_es_valid(self):
        """Test 1 — tree_pre == tree_current → VALID."""
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar(OK_SIN_CAMBIOS)
        post = leer_estado_git(str(self.repo))

        self.assertEqual(pre.tree_sha, post.tree_sha)
        self.assertIsInstance(r["verdict"], Verdict)
        self.assertIsInstance(r["validity"], ValidityResult)
        self.assertEqual(r["validity"].status, VALID)
        self.assertEqual(r["validity"].status, "VALID")
        self.assertEqual(r["validity"].motivo, MOTIVO_COINCIDE)
        self.assertTrue(r["validity"].es_valido)

    def test_exitoso_que_modifica_el_arbol_es_obsolete(self):
        """Test 2 — tree_pre != tree_current → OBSOLETE."""
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar(OK_CON_CAMBIO)
        post = leer_estado_git(str(self.repo))

        self.assertNotEqual(pre.tree_sha, post.tree_sha)
        # El anchor identifica lo VERIFICADO (PRE) y la validez mira el ACTUAL.
        self.assertEqual(r["verdict"].git_anchor.tree_sha, pre.tree_sha)
        self.assertNotEqual(r["verdict"].git_anchor.tree_sha, post.tree_sha)
        self.assertEqual(r["validity"].status, OBSOLETE)
        self.assertEqual(r["validity"].status, "OBSOLETE")
        self.assertEqual(r["validity"].motivo, MOTIVO_TREE_DISTINTO)
        self.assertFalse(r["validity"].es_valido)

    def test_verificacion_fallida_sin_cambios_sigue_valid(self):
        """Test 3 — 'falla' y VALID son ortogonales."""
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar(FALLA_SIN_CAMBIOS)
        post = leer_estado_git(str(self.repo))

        self.assertEqual(pre.tree_sha, post.tree_sha)
        self.assertEqual(r["verdict"].resultado, "falla")
        self.assertEqual(r["validity"].status, VALID)

    def test_dirty_sin_modificacion_posterior_es_valid(self):
        """Test 4 — working_tree_clean NO participa en la identidad."""
        self.ensuciar()
        pre = leer_estado_git(str(self.repo))
        self.assertFalse(pre.working_tree_clean)
        r = self.ejecutar(OK_SIN_CAMBIOS)
        post = leer_estado_git(str(self.repo))

        self.assertFalse(pre.working_tree_clean)
        self.assertFalse(post.working_tree_clean)
        self.assertEqual(pre.tree_sha, post.tree_sha)
        self.assertEqual(r["validity"].status, VALID)

    def test_dirty_con_modificacion_posterior_es_obsolete(self):
        """Test 5 — dirty antes + cambio después → OBSOLETE."""
        self.ensuciar()
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar(OK_CON_CAMBIO)
        post = leer_estado_git(str(self.repo))

        self.assertEqual(r["verdict"].git_anchor.tree_sha, pre.tree_sha)
        self.assertNotEqual(post.tree_sha, r["verdict"].git_anchor.tree_sha)
        self.assertEqual(r["validity"].status, OBSOLETE)

    def test_mismo_commit_no_implica_valid(self):
        """§22 — la identidad NO es (commit, working_tree_clean)."""
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar(OK_CON_CAMBIO)
        post = leer_estado_git(str(self.repo))

        self.assertEqual(pre.commit, post.commit)  # commit idéntico
        self.assertEqual(r["validity"].status, OBSOLETE)  # pero OBSOLETE


class TestSecuenciaDeObservaciones(BaseConsumidor):
    """Tests 6-9: orden PRE/CURRENT y argumentos reales del evaluador."""

    def _registrador(self, vistos: list):
        def registrar(directorio="."):
            estado = leer_estado_git(directorio)
            vistos.append(estado)
            return estado

        return registrar

    def test_anchor_usa_pre_y_no_current(self):
        """Test 6."""
        vistos: list = []
        with mock.patch.object(
            work_verdict, "leer_estado_git", side_effect=self._registrador(vistos)
        ):
            r = self.ejecutar(OK_CON_CAMBIO)

        self.assertEqual(len(vistos), 2)
        self.assertEqual(r["verdict"].git_anchor.tree_sha, vistos[0].tree_sha)
        self.assertNotEqual(vistos[0].tree_sha, vistos[1].tree_sha)

    def test_leer_estado_git_se_llama_exactamente_dos_veces(self):
        """Test 7 — PRE y CURRENT, ni una más."""
        with mock.patch.object(work_verdict, "leer_estado_git", wraps=leer_estado_git) as espia:
            self.ejecutar(OK_SIN_CAMBIOS)

        self.assertEqual(espia.call_count, 2)

    def test_evaluador_recibe_el_verdict_y_el_estado_actual(self):
        """Test 8 — exactamente (Verdict producido, CurrentGitState actual)."""
        vistos: list = []
        with mock.patch.object(
            work_verdict, "leer_estado_git", side_effect=self._registrador(vistos)
        ):
            with mock.patch.object(
                work_verdict,
                "evaluar_validez_verdict",
                wraps=evaluar_validez_verdict,
            ) as evaluador:
                r = self.ejecutar(OK_CON_CAMBIO)

        evaluador.assert_called_once()
        verdict_arg, estado_arg = evaluador.call_args.args
        self.assertIs(verdict_arg, r["verdict"])
        self.assertEqual(estado_arg, vistos[1])  # el CURRENT, no el PRE
        self.assertNotEqual(estado_arg.tree_sha, verdict_arg.git_anchor.tree_sha)

    def test_f4_delega_en_el_evaluador_canonico(self):
        """Test 9 — sustituir el evaluador cambia el resultado de F4."""
        propio = ValidityResult(status=OBSOLETE, motivo="evaluador alternativo")
        with mock.patch.object(work_verdict, "evaluar_validez_verdict", return_value=propio):
            r = self.ejecutar(OK_SIN_CAMBIOS)

        # El árbol no cambió (sería VALID), pero F4 reporta lo que devuelve el
        # evaluador: no hay implementación alternativa dentro de F4.
        self.assertIs(r["validity"], propio)

    def test_f4_no_reimplementa_la_comparacion(self):
        codigo = "\n".join(linea.split("#", 1)[0] for linea in _cuerpo_verificacion().splitlines())
        for prohibido in (VALID, OBSOLETE, "working_tree_clean"):
            self.assertNotIn(prohibido, codigo, f"F4 no debe usar {prohibido!r}")
        for duplicado in ("rev-parse", "write-tree", "git add", "git status", "subprocess"):
            self.assertNotIn(duplicado, _cuerpo_f4())


class TestFallosDeGit(BaseConsumidor):
    """Tests 10-11: sin validez inventada."""

    def test_git_pre_falla_no_hay_verdict_ni_validity(self):
        """Test 10 — política B15-I conservada, sin fabricar con el CURRENT."""
        with mock.patch.object(
            work_verdict,
            "leer_estado_git",
            side_effect=EstadoGitIndisponibleError("sin repo", "sin_repositorio"),
        ):
            with mock.patch.object(sc, "_ejecutar_comando", return_value=(0, "ok", "")):
                r = self.ejecutar(OK_SIN_CAMBIOS)

        self.assertIsNone(r["verdict"])
        self.assertIsNone(r["validity"])
        self.assertIn("verdict_error", r)
        self.assertNotIn("validity_error", r)
        # La verificación se ejecutó igualmente (contrato histórico de F4).
        self.assertTrue(r["ok"])

    def test_git_current_falla_no_inventa_validez(self):
        """Test 11 — el Verdict se conserva; la validez se declara error."""
        llamadas = {"n": 0}
        real = leer_estado_git

        def solo_pre(directorio="."):
            llamadas["n"] += 1
            if llamadas["n"] > 1:
                raise EstadoGitIndisponibleError("boom", "git_no_disponible")
            return real(directorio)

        with mock.patch.object(work_verdict, "leer_estado_git", side_effect=solo_pre):
            r = self.ejecutar(OK_SIN_CAMBIOS)

        self.assertIsInstance(r["verdict"], Verdict)
        self.assertIsNone(r["validity"])
        self.assertNotIsInstance(r["validity"], ValidityResult)
        self.assertIn("git_no_disponible", r["validity_error"])
        self.assertTrue(r["ok"])

    def test_no_se_llama_al_evaluador_sin_verdict(self):
        """No se evalúa sin Verdict aunque el CURRENT sea observable."""
        with mock.patch.object(
            work_verdict,
            "leer_estado_git",
            side_effect=EstadoGitIndisponibleError("sin repo", "sin_repositorio"),
        ):
            with mock.patch.object(sc, "_ejecutar_comando", return_value=(0, "ok", "")):
                with mock.patch.object(work_verdict, "evaluar_validez_verdict") as ev:
                    r = self.ejecutar(OK_SIN_CAMBIOS)

        ev.assert_not_called()
        self.assertIsNone(r["validity"])


class TestContratoF4(BaseConsumidor):
    """Test 12 — las claves históricas mantienen su semántica."""

    def test_claves_historicas_intactas(self):
        r = self.ejecutar(OK_SIN_CAMBIOS)
        for clave in ("ok", "codigo", "comando", "stdout", "stderr", "verdict"):
            self.assertIn(clave, r)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["codigo"], 0)
        self.assertEqual(r["comando"], OK_SIN_CAMBIOS)
        self.assertIn("ok", r["stdout"])
        self.assertIsInstance(r["stderr"], str)

    def test_verdict_y_validity_conviven(self):
        """§10 — el hecho y su evaluación son campos distintos."""
        r = self.ejecutar(OK_SIN_CAMBIOS)
        self.assertIsInstance(r["verdict"], Verdict)
        self.assertIsInstance(r["validity"], ValidityResult)
        self.assertIsNot(r["verdict"], r["validity"])

    def test_observacion_al_llm_no_cambia(self):
        """§9 — `_observar_resultado` intacto."""
        r = self.ejecutar(OK_SIN_CAMBIOS)
        observacion = react_agent.ReactAgent._observar_resultado(r)
        self.assertIn("codigo=0", observacion)
        self.assertNotIn("VALID", observacion)
        self.assertNotIn("OBSOLETE", observacion)

    def test_no_persiste_nada(self):
        """§11 — ni state.md, ni decisions.md, ni assertions.md, ni .work."""
        antes = {p.name for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.ejecutar(OK_SIN_CAMBIOS)
        self.ejecutar(OK_CON_CAMBIO)
        despues = {p.name for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.parts}

        for nombre in ("state.md", "decisions.md", "assertions.md"):
            self.assertNotIn(nombre, despues)
        self.assertFalse((self.repo / ".work").exists())
        # Solo el comando de Test 2 crea ficheros, y no documentos de WORK.
        self.assertEqual(despues - antes, {"generado.txt"})

    def test_f4_no_toca_persistencia_directa_ni_f1_f2(self):
        """§17 y §24.

        B15-K introduce la persistencia, pero F4 **no** edita `state.md`:
        delega en `work_context.registrar_verificacion()`. Lo que sigue
        prohibido en F4 es el acceso directo al documento y a las claves
        protegidas.
        """
        codigo = "\n".join(linea.split("#", 1)[0] for linea in _cuerpo_verificacion().splitlines())
        for prohibido in (
            "actualizar_estado",
            "state.md",
            "decisions.md",
            "assertions.md",
            "ciclo_de_vida",
            "ultimo_veredicto",
            "veredictos_obsoletos",
        ):
            self.assertNotIn(prohibido, codigo)
        for modulo in ("agentes.py", "orquestador.py"):
            with self.subTest(modulo=modulo):
                fuente = (RAIZ / modulo).read_text(encoding="utf-8")
                self.assertNotIn("evaluar_validez_verdict", fuente)
                self.assertNotIn("producir_verdict", fuente)


class TestPurezaDelEvaluador(unittest.TestCase):
    """§7 / §16 — el evaluador sigue siendo puro."""

    @staticmethod
    def _cuerpo_evaluador() -> str:
        """Code real de `evaluar_validez_verdict`, sin el docstring."""
        import ast

        fuente = Path(work_verdict.__file__).read_text(encoding="utf-8")
        arbol = ast.parse(fuente)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "evaluar_validez_verdict":
                cuerpo = ast.get_source_segment(fuente, nodo)
                doc = ast.get_docstring(nodo, clean=False)
                return cuerpo.replace(doc, "")
        raise AssertionError("no se encontró evaluar_validez_verdict")

    def test_el_evaluador_no_toca_nada_externo(self):
        cuerpo = self._cuerpo_evaluador()
        for prohibido in (
            "subprocess",
            "import os",
            "open(",
            "Path(",
            "sandbox_utils",
            "leer_estado_git",
            "_correr_git",
        ):
            self.assertNotIn(prohibido, cuerpo)

    def test_evaluar_no_ejecuta_git(self):
        with mock.patch.object(work_verdict, "_correr_git") as git:
            v = work_verdict.producir_verdict(
                True, "pytest", work_verdict.CurrentGitState("t", "c", True)
            )
            r = work_verdict.evaluar_validez_verdict(
                v, work_verdict.CurrentGitState("t", "c", False)
            )
        git.assert_not_called()
        self.assertEqual(r.status, VALID)

    def test_evaluador_es_determinista_y_no_muta(self):
        v = work_verdict.producir_verdict(
            True, "pytest", work_verdict.CurrentGitState("t1", "c", True)
        )
        actual = work_verdict.CurrentGitState("t2", "c", True)
        primero = work_verdict.evaluar_validez_verdict(v, actual)
        segundo = work_verdict.evaluar_validez_verdict(v, actual)
        self.assertEqual(primero, segundo)
        self.assertEqual(primero.status, OBSOLETE)
        self.assertEqual(v.git_anchor.tree_sha, "t1")


if __name__ == "__main__":
    unittest.main()
