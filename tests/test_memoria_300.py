#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v3.0.0: memoria SQLite, skills, curador y daemon."""

import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc  # noqa: E402


class BaseMemoria300(unittest.TestCase):
    """Aísla CONFIG_DIR/DB_PATH en un directorio temporal por test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = self.tmp.name
        self.parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp),
            mock.patch.object(sc, "DB_PATH",
                              os.path.join(self.dir_tmp, "memoria.db")),
            mock.patch.object(sc, "MEMORIA_PROYECTO", ""),
        ]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(sc._db_cerrar)
        sc._db_cerrar()

    def tearDown(self):
        sc._db_cerrar()

    @staticmethod
    def _resultados():
        return [{"paso": 1, "descripcion": "editar login", "accion": "editar",
                 "resultado": "éxito", "detalle": "ok", "intentos": 1,
                 "archivos": ["lib/login.dart"]},
                {"paso": 2, "descripcion": "correr tests", "accion": "ejecutar",
                 "resultado": "éxito", "detalle": "código 0", "intentos": 1,
                 "comando": "flutter test"}]


class TestDB(BaseMemoria300):
    def test_init_crea_tablas(self):
        ruta = sc._db_init()
        self.assertTrue(os.path.exists(ruta))
        tablas = {f["name"] for f in sc._db_query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual(
            {"skills", "historial_aprendizaje", "contexto_kv", "cola"}, tablas)

    def test_insert_y_query(self):
        sid = sc._db_insert("INSERT INTO contexto_kv (clave, valor) "
                            "VALUES (?, ?)", ("prueba", "v1"))
        self.assertGreater(sid, 0)
        filas = sc._db_query("SELECT valor FROM contexto_kv WHERE clave = ?",
                             ("prueba",))
        self.assertEqual(filas[0]["valor"], "v1")

    def test_kv_obtener_y_fijar(self):
        self.assertEqual(sc._kv_obtener("inexistente", "def"), "def")
        sc._kv_fijar("clave", "valor1")
        sc._kv_fijar("clave", "valor2")     # upsert
        self.assertEqual(sc._kv_obtener("clave"), "valor2")


class TestSkills(BaseMemoria300):
    def test_guardar_es_idempotente_por_nombre(self):
        id1 = sc._skill_guardar("tarea-x", "hacer tarea x",
                                [{"descripcion": "a", "accion": "editar"}])
        id2 = sc._skill_guardar("tarea-x", "hacer tarea x mejorada",
                                [{"descripcion": "b", "accion": "ejecutar"}])
        self.assertEqual(id1, id2)
        skill = sc._skill_obtener(id1)
        self.assertEqual(skill["consulta"], "hacer tarea x mejorada")
        self.assertEqual(len(sc._skill_listar()), 1)

    def test_normalizar_nombre(self):
        self.assertEqual(
            sc._skill_normalizar_nombre("Arreglar Login con Google!"),
            "arreglar-login-con-google")

    def test_exito_refuerza_y_marca_confiable(self):
        sid = sc._skill_guardar("s1", "tarea uno", [])
        for _ in range(3):
            conf = sc._skill_registrar_exito(sid)
        fila = sc._db_query("SELECT usos, confiabilidad FROM skills "
                            "WHERE id = ?", (sid,))
        self.assertEqual(fila[0]["usos"], 3)
        self.assertEqual(fila[0]["confiabilidad"], 1.0)
        confiables = sc._skill_listar(solo_confiables=True)
        self.assertEqual([f["id"] for f in confiables], [sid])

    def test_fallo_penaliza(self):
        sid = sc._skill_guardar("s2", "tarea dos", [])
        sc._skill_registrar_exito(sid)
        conf = sc._skill_registrar_fallo(sid)
        self.assertLess(conf, 0.5)
        fila = sc._db_query("SELECT fallos FROM skills WHERE id = ?", (sid,))
        self.assertEqual(fila[0]["fallos"], 1)


class TestBusquedaSkills(BaseMemoria300):
    def setUp(self):
        super().setUp()
        with mock.patch.object(sc, "_MODELO_EMBEDDINGS", None), \
             mock.patch.object(sc, "SentenceTransformer", None):
            self.sid = sc._skill_guardar(
                "pagos-stripe", "migrar pagos a stripe", [])
            sc._skill_guardar("otro-tema", "configurar docker compose", [])

    def test_buscar_similar_jaccard(self):
        with mock.patch.object(sc, "_MODELO_EMBEDDINGS", None), \
             mock.patch.object(sc, "SentenceTransformer", None):
            skill = sc._skill_buscar("migrar pagos a stripe ya")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["id"], self.sid)
        self.assertGreaterEqual(skill["similitud"], 0.75)

    def test_buscar_sin_coincidencia(self):
        with mock.patch.object(sc, "_MODELO_EMBEDDINGS", None), \
             mock.patch.object(sc, "SentenceTransformer", None):
            skill = sc._skill_buscar("cocinar paella valenciana")
        self.assertIsNone(skill)

    def test_buscar_ignora_archivados(self):
        sc._db_ejecutar("UPDATE skills SET archivado = 1 WHERE id = ?",
                        (self.sid,))
        with mock.patch.object(sc, "_MODELO_EMBEDDINGS", None), \
             mock.patch.object(sc, "SentenceTransformer", None):
            skill = sc._skill_buscar("migrar pagos a stripe ya")
        self.assertIsNone(skill)


class TestAprendizaje(BaseMemoria300):
    def setUp(self):
        super().setUp()
        # Sin red/proveedor: _skill_generar cae al modo local silenciosamente.
        parche = mock.patch.object(sc, "_enviar_al_proveedor",
                                   side_effect=RuntimeError("sin red"))
        parche.start()
        self.addCleanup(parche.stop)

    def test_exito_genera_skill_nuevo(self):
        sid = sc._aprender_de_tarea("añadir modo oscuro", True,
                                    self._resultados())
        self.assertIsNotNone(sid)
        skill = sc._skill_obtener(sid)
        self.assertEqual(skill["nombre"], "anadir-modo-oscuro")
        self.assertEqual(len(skill["pasos"]), 2)

    def test_tarea_repetida_refuerza_en_lugar_de_duplicar(self):
        sid1 = sc._aprender_de_tarea("refactor del carrito", True,
                                     self._resultados())
        sid2 = sc._aprender_de_tarea("refactor del carrito ya hecho", True,
                                     self._resultados())
        self.assertEqual(sid1, sid2)
        fila = sc._db_query("SELECT usos FROM skills WHERE id = ?", (sid1,))
        self.assertEqual(fila[0]["usos"], 1)
        total = len(sc._db_query("SELECT id FROM skills"))
        self.assertEqual(total, 1)

    def test_fallo_registra_en_historial(self):
        n0 = len(sc._db_query("SELECT id FROM historial_aprendizaje"))
        sc._aprender_de_tarea("tarea que falla", False,
                              self._resultados(), detalle="tests en rojo")
        filas = sc._db_query(
            "SELECT exito, detalle FROM historial_aprendizaje")
        self.assertEqual(len(filas), n0 + 1)
        self.assertEqual(filas[-1]["exito"], 0)
        self.assertEqual(filas[-1]["detalle"], "tests en rojo")

    def test_sin_pasos_utiles_no_genera_skill(self):
        sid = sc._aprender_de_tarea("tarea vacía", True, [])
        self.assertIsNone(sid)


class TestCurador(BaseMemoria300):
    def _insertar_con_fecha(self, nombre, consulta, dias_atras):
        sid = sc._skill_guardar(nombre, consulta, [])
        fecha = (datetime.datetime.now()
                 - datetime.timedelta(days=dias_atras)
                 ).strftime("%Y-%m-%dT%H:%M:%S")
        sc._db_ejecutar("UPDATE skills SET creado = ?, ultimo_exito = ? "
                        "WHERE id = ?", (fecha, fecha, sid))
        return sid

    def test_archiva_skills_antiguos(self):
        viejo = self._insertar_con_fecha("viejo", "tarea muy antigua", 45)
        nuevo = self._insertar_con_fecha("nuevo", "tarea reciente", 1)
        acciones = sc._curador_ejecutar(dias_sin_uso=30, umbral_fusion=0.99)
        archivados = {a["id"] for a in acciones["archivados"]}
        self.assertIn(viejo, archivados)
        self.assertNotIn(nuevo, archivados)
        fila = sc._db_query("SELECT archivado FROM skills WHERE id = ?",
                            (viejo,))
        self.assertEqual(fila[0]["archivado"], 1)

    def test_fusiona_similares(self):
        a = self._insertar_con_fecha("dup-a", "instalar redis cache server",
                                     1)
        b = self._insertar_con_fecha("dup-b", "instalar redis cache server!",
                                     1)
        sc._db_ejecutar("UPDATE skills SET usos = 5 WHERE id = ?", (a,))
        acciones = sc._curador_ejecutar(dias_sin_uso=30, umbral_fusion=0.9)
        fusiones = [(f["conservado"], f["archivado"])
                    for f in acciones["fusiones"]]
        self.assertIn((a, b), fusiones)
        fila = sc._db_query(
            "SELECT usos, archivado FROM skills WHERE id = ?", (a,))
        self.assertEqual(fila[0]["usos"], 5)      # se conserva el más usado
        fila_b = sc._db_query("SELECT archivado FROM skills WHERE id = ?",
                              (b,))
        self.assertEqual(fila_b[0]["archivado"], 1)

    def test_notifica_revision(self):
        sid = sc._skill_guardar("roto", "tarea con fallos", [])
        for _ in range(3):
            sc._skill_registrar_fallo(sid)
        acciones = sc._curador_ejecutar(dias_sin_uso=30, umbral_fusion=0.99)
        self.assertIn(sid, {r["id"] for r in acciones["revision"]})

    def test_registra_ultima_ejecucion(self):
        sc._curador_ejecutar(dias_sin_uso=30, umbral_fusion=0.99)
        self.assertNotEqual(sc._kv_obtener(sc.CLAVE_CURADOR_ULTIMA), "")


class TestDaemon(BaseMemoria300):
    def test_primera_pasada_ejecuta_curador(self):
        tick = sc._daemon_tick(intervalo_horas=168)
        self.assertTrue(tick["curador"])
        self.assertEqual(tick["procesados"], [])

    def test_curador_no_vence_si_es_reciente(self):
        sc._curador_ejecutar()          # deja fecha actual
        ahora = datetime.datetime.now()
        tick = sc._daemon_tick(intervalo_horas=168, ahora=ahora)
        self.assertFalse(tick["curador"])

    def test_curador_vence_tras_intervalo(self):
        pasado = (datetime.datetime.now()
                  - datetime.timedelta(hours=200)).strftime(
                      "%Y-%m-%dT%H:%M:%S")
        sc._kv_fijar(sc.CLAVE_CURADOR_ULTIMA, pasado)
        ahora = datetime.datetime.now()
        tick = sc._daemon_tick(intervalo_horas=168, ahora=ahora)
        self.assertTrue(tick["curador"])

    def test_procesa_cola_pendiente(self):
        sid = sc._skill_guardar("cola-1", "tarea en cola", [])
        sc._cola_encolar(sid)
        sc._curador_ejecutar()          # fija fecha → curador no corre
        tick = sc._daemon_tick(intervalo_horas=999999)
        self.assertFalse(tick["curador"])
        self.assertIn(sid, tick["procesados"])
        estados = {f["estado"] for f in sc._db_query(
            "SELECT estado FROM cola")}
        self.assertIn("hecho", estados)

    def test_descarta_cola_de_skill_archivado(self):
        sid = sc._skill_guardar("viejo-cola", "tarea vieja en cola", [])
        sc._db_ejecutar("UPDATE skills SET archivado = 1 WHERE id = ?",
                        (sid,))
        sc._cola_encolar(sid)
        sc._curador_ejecutar()
        tick = sc._daemon_tick(intervalo_horas=999999)
        self.assertNotIn(sid, tick["procesados"])
        estado = sc._db_query("SELECT estado FROM cola")[0]["estado"]
        self.assertEqual(estado, "descartado")


class TestAgenteAprendizaje(BaseMemoria300):
    def test_delegacion_completa(self):
        from agentes import AgenteAprendizaje

        agente = AgenteAprendizaje()
        self.assertTrue(os.path.exists(agente.inicializar()))
        sid = agente.generar_skill("agente skill", self._resultados())
        self.assertIsNotNone(sid)
        encontrado = agente.buscar_skill("agente skill ahora")
        self.assertIsNotNone(encontrado)
        conf = agente.registrar_exito(sid)
        self.assertGreater(conf, 0.5)
        self.assertEqual(agente.aprender_de_tarea(
            "agente skill otra vez", True, self._resultados()), sid)
        self.assertIsInstance(agente.listar_skills(), list)
        resumen = agente.curar(dias_sin_uso=30, umbral_fusion=0.99)
        self.assertIn("archivados", resumen)
        agente.encolar_skill(sid)


class TestFlagsCLI(unittest.TestCase):
    def test_flags_nuevos(self):
        args = sc.crear_parser().parse_args(["--plan", "t"])
        self.assertFalse(args.daemon)
        self.assertFalse(args.curador)
        self.assertFalse(args.skills)
        self.assertFalse(args.sin_aprendizaje)
        self.assertEqual(args.daemon_intervalo,
                         sc.DAEMON_INTERVALO_HORAS_DEFECTO)

    def test_flag_daemon_intervalo(self):
        args = sc.crear_parser().parse_args(
            ["--daemon", "--daemon-intervalo", "24"])
        self.assertTrue(args.daemon)
        self.assertEqual(args.daemon_intervalo, 24)

    def test_version_300(self):
        self.assertEqual(sc.VERSION, "5.4.0")


if __name__ == "__main__":
    unittest.main()