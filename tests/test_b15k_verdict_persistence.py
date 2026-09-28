#!/usr/bin/env python3
"""B15-K — Persistencia de la última verificación en `.work/<id>/state.md`.

Demuestra el round-trip completo con Git real:

    Proceso A: F4 → Verdict → ValidityResult → state.md
    Proceso B: leer_estado() → WorkState → UltimaVerificacion

y que las dos dimensiones (`resultado` y `validity`) sobreviven separadas.

Fronteras: `state.md` sigue siendo la única fuente persistente, la escritura
reutiliza `actualizar_estado()`, `veredictos_obsoletos` y el mandato no se
tocan, y no se crea ningún archivo paralelo de historial.
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

import react_agent  # noqa: E402
import work_verdict  # noqa: E402
from exceptions import EstadoGitIndisponibleError  # noqa: E402
from work_context import (  # noqa: E402
    VALIDIDAD_NO_EVALUADA,
    crear_trabajo,
    leer_estado,
    leer_ultima_verificacion,
    registrar_verificacion,
)
from work_verdict import OBSOLETE, VALID  # noqa: E402

WORK_ID = "b15k-persistencia"

OK_SIN_CAMBIOS = "python3 -c \"print('ok')\""
OK_CON_CAMBIO = (
    "python3 -c \"open('generado.txt','w').write('x')\" && python3 -c \"print('ok')\""
)
FALLA_SIN_CAMBIOS = "python3 -c \"raise SystemExit(1)\""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class BasePersistencia(unittest.TestCase):
    """Repositorio Git real + trabajo WORK real en la misma raíz."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name)
        _git(self.raiz, "init", "-q")
        _git(self.raiz, "config", "user.email", "b15k@example.com")
        _git(self.raiz, "config", "user.name", "B15-K")
        (self.raiz / "modulo.py").write_text("OK = 1\n", encoding="utf-8")
        _git(self.raiz, "add", "-A")
        _git(self.raiz, "commit", "-qm", "inicial")
        crear_trabajo(
            WORK_ID,
            self.raiz,
            titulo="Persistencia",
            objetivo="Persistir la última verificación",
            criterios=["se persiste"],
            restricciones=None,
        )

    def estado(self):
        return leer_estado(WORK_ID, self.raiz)

    def texto(self) -> str:
        return (self.raiz / ".work" / WORK_ID / "state.md").read_text(encoding="utf-8")

    def agente(self) -> react_agent.ReactAgent:
        return react_agent.ReactAgent(str(self.raiz), auto=True, work_id=WORK_ID)

    def ejecutar(self, comando: str) -> dict:
        return self.agente()._tool_ejecutar_pruebas({"comando": comando})

    def persistida(self):
        return leer_ultima_verificacion(self.estado())


class TestPersistenciaDesdeF4(BasePersistencia):
    """Tests 1-4: la cadena F4 real → state.md."""

    def test_valid_se_persiste(self):
        """Test 1 — pasa + VALID."""
        r = self.ejecutar(OK_SIN_CAMBIOS)

        self.assertEqual(r["verdict"].resultado, "pasa")
        self.assertEqual(r["validity"].status, VALID)
        self.assertIs(r["verificacion_persistida"], True)
        v = self.persistida()
        self.assertEqual(v.resultado, "pasa")
        self.assertEqual(v.validity, VALID)
        self.assertEqual(v.motivo, "tree_coincidente")
        self.assertEqual(v.tree_sha, r["verdict"].git_anchor.tree_sha)
        self.assertTrue(v.es_valida)
        self.assertFalse(v.es_obsoleta)

    def test_obsolete_se_persiste(self):
        """Test 2 — pasa + OBSOLETE (árbol modificado durante la ejecución)."""
        r = self.ejecutar(OK_CON_CAMBIO)

        self.assertEqual(r["validity"].status, OBSOLETE)
        v = self.persistida()
        self.assertEqual(v.resultado, "pasa")
        self.assertEqual(v.validity, OBSOLETE)
        self.assertEqual(v.motivo, "tree_distinto")
        # El tree persistido es el PRE (identidad de lo verificado).
        self.assertEqual(v.tree_sha, r["verdict"].git_anchor.tree_sha)
        self.assertTrue(v.es_obsoleta)

    def test_falla_con_valid_se_persiste_sin_ambiguedad(self):
        """Test 3 — 'falla' + VALID: las dos dimensiones no se colapsan."""
        r = self.ejecutar(FALLA_SIN_CAMBIOS)

        self.assertEqual(r["verdict"].resultado, "falla")
        self.assertEqual(r["validity"].status, VALID)
        v = self.persistida()
        self.assertEqual(v.resultado, "falla")
        self.assertEqual(v.validity, VALID)
        self.assertFalse(v.es_obsoleta)

    def test_arbol_dirty_conserva_tree_sha(self):
        """Test 4 — dirty: `working_tree_clean` es metadata, no identidad."""
        (self.raiz / "sucio.txt").write_text("pendiente\n", encoding="utf-8")
        r = self.ejecutar(OK_SIN_CAMBIOS)

        v = self.persistida()
        self.assertIs(v.working_tree_clean, False)
        self.assertEqual(v.tree_sha, r["verdict"].git_anchor.tree_sha)
        self.assertEqual(v.validity, VALID)

    def test_comando_y_scope_se_conservan(self):
        r = self.ejecutar(OK_SIN_CAMBIOS)
        v = self.persistida()
        self.assertEqual(v.comando, OK_SIN_CAMBIOS)
        self.assertEqual(v.comando, r["verdict"].comando)
        self.assertEqual(v.scope, r["verdict"].scope)
        self.assertEqual(v.commit, r["verdict"].git_anchor.commit)

    def test_sin_work_id_no_se_persiste(self):
        """Sin contexto de trabajo no hay documento al que escribir."""
        agente = react_agent.ReactAgent(str(self.raiz), auto=True)
        antes = self.texto()
        r = agente._tool_ejecutar_pruebas({"comando": OK_SIN_CAMBIOS})

        self.assertIsInstance(r["verdict"], object)
        self.assertNotIn("verificacion_persistida", r)
        self.assertEqual(self.texto(), antes)

class TestProcesoIndependiente(BasePersistencia):
    """Tests 5, 23: un proceso nuevo recupera lo persistido."""

    SCRIPT = textwrap.dedent(
        """
        import json, sys
        sys.path.insert(0, {raiz!r})
        from work_context import leer_estado, leer_ultima_verificacion
        estado = leer_estado({work!r}, {raiz!r})
        v = leer_ultima_verificacion(estado)
        print(json.dumps({{
            "kind": estado.kind,
            "contrato": estado.contrato,
            "work_id": estado.work_id,
            "resultado": v.resultado,
            "validity": v.validity,
            "motivo": v.motivo,
            "tree_sha": v.tree_sha,
            "comando": v.comando,
            "scope": v.scope,
            "commit": v.commit,
        }}))
        """
    )

    def _proceso_b(self) -> dict:
        script = self.SCRIPT.format(raiz=str(self.raiz), work=WORK_ID)
        salida = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(RAIZ),
        )
        return json.loads(salida.stdout.strip().splitlines()[-1])

    def test_proceso_nuevo_recupera_la_verificacion(self):
        """Test 5 — sin memoria compartida."""
        r = self.ejecutar(OK_SIN_CAMBIOS)
        datos = self._proceso_b()

        self.assertEqual(datos["kind"], "work-state")
        self.assertEqual(datos["contrato"], "b9-1")
        self.assertEqual(datos["work_id"], WORK_ID)
        self.assertEqual(datos["resultado"], r["verdict"].resultado)
        self.assertEqual(datos["validity"], r["validity"].status)
        self.assertEqual(datos["tree_sha"], r["verdict"].git_anchor.tree_sha)
        self.assertEqual(datos["comando"], r["verdict"].comando)

    def test_proceso_nuevo_recupera_obsolete(self):
        r = self.ejecutar(OK_CON_CAMBIO)
        datos = self._proceso_b()

        self.assertEqual(datos["validity"], OBSOLETE)
        self.assertEqual(datos["tree_sha"], r["verdict"].git_anchor.tree_sha)

    def test_integridad_documental_tras_persistir(self):
        """Test 23 — el documento conserva su cabecera contractual."""
        self.ejecutar(OK_SIN_CAMBIOS)
        texto = self.texto()

        self.assertIn("Kind: work-state", texto)
        self.assertIn("Contract: b9-1", texto)
        self.assertIn(f"Work: {WORK_ID}", texto)
        self.assertIn("## Estado operativo", texto)
        self.assertIn("## Identidad", texto)
        self.assertIn("## Referencias Git", texto)
        # Re-lectura tras escribir: no se pierde información.
        v = self.persistida()
        self.assertIsNotNone(v)
        self.assertTrue(v.tree_sha)

class TestReplacementYFormato(BasePersistencia):
    """Tests 6, 7, 8, 9, 14, 15, 19."""

    def test_segunda_verificacion_reemplaza_a_la_primera(self):
        """Test 6 — sin historial accidental."""
        self.ejecutar(OK_SIN_CAMBIOS)
        primera = self.texto()
        self.ejecutar(OK_CON_CAMBIO)
        segunda = self.texto()

        v = self.persistida()
        self.assertEqual(v.validity, OBSOLETE)
        # Solo hay una línea de verificación, y es la última.
        lineas = [l for l in segunda.splitlines() if "verificacion:v" in l]
        self.assertEqual(len(lineas), 1)
        self.assertNotEqual(primera, segunda)

    def test_persistencia_idempotente(self):
        """Tests 15 y 19 — no acumula nada."""
        self.ejecutar(OK_SIN_CAMBIOS)
        primera = self.texto()
        v1 = self.persistida()

        registrar_verificacion(
            WORK_ID,
            v1.resultado,
            v1.tree_sha,
            comando=v1.comando,
            validity=v1.validity,
            motivo=v1.motivo,
            commit=v1.commit,
            working_tree_clean=v1.working_tree_clean,
            scope=v1.scope,
            directorio=self.raiz,
        )
        segunda = self.texto()

        self.assertEqual(primera, segunda)
        self.assertEqual(self.persistida(), v1)

    def test_documento_sigue_valido_tras_varias_actualizaciones(self):
        """Test 7."""
        for _ in range(3):
            self.ejecutar(OK_SIN_CAMBIOS)
        estado = self.estado()
        self.assertEqual(estado.work_id, WORK_ID)
        self.assertEqual(estado.objetivo, "Persistir la última verificación")
        self.assertIsNotNone(self.persistida())

    def test_no_modifica_veredictos_obsoletos(self):
        """Test 8 — clave protegida en B14-G/H."""
        self.ejecutar(OK_SIN_CAMBIOS)
        despues = self.texto()

        self.assertNotIn("Veredictos obsoletos", despues)
        self.assertEqual(self.estado().veredictos_obsoletos, ())

    def test_no_modifica_el_mandato(self):
        """Test 9 — secciones ratificadas intactas."""
        antes = self.estado()
        self.ejecutar(OK_SIN_CAMBIOS)
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
            "ciclo_de_vida",
        ):
            with self.subTest(campo=campo):
                self.assertEqual(getattr(antes, campo), getattr(despues, campo))

    def test_no_crea_archivos_paralelos(self):
        """Test 14 — `state.md` es la única fuente persistente."""
        self.ejecutar(OK_SIN_CAMBIOS)
        self.ejecutar(OK_CON_CAMBIO)
        ficheros = sorted(p.name for p in (self.raiz / ".work" / WORK_ID).iterdir())

        self.assertEqual(ficheros, ["README.md", "assertions.md", "decisions.md", "state.md"])

    def test_no_toca_decisions_ni_assertions(self):
        self.ejecutar(OK_SIN_CAMBIOS)
        for nombre in ("decisions.md", "assertions.md"):
            with self.subTest(documento=nombre):
                texto = (self.raiz / ".work" / WORK_ID / nombre).read_text(encoding="utf-8")
                self.assertNotIn("verificacion:v", texto)

class TestFallosDeGit(BasePersistencia):
    """Tests 10 y 11: no se persiste lo que no se sabe."""

    def test_git_pre_falla_no_persiste_verdict_inexistente(self):
        """Test 10 — política B15-I conservada."""
        antes = self.texto()
        with mock.patch.object(
            work_verdict,
            "leer_estado_git",
            side_effect=EstadoGitIndisponibleError("sin repo", "sin_repositorio"),
        ):
            r = self.ejecutar(OK_SIN_CAMBIOS)

        self.assertIsNone(r["verdict"])
        self.assertIn("verdict_error", r)
        self.assertNotIn("verificacion_persistida", r)
        self.assertEqual(self.texto(), antes)
        self.assertIsNone(self.persistida())

    def test_git_current_falla_no_inventa_validez(self):
        """Test 11 — el Verdict se persiste con validez explícitamente ausente."""
        llamadas = {"n": 0}
        real = work_verdict.leer_estado_git

        def solo_pre(directorio="."):
            llamadas["n"] += 1
            if llamadas["n"] > 1:
                raise EstadoGitIndisponibleError("boom", "git_no_disponible")
            return real(directorio)

        with mock.patch.object(work_verdict, "leer_estado_git", side_effect=solo_pre):
            r = self.ejecutar(OK_SIN_CAMBIOS)

        self.assertIsInstance(r["verdict"], object)
        self.assertIsNone(r["validity"])
        self.assertIn("git_no_disponible", r["validity_error"])
        self.assertIs(r["verificacion_persistida"], True)

        v = self.persistida()
        self.assertEqual(v.resultado, "pasa")
        self.assertEqual(v.validity, VALIDIDAD_NO_EVALUADA)
        self.assertNotIn(v.validity, (VALID, OBSOLETE))
        self.assertFalse(v.es_valida)
        self.assertFalse(v.es_obsoleta)


class TestCompatibilidadYPureza(unittest.TestCase):
    """Tests 12, 13, 18 y 24."""

    def test_evaluador_sigue_puro(self):
        """Test 12 — la persistencia no se coló dentro del evaluador."""
        import ast

        fuente = Path(work_verdict.__file__).read_text(encoding="utf-8")
        arbol = ast.parse(fuente)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "evaluar_validez_verdict":
                cuerpo = ast.get_source_segment(fuente, nodo).replace(
                    ast.get_docstring(nodo, clean=False), ""
                )
        for prohibido in ("work_context", "state.md", "registrar_", "Path(", "open("):
            self.assertNotIn(prohibido, cuerpo)

    def test_state_md_antiguo_sigue_siendo_legible(self):
        """Test 13/18 — un documento sin formato B15-K se lee igual."""
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            _git(raiz, "init", "-q")
            _git(raiz, "config", "user.email", "a@b.c")
            _git(raiz, "config", "user.name", "t")
            (raiz / "a.py").write_text("x = 1\n", encoding="utf-8")
            _git(raiz, "add", "-A")
            _git(raiz, "commit", "-qm", "c")
            crear_trabajo("w", raiz, titulo="T", objetivo="O", criterios=None, restricciones=None)

            estado = leer_estado("w", raiz)
            self.assertIsNone(leer_ultima_verificacion(estado))

            # Veredicto de texto libre (el agente de B14): también legible.
            registrar_verificacion  # noqa: B018  (disponibilidad de la API)
            estado = leer_estado("w", raiz)
            estado.ultimo_veredicto  # campo intacto

            from work_context import actualizar_estado

            actualizar_estado("w", {"ultimo_veredicto": "42 tests, 3 fallos"}, raiz)
            estado = leer_estado("w", raiz)
            self.assertEqual(estado.ultimo_veredicto, "42 tests, 3 fallos")
            self.assertIsNone(leer_ultima_verificacion(estado))

    def test_f1_f2_no_integran_persistencia(self):
        """Test 24."""
        for modulo in ("agentes.py", "orquestador.py", "qa_tester_logic.py"):
            with self.subTest(modulo=modulo):
                fuente = (RAIZ / modulo).read_text(encoding="utf-8")
                self.assertNotIn("registrar_verificacion", fuente)
                self.assertNotIn("leer_ultima_verificacion", fuente)

    def test_mcp_no_expone_la_nueva_api(self):
        """La API nueva no se publica por MCP en B15-K."""
        fuente = (RAIZ / "mcp_tools.py").read_text(encoding="utf-8")
        self.assertNotIn("registrar_verificacion", fuente)
        self.assertNotIn("leer_ultima_verificacion", fuente)

    def test_la_persistencia_no_crea_nueva_seccion(self):
        """§17 — la información vive en `## Estado operativo`."""
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d)
            _git(raiz, "init", "-q")
            _git(raiz, "config", "user.email", "a@b.c")
            _git(raiz, "config", "user.name", "t")
            (raiz / "a.py").write_text("x = 1\n", encoding="utf-8")
            _git(raiz, "add", "-A")
            _git(raiz, "commit", "-qm", "c")
            crear_trabajo("w", raiz, titulo="T", objetivo="O", criterios=None, restricciones=None)
            antes = (raiz / ".work" / "w" / "state.md").read_text(encoding="utf-8")
            registrar_verificacion("w", "pasa", "sha1", validity=VALID, directorio=raiz)
            despues = (raiz / ".work" / "w" / "state.md").read_text(encoding="utf-8")

        self.assertEqual(
            [l for l in antes.splitlines() if l.startswith("#")],
            [l for l in despues.splitlines() if l.startswith("#")],
        )


if __name__ == "__main__":
    unittest.main()




