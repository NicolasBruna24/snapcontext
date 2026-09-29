"""Tests B12 — reproducción automatizada del vertical slice cross-agent.

Cada clase cubre un criterio de éxito de B12 §15 sobre un proyecto Git
temporal autocontenido (nunca el repositorio real):

* C1/C3/C8 — el WORK sobrevive al "cierre" del agente A y un lector en frío
  reconstruye el estado relevante leyendo solo los tres documentos canónicos.
* C2 — la lectura en frío no necesita SnapContext: se hace con ``open()`` puro.
* C4 — el agente B continúa desde lo registrado sin rehacer trabajo de A.
* C5 — las observaciones/propuestas de A no aparecen en ``decisions.md``.
* C6 — el veredicto se asocia a un Git anchor y queda marcado obsoleto
  tras un cambio posterior.
* B8 R1 + B11-D — ``.work/`` sigue visible en status y fuera del staging.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from work_context import (
    DOCUMENTOS_CANONICOS,
    actualizar_estado,
    crear_trabajo,
    escribir_documento_trabajo,
)

WORK_ID = "b12-cross-agent"


def _git(raiz: Path, *args: str) -> str:
    """Ejecuta un comando Git no destructivo dentro de ``raiz``."""
    res = subprocess.run(
        ["git", "-C", str(raiz), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


def _crear_proyecto(tmp: str) -> Path:
    """Proyecto minúsculo con commit inicial (el 'PROJECT' del experimento)."""
    raiz = Path(tmp) / "demo-project"
    (raiz / "src").mkdir(parents=True)
    (raiz / "tests").mkdir()
    (raiz / "README.md").write_text("# demo\n", encoding="utf-8")
    (raiz / "src" / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (raiz / "tests" / "test_calculator.py").write_text(
        "def test_add():\n    assert True\n", encoding="utf-8"
    )
    _git(raiz, "init", "-q")
    _git(raiz, "add", "README.md", "src", "tests")
    subprocess.run(
        [
            "git",
            "-C",
            str(raiz),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    return raiz


def _leer_frio(raiz: Path) -> dict[str, str]:
    """Lee los documentos canónicos con ``open()`` puro (sin SnapContext)."""
    base = raiz / ".work" / WORK_ID
    return {nombre: open(base / nombre, encoding="utf-8").read() for nombre in DOCUMENTOS_CANONICOS}


class TestSliceVertical(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = _crear_proyecto(self._tmp.name)
        self.head = _git(self.raiz, "rev-parse", "HEAD").strip()
        crear_trabajo(
            WORK_ID,
            self.raiz,
            titulo="Division segura",
            objetivo="Soportar division segura y documentar divisor cero.",
            criterios=[
                "safe_divide existe",
                "b == 0 lanza ValueError",
                "tests pasan",
                "README documenta divisor cero",
            ],
            restricciones=["sin dependencias nuevas"],
        )
        # Estado inicial operativo (siguiente paso declarado).
        # B14-F: evolución legítima del trabajo → API declarativa del canal.
        actualizar_estado(
            WORK_ID,
            {"siguiente_paso": ("Agente A implementa safe_divide; documentación pendiente")},
            self.raiz,
        )

    # -- C1/C2/C3/C8: cold read solo con WORK --------------------------------
    def test_cold_read_respuestas_desde_los_documentos(self):
        # Agente A registra su resultado y desaparece.
        docs = _leer_frio(self.raiz)
        # B14-F: Agente A registra su resultado con la API declarativa.
        actualizar_estado(
            WORK_ID,
            {
                "trabajo_completado": "safe_divide con guardia b == 0; 3 tests nuevos",
                "ultimo_veredicto": (
                    "pasa — comando `python3 -m pytest tests/ -q`; scope: src+tests; "
                    f"anchor Git: {self.head} (dirty); autor: Agente A; "
                    "instante: 2026-09-27T00:00:00Z"
                ),
            },
            self.raiz,
        )
        aserciones = docs["assertions.md"].replace(
            "(ninguna todavía)",
            "### O-1 — safe_divide implementado\n"
            "- Tipo: observación\n- Autor: Agente A\n"
            "### P-1 — usar Decimal\n- Tipo: propuesta\n- Autor: Agente A",
        )
        escribir_documento_trabajo(f".work/{WORK_ID}/assertions.md", aserciones, self.raiz)

        # Agente B: lectura en frío con apertura directa de ficheros.
        frio = _leer_frio(self.raiz)
        st, dec, asc = (frio["state.md"], frio["decisions.md"], frio["assertions.md"])
        self.assertIn(f"Work: {WORK_ID}", st)  # id
        self.assertIn("Soportar division segura", st)  # objetivo
        self.assertEqual(st.count("- safe_divide existe"), 1)  # criterios
        self.assertIn("### Alcance incluido", st)  # scope
        self.assertIn("### Restricciones", st)  # restricciones
        self.assertIn("safe_divide con guardia b == 0", st)  # completado
        self.assertIn("documentación pendiente", st)  # pendiente
        self.assertIn("Bloqueos", st)  # bloqueos
        self.assertIn("pasa", st)  # veredicto
        self.assertIn(self.head, st)  # anchor
        self.assertIn("Agente A implementa safe_divide", st)  # siguiente paso
        # C5: la propuesta de A NO es decisión ratificada.
        self.assertIn("(ninguna todavía)", dec)
        self.assertNotIn("Decimal", dec)
        self.assertIn("### P-1", asc)
        # C7: el canal no menciona proceso/provider/modelo/sesión.
        for marca in ("pid", "provider", "modelo", "sesión", "~/.snapcontext"):
            self.assertNotIn(marca, st.lower() + asc.lower())

    # -- C4: continuidad de B sin rehacer trabajo de A -----------------------
    def test_b_continua_y_cierra_sin_rehacer_a(self):
        docs = _leer_frio(self.raiz)
        # B solo documenta; el trabajo previo (no reabierto) queda intacto.
        (self.raiz / "README.md").write_text(
            "# demo\n\n## Division segura\nsafe_divide(a, 0) lanza ValueError.\n",
            encoding="utf-8",
        )
        _git(self.raiz, "add", ".", ":(exclude).work")
        subprocess.run(
            [
                "git",
                "-C",
                str(self.raiz),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "-m",
                "b12: docs",
            ],
            check=True,
        )
        c2 = _git(self.raiz, "rev-parse", "HEAD").strip()
        ahora = "2026-09-27T01:00:00Z"
        # B14-F: Agente B registra el cierre con la API declarativa.
        actualizar_estado(
            WORK_ID,
            {
                "ciclo_de_vida": "cerrado",
                "trabajo_completado": "documentación README (Agente B); trabajo previo intacto",
                "ultimo_veredicto": (
                    "pasa — comando `python3 -m pytest tests/ -q` (6 passed); scope: proyecto "
                    f"completo; anchor Git: {c2} (clean); autor: Agente B; instante: {ahora}"
                ),
                "cierre_estado": "cerrado",
                "cierre_motivo": f"integrado — integrado en {c2[:12]}…",
            },
            self.raiz,
        )

        frio = _leer_frio(self.raiz)
        self.assertIn("documentación README (Agente B)", frio["state.md"])
        self.assertIn(f"anchor Git: {c2} (clean)", frio["state.md"])
        self.assertIn("- Estado: cerrado", frio["state.md"])
        self.assertIn("integrado", frio["state.md"])

    # -- C6: veredicto anclado y obsolescencia -------------------------------
    def test_veredicto_anclado_queda_obsoleto_tras_cambio(self):
        docs = _leer_frio(self.raiz)
        ts1 = "2026-09-27T02:00:00Z"
        ts2 = "2026-09-27T03:00:00Z"
        veredicto_texto = (
            "pasa — comando `python3 -m pytest tests/ -q`; scope: proyecto completo; "
            f"anchor Git: {self.head} (clean); autor: Agente B; instante: {ts1}"
        )
        # B14-F: registrar el veredicto es una actualización declarativa legítima.
        actualizar_estado(WORK_ID, {"ultimo_veredicto": veredicto_texto}, self.raiz)
        veredicto = f"- Último veredicto de verificación: {veredicto_texto}"
        # Cambio Git posterior (sin commit): el anchor ya no describe el árbol.
        (self.raiz / "README.md").write_text("# demo\npost-veredicto\n", encoding="utf-8")
        status = _git(self.raiz, "status", "--short")
        self.assertIn("README.md", status)
        obsoleto = (
            f"{veredicto}\n- Veredictos obsoletos: (pasa @ {self.head} clean, "
            f"Agente B, {ts1}) — OBSOLETO desde {ts2}: cambio Git posterior "
            f"alteró el árbol respecto a su anchor"
        )
        # B14-F (NO migrado, deliberadamente): la transición a obsoleto **mueve**
        # la línea del veredicto vigente y además inserta la clave
        # `Veredictos obsoletos:`, que `crear_trabajo()` no genera. El Writer
        # solo edita claves existentes y no crea claves nuevas: hacerlo aquí
        # exigiría ampliar su contrato (nueva clave + semántica de obsolescencia,
        # que pertenece al Verification Engine, no a este bloque). Se mantiene
        # la escritura directa para representar ese documento concreto.
        estado2 = _leer_frio(self.raiz)["state.md"].replace(veredicto, obsoleto, 1)
        escribir_documento_trabajo(f".work/{WORK_ID}/state.md", estado2, self.raiz)
        final = _leer_frio(self.raiz)["state.md"]
        self.assertIn(f"OBSOLETO desde {ts2}", final)
        self.assertIn(f"anchor Git: {self.head}", final)

    # -- B8 R1 + B11-D: .work visible y fuera del staging -------------------
    def test_work_visible_y_fuera_del_staging_automatico(self):
        status = _git(self.raiz, "status", "--short")
        self.assertIn(".work/", status)  # visible, no ignorado
        _git(self.raiz, "add", ".", ":(exclude).work")
        staged = _git(self.raiz, "diff", "--cached", "--name-only")
        self.assertNotIn(".work", staged)
        self.assertTrue((self.raiz / ".work" / WORK_ID).is_dir())


if __name__ == "__main__":
    unittest.main()
