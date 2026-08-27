#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la v5.0.0: curador proactivo (motor de refactorización autónoma).

Cubre: migración de BD, registro de métricas, evaluación de candidatos,
refactorización (exitosa y cada modo de fallo), notificaciones,
comandos CLI y daemon.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc          # noqa: E402
import curador_proactivo as cp    # noqa: E402


class BaseCurador500(unittest.TestCase):
    """Aísla CONFIG_DIR/DB_PATH en un directorio temporal por test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir_tmp = self.tmp.name
        self.parches = [
            mock.patch.object(sc, "CONFIG_DIR", self.dir_tmp),
            mock.patch.object(sc, "DB_PATH",
                              os.path.join(self.dir_tmp, "memoria.db")),
        ]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(sc._db_cerrar)
        sc._db_cerrar()
        sc._db_init()

    def tearDown(self):
        sc._db_cerrar()

    def _sembrar(self, nombre="skill_pesado", consulta="Hacer una tarea "
                 "compleja paso a paso con muchos detalles",
                 usos=10, exitos=2, fallos=8, tokens=2000,
                 tiempo_ms=900, version=1):
        sid = sc._db_insert(
            "INSERT INTO skills (nombre, consulta, creado, usos, exitos, "
            "fallos, tokens_promedio, tiempo_promedio_ms, version, activo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (nombre, consulta, "2026-08-26T00:00:00", usos, exitos, fallos,
             tokens, tiempo_ms, version))
        return sid

    def _salida(self, funcion, *args, **kwargs):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            resultado = funcion(*args, **kwargs)
        return resultado, buffer.getvalue()


class TestMigracion(BaseCurador500):
    def test_migracion_crea_columnas_y_historial(self):
        columnas = {f["name"] for f in sc._db_query("PRAGMA table_info(skills)")}
        esperadas = {"exitos", "fallos", "tokens_promedio",
                     "tiempo_promedio_ms", "ultimo_uso", "version", "activo"}
        self.assertLessEqual(esperadas, columnas)
        tablas = {f["name"] for f in sc._db_query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertIn("historial_skills", tablas)

    def test_migracion_es_idempotente(self):
        # Segunda inicialización sobre una BD ya migrada: sin duplicados.
        sc._db_init()
        sc._db_migrar_curador()
        columnas = [f["name"] for f in sc._db_query("PRAGMA table_info(skills)")]
        self.assertEqual(len(columnas), len(set(columnas)))
        self.assertIn("version", columnas)


class TestMetricas(BaseCurador500):
    def test_registro_exito_actualiza_medias(self):
        sid = self._sembrar(usos=9, exitos=9, fallos=0, tokens=100,
                            tiempo_ms=100)
        nueva_conf = sc._skill_registrar_exito(sid, tokens=200, tiempo_ms=300)
        fila = sc._db_query("SELECT * FROM skills WHERE id = ?", (sid,))[0]
        self.assertEqual(fila["usos"], 10)
        self.assertEqual(fila["exitos"], 10)
        # Media ponderada: (100*9 + 200) / 10 = 110.
        self.assertAlmostEqual(fila["tokens_promedio"], 110.0, places=5)
        self.assertAlmostEqual(fila["tiempo_promedio_ms"], 120.0, places=5)
        self.assertTrue(fila["ultimo_uso"])
        self.assertGreaterEqual(nueva_conf, 0.6)

    def test_registro_fallo_penaliza(self):
        sid = self._sembrar(usos=4, exitos=4, fallos=0, tokens=100,
                            tiempo_ms=100)
        conf_antes = sc._db_query(
            "SELECT confiabilidad FROM skills WHERE id = ?",
            (sid,))[0]["confiabilidad"]
        sc._skill_registrar_fallo(sid, tokens=100, tiempo_ms=100)
        fila = sc._db_query("SELECT * FROM skills WHERE id = ?", (sid,))[0]
        self.assertEqual(fila["usos"], 5)
        self.assertEqual(fila["fallos"], 1)
        self.assertLess(fila["confiabilidad"], conf_antes)


class TestEvaluacion(BaseCurador500):
    def test_skills_saludables_no_son_candidatos(self):
        self._sembrar(nombre="sano", usos=20, exitos=19, fallos=1, tokens=300)
        self.assertEqual(cp.evaluar_skills(), [])

    def test_candidatos_por_fallos_y_por_tokens(self):
        self._sembrar(nombre="muchos_fallos", usos=10, fallos=5, tokens=100)
        self._sembrar(nombre="mucho_token", usos=10, fallos=0, tokens=4000)
        nombres = {c["nombre"] for c in cp.evaluar_skills()}
        self.assertEqual(nombres, {"muchos_fallos", "mucho_token"})

    def test_con_pocos_usos_descarta(self):
        self._sembrar(nombre="apenas", usos=2, fallos=2, tokens=9999)
        self.assertEqual(cp.evaluar_skills(), [])


class TestRefactorizacion(BaseCurador500):
    def test_skill_inexistente(self):
        r = cp.refactorizar_skill(99999)
        self.assertFalse(r["ok"])
        self.assertIn("no encontrado", r["motivo"])

    def test_prompt_identico_se_rechaza(self):
        sid = self._sembrar(usos=10, fallos=5)
        consulta = sc._db_query("SELECT consulta FROM skills WHERE id = ?",
                                (sid,))[0]["consulta"]
        with mock.patch.object(cp, "_llm_reescribir", return_value=consulta):
            r = cp.refactorizar_skill(sid)
        self.assertFalse(r["ok"])                 # sin cambios → no guarda
        self.assertEqual(sc._db_query(
            "SELECT version FROM skills WHERE id = ?", (sid,))[0]["version"], 1)

    def test_error_del_llm_registra_motivo(self):
        sid = self._sembrar(usos=10, fallos=5)
        with mock.patch.object(cp, "_llm_reescribir",
                               side_effect=RuntimeError("API caída")):
            r = cp.refactorizar_skill(sid)
        self.assertFalse(r["ok"])
        self.assertIn("error del LLM", r["motivo"])
        historial = sc._db_query(
            "SELECT motivo FROM historial_skills WHERE skill_id = ?", (sid,))
        self.assertTrue(any("error:" in h["motivo"] for h in historial))

    def test_sandbox_fallido_no_guarda_nada(self):
        sid = self._sembrar(usos=10, fallos=5)
        with mock.patch.object(cp, "_llm_reescribir",
                               return_value="Tarea simple"), \
             mock.patch.object(cp, "_probar_prompt", return_value=False):
            r = cp.refactorizar_skill(sid)
        self.assertFalse(r["ok"])
        self.assertIn("sandbox", r["motivo"])
        fila = sc._db_query("SELECT version, consulta FROM skills WHERE id = ?",
                            (sid,))[0]
        self.assertEqual(fila["version"], 1)
        self.assertNotIn("Tarea simple", fila["consulta"])

    def test_sin_mejora_de_tokens_no_adquiere(self):
        sid = self._sembrar(usos=10, fallos=5)
        largo = "x" * 9999
        with mock.patch.object(cp, "_llm_reescribir", return_value=largo), \
             mock.patch.object(cp, "_probar_prompt", return_value=True):
            r = cp.refactorizar_skill(sid)
        self.assertTrue(r["ok"])
        self.assertFalse(r["mejorado"])

    def test_refactorizacion_exitosa(self):
        sid = self._sembrar(consulta="Por favor, podrias hacer la tarea "
                            "grande completando todos los detallitos",
                            usos=10, fallos=5)
        nuevo = "Tarea concisa"
        with mock.patch.object(cp, "_llm_reescribir", return_value=nuevo), \
             mock.patch.object(cp, "_probar_prompt", return_value=True), \
             mock.patch.object(cp, "notificar_mejora") as notif:
            r = cp.refactorizar_skill(sid)
        self.assertTrue(r["ok"])
        self.assertTrue(r["mejorado"])
        self.assertEqual(r["version"], 2)
        self.assertGreater(r["ahorro_pct"], 0.0)
        fila = sc._db_query("SELECT * FROM skills WHERE id = ?", (sid,))[0]
        self.assertEqual(fila["consulta"], nuevo)
        self.assertEqual(fila["version"], 2)
        previa = sc._db_query(
            "SELECT * FROM historial_skills WHERE skill_id = ? "
            "AND motivo = 'refactorizado'", (sid,))
        self.assertEqual(len(previa), 1)
        self.assertIn("detallitos", previa[0]["prompt"])
        notif.assert_called_once()


class TestMotorYPersistencia(BaseCurador500):
    def test_desactivar_y_activar(self):
        self.assertTrue(cp.esta_activo())         # por defecto activo
        cp.desactivar_curador()
        self.assertFalse(cp.esta_activo())
        cp.activar_curador()
        self.assertTrue(cp.esta_activo())

    def test_ejecutar_respeta_el_interruptor(self):
        cp.desactivar_curador()
        self.assertIsNone(cp.ejecutar_curador(auto=True))

    def test_ejecutar_refactoriza_candidatos_y_marca_pasada(self):
        self._sembrar(nombre="candidato", usos=10, fallos=5)
        with mock.patch.object(cp, "_llm_reescribir",
                               return_value="Prompt corto"), \
             mock.patch.object(cp, "_probar_prompt", return_value=True), \
             mock.patch.object(cp, "notificar_mejora"):
            resultados = cp.ejecutar_curador(auto=True)
        self.assertEqual(len(resultados), 1)
        self.assertTrue(resultados[0]["mejorado"])
        self.assertTrue(sc._kv_obtener(cp.CLAVE_ULTIMA_PASADA))

    def test_estado_curador_devuelve_resumen(self):
        self._sembrar(nombre="x", usos=10, fallos=5)
        resumen = cp.estado_curador()
        for clave in ("activo", "intervalo_horas", "total_skills", "activos",
                      "candidatos", "ultima_pasada", "mejoras_totales",
                      "reinado_lista"):
            self.assertIn(clave, resumen)
        self.assertEqual(resumen["total_skills"], 1)
        self.assertEqual(resumen["candidatos"], 1)


class TestNotificaciones(BaseCurador500):
    def test_telegram_envia_cuando_esta_configurado(self):
        fake_resp = mock.Mock(status_code=200)
        ent = {"TELEGRAM_BOT_TOKEN": "tk", "TELEGRAM_CHAT_ID": "42"}
        with mock.patch.dict(os.environ, ent), \
                mock.patch("httpx.post", return_value=fake_resp) as post:
            ok = cp.notificar_mejora("refactorizar_api", 2, 30.0)
        self.assertTrue(ok)
        url = post.call_args[0][0]
        self.assertIn("/sendMessage", url)
        self.assertIn("Skill 'refactorizar_api' mejorado (v2)",
                      post.call_args[1]["json"]["text"])

    def test_discord_webhook_alternativo(self):
        fake_resp = mock.Mock(status_code=204)
        ent = {"DISCORD_WEBHOOK_URL": "https://discord/hook"}
        with mock.patch.dict(os.environ, ent), \
                mock.patch("httpx.post", return_value=fake_resp) as post:
            ok = cp.notificar_mejora("s", 3, 12.0)
        self.assertTrue(ok)
        self.assertEqual(post.call_args[0][0], "https://discord/hook")

    def test_sin_credenciales_es_silencioso(self):
        with mock.patch.dict(os.environ), \
                mock.patch("httpx.post") as post:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            os.environ.pop("DISCORD_WEBHOOK_URL", None)
            ok = cp.notificar_mejora("s", 2, 10.0)
        self.assertFalse(ok)
        post.assert_not_called()

    def test_fallo_de_red_no_propaga(self):
        ent = {"TELEGRAM_BOT_TOKEN": "tk", "TELEGRAM_CHAT_ID": "42"}
        with mock.patch.dict(os.environ, ent), \
                mock.patch("httpx.post", side_effect=OSError("red")):
            self.assertFalse(cp.notificar_mejora("s", 2, 10.0))


class TestCLI(BaseCurador500):
    def test_comando_estado(self):
        self._sembrar(nombre="pesado", usos=10, fallos=5)
        codigo, salida = self._salida(sc._ejecutar_comando_curador, ["estado"])
        self.assertEqual(codigo, 0)
        self.assertIn("pesado", salida)

    def test_comando_desactivar_y_activar(self):
        codigo, _ = self._salida(sc._ejecutar_comando_curador, ["desactivar"])
        self.assertEqual(codigo, 0)
        self.assertFalse(cp.esta_activo())
        codigo, _ = self._salida(sc._ejecutar_comando_curador, ["activar"])
        self.assertEqual(codigo, 0)
        self.assertTrue(cp.esta_activo())

    def test_comando_ejecutar_avisa_si_esta_desactivado(self):
        cp.desactivar_curador()
        codigo, salida = self._salida(sc._ejecutar_comando_curador,
                                      ["ejecutar"])
        self.assertEqual(codigo, 0)
        self.assertIn("desactivado", salida.lower())

    def test_ayuda_del_subcomando(self):
        codigo, salida = self._salida(sc._ejecutar_comando_curador, ["--help"])
        self.assertEqual(codigo, 0)
        self.assertIn("estado", salida)


class TestDaemonYConfig(BaseCurador500):
    def test_intervalo_horas_desde_entorno(self):
        with mock.patch.dict(os.environ, {"CURADOR_INTERVALO_HORAS": "2"}):
            self.assertEqual(cp.intervalo_horas(), 2)
        with mock.patch.dict(os.environ, {"CURADOR_INTERVALO_HORAS": "zzz"}):
            self.assertEqual(cp.intervalo_horas(), 6)

    def test_daemon_arranca_para_y_no_bloquea(self):
        hilo = cp.iniciar_daemon_fondo()
        try:
            self.assertIsNotNone(hilo)
            self.assertTrue(hilo.daemon)
            self.assertTrue(hilo.is_alive())
        finally:
            cp.detener_daemon()
            hilo.join(timeout=5)
        self.assertFalse(hilo.is_alive())

    def test_daemon_no_arranca_si_esta_desactivado(self):
        cp.desactivar_curador()
        self.assertIsNone(cp.iniciar_daemon_fondo())


if __name__ == "__main__":
    unittest.main()