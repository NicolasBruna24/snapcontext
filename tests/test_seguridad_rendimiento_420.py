#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del asesor mejorado: seguridad 🔒 y rendimiento ⚡ (v4.2.0)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import snapcontext as sc
from agentes import AgenteAsesor


class BaseSeg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc420_")
        self.raiz = Path(self.tmp).resolve()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _archivo(self, nombre, contenido):
        ruta = self.raiz / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(contenido, encoding="utf-8")


class TestSeguridad(BaseSeg):
    CODIGO_INSEGURO = (
        "import os\n"
        "API_KEY = \"sk-1234567890abcd\"\n"
        "os.system(\"ping \" + ip)\n"
        "eval(entrada)\n"
        "query = \"SELECT * FROM users WHERE id = \" + user_id\n"
        "f = open(\"../\" + filename)\n"
    )

    def test_detecta_os_system(self):
        hallazgos = sc._detectar_vulnerabilidades('os.system("ping " + ip)')
        self.assertTrue(any("os.system" in h["mensaje"] for h in hallazgos))

    def test_detecta_eval_y_exec(self):
        for codigo in ("eval(x)", "exec(codigo)"):
            hallazgos = sc._detectar_vulnerabilidades(codigo)
            self.assertTrue(any("eval" in h["mensaje"] or
                                "exec" in h["mensaje"]
                                for h in hallazgos), codigo)

    def test_detecta_inyeccion_sql(self):
        hallazgos = sc._detectar_vulnerabilidades(
            'q = "SELECT * FROM u WHERE id = " + uid')
        self.assertTrue(any("SQL" in h["mensaje"] for h in hallazgos))

    def test_detecta_hardcoded_secret(self):
        hallazgos = sc._detectar_vulnerabilidades(
            'API_KEY = "sk-1234567890abcd"')
        self.assertTrue(any("secret" in h["mensaje"].lower()
                            for h in hallazgos))
        # Clave corta o sin patrón no se marca.
        self.assertFalse(sc._detectar_vulnerabilidades(
            'API_KEY = ""'))

    def test_detecta_path_traversal(self):
        hallazgos = sc._detectar_vulnerabilidades(
            'f = open("../" + filename)')
        self.assertTrue(any("traversal" in h["mensaje"].lower()
                            for h in hallazgos))

    def test_detecta_xss(self):
        hallazgos = sc._detectar_vulnerabilidades(
            'elemento.innerHTML = entrada')
        self.assertTrue(any("XSS" in h["mensaje"] for h in hallazgos))

    def test_codigo_seguro_sin_hallazgos(self):
        seguro = ("import subprocess\n"
                  "subprocess.run(['ping', ip])\n"
                  "dato = input()\n")
        self.assertEqual(sc._detectar_vulnerabilidades(seguro), [])

    def test_comentarios_ignorados(self):
        self.assertEqual(sc._detectar_vulnerabilidades("# eval(x)\n"), [])

    def test_prioridades_altas(self):
        hallazgos = sc._detectar_vulnerabilidades(self.CODIGO_INSEGURO)
        self.assertTrue(all(h["prioridad"] == "alta" for h in hallazgos))


class TestRendimiento(BaseSeg):
    def test_bucles_anidados_on2(self):
        hallazgos = sc._detectar_rendimiento(
            "for a in lista:\n    for b in lista:\n        pass\n")
        self.assertTrue(any("O(n²)" in h["mensaje"] for h in hallazgos))

    def test_range_len(self):
        hallazgos = sc._detectar_rendimiento(
            "for i in range(len(lista)):\n    print(i)\n")
        self.assertTrue(any("range(len" in h["mensaje"] for h in hallazgos))

    def test_concatenacion_en_bucle(self):
        hallazgos = sc._detectar_rendimiento(
            "texto = \"\"\nfor x in lista:\n    texto += \"sep\"\n")
        self.assertTrue(any("+=" in h["mensaje"] or "+=' in" in h["mensaje"]
                            or "'+='" in h["mensaje"]
                            for h in hallazgos))

    def test_n1_orm(self):
        hallazgos = sc._detectar_rendimiento(
            "for u in usuarios:\n    p = User.objects.get(id=u)\n")
        self.assertTrue(any("N+1" in h["mensaje"] for h in hallazgos))

    def test_read_completo(self):
        hallazgos = sc._detectar_rendimiento(
            "datos = open(\"grande.csv\").read()")
        self.assertTrue(any("memoria" in h["mensaje"].lower()
                            for h in hallazgos))


class TestIntegracionAsesor(BaseSeg):
    def test_basico_sin_seguridad(self):
        self._archivo("m.py", "eval(x)\n")
        tipos = {s["tipo"] for s in sc._asesor_analizar(str(self.raiz))}
        self.assertNotIn("vulnerabilidad", tipos)

    def test_profundo_incluye_vulnerabilidades(self):
        self._archivo("m.py", "eval(x)\n")
        sugerencias = sc._asesor_analizar(str(self.raiz), profundo=True)
        vulns = [s for s in sugerencias if s["tipo"] == "vulnerabilidad"]
        self.assertTrue(vulns)
        self.assertIn("🔒", vulns[0]["descripcion"])
        self.assertTrue(all("solucion" in v and "linea" in v
                            for v in vulns))

    def test_profundo_incluye_rendimiento(self):
        self._archivo("m.py",
                      "for a in l:\n    for b in l:\n        pass\n")
        sugerencias = sc._asesor_analizar(str(self.raiz), profundo=True)
        rends = [s for s in sugerencias if s["tipo"] == "rendimiento"]
        self.assertTrue(rends)
        self.assertIn("⚡", rends[0]["descripcion"])

    def test_wrappers_independientes(self):
        self._archivo("m.py", "eval(x)\nfor i in range(len(l)):\n    pass\n")
        self.assertTrue(sc._analizar_seguridad(str(self.raiz)))
        self.assertTrue(sc._analizar_rendimiento(str(self.raiz)))

    def test_agente_asesor_delega(self):
        agente = AgenteAsesor()
        self._archivo("m.py", "eval(x)\n")
        self.assertTrue(agente.analizar_seguridad(str(self.raiz)))
        self.assertEqual(agente.analizar_rendimiento(str(self.raiz)), [])


class TestFlagsYPlan(BaseSeg):
    def test_flag_asesor_profundo_existe(self):
        args = sc.crear_parser().parse_args(["--asesor-profundo"])
        self.assertTrue(args.asesor_profundo)
        defecto = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(defecto.asesor_profundo)

    def test_acciones_validas_en_plan(self):
        pasos = sc._normalizar_pasos({"pasos": [
            {"descripcion": "auditar", "accion": "seguridad"},
            {"descripcion": "optimizar", "accion": "rendimiento"}]})
        self.assertEqual({p["accion"] for p in pasos},
                         {"seguridad", "rendimiento"})

    def test_paso_seguridad_en_auto_solo_informa(self):
        args = mock.MagicMock(auto=True)
        hallazgos = [{"descripcion": "eval inseguro", "archivo": "m.py",
                      "linea": 2, "solucion": "usa literal_eval",
                      "prioridad": "alta"}]
        with mock.patch.object(sc, "_asesor_analizar_por_tipo",
                               return_value=hallazgos) as analiza, \
                mock.patch.object(sc, "_confirmar_accion") as confirma:
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "seguridad", "descripcion": "auditar"}, args,
                str(self.raiz))
        self.assertTrue(ok)
        self.assertIn("1 hallazgo", detalle)
        analiza.assert_called_once_with(
            str(self.raiz), ("vulnerabilidad",))
        confirma.assert_not_called()

    def test_paso_rendimiento_sin_hallazgos_es_exito(self):
        args = mock.MagicMock(auto=False, confirmar=False)
        with mock.patch.object(sc, "_asesor_analizar_por_tipo",
                               return_value=[]), \
                mock.patch.object(sc, "_confirmar_accion",
                                  return_value=True):
            ok, detalle = sc._ejecutar_paso_plan(
                {"accion": "rendimiento", "descripcion": "optimizar"},
                args, str(self.raiz))
        self.assertTrue(ok)
        self.assertEqual(detalle, "sin hallazgos")

    def test_chat_ayuda_incluye_comandos(self):
        self.assertIn("/seguridad", sc.AYUDA_CHAT)
        self.assertIn("/rendimiento", sc.AYUDA_CHAT)


if __name__ == "__main__":
    unittest.main()
