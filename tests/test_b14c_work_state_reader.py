"""Tests B14-C — Work State Reader: `leer_estado()`.

Cada clase cubre un caso de la matriz de B14-B (A–N). El Reader es de solo
lectura y no invoca Git: todo corre sobre directorios temporales y nunca toca
el repositorio real.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exceptions import ContratoEstadoInvalidoError, RutaInseguraError
from work_context import (
    KIND_ESTADO,
    VERSION_CONTRATO,
    crear_trabajo,
    escribir_documento_trabajo,
    leer_estado,
)

WORK_ID = "b14c-reader"


def _raiz(tmp: Path, nombre: str = "proj") -> Path:
    raiz = tmp / nombre
    raiz.mkdir(parents=True)
    return raiz


def _crear(raiz: Path, work_id: str = WORK_ID) -> Path:
    crear_trabajo(
        work_id,
        raiz,
        titulo="Division segura",
        objetivo="Soportar division segura y documentar divisor cero.",
        criterios=["safe_divide existe", "b == 0 lanza ValueError"],
        restricciones=["sin dependencias nuevas"],
    )
    return raiz / ".work" / work_id / "state.md"


class _BaseReader(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = _raiz(Path(self._tmp.name))

    def _estado(self) -> str:
        return (self.raiz / ".work" / WORK_ID / "state.md").read_text(encoding="utf-8")

    def _reescribir(self, texto: str) -> None:
        escribir_documento_trabajo(f".work/{WORK_ID}/state.md", texto, self.raiz)

    def _sustituir(self, *pares: tuple[str, str]) -> str:
        texto = self._estado()
        for viejo, nuevo in pares:
            self.assertIn(viejo, texto)
            texto = texto.replace(viejo, nuevo)
        return texto


# ===========================================================================
# A / D — Documento canónico y placeholders
# ===========================================================================
class TestDocumentoCanonico(_BaseReader):
    def test_a_lee_documento_generado_por_crear_trabajo(self):
        _crear(self.raiz)
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.work_id, WORK_ID)
        self.assertEqual(estado.kind, KIND_ESTADO)
        self.assertEqual(estado.contrato, VERSION_CONTRATO)
        self.assertEqual(estado.contrato, "b9-1")
        self.assertEqual(estado.proyecto, "proj")
        self.assertEqual(estado.titulo, "Division segura")
        self.assertEqual(estado.objetivo, "Soportar division segura y documentar divisor cero.")
        self.assertEqual(estado.criterios, ("safe_divide existe", "b == 0 lanza ValueError"))
        self.assertEqual(estado.restricciones, ("sin dependencias nuevas",))
        self.assertEqual(estado.ciclo_de_vida, "declarado")
        self.assertEqual(estado.cierre_estado, "(abierto)")

    def test_a_modelo_es_datos_puros_sin_cerrazo_derivado(self):
        """El Reader no calcula `cerrado`: el cierre es texto documental."""
        _crear(self.raiz)
        estado = leer_estado(WORK_ID, self.raiz)
        nombres = {campo.name for campo in dataclasses.fields(estado)}
        self.assertNotIn("cerrado", nombres)
        self.assertNotIn("esta_cerrado", nombres)
        for campo in dataclasses.fields(estado):
            with self.subTest(campo=campo.name):
                self.assertIsInstance(
                    getattr(estado, campo.name), (str, tuple, dict, type(None))
                )

    def test_d_placeholders_se_preservan_literalmente(self):
        """B14-B §6: los placeholders NO se convierten en ausencia."""
        crear_trabajo(WORK_ID, self.raiz)  # sin objetivo ni criterios
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.objetivo, "(pendiente de declarar)")
        self.assertEqual(estado.criterios, ("(pendiente de declarar)",))
        self.assertEqual(estado.alcance_incluido, ("(no declarado)",))
        self.assertEqual(estado.alcance_excluido, ("(no declarado)",))
        self.assertEqual(estado.restricciones, ("(ninguna declarada)",))
        self.assertEqual(estado.dependencias_entorno, ("(ninguna declarada)",))
        self.assertEqual(estado.trabajo_completado, ("(nada registrado)",))
        self.assertEqual(estado.bloqueos, ("(ninguno)",))
        self.assertEqual(estado.siguiente_paso, "(pendiente de declarar)")
        self.assertEqual(estado.comando_verificacion, "(pendiente de declarar)")


# ===========================================================================
# B / F / G — Documento evolucionado, verdict libre, Git declarativo
# ===========================================================================
VERDICT_B12 = (
    "pasa — comando `python3 -m pytest tests/ -q` (6 passed); scope: proyecto "
    "completo; anchor Git: 45e2e0a71d06; autor: Agente B; instante: 2026-09-27T01:00:00Z"
)

PLACEHOLDER_VERDICT = "- Último veredicto de verificación: (no hay verificación registrada)"


class TestDocumentoEvolucionado(_BaseReader):
    def test_b_lee_estado_actualizado_como_b12(self):
        _crear(self.raiz)
        self._reescribir(
            self._sustituir(
                ("- Ciclo de vida: declarado", "- Ciclo de vida: cerrado"),
                (
                    "- Trabajo completado: - (nada registrado)",
                    "- Trabajo completado: documentación README (Agente B)",
                ),
                ("- Estado: (abierto)", "- Estado: cerrado"),
                ("- Motivo: (n/a)", "- Motivo: integrado — integrado en 45e2e0a71d06…"),
            )
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.ciclo_de_vida, "cerrado")
        self.assertEqual(estado.trabajo_completado, ("documentación README (Agente B)",))
        self.assertEqual(estado.cierre_estado, "cerrado")
        self.assertEqual(estado.cierre_motivo, "integrado — integrado en 45e2e0a71d06…")

    def test_f_verdict_se_conserva_como_string_opaco(self):
        """B14-A-R: el Reader NO parsea el verdict (ni hace split(';'))."""
        _crear(self.raiz)
        self._reescribir(
            self._sustituir((PLACEHOLDER_VERDICT, f"- Último veredicto de verificación: {VERDICT_B12}"))
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertIsInstance(estado.ultimo_veredicto, str)
        self.assertEqual(estado.ultimo_veredicto, VERDICT_B12)
        self.assertIn("; scope:", estado.ultimo_veredicto)

    def test_f_verdict_libre_sin_estructura_de_puntos_y_coma(self):
        _crear(self.raiz)
        self._reescribir(
            self._sustituir(
                (PLACEHOLDER_VERDICT, "- Último veredicto de verificación: no se pudo ejecutar nada")
            )
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.ultimo_veredicto, "no se pudo ejecutar nada")

    def test_g_referencias_git_se_conservan_como_texto(self):
        _crear(self.raiz)
        self._reescribir(
            self._sustituir(
                ("- Base: (pendiente)", "- Base: main"),
                ("- Actual: (pendiente)", "- Actual: feat/reader"),
                ("- Rama: (pendiente)", "- Rama: feat/reader"),
                ("- PR/issue: (ninguno)", "- PR/issue: #42"),
            )
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.git_base, "main")
        self.assertEqual(estado.git_actual, "feat/reader")
        self.assertEqual(estado.git_rama, "feat/reader")
        self.assertEqual(estado.git_pr_issue, "#42")

    def test_veredictos_obsoletos_se_leen_como_texto(self):
        _crear(self.raiz)
        self._reescribir(
            self._sustituir(
                (
                    PLACEHOLDER_VERDICT,
                    PLACEHOLDER_VERDICT
                    + "\n- Veredictos obsoletos: (pasa @ 45e2e0a71d06 clean, Agente B, "
                    "2026-09-27T01:00:00Z) — OBSOLETO desde 2026-09-27T02:00:00Z",
                )
            )
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(len(estado.veredictos_obsoletos), 1)
        self.assertIn("OBSOLETO desde 2026-09-27T02:00:00Z", estado.veredictos_obsoletos[0])


# ===========================================================================
# C / E / L — Tolerancia: campos ausentes, markdown adicional, cuerpo parcial
# ===========================================================================
class TestTolerancia(_BaseReader):
    def test_c_campos_opcionales_ausentes_no_rompen(self):
        _crear(self.raiz)
        self._reescribir(
            "# Trabajo: minimo\n\n"
            f"Kind: {KIND_ESTADO}\n"
            f"Contract: {VERSION_CONTRATO}\n"
            f"Work: {WORK_ID}\n"
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.work_id, WORK_ID)
        self.assertEqual(estado.objetivo, None)
        self.assertEqual(estado.titulo, None)
        self.assertEqual(estado.criterios, ())
        self.assertEqual(estado.veredictos_obsoletos, ())
        self.assertEqual(estado.ultimo_veredicto, None)
        self.assertEqual(estado.cierre_estado, None)

    def test_l_cabecera_valida_sin_cuerpo(self):
        _crear(self.raiz)
        self._reescribir(f"Kind: {KIND_ESTADO}\nContract: {VERSION_CONTRATO}\nWork: {WORK_ID}\n")
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.proyecto, None)
        self.assertEqual(estado.siguiente_paso, None)

    def test_e_markdown_adicional_no_rompe(self):
        _crear(self.raiz)
        self._reescribir(
            self._estado() + "\n## Notas del lector\n\nTexto libre sin contrato.\n\n- Rama: feat/x\n"
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.criterios, ("safe_divide existe", "b == 0 lanza ValueError"))

    def test_e_clave_desconocida_no_aborta(self):
        _crear(self.raiz)
        self._reescribir(
            self._sustituir(
                (
                    "- Siguiente paso: (pendiente de declarar)",
                    "- Siguiente paso: seguir\n- Revisor: Agente C",
                )
            )
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.siguiente_paso, "seguir")
        self.assertEqual(estado.campos_extra, {"revisor": "Agente C"})

    def test_whitespace_adicional_tolerado(self):
        _crear(self.raiz)
        self._reescribir(
            self._sustituir(
                ("- Siguiente paso: (pendiente de declarar)", "-  Siguiente paso:   seguir  ")
            )
        )
        self.assertEqual(leer_estado(WORK_ID, self.raiz).siguiente_paso, "seguir")


# ===========================================================================
# H / I / J / K / N — Rechazos: contrato, identidad, ruta, vacío, ambigüedad
# ===========================================================================
class TestRechazos(_BaseReader):
    def test_h_kind_invalido_rechazado(self):
        _crear(self.raiz)
        self._reescribir(self._sustituir((f"Kind: {KIND_ESTADO}", "Kind: work-decisions")))
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)

    def test_i_contract_invalido_rechazado(self):
        _crear(self.raiz)
        self._reescribir(self._sustituir((f"Contract: {VERSION_CONTRATO}", "Contract: 1.0")))
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)

    def test_j_work_que_no_coincide_rechazado(self):
        _crear(self.raiz)
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado("otro-trabajo", self.raiz)

    def test_j_cabecera_ausente_rechazada(self):
        _crear(self.raiz)
        self._reescribir("# Trabajo: sin cabecera\n\n## Identidad\n\n- Id: x\n")
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)

    def test_k_documento_vacio_rechazado(self):
        _crear(self.raiz)
        self._reescribir("")
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)

    def test_k_documento_inexistente_rechazado(self):
        crear_trabajo("otro", self.raiz)
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado("no-existe", self.raiz)

    def test_n_cabecera_duplicada_conflictiva_rechazada(self):
        _crear(self.raiz)
        self._reescribir(self._sustituir((f"Work: {WORK_ID}", "Work: otro-trabajo")))
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)

    def test_m_path_traversal_rechazado_por_frontera_existente(self):
        _crear(self.raiz)
        for malo in ("../fuera", "a/b", "/abs", "con espacios", "MAYUS", "..", "."):
            with self.subTest(work_id=malo):
                with self.assertRaises((RutaInseguraError, ValueError)):
                    leer_estado(malo, self.raiz)

    def test_m_symlink_rechazado(self):
        _crear(self.raiz)
        directorio = self.raiz / ".work" / WORK_ID
        ruta = directorio / "state.md"
        texto = ruta.read_text(encoding="utf-8")
        ruta.unlink()
        try:
            ruta.symlink_to(directorio / "otro.md")
        except OSError:
            self.skipTest("el sistema no permite crear symlinks")
        (directorio / "otro.md").write_text(texto, encoding="utf-8")
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)


# ===========================================================================
# Frontera — el Reader es de solo lectura y no cruza responsabilidades
# ===========================================================================
class TestFronteraSoloLectura(_BaseReader):
    def test_leer_estado_no_modifica_state_md(self):
        _crear(self.raiz)
        ruta = self.raiz / ".work" / WORK_ID / "state.md"
        antes = ruta.read_text(encoding="utf-8")
        mtime_antes = ruta.stat().st_mtime_ns
        leer_estado(WORK_ID, self.raiz)
        self.assertEqual(ruta.read_text(encoding="utf-8"), antes)
        self.assertEqual(ruta.stat().st_mtime_ns, mtime_antes)

    def test_leer_estado_no_crea_archivos(self):
        _crear(self.raiz)
        antes = sorted(p.name for p in (self.raiz / ".work" / WORK_ID).iterdir())
        leer_estado(WORK_ID, self.raiz)
        despues = sorted(p.name for p in (self.raiz / ".work" / WORK_ID).iterdir())
        self.assertEqual(antes, despues)

    def test_leer_estado_no_toca_los_otros_documentos(self):
        _crear(self.raiz)
        decisiones = self.raiz / ".work" / WORK_ID / "decisions.md"
        antes = decisiones.read_text(encoding="utf-8")
        leer_estado(WORK_ID, self.raiz)
        self.assertEqual(decisiones.read_text(encoding="utf-8"), antes)

    def test_fuentes_del_reader_no_importan_git_ni_decisiones(self):
        """Auditoría de frontera por fuente (B14-C §16).

        Se examina el **código** del Reader (docstring excluido a propósito:
        el docstring nombra `decisions.md` para declarar que no se lee).
        """
        import ast

        modulo = Path(__file__).resolve().parents[1] / "work_context.py"
        arbol = ast.parse(modulo.read_text(encoding="utf-8"))
        funcion = next(
            nodo
            for nodo in arbol.body
            if isinstance(nodo, ast.FunctionDef) and nodo.name == "leer_estado"
        )
        # Se descarta el docstring: no es código ejecutable.
        cuerpo = list(funcion.body)
        if (
            cuerpo
            and isinstance(cuerpo[0], ast.Expr)
            and isinstance(cuerpo[0].value, ast.Constant)
        ):
            cuerpo = cuerpo[1:]

        atributos = {
            nodo.attr
            for stmt in cuerpo
            for nodo in ast.walk(stmt)
            if isinstance(nodo, ast.Attribute)
        }
        imported = {
            alias.name
            for stmt in cuerpo
            for nodo in ast.walk(stmt)
            if isinstance(nodo, ast.Import)
            for alias in nodo.names
        } | {
            nodo.module or ""
            for stmt in cuerpo
            for nodo in ast.walk(stmt)
            if isinstance(nodo, ast.ImportFrom)
        }
        called = {
            nodo.func.id
            for stmt in cuerpo
            for nodo in ast.walk(stmt)
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
        }

        with self.subTest("no ejecuta procesos"):  # Git / shell
            self.assertNotIn("subprocess", imported)
            self.assertNotIn("os.system", atributos)
        with self.subTest("no toca otros documentos del canal"):
            self.assertNotIn("DOCUMENTO_DECISIONES", atributos)
            self.assertNotIn("DOCUMENTO_ASERCIONES", atributos)
        with self.subTest("no descompone el verdict"):
            self.assertNotIn("split", atributos)  # solo `splitlines` es admisible
        with self.subTest("no escribe ni evalúa"):
            self.assertNotIn("write_text", atributos)
            self.assertNotIn("open", atributos)
            self.assertNotIn("escribir_documento_trabajo", called)
            self.assertFalse(called & {"eval", "exec"})


if __name__ == "__main__":
    unittest.main()
