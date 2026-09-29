#!/usr/bin/env python3
"""B15-L — Historial y transición explícita de obsolescencia.

Demuestra, con Git real y `state.md` real, que la obsolescencia depende del
**contenido efectivo** del árbol:

    Verdict persistido → el árbol cambia → detección explícita
    → ValidityResult = OBSOLETE → una entrada en `veredictos_obsoletos`

y que un revert exacto o un commit con el mismo contenido dejan de ser
obsolescentes. El historial es append-only e idempotente, y la verificación
original nunca se borra ni se reescribe.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

import react_agent
import snapcontext as sc
import work_context
import work_obsolencia
import work_verdict
from exceptions import (
    ContratoEstadoInvalidoError,
    EstadoGitIndisponibleError,
)
from work_context import (
    actualizar_estado,
    crear_trabajo,
    escribir_documento_trabajo,
    formatear_veredicto_obsoleto,
    leer_estado,
    leer_ultima_verificacion,
    leer_veredictos_obsoletos,
    registrar_veredicto_obsoleto,
)
from work_obsolencia import (
    ES_OBSOLETO,
    ES_SIN_VERIFICACION,
    ES_VIGENTE,
    detectar_obsolescencia,
)
from work_verdict import OBSOLETE, VALID

WORK_ID = "b15l-obsoleto"
OK = "python3 -c \"print('ok')\""
TS = "2026-09-27T10:00:00Z"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class BaseObsolescencia(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name)
        _git(self.raiz, "init", "-q")
        _git(self.raiz, "config", "user.email", "b15l@example.com")
        _git(self.raiz, "config", "user.name", "B15-L")
        # El canal WORK no forma parte del árbol verificado.
        (self.raiz / ".gitignore").write_text(".work/\n", encoding="utf-8")
        (self.raiz / "modulo.py").write_text("OK = 1\n", encoding="utf-8")
        _git(self.raiz, "add", "-A")
        _git(self.raiz, "commit", "-qm", "inicial")
        crear_trabajo(
            WORK_ID,
            self.raiz,
            titulo="Obsolescencia",
            objetivo="Detectar",
            criterios=None,
            restricciones=None,
        )

    # -- utilidades ---------------------------------------------------------
    def verificar(self) -> str:
        """Ejecuta F4 de verdad: produce Verdict y lo persiste."""
        agente = react_agent.ReactAgent(str(self.raiz), auto=True, work_id=WORK_ID)
        r = agente._tool_ejecutar_pruebas({"comando": OK})
        self.assertIs(r["verificacion_persistida"], True)
        return r["verdict"].git_anchor.tree_sha

    def detectar(self, **kw):
        return detectar_obsolescencia(WORK_ID, str(self.raiz), **kw)

    def estado(self):
        return leer_estado(WORK_ID, self.raiz)

    def historial(self):
        return leer_veredictos_obsoletos(self.estado())

    def texto(self) -> str:
        return (self.raiz / ".work" / WORK_ID / "state.md").read_text(encoding="utf-8")

    def cambiar_arbol(self) -> None:
        (self.raiz / "modulo.py").write_text("OK = 2\n", encoding="utf-8")

    def revertir(self) -> None:
        _git(self.raiz, "checkout", "--", "modulo.py")


class TestDeteccion(BaseObsolescencia):
    """Tests 1-7: la transición y sus casos límite."""

    def test_arbol_identificado_es_vigente_sin_historial(self):
        """Test 1."""
        tree = self.verificar()
        d = self.detectar()

        self.assertEqual(d.estado, ES_VIGENTE)
        self.assertEqual(d.validez.status, VALID)
        self.assertEqual(d.verificado_tree_sha, tree)
        self.assertEqual(d.tree_actual, tree)
        self.assertEqual(self.historial(), ())
        self.assertNotIn("Veredictos obsoletos", self.texto())

    def test_arbol_distinto_registra_una_entrada(self):
        """Test 2."""
        tree = self.verificar()
        self.cambiar_arbol()
        d = self.detectar(instante_deteccion=TS)

        self.assertEqual(d.estado, ES_OBSOLETO)
        self.assertEqual(d.validez.status, OBSOLETE)
        self.assertTrue(d.se_registro)
        self.assertEqual(d.verificado_tree_sha, tree)
        self.assertNotEqual(d.tree_actual, tree)

        h = self.historial()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0].tree_sha, tree)
        self.assertEqual(h[0].tree_actual, d.tree_actual)
        self.assertEqual(h[0].motivo, "tree_distinto")
        self.assertEqual(h[0].resultado, "pasa")
        self.assertEqual(h[0].detectado, TS)

    def test_no_duplica_la_misma_obsolescencia(self):
        """Test 3."""
        self.verificar()
        self.cambiar_arbol()
        self.detectar(instante_deteccion=TS)
        segundo = self.detectar(instante_deteccion="2026-09-27T11:00:00Z")
        tercero = self.detectar()

        self.assertTrue(segundo.ya_registrado)
        self.assertFalse(segundo.se_registro)
        self.assertFalse(tercero.se_registro)
        self.assertEqual(len(self.historial()), 1)

    def test_revert_exacto_deja_de_ser_obsoleto(self):
        """Test 4 — la identidad es el contenido, no la historia."""
        tree = self.verificar()
        self.cambiar_arbol()
        self.assertEqual(self.detectar().estado, ES_OBSOLETO)
        self.revertir()

        d = self.detectar()
        self.assertEqual(d.estado, ES_VIGENTE)
        self.assertEqual(d.validez.status, VALID)
        self.assertEqual(d.tree_actual, tree)
        # No se añade una segunda entrada ni se borra la primera.
        self.assertEqual(len(self.historial()), 1)

    def test_commit_con_mismo_contenigo_no_es_obsolescencia(self):
        """Test 5."""
        tree = self.verificar()
        _git(self.raiz, "commit", "-q", "--allow-empty", "-m", "vacio")

        d = self.detectar()
        self.assertEqual(d.estado, ES_VIGENTE)
        self.assertEqual(d.tree_actual, tree)
        self.assertEqual(self.historial(), ())

    def test_dirty_con_mismo_tree_sigue_vigente(self):
        """Test 6 — `working_tree_clean` no decide obsolescencia.

        Se ensucia el **índice** (borrando el fichero de su staging) sin
        cambiar el contenido efectivo: `git status` reporta cambios, pero el
        árbol efectivo es idéntico al verificado.
        """
        self.verificar()
        _git(self.raiz, "rm", "-q", "--cached", "modulo.py")  # sucio, sin cambio de contenido

        d = self.detectar()
        self.assertEqual(d.estado, ES_VIGENTE)
        self.assertEqual(self.historial(), ())

    def test_dirty_con_tree_distinto_es_obsoleto(self):
        """Test 7."""
        self.verificar()
        self.cambiar_arbol()

        d = self.detectar()
        self.assertEqual(d.estado, ES_OBSOLETO)
        self.assertEqual(len(self.historial()), 1)

    def test_no_reejecuta_la_verificacion(self):
        """§12 — solo se inspecciona el árbol."""
        with mock.patch.object(sc, "_ejecutar_comando") as ejecutar:
            self.detectar()
        ejecutar.assert_not_called()

    def test_no_modifica_ultimo_verdicto(self):
        """§9 — la verificación original se conserva íntegra."""
        tree = self.verificar()
        antes = self.estado().ultimo_veredicto
        self.cambiar_arbol()
        self.detectar()

        self.assertEqual(self.estado().ultimo_veredicto, antes)
        v = leer_ultima_verificacion(self.estado())
        self.assertEqual(v.resultado, "pasa")
        self.assertEqual(v.tree_sha, tree)
        self.assertEqual(v.comando, OK)


class TestCasosLimite(BaseObsolescencia):
    """Tests 8-11: ausencia, corrupción y Git no disponible."""

    def test_sin_verificacion_persistida_no_hace_nada(self):
        """Test 8 — un trabajo recién creado."""
        d = self.detectar()
        self.assertEqual(d.estado, ES_SIN_VERIFICACION)
        self.assertIsNone(d.validez)
        self.assertNotIn("Veredictos obsoletos", self.texto())

    def test_veredicto_antiguo_no_estructurado_es_compatible(self):
        """Test 9 — veredicto de texto libre (B14)."""
        actualizar_estado(WORK_ID, {"ultimo_veredicto": "42 tests, 3 fallos"}, self.raiz)
        self.cambiar_arbol()

        d = self.detectar()
        self.assertEqual(d.estado, ES_SIN_VERIFICACION)
        self.assertEqual(self.estado().ultimo_veredicto, "42 tests, 3 fallos")
        self.assertNotIn("Veredictos obsoletos", self.texto())

    def test_gramatica_corrupta_falla_explicitamente(self):
        """Test 10 — se declara `[verificacion:v1]` pero no es parseable."""
        actualizar_estado(
            WORK_ID,
            {"ultimo_veredicto": "[verificacion:v1] resultado=pasa"},
            self.raiz,
        )
        antes = self.texto()

        with self.assertRaises(ContratoEstadoInvalidoError) as ctx:
            self.detectar()
        self.assertIn("tree_sha", str(ctx.exception))
        # No se inventa nada ni se registra historial.
        self.assertEqual(self.texto(), antes)
        self.assertNotIn("Veredictos obsoletos", antes)

    def test_gramatica_sin_cerrar_la_marca_falla(self):
        actualizar_estado(
            WORK_ID, {"ultimo_veredicto": "[verificacion:v1 resultado=pasa"}, self.raiz
        )
        with self.assertRaises(ContratoEstadoInvalidoError):
            self.detectar()

    def test_git_no_disponible_no_modifica_nada(self):
        """Test 11 — no se afirma nada sin observación."""
        self.verificar()
        antes = self.texto()
        with mock.patch.object(
            work_obsolencia,
            "leer_estado_git",
            side_effect=EstadoGitIndisponibleError("boom", "git_no_disponible"),
        ):
            with self.assertRaises(EstadoGitIndisponibleError):
                self.detectar()

        self.assertEqual(self.texto(), antes)
        self.assertEqual(self.historial(), ())


class TestAutoridadYAtomicidad(BaseObsolescencia):
    """Tests 12-15: autoridad, atomicidad e idempotencia."""

    def test_escritura_atomica_del_historial(self):
        """Test 12 — se reutiliza el mecanismo atómico existente."""
        self.verificar()
        self.cambiar_arbol()
        with mock.patch.object(
            work_context, "escribir_documento_trabajo", wraps=escribir_documento_trabajo
        ) as escritor:
            self.detectar()

        escritor.assert_called_once()
        # No se abre el documento directamente para escribirlo.
        fuente = Path(work_obsolencia.__file__).read_text(encoding="utf-8")
        self.assertNotIn("open(", fuente)
        self.assertNotIn("write_text", fuente)

    def test_no_deja_ficheros_temporales(self):
        self.verificar()
        self.cambiar_arbol()
        self.detectar()
        self.detectar()
        base = self.raiz / ".work" / WORK_ID
        self.assertEqual(
            sorted(p.name for p in base.iterdir()),
            ["README.md", "assertions.md", "decisions.md", "state.md"],
        )

    def test_actualizar_estado_sigue_rechazando_el_historial(self):
        """Test 14 — la vía genérica no puede eludir la autoridad."""
        with self.assertRaises(ValueError) as ctx:
            actualizar_estado(WORK_ID, {"veredictos_obsoletos": ["x"]}, self.raiz)
        self.assertIn("veredictos_obsoletos", str(ctx.exception))

    def test_registrar_veredicto_obsoleto_rechaza_texto_libre(self):
        with self.assertRaises(ValueError):
            registrar_veredicto_obsoleto(WORK_ID, "OBSOLETO a mano", self.raiz)

    def test_registro_directo_es_idempotente(self):
        """Test 15 — la clave de una entrada es el `tree_sha` obsoleto."""
        entrada = formatear_veredicto_obsoleto(
            "pasa", "tree-verificado", "tree-actual", "tree_distinto"
        )
        registrar_veredicto_obsoleto(WORK_ID, entrada, self.raiz)
        registrar_veredicto_obsoleto(WORK_ID, entrada, self.raiz)
        self.assertEqual(len(self.historial()), 1)

    def test_varias_entradas_conviven(self):
        for verificado, actual in [("t1", "u1"), ("t2", "u2"), ("t3", "u3")]:
            registrar_veredicto_obsoleto(
                WORK_ID,
                formatear_veredicto_obsoleto("pasa", verificado, actual, "tree_distinto"),
                self.raiz,
            )
        h = self.historial()
        self.assertEqual([e.tree_sha for e in h], ["t1", "t2", "t3"])
        self.assertEqual([e.tree_actual for e in h], ["u1", "u2", "u3"])

    def test_mandato_protegido_no_cambia(self):
        """Test 13."""
        antes = self.estado()
        self._documentos_antes = {
            nombre: (self.raiz / ".work" / WORK_ID / nombre).read_text(encoding="utf-8")
            for nombre in ("decisions.md", "assertions.md")
        }
        self.verificar()
        self.cambiar_arbol()
        self.detectar()
        despues = self.estado()

        for campo in (
            "objetivo",
            "criterios",
            "alcance_incluido",
            "alcance_excluido",
            "restricciones",
            "dependencias_entorno",
            "titulo",
            "proyecto",
            "cierre_estado",
            "cierre_motivo",
            "ciclo_de_vida",
            "bloqueos",
            "siguiente_paso",
        ):
            with self.subTest(campo=campo):
                self.assertEqual(getattr(antes, campo), getattr(despues, campo))
        for nombre in ("decisions.md", "assertions.md"):
            with self.subTest(documento=nombre):
                self.assertEqual(
                    (self.raiz / ".work" / WORK_ID / nombre).read_text(encoding="utf-8"),
                    self._documentos_antes[nombre],
                )

    def test_integridad_documental_tras_registrar(self):
        self.verificar()
        self.cambiar_arbol()
        self.detectar()
        texto = self.texto()

        self.assertIn("Kind: work-state", texto)
        self.assertIn("Contract: b9-1", texto)
        self.assertIn(f"Work: {WORK_ID}", texto)
        self.assertIn("## Estado operativo", texto)
        # La clave vive en la sección correcta, no en una nueva.
        estado_operativo = texto.split("## Estado operativo")[1].split("## Referencias Git")[0]
        self.assertIn("Veredictos obsoletos", estado_operativo)


class TestProcesosIndependientes(BaseObsolescencia):
    """Test 23 — A persiste, B modifica y detecta, C lee. Sin memoria compartida."""

    def _proceso(self, script: str) -> str:
        return (
            subprocess.run(
                [sys.executable, "-c", textwrap.dedent(script)],
                check=True,
                capture_output=True,
                text=True,
                cwd=str(RAIZ),
            )
            .stdout.strip()
            .splitlines()[-1]
        )

    def test_ciclo_entre_tres_procesos(self):
        # Proceso A: F4 verifica y persiste.
        self.verificar()

        # Proceso B: modifica el árbol y ejecuta la detección.
        self.cambiar_arbol()
        salida_b = self._proceso(
            f"""
            import json, sys
            sys.path.insert(0, {str(RAIZ)!r})
            from work_obsolencia import detectar_obsolescencia
            d = detectar_obsolescencia({WORK_ID!r}, {str(self.raiz)!r},
                                       instante_deteccion={TS!r})
            print(json.dumps({{"estado": d.estado, "se_registro": d.se_registro,
                              "entradas": d.entradas_historicas}}))
            """
        )
        self.assertEqual(
            json.loads(salida_b),
            {"estado": ES_OBSOLETO, "se_registro": True, "entradas": 1},
        )

        # Proceso C: solo lee el documento persistido.
        salida_c = self._proceso(
            f"""
            import json, sys
            sys.path.insert(0, {str(RAIZ)!r})
            from work_context import leer_estado, leer_veredictos_obsoletos
            estado = leer_estado({WORK_ID!r}, {str(self.raiz)!r})
            h = leer_veredictos_obsoletos(estado)
            print(json.dumps({{"n": len(h), "tree_sha": h[0].tree_sha,
                              "motivo": h[0].motivo, "detectado": h[0].detectado,
                              "ultimo": estado.ultimo_veredicto.split(" ")[0]}}))
            """
        )
        datos = json.loads(salida_c)
        self.assertEqual(datos["n"], 1)
        self.assertEqual(datos["motivo"], "tree_distinto")
        self.assertEqual(datos["detectado"], TS)
        # La verificación original sigue ahí, no fue reemplazada.
        self.assertEqual(datos["ultimo"], "[verificacion:v1]")


class TestFronteras(unittest.TestCase):
    """§11, §24, §25: pureza y alcance."""

    def test_el_evaluador_no_depende_de_la_obsolescencia(self):
        import ast

        fuente = Path(work_verdict.__file__).read_text(encoding="utf-8")
        arbol = ast.parse(fuente)
        # Independencia real: ningún import del canal WORK ni de la operación.
        importadas = set()
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                importadas.update(alias.name.split(".")[0] for alias in nodo.names)
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                importadas.add(nodo.module.split(".")[0])
        for prohibido in ("work_context", "work_obsolencia"):
            self.assertNotIn(prohibido, importadas)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "evaluar_validez_verdict":
                cuerpo = ast.get_source_segment(fuente, nodo).replace(
                    ast.get_docstring(nodo, clean=False), ""
                )
        for prohibido in ("work_context", "open(", "state.md", "registrar_"):
            self.assertNotIn(prohibido, cuerpo)

    def test_la_operacion_delega_la_escritura(self):
        """§21 — la obsolescencia no escribe el documento por su cuenta."""
        fuente = Path(work_obsolencia.__file__).read_text(encoding="utf-8")
        codigo = "\n".join(l.split("#", 1)[0] for l in fuente.splitlines())
        self.assertIn("registrar_veredicto_obsoleto", codigo)
        for prohibido in ("escribir_documento_trabajo", "write_text", "open("):
            self.assertNotIn(prohibido, codigo)

    def test_la_operacion_delega_la_decision_al_evaluador(self):
        """§11 — la decisión es del evaluador puro."""
        fuente = Path(work_obsolencia.__file__).read_text(encoding="utf-8")
        self.assertIn("evaluar_validez_verdict", fuente)
        codigo = "\n".join(l.split("#", 1)[0] for l in fuente.splitlines())
        # No reimplementa la comparación de árboles.
        for prohibido in ("tree_sha ==", "!= actual", "if anchor"):
            self.assertNotIn(prohibido, codigo)

    def test_no_se_integra_en_f1_f2_ni_mcp(self):
        """§24, §25."""
        for modulo in ("agentes.py", "orquestador.py", "qa_tester_logic.py", "mcp_tools.py"):
            with self.subTest(modulo=modulo):
                fuente = (RAIZ / modulo).read_text(encoding="utf-8")
                self.assertNotIn("detectar_obsolescencia", fuente)
                self.assertNotIn("registrar_veredicto_obsoleto", fuente)

    def test_f4_no_detecta_obsolescencia_automaticamente(self):
        """§10 — la detección es explícita, no implícita en cada verificación."""
        fuente = Path(react_agent.__file__).read_text(encoding="utf-8")
        self.assertNotIn("obsolescencia", fuente)
        self.assertNotIn("detectar_obsolescencia", fuente)


if __name__ == "__main__":
    unittest.main()
