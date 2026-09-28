"""Tests B14-E — Writer declarativo de `state.md` (`actualizar_estado`).

El writer es el complemento del Reader (B14-C) y la contraescritura de la
integración B14-D. Sustituye el patrón `read_text().replace().write_text()`.

Todos los casos corren sobre directorios temporales y no tocan el repositorio
real ni ejecutan Git.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exceptions import ContratoEstadoInvalidoError, RutaInseguraError
from work_context import (
    actualizar_estado,
    crear_trabajo,
    escribir_documento_trabajo,
    leer_estado,
)

WORK_ID = "b14e-writer"

VERDICT = (
    "pasa — comando `python3 -m pytest tests/ -q`; scope: proyecto completo; "
    "anchor Git: 45e2e0a71d06; autor: Agente B; instante: 2026-09-27T01:00:00Z"
)


class _BaseWriter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name) / "proj"
        self.raiz.mkdir(parents=True)
        crear_trabajo(
            WORK_ID,
            self.raiz,
            titulo="Division segura",
            objetivo="Soportar division segura.",
            criterios=["safe_divide existe", "b == 0 lanza ValueError"],
            restricciones=["sin dependencias nuevas"],
        )

    @property
    def _ruta(self) -> Path:
        return self.raiz / ".work" / WORK_ID / "state.md"

    def _texto(self) -> str:
        return self._ruta.read_text(encoding="utf-8")

    def _actualizar(self, cambios) -> object:
        return actualizar_estado(WORK_ID, cambios, self.raiz)


# ===========================================================================
# A / B — Actualización simple y múltiple
# ===========================================================================
class TestActualizacionBasica(_BaseWriter):
    def test_a_actualiza_un_campo_y_el_archivo_cambia(self):
        antes = self._texto()
        estado = self._actualizar({"siguiente_paso": "B14-E verification"})
        self.assertNotEqual(self._texto(), antes)
        self.assertIn("- Siguiente paso: B14-E verification", self._texto())
        self.assertEqual(estado.siguiente_paso, "B14-E verification")

    def test_a_el_retorno_refleja_lo_persistido(self):
        estado = self._actualizar({"siguiente_paso": "seguir"})
        # El retorno viene de leer el documento, no de la intención de entrada.
        self.assertEqual(estado, leer_estado(WORK_ID, self.raiz))

    def test_b_actualiza_varios_campos_en_una_operacion(self):
        estado = self._actualizar(
            {
                "siguiente_paso": "B14-E verification",
                "ciclo_de_vida": "en curso",
                "trabajo_completado": ("safe_divide", "3 tests nuevos"),
                "comando_verificacion": "python3 -m pytest tests/ -q",
            }
        )
        self.assertEqual(estado.siguiente_paso, "B14-E verification")
        self.assertEqual(estado.ciclo_de_vida, "en curso")
        self.assertEqual(estado.trabajo_completado, ("safe_divide; 3 tests nuevos",))
        self.assertEqual(estado.comando_verificacion, "python3 -m pytest tests/ -q")

    def test_b_actualiza_un_campo_de_seccion_h3(self):
        estado = self._actualizar({"criterios": ("criterio nuevo", "otro criterio")})
        self.assertEqual(estado.criterios, ("criterio nuevo", "otro criterio"))
        self.assertIn("- criterio nuevo\n- otro criterio", self._texto())

    def test_b_acepta_texto_plano_para_un_campo_de_lista(self):
        estado = self._actualizar({"trabajo_completado": "un único elemento"})
        self.assertEqual(estado.trabajo_completado, ("un único elemento",))


# ===========================================================================
# C / K — Preservación documental y round trip
# ===========================================================================
class TestPreservacion(_BaseWriter):
    def test_c_conserva_campos_no_afectados(self):
        antes = leer_estado(WORK_ID, self.raiz)
        self._actualizar({"siguiente_paso": "otro paso"})
        despues = leer_estado(WORK_ID, self.raiz)
        for campo in ("objetivo", "titulo", "criterios", "restricciones", "cierre_estado"):
            with self.subTest(campo=campo):
                self.assertEqual(getattr(antes, campo), getattr(despues, campo))

    def test_c_conserva_placeholders_no_modificados(self):
        self._actualizar({"siguiente_paso": "seguir"})
        texto = self._texto()
        for marcador in (
            "- (no declarado)",
            "(ninguna declarada)",
            "(no hay verificación registrada)",
            "- Base: (pendiente)",
            "- Estado: (abierto)",
        ):
            with self.subTest(marcador=marcador):
                self.assertIn(marcador, texto)

    def test_c_conserva_prosa_y_markdown_adicionales(self):
        despues = self._texto()
        self.assertIn(
            "Solo la persona responsable ratifica cambios en esta sección", despues
        )
        self.assertIn("Las propuestas pendientes no se enumeran aquí", despues)
        self.assertIn("## Pendiente de ratificación", despues)

    def test_c_conserva_claves_desconocidas_del_documento(self):
        escribir_documento_trabajo(
            f".work/{WORK_ID}/state.md",
            self._texto() + "\n- Revisor: Agente C\n",
            self.raiz,
        )
        self._actualizar({"siguiente_paso": "seguir"})
        self.assertIn("- Revisor: Agente C", self._texto())

    def test_k_round_trip_conserva_lo_no_afectado(self):
        original = leer_estado(WORK_ID, self.raiz)
        self._actualizar({"siguiente_paso": "B14-E", "ciclo_de_vida": "en curso"})
        intermedio = leer_estado(WORK_ID, self.raiz)
        # Repetir la misma operación es idempotente: el documento ya la refleja.
        self._actualizar({"siguiente_paso": "B14-E", "ciclo_de_vida": "en curso"})
        final = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(intermedio, final)
        self.assertEqual(original.objetivo, final.objetivo)
        self.assertEqual(original.criterios, final.criterios)
        self.assertEqual(original.git_rama, final.git_rama)


# ===========================================================================
# D / E / F — Campos especiales
# ===========================================================================
class TestCamposEspeciales(_BaseWriter):
    def test_d_verdict_se_conserva_literalmente(self):
        estado = self._actualizar({"ultimo_veredicto": VERDICT})
        self.assertEqual(estado.ultimo_veredicto, VERDICT)
        # No se ha descompuesto: sigue siendo una única cadena en el documento.
        self.assertIn(f"- Último veredicto de verificación: {VERDICT}", self._texto())

    def test_d_verdict_libre_se_conserva(self):
        estado = self._actualizar({"ultimo_veredicto": "no se pudo ejecutar nada"})
        self.assertEqual(estado.ultimo_veredicto, "no se pudo ejecutar nada")

    def test_e_git_se_actualiza_como_texto(self):
        estado = self._actualizar(
            {
                "git_base": "main",
                "git_actual": "feat/x",
                "git_rama": "feat/x",
                "git_pr_issue": "#42",
            }
        )
        self.assertEqual(estado.git_base, "main")
        self.assertEqual(estado.git_actual, "feat/x")
        self.assertEqual(estado.git_rama, "feat/x")
        self.assertEqual(estado.git_pr_issue, "#42")
        # No se valida el ref ni se calcula clean/dirty.
        estado2 = self._actualizar({"git_base": "no-es-un-ref-valido"})
        self.assertEqual(estado2.git_base, "no-es-un-ref-valido")

    def test_f_cierre_se_actualiza_sin_derivar_cerrado(self):
        import dataclasses

        estado = self._actualizar({"cierre_estado": "cerrado", "cierre_motivo": "integrado"})
        self.assertEqual(estado.cierre_estado, "cerrado")
        self.assertEqual(estado.cierre_motivo, "integrado")
        nombres = {campo.name for campo in dataclasses.fields(estado)}
        self.assertNotIn("cerrado", nombres)


# ===========================================================================
# G / H / I — Validación y rechazos
# ===========================================================================
class TestValidacion(_BaseWriter):
    def test_g_campo_desconocido_error_explicito(self):
        with self.assertRaises(ValueError) as ctx:
            self._actualizar({"campo_inventado": "x"})
        self.assertIn("campo_inventado", str(ctx.exception))
        self.assertNotIn("campo_inventado", self._texto())

    def test_g_no_acepta_claves_markdown_en_bruto(self):
        with self.assertRaises(ValueError):
            self._actualizar({"Siguiente paso": "x"})

    def test_g_cambios_vacios_rechazados(self):
        with self.assertRaises(ValueError):
            self._actualizar({})

    def test_g_tipo_incorrecto_rechazado(self):
        with self.assertRaises(TypeError):
            self._actualizar({"siguiente_paso": ["no", "es", "texto"]})
        with self.assertRaises(TypeError):
            self._actualizar({"criterios": 42})

    def test_g_cambios_debe_ser_mapping(self):
        with self.assertRaises(TypeError):
            actualizar_estado(WORK_ID, [("siguiente_paso", "x")], self.raiz)

    def test_h_trabajo_inexistente_falla_sin_crear_archivos(self):
        with self.assertRaises(ContratoEstadoInvalidoError):
            actualizar_estado("no-existe", {"siguiente_paso": "x"}, self.raiz)
        self.assertFalse((self.raiz / ".work" / "no-existe").exists())

    def test_h_no_repara_documento_invalido(self):
        escribir_documento_trabajo(
            f".work/{WORK_ID}/state.md",
            self._texto().replace("Kind: work-state", "Kind: work-decisions"),
            self.raiz,
        )
        with self.assertRaises(ContratoEstadoInvalidoError):
            self._actualizar({"siguiente_paso": "x"})

    def test_i_ruta_insegura_rechazada(self):
        for malo in ("../fuera", "/abs", "con espacios", "MAYUS", ".."):
            with self.subTest(work_id=malo):
                with self.assertRaises((RutaInseguraError, ValueError)):
                    actualizar_estado(malo, {"siguiente_paso": "x"}, self.raiz)

    def test_i_raiz_inexistente_rechazada(self):
        # `utils.resolver_raiz` falla antes que el writer: es la misma
        # semántica que aplica al Reader (B14-C), no una política nueva.
        with self.assertRaises((RutaInseguraError, RuntimeError)):
            actualizar_estado(WORK_ID, {"siguiente_paso": "x"}, self.raiz / "no-existe")
        with self.assertRaises((RutaInseguraError, RuntimeError)):
            leer_estado(WORK_ID, self.raiz / "no-existe")


# ===========================================================================
# J — Escritura atómica
# ===========================================================================
class TestEscrituraAtomica(_BaseWriter):
    def test_j_fallo_antes_del_replace_conserva_el_documento(self):
        import work_context as wc

        antes = self._texto()
        real = wc._reemplazar

        def _romper(temporal, destino):
            raise OSError("fallo simulado antes del reemplazo")

        wc._reemplazar = _romper  # type: ignore[method-assign]
        try:
            with self.assertRaises(OSError):
                self._actualizar({"siguiente_paso": "no debe persistirse"})
        finally:
            wc._reemplazar = real  # type: ignore[method-assign]
        self.assertEqual(self._texto(), antes)
        # El documento sigue íntegro y legible: no queda truncado.
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.siguiente_paso, "(pendiente de declarar)")
        self.assertEqual(estado.objetivo, "Soportar division segura.")

    def test_j_no_deja_temporales(self):
        self._actualizar({"siguiente_paso": "x"})
        directorio = self.raiz / ".work" / WORK_ID
        self.assertEqual(
            [p.name for p in directorio.iterdir() if p.name.startswith(".tmp-work-")], []
        )


# ===========================================================================
# L — Frontera negativa
# ===========================================================================
class TestFronteraNegativa(_BaseWriter):
    def test_l_no_modifica_los_otros_documentos(self):
        base = self.raiz / ".work" / WORK_ID
        antes = {
            nombre: (base / nombre).read_text(encoding="utf-8")
            for nombre in ("decisions.md", "assertions.md", "README.md")
        }
        self._actualizar({"siguiente_paso": "x", "cierre_estado": "cerrado"})
        for nombre, contenido in antes.items():
            with self.subTest(documento=nombre):
                self.assertEqual((base / nombre).read_text(encoding="utf-8"), contenido)

    def test_l_no_crea_archivos_nuevos(self):
        base = self.raiz / ".work" / WORK_ID
        antes = sorted(p.name for p in base.iterdir())
        self._actualizar({"siguiente_paso": "x"})
        self.assertEqual(sorted(p.name for p in base.iterdir()), antes)

    def test_l_no_ejecuta_git(self):
        import ast

        modulo = Path(__file__).resolve().parents[1] / "work_context.py"
        arbol = ast.parse(modulo.read_text(encoding="utf-8"))
        nodo = next(
            n
            for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name == "actualizar_estado"
        )
        cuerpo = list(nodo.body)
        if cuerpo and isinstance(cuerpo[0], ast.Expr):
            cuerpo = cuerpo[1:]  # el docstring documenta, no ejecuta
        importados = {
            alias.name
            for stmt in cuerpo
            for hijo in ast.walk(stmt)
            if isinstance(hijo, ast.Import)
            for alias in hijo.names
        } | {
            (hijo.module or "")
            for stmt in cuerpo
            for hijo in ast.walk(stmt)
            if isinstance(hijo, ast.ImportFrom)
        }
        self.assertNotIn("subprocess", importados)
        self.assertNotIn("os", importados)
        # Tampoco invoca Git ni un motor de verificación (aún no existen).
        llamadas = {
            hijo.func.id
            for stmt in cuerpo
            for hijo in ast.walk(stmt)
            if isinstance(hijo, ast.Call) and isinstance(hijo.func, ast.Name)
        }
        self.assertFalse(
            llamadas
            & {
                "git_status",
                "git_diff",
                "rev_parse",
                "ejecutar_comando",
                "verificar_veredicto",
                "resolver_decisiones",
            }
        )

    def test_l_la_escritura_mcp_no_amplia_la_primitive(self):
        """B14-H: la herramienta MCP de escritura no cambia esta primitive.

        B14-E §13 pidió que la API Python existiera antes que la capacidad de
        agente; B14-H la añadió encima, con su propia frontera de autoridad. Lo
        que este test sigue garantizando es que `actualizar_estado()` mantiene
        su contrato completo y que no expone campos como `cerrado: bool`.
        """
        import dataclasses

        import mcp_tools

        from work_context import actualizar_estado

        catalogo = mcp_tools._cargar_herramientas_mcp()
        # La primitive conserva su capacidad técnica total (código interno).
        estado = actualizar_estado(WORK_ID, {"criterios": ["x"]}, self.raiz)
        self.assertEqual(estado.criterios, ("x",))
        # Y sigue sin derivar semántica de cierre.
        nombres = {campo.name for campo in dataclasses.fields(estado)}
        self.assertNotIn("cerrado", nombres)
        # La lectura por MCP sigue sin permiso.
        self.assertFalse(catalogo["work_state"]["requiere_permiso"])

    def test_l_no_devuelve_verdict_estructurado(self):
        estado = self._actualizar({"ultimo_veredicto": VERDICT})
        self.assertIsInstance(estado.ultimo_veredicto, str)


if __name__ == "__main__":
    unittest.main()
