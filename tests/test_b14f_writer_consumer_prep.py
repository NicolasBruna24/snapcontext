"""Tests B14-F — preparación del consumidor del Writer (`actualizar_estado`).

B14-E demostró que la primitive de escritura existe y es segura. B14-F la
somete a los **casos reales del canal WORK** (los que hasta ahora manipulaban
`state.md` con `read_text().replace()`), y comprueba dos cosas:

1. Round-trip completo sobre documentos que ya existen en el canal.
2. Que la primitive es usable por código de aplicación **sin** conceder ningún
   permiso MCP: la separación entre "API Python" y "capacidad de agente" se
   mantiene (B14-F §8).

No se introduce ninguna herramienta MCP de escritura (B14-F §6).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_tools
from exceptions import ContratoEstadoInvalidoError
from work_context import (
    actualizar_estado,
    crear_trabajo,
    escribir_documento_trabajo,
    leer_estado,
)

WORK_ID = "b14f-roundtrip"

VERDICT = (
    "pasa — comando `python3 -m pytest tests/ -q`; scope: proyecto completo; "
    "anchor Git: 45e2e0a71d06; autor: Agente B; instante: 2026-09-27T01:00:00Z"
)


def _git(raiz: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(raiz), *args], capture_output=True, text=True, check=True
    )
    return res.stdout


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name) / "proj"
        self.raiz.mkdir(parents=True)
        crear_trabajo(
            WORK_ID,
            self.raiz,
            titulo="Division segura",
            objetivo="Soportar division segura y documentar divisor cero.",
            criterios=["safe_divide existe", "b == 0 lanza ValueError"],
            restricciones=["sin dependencias nuevas"],
        )

    @property
    def _state(self) -> Path:
        return self.raiz / ".work" / WORK_ID / "state.md"

    def _texto(self) -> str:
        return self._state.read_text(encoding="utf-8")


# ===========================================================================
# Round-trip sobre un flujo real del canal
# ===========================================================================
class TestRoundTripReal(_Base):
    def test_flujo_completo_registra_y_cierra_un_trabajo(self):
        """fixture → leer → actualizar → leer (B14-F §5)."""
        # --- fixture inicial ------------------------------------------------
        inicial = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(inicial.ciclo_de_vida, "declarado")
        self.assertEqual(inicial.siguiente_paso, "(pendiente de declarar)")
        self.assertEqual(inicial.cierre_estado, "(abierto)")

        # --- Agente A: declara el siguiente paso ----------------------------
        tras_a = actualizar_estado(
            WORK_ID,
            {
                "siguiente_paso": "Agente A implementa safe_divide; documentación pendiente",
                "trabajo_pendiente": ("documentación del divisor cero",),
            },
            self.raiz,
        )
        self.assertEqual(
            tras_a.siguiente_paso, "Agente A implementa safe_divide; documentación pendiente"
        )
        self.assertEqual(tras_a.trabajo_pendiente, ("documentación del divisor cero",))
        self.assertEqual(tras_a.ciclo_de_vida, inicial.ciclo_de_vida)

        # --- Agente A: registra resultado y veredicto -----------------------
        tras_veredicto = actualizar_estado(
            WORK_ID,
            {
                "trabajo_completado": "safe_divide con guardia b == 0; 3 tests nuevos",
                "ultimo_veredicto": VERDICT,
            },
            self.raiz,
        )
        self.assertEqual(tras_veredicto.ultimo_veredicto, VERDICT)

        # --- Agente B: cierra el trabajo ------------------------------------
        final = actualizar_estado(
            WORK_ID,
            {
                "ciclo_de_vida": "cerrado",
                "trabajo_completado": "documentación README (Agente B); trabajo previo intacto",
                "cierre_estado": "cerrado",
                "cierre_motivo": "integrado — integrado en 45e2e0a71d0…",
                "git_base": "main",
                "git_rama": "feat/reader",
            },
            self.raiz,
        )
        self.assertEqual(final.ciclo_de_vida, "cerrado")
        self.assertEqual(final.cierre_estado, "cerrado")
        self.assertEqual(final.git_rama, "feat/reader")

        # (1) el campo modificado cambió
        self.assertNotEqual(final.ciclo_de_vida, inicial.ciclo_de_vida)
        # (2) los no modificados permanecieron
        for campo in ("objetivo", "titulo", "criterios", "restricciones", "proyecto", "declarado"):
            with self.subTest(campo=campo):
                self.assertEqual(getattr(inicial, campo), getattr(final, campo))
        # (3) el documento sigue siendo legible
        self.assertEqual(leer_estado(WORK_ID, self.raiz), final)
        # (4) la información documental no gestionada permanece
        texto = self._texto()
        self.assertIn("Solo la persona responsable ratifica cambios en esta sección", texto)
        self.assertIn("Las propuestas pendientes no se enumeran aquí", texto)
        self.assertIn("- (no declarado)", texto)
        self.assertIn("PR/issue: (ninguno)", texto)

    def test_el_writer_no_verifica_el_anchor_git(self):
        """El anchor se declara como texto: el writer no lo contrasta con Git.

        Se usa un repositorio real con un commit real para que quede demostrado
        que, aun pudiendo comprobarlo, `actualizar_estado()` no lo hace.
        """
        (self.raiz / "README.md").write_text("# demo\n", encoding="utf-8")
        _git(self.raiz, "init", "-q")
        _git(self.raiz, "add", "README.md")
        _git(
            self.raiz,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "-m",
            "inicial",
        )
        head = _git(self.raiz, "rev-parse", "HEAD").strip()
        # Un anchor de otro árbol: el writer lo acepta tal cual.
        estado = actualizar_estado(
            WORK_ID,
            {"ultimo_veredicto": VERDICT.replace("45e2e0a71d06", "0" * 40)},
            self.raiz,
        )
        self.assertIn("0" * 40, estado.ultimo_veredicto)
        # Y el hash real también se acepta, sin ninguna validación.
        estado2 = actualizar_estado(
            WORK_ID, {"ultimo_veredicto": VERDICT.replace("45e2e0a71d06", head)}, self.raiz
        )
        self.assertIn(head, estado2.ultimo_veredicto)

    def test_criterios_h3_hacen_round_trip_exacto(self):
        """La sección H3 sí es una lista real: ida y vuelta sin pérdida."""
        nuevos = ("criterio A", "criterio B", "criterio C")
        estado = actualizar_estado(WORK_ID, {"criterios": nuevos}, self.raiz)
        self.assertEqual(estado.criterios, nuevos)
        self.assertIn(
            "### Criterios de aceptación\n\n- criterio A\n- criterio B\n- criterio C", self._texto()
        )

    def test_ambos_formatos_documentales_conviven(self):
        """H3 (multi-línea) y clave en línea (una línea) tras una misma operación."""
        actualizar_estado(
            WORK_ID,
            {
                "criterios": ("uno", "dos"),
                "trabajo_completado": "tarea hecha; otra tarea",
            },
            self.raiz,
        )
        texto = self._texto()
        self.assertIn("### Criterios de aceptación\n\n- uno\n- dos\n", texto)
        self.assertIn("- Trabajo completado: tarea hecha; otra tarea\n", texto)
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.criterios, ("uno", "dos"))
        self.assertEqual(estado.trabajo_completado, ("tarea hecha; otra tarea",))


# ===========================================================================
# Frontera: la primitive Python no concede capacidad de agente (B14-F §8)
# ===========================================================================
class TestFronteraPrimitiveVsMcp(_Base):
    def test_actualizar_estado_es_usable_desde_codigo_de_aplicacion(self):
        """Un consumidor Python escribe sin pasar por el dispatcher MCP.

        No se pide confirmación, no se toca `HERRAMIENTAS_PREDEFINIDAS` y no
        se otorga ningún permiso: la primitive es una capacidad de library.
        """
        estado = actualizar_estado(WORK_ID, {"siguiente_paso": "cierre preparado"}, self.raiz)
        self.assertEqual(estado.siguiente_paso, "cierre preparado")
        # B14-H: la primitive por sí sola no otorga capacidad de agente. La
        # escritura existe, pero vive detrás de una frontera de autoridad
        # (`work_state_update`), no de la llamada directa a la primitive.
        catalogo = mcp_tools._cargar_herramientas_mcp()
        self.assertIn("work_state_update", catalogo)
        self.assertTrue(catalogo["work_state_update"]["requiere_permiso"])

    def test_mcp_sigue_siendo_exclusivamente_de_lectura(self):
        catalogo = mcp_tools._cargar_herramientas_mcp()
        # B14-H: la herramienta de LECTURA no cambia. La de escritura es una
        # entrada separada, con permiso y política propias.
        self.assertIn("work_state", catalogo)
        self.assertFalse(catalogo["work_state"]["requiere_permiso"])
        self.assertNotIn("cambios", catalogo["work_state"]["parametros"])
        for nombre, cfg in catalogo.items():
            with self.subTest(herramienta=nombre):
                if nombre.startswith("work") and nombre != "work_state_update":
                    self.assertFalse(cfg["requiere_permiso"])

    def test_la_herramienta_de_lectura_refleja_lo_escrito_por_la_primitive(self):
        actualizar_estado(WORK_ID, {"siguiente_paso": "verificado"}, self.raiz)
        llamada = mcp_tools._ejecutar_herramienta_mcp(
            "work_state", {"work_id": WORK_ID, "directorio": str(self.raiz)}, confirmar=False
        )
        self.assertTrue(llamada["ok"], llamada.get("error"))
        self.assertEqual(llamada["resultado"]["siguiente_paso"], "verificado")

    def test_la_escritura_no_toca_decisions_ni_assertions(self):
        base = self.raiz / ".work" / WORK_ID
        antes = {
            nombre: (base / nombre).read_text(encoding="utf-8")
            for nombre in ("decisions.md", "assertions.md")
        }
        actualizar_estado(
            WORK_ID,
            {
                "ciclo_de_vida": "cerrado",
                "cierre_estado": "cerrado",
                "ultimo_veredicto": VERDICT,
            },
            self.raiz,
        )
        for nombre, contenido in antes.items():
            with self.subTest(documento=nombre):
                self.assertEqual((base / nombre).read_text(encoding="utf-8"), contenido)

    def test_los_casos_negativos_siguen_pudiendo_construir_documentos_invalidos(self):
        """B14-F: la migration no amputa la capacidad de fabricar fixtures."""
        escribir_documento_trabajo(
            f".work/{WORK_ID}/state.md",
            self._texto().replace("Contract: b9-1", "Contract: 1.0"),
            self.raiz,
        )
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)
        with self.assertRaises(ContratoEstadoInvalidoError):
            actualizar_estado(WORK_ID, {"siguiente_paso": "x"}, self.raiz)
