#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del Asesor de código proactivo (v3.5.0).

Cubre los detectores estáticos, ``_asesor_analizar`` extremo a extremo sobre
un directorio temporal, los flags CLI, la integración con el planificador
(acción ``asesor``), la aplicación automática segura y el comando de chat.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapcontext as sc
from agentes import AgenteAsesor


class BaseAsesor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc350_")
        self.raiz = Path(self.tmp).resolve()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _archivo(self, nombre, contenido):
        ruta = self.raiz / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(contenido, encoding="utf-8")
        return ruta


class TestDetectores(BaseAsesor):
    def test_funcion_larga_detectada(self):
        cuerpo = "\n".join("    x = %d" % i for i in range(25))
        hallazgos = sc._detectar_funciones_largas(
            "def larga():\n" + cuerpo + "\n", 20)
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["nombre"], "larga")
        self.assertEqual(hallazgos[0]["lineas"], 26)

    def test_funcion_corta_no_detectada(self):
        self.assertEqual(
            sc._detectar_funciones_largas("def f():\n    return 1\n", 20), [])

    def test_sintaxis_invalida_ignorada(self):
        self.assertEqual(sc._detectar_funciones_largas("def (\n", 5), [])

    def test_clase_grande_detectada(self):
        metodos = "".join(f"    def m{i}(self):\n        pass\n"
                          for i in range(12))
        hallazgos = sc._detectar_clases_grandes(
            "class Grande:\n" + metodos, 10)
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["metodos"], 12)

    def test_nombres_cortos_con_sugerencia(self):
        hallazgos = sc._detectar_nombres_cortos("d = {}\n")
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["sugerido"], "datos")

    def test_indices_de_bucle_excluidos(self):
        self.assertEqual(
            sc._detectar_nombres_cortos("for i in range(3):\n    print(i)\n"),
            [])


class TestPatronesYDuplicados(BaseAsesor):
    def test_patrones_obsoletos(self):
        codigo = ("try:\n    pass\nexcept:\n    pass\n"
                  "if x == None:\n    pass\n")
        hallazgos = sc._detectar_patrones_obsoletos(codigo)
        mensajes = [h["mensaje"] for h in hallazgos]
        self.assertTrue(any("except" in m for m in mensajes))
        self.assertTrue(any("is None" in m for m in mensajes))

    def test_comentarios_ignorados(self):
        self.assertEqual(
            sc._detectar_patrones_obsoletos("# if x == None\n"), [])

    def test_duplicados_entre_archivos(self):
        bloque = "".join(f"linea_{i} = {i}\n" for i in range(8))
        contenidos = {"a.py": bloque, "b.py": bloque,
                      "c.py": "distinto = 1\n"}
        hallazgos = sc._detectar_duplicados(contenidos, 6)
        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["archivo"], "b.py")
        self.assertIn("a.py", hallazgos[0]["original"])

    def test_sin_duplicados(self):
        self.assertEqual(
            sc._detectar_duplicados({"a.py": "x = 1\n"}, 6), [])


class TestAsesorAnalizar(BaseAsesor):
    def test_analisis_end_to_end(self):
        bloque = "".join(f"linea_{i} = {i}\n" for i in range(8))
        self._archivo("app.py", "d = 1\ntry:\n    pass\nexcept:\n    pass\n")
        self._archivo("otro.py", bloque)
        self._archivo("copia.js", "// js\n" + "var x = 1;\n" * 10)
        sugerencias = sc._asesor_analizar(str(self.raiz))
        tipos = {s["tipo"] for s in sugerencias}
        self.assertIn("nombre_poco_descriptivo", tipos)
        self.assertIn("patron_obsoleto", tipos)
        self.assertIn("codigo_duplicado", tipos)
        for s in sugerencias:
            self.assertIn(s["prioridad"], ("alta", "media", "baja"))
            self.assertIn("solucion", s)
            self.assertIn("descripcion", s)

    def test_directorio_vacio_sin_sugerencias(self):
        self.assertEqual(sc._asesor_analizar(str(self.raiz)), [])

    def test_umbral_personalizado(self):
        self._archivo("m.py", "def f():\n" + "    x = 1\n" * 12)   # 13 líneas
        con_umbral_5 = [s for s in sc._asesor_analizar(
            str(self.raiz), umbral_funcion=5)
            if s["tipo"] == "funcion_larga"]
        self.assertEqual(len(con_umbral_5), 1)          # 13 > 5 → detectada
        con_umbral_20 = [s for s in sc._asesor_analizar(
            str(self.raiz), umbral_funcion=20)
            if s["tipo"] == "funcion_larga"]
        self.assertEqual(con_umbral_20, [])             # 13 < 20 → ignorada


class TestFlags(BaseAsesor):
    def test_asesor_por_defecto_apagado(self):
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(args.asesor)
        self.assertFalse(args.asesor_auto)
        self.assertIsNone(args.asesor_umbral)

    def test_flag_asesor_y_alias_sugerir(self):
        args = sc.crear_parser().parse_args(["--asesor"])
        self.assertTrue(args.asesor)
        args2 = sc.crear_parser().parse_args(["--sugerir"])
        self.assertTrue(args2.asesor)

    def test_flag_asesor_auto_y_umbral(self):
        args = sc.crear_parser().parse_args(
            ["--asesor-auto", "--asesor-umbral", "30"])
        self.assertTrue(args.asesor_auto)
        self.assertEqual(args.asesor_umbral, 30)


class TestPlanificador(BaseAsesor):
    def test_accion_asesor_valida_en_pasos(self):
        pasos = sc._normalizar_pasos({"pasos": [
            {"descripcion": "analizar deuda", "accion": "asesor"}]})
        self.assertEqual(len(pasos), 1)
        self.assertEqual(pasos[0]["accion"], "asesor")

    def test_paso_asesor_presenta_sugerencias(self):
        args = mock.MagicMock(auto=False, confirmar=True)
        sugerencias = [{"descripcion": "función larga", "archivo": "m.py",
                        "linea": 3, "solucion": "extráela",
                        "prioridad": "media"}]
        with mock.patch.object(sc, "_asesor_analizar",
                               return_value=sugerencias) as analiza, \
                mock.patch.object(sc, "_confirmar_accion",
                                  return_value=True) as confirma:
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "asesor", "descripcion": "analizar"}, args,
                str(self.raiz))
        self.assertTrue(ok)
        self.assertIn("1 sugerencia", detalle)
        self.assertIn("1 aceptada", detalle)
        # 2 llamadas: la de permisos del paso y la de la sugerencia.
        self.assertEqual(confirma.call_count, 2)
        analiza.assert_called_once_with(str(self.raiz))

    def test_paso_asesor_auto_solo_informa(self):
        args = mock.MagicMock(auto=True, confirmar=False)
        sugerencias = [{"descripcion": "duplicado", "archivo": "m.py",
                        "linea": 3, "solucion": "extrae",
                        "prioridad": "media"}]
        with mock.patch.object(sc, "_asesor_analizar",
                               return_value=sugerencias), \
                mock.patch.object(sc, "_confirmar_accion") as confirma:
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "asesor", "descripcion": "analizar"}, args,
                str(self.raiz))
        self.assertTrue(ok)
        confirma.assert_not_called()

    def test_paso_asesor_sin_sugerencias_es_exito(self):
        args = mock.MagicMock(auto=False)
        with mock.patch.object(sc, "_asesor_analizar", return_value=[]), \
                mock.patch.object(sc, "_confirmar_accion", return_value=True):
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "asesor", "descripcion": "analizar"}, args,
                str(self.raiz))
        self.assertTrue(ok)
        self.assertEqual(detalle, "sin sugerencias")


class TestAplicarAutomaticas(BaseAsesor):
    SUGERENCIA_RENOMBRE = {
        "tipo": "nombre_poco_descriptivo", "auto": True,
        "archivo": "m.py", "linea": 1,
        "descripcion": "El nombre 'd' no es descriptivo.",
        "solucion": "Renómbralo a 'datos'.",
        "prioridad": "baja",
        "operaciones": [{"tipo": "renombrar", "nombre": "d",
                         "nuevo": "datos"}],
    }

    def test_aplica_renombre_validado(self):
        ruta = self._archivo("m.py", "d = {}\nd['a'] = 1\nprint(d)\n")
        aplicadas = sc._asesor_aplicar_automaticas(
            [dict(self.SUGERENCIA_RENOMBRE)], str(self.raiz))
        self.assertEqual(aplicadas, 1)
        contenido = ruta.read_text(encoding="utf-8")
        self.assertIn("datos = {}", contenido)
        self.assertNotIn("d['a']", contenido)

    def test_descarta_cambio_si_validacion_falla(self):
        ruta = self._archivo("m.py", "d = 1\n")
        sugerencia = dict(self.SUGERENCIA_RENOMBRE)
        with mock.patch.object(sc, "_validar_sintaxis",
                               return_value=(False, "SyntaxError")):
            aplicadas = sc._asesor_aplicar_automaticas(
                [sugerencia], str(self.raiz))
        self.assertEqual(aplicadas, 0)
        self.assertEqual(ruta.read_text(encoding="utf-8"), "d = 1\n")

    def test_ignora_sugerencias_no_auto(self):
        self._archivo("m.py", "d = 1\n")
        sugerencia = dict(self.SUGERENCIA_RENOMBRE)
        sugerencia["auto"] = False
        self.assertEqual(
            sc._asesor_aplicar_automaticas([sugerencia], str(self.raiz)), 0)

    def test_sugerencia_sin_operaciones_se_ignora(self):
        self._archivo("m.py", "x = 1\n")
        sugerencia = {"tipo": "funcion_larga", "auto": True,
                      "archivo": "m.py", "linea": 1}
        self.assertEqual(
            sc._asesor_aplicar_automaticas([sugerencia], str(self.raiz)), 0)


class TestAgenteYChat(BaseAsesor):
    def test_agente_asesor_delega_en_snapcontext(self):
        agente = AgenteAsesor()
        self._archivo("m.py", "d = 1\n")
        sugerencias = agente.analizar(str(self.raiz))
        self.assertTrue(any(s["tipo"] == "nombre_poco_descriptivo"
                            for s in sugerencias))
        # mostrar() no debe lanzar excepciones (con y sin sugerencias).
        agente.mostrar(sugerencias)
        agente.mostrar([])

    def test_orquestador_tiene_agente_asesor(self):
        from orquestador import Orquestador
        orch = Orquestador()
        self.assertTrue(hasattr(orch, "agente_asesor"))

    def test_chat_ayuda_incluye_asesor(self):
        self.assertIn("/asesor", sc.AYUDA_CHAT)


if __name__ == "__main__":
    unittest.main()
