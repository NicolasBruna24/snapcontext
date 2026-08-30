#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests v6.1.0: manejo de contexto inteligente (context_utils.py).

Cubre:
- ``estimar_tokens`` / ``estimar_tokens_de_archivo`` (con y sin tiktoken).
- ``extraer_bloques_relevantes`` (tree-sitter, objetivo y fallback regex).
- ``seleccionar_contexto`` (archivo pequeño → completo; grande → fragmento).
- Integración con ``agentes.py`` (mockeando el proveedor) y ``snapcontext.py``.
- Flags CLI ``--max-context-tokens`` y ``--editor-fallback``.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agentes as ag
import context_utils as ctx
import snapcontext as sc


def _contenido_grande(num_funcs: int = 120, lineas_por_func: int = 10) -> str:
    """Genera un archivo Python sintético con muchas funciones pequeñas."""
    partes = ["# archivo grande sintético"]
    for i in range(num_funcs):
        partes.append(f"def funcion_{i:03d}():")
        for _ in range(lineas_por_func - 2):
            partes.append(f"    MARCA_{i:03d} = {i}")
        partes.append(f"    return MARCA_{i:03d}")
        partes.append("")
    return "\n".join(partes) + "\n"


CODIGO_PEQUEÑO = (
    "def funcion_a():\n"
    "    return 1\n"
    "\n"
    "def funcion_b():\n"
    "    return 2\n"
)


class TestEstimacionTokens(unittest.TestCase):
    """Estimación de tokens (tiktoken o regla 4 caracteres)."""

    def test_estimar_tokens_vacio(self):
        self.assertEqual(ctx.estimar_tokens(""), 0)

    def test_estimar_tokens_regla_4_caracteres_sin_tiktoken(self):
        """Sin tiktoken: 1 token ≈ 4 caracteres."""
        original, buscado = ctx._tiktoken_enc, ctx._tiktoken_buscado
        ctx._tiktoken_enc, ctx._tiktoken_buscado = None, True
        try:
            self.assertEqual(ctx.estimar_tokens("a" * 400), 100)
            self.assertEqual(ctx.estimar_tokens("hola"), 1)
        finally:
            ctx._tiktoken_enc, ctx._tiktoken_buscado = original, buscado

    def test_estimar_tokens_con_tiktoken_si_disponible(self):
        enc = ctx._cargar_tiktoken()
        if enc is None:
            self.skipTest("tiktoken no está instalado")
        texto = "def hola():\n    return 'mundo'\n"
        self.assertEqual(ctx.estimar_tokens(texto), len(enc.encode(texto)))
        self.assertGreater(ctx.estimar_tokens(texto), 0)

    def test_estimar_tokens_no_negativo_y_monotono(self):
        corto = ctx.estimar_tokens("def f(): pass")
        largo = ctx.estimar_tokens(_contenido_grande())
        self.assertGreaterEqual(corto, 1)
        self.assertGreater(largo, corto)

    def test_estimar_tokens_de_archivo(self):
        tmp = Path(tempfile.mkdtemp(prefix="scctx_est_"))
        try:
            destino = tmp / "modulo.py"
            destino.write_text(CODIGO_PEQUEÑO, encoding="utf-8")
            self.assertEqual(ctx.estimar_tokens_de_archivo(str(destino)),
                             ctx.estimar_tokens(CODIGO_PEQUEÑO))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_estimar_tokens_de_archivo_inexistente(self):
        self.assertEqual(ctx.estimar_tokens_de_archivo("no/existe.py"), 0)


class TestExtraerBloques(unittest.TestCase):
    """Extracción de bloques (tree-sitter → ast → regex)."""

    def test_extraer_bloques_python(self):
        resumen, bloques = ctx.extraer_bloques_relevantes(CODIGO_PEQUEÑO,
                                                          "python")
        self.assertIn("funcion_a", resumen)
        self.assertIn("funcion_b", resumen)
        self.assertEqual(len(bloques), 2)
        self.assertTrue(bloques[0].startswith("def funcion_a():"))
        self.assertIn("return 2", bloques[1])

    def test_objetivo_se_coloca_primero(self):
        _resumen, bloques = ctx.extraer_bloques_relevantes(
            CODIGO_PEQUEÑO, "python", objetivo="funcion_b")
        self.assertEqual(len(bloques), 2)
        self.assertTrue(bloques[0].startswith("def funcion_b():"))

    def test_fallback_regex_sin_tree_sitter_ni_ast(self):
        """Si tree-sitter y ast fallan, regex básico sigue extrayendo bloques."""
        original_ts = ctx._metadatos_tree_sitter
        original_ast = ctx._metadatos_ast

        def _romper_ts(*_a, **_k):
            raise RuntimeError("tree-sitter simulado como no disponible")

        def _romper_ast(*_a, **_k):
            raise RuntimeError("ast simulado como no disponible")

        ctx._metadatos_tree_sitter = _romper_ts
        ctx._metadatos_ast = _romper_ast
        try:
            metas = ctx._extraer_metadatos(CODIGO_PEQUEÑO, "python")
            self.assertEqual(len(metas), 2)
            self.assertEqual([m["nombre"] for m in metas],
                             ["funcion_a", "funcion_b"])
            resumen, bloques = ctx.extraer_bloques_relevantes(
                CODIGO_PEQUEÑO, "python")
            self.assertEqual(len(bloques), 2)
            self.assertTrue(bloques[1].startswith("def funcion_b():"))
        finally:
            ctx._metadatos_tree_sitter = original_ts
            ctx._metadatos_ast = original_ast

    def test_objetivo_en_mensaje(self):
        objetivo = ctx.objetivo_en_mensaje(CODIGO_PEQUEÑO, "python",
                                           "mejora la función funcion_b")
        self.assertEqual(objetivo, "funcion_b")
        self.assertIsNone(ctx.objetivo_en_mensaje(
            CODIGO_PEQUEÑO, "python", "no hay bloque mencionado"))
        self.assertIsNone(ctx.objetivo_en_mensaje(CODIGO_PEQUEÑO, "python", ""))


class TestSeleccionarContexto(unittest.TestCase):
    """Selección de contexto: pequeño → completo; grande → fragmento."""

    def test_archivo_pequeno_devuelve_contenido_completo(self):
        self.assertEqual(
            ctx.seleccionar_contexto(CODIGO_PEQUEÑO, "python"),
            CODIGO_PEQUEÑO)
        # También con un presupuesto explícito holgado.
        self.assertEqual(
            ctx.seleccionar_contexto(CODIGO_PEQUEÑO, "python",
                                     max_tokens=3000),
            CODIGO_PEQUEÑO)

    def test_contenido_vacio(self):
        self.assertEqual(ctx.seleccionar_contexto("", "python"), "")

    def test_archivo_grande_devuelve_fragmento(self):
        grande = _contenido_grande()
        fragmento = ctx.seleccionar_contexto(grande, "python",
                                             max_tokens=200)
        self.assertLess(len(fragmento), len(grande))
        self.assertIn("RESTRICCIÓN", fragmento)
        self.assertIn("[CÓDIGO RELEVANTE A EDITAR]", fragmento)

    def test_objetivo_incluido_en_fragmento(self):
        grande = _contenido_grande()
        fragmento = ctx.seleccionar_contexto(
            grande, "python", objetivo="funcion_060", max_tokens=200)
        # El bloque objetivo se incluye completo (cabecera + cuerpo).
        self.assertIn("def funcion_060():", fragmento)
        self.assertIn("MARCA_060 = 60", fragmento)

    def test_fragmento_respeta_el_presupuesto(self):
        grande = _contenido_grande()
        fragmento = ctx.seleccionar_contexto(grande, "python",
                                             max_tokens=300)
        # Margen para la cabecera de restricción (texto fijo tras el recorte).
        self.assertLessEqual(ctx.estimar_tokens(fragmento), 300 + 120)

    def test_sin_bloques_detectados_usa_cabecera(self):
        texto_sin_bloques = "\n".join(f"x = {i}" for i in range(600))
        fragmento = ctx.seleccionar_contexto(texto_sin_bloques, "python",
                                             max_tokens=100)
        self.assertIn("RESTRICCIÓN", fragmento)
        self.assertLess(len(fragmento), len(texto_sin_bloques))


class TestIntegracionEditorPropio(unittest.TestCase):
    """Integración con agentes.py mockeando el proveedor de IA."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scctx_edit_")
        self.archivo = Path(self.tmp) / "grande.py"
        self.contenido = _contenido_grande()
        self.archivo.write_text(self.contenido, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _parche_bloque(self, viejo: str, nuevo: str) -> str:
        return f"<<<ANTES>>>\n{viejo}\n<<<DESPUES>>>\n{nuevo}\n<<<FIN>>>"

    def test_sobrescribir_usa_contexto_selectivo(self):
        """Archivo grande → prompt reducido y bloque reinsertado en el original."""
        prompts = []

        def fake_proveedor(proveedor, modelo, mensajes, **_k):
            prompts.append(mensajes[0]["content"])
            return self._parche_bloque("MARCA_001 = 1", "    MARCA_001 = 111")

        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=fake_proveedor):
            ok = ag.AgenteEditorPropio()._aplicar_modo_sobrescribir(
                "grande.py", "mejora la función funcion_001", self.contenido,
                None, self.tmp, validar=False, max_intentos_validacion=1,
                conciso=False, max_context_tokens=250)
        self.assertTrue(ok)
        self.assertEqual(len(prompts), 1)
        # El prompt enviado era el fragmento, no el archivo completo.
        self.assertIn("RESTRICCIÓN", prompts[0])
        self.assertLess(len(prompts[0]), len(self.contenido))
        # El cambio del bloque quedó aplicado sobre el archivo real.
        resultado = self.archivo.read_text(encoding="utf-8")
        self.assertIn("MARCA_001 = 111", resultado)
        self.assertIn("funcion_119", resultado)   # el resto no se perdió

    def test_reintento_con_archivo_completo_tras_error_contexto(self):
        """Si el proveedor falla por contexto se reintenta con el archivo entero."""
        prompts = []
        llamadas = {"n": 0}

        def fake_proveedor(proveedor, modelo, mensajes, **_k):
            prompts.append(mensajes[0]["content"])
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise RuntimeError(
                    "maximum context length: 4096 tokens exceeded")
            return self.contenido.replace("    MARCA_002 = 2\n",
                                          "    MARCA_002 = 222\n")

        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=fake_proveedor):
            ok = ag.AgenteEditorPropio()._aplicar_modo_sobrescribir(
                "grande.py", "mejora la función funcion_002", self.contenido,
                None, self.tmp, validar=False, max_intentos_validacion=1,
                conciso=False, max_context_tokens=250)
        self.assertTrue(ok)
        self.assertEqual(llamadas["n"], 2)
        # La segunda llamada incluyó el archivo completo (cola del archivo).
        self.assertIn("funcion_119", prompts[1])
        self.assertIn("MARCA_002 = 222",
                      self.archivo.read_text(encoding="utf-8"))


class TestEditorAstYFlags(unittest.TestCase):
    """Integración con el modo AST y flags CLI del planificador."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scctx_ast_")
        self.archivo = Path(self.tmp) / "grande.py"
        self.archivo.write_text(_contenido_grande(), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_editor_ast_acepta_max_context_tokens(self):
        """``sc._editor_ast`` admite ``max_context_tokens`` y reduce el prompt."""
        grande = _contenido_grande() + "def vieja():\n    return 42\n"
        self.archivo.write_text(grande, encoding="utf-8")
        prompts = []

        def fake_proveedor(proveedor, modelo, mensajes, **_k):
            prompts.append(mensajes[0]["content"])
            return '[{"tipo": "renombrar", "nombre": "vieja", "nuevo": "nueva"}]'

        with mock.patch.object(sc, "_enviar_al_proveedor",
                               side_effect=fake_proveedor):
            ok = sc._editor_ast("grande.py", "renombra la función vieja",
                                directorio=self.tmp, max_context_tokens=250)
        self.assertTrue(ok)
        self.assertEqual(len(prompts), 1)
        self.assertIn("RESTRICCIÓN", prompts[0])
        self.assertLess(len(prompts[0]), len(grande))
        resultado = self.archivo.read_text(encoding="utf-8")
        self.assertIn("def nueva():", resultado)
        # Las operaciones se aplicaron sobre el archivo completo (no truncado).
        self.assertIn("funcion_119", resultado)

    def test_preparar_contenido_envio_small_vs_big(self):
        agente = ag.AgenteEditorPropio()
        # Archivo pequeño: se envía completo y sin truncar.
        contenido, truncado, objetivo, _n = agente._preparar_contenido_envio(
            "grande.py", "tarea", CODIGO_PEQUEÑO, 3000)
        self.assertEqual(contenido, CODIGO_PEQUEÑO)
        self.assertFalse(truncado)
        self.assertIsNone(objetivo)
        # Archivo grande: se trunca y detecta el objetivo del mensaje.
        contenido, truncado, objetivo, n = agente._preparar_contenido_envio(
            "grande.py", "mejora la función funcion_050",
            _contenido_grande(), 250)
        self.assertTrue(truncado)
        self.assertEqual(objetivo, "funcion_050")
        self.assertIn("funcion_050", contenido)
        self.assertGreater(n, 250)

    def test_flag_max_context_tokens(self):
        args = sc.crear_parser().parse_args(
            ["consulta", "--max-context-tokens", "1500"])
        self.assertEqual(args.max_context_tokens, 1500)
        # Por defecto: 3000 (compatibilidad hacia atrás).
        args_def = sc.crear_parser().parse_args(["consulta"])
        self.assertEqual(args_def.max_context_tokens, sc.MAX_CONTEXT_TOKENS)
        self.assertEqual(sc.MAX_CONTEXT_TOKENS, 3000)

    def test_flag_editor_fallback(self):
        args = sc.crear_parser().parse_args(["consulta", "--editor-fallback"])
        self.assertTrue(args.editor_fallback)
        args_def = sc.crear_parser().parse_args(["consulta"])
        self.assertFalse(args_def.editor_fallback)

    def test_constantes_compartidas_en_los_tres_modulos(self):
        self.assertEqual(ctx.MAX_CONTEXT_TOKENS, 3000)
        self.assertEqual(sc.MAX_CONTEXT_TOKENS, ctx.MAX_CONTEXT_TOKENS)
        self.assertEqual(ag.MAX_CONTEXT_TOKENS, ctx.MAX_CONTEXT_TOKENS)

    def test_es_error_contexto(self):
        self.assertTrue(ctx.es_error_contexto(
            RuntimeError("exceed_context_size_error: prompt too long")))
        self.assertTrue(ctx.es_error_contexto(
            RuntimeError("maximum context length: 4096 tokens exceeded")))
        self.assertFalse(ctx.es_error_contexto(
            RuntimeError("conexión rechazada por el servidor")))
        # La versión de agentes.py delega en context_utils (implementación única).
        exc = RuntimeError("context length exceeded")
        self.assertEqual(ag._es_error_contexto(exc),
                         ctx.es_error_contexto(exc))


if __name__ == "__main__":
    unittest.main()
