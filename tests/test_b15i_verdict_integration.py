#!/usr/bin/env python3
"""B15-I — Integración del productor de Verdict en el flujo real F4 (ReAct).

Demuestra experimentalmente que el flujo productivo F4 (ReAct) produce un
:class:`Verdict` cuyo ``GitAnchor`` se basa en el **estado efectivo del árbol
capturado antes de ejecutar la verificación**.

Fronteras verificadas aquí:

* F4 no implementa Git, no calcula ``tree_sha``, no evalúa validez, no decide
  ``VALID``/``OBSOLETE`` y no persiste nada (§13).
* F4 **usa** ``producir_verdict()``; la construcción del anchor vive solo en
  ``work_verdict.py`` (§9).
* El contrato previo de F4 (``ok``/``codigo``/``comando``/``stdout``/``stderr``)
  se conserva intacto (§3, Test 9).
"""

from __future__ import annotations

import dataclasses
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
import work_context
import work_verdict
from exceptions import EstadoGitIndisponibleError
from work_verdict import (
    OBSOLETE,
    SCOPE_NO_DEFINIDO,
    VALID,
    ValidityResult,
    Verdict,
    leer_estado_git,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _cuerpo_f4() -> str:
    """Source de `_tool_ejecutar_pruebas`, para auditar sus fronteras."""
    fuente = Path(react_agent.__file__).read_text(encoding="utf-8")
    return fuente[
        fuente.index("def _tool_ejecutar_pruebas") : fuente.index("def _tool_leer_archivo")
    ]


class BaseF4(unittest.TestCase):
    """Repositorio Git real y temporal + agente ReAct real."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "b15i@example.com")
        _git(self.repo, "config", "user.name", "B15-I")
        (self.repo / "modulo.py").write_text("OK = 1\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "inicial")

    def agente(self) -> react_agent.ReactAgent:
        return react_agent.ReactAgent(str(self.repo), auto=True)

    def ejecutar(self, comando: str) -> dict:
        return self.agente()._tool_ejecutar_pruebas({"comando": comando})


class TestProduccionVerdictF4(BaseF4):
    """Tests 1-9: producción real de Verdict desde F4."""

    def test_ejecucion_exitosa_produce_pasa_y_anchor_real(self):
        """Test 1."""
        r = self.ejecutar("python3 -c \"print('ok')\"")

        self.assertTrue(r["ok"])
        v = r["verdict"]
        self.assertIsInstance(v, Verdict)
        self.assertEqual(v.resultado, "pasa")
        self.assertEqual(v.git_anchor.tree_sha, _git(self.repo, "write-tree"))

    def test_ejecucion_fallida_produce_falla_y_conserva_anchor(self):
        """Test 2."""
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar('python3 -c "raise SystemExit(3)"')

        self.assertFalse(r["ok"])
        v = r["verdict"]
        self.assertEqual(v.resultado, "falla")
        self.assertEqual(v.git_anchor.tree_sha, pre.tree_sha)
        self.assertEqual(v.git_anchor.commit, pre.commit)

    def test_tree_sha_es_el_estado_capturado_antes_de_ejecutar(self):
        """Test 3 — el anchor es exactamente la captura previa."""
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar('python3 -c "print(1)"')

        self.assertEqual(r["verdict"].git_anchor.tree_sha, pre.tree_sha)
        self.assertEqual(r["verdict"].git_anchor.commit, pre.commit)
        self.assertIs(r["verdict"].git_anchor.working_tree_clean, pre.working_tree_clean)

    def test_anchor_usa_la_captura_pre_y_no_la_current(self):
        """Test 3 — el anchor se construye con la captura PRE, no con la CURRENT.

        Tras B15-J hay una segunda lectura Git (la CURRENT, para evaluar
        validez). Se registra cada estado observado y se comprueba que el
        anchor sale del **primer** (PRE), nunca del segundo.
        """
        vistos = []

        def registrar(directorio="."):
            estado = leer_estado_git(directorio)
            vistos.append(estado)
            return estado

        with mock.patch.object(work_verdict, "leer_estado_git", side_effect=registrar):
            r = self.ejecutar('python3 -c "print(1)"')

        self.assertEqual(len(vistos), 2)  # PRE + CURRENT
        self.assertEqual(r["verdict"].git_anchor.tree_sha, vistos[0].tree_sha)
        self.assertEqual(r["verdict"].git_anchor.commit, vistos[0].commit)

    def test_ejecucion_que_modifica_el_arbol_no_cambia_el_anchor(self):
        """Test 4 — el comando verificado ensucia el árbol post-ejecución."""
        pre = leer_estado_git(str(self.repo))
        comando = "python3 -c \"open('generado.txt','w').write('x')\" && python3 -c \"print('ok')\""
        r = self.ejecutar(comando)

        self.assertTrue(r["ok"])
        post = leer_estado_git(str(self.repo))
        self.assertNotEqual(post.tree_sha, pre.tree_sha)
        self.assertEqual(r["verdict"].git_anchor.tree_sha, pre.tree_sha)

    def test_arbol_sucio_antes_de_ejecutar(self):
        """Test 5 — `working_tree_clean` refleja el estado previo."""
        (self.repo / "sucio.txt").write_text("pendiente\n", encoding="utf-8")
        pre = leer_estado_git(str(self.repo))
        self.assertFalse(pre.working_tree_clean)

        r = self.ejecutar("python3 -c \"print('ok')\"")

        self.assertIs(r["verdict"].git_anchor.working_tree_clean, False)
        self.assertEqual(r["verdict"].git_anchor.tree_sha, pre.tree_sha)

    def test_commit_metadata_corresponde_a_la_captura_previa(self):
        """Test 6."""
        pre = leer_estado_git(str(self.repo))
        r = self.ejecutar("python3 -c \"print('ok')\"")
        self.assertEqual(r["verdict"].git_anchor.commit, pre.commit)
        self.assertEqual(pre.commit, _git(self.repo, "rev-parse", "HEAD"))

    def test_no_evalua_validez_dentro_del_verdict(self):
        """El `Verdict` de F4 no lleva validez: son hechos separados (B15-J §10).

        La evaluación de validez vive en la clave `validity`, nunca dentro del
        `Verdict` (B15-B). Este test sigue siendo válido tras B15-J: lo que
        comprueba es que el hecho producido no se autodeclara válido/obsoleto.
        """
        r = self.ejecutar("python3 -c \"print('ok')\"")

        self.assertIsInstance(r["verdict"], Verdict)
        self.assertNotIsInstance(r["verdict"], ValidityResult)
        nombres = {f.name for f in dataclasses.fields(r["verdict"])}
        self.assertNotIn("validity", nombres)
        r2 = self.ejecutar('python3 -c "raise SystemExit(1)"')
        self.assertNotEqual(r2["verdict"].resultado, OBSOLETE)

    def test_no_persiste_nada(self):
        """Test 8 — ni state.md, ni decisions.md, ni assertions.md, ni .work."""
        antes = {p.name for p in self.repo.rglob("*") if p.is_file()}
        self.ejecutar("python3 -c \"print('ok')\"")
        despues = {p.name for p in self.repo.rglob("*") if p.is_file()}

        self.assertEqual(antes, despues)
        for nombre in ("state.md", "decisions.md", "assertions.md"):
            self.assertNotIn(nombre, despues)
        self.assertFalse((self.repo / ".work").exists())

    def test_f4_delega_la_persistencia_en_work_context(self):
        """B15-K: F4 **sí** persiste, pero delega en `work_context`.

        Lo que no hace es implementar la escritura de `state.md`: no serializa,
        no formatea la línea y no edita el documento. Eso vive en
        `work_context.registrar_verificacion()` (§9, §20).
        """
        cuerpo = _cuerpo_f4()
        codigo = "\n".join(linea.split("#", 1)[0] for linea in cuerpo.splitlines())
        self.assertIn("registrar_verificacion", codigo)
        for propio in ("formatear_verificacion", "escribir_documento_trabajo", "actualizar_estado"):
            self.assertNotIn(propio, codigo, f"F4 no debe implementar {propio!r}")
        fuente_work = Path(work_context.__file__).read_text(encoding="utf-8")
        self.assertIn("def registrar_verificacion", fuente_work)

    def test_contrato_preexistente_de_f4_intacto(self):
        """Test 9."""
        r = self.ejecutar("python3 -c \"print('hola')\"")
        for clave in ("ok", "codigo", "comando", "stdout", "stderr"):
            self.assertIn(clave, r)
        self.assertIs(r["ok"], True)
        self.assertEqual(r["codigo"], 0)
        self.assertEqual(r["comando"], "python3 -c \"print('hola')\"")
        self.assertIn("hola", r["stdout"])
        self.assertIsInstance(r["stderr"], str)

    def test_observacion_al_llm_sigue_funcionando(self):
        """Test 9 — el Verdict no rompe el consumo existente del resultado."""
        r = self.ejecutar("python3 -c \"print('hola')\"")
        observacion = react_agent.ReactAgent._observar_resultado(r)
        self.assertIn("codigo=0", observacion)
        self.assertNotIn("Verdict", observacion)

    def test_comando_real_efectivo_se_conserva(self):
        """§6 — Verdict.comando es el comando realmente ejecutado."""
        r = self.ejecutar("python3 -c \"print('hola')\"")
        self.assertEqual(r["verdict"].comando, r["comando"])


class TestFalloDeGit(BaseF4):
    """Test 10 — el fallo de Git no se oculta y no inventa un anchor."""

    def test_git_fallido_no_produce_anchor_ni_oculta_el_error(self):
        with mock.patch.object(
            work_verdict,
            "leer_estado_git",
            side_effect=EstadoGitIndisponibleError("sin repo", "sin_repositorio"),
        ):
            with mock.patch.object(sc, "_ejecutar_comando", return_value=(0, "ok", "")):
                r = self.agente()._tool_ejecutar_pruebas({"comando": 'python3 -c "print(1)"'})

        # Sin estado previo NO hay identidad: no se fabrica un Verdict.
        self.assertIsNone(r["verdict"])
        self.assertIn("estado Git", r["verdict_error"])
        self.assertIn("sin repo", r["verdict_error"])
        # El contrato previo de F4 sigue intacto (directorios no-repo).
        self.assertTrue(r["ok"])
        self.assertEqual(r["codigo"], 0)

    def test_git_fallido_en_directorio_sin_repo_es_real(self):
        plano = Path(self._tmp.name) / "plano"
        plano.mkdir()
        with mock.patch.object(sc, "_ejecutar_comando", return_value=(0, "ok", "")):
            r = react_agent.ReactAgent(str(plano), auto=True)._tool_ejecutar_pruebas(
                {"comando": 'python3 -c "print(1)"'}
            )

        self.assertIsNone(r["verdict"])
        self.assertIn("estado Git", r["verdict_error"])
        self.assertTrue(r["ok"])


class TestFronterasDeResponsabilidad(unittest.TestCase):
    """§13 / §14 — la integración no mueve responsabilidades."""

    def test_f4_no_ejecuta_git_ni_calcula_tree_sha(self):
        cuerpo = _cuerpo_f4()
        for prohibido in ("rev-parse", "write-tree", "git add", "git status", "subprocess"):
            self.assertNotIn(prohibido, cuerpo, f"F4 no debe contener {prohibido!r}")
        codigo = "\n".join(linea.split("#", 1)[0] for linea in cuerpo.splitlines())
        # F4 no *calcula* el hash del árbol: solo lo lee del anchor que produjo
        # `work_verdict` (B15-K persiste ese valor, no lo deriva).
        self.assertNotIn("write-tree", codigo)
        self.assertNotIn("hashlib", codigo)
        self.assertNotIn("sha1", codigo.lower().replace("tree_sha", ""))

    def test_f4_no_calcula_validez_por_si_mismo(self):
        """F4 delega en el evaluador puro; no reimplementa la comparación.

        B15-J integra `evaluar_validez_verdict()` (el evaluador canónico), pero
        F4 no contiene `VALID`/`OBSOLETE` ni una comparación de hashes propia.
        """
        fuente = Path(react_agent.__file__).read_text(encoding="utf-8")
        self.assertIn("evaluar_validez_verdict", fuente)
        # F4 no *usa* las constantes de validez: solo delega en el evaluador.
        codigo = "\n".join(linea.split("#", 1)[0] for linea in _cuerpo_f4().splitlines())
        for constante in (OBSOLETE, VALID):
            self.assertNotIn(constante, codigo)

    def test_f4_usa_el_productor_canonico(self):
        fuente = Path(react_agent.__file__).read_text(encoding="utf-8")
        self.assertIn("producir_verdict", fuente)
        self.assertNotIn("GitAnchor(", fuente)
        self.assertNotIn("Verdict(", fuente)

    def test_f1_f2_no_integradas_aun(self):
        """§14 — esta iteración no toca AgenteTester ni el bucle del orquestador."""
        for modulo in ("agentes.py", "orquestador.py", "qa_tester_logic.py"):
            with self.subTest(modulo=modulo):
                self.assertNotIn("producir_verdict", (RAIZ / modulo).read_text(encoding="utf-8"))

    def test_el_productor_canonico_sigue_en_work_verdict(self):
        self.assertIn("producir_verdict", Path(work_verdict.__file__).read_text(encoding="utf-8"))

    def test_scope_autor_instante_usan_los_valores_contractuales(self):
        """§7 / §8 — sin inventar scope, autor ni instante."""
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "r"
            repo.mkdir()
            for args in (
                ("init", "-q"),
                ("config", "user.email", "a@b.c"),
                ("config", "user.name", "t"),
            ):
                _git(repo, *args)
            (repo / "a.py").write_text("x=1\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "c")
            r = react_agent.ReactAgent(str(repo), auto=True)._tool_ejecutar_pruebas(
                {"comando": 'python3 -c "print(1)"'}
            )

        self.assertEqual(r["verdict"].scope, SCOPE_NO_DEFINIDO)
        self.assertIsNone(r["verdict"].autor)
        self.assertIsNone(r["verdict"].instante)


if __name__ == "__main__":
    unittest.main()
