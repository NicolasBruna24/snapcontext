#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la API pÃºblica (v3.6.0) usando ``fastapi.testclient``.

Cubre autenticaciÃ³n (X-API-Key), health pÃºblico, tareas asÃ­ncronas
(query/plan + /tasks/{id}), chat, skills y el control del daemon.
"""

import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import snapcontext as sc
from web.app import API_PREFIJO, crear_app

CLAVE = "clave-de-test-360"
HEADERS = {"X-API-Key": CLAVE}


class BaseAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(crear_app(api_token=CLAVE))

    def tearDown(self):
        self.client.close()


class TestAutenticacion(BaseAPI):
    def test_health_es_publico(self):
        r = self.client.get(f"{API_PREFIJO}/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["estado"], "ok")
        self.assertEqual(r.json()["servicio"], "snapcontext")

    def test_endpoints_protegidos_sin_clave(self):
        for metodo, ruta in (("get", f"{API_PREFIJO}/skills"),
                             ("post", f"{API_PREFIJO}/query"),
                             ("post", f"{API_PREFIJO}/plan"),
                             ("post", f"{API_PREFIJO}/chat"),
                             ("post", f"{API_PREFIJO}/daemon"),
                             ("get", f"{API_PREFIJO}/tasks/no-existe")):
            r = getattr(self.client, metodo)(ruta)
            self.assertEqual(r.status_code, 401, ruta)

    def test_clave_invalida_rechazada(self):
        r = self.client.get(f"{API_PREFIJO}/skills",
                            headers={"X-API-Key": "incorrecta"})
        self.assertEqual(r.status_code, 401)

    def test_clave_por_query_param(self):
        with mock.patch.object(sc, "_skill_listar", return_value=[]):
            r = self.client.get(f"{API_PREFIJO}/skills?api_key={CLAVE}")
        self.assertEqual(r.status_code, 200)

    def test_documentacion_openapi_publica(self):
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/redoc").status_code, 200)


class TestTareasAsincronas(BaseAPI):
    def test_query_sin_consulta_es_400(self):
        r = self.client.post(f"{API_PREFIJO}/query", json={},
                             headers=HEADERS)
        self.assertEqual(r.status_code, 400)

    def test_query_responde_202_y_completa(self):
        with mock.patch.object(sc, "flujo_principal", return_value=0) as flu:
            r = self.client.post(f"{API_PREFIJO}/query",
                                 json={"consulta": "revisar login"},
                                 headers=HEADERS)
            self.assertEqual(r.status_code, 202)
            datos = r.json()
            self.assertIn("task_id", datos)
            self.assertIn("/api/v1/tasks/", datos["url"])
            estado = self._esperar_estado(datos["task_id"])
            self.assertEqual(estado["estado"], "completada")
            self.assertEqual(estado["resultado"]["codigo_salida"], 0)
        flu.assert_called_once()

    def test_plan_fallido_marca_tarea_como_fallida(self):
        with mock.patch.object(sc, "_ejecutar_planificador", return_value=1):
            r = self.client.post(f"{API_PREFIJO}/plan",
                                 json={"consulta": "planear algo"},
                                 headers=HEADERS)
            self.assertEqual(r.status_code, 202)
            estado = self._esperar_estado(r.json()["task_id"])
            self.assertEqual(estado["estado"], "fallida")

    def test_error_en_tarea_se_reporta(self):
        with mock.patch.object(sc, "flujo_principal",
                               side_effect=RuntimeError("boom")):
            r = self.client.post(f"{API_PREFIJO}/query",
                                 json={"consulta": "x"}, headers=HEADERS)
            estado = self._esperar_estado(r.json()["task_id"])
            self.assertEqual(estado["estado"], "error")
            self.assertIn("boom", estado["error"])

    def test_task_desconocida_es_404(self):
        r = self.client.get(f"{API_PREFIJO}/tasks/xyz", headers=HEADERS)
        self.assertEqual(r.status_code, 404)

    def _esperar_estado(self, task_id: str, timeout: float = 5.0) -> dict:
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            r = self.client.get(f"{API_PREFIJO}/tasks/{task_id}",
                                headers=HEADERS)
            if r.status_code == 200 and r.json()["estado"] not in (
                    "pendiente", "ejecutando"):
                return r.json()
            time.sleep(0.02)
        self.fail("La tarea no terminÃ³ a tiempo")


class TestChatYSkills(BaseAPI):
    def test_chat_sin_mensaje_es_400(self):
        r = self.client.post(f"{API_PREFIJO}/chat", json={}, headers=HEADERS)
        self.assertEqual(r.status_code, 400)

    def test_chat_devuelve_respuesta_del_proveedor(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value="Â¡Hola!") as enviar, \
                mock.patch.object(sc, "cargar_configuracion",
                                  return_value={"provider": "groq"}):
            r = self.client.post(
                f"{API_PREFIJO}/chat",
                json={"mensaje": "hola",
                      "historial": [{"role": "user", "content": "previo"}]},
                headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["respuesta"], "Â¡Hola!")
        self.assertEqual(r.json()["proveedor"], "groq")
        mensajes = enviar.call_args[0][2]
        # historial truncado a 20 + mensaje actual
        self.assertEqual(mensajes[-1]["content"], "hola")

    def test_chat_error_de_proveedor_es_502(self):
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=RuntimeError("sin clave")), \
                mock.patch.object(sc, "cargar_configuracion",
                                  return_value={}):
            r = self.client.post(f"{API_PREFIJO}/chat",
                                 json={"mensaje": "x"}, headers=HEADERS)
        self.assertEqual(r.status_code, 502)

    def test_skills_lista(self):
        with mock.patch.object(sc, "_skill_listar",
                               return_value=[{"nombre": "modo-oscuro"}]) as lst:
            r = self.client.get(f"{API_PREFIJO}/skills?archivados=true",
                                headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        datos = r.json()
        self.assertEqual(datos["total"], 1)
        self.assertEqual(datos["skills"][0]["nombre"], "modo-oscuro")
        lst.assert_called_once_with(incluir_archivados=True)


class TestDaemonYFlags(BaseAPI):
    def test_daemon_estado_por_defecto_inactivo(self):
        from web import app as web_app
        anterior = web_app._DAEMON_HILO
        web_app._DAEMON_HILO = None
        try:
            r = self.client.post(f"{API_PREFIJO}/daemon",
                                 json={"accion": "estado"}, headers=HEADERS)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["activo"])
        finally:
            web_app._DAEMON_HILO = anterior

    def test_daemon_accion_invalida_es_400(self):
        r = self.client.post(f"{API_PREFIJO}/daemon",
                             json={"accion": "volar"}, headers=HEADERS)
        self.assertEqual(r.status_code, 400)

    def test_flags_api_en_el_parser(self):
        args = sc.crear_parser().parse_args(["--api"])
        self.assertTrue(args.api)
        args = sc.crear_parser().parse_args(["--api-server"])
        self.assertTrue(args.api)
        defecto = sc.crear_parser().parse_args(["--api"])
        self.assertEqual(defecto.api_puerto, 8001)
        self.assertEqual(defecto.api_host, "127.0.0.1")
        self.assertIsNone(defecto.api_token)

    def test_generar_clave_api_devuelve_y_guarda(self):
        with mock.patch.object(sc, "_actualizar_clave_configuracion",
                               return_value=True) as guarda:
            clave = sc._generar_clave_api()
        self.assertGreaterEqual(len(clave), 32)
        guarda.assert_called_once_with("api_key", clave)

    def test_compatibilidad_web_crear_app_sin_argumentos(self):
        cliente = TestClient(crear_app())
        try:
            self.assertEqual(cliente.get("/health").status_code, 200)
        finally:
            cliente.close()


if __name__ == "__main__":
    unittest.main()
