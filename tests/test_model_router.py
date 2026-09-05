#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests para model_router (v6.30.0) — orquestación e híbrido Local-Nube."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import model_router as mr


class TestClasificarTarea(unittest.TestCase):
    """Clasificación de tareas por heurísticas rápidas."""

    def test_indexacion_keyword(self):
        self.assertEqual(mr.clasificar_tarea("indexa el proyecto"), "indexacion")

    def test_busqueda_semantica_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("busca archivos similares a login.py"),
            "busqueda_semantica",
        )

    def test_edicion_critica_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("arregla el botón de pago"),
            "edicion_critica",
        )

    def test_planificacion_simple_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("planifica los pasos para el deploy"),
            "planificacion_simple",
        )

    def test_razonamiento_complejo_keyword(self):
        self.assertEqual(
            mr.clasificar_tarea("analiza la arquitectura del sistema"),
            "razonamiento_complejo",
        )

    def test_chat_general_corta(self):
        self.assertEqual(mr.clasificar_tarea("hola"), "chat_general")

    def test_chat_general_vacia(self):
        self.assertEqual(mr.clasificar_tarea(""), "chat_general")

    def test_chat_general_none(self):
        self.assertEqual(mr.clasificar_tarea(None), "chat_general")

    def test_accion_explicita_indexar(self):
        self.assertEqual(
            mr.clasificar_tarea("foo", contexto={"accion": "indexar"}),
            "indexacion",
        )

    def test_accion_explicita_edicion(self):
        self.assertEqual(
            mr.clasificar_tarea("foo", contexto={"accion": "editar"}),
            "edicion_critica",
        )

    def test_archivos_a_editar(self):
        self.assertEqual(
            mr.clasificar_tarea(
                "procesa esto",
                contexto={"archivos": ["a.py", "b.py"]},
            ),
            "edicion_critica",
        )

    def test_consulta_larga_razonamiento(self):
        consulta = " ".join(["palabra"] * 70)
        self.assertEqual(mr.clasificar_tarea(consulta), "razonamiento_complejo")


class TestSeleccionarModelo(unittest.TestCase):
    """Selección de modelo según configuración."""

    def test_sin_config_default(self):
        prov, mod = mr.seleccionar_modelo("indexacion", {})
        self.assertIsNone(prov)
        self.assertIsNone(mod)

    def test_con_config_ollama(self):
        config = {"model_routing": {"indexacion": {"provider": "ollama", "model": "qwen3.5:9b"}}}
        prov, mod = mr.seleccionar_modelo("indexacion", config)
        self.assertEqual(prov, "ollama")
        self.assertEqual(mod, "qwen3.5:9b")

    def test_con_config_claude(self):
        config = {
            "model_routing": {
                "razonamiento_complejo": {"provider": "anthropic", "model": "claude-3.7-sonnet"}
            }
        }
        prov, mod = mr.seleccionar_modelo("razonamiento_complejo", config)
        self.assertEqual(prov, "anthropic")
        self.assertEqual(mod, "claude-3.7-sonnet")

    def test_categoria_sin_entrada(self):
        config = {"model_routing": {"indexacion": {"provider": "ollama", "model": "qwen3.5:9b"}}}
        prov, mod = mr.seleccionar_modelo("chat_general", config)
        self.assertIsNone(prov)
        self.assertIsNone(mod)

    def test_config_none(self):
        prov, mod = mr.seleccionar_modelo("edicion_critica", None)
        self.assertIsNone(prov)
        self.assertIsNone(mod)


class TestEnrutarTarea(unittest.TestCase):
    """Combinación clasificación + selección."""

    def test_enrutado_con_config(self):
        config = {"model_routing": {"edicion_critica": {"provider": "gemini", "model": "gemini-2.5-pro"}}}
        resultado = mr.enrutar_tarea("arregla el botón", config=config)
        self.assertEqual(resultado["categoria"], "edicion_critica")
        self.assertEqual(resultado["provider"], "gemini")
        self.assertEqual(resultado["model"], "gemini-2.5-pro")
        self.assertTrue(resultado["enrutado"])

    def test_no_enrutado_sin_config(self):
        resultado = mr.enrutar_tarea("arregla el botón", config={})
        self.assertEqual(resultado["categoria"], "edicion_critica")
        self.assertIsNone(resultado["provider"])
        self.assertFalse(resultado["enrutado"])


class TestIntegracionSnapcontext(unittest.TestCase):
    """Integración con snapcontext.py."""

    def test_existe_flag_model_routing(self):
        """Verifica que el flag --model-routing existe en snapcontext."""
        import snapcontext as sc
        self.assertTrue(hasattr(sc, "_MODEL_ROUTING_ACTIVO"))

    def test_existe_configurar_model_routing(self):
        import snapcontext as sc
        self.assertTrue(callable(getattr(sc, "_configurar_model_routing", None)))

    def test_existe_cargar_configuracion_routing(self):
        import snapcontext as sc
        self.assertTrue(callable(getattr(sc, "_cargar_configuracion_routing", None)))



# ═══════════════════════════════════════════════════════════════════════════
# v6.30.0 — Enrutamiento híbrido Local-Nube
# ═══════════════════════════════════════════════════════════════════════════

_CFG_HIBRIDO = {
    "model_routing": {
        "prioridad_local": ["ollama/qwen3.5:9b", "ollama/llama3.2"],
        "prioridad_nube": ["gemini/gemini-2.5-pro",
                           "anthropic/claude-3.7-sonnet",
                           "deepseek/deepseek-v3"],
        "umbral_complejidad": {
            "longitud_consulta": 100,
            "tamano_archivo": 1000,
            "num_archivos": 3,
        },
        "fallback_automatico": True,
    }
}


class TestEsTareaCompleja(unittest.TestCase):
    """Detección de complejidad con heurísticas rápidas (sin IA)."""

    def test_consulta_corta_simple(self):
        self.assertFalse(mr.es_tarea_compleja("arregla el botón de pago"))

    def test_consulta_none_simple(self):
        self.assertFalse(mr.es_tarea_compleja(None))

    def test_contexto_none_no_rompe(self):
        self.assertFalse(mr.es_tarea_compleja("hola qué tal", None))

    def test_consulta_larga_es_compleja(self):
        consulta = " ".join(["palabra"] * 120)      # > 100 palabras (defecto)
        self.assertTrue(mr.es_tarea_compleja(consulta))

    def test_consulta_en_el_limite_no_es_compleja(self):
        consulta = " ".join(["palabra"] * 100)      # exactamente 100 → no
        self.assertFalse(mr.es_tarea_compleja(consulta))

    def test_umbral_configurable(self):
        config = {"model_routing": {"umbral_complejidad":
                                    {"longitud_consulta": 5}}}
        self.assertTrue(mr.es_tarea_compleja(
            "una dos tres cuatro cinco seis", config=config))

    def test_umbral_invalido_usa_defecto(self):
        config = {"model_routing": {"umbral_complejidad":
                                    {"longitud_consulta": "mucho"}}}
        self.assertFalse(mr.es_tarea_compleja("hola qué tal", config=config))

    def test_comando_complejo_deploy(self):
        self.assertTrue(mr.es_tarea_compleja(
            "haz deploy del servicio a producción"))

    def test_comando_complejo_docker(self):
        self.assertTrue(mr.es_tarea_compleja(
            "ejecuta docker compose up -d y revisa los logs"))

    def test_consulta_normal_no_es_compleja(self):
        self.assertFalse(mr.es_tarea_compleja(
            "añade un campo email al formulario de registro"))

    def test_varios_archivos_es_compleja(self):
        contexto = {"archivos": ["a.py", "b.py", "c.py", "d.py"]}
        self.assertTrue(mr.es_tarea_compleja("edita estos", contexto))

    def test_tres_archivos_no_es_compleja(self):
        contexto = {"archivos": ["a.py", "b.py", "c.py"]}
        self.assertFalse(mr.es_tarea_compleja("edita estos", contexto))

    def test_num_archivos_configurable(self):
        config = {"model_routing": {"umbral_complejidad": {"num_archivos": 1}}}
        contexto = {"archivos": ["a.py", "b.py"]}
        self.assertTrue(mr.es_tarea_compleja("edita", contexto, config))

    def test_archivo_grande_en_disco(self):
        with tempfile.TemporaryDirectory() as tmp:
            grande = os.path.join(tmp, "grande.py")
            Path(grande).write_text("x = 1\n" * 1500, encoding="utf-8")
            self.assertTrue(mr.es_tarea_compleja(
                "revisa esto", {"archivos": [grande]}))

    def test_archivo_pequeno_no_es_compleja(self):
        with tempfile.TemporaryDirectory() as tmp:
            peque = os.path.join(tmp, "peque.py")
            Path(peque).write_text("x = 1\n", encoding="utf-8")
            self.assertFalse(mr.es_tarea_compleja(
                "revisa esto", {"archivos": [peque]}))

    def test_tamano_archivo_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "medio.py")
            Path(ruta).write_text("x = 1\n" * 15, encoding="utf-8")
            config = {"model_routing": {"umbral_complejidad":
                                        {"tamano_archivo": 10}}}
            self.assertTrue(mr.es_tarea_compleja(
                "revisa", {"archivos": [ruta]}, config))

    def test_archivo_inexistente_no_rompe(self):
        self.assertFalse(mr.es_tarea_compleja(
            "revisa esto", {"archivos": ["no_existe_12345.py"]}))

    def test_archivos_como_dicts(self):
        contexto = {"archivos": [{"ruta": "a.py"}, {"path": "b.py"},
                                 {"archivo": "c.py"}, {"ruta": "d.py"},
                                 {"otro": 1}]}
        self.assertTrue(mr.es_tarea_compleja("edita estos", contexto))

    def test_hint_lineas_sin_disco(self):
        self.assertTrue(mr.es_tarea_compleja("revisa", {"lineas": 2000}))
        self.assertFalse(mr.es_tarea_compleja(
            "revisa", {"lineas_por_archivo": {"a.py": 50}}))


class TestObtenerOrdenPrioridad(unittest.TestCase):
    """Cadena de prioridad local↔nube según complejidad."""

    def test_simple_local_primero(self):
        orden = mr.obtener_orden_prioridad(False, _CFG_HIBRIDO)
        self.assertEqual(orden[0], ("ollama", "qwen3.5:9b"))
        self.assertEqual(orden[1], ("ollama", "llama3.2"))

    def test_simple_nube_como_fallback(self):
        orden = mr.obtener_orden_prioridad(False, _CFG_HIBRIDO)
        self.assertEqual(orden[-1], ("deepseek", "deepseek-v3"))
        self.assertIn(("gemini", "gemini-2.5-pro"), orden)

    def test_compleja_nube_primero(self):
        orden = mr.obtener_orden_prioridad(True, _CFG_HIBRIDO)
        self.assertEqual(orden[0], ("gemini", "gemini-2.5-pro"))
        self.assertEqual(orden[1], ("anthropic", "claude-3.7-sonnet"))

    def test_compleja_local_como_fallback(self):
        orden = mr.obtener_orden_prioridad(True, _CFG_HIBRIDO)
        self.assertIn(("ollama", "qwen3.5:9b"), orden[-2:])

    def test_sin_config_vacio(self):
        self.assertEqual(mr.obtener_orden_prioridad(True, {}), [])
        self.assertEqual(mr.obtener_orden_prioridad(False, None), [])

    def test_config_sin_seccion_model_routing(self):
        self.assertEqual(mr.obtener_orden_prioridad(True, {"otro": 1}), [])

    def test_entrada_str_sin_barra_ignorada(self):
        config = {"model_routing": {"prioridad_local": ["ollama"]}}
        self.assertEqual(mr.obtener_orden_prioridad(False, config), [])

    def test_entrada_como_dicts(self):
        config = {"model_routing": {"prioridad_local": [
            {"provider": "ollama", "model": "qwen3.5:9b"}]}}
        self.assertEqual(mr.obtener_orden_prioridad(False, config),
                         [("ollama", "qwen3.5:9b")])

    def test_entrada_str_unica(self):
        config = {"model_routing": {"prioridad_local": "ollama/qwen3.5:9b"}}
        self.assertEqual(mr.obtener_orden_prioridad(False, config),
                         [("ollama", "qwen3.5:9b")])

    def test_sin_duplicados(self):
        config = {"model_routing": {
            "prioridad_local": ["ollama/qwen3.5:9b"],
            "prioridad_nube": ["ollama/qwen3.5:9b"]}}
        self.assertEqual(mr.obtener_orden_prioridad(False, config),
                         [("ollama", "qwen3.5:9b")])


class TestSeleccionarModeloConFallback(unittest.TestCase):
    """Selección del primer modelo de la cadena (+ compatibilidad v6.24.0)."""

    def test_compleja_devuelve_nube(self):
        prov, mod = mr.seleccionar_modelo_con_fallback(
            "edicion_critica", _CFG_HIBRIDO, compleja=True)
        self.assertEqual((prov, mod), ("gemini", "gemini-2.5-pro"))

    def test_simple_devuelve_local(self):
        prov, mod = mr.seleccionar_modelo_con_fallback(
            "indexacion", _CFG_HIBRIDO, compleja=False)
        self.assertEqual((prov, mod), ("ollama", "qwen3.5:9b"))

    def test_sin_prioridades_delega_en_seleccionar_modelo(self):
        config = {"model_routing": {"edicion_critica":
                                    {"provider": "gemini",
                                     "model": "gemini-2.5-pro"}}}
        prov, mod = mr.seleccionar_modelo_con_fallback(
            "edicion_critica", config, compleja=False)
        self.assertEqual((prov, mod), ("gemini", "gemini-2.5-pro"))

    def test_sin_nada_devuelve_none(self):
        prov, mod = mr.seleccionar_modelo_con_fallback(
            "chat_general", {}, compleja=True)
        self.assertIsNone(prov)
        self.assertIsNone(mod)

    def test_solo_prioridad_local(self):
        config = {"model_routing": {"prioridad_local": ["ollama/llama3.2"]}}
        prov, mod = mr.seleccionar_modelo_con_fallback(
            "edicion_critica", config, compleja=True)
        self.assertEqual((prov, mod), ("ollama", "llama3.2"))


class TestEnrutarTareaHibrido(unittest.TestCase):
    """``enrutar_tarea`` informa la complejidad detectada (v6.30.0)."""

    def test_consulta_simple_marca_no_compleja(self):
        resultado = mr.enrutar_tarea("hola", config=_CFG_HIBRIDO)
        self.assertFalse(resultado["compleja"])
        self.assertEqual(resultado["provider"], "ollama")

    def test_consulta_larga_marca_compleja(self):
        resultado = mr.enrutar_tarea(" ".join(["palabra"] * 120),
                                     config=_CFG_HIBRIDO)
        self.assertTrue(resultado["compleja"])
        self.assertEqual(resultado["provider"], "gemini")

    def test_compatibilidad_v624(self):
        # Sin prioridades configuradas el resultado es el de siempre.
        config = {"model_routing": {"indexacion":
                                    {"provider": "ollama",
                                     "model": "qwen3.5:9b"}}}
        resultado = mr.enrutar_tarea("indexa el proyecto", config=config)
        self.assertEqual(resultado["categoria"], "indexacion")
        self.assertEqual(resultado["provider"], "ollama")
        self.assertEqual(resultado["model"], "qwen3.5:9b")
        self.assertTrue(resultado["enrutado"])
        self.assertFalse(resultado["compleja"])


class TestEsProveedorLocal(unittest.TestCase):
    """Detección de proveedores locales."""

    def test_ollama_es_local(self):
        self.assertTrue(mr.es_proveedor_local("ollama"))

    def test_gemini_no_es_local(self):
        self.assertFalse(mr.es_proveedor_local("gemini"))

    def test_none_no_es_local(self):
        self.assertFalse(mr.es_proveedor_local(None))

    def test_configurable(self):
        config = {"model_routing": {"proveedores_locales": ["lmstudio"]}}
        self.assertTrue(mr.es_proveedor_local("LMStudio", config))
        self.assertFalse(mr.es_proveedor_local("ollama", config))


class TestFallbackEnvio(unittest.TestCase):
    """Cadena de fallback en ``_enviar_al_proveedor`` (snapcontext)."""

    def setUp(self):
        import snapcontext as sc
        self.sc = sc
        self._estado = (sc._MODEL_ROUTING_ACTIVO, sc._MODELO_EXPLICITO,
                        sc._MODEL_FALLBACK_ACTIVO)
        sc._MODEL_ROUTING_ACTIVO = True
        sc._MODELO_EXPLICITO = False
        sc._configurar_model_fallback(True)
        sc._configurar_hibrido_cli()

    def tearDown(self):
        (self.sc._MODEL_ROUTING_ACTIVO,
         self.sc._MODELO_EXPLICITO,
         self.sc._MODEL_FALLBACK_ACTIVO) = self._estado
        self.sc._configurar_hibrido_cli()

    def test_fallback_tras_error_de_api(self):
        sc = self.sc
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  side_effect=[RuntimeError("connection timeout"),
                                               "respuesta"]) as enviar:
            resultado = sc._enviar_al_proveedor(
                "ollama", "llama3.2",
                [{"role": "user", "content": "hola"}],
                categoria="chat_general")
        self.assertEqual(resultado, "respuesta")
        # La cadena recorre primero TODA la lista local y después la nube.
        self.assertEqual([c.args[:2] for c in enviar.call_args_list],
                         [("ollama", "qwen3.5:9b"),
                          ("ollama", "llama3.2")])

    def test_error_autenticacion_no_reintenta(self):
        sc = self.sc
        error_clave = RuntimeError(
            "No se encontró la variable de entorno GEMINI_API_KEY.")
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  side_effect=error_clave) as enviar:
            with self.assertRaises(RuntimeError):
                sc._enviar_al_proveedor(
                    "ollama", "llama3.2",
                    [{"role": "user", "content": "hola"}],
                    categoria="chat_general")
        self.assertEqual(enviar.call_count, 1)

    def test_todos_fallan_error_claro(self):
        sc = self.sc
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  side_effect=RuntimeError("boom")) as enviar:
            with self.assertRaises(RuntimeError) as ctx:
                sc._enviar_al_proveedor(
                    "ollama", "llama3.2",
                    [{"role": "user", "content": "hola"}],
                    categoria="chat_general")
        self.assertIn("Todos los modelos", str(ctx.exception))
        self.assertEqual(enviar.call_count, 5)      # 2 locales + 3 nube

    def test_fallback_desactivado_un_solo_intento(self):
        sc = self.sc
        sc._configurar_model_fallback(False)
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  side_effect=RuntimeError("boom")) as enviar:
            with self.assertRaises(RuntimeError):
                sc._enviar_al_proveedor(
                    "ollama", "llama3.2",
                    [{"role": "user", "content": "hola"}],
                    categoria="chat_general")
        self.assertEqual(enviar.call_count, 1)

    def test_tarea_compleja_usa_nube_primero(self):
        sc = self.sc
        consulta = " ".join(["palabra"] * 150)
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  return_value="ok") as enviar:
            sc._enviar_al_proveedor(
                "ollama", "llama3.2",
                [{"role": "user", "content": consulta}],
                categoria="chat_general")
        self.assertEqual(enviar.call_args.args[0], "gemini")

    def test_mensaje_tarea_simple_local(self):
        sc = self.sc
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "info") as info, \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  return_value="ok"):
            sc._enviar_al_proveedor(
                "ollama", "llama3.2",
                [{"role": "user", "content": "hola"}],
                categoria="chat_general")
        textos = " ".join(str(c.args[0])
                          for c in info.call_args_list if c.args)
        self.assertIn("🧠 Tarea simple. Usando modelo local: "
                      "ollama/qwen3.5:9b", textos)

    def test_mensaje_tarea_compleja_cloud(self):
        sc = self.sc
        consulta = " ".join(["palabra"] * 150)
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "info") as info, \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  return_value="ok"):
            sc._enviar_al_proveedor(
                "ollama", "llama3.2",
                [{"role": "user", "content": consulta}],
                categoria="chat_general")
        textos = " ".join(str(c.args[0])
                          for c in info.call_args_list if c.args)
        self.assertIn("🧠 Tarea compleja detectada. Usando modelo cloud: "
                      "gemini/gemini-2.5-pro", textos)

    def test_mensaje_aviso_reintento(self):
        sc = self.sc
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "aviso") as aviso, \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  side_effect=[RuntimeError("timeout"), "ok"]):
            sc._enviar_al_proveedor(
                "ollama", "llama3.2",
                [{"role": "user", "content": "hola"}],
                categoria="chat_general")
        textos = " ".join(str(c.args[0])
                          for c in aviso.call_args_list if c.args)
        self.assertIn("⚠️ Fallo en ollama/qwen3.5:9b. Reintentando con "
                      "ollama/llama3.2", textos)

    def test_sin_prioridades_comportamiento_v624(self):
        sc = self.sc
        config = {"chat_general": {"provider": "gemini",
                                   "model": "gemini-2.5-pro"}}
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=config), \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  return_value="ok") as enviar:
            sc._enviar_al_proveedor(
                "ollama", "llama3.2",
                [{"role": "user", "content": "hola"}],
                categoria="chat_general")
        self.assertEqual(enviar.call_args.args[0], "gemini")
        self.assertEqual(enviar.call_count, 1)

    def test_modelo_explicito_sin_fallback(self):
        sc = self.sc
        sc._MODELO_EXPLICITO = True
        with mock.patch.object(sc, "_cargar_configuracion_routing",
                               return_value=dict(
                                   _CFG_HIBRIDO["model_routing"])), \
                mock.patch.object(sc, "_enviar_al_proveedor_unico",
                                  return_value="ok") as enviar:
            sc._enviar_al_proveedor(
                "anthropic", "claude-3-5-sonnet-20241022",
                [{"role": "user", "content": "hola"}],
                categoria="chat_general")
        self.assertEqual(enviar.call_args.args[0], "anthropic")
        self.assertEqual(enviar.call_count, 1)


class TestFlagsCLI(unittest.TestCase):
    """Flags nuevos del enrutamiento híbrido (v6.30.0)."""

    def test_model_fallback_por_defecto_none(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.model_fallback)

    def test_no_model_fallback(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(
            ["consulta", "--no-model-fallback"])
        self.assertFalse(args.model_fallback)

    def test_model_fallback_explicito(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["consulta", "--model-fallback"])
        self.assertTrue(args.model_fallback)

    def test_complejidad_umbral(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(
            ["consulta", "--complejidad-umbral", "25"])
        self.assertEqual(args.complejidad_umbral, 25)

    def test_complejidad_umbral_por_defecto_none(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(["consulta"])
        self.assertIsNone(args.complejidad_umbral)

    def test_model_prioridad_local(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(
            ["consulta", "--model-prioridad-local",
             "ollama/qwen3.5:9b", "ollama/llama3.2"])
        self.assertEqual(args.model_prioridad_local,
                         ["ollama/qwen3.5:9b", "ollama/llama3.2"])

    def test_model_prioridad_nube(self):
        import snapcontext as sc
        args = sc.crear_parser().parse_args(
            ["consulta", "--model-prioridad-nube", "gemini/gemini-2.5-pro"])
        self.assertEqual(args.model_prioridad_nube,
                         ["gemini/gemini-2.5-pro"])

    def test_configuracion_routing_efectiva_umbral(self):
        import snapcontext as sc
        sc._configurar_hibrido_cli(umbral=25)
        try:
            seccion = sc._configuracion_routing_efectiva()
            self.assertEqual(
                seccion["umbral_complejidad"]["longitud_consulta"], 25)
        finally:
            sc._configurar_hibrido_cli()

    def test_configuracion_routing_efectiva_prioridades(self):
        import snapcontext as sc
        sc._configurar_hibrido_cli(
            prioridad_local=["ollama/llama3.2"],
            prioridad_nube=["deepseek/deepseek-v3"])
        try:
            seccion = sc._configuracion_routing_efectiva()
            self.assertEqual(seccion["prioridad_local"], ["ollama/llama3.2"])
            self.assertEqual(seccion["prioridad_nube"],
                             ["deepseek/deepseek-v3"])
        finally:
            sc._configurar_hibrido_cli()

    def test_existen_configuradores_hibrido(self):
        import snapcontext as sc
        self.assertTrue(callable(getattr(sc, "_configurar_model_fallback",
                                         None)))
        self.assertTrue(callable(getattr(sc,
                                         "_configuracion_routing_efectiva",
                                         None)))
        self.assertTrue(callable(getattr(sc, "_es_error_autenticacion", None)))
        self.assertTrue(callable(getattr(sc, "_extraer_consulta_mensajes",
                                         None)))


class TestEsErrorAutenticacion(unittest.TestCase):
    """Los errores de autenticación no disparan el fallback (v6.30.0)."""

    def test_api_key_faltante(self):
        import snapcontext as sc
        self.assertTrue(sc._es_error_autenticacion(RuntimeError(
            "No se encontró la variable de entorno GEMINI_API_KEY.")))

    def test_status_code_401(self):
        import snapcontext as sc
        exc = RuntimeError("authentication failed")
        exc.status_code = 401
        self.assertTrue(sc._es_error_autenticacion(exc))

    def test_invalid_api_key(self):
        import snapcontext as sc
        self.assertTrue(sc._es_error_autenticacion(
            RuntimeError("invalid_api_key: Incorrect API key provided")))

    def test_error_api_normal_no_es_auth(self):
        import snapcontext as sc
        self.assertFalse(sc._es_error_autenticacion(
            RuntimeError("Connection timeout after 120s")))


class TestVersion630(unittest.TestCase):
    """Versión 6.30.0 (híbrido Local-Nube)."""

    def test_version_6_30_0(self):
        import snapcontext as sc
        self.assertEqual(sc.VERSION, "6.30.0")


if __name__ == "__main__":
    unittest.main()
