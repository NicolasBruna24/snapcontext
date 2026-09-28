"""Tests B14-I — Vertical slice de escritura de Work State por un agente.

Camino real ejercitado (el mismo que usa el planificador cuando el LLM
propone un paso `{"accion": "mcp", "herramienta": ...}`):

    LLM (planificador)
      -> snapcontext._ejecutar_paso_plan   <- paso real del agente
      -> mcp_tools._ejecutar_herramienta_mcp  <- dispatcher MCP real
      -> _tool_work_state_update  ->  _exigir_autoridad  ->  actualizar_estado
      -> state.md
      -> _tool_work_state  ->  leer_estado  ->  WorkState

No se invoca `_exigir_autoridad` ni `actualizar_estado` directamente en el
camino principal: el slice atraviesa las capas reales de extremo a extremo.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import snapcontext as sc
from work_context import crear_trabajo

WORK_ID = "b14i-slice"

VERDICT = (
    "pasa — comando `python3 -m pytest tests/ -q`; scope: proyecto completo; "
    "anchor Git: 45e2e0a71d06; autor: Agente B; instante: 2026-09-27T01:00:00Z"
)

RAIZ_REPO = Path(__file__).resolve().parents[1]


def _args(extra=None):
    base = {
        "plan": True,
        "consulta": "tarea",
        "auto": True,
        "paralelo": 1,
        "git_commit": False,
        "confirmar": False,
        "branch": None,
        "modelo": None,
        "depurar": False,
    }
    base.update(extra or {})
    return argparse.Namespace(**base)


class _BaseSlice(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name) / "proj"
        self.raiz.mkdir(parents=True)
        # Estado inicial conocido (§2): trabajo pendiente + siguiente paso.
        crear_trabajo(
            WORK_ID,
            self.raiz,
            titulo="Division segura",
            objetivo="Soportar division segura.",
            criterios=["safe_divide existe"],
        )
        # Camino real: el planificador pide confirmación interactiva; en un
        # test sin TTY se denegaría, así que se autoriza explícitamente.
        self._permiso = mock.patch.object(
            sc, "_confirmar_accion", return_value=True
        )
        self._permiso.start()
        self.addCleanup(self._permiso.stop)
        sc._contexto_plan_reiniciar()
        self.addCleanup(sc._contexto_plan_reiniciar)

    @property
    def _state(self) -> Path:
        return self.raiz / ".work" / WORK_ID / "state.md"

    def _texto(self) -> str:
        return self._state.read_text(encoding="utf-8")

    def _paso(self, herramienta, argumentos, **extra):
        """Construye el paso MCP que el planificador ejecutaría."""
        paso = {
            "descripcion": f"usar {herramienta}",
            "accion": "mcp",
            "herramienta": herramienta,
            "args": argumentos,
            "variable": "",
        }
        paso.update(extra)
        return paso

    def _agente_actualiza(self, cambios, autorizacion=False) -> tuple:
        """Consumidor: escribe por el camino real del agente."""
        argumentos = {
            "work_id": WORK_ID,
            "cambios": cambios,
            "autorizacion": autorizacion,
            "directorio": str(self.raiz),
        }
        return sc._ejecutar_paso_plan(
            self._paso("work_state_update", argumentos), _args(), str(self.raiz)
        )

    def _agente_consulta(self) -> dict:
        """Consumidor: lee por el camino real del agente."""
        argumentos = {"work_id": WORK_ID, "directorio": str(self.raiz)}
        ok, _ = sc._ejecutar_paso_plan(
            self._paso("work_state", argumentos), _args(), str(self.raiz)
        )
        self.assertTrue(ok)
        # `_ejecutar_paso_plan` publica el resultado del paso en el contexto
        # del plan, bajo el nombre de la herramienta.
        return sc._CONTEXTO_PLAN["variables"]["work_state"]


# ===========================================================================
# §2 — Escenario principal: escritura directa
# ===========================================================================
class TestEscrituraDirectaSlice(_BaseSlice):
    def test_agente_actualiza_y_luego_consulta(self):
        inicial = self._agente_consulta()
        self.assertEqual(inicial["siguiente_paso"], "(pendiente de declarar)")
        self.assertEqual(inicial["trabajo_completado"], ["(nada registrado)"])

        # Escritura directa por el camino real, sin autorización.
        ok, detalle = self._agente_actualiza(
            {
                "trabajo_completado": "implementar X",
                "siguiente_paso": "ejecutar verificación",
            }
        )
        self.assertTrue(ok, detalle)

        # Lectura por el camino real: refleja exactamente lo persistido.
        leido = self._agente_consulta()
        self.assertEqual(leido["trabajo_completado"], ["implementar X"])
        self.assertEqual(leido["siguiente_paso"], "ejecutar verificación")
        # Y el documento físico lo confirma.
        self.assertIn("- Trabajo completado: implementar X", self._texto())
        self.assertIn("- Siguiente paso: ejecutar verificación", self._texto())

    def test_el_documento_fisico_coincide_con_la_lectura(self):
        self._agente_actualiza({"siguiente_paso": "paso real"})
        leido = self._agente_consulta()
        self.assertIn(f"- Siguiente paso: {leido['siguiente_paso']}", self._texto())


# ===========================================================================
# §3 — Escenario de confirmación
# ===========================================================================
class TestConfirmacionSlice(_BaseSlice):
    def test_sin_autorizacion_rechaza_y_no_escribe(self):
        antes = self._texto()
        ok, _ = self._agente_actualiza({"ciclo_de_vida": "cerrado"})
        self.assertFalse(ok)
        # Sin escritura parcial: el documento es idéntico byte a byte.
        self.assertEqual(self._texto(), antes)
        self.assertEqual(self._agente_consulta()["ciclo_de_vida"], "declarado")

    def test_el_rechazo_es_distinguible_de_una_entrada_invalida(self):
        # Autorización: el paso falla, pero `state.md` no se toca.
        self._agente_actualiza({"ciclo_de_vida": "cerrado"})
        # Entrada inválida: mismo desenlace del paso, otra causa.
        ok, _ = self._agente_actualiza({"campo_inexistente": "x"})
        self.assertFalse(ok)
        # La causa se distingue consultando el resultado del dispatcher.
        from exceptions import (
            AutoridadInsuficienteError,
            ContratoEstadoInvalidoError,
        )
        from snapcontext import _tool_work_state_update

        r_aut = _tool_work_state_update(
            WORK_ID, {"ciclo_de_vida": "cerrado"}, False, str(self.raiz)
        )
        r_ent = _tool_work_state_update(
            WORK_ID, {"campo_inexistente": "x"}, False, str(self.raiz)
        )
        self.assertEqual(r_aut["categoria"], "autoridad")
        self.assertEqual(r_ent["categoria"], "entrada")
        # Las excepciones también son distintas.
        with self.assertRaises(AutoridadInsuficienteError):
            sc._exigir_autoridad({"ciclo_de_vida": "cerrado"}, False)
        with self.assertRaises(ValueError):
            sc._exigir_autoridad({"campo_inexistente": "x"}, False)
        self.assertTrue(issubclass(AutoridadInsuficienteError, ValueError))
        self.assertTrue(issubclass(ContratoEstadoInvalidoError, ValueError))

    def test_con_autorizacion_persiste(self):
        ok, detalle = self._agente_actualiza(
            {"ciclo_de_vida": "cerrado"}, autorizacion=True
        )
        self.assertTrue(ok, detalle)
        self.assertEqual(self._agente_consulta()["ciclo_de_vida"], "cerrado")
        self.assertIn("- Ciclo de vida: cerrado", self._texto())


# ===========================================================================
# §4 — Escenario protegido
# ===========================================================================
class TestProtegidoSlice(_BaseSlice):
    def test_mandato_rechazado_incluso_con_autorizacion(self):
        from snapcontext import _tool_work_state

        antes = self._texto()
        ok, _ = self._agente_actualiza({"criterios": ["criterio inyectado"]}, True)
        self.assertFalse(ok)
        self.assertEqual(self._texto(), antes)
        # Y el Mandato sigue idéntico tras consultarlo por `work_state`.
        leido = _tool_work_state(WORK_ID, str(self.raiz))
        self.assertTrue(leido["ok"], leido.get("error"))
        # Los criterios originales (creados en el fixture) siguen ahí.
        self.assertNotIn("criterio inyectado", self._texto())
        self.assertIn("- safe_divide existe", self._texto())

    def test_veredictos_obsoletos_rechazado(self):
        antes = self._texto()
        ok, _ = self._agente_actualiza({"veredictos_obsoletos": ["x"]}, True)
        self.assertFalse(ok)
        self.assertEqual(self._texto(), antes)

    def test_no_hay_via_alternativa_para_tocar_el_mandato(self):
        antes = self._texto()
        for alias in ("objetivo_nuevo", "nuevos_criterios", "cerrado", "finalizar"):
            with self.subTest(alias=alias):
                self._agente_actualiza({alias: "x"}, True)
                self.assertEqual(self._texto(), antes)


# ===========================================================================
# §5 — Escenario de continuidad (el central)
# ===========================================================================
_SCRIPT_LECTOR = """
import json, sys
sys.path.insert(0, {raiz_repo!r})
import snapcontext as sc

# Consumidor B: proceso NUEVO. No comparte memoria con el consumidor A.
sc._confirmar_accion = lambda *a, **k: True
llamada = sc._ejecutar_herramienta_mcp(
    "work_state",
    {{"work_id": {work_id!r}, "directorio": {directorio!r}}},
    confirmar=True,
)
print(json.dumps(llamada["resultado"]))
"""


class TestContinuidadSlice(_BaseSlice):
    def test_consumidor_b_ve_lo_que_persistio_a_en_otro_proceso(self):
        # --- Consumidor A: lee y registra progreso -----------------------
        inicial = self._agente_consulta()
        self.assertEqual(inicial["siguiente_paso"], "(pendiente de declarar)")
        ok, detalle = self._agente_actualiza(
            {"siguiente_paso": "Continuar con Y", "trabajo_completado": "hecho Z"}
        )
        self.assertTrue(ok, detalle)
        # A descarta su memoria: no se pasa nada a B.

        # --- Consumidor B: proceso del sistema operativo independiente ----
        script = _SCRIPT_LECTOR.format(
            raiz_repo=str(RAIZ_REPO),
            work_id=WORK_ID,
            directorio=str(self.raiz),
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        visto_por_b = json.loads(proc.stdout.strip().splitlines()[-1])

        # B observa el estado persistido por A.
        self.assertEqual(visto_por_b["siguiente_paso"], "Continuar con Y")
        self.assertEqual(visto_por_b["trabajo_completado"], ["hecho Z"])
        # Y coincide con lo que A había escrito, leído ahora desde disco.
        self.assertEqual(visto_por_b["siguiente_paso"], "Continuar con Y")

    def test_el_estado_sobrevive_a_la_persistencia_en_disco(self):
        """Evidencia directa: lo que B lee viene del fichero, no de memoria."""
        self._agente_actualiza({"siguiente_paso": "desde disco"})
        # Se relee el fichero frío, sin ninguna estructura en memoria.
        from work_context import leer_estado

        estado_en_disco = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado_en_disco.siguiente_paso, "desde disco")
        # Y el paso MCP en este mismo proceso ve lo mismo.
        self.assertEqual(self._agente_consulta()["siguiente_paso"], "desde disco")


# ===========================================================================
# §6 — Escenario `titulo`
# ===========================================================================
class TestTituloSlice(_BaseSlice):
    def test_titulo_via_el_camino_real_y_documento_coherente(self):
        ok, detalle = self._agente_actualiza({"titulo": "Titulo del agente"})
        self.assertTrue(ok, detalle)
        # Lectura por el camino real.
        self.assertEqual(self._agente_consulta()["titulo"], "Titulo del agente")
        # Y el documento FÍSICO: H1 y campo canónico coinciden (§6).
        texto = self._texto()
        self.assertIn("# Trabajo: Titulo del agente", texto)
        self.assertIn("- Título: Titulo del agente", texto)

    def test_titulo_conserva_coherencia_tras_varias_operaciones(self):
        for titulo in ("Uno", "Dos", "Tres"):
            self._agente_actualiza({"titulo": titulo})
            texto = self._texto()
            self.assertIn(f"# Trabajo: {titulo}", texto)
            self.assertIn(f"- Título: {titulo}", texto)
        self.assertEqual(self._agente_consulta()["titulo"], "Tres")


# ===========================================================================
# §7 / §8 — Verdict y referencias Git: transporte declarativo
# ===========================================================================
class TestVerdictYGitSlice(_BaseSlice):
    def test_verdict_se_persiste_y_se_devuelve_literal(self):
        ok, detalle = self._agente_actualiza({"ultimo_veredicto": VERDICT})
        self.assertTrue(ok, detalle)
        leido = self._agente_consulta()
        # Literal: sin transformar, sin campos descompuestos.
        self.assertEqual(leido["ultimo_veredicto"], VERDICT)
        self.assertIn(f"- Último veredicto de verificación: {VERDICT}", self._texto())
        # No aparece ninguna afirmación adicional de validez/verificación.
        for clave in leido:
            with self.subTest(clave=clave):
                self.assertNotIn("verificado", clave.lower())
                self.assertNotIn("valido", clave.lower())
        self.assertNotIn("OBSOLETO", self._texto())

    def test_referencias_git_se_persisten_como_texto(self):
        ok, detalle = self._agente_actualiza(
            {"git_actual": "feat/slice", "git_rama": "feat/slice"}
        )
        self.assertTrue(ok, detalle)
        leido = self._agente_consulta()
        self.assertEqual(leido["git"]["actual"], "feat/slice")
        self.assertEqual(leido["git"]["rama"], "feat/slice")
        self.assertIn("- Rama: feat/slice", self._texto())
        # No se convierte en evidencia verificada (no hay clean/dirty).
        self.assertNotIn("clean", self._texto())
        self.assertNotIn("dirty", self._texto())

    def test_el_slice_no_ejecuta_git(self):
        """El proyecto no es un repo Git y la operación aun así funciona."""
        import subprocess as sp

        with mock.patch.object(
            sp, "run", side_effect=AssertionError("se intentó ejecutar un proceso")
        ):
            ok, _ = self._agente_actualiza(
                {"ultimo_veredicto": VERDICT, "git_rama": "cualquiera"}
            )
        self.assertTrue(ok)
        self.assertEqual(self._agente_consulta()["git"]["rama"], "cualquiera")


# ===========================================================================
# §10 — Superficie CLI `/tool` (misma capa, sin lógica duplicada)
# ===========================================================================
class TestSuperficieCli(_BaseSlice):
    def test_tool_actualiza_y_tool_consulta(self):
        """`/tool` es una superficie real de consumo; comparte la política."""
        import mcp_tools

        with mock.patch.object(sc, "_confirmar_accion", return_value=True):
            escritura = mcp_tools._ejecutar_herramienta_mcp(
                "work_state_update",
                {
                    "work_id": WORK_ID,
                    "cambios": {"siguiente_paso": "vía /tool"},
                    "autorizacion": False,
                    "directorio": str(self.raiz),
                },
                confirmar=True,
            )
        self.assertTrue(escritura["ok"], escritura.get("error"))

        with mock.patch.object(sc, "_confirmar_accion", return_value=True):
            lectura = mcp_tools._ejecutar_herramienta_mcp(
                "work_state",
                {"work_id": WORK_ID, "directorio": str(self.raiz)},
                confirmar=False,
            )
        self.assertEqual(lectura["resultado"]["siguiente_paso"], "vía /tool")

    def test_la_superficie_cli_respeta_la_misma_politica(self):
        import mcp_tools

        antes = self._texto()
        with mock.patch.object(sc, "_confirmar_accion", return_value=True):
            r = mcp_tools._ejecutar_herramienta_mcp(
                "work_state_update",
                {
                    "work_id": WORK_ID,
                    "cambios": {"criterios": ["hack"]},
                    "autorizacion": True,
                    "directorio": str(self.raiz),
                },
                confirmar=True,
            )
        self.assertFalse(r["ok"])
        self.assertEqual(r["resultado"]["categoria"], "autoridad")
        self.assertEqual(self._texto(), antes)


# ===========================================================================
# §11 / §14 — Fronteras
# ===========================================================================
class TestFronteras(_BaseSlice):
    def test_work_state_sigue_siendo_read_only(self):
        import mcp_tools

        catalogo = mcp_tools._cargar_herramientas_mcp()
        self.assertFalse(catalogo["work_state"]["requiere_permiso"])
        self.assertNotIn("cambios", catalogo["work_state"]["parametros"])
        self.assertTrue(catalogo["work_state_update"]["requiere_permiso"])

    def test_la_lectura_no_persiste_nada(self):
        antes = self._texto()
        mtime = self._state.stat().st_mtime_ns
        self._agente_consulta()
        self.assertEqual(self._texto(), antes)
        self.assertEqual(self._state.stat().st_mtime_ns, mtime)

    def test_no_se_tocan_decisions_ni_assertions(self):
        base = self.raiz / ".work" / WORK_ID
        antes = {
            n: (base / n).read_text(encoding="utf-8")
            for n in ("decisions.md", "assertions.md")
        }
        self._agente_actualiza({"siguiente_paso": "x"})
        self._agente_actualiza({"ciclo_de_vida": "cerrado"}, True)
        for nombre, contenido in antes.items():
            with self.subTest(documento=nombre):
                self.assertEqual((base / nombre).read_text(encoding="utf-8"), contenido)

    def test_la_politica_no_cambio_en_b14i(self):
        """B14-I no introduce semánticas ni políticas nuevas."""
        import snapcontext as modulo

        self.assertEqual(len(modulo._CAMPOS_ESCRITURA_DIRECTA), 12)
        self.assertEqual(
            set(modulo._CAMPOS_ESCRITURA_CON_AUTORIZACION),
            {"ciclo_de_vida", "cierre_estado"},
        )
        self.assertEqual(len(modulo._CAMPOS_ESCRITURA_PROTEGIDOS), 7)
        # Sin estado derivado de cierre ni lifecycle.
        from work_context import WorkState

        nombres = {campo.name for campo in dataclasses.fields(WorkState)}
        self.assertNotIn("cerrado", nombres)
        self.assertNotIn("obsoleto", nombres)


if __name__ == "__main__":
    unittest.main()
