"""Tests B14-D — primera integración real del Work State Reader.

El consumidor es la **capa de herramientas MCP** (`work_state`): hasta ahora
un agente que preguntaba por el estado de un trabajo solo podía hacer
`read_file .work/<id>/state.md` e interpretar el Markdown a mano.

Cada caso demuestra la integración real, sin mocks de sistemas que aún no
existen (ni resolver, ni motor de verificación, ni Git).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_tools
import snapcontext as sc
from exceptions import ContratoEstadoInvalidoError
from work_context import (
    actualizar_estado,
    crear_trabajo,
    escribir_documento_trabajo,
    leer_estado,
)

WORK_ID = "b14d-consumer"

PLACEHOLDER_VERDICT = "- Último veredicto de verificación: (no hay verificación registrada)"

VERDICT = (
    "pasa — comando `python3 -m pytest tests/ -q`; scope: proyecto completo; "
    "anchor Git: 45e2e0a71d06; autor: Agente B; instante: 2026-09-27T01:00:00Z"
)


class _BaseIntegracion(unittest.TestCase):
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

    def _herramienta(self, **argumentos) -> dict:
        """Invoca la herramienta por su API oficial (el dispatcher MCP)."""
        return mcp_tools._ejecutar_herramienta_mcp("work_state", argumentos, confirmar=False)


# ===========================================================================
# A — Trabajo existente: el consumidor obtiene WorkState vía la API oficial
# ===========================================================================
class TestConsumidor(_BaseIntegracion):
    def test_a_herramienta_esta_registrada_y_es_de_solo_lectura(self):
        catalogo = mcp_tools._cargar_herramientas_mcp()
        self.assertIn("work_state", catalogo)
        self.assertFalse(catalogo["work_state"]["requiere_permiso"])
        self.assertIn("work_id", catalogo["work_state"]["parametros"])

    def test_a_trabajo_existente_devuelve_estado_estructurado(self):
        llamada = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))
        self.assertTrue(llamada["ok"], llamada.get("error"))
        resultado = llamada["resultado"]
        self.assertEqual(resultado["work_id"], WORK_ID)
        self.assertEqual(resultado["titulo"], "Division segura")
        self.assertEqual(resultado["proyecto"], "proj")
        self.assertEqual(resultado["ciclo_de_vida"], "declarado")
        self.assertEqual(
            resultado["objetivo"], "Soportar division segura y documentar divisor cero."
        )
        self.assertEqual(
            resultado["criterios"], ["safe_divide existe", "b == 0 lanza ValueError"]
        )

    def test_a_coincide_con_la_api_oficial_leer_estado(self):
        """El consumidor no duplica el parseo: usa la misma API que B14-C."""
        llamada = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))
        estado = leer_estado(WORK_ID, self.raiz)
        resultado = llamada["resultado"]
        self.assertEqual(resultado["objetivo"], estado.objetivo)
        self.assertEqual(resultado["criterios"], list(estado.criterios))
        self.assertEqual(resultado["siguiente_paso"], estado.siguiente_paso)
        self.assertEqual(resultado["git"]["rama"], estado.git_rama)

    def test_a_placeholders_conservados_literales(self):
        """La capa de consumidor no decide qué es «vacío» (B14-B §6)."""
        resultado = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))["resultado"]
        self.assertEqual(resultado["siguiente_paso"], "(pendiente de declarar)")
        self.assertEqual(resultado["ultimo_veredicto"], "(no hay verificación registrada)")
        self.assertEqual(resultado["bloqueos"], ["(ninguno)"])
        self.assertEqual(resultado["cierre"]["estado"], "(abierto)")


# ===========================================================================
# B — Estado actualizado: la segunda lectura ve el snapshot nuevo
# ===========================================================================
class TestEstadoActualizado(_BaseIntegracion):
    def test_b_segunda_lectura_refleja_el_snapshot_nuevo(self):
        primera = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))["resultado"]
        self.assertEqual(primera["siguiente_paso"], "(pendiente de declarar)")

        # B14-F: la evolución del trabajo se registra con la API declarativa;
        # la herramienta de lectura debe ver ese nuevo snapshot.
        actualizar_estado(
            WORK_ID,
            {
                "ciclo_de_vida": "cerrado",
                "trabajo_completado": "documentación README (Agente B)",
                "siguiente_paso": "revisar el cierre",
                "ultimo_veredicto": VERDICT,
                "cierre_estado": "cerrado",
            },
            self.raiz,
        )
        segunda = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))["resultado"]
        self.assertEqual(segunda["ciclo_de_vida"], "cerrado")
        self.assertEqual(segunda["trabajo_completado"], ["documentación README (Agente B)"])
        self.assertEqual(segunda["siguiente_paso"], "revisar el cierre")
        self.assertEqual(segunda["cierre"]["estado"], "cerrado")
        # El veredicto llega íntegro, sin descomponerse (B14-A-R).
        self.assertEqual(segunda["ultimo_veredicto"], VERDICT)


# ===========================================================================
# C / D — Trabajo inexistente y estado documental inválido
# ===========================================================================
class TestErrores(_BaseIntegracion):
    def test_c_trabajo_inexistente_no_rompe_la_herramienta(self):
        llamada = self._herramienta(work_id="no-existe", directorio=str(self.raiz))
        self.assertFalse(llamada["ok"])
        self.assertIn("error", llamada["resultado"])

    def test_c_work_id_inseguro_rechazado_sin_ejecutar(self):
        for malo in ("../fuera", "/abs", "con espacios", "MAYUS", ".."):
            with self.subTest(work_id=malo):
                llamada = self._herramienta(work_id=malo, directorio=str(self.raiz))
                self.assertFalse(llamada["ok"])

    def test_c_raiz_inexistente_reportada(self):
        llamada = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz / "no-existe"))
        self.assertFalse(llamada["ok"])

    def test_d_estado_invalido_reportado_como_error_contratual(self):
        self._reescribir(self._sustituir(("Kind: work-state", "Kind: work-decisions")))
        llamada = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))
        self.assertFalse(llamada["ok"])
        self.assertIn("inválido", llamada["resultado"]["error"])
        # Y la API oficial sigue lanzando su excepción propia (no se degrada).
        with self.assertRaises(ContratoEstadoInvalidoError):
            leer_estado(WORK_ID, self.raiz)

    def test_d_documento_vacio_reportado(self):
        self._reescribir("")


# ===========================================================================
# E — Frontera negativa de la integración
# ===========================================================================
class TestFronteraNegativa(_BaseIntegracion):
    def test_e_no_modifica_state_md(self):
        ruta = self.raiz / ".work" / WORK_ID / "state.md"
        antes = ruta.read_text(encoding="utf-8")
        mtime = ruta.stat().st_mtime_ns
        self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))
        self.assertEqual(ruta.read_text(encoding="utf-8"), antes)
        self.assertEqual(ruta.stat().st_mtime_ns, mtime)

    def test_e_no_toca_decisions_ni_assertions(self):
        base = self.raiz / ".work" / WORK_ID
        antes = {
            nombre: (base / nombre).read_text(encoding="utf-8")
            for nombre in ("decisions.md", "assertions.md")
        }
        self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))
        for nombre, contenido in antes.items():
            with self.subTest(documento=nombre):
                self.assertEqual((base / nombre).read_text(encoding="utf-8"), contenido)

    def test_e_no_crea_archivos(self):
        base = self.raiz / ".work" / WORK_ID
        antes = sorted(p.name for p in base.iterdir())
        self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))
        self.assertEqual(sorted(p.name for p in base.iterdir()), antes)

    def test_e_no_resuelve_decisiones_en_la_salida(self):
        """La salida no incorpora nada de `decisions.md` (B13-D)."""
        resultado = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))["resultado"]
        for clave in resultado:
            with self.subTest(clave=clave):
                self.assertNotIn("decision", clave.lower())
                self.assertNotIn("sustituid", clave.lower())
        self.assertNotIn("decisions", resultado)

    def test_e_no_expone_verdicto_interpretado_ni_obsolescencia(self):
        self._reescribir(
            self._sustituir(
                (PLACEHOLDER_VERDICT, f"- Último veredicto de verificación: {VERDICT}")
            )
        )
        resultado = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))["resultado"]
        # El veredicto es una cadena opaca: no hay resultado/anchor/autor sueltos.
        self.assertIsInstance(resultado["ultimo_veredicto"], str)
        for clave in resultado:
            with self.subTest(clave=clave):
                self.assertNotIn("anchor", clave.lower())
                self.assertNotIn("obsolet", clave.lower())

    def test_e_no_calcula_cierro_derivado(self):
        resultado = self._herramienta(work_id=WORK_ID, directorio=str(self.raiz))["resultado"]
        self.assertIsInstance(resultado["cierre"]["estado"], str)
        for clave in resultado:
            with self.subTest(clave=clave):
                self.assertNotIn("cerrado", clave.lower())

    def test_e_no_ejecuta_git(self):
        """Auditoría por fuente: la integración no importa `subprocess` ni `os`."""
        import ast

        raiz = Path(__file__).resolve().parents[1]
        for archivo, funcion in (("snapcontext.py", "_tool_work_state"),):
            arbol = ast.parse((raiz / archivo).read_text(encoding="utf-8"))
            nodo = next(
                n for n in ast.walk(arbol)
                if isinstance(n, ast.FunctionDef) and n.name == funcion
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
            with self.subTest(funcion=funcion):
                self.assertNotIn("subprocess", importados)
                self.assertNotIn("os", importados)

    def test_e_leer_estado_tiene_un_unico_consumidor_real(self):
        """`leer_estado()` deja de ser una API huérfana, sin crecer sin control.

        Se cuentan las *llamadas directas* a la API en el código de producción
        (los tests no cuentan: un consumidor de prueba no es una integración).
        """
        import ast

        raiz = Path(__file__).resolve().parents[1]
        llamadas_directas = []
        herramientas = []
        for archivo in sorted(raiz.glob("*.py")):
            if archivo.name in ("work_context.py",) or archivo.name.startswith("test_"):
                continue
            arbol = ast.parse(archivo.read_text(encoding="utf-8"))
            for nodo in ast.walk(arbol):
                if not isinstance(nodo, ast.Call):
                    continue
                func = nodo.func
                nombre = getattr(func, "id", None) or getattr(func, "attr", None)
                if nombre == "leer_estado":
                    llamadas_directas.append(archivo.name)
                if nombre == "_tool_work_state":
                    herramientas.append(archivo.name)
        # B15-L añade un segundo consumidor real y declarado: la operación de
        # obsolescencia, que necesita leer el documento para comparar el árbol.
        self.assertEqual(
            sorted(set(llamadas_directas)), ["snapcontext.py", "work_obsolencia.py"]
        )
        # Y el dispatcher MCP la expone como herramienta real, no privada.
        self.assertIn("mcp_tools.py", herramientas)


if __name__ == "__main__":
    unittest.main()

