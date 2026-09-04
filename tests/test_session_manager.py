#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para session_manager (v6.28.0) — Agente Fantasma."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSession(unittest.TestCase):
    """Tests para la clase Session."""

    def test_creacion_session(self):
        from session_manager import Session
        s = Session(consulta_inicial="test")
        self.assertIsNotNone(s.id)
        self.assertEqual(s.consulta_inicial, "test")
        self.assertEqual(s.estado["fase"], "inactivo")

    def test_añadir_mensaje(self):
        from session_manager import Session
        s = Session()
        s.añadir_mensaje("user", "hola")
        self.assertEqual(len(s.historial), 1)
        self.assertEqual(s.historial[0]["rol"], "user")

    def test_actualizar_estado(self):
        from session_manager import Session
        s = Session()
        s.actualizar_estado({"fase": "pensando", "detalle": "analizando"})
        self.assertEqual(s.estado["fase"], "pensando")

    def test_guardar_plan(self):
        from session_manager import Session
        s = Session()
        plan = [{"descripcion": "Paso 1"}, {"descripcion": "Paso 2"}]
        s.guardar_plan(plan)
        self.assertEqual(len(s.plan), 2)

    def test_añadir_archivo_modificado(self):
        from session_manager import Session
        s = Session()
        s.añadir_archivo_modificado("app.py")
        s.añadir_archivo_modificado("app.py")  # No duplicar
        self.assertEqual(len(s.archivos_modificados), 1)

    def test_to_dict(self):
        from session_manager import Session
        s = Session(consulta_inicial="test")
        d = s.to_dict()
        self.assertIn("id", d)
        self.assertIn("historial", d)
        self.assertEqual(d["consulta_inicial"], "test")

    def test_expirado(self):
        from session_manager import Session
        s = Session()
        self.assertFalse(s.expirado(timeout=3600))
        # Simular inactividad
        s.ultima_actividad = 0
        self.assertTrue(s.expirado(timeout=1))


class TestSessionManager(unittest.TestCase):
    """Tests para SessionManager."""

    def setUp(self):
        from session_manager import SessionManager
        self.mgr = SessionManager(timeout=3600)

    def test_crear_sesion(self):
        id = self.mgr.crear_sesion("consulta test")
        self.assertIsNotNone(id)
        self.assertEqual(len(id), 8)

    def test_obtener_sesion(self):
        id = self.mgr.crear_sesion()
        s = self.mgr.obtener_sesion(id)
        self.assertIsNotNone(s)
        self.assertEqual(s.id, id)

    def test_obtener_sesion_inexistente(self):
        s = self.mgr.obtener_sesion("no-existe")
        self.assertIsNone(s)

    def test_eliminar_sesion(self):
        id = self.mgr.crear_sesion()
        self.assertTrue(self.mgr.eliminar_sesion(id))
        self.assertFalse(self.mgr.eliminar_sesion(id))

    def test_listar_sesiones(self):
        self.mgr.crear_sesion("test1")
        self.mgr.crear_sesion("test2")
        lista = self.mgr.listar_sesiones()
        self.assertEqual(len(lista), 2)

    def test_persistir_cargar_sesion(self):
        id = self.mgr.crear_sesion("persist test")
        self.mgr.persistir_sesion(id)
        # Crear nuevo manager y cargar
        from session_manager import SessionManager
        mgr2 = SessionManager(timeout=3600)
        s = mgr2.cargar_sesion(id)
        self.assertIsNotNone(s)
        self.assertEqual(s.consulta_inicial, "persist test")

    def test_ejecutar_comando(self):
        id = self.mgr.crear_sesion()
        resultado = self.mgr.ejecutar_comando(id, "test comando")
        self.assertTrue(resultado["ok"])

    def test_ejecutar_comando_sesion_inexistente(self):
        resultado = self.mgr.ejecutar_comando("no-existe", "test")
        self.assertFalse(resultado["ok"])

    def test_limpiar_expiradas(self):
        id = self.mgr.crear_sesion()
        # Simular expiracion
        s = self.mgr.obtener_sesion(id)
        s.ultima_actividad = 0
        count = self.mgr.limpiar_expiradas()
        self.assertGreaterEqual(count, 1)


class TestSessionFlags(unittest.TestCase):
    """Tests para flags CLI de sesiones."""

    def test_flag_new_session_existe(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--new-session", "test"])
        self.assertTrue(hasattr(args, "new_session"))

    def test_flag_attach_existe(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--attach", "abc12345", "test"])
        self.assertEqual(args.attach, "abc12345")

    def test_flag_session_timeout(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--session-timeout", "1800", "test"])
        self.assertEqual(args.session_timeout, 1800)

    def test_flag_list_sessions(self):
        import snapcontext as sc
        parser = sc.crear_parser()
        args = parser.parse_args(["--list-sessions"])
        self.assertTrue(args.list_sessions)


if __name__ == "__main__":
    unittest.main()
