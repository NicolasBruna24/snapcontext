#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de skills dinámicos (v6.6.0): skill_abstraction + integración.

Cubre: extracción (LLM mockeado y heurística), aplicación de reglas,
inyección idempotente en CLAUDE.md, tabla ``reglas`` (BD temporal),
curador ``aprender_de_plan``, enriquecimiento del prompt del planificador
y flags ``--skills-dinamicos`` / ``--inyectar-reglas``.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snapcontext as sc                          # noqa: E402
import skill_abstraction as sa                    # noqa: E402
import curador_proactivo as cp                    # noqa: E402

PLAN_EXITOSO = {
    "tarea": "añadir endpoint de pagos con autenticación",
    "pasos": [
        {"tipo": "editar", "archivo": "pago_service.dart"},
        {"tipo": "editar", "archivo": "middleware/auth.dart"},
        {"tipo": "ejecutar", "comando": "flutter test"},
    ],
}

REGLA_LLM = {
    "patron": "añadir endpoint de pagos",
    "accion": "modificar pago_service.dart e inyectar middleware",
    "archivos_afectados": ["pago_service.dart", "middleware/auth.dart"],
    "dependencias": ["auth_middleware"],
}


class BaseReglas(unittest.TestCase):
    """Base: BD temporal aislada por test."""

    def setUp(self):
        sc._db_cerrar()                     # libera cualquier BD anterior
        self._tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self._tmp.name)
        p1 = mock.patch.object(sc, "DB_PATH", self.raiz / "memoria.db")
        p2 = mock.patch.object(sc, "_DB_CONEXION", None)
        for p in (p1, p2):
            p.start()
            self.addCleanup(p.stop)
        # addCleanup es LIFO: _db_cerrar se registra después de _tmp.cleanup
        # para que cierre la conexión SQLite ANTES de borrar el directorio
        # temporal (Windows no permite eliminar archivos SQLite abiertos).
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(sc._db_cerrar)


    def tearDown(self):
        sc.SKILLS_DINAMICOS = True


class TestExtraccion(BaseReglas):
    """Extracción de reglas: LLM (mockeado) y heurística."""

    def test_extraccion_con_llm_mockeado(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=json.dumps(REGLA_LLM)):
            regla = sa.extraer_regla(PLAN_EXITOSO, {"directorio": "."})
        self.assertEqual(regla["patron"], "añadir endpoint de pagos")
        self.assertIn("pago_service.dart", regla["archivos_afectados"])
        self.assertEqual(regla["dependencias"], ["auth_middleware"])

    def test_extraccion_fallback_heuristica_si_llm_falla(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=RuntimeError("sin API")):
            regla = sa.extraer_regla(PLAN_EXITOSO, {})
        self.assertTrue(regla["patron"])
        archivos = set(regla["archivos_afectados"])
        self.assertIn("pago_service.dart", archivos)
        self.assertIn("middleware/auth.dart", archivos)

    def test_extraccion_heuristica_con_llm_respuesta_invalida(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="no soy json"):
            regla = sa.extraer_regla(PLAN_EXITOSO, {})
        self.assertIn("pago_service.dart", regla["archivos_afectados"])
        self.assertEqual(regla["confianza"], 0.6)   # heurística

    def test_extraccion_llm_json_parcial(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value='{"patron": "solo patron"}'):
            regla = sa.extraer_regla(PLAN_EXITOSO, {})
        self.assertEqual(regla["patron"], "solo patron")
        self.assertIsInstance(regla["archivos_afectados"], list)

    def test_extraccion_plan_vacio_no_lanza(self):
        regla = sa.extraer_regla({}, {})
        self.assertIsInstance(regla, dict)
        self.assertIn("patron", regla)


class TestAplicacionReglas(BaseReglas):
    """aplicar_regla: coincidencia y no coincidencia."""

    def test_aplicar_regla_tarea_similar(self):
        pasos = sa.aplicar_regla(
            REGLA_LLM, "añadir endpoint de pagos para facturas")
        self.assertIsNotNone(pasos)
        self.assertIn("pago_service.dart",
                      json.dumps(pasos, ensure_ascii=False))

    def test_aplicar_regla_tarea_diferente_devuelve_none(self):
        self.assertIsNone(
            sa.aplicar_regla(REGLA_LLM, "migrar la base de datos a postgres"))

    def test_aplicar_regla_tarea_vacia(self):
        self.assertIsNone(sa.aplicar_regla(REGLA_LLM, ""))


class TestInyeccionClaudeMd(BaseReglas):
    """Inyección idempotente en CLAUDE.md."""

    def test_inyeccion_crea_seccion_si_no_existe(self):
        md = self.raiz / "CLAUDE.md"
        md.write_text("# Proyecto\n\nContenido.\n", encoding="utf-8")
        self.assertTrue(sa.inyectar_en_claudemd(REGLA_LLM, str(self.raiz)))
        texto = md.read_text(encoding="utf-8")
        self.assertIn(sa.SECCION_REGLAS, texto)
        self.assertIn("añadir endpoint de pagos", texto)

    def test_inyeccion_idempotente_no_duplica(self):
        self.assertTrue(sa.inyectar_en_claudemd(REGLA_LLM, str(self.raiz)))
        self.assertFalse(sa.inyectar_en_claudemd(REGLA_LLM, str(self.raiz)))
        texto = (self.raiz / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(texto.count("añadir endpoint de pagos"), 1)

    def test_inyeccion_sanitiza_bloques_de_codigo(self):
        regla = dict(REGLA_LLM, patron="patron ```danger``` raro")
        self.assertTrue(sa.inyectar_en_claudemd(regla, str(self.raiz)))
        texto = (self.raiz / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertNotIn("```", texto)

    def test_inyectar_todas_las_reglas(self):
        sa.guardar_regla(dict(REGLA_LLM, patron="regla A distinta",
                              confianza=0.5), directorio=str(self.raiz))
        sa.guardar_regla(dict(REGLA_LLM, patron="regla B muy diferente",
                              confianza=0.5), directorio=str(self.raiz))
        self.assertEqual(sa.inyectar_todas_las_reglas(str(self.raiz)), 2)
        texto = (self.raiz / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("regla A distinta", texto)
        self.assertIn("regla B muy diferente", texto)
        # Idempotente en la segunda pasada.
        self.assertEqual(sa.inyectar_todas_las_reglas(str(self.raiz)), 0)


class TestBaseDatos(BaseReglas):
    """Tabla ``reglas``: migración, guardado y refuerzo."""

    def test_migracion_crea_tabla_reglas(self):
        sc._db_init()
        info = sc._DB_CONEXION.execute(
            "PRAGMA table_info(reglas)").fetchall()
        columnas = {fila[1] for fila in info}
        esperadas = {"id", "patron", "accion", "archivos_afectados",
                     "dependencias", "confianza", "usos", "creado"}
        self.assertTrue(esperadas.issubset(columnas))

    def test_guardar_regla_nueva_y_refuerzo(self):
        r1 = sa.guardar_regla(dict(REGLA_LLM, confianza=0.6))
        self.assertTrue(r1["nueva"])
        r2 = sa.guardar_regla(dict(REGLA_LLM, confianza=0.6))
        self.assertFalse(r2["nueva"])
        self.assertGreater(r2["confianza"], 0.6)
        filas = sc._db_query("SELECT COUNT(*) AS n FROM reglas")
        self.assertEqual(filas[0]["n"], 1)   # no duplica

    def test_buscar_reglas_prioriza_confianza(self):
        sa.guardar_regla(dict(REGLA_LLM, patron="endpoint de pagos",
                              confianza=0.5), directorio=str(self.raiz))
        sa.guardar_regla(dict(REGLA_LLM, patron="añadir endpoint de pagos",
                              confianza=0.9), directorio=str(self.raiz))
        reglas = sa.buscar_reglas("añadir endpoint de pagos")
        self.assertTrue(reglas)
        self.assertGreaterEqual(reglas[0]["confianza"],
                                reglas[-1]["confianza"])

    def test_sql_reglas_in_memory(self):
        """El esquema de la tabla funciona de forma aislada en SQLite."""
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE reglas (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              patron TEXT NOT NULL,
              accion TEXT NOT NULL,
              archivos_afectados TEXT,
              dependencias TEXT,
              confianza REAL DEFAULT 1.0,
              usos INTEGER DEFAULT 0,
              creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        con.execute("INSERT INTO reglas (patron, accion) VALUES (?, ?)",
                    ("patron x", "accion y"))
        fila = dict(con.execute(
            "SELECT patron, confianza, usos FROM reglas").fetchone())
        self.assertEqual(fila["patron"], "patron x")
        self.assertEqual(fila["confianza"], 1.0)
        self.assertEqual(fila["usos"], 0)
        con.close()


class TestCuradorYPlanificador(BaseReglas):
    """Integración con el curador y el planificador."""

    def test_curador_aprende_de_plan_exitoso(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=json.dumps(REGLA_LLM)):
            regla = cp.aprender_de_plan(
                PLAN_EXITOSO["tarea"], PLAN_EXITOSO["pasos"],
                raiz=str(self.raiz))
        self.assertIsNotNone(regla)
        filas = sc._db_query("SELECT patron FROM reglas")
        self.assertEqual(len(filas), 1)

    def test_curador_no_aprende_si_deshabilitado(self):
        sc.SKILLS_DINAMICOS = False
        regla = cp.aprender_de_plan("tarea", PLAN_EXITOSO["pasos"])
        self.assertIsNone(regla)
        self.assertEqual(
            sc._db_query("SELECT COUNT(*) AS n FROM reglas")[0]["n"], 0)

    def test_planificador_enriquece_prompt_con_regla(self):
        sa.guardar_regla(dict(REGLA_LLM, patron="endpoint de pagos",
                              confianza=0.9), directorio=str(self.raiz))
        prompt = "Crea el endpoint de pagos."
        enriquecido = sc._enriquecer_prompt_con_reglas(prompt, prompt)
        self.assertIn("REGLAS APRENDIDAS", enriquecido)
        self.assertIn("endpoint de pagos", enriquecido)
        self.assertTrue(enriquecido.startswith(prompt))

    def test_planificador_sin_reglas_devuelve_prompt_intacto(self):
        prompt = "Tarea sin reglas asociadas."
        self.assertEqual(
            sc._enriquecer_prompt_con_reglas(prompt, prompt), prompt)

    def test_planificador_respeta_flag_deshabilitado(self):
        sa.guardar_regla(dict(REGLA_LLM, patron="endpoint de pagos"),
                         directorio=str(self.raiz))
        sc.SKILLS_DINAMICOS = False
        prompt = "Crea el endpoint de pagos."
        self.assertEqual(
            sc._enriquecer_prompt_con_reglas(prompt, prompt), prompt)


class TestFlagsCli(unittest.TestCase):
    """Flags ``--skills-dinamicos`` y ``--inyectar-reglas``."""

    def test_flag_activado_por_defecto(self):
        args = sc.crear_parser().parse_args(["hola"])
        self.assertTrue(args.skills_dinamicos)
        self.assertFalse(args.inyectar_reglas)

    def test_flag_deshabilitar(self):
        args = sc.crear_parser().parse_args(
            ["--sin-skills-dinamicos", "hola"])
        self.assertFalse(args.skills_dinamicos)

    def test_flag_inyectar_reglas(self):
        args = sc.crear_parser().parse_args(["--inyectar-reglas"])
        self.assertTrue(args.inyectar_reglas)

    def test_version_660(self):
        self.assertEqual(sc.VERSION, "6.30.0")


if __name__ == "__main__":
    unittest.main()


