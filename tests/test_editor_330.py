#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests del editor propio v3.3.0.

Cubre el refinamiento multi-lenguaje del AST, el manejo de conflictos en
parches (validación previa, resolución incremental) y la integración con
el sistema de skills.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import snapcontext as sc
from agentes import AgenteEditorPropio


class TestClasificacionTareas(unittest.TestCase):
    """_editor_clasificar_tarea: patrones de edición reconocidos."""

    def test_renombrar(self):
        self.assertEqual(sc._editor_clasificar_tarea(
            "renombrar la función calcular_total"), "renombrar")

    def test_import(self):
        self.assertEqual(sc._editor_clasificar_tarea(
            "añadir import de typing"), "añadir_import")

    def test_refactorizar(self):
        self.assertEqual(sc._editor_clasificar_tarea(
            "refactorizar la clase Carrito"), "refactorizar_clase")

    def test_funcion_nueva(self):
        self.assertEqual(sc._editor_clasificar_tarea(
            "crear una función de validación"), "añadir_funcion")

    def test_corregir_error(self):
        self.assertEqual(sc._editor_clasificar_tarea(
            "corrige el bug del checkout"), "corregir_error")

    def test_general(self):
        self.assertEqual(sc._editor_clasificar_tarea(
            "mejorar rendimiento"), "general")
        self.assertEqual(sc._editor_clasificar_tarea(""), "general")


class TestDeteccionLenguaje(unittest.TestCase):
    """Detección multi-lenguaje refinada (extensión + contenido)."""

    def test_extensiones_nuevas(self):
        self.assertEqual(sc._lenguaje_tree_sitter("a.mts"), "typescript")
        self.assertEqual(sc._lenguaje_tree_sitter("a.cxx"), "cpp")
        self.assertEqual(sc._lenguaje_tree_sitter("a.zsh"), "bash")

    def test_por_contenido_shebang(self):
        self.assertEqual(sc._detectar_lenguaje_contenido(
            "#!/usr/bin/env python3\nprint(1)\n"), "python")
        self.assertEqual(sc._detectar_lenguaje_contenido(
            "#!/bin/bash\necho hola\n"), "bash")

    def test_lenguaje_archivo_combina(self):
        self.assertEqual(sc._lenguaje_archivo("script", "#!/bin/bash\n"),
                         "bash")
        self.assertEqual(sc._lenguaje_archivo("main.go"), "go")
        self.assertIsNone(sc._lenguaje_archivo("datos.xyz", "contenido"))

    def test_resumen_ast_python_sin_extension(self):
        resumen = sc._resumen_ast("def hola():\n    pass\n", "sin_nombre")
        self.assertTrue(resumen["ok"])
        self.assertEqual(resumen["motor"], "ast")


class TestRutaYValidacionParche(unittest.TestCase):
    """Extracción de ruta del parche y validación previa."""

    PARCH = ("--- a/modulo.py\n+++ b/modulo.py\n@@ -1 +1 @@\n-viejo\n+nuevo\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc330_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ruta_del_parche(self):
        self.assertEqual(sc._ruta_del_parche(self.PARCH), "modulo.py")
        sin_prefijo = self.PARCH.replace("b/", "").replace("a/", "")
        self.assertEqual(sc._ruta_del_parche(sin_prefijo), "modulo.py")
        self.assertIsNone(sc._ruta_del_parche(""))
        self.assertIsNone(sc._ruta_del_parche("sin encabezados"))

    def test_validacion_sin_referencia_ok(self):
        ok, _ = sc._validar_parche_previo(self.PARCH, self.tmp, None)
        self.assertTrue(ok)

    def test_validacion_coincide(self):
        (Path(self.tmp) / "modulo.py").write_text("viejo\n", encoding="utf-8")
        ok, _ = sc._validar_parche_previo(self.PARCH, self.tmp, "viejo\n")
        self.assertTrue(ok)

    def test_validacion_detecta_cambio_concurrente(self):
        (Path(self.tmp) / "modulo.py").write_text("otra cosa\n",
                                                  encoding="utf-8")
        ok, detalle = sc._validar_parche_previo(self.PARCH, self.tmp,
                                                "viejo\n")
        self.assertFalse(ok)
        self.assertIn("concurrente", detalle)

    def test_validacion_archivo_inexistente(self):
        ok, detalle = sc._validar_parche_previo(self.PARCH, self.tmp,
                                                "viejo\n")
        self.assertFalse(ok)
        self.assertIn("no existe", detalle)


class TestParsearHunks(unittest.TestCase):
    def test_hunks_basicos(self):
        parche = ("--- a/f.py\n+++ b/f.py\n"
                  "@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n ctx2\n")
        hunks = sc._parsear_hunks(parche)
        self.assertEqual(len(hunks), 1)
        inicio, cambios = hunks[0]
        self.assertEqual(inicio, 1)
        self.assertEqual(cambios, [(" ", "ctx"), ("-", "old"),
                                   ("+", "new"), (" ", "ctx2")])

    def test_sin_hunks(self):
        self.assertEqual(sc._parsear_hunks(""), [])
        self.assertEqual(sc._parsear_hunks("--- a/f.py\n+++ b/f.py\n"), [])


class TestAplicacionIncremental(unittest.TestCase):
    """Resolución automática línea a línea sobre archivos reales."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc330inc_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _escribir(self, nombre: str, contenido: str) -> str:
        (Path(self.tmp) / nombre).write_text(contenido, encoding="utf-8")
        return nombre

    def test_aplica_parche_limpio_linea_a_linea(self):
        original = "linea 1\nlinea 2\nlinea 3\nlinea 4\nlinea 5\n"
        nuevo = "linea 1\nlinea DOS\nlinea 3\nlinea CUATRO\nlinea 5\n"
        nombre = self._escribir("f.py", original)
        parche = sc._generar_parche(original, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = (Path(self.tmp) / nombre).read_text(encoding="utf-8")
        self.assertEqual(resultado, nuevo)

    def test_aplica_con_desfase_de_lineas(self):
        # El archivo tiene 2 líneas extra al principio: git apply fallaría,
        # pero la resolución incremental encuentra el bloque por desfase.
        original = "linea 1\nlinea 2\nlinea 3\nlinea 4\nlinea 5\n"
        nuevo = "linea 1\nlinea 2\nlinea 3\nlinea CUATRO\nlinea 5\n"
        nombre = self._escribir("g.py",
                                "extra A\nextra B\n" + original)
        parche = sc._generar_parche(original, nuevo, nombre)
        self.assertTrue(sc._aplicar_hunks_incremental(parche, self.tmp))
        resultado = (Path(self.tmp) / nombre).read_text(encoding="utf-8")
        self.assertEqual(
            resultado,
            "extra A\nextra B\nlinea 1\nlinea 2\nlinea 3\n"
            "linea CUATRO\nlinea 5\n")

    def test_falla_si_nada_coincide(self):
        self._escribir("h.py", "totalmente distinto\notra cosa\n")
        parche = ("--- a/h.py\n+++ b/h.py\n@@ -1 +1 @@\n-viejo\n+nuevo\n")
        self.assertFalse(sc._aplicar_hunks_incremental(parche, self.tmp))


class TestResolucionConConflictos(unittest.TestCase):
    """_aplicar_parche_con_resolucion: flujo completo de resolución."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc330res_")
        self.parche = ("--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-viejo\n+nuevo\n")
        (Path(self.tmp) / "m.py").write_text("viejo\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_git_apply_exitoso_no_requiere_resolucion(self):
        with mock.patch.object(sc, "_aplicar_parche", return_value=True), \
                mock.patch.object(sc, "_aplicar_hunks_incremental") as inc:
            self.assertTrue(sc._aplicar_parche_con_resolucion(
                self.parche, self.tmp))
            inc.assert_not_called()

    def test_conflicto_cae_a_resolucion_incremental(self):
        with mock.patch.object(sc, "_aplicar_parche", return_value=False), \
                mock.patch.object(sc, "_aplicar_hunks_incremental",
                                  return_value=True) as inc:
            self.assertTrue(sc._aplicar_parche_con_resolucion(
                self.parche, self.tmp))
            inc.assert_called_once_with(self.parche, self.tmp)

    def test_validacion_previa_avisa_pero_intenta(self):
        # Contenido esperado distinto del actual → aviso y reintento igual.
        with mock.patch.object(sc, "_aplicar_parche", return_value=True), \
                mock.patch.object(sc, "aviso") as avisos:
            ok = sc._aplicar_parche_con_resolucion(
                self.parche, self.tmp, contenido_esperado="otro\n")
            self.assertTrue(ok)
            self.assertTrue(any("Validación previa fallida" in str(c.args[0])
                                for c in avisos.call_args_list))


class TestSkillsEditor(unittest.TestCase):
    """Integración del editor propio con el sistema de skills."""

    def test_guardar_skill_editor_usa_nombre_idempotente(self):
        with mock.patch.object(sc, "_skill_guardar",
                               return_value=42) as guardar:
            sid = sc._skill_editor_guardar("renombrar la función x",
                                           "modulo.py", "renombrar",
                                           estrategia="ast")
        self.assertEqual(sid, 42)
        kwargs = guardar.call_args.kwargs
        self.assertEqual(kwargs["nombre"], "editor-renombrar")
        self.assertEqual(kwargs["pasos"][0]["estrategia"], "ast")
        self.assertEqual(kwargs["contexto"]["archivo"], "modulo.py")

    def test_guardar_skill_nunca_lanza(self):
        with mock.patch.object(sc, "_skill_guardar",
                               side_effect=RuntimeError("db rota")):
            self.assertIsNone(sc._skill_editor_guardar("t", "a.py", "general"))

    def test_estrategia_desde_skill_valido(self):
        skill = {"id": 7, "nombre": "editor-renombrar",
                 "confiabilidad": 0.8,
                 "pasos": [{"accion": "editor_propio",
                            "estrategia": "parche"}]}
        with mock.patch.object(sc, "_skill_buscar", return_value=skill):
            self.assertEqual(
                sc._skill_editor_estrategia("renombrar la función y"),
                "parche")

    def test_ignora_skills_que_no_son_del_editor(self):
        skill = {"id": 1, "nombre": "tarea-cualquiera", "confiabilidad": 1.0,
                 "pasos": [{"estrategia": "parche"}]}
        with mock.patch.object(sc, "_skill_buscar", return_value=skill):
            self.assertIsNone(sc._skill_editor_estrategia("lo que sea"))

    def test_ignora_baja_confiabilidad(self):
        skill = {"id": 2, "nombre": "editor-general",
                 "confiabilidad": 0.3,
                 "pasos": [{"estrategia": "sobrescribir"}]}
        with mock.patch.object(sc, "_skill_buscar", return_value=skill):
            self.assertIsNone(sc._skill_editor_estrategia("lo que sea"))


class TestIntegracionSkillsEnEjecutar(unittest.TestCase):
    """AgenteEditorPropio.ejecutar reutiliza skills y guarda el patrón."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc330skills_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exito_guarda_patron_y_refuerza(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        (Path(self.tmp) / archivo).write_text("def fn(): return 1\n",
                                              encoding="utf-8")
        diff = ("--- a/modulo.py\n+++ b/modulo.py\n@@ -1 +1 @@\n"
                "-def fn(): return 1\n+def fn(): return 2\n")
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=diff), \
                mock.patch.object(agente, "aplicar_parche", return_value=True), \
                mock.patch.object(sc, "_skill_editor_estrategia",
                                  return_value=None) as buscar, \
                mock.patch.object(sc, "_skill_editor_guardar",
                                  return_value=9) as guardar, \
                mock.patch.object(sc, "_skill_registrar_exito",
                                  return_value=0.65) as reforzar:
            ok = agente.ejecutar([archivo], "corrige el retorno",
                                 directorio=self.tmp, modo_edicion="parche")
            self.assertTrue(ok)
            buscar.assert_called_once()
            guardar.assert_called_once()
            args, _kwargs = guardar.call_args
            self.assertEqual(args[2], "corregir_error")   # patrón clasificado
            reforzar.assert_called_once_with(9)

    def test_skill_aprendido_prioriza_estrategia(self):
        agente = AgenteEditorPropio()
        archivo = "modulo.py"
        (Path(self.tmp) / archivo).write_text("x\n", encoding="utf-8")
        with mock.patch.object(agente, "_cadena_modos",
                               return_value=["parche", "sobrescribir"]), \
                mock.patch.object(sc, "_skill_editor_estrategia",
                                  return_value="sobrescribir"), \
                mock.patch.object(sc, "_skill_editor_guardar",
                                  return_value=None), \
                mock.patch.object(agente, "_aplicar_modo_sobrescribir",
                                  return_value=True) as sob, \
                mock.patch.object(agente, "_aplicar_modo_parche") as parche:
            ok = agente.ejecutar([archivo], "cambia algo",
                                 directorio=self.tmp, modo_edicion="auto")
            self.assertTrue(ok)
            # La estrategia aprendida ('sobrescribir') se ejecutó primero.
            sob.assert_called_once()
            parche.assert_not_called()


class TestPromptEditacionEnriquecido(unittest.TestCase):
    """El prompt de parche incluye lenguaje, tamaño e instrucciones."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc330prompt_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prompt_parche_contiene_lenguaje_e_instrucciones(self):
        agente = AgenteEditorPropio()
        diff = ("--- a/modulo.py\n+++ b/modulo.py\n@@ -1 +1 @@\n"
                "-def fn(): return 1\n+def fn(): return 2\n")
        with mock.patch.object(sc, "_enviar_al_proveedor",
                               return_value=diff) as enviar, \
                mock.patch.object(agente, "aplicar_parche", return_value=True):
            agente._aplicar_modo_parche(
                "modulo.py", "cambiar retorno a 2",
                contenido_actual="def fn(): return 1\n",
                modelo=None, directorio=self.tmp)
        prompt = enviar.call_args[0][2][0]["content"]
        self.assertIn("lenguaje: python", prompt)
        self.assertIn("Instrucciones:", prompt)
        self.assertIn("Conserva el estilo existente", prompt)
        self.assertIn("```python", prompt)


if __name__ == "__main__":
    unittest.main()



