"""Tests B14-H — `work_state_update` y frontera de autoridad de escritura.

Comprueba que la política ratificada en B14-G-R se aplica exactamente:
12 campos directos, 2 con autorización explícita, 7 protegidos siempre.

Todos los casos corren sobre directorios temporales, no tocan el repositorio
real y no ejecutan Git.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_tools
import snapcontext as sc
from exceptions import (
    AutoridadInsuficienteError,
    ContratoEstadoInvalidoError,
    RutaInseguraError,
)
from work_context import crear_trabajo, escribir_documento_trabajo, leer_estado

WORK_ID = "b14h-authority"

VERDICT = (
    "pasa — comando `python3 -m pytest tests/ -q`; scope: proyecto completo; "
    "anchor Git: 45e2e0a71d06; autor: Agente B; instante: 2026-09-27T01:00:00Z"
)

#: 12 campos de escritura directa (B14-G-R).
DIRECTOS = (
    "titulo",
    "trabajo_completado",
    "trabajo_pendiente",
    "bloqueos",
    "siguiente_paso",
    "comando_verificacion",
    "ultimo_veredicto",
    "git_base",
    "git_actual",
    "git_rama",
    "git_pr_issue",
    "cierre_motivo",
)

#: 2 campos que exigen autorización explícita.
CON_AUTORIZACION = ("ciclo_de_vida", "cierre_estado")

#: 7 campos protegidos: se rechazan siempre, incluso con autorización.
PROTEGIDOS = (
    "objetivo",
    "criterios",
    "alcance_incluido",
    "alcance_excluido",
    "restricciones",
    "dependencias_entorno",
    "veredictos_obsoletos",
)

VALOR_DIRECTO = {
    "titulo": "Titulo nuevo",
    "trabajo_completado": "tarea hecha",
    "trabajo_pendiente": "tarea pendiente",
    "bloqueos": "ninguno activo",
    "siguiente_paso": "seguir",
    "comando_verificacion": "python3 -m pytest",
    "ultimo_veredicto": VERDICT,
    "git_base": "main",
    "git_actual": "feat/x",
    "git_rama": "feat/x",
    "git_pr_issue": "#42",
    "cierre_motivo": "motivo documental",
}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name) / "proj"
        self.raiz.mkdir(parents=True)
        crear_trabajo(
            WORK_ID,
            self.raiz,
            titulo="Titulo inicial",
            objetivo="Objetivo ratificado",
            criterios=["criterio ratificado"],
            restricciones=["restriccion ratificada"],
        )

    @property
    def _state(self) -> Path:
        return self.raiz / ".work" / WORK_ID / "state.md"

    def _texto(self) -> str:
        return self._state.read_text(encoding="utf-8")

    def _update(self, cambios, autorizacion=False, **kwargs) -> dict:
        return sc._tool_work_state_update(
            WORK_ID, cambios, autorizacion, str(self.raiz), **kwargs
        )

    def _mcp(self, cambios, autorizacion=False) -> dict:
        """Invoca por el dispatcher MCP oficial, confirmando el permiso.

        `work_state_update` exige permiso MCP (es una mutación). En un test sin
        TTY la confirmación interactiva se deniega por seguridad, así que se
        parchea `_confirmar_accion` para simular que el usuario autoriza.
        """
        import unittest.mock as mock

        with mock.patch.object(sc, "_confirmar_accion", return_value=True):
            llamada = mcp_tools._ejecutar_herramienta_mcp(
                "work_state_update",
                {
                    "work_id": WORK_ID,
                    "cambios": cambios,
                    "autorizacion": autorizacion,
                    "directorio": str(self.raiz),
                },
                confirmar=True,
            )
        self.assertIn("resultado", llamada, llamada.get("error"))
        return llamada["resultado"]


# ===========================================================================
# Escritura directa — los 12 campos
# ===========================================================================
class TestEscrituraDirecta(_Base):
    def test_cada_campo_directo_se_puede_actualizar(self):
        for indice, campo in enumerate(DIRECTOS):
            with self.subTest(campo=campo):
                # Un trabajo propio por campo: cada caso parte del fixture
                # inicial, sin arrastrar el estado del anterior.
                work_id = f"b14h-{indice}"
                crear_trabajo(work_id, self.raiz, objetivo="o")
                r = sc._tool_work_state_update(
                    work_id, {campo: VALOR_DIRECTO[campo]}, False, str(self.raiz)
                )
                self.assertTrue(r["ok"], r.get("error"))
                self.assertEqual(r["categoria"], "actualizado")

    def test_valores_quedan_reflejados_al_releer(self):
        r = self._update({"siguiente_paso": "seguir", "bloqueos": "ninguno activo"})
        self.assertTrue(r["ok"])
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.siguiente_paso, "seguir")
        self.assertEqual(estado.bloqueos, ("ninguno activo",))

    def test_multiples_campos_directos_en_una_operacion(self):
        r = self._update(
            {
                "siguiente_paso": "paso A",
                "trabajo_completado": "hecho X",
                "trabajo_pendiente": "falta Y",
                "comando_verificacion": "pytest -q",
            }
        )
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(
            sorted(r["actualizados"]),
            ["comando_verificacion", "siguiente_paso", "trabajo_completado", "trabajo_pendiente"],
        )
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.siguiente_paso, "paso A")
        self.assertEqual(estado.trabajo_completado, ("hecho X",))
        self.assertEqual(estado.trabajo_pendiente, ("falta Y",))
        self.assertEqual(estado.comando_verificacion, "pytest -q")

    def test_la_allowlist_coincide_con_la_politica_ratificada(self):
        self.assertEqual(set(sc._CAMPOS_ESCRITURA_DIRECTA), set(DIRECTOS))
        self.assertEqual(set(sc._CAMPOS_ESCRITURA_CON_AUTORIZACION), set(CON_AUTORIZACION))
        self.assertEqual(set(sc._CAMPOS_ESCRITURA_PROTEGIDOS), set(PROTEGIDOS))
        # B15-L: la política no cambia, pero `veredictos_obsoletos` deja de estar
        # en la allowlist del writer genérico — su única vía es la operación
        # semántica `registrar_verdicto_obsoleto()`. La protección se refuerza:
        # el campo sigue clasificado como "autoridad" en `_exigir_autoridad`.
        from work_context import _CAMPOS_ESCRIBIBLES, registrar_veredicto_obsoleto

        total = (
            set(sc._CAMPOS_ESCRITURA_DIRECTA)
            | set(sc._CAMPOS_ESCRITURA_CON_AUTORIZACION)
            | set(sc._CAMPOS_ESCRITURA_PROTEGIDOS)
        )
        self.assertEqual(len(total), 21)
        # Los campos que la autoridad permite siguen siendo escribibles; el
        # único que sale de la primitiva es el protegido `veredictos_obsoletos`.
        self.assertEqual(
            set(_CAMPOS_ESCRIBIBLES),
            set(sc._CAMPOS_ESCRITURA_DIRECTA)
            | set(sc._CAMPOS_ESCRITURA_CON_AUTORIZACION)
            | (set(PROTEGIDOS) - {"veredictos_obsoletos"}),
        )
        self.assertNotIn("veredictos_obsoletos", _CAMPOS_ESCRIBIBLES)
        self.assertTrue(callable(registrar_veredicto_obsoleto))


# ===========================================================================
# Protección — los 7 campos, siempre
# ===========================================================================
class TestProteccion(_Base):
    def test_cada_campo_protegido_se_rechaza(self):
        for campo in PROTEGIDOS:
            with self.subTest(campo=campo):
                work_id = f"b14hp-{campo}"
                crear_trabajo(work_id, self.raiz, objetivo="o")
                antes = (self.raiz / ".work" / work_id / "state.md").read_text(
                    encoding="utf-8"
                )
                r = sc._tool_work_state_update(
                    work_id, {campo: ["x"]}, False, str(self.raiz)
                )
                self.assertFalse(r["ok"])
                self.assertEqual(r["categoria"], "autoridad")
                self.assertIn(campo, r["campos"])
                # El rechazo no modifica el documento.
                self.assertEqual(
                    (self.raiz / ".work" / work_id / "state.md").read_text(
                        encoding="utf-8"
                    ),
                    antes,
                )

    def test_autorizacion_no_habilita_los_protegidos(self):
        for campo in PROTEGIDOS:
            with self.subTest(campo=campo):
                antes = self._texto()
                r = self._update({campo: ["x"]}, autorizacion=True)
                self.assertFalse(r["ok"])
                self.assertEqual(r["categoria"], "autoridad")
                self.assertEqual(self._texto(), antes)

    def test_veredictos_obsoletos_siempre_rechazado(self):
        r = self._update({"veredictos_obsoletos": ["x"]}, autorizacion=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")

    def test_no_se_toca_decisions_ni_assertions(self):
        base = self.raiz / ".work" / WORK_ID
        antes = {
            n: (base / n).read_text(encoding="utf-8")
            for n in ("decisions.md", "assertions.md")
        }
        self._update({"siguiente_paso": "x"})
        self._update({"cierre_estado": "cerrado"}, autorizacion=True)
        for nombre, contenido in antes.items():
            with self.subTest(documento=nombre):
                self.assertEqual((base / nombre).read_text(encoding="utf-8"), contenido)


# ===========================================================================
# Confirmación — los 2 campos
# ===========================================================================
class TestConfirmacion(_Base):
    def test_ciclo_de_vida_sin_autorizacion_rechazado(self):
        antes = self._texto()
        r = self._update({"ciclo_de_vida": "cerrado"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")
        self.assertIn("ciclo_de_vida", r["campos"])
        self.assertEqual(self._texto(), antes)

    def test_ciclo_de_vida_con_autorizacion_actualiza(self):
        r = self._update({"ciclo_de_vida": "cerrado"}, autorizacion=True)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(leer_estado(WORK_ID, self.raiz).ciclo_de_vida, "cerrado")

    def test_cierre_estado_sin_autorizacion_rechazado(self):
        antes = self._texto()
        r = self._update({"cierre_estado": "cerrado"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")
        self.assertEqual(self._texto(), antes)

    def test_cierre_estado_con_autorizacion_actualiza(self):
        r = self._update({"cierre_estado": "cerrado"}, autorizacion=True)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(leer_estado(WORK_ID, self.raiz).cierre_estado, "cerrado")

    def test_cierre_motivo_solo_es_directo(self):
        r = self._update({"cierre_motivo": "integrado"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(leer_estado(WORK_ID, self.raiz).cierre_motivo, "integrado")

    def test_cierre_motivo_mas_estado_sin_autorizacion_rechazado(self):
        antes = self._texto()
        r = self._update({"cierre_motivo": "integrado", "cierre_estado": "cerrado"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")
        self.assertIn("cierre_estado", r["campos"])
        self.assertEqual(self._texto(), antes)

    def test_cierre_motivo_mas_estado_con_autorizacion_actualiza(self):
        r = self._update(
            {"cierre_motivo": "integrado", "cierre_estado": "cerrado"},
            autorizacion=True,
        )
        self.assertTrue(r["ok"], r.get("error"))
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.cierre_motivo, "integrado")
        self.assertEqual(estado.cierre_estado, "cerrado")

    def test_la_autorizacion_no_es_global(self):
        """No se arrastra entre operaciones: hay que autorizar cada vez."""
        self.assertTrue(self._update({"cierre_estado": "cerrado"}, True)["ok"])
        # La siguiente operación vuelve a estar sin autorización.
        r = self._update({"ciclo_de_vida": "cerrado"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")


# ===========================================================================
# Operaciones mixtas — atomicidad de la política
# ===========================================================================
class TestOperacionesMixtas(_Base):
    def test_directo_mas_protegido_rechaza_completo(self):
        antes = self._texto()
        r = self._update({"siguiente_paso": "nuevo", "criterios": ["hack"]})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")
        self.assertIn("criterios", r["campos"])
        # Sin escritura parcial: el campo directo tampoco se aplicó.
        self.assertEqual(self._texto(), antes)
        self.assertEqual(leer_estado(WORK_ID, self.raiz).siguiente_paso, "(pendiente de declarar)")

    def test_directo_mas_confirmacion_sin_autorizacion_rechaza_completo(self):
        antes = self._texto()
        r = self._update({"siguiente_paso": "nuevo", "cierre_estado": "cerrado"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")
        self.assertEqual(self._texto(), antes)

    def test_directo_mas_confirmacion_con_autorizacion_actualiza(self):
        r = self._update(
            {"siguiente_paso": "nuevo", "cierre_estado": "cerrado"}, autorizacion=True
        )
        self.assertTrue(r["ok"], r.get("error"))
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.siguiente_paso, "nuevo")
        self.assertEqual(estado.cierre_estado, "cerrado")

    def test_protegido_tiene_prioridad_sobre_la_autorizacion(self):
        antes = self._texto()
        r = self._update({"siguiente_paso": "nuevo", "objetivo": "hack"}, True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")
        self.assertEqual(self._texto(), antes)


# ===========================================================================
# `titulo` — H1 y campo canónico sincronizados
# ===========================================================================
class TestTitulo(_Base):
    def test_h1_y_campo_canonico_son_coherentes(self):
        r = self._update({"titulo": "Titulo nuevo"})
        self.assertTrue(r["ok"], r.get("error"))
        texto = self._texto()
        self.assertIn("# Trabajo: Titulo nuevo", texto)
        self.assertIn("- Título: Titulo nuevo", texto)
        self.assertEqual(leer_estado(WORK_ID, self.raiz).titulo, "Titulo nuevo")

    def test_siguen_sincronizados_tras_varias_actualizaciones(self):
        for titulo in ("Uno", "Dos", "Tres"):
            self.assertTrue(self._update({"titulo": titulo})["ok"])
            texto = self._texto()
            self.assertIn(f"# Trabajo: {titulo}", texto)
            self.assertIn(f"- Título: {titulo}", texto)
        self.assertEqual(leer_estado(WORK_ID, self.raiz).titulo, "Tres")

    def test_la_sincronizacion_es_atomica(self):
        """Un fallo antes del reemplazo no deja H1 y campo descuadrados."""
        import work_context as wc

        antes = self._texto()
        real = wc._reemplazar

        def _romper(temporal, destino):
            raise OSError("fallo simulado")

        wc._reemplazar = _romper  # type: ignore[method-assign]
        try:
            with self.assertRaises(OSError):
                self._update({"titulo": "No debe persistirse"})
        finally:
            wc._reemplazar = real  # type: ignore[method-assign]
        self.assertEqual(self._texto(), antes)

    def test_titulo_y_otros_campos_en_la_misma_operacion(self):
        r = self._update({"titulo": "Nuevo", "siguiente_paso": "paso"})
        self.assertTrue(r["ok"], r.get("error"))
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.titulo, "Nuevo")
        self.assertEqual(estado.siguiente_paso, "paso")
        self.assertIn("# Trabajo: Nuevo", self._texto())


# ===========================================================================
# Verdict y Git — texto declarativo, sin interpretación
# ===========================================================================
class TestVerdictYGit(_Base):
    def test_verdict_se_almacena_literalmente(self):
        r = self._update({"ultimo_veredicto": VERDICT})
        self.assertTrue(r["ok"], r.get("error"))
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.ultimo_veredicto, VERDICT)
        self.assertEqual(r["estado"]["ultimo_veredicto"], VERDICT)

    def test_la_respuesta_no_declara_el_verdict_como_verificado(self):
        r = self._update({"ultimo_veredicto": VERDICT})
        # No se añade ningún campo que presente el valor como verificado.
        for clave in r:
            with self.subTest(clave=clave):
                self.assertNotIn("verificado", clave.lower())
                self.assertNotIn("valido", clave.lower())
        self.assertNotIn("verificado", str(r).lower())

    def test_referencias_git_se_almacenan_como_texto(self):
        r = self._update(
            {"git_base": "main", "git_actual": "feat/x", "git_rama": "feat/x", "git_pr_issue": "#42"}
        )
        self.assertTrue(r["ok"], r.get("error"))
        estado = leer_estado(WORK_ID, self.raiz)
        self.assertEqual(estado.git_base, "main")
        self.assertEqual(estado.git_rama, "feat/x")
        self.assertEqual(estado.git_pr_issue, "#42")

    def test_la_herramienta_no_importa_subprocess_ni_os(self):
        import ast as astmod

        arbol = astmod.parse((Path(__file__).resolve().parents[1] / "snapcontext.py").read_text(encoding="utf-8"))
        for nombre in ("_tool_work_state_update", "_exigir_autoridad"):
            nodo = next(
                n for n in astmod.walk(arbol)
                if isinstance(n, astmod.FunctionDef) and n.name == nombre
            )
            cuerpo = list(nodo.body)
            if cuerpo and isinstance(cuerpo[0], astmod.Expr):
                cuerpo = cuerpo[1:]
            importados = {
                a.name
                for s in cuerpo
                for h in astmod.walk(s)
                if isinstance(h, astmod.Import)
                for a in h.names
            } | {
                (h.module or "")
                for s in cuerpo
                for h in astmod.walk(s)
                if isinstance(h, astmod.ImportFrom)
            }
            with self.subTest(funcion=nombre):
                self.assertNotIn("subprocess", importados)
                self.assertNotIn("os", importados)


# ===========================================================================
# Alias — no son vía alternativa
# ===========================================================================
class TestAlias(_Base):
    ALIAS = ("cerrado", "estado_final", "finalizar", "complete_work", "complete", "final_state")

    def test_alias_no_son_via_alternativa(self):
        for alias in self.ALIAS:
            with self.subTest(alias=alias):
                antes = self._texto()
                r = self._update({alias: True})
                self.assertFalse(r["ok"])
                # Ni siquiera con autorización: no es un nombre contractual.
                r2 = self._update({alias: True}, autorizacion=True)
                self.assertFalse(r2["ok"])
                self.assertEqual(self._texto(), antes)

    def test_alias_de_campos_protegidos_tampoco_funcionan(self):
        for alias in ("nuevo_objetivo", "criterios_extra", "modificar_alcance"):
            with self.subTest(alias=alias):
                self.assertFalse(self._update({alias: "x"})["ok"])


# ===========================================================================
# Errores — señales distinguibles
# ===========================================================================
class TestErrores(_Base):
    def test_autoridad_ingsuficiente_es_senal_propia(self):
        with self.assertRaises(AutoridadInsuficienteError) as ctx:
            sc._exigir_autoridad({"cierre_estado": "cerrado"}, False)
        self.assertEqual(ctx.exception.campos, ("cierre_estado",))
        # Distinta de una entrada inválida, aunque ambas sean ValueError.
        with self.assertRaises(ValueError) as otro:
            sc._exigir_autoridad({"no_existe": 1}, False)
        self.assertNotIsInstance(otro.exception, AutoridadInsuficienteError)

    def test_categoria_autoridad_para_campos_validos_no_autorizados(self):
        r = self._update({"ciclo_de_vida": "cerrado"})
        self.assertEqual(r["categoria"], "autoridad")
        self.assertIn("campos", r)

    def test_categoria_entrada_para_nombres_desconocidos(self):
        r = self._update({"campo_inventado": "x"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "entrada")
        self.assertIn("campo_inventado", r["error"])

    def test_categoria_entrada_para_tipo_incorrecto(self):
        r = self._update({"siguiente_paso": ["no", "es", "texto"]})
        self.assertEqual(r["categoria"], "entrada")

    def test_categoria_documento_para_documento_invalido(self):
        escribir_documento_trabajo(
            f".work/{WORK_ID}/state.md",
            self._texto().replace("Kind: work-state", "Kind: work-decisions"),
            self.raiz,
        )
        r = self._update({"siguiente_paso": "x"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "documento")

    def test_trabajo_inexistente_es_error_de_documento(self):
        r = sc._tool_work_state_update("no-existe", {"siguiente_paso": "x"}, False, str(self.raiz))
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "documento")
        self.assertFalse((self.raiz / ".work" / "no-existe").exists())

    def test_ruta_insegura_reportada(self):
        for malo in ("../fuera", "/abs", "MAYUS"):
            with self.subTest(work_id=malo):
                r = sc._tool_work_state_update(malo, {"siguiente_paso": "x"}, False, str(self.raiz))
                self.assertFalse(r["ok"])
                self.assertIn(r["categoria"], ("ruta", "entrada"))

    def test_cambios_vacios_rechazados(self):
        r = self._update({})
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "entrada")


# ===========================================================================
# Frontera MCP y compatibilidad
# ===========================================================================
class TestFronteraMcp(_Base):
    def test_work_state_sigue_siendo_read_only(self):
        catalogo = mcp_tools._cargar_herramientas_mcp()
        self.assertIn("work_state", catalogo)
        self.assertFalse(catalogo["work_state"]["requiere_permiso"])
        self.assertEqual(catalogo["work_state"]["parametros"]["work_id"], "str")
        self.assertNotIn("cambios", catalogo["work_state"]["parametros"])

    def test_work_state_update_exige_permiso(self):
        catalogo = mcp_tools._cargar_herramientas_mcp()
        self.assertIn("work_state_update", catalogo)
        self.assertTrue(catalogo["work_state_update"]["requiere_permiso"])

    def test_el_dispatcher_aplica_la_misma_politica(self):
        antes = self._texto()
        r = self._mcp({"criterios": ["hack"]}, autorizacion=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["categoria"], "autoridad")
        self.assertEqual(self._texto(), antes)

    def test_el_dispatcher_actualiza_cuando_corresponde(self):
        r = self._mcp({"siguiente_paso": "vía MCP"})
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(leer_estado(WORK_ID, self.raiz).siguiente_paso, "vía MCP")

    def test_la_primitive_sigue_disponible_para_codigo_interno(self):
        """`actualizar_estado()` conserva su capacidad técnica completa."""
        from work_context import actualizar_estado

        estado = actualizar_estado(
            WORK_ID, {"criterios": ["cambio interno"]}, str(self.raiz)
        )
        self.assertEqual(estado.criterios, ("cambio interno",))
        # El Mandato sigue protegido para el AGENTE, no para el código interno.
        r = self._update({"criterios": ["cambio interno"]})
        self.assertFalse(r["ok"])

    def test_leer_estado_no_ha_cambiado(self):
        import inspect

        from work_context import leer_estado

        self.assertEqual(
            list(inspect.signature(leer_estado).parameters), ["work_id", "directorio"]
        )


if __name__ == "__main__":
    unittest.main()
