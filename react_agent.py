#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Motor ReAct (Reasoning + Acting) de SnapContext — v5.1.0.

Bucle dinámico *pensamiento → acción → observación*: a diferencia del
planificador estático (`--plan`, que genera todos los pasos por adelantado),
el agente ReAct decide cada paso después de **observar** el resultado del
anterior, adaptándose en tiempo real.

Se activa con ``snapcontext --react "consulta"`` y es completamente opcional:
el flujo `--plan` sigue intacto. Respeta el sandbox Docker (--sandbox, v4.3.0)
para todo comando de shell y el modo no interactivo (--auto).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

import snapcontext as sc
import detector_tests as det
import ui


UMBRAL_RESUMEN_TOKENS_DEFAULT = 8000   # dispara resumen automático del historial
MAX_REINTENTOS_JSON = 3                # reintentos si el LLM no devuelve JSON válido
LLM_TIMEOUT_SEG = int(os.environ.get("REACT_LLM_TIMEOUT", "300"))
MAX_SALIDA_OBSERVACION = 4000          # caracteres máx. de una observación
# v6.9.0: límite de iteraciones de historial del bucle ReAct (configurable con
# REACT_MAX_HISTORIAL). Por encima del límite se comprime el historial con un
# resumen LLM para acotar el uso de memoria en sesiones largas.
MAX_HISTORIAL_DEFAULT = 20             # ~2 entradas de historial por iteración


def _max_historial() -> int:
    """Nº máximo de iteraciones antes de resumir el historial (v6.9.0)."""
    bruto = os.environ.get("REACT_MAX_HISTORIAL", "")
    try:
        return max(1, int(bruto))
    except (TypeError, ValueError):
        return MAX_HISTORIAL_DEFAULT


def estimar_tokens(texto: str) -> int:
    """Estimación ligera de tokens (~4 caracteres por token)."""
    return max(1, len(texto or "") // 4)


def _umbral_resumen() -> int:
    bruto = os.environ.get("REACT_UMBRAL_RESUMEN", "")
    try:
        return max(1000, int(bruto))
    except (TypeError, ValueError):
        return UMBRAL_RESUMEN_TOKENS_DEFAULT


class ReactAgent:
    """Agente ReAct: razona, actúa con herramientas y observa el resultado."""

    ACCIONES_VALIDAS = (
        "editar_archivo", "ejecutar_comando", "buscar_codigo",
        "ejecutar_pruebas", "leer_archivo", "finalizar",
        # v6.7.0: expansión MCP — bases de datos y APIs externas.
        "db_query", "db_schema", "api_request", "api_inspect",
        # v6.10.0: navegador (Playwright) — ver/depurar interfaces visuales.
        "browser_abrir", "browser_screenshot", "browser_click",
        "browser_type", "browser_get_text", "browser_analizar_imagen",
        # v6.14.0: LSP — resolución de definiciones/referencias/tipos.
        "lsp_definicion", "lsp_referencias", "lsp_tipo",
    )

    def __init__(self, directorio: str = ".", auto: bool = False,
                 max_iter: int = 15, proveedor: Optional[str] = None,
                 modelo: Optional[str] = None, graph_rag: bool = False,
                 mostrar_razonamiento: bool = False,
                 sesion_docker: bool = False,
                 web_interactive: bool = False,
                 max_historial: Optional[int] = None,
                 browser: bool = False,
                 prompt_caching: Optional[bool] = None,
                 lsp: bool = False):
        self.directorio = str(Path(directorio).resolve())
        self.auto = bool(auto)
        # v6.10.0: modo navegador (--browser). Activa las herramientas de
        # Playwright; si está inactivo, las herramientas dan error claro.
        self.browser = bool(browser)
        # v6.14.0: herramientas LSP (--lsp). Si están inactivas, dan error
        # claro; el cliente se lanza perezosamente en la primera consulta.
        self.lsp = bool(lsp)
        if self.browser:
            try:
                import mcp_tools_browser as btool
                btool.browser_activar()
            except Exception:                            # noqa: BLE001
                pass
        self.max_iteraciones = max(int(max_iter), 1)
        # v6.9.0: límite de historial (por defecto MAX_HISTORIAL_DEFAULT=20).
        self.max_historial = (
            max(int(max_historial), 1) if max_historial
            else _max_historial())
        self.historial: List[Dict[str, str]] = []
        self.proveedor = proveedor
        self.modelo = modelo
        # v6.11.0: Prompt Caching. None → se resuelve por entorno/config.json
        # dentro de _enviar_al_proveedor; True/False lo fuerza explícitamente.
        self.prompt_caching = prompt_caching
        # v5.5.0: Grafo de conocimiento (opcional; --graph-rag o env).
        self.graph_rag = bool(graph_rag)
        # v6.2.0: mostrar el razonamiento COMPLETO del modelo en cada turno
        # (--mostrar-razonamiento o env SNAPCONTEXT_MOSTRAR_RAZONAMIENTO).
        self.mostrar_razonamiento = bool(mostrar_razonamiento)
        # v6.4.0: persistencia Docker por sesión (--sandbox-session). El agente
        # crea la sesión al empezar y la destruye al terminar (éxito/aborto).
        self.sesion_docker = bool(sesion_docker)
        # v6.5.0: UI web interactiva (--web-interactive). Si está activa, cada
        # paso del bucle se emite por WebSocket (timeline Pensamiento→Acción→
        # Observación) sin ralentizar el agente (emisión nunca bloquea).
        self.web_interactive = bool(web_interactive)
        self._wi = None
        if self.web_interactive:
            try:
                import web.interactive as _wi
                self._wi = _wi
            except Exception:                        # noqa: BLE001
                self._wi = None
        # v6.12.0: si la TUI está activa (--tui), emite el timeline ReAct por
        # tui_hub (misma interfaz que web.interactive: enviar_paso_react y
        # enviar_estado), sin bloquear al agente.
        if self._wi is None:
            try:
                import tui_hub as _hub
                if _hub.esta_activo():
                    self._wi = _hub
            except Exception:                        # noqa: BLE001
                pass
        self._grafo: Optional[dict] = None
        self._accion_actual = ""    # v6.10.0: última acción solicitada
        # Herramientas disponibles: nombre → callable(argumentos) -> dict.
        self.herramientas: Dict[str, Callable[[dict], dict]] = {
            "editar_archivo": self._tool_editar_archivo,
            "ejecutar_comando": self._tool_ejecutar_comando,
            "buscar_codigo": self._tool_buscar_codigo,
            "ejecutar_pruebas": self._tool_ejecutar_pruebas,
            "leer_archivo": self._tool_leer_archivo,
            # v6.7.0: expansión MCP (bases de datos y APIs externas).
            "db_query": self._tool_db_query,
            "db_schema": self._tool_db_schema,
            "api_request": self._tool_api_request,
            "api_inspect": self._tool_api_inspect,
            # v6.10.0: navegador (Playwright), activado con --browser.
            "browser_abrir": self._tool_browser,
            "browser_screenshot": self._tool_browser,
            "browser_click": self._tool_browser,
            "browser_type": self._tool_browser,
            "browser_get_text": self._tool_browser,
            "browser_analizar_imagen": self._tool_browser,
            # v6.14.0: LSP (activado con --lsp; cliente perezoso).
            "lsp_definicion": self._tool_lsp_definicion,
            "lsp_referencias": self._tool_lsp_referencias,
            "lsp_tipo": self._tool_lsp_tipo,
        }
        if self.proveedor is None:
            try:
                config = sc.cargar_configuracion()
                self.proveedor = (config.get("provider")
                                  or os.environ.get("SNAPCONTEXT_PROVIDER")
                                  or sc.PROVEEDOR_DEFECTO)
            except Exception:                                # noqa: BLE001
                self.proveedor = sc.PROVEEDOR_DEFECTO
        self._mutex = threading.Lock()

    def _construir_prompt_sistema(self) -> str:
        """Prompt base: rol, herramientas y formato JSON estricto."""
        lista = ", ".join(self.ACCIONES_VALIDAS)
        return (
            "Eres un agente de software ingeniero dentro del proyecto "
            f"'{self.directorio}'. Trabajas con un bucle ReAct: en CADA turno "
            "razonas sobre la situación, eliges UNA acción y observas su "
            "resultado en el siguiente mensaje.\n\n"
            "HERRAMIENTAS DISPONIBLES:\n"
            "- editar_archivo(ruta, contenido): crea o sobrescribe un archivo "
            "del proyecto con `contenido` completo.\n"
            "- ejecutar_comando(comando): ejecuta un comando de shell en la "
            "raíz del proyecto (puede estar aislado en un sandbox Docker).\n"
            "- buscar_codigo(patron): busca el patrón (regex) en el código y "
            "devuelve archivo:línea.\n"
            "- ejecutar_pruebas(archivo=None): corre la suite de pruebas "
            "(opcionalmente un solo archivo).\n"
            "- leer_archivo(ruta): lee un archivo (truncado si es enorme).\n"
            # v6.10.0: navegador (solo con --browser; si no, dará error).
            "- browser_abrir(url, wait_for?, timeout?): abre una URL en el "
            "navegador headless.\n"
            "- browser_screenshot(url?, full_page?, selector?): captura de "
            "pantalla (base64) para VER la interfaz.\n"
            "- browser_click(selector) / browser_type(selector, texto): "
            "interactúa con la página.\n"
            "- browser_get_text(selector): extrae el texto de un elemento.\n"
            "- browser_analizar_imagen(imagen_base64, pregunta): análisis "
            "visual con un modelo de visión.\n"
            "- finalizar(resumen): termina cuando la tarea está completa.\n\n"
            f"Herramientas válidas: {lista}.\n\n"
            "FORMATO DE SALIDA OBLIGATORIO: UN ÚNICO objeto JSON válido, sin "
            "texto fuera del JSON, exactamente con estas claves:\n"
            '{"pensamiento": "<tu razonamiento breve>", '
            '"accion": "<nombre_herramienta>", "argumentos": {…}}\n'
            'Para finalizar usa "accion": "finalizar" con '
            '{"resumen": "<qué se hizo>".}\n'
            "Nunca inventes herramientas. Antes de editar un archivo, léelo. "
            "Después de editar, ejecuta las pruebas."
        )

    # ------------------------------------------------------------------
    # Llamada al LLM (hilo separado vía asyncio; con timeout)
    # ------------------------------------------------------------------
    def _llamada_sync(self, mensajes: List[dict]) -> str:
        return str(sc._enviar_al_proveedor(
            self.proveedor, self.modelo, mensajes,
            prompt_caching=self.prompt_caching))

    async def _llamada_async(self, mensajes: List[dict]) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._llamada_sync, mensajes)

    def _llamar_llm(self, mensajes: List[dict],
                    timeout: float = LLM_TIMEOUT_SEG) -> str:
        with self._mutex:
            try:
                return asyncio.run(asyncio.wait_for(
                    self._llamada_async(mensajes), timeout=timeout))
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"El LLM tardó más de {timeout}s") from exc


    # ------------------------------------------------------------------
    # Herramientas (acciones) — todas devuelven un dict de resultado
    # ------------------------------------------------------------------
    def _ruta_segura(self, ruta: str) -> Optional[Path]:
        """Resuelve `ruta` y garantiza que viva dentro del proyecto."""
        try:
            camino = Path(ruta)
            if not camino.is_absolute():
                camino = Path(self.directorio) / camino
            camino = camino.resolve()
            raiz = Path(self.directorio).resolve()
            if str(camino).startswith(str(raiz)):
                return camino
        except OSError:                                      # noqa: PERF203
            return None
        return None

    def _tool_editar_archivo(self, argumentos: dict) -> dict:
        """Crea/sobrescribe un archivo del proyecto y devuelve el diff."""
        ruta = str(argumentos.get("ruta", "")).strip()
        contenido = str(argumentos.get("contenido", ""))
        if not ruta:
            return {"ok": False, "error": "falta 'ruta'"}
        camino = self._ruta_segura(ruta)
        if camino is None:
            return {"ok": False,
                    "error": f"ruta fuera del proyecto: {ruta}"}
        try:
            original = (camino.read_text(encoding="utf-8", errors="replace")
                        if camino.exists() else "")
            camino.parent.mkdir(parents=True, exist_ok=True)
            camino.write_text(contenido, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        diff = sc._generar_parche(original, contenido, ruta)
        return {"ok": True, "ruta": ruta, "diff": diff or "(sin cambios)",
                "lineas": len(contenido.splitlines())}

    def _tool_ejecutar_comando(self, argumentos: dict) -> dict:
        """Ejecuta un comando de shell; respeta --sandbox automáticamente."""
        comando = str(argumentos.get("comando", "")).strip()
        if not comando:
            return {"ok": False, "error": "falta 'comando'"}
        codigo, stdout, stderr = sc._ejecutar_comando(
            comando, self.directorio, timeout=300)
        return {"ok": codigo == 0, "codigo": codigo,
                "stdout": stdout, "stderr": stderr}

    def _tool_db_query(self, argumentos: dict) -> dict:
        """Consulta SQL de SOLO LECTURA sobre la BD conectada (v6.7.0)."""
        try:
            import mcp_tools_db as dbt
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": f"mcp_tools_db no disponible: {exc}"}
        consulta = str(argumentos.get("consulta", "")).strip()
        if not consulta:
            return {"ok": False, "error": "falta 'consulta'"}
        auto = self.auto or bool(argumentos.get("auto", False))
        return dbt.db_query(consulta, auto=auto)

    def _tool_db_schema(self, argumentos: dict) -> dict:
        """Esquema de la base de datos conectada (v6.7.0)."""
        try:
            import mcp_tools_db as dbt
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": f"mcp_tools_db no disponible: {exc}"}
        return dbt.db_schema()

    def _tool_api_request(self, argumentos: dict) -> dict:
        """Petición HTTP externa (GET/POST/...) (v6.7.0)."""
        try:
            import mcp_tools_api as apit
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": f"mcp_tools_api no disponible: {exc}"}
        url = str(argumentos.get("url", "")).strip()
        if not url:
            return {"ok": False, "error": "falta 'url'"}
        return apit.api_request(
            url, metodo=str(argumentos.get("metodo", "GET")),
            headers=argumentos.get("headers") or {},
            body=str(argumentos.get("body", "") or ""))

    def _tool_api_inspect(self, argumentos: dict) -> dict:
        """Análisis de una API externa (status, tiempo, tamaño) (v6.7.0)."""
        try:
            import mcp_tools_api as apit
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": f"mcp_tools_api no disponible: {exc}"}
        url = str(argumentos.get("url", "")).strip()
        if not url:
            return {"ok": False, "error": "falta 'url'"}
        return apit.api_inspect(url)

    # ------------------------------------------------------------------
    # v6.10.0: herramientas de navegador (Playwright) — modo --browser
    # ------------------------------------------------------------------
    def _tool_browser(self, argumentos: dict, accion: str = "") -> dict:
        """Despacha una herramienta de navegador a mcp_tools_browser.

        Todas las herramientas comparten esta pasarela: el módulo valida el
        modo (--browser), la disponibilidad de Playwright y nunca lanza.
        """
        try:
            import mcp_tools_browser as btool
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False,
                    "error": f"mcp_tools_browser no disponible: {exc}"}
        # Determinar la herramienta por el nombre del método parcheado.
        if not accion:
            accion = self._accion_actual or ""
        args = dict(argumentos or {})
        if accion == "browser_abrir":
            return btool.browser_abrir(
                str(args.get("url", "")),
                wait_for=(str(args["wait_for"])
                          if args.get("wait_for") else None),
                timeout=int(args.get("timeout", 30) or 30))
        if accion == "browser_screenshot":
            resultado = btool.browser_screenshot(
                str(args.get("url", "") or ""),
                full_page=bool(args.get("full_page", False)),
                selector=(str(args["selector"])
                          if args.get("selector") else None))
            if resultado.get("ok") and self.modelo_soporta_vision():
                # v6.10.0: si hay visión, analizar la captura automáticamente.
                analisis = btool.browser_analizar_imagen(
                    resultado.get("imagen", ""),
                    "¿Ves algún error visual (CSS roto, elementos "
                    "superpuestos, textos cortados)? Descríbelo.")
                if analisis.get("ok"):
                    resultado["analisis"] = analisis.get("analisis", "")
            return resultado
        if accion == "browser_click":
            return btool.browser_click(str(args.get("selector", "")))
        if accion == "browser_type":
            return btool.browser_type(str(args.get("selector", "")),
                                      str(args.get("texto", "")))
        if accion == "browser_get_text":
            return btool.browser_get_text(str(args.get("selector", "")))
        if accion == "browser_analizar_imagen":
            return btool.browser_analizar_imagen(
                str(args.get("imagen_base64", "")),
                str(args.get("pregunta", "")))
        if accion == "browser_cerrar":
            return btool.browser_cerrar()
        return {"ok": False, "error": f"acción de navegador desconocida: "
                                      f"{accion}"}

    def modelo_soporta_vision(self) -> bool:
        """True si el proveedor/modelo activo soporta imágenes (v6.10.0)."""
        try:
            import mcp_tools_browser as btool
            return btool.modelo_soporta_vision(self.proveedor, self.modelo)
        except Exception:                        # noqa: BLE001
            return False

    # Pasarelas por acción: `_ejecutar_accion` resuelve `_tool_{accion}`.
    def _tool_browser_abrir(self, argumentos: dict) -> dict:
        return self._tool_browser(argumentos, accion="browser_abrir")

    def _tool_browser_screenshot(self, argumentos: dict) -> dict:
        return self._tool_browser(argumentos, accion="browser_screenshot")

    def _tool_browser_click(self, argumentos: dict) -> dict:
        return self._tool_browser(argumentos, accion="browser_click")

    def _tool_browser_type(self, argumentos: dict) -> dict:
        return self._tool_browser(argumentos, accion="browser_type")

    def _tool_browser_get_text(self, argumentos: dict) -> dict:
        return self._tool_browser(argumentos, accion="browser_get_text")

    def _tool_browser_analizar_imagen(self, argumentos: dict) -> dict:
        return self._tool_browser(argumentos,
                                  accion="browser_analizar_imagen")

    def _tool_browser_cerrar(self, argumentos: dict) -> dict:
        return self._tool_browser(argumentos, accion="browser_cerrar")

    def _tool_lsp_definicion(self, argumentos: dict) -> dict:
        return self._tool_lsp(argumentos, accion="lsp_definicion")

    def _tool_lsp_referencias(self, argumentos: dict) -> dict:
        return self._tool_lsp(argumentos, accion="lsp_referencias")

    def _tool_lsp_tipo(self, argumentos: dict) -> dict:
        return self._tool_lsp(argumentos, accion="lsp_tipo")

    def _tool_lsp(self, argumentos: dict, accion: str = "") -> dict:
        """v6.14.0: herramientas LSP (definición/referencias/tipo).

        El cliente LSP se lanza perezosamente en la primera consulta; si el
        servidor no está disponible se devuelve un error claro y el agente
        continúa con el resto de herramientas.
        """
        if not self.lsp:
            return {"ok": False,
                    "error": "LSP inactivo: arranca con --lsp o "
                             "SNAPCONTEXT_LSP=1 para usar lsp_*."}
        import lsp_client as lc                  # noqa: E402
        archivo = str(argumentos.get("archivo", "")).strip()
        try:
            linea = int(argumentos.get("linea", 0))
            columna = int(argumentos.get("columna", 0))
        except (TypeError, ValueError):
            return {"ok": False, "error": "'linea'/'columna' deben ser "
                                          "enteros (1-based)."}
        if not archivo or linea < 1 or columna < 1:
            return {"ok": False,
                    "error": "se requiere archivo, linea y columna "
                             "(1-based)."}
        cliente = lc.obtener_cliente_lsp(self.directorio)
        if cliente is None:
            return {"ok": False,
                    "error": "LSP no disponible para este lenguaje."}
        try:
            if accion == "lsp_definicion":
                r = cliente.obtener_definicion(archivo, linea, columna)
                if r:
                    sc.info(f"LSP: definición encontrada en "
                            f"{r['archivo']}:{r['linea']}")
            elif accion == "lsp_referencias":
                r = cliente.obtener_referencias(archivo, linea, columna)
            else:
                r = cliente.obtener_tipo(archivo, linea, columna)
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": f"LSP falló: {exc}"}
        if r is None:
            return {"ok": False,
                    "error": "LSP no devolvió resultados para esa posición."}
        return {"ok": True, **r}

    def _tool_buscar_codigo(self, argumentos: dict) -> dict:
        """Busca una regex en los archivos de texto del proyecto."""
        patron = str(argumentos.get("patron", "")).strip()
        if not patron:
            return {"ok": False, "error": "falta 'patron'"}
        try:
            regex = re.compile(patron)
        except re.error as exc:
            return {"ok": False, "error": f"regex inválida: {exc}"}
        ignorados = {".git", "__pycache__", ".venv", "venv", "node_modules",
                     ".dart_tool", "build", "dist", ".idea"}
        hallazgos: List[str] = []
        raiz = Path(self.directorio)
        for camino in sorted(raiz.rglob("*")):
            if any(part in ignorados for part in camino.parts):
                continue
            if not camino.is_file() or camino.stat().st_size > 1_000_000:
                continue
            try:
                texto = camino.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for numero, linea in enumerate(texto.splitlines(), 1):
                if regex.search(linea):
                    rel = camino.relative_to(raiz).as_posix()
                    hallazgos.append(f"{rel}:{numero}: {linea.strip()[:160]}")
                    if len(hallazgos) >= 50:
                        break
            if len(hallazgos) >= 50:
                break
        resultado = {"ok": True, "total": len(hallazgos),
                     "coincidencias": hallazgos}
        # v5.5.0 (Graph RAG): añade archivos relacionados por dependencias.
        if self.graph_rag and hallazgos:
            afectados = sorted({h.split(":", 1)[0] for h in hallazgos
                                if h.split(":", 1)[0].endswith(".py")})
            relacionados = self._expander_con_grafo(afectados)
            if len(relacionados) > len(afectados):
                # Contexto expandido: originales + vecinos del grafo.
                resultado["archivos_relacionados"] = relacionados
        return resultado

    def _grafo_del_proyecto(self) -> Optional[dict]:
        """Grafo del proyecto (construido una vez, perezosamente)."""
        if not self.graph_rag:
            return None
        if self._grafo is None:
            try:
                import graph_rag as gr               # noqa: E402
                self._grafo = gr.construir_grafo(self.directorio)
            except Exception:                        # noqa: BLE001
                self._grafo = {}
        return self._grafo or None

    def _expander_con_grafo(self, archivos: List[str],
                            max_adicionales: int = 3) -> List[str]:
        """v5.5.0: amplía ``archivos`` con vecinos del grafo (best-effort)."""
        if not self.graph_rag or not archivos:
            return archivos
        try:
            import graph_rag as gr                   # noqa: E402
            grafo = self._grafo_del_proyecto()
            if not grafo:
                return archivos
            return gr.expandir_contexto(archivos, grafo,
                                        max_adicionales=max_adicionales)
        except Exception:                            # noqa: BLE001
            return archivos

    def _tool_ejecutar_pruebas(self, argumentos: dict) -> dict:
        """Corre la suite de pruebas.

        v5.3.0: el comando se resuelve por prioridad:
          1) ``comando`` explícito pasado como argumento;
          2) variable de entorno ``SNAPCONTEXT_COMANDO_TEST``;
          3) detección automática con ``detector_tests``;
          4) si hay un ``archivo`` python, pytest para ese archivo;
          5) por defecto ``sc.COMANDO_TEST_DEFECTO`` (compatibilidad).

        Si nada se detecta, se usa ``sc.COMANDO_TEST_DEFECTO`` (compatibilidad
        con el comportamiento histórico). Queda un retorno defensivo de error
        por si en el futuro no hubiera ningún comando disponible.
        """
        archivo = str(argumentos.get("archivo") or "").strip()
        comando = str(argumentos.get("comando") or "").strip()

        if not comando:
            comando = os.environ.get("SNAPCONTEXT_COMANDO_TEST", "").strip()
        if not comando:
            comando = det.detectar_automaticamente(self.directorio)["comando"] or ""
        if not comando and archivo and archivo.endswith(".py"):
            comando = f"{Path(sys.executable).name} -m pytest -q {archivo}"
        if not comando:
            # Backwards compatible: usar el comando por defecto histórica.
            comando = sc.COMANDO_TEST_DEFECTO

        if not comando:
            return {"ok": False,
                    "error": "No se pudo detectar automáticamente el comando de "
                             "test. Por favor, especifica uno manualmente."}
        codigo, stdout, stderr = sc._ejecutar_comando(
            comando, self.directorio, timeout=600)
        return {"ok": codigo == 0, "codigo": codigo, "comando": comando,
                "stdout": stdout, "stderr": stderr}

    def _tool_leer_archivo(self, argumentos: dict) -> dict:
        """Lee un archivo del proyecto (truncado a 8 KB)."""
        ruta = str(argumentos.get("ruta", "")).strip()
        if not ruta:
            return {"ok": False, "error": "falta 'ruta'"}
        camino = self._ruta_segura(ruta)
        texto = None
        if camino is not None:
            try:
                texto = camino.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
        else:
            texto = sc._leer_archivo(ruta)
        if texto is None:
            return {"ok": False, "error": f"no se pudo leer: {ruta}"}
        truncado = len(texto) > 8000
        return {"ok": True, "ruta": ruta,
                "contenido": texto[:8000]
                + ("\n…(truncado)" if truncado else "")}

    # ------------------------------------------------------------------
    # Parseo de la respuesta del LLM (con reintentos correctivos)
    # ------------------------------------------------------------------
    @staticmethod
    def _extraer_json(texto: str) -> Optional[dict]:
        """Extrae el primer objeto JSON válido (tolera ```json fences)."""
        if not texto:
            return None
        limpio = re.sub(r"```(?:json)?", "", texto).strip().strip("`").strip()
        candidatos = [limpio]
        inicio = limpio.find("{")
        fin = limpio.rfind("}")
        if 0 <= inicio < fin:
            candidatos.append(limpio[inicio:fin + 1])
        for candidato in candidatos:
            try:
                dato = json.loads(candidato)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(dato, dict):
                return dato
        return None

    def _pedir_decision(self, mensajes: List[dict]) -> Optional[dict]:
        """Pide un turno al LLM; reintenta hasta MAX_REINTENTOS_JSON veces."""
        correctivo = (
            "Tu respuesta anterior NO fue JSON válido. Responde de nuevo "
            "con UN ÚNICO objeto JSON válido con las claves 'pensamiento', "
            "'accion' y 'argumentos', sin texto adicional.")
        intento = 0
        while intento < MAX_REINTENTOS_JSON:
            bruto = self._llamar_llm(mensajes)
            # v6.2.0: conservar la respuesta bruta para poder mostrar el
            # razonamiento completo (<think>…) cuando el flag está activo.
            self._ultima_respuesta_bruta = bruto
            decision = self._extraer_json(bruto)
            if decision is not None and ("accion" in decision or "action"
                                         in decision):
                accion = str(decision.get("accion")
                             or decision.get("action") or "").strip()
                decision["accion"] = accion
                decision["pensamiento"] = str(
                    decision.get("pensamiento")
                    or decision.get("thought") or "")
                decision["argumentos"] = (
                    decision.get("argumentos") or decision.get("args") or {})
                if not isinstance(decision["argumentos"], dict):
                    decision["argumentos"] = {}
                return decision
            mensajes = mensajes + [{"role": "assistant", "content": bruto},
                                   {"role": "user", "content": correctivo}]
            intento += 1
        return None

    @staticmethod
    def _observar_resultado(resultado: dict) -> str:
        """Convierte el resultado crudo en un mensaje legible para el LLM."""
        partes = ["ok" if resultado.get("ok") else "FALLO"]
        for clave in ("ruta", "comando", "codigo", "total", "lineas",
                      "url", "titulo"):
            if clave in resultado:
                partes.append(f"{clave}={resultado[clave]}")
        mensaje = " | ".join(partes)
        for clave in ("error", "stderr"):
            valor = str(resultado.get(clave) or "").strip()
            if valor:
                mensaje += f"\n{clave}: {valor[:1200]}"
        for clave in ("stdout", "coincidencias", "contenido", "diff",
                      "texto", "analisis"):
            valor = resultado.get(clave)
            if isinstance(valor, list):
                valor = "\n".join(str(v) for v in valor)
            if valor:
                mensaje += f"\n{clave}:\n{str(valor)}"
        if len(mensaje) > MAX_SALIDA_OBSERVACION:
            mensaje = (mensaje[:MAX_SALIDA_OBSERVACION]
                       + "\n…(salida truncada)")
        return mensaje

    # ------------------------------------------------------------------
    # Control de contexto: resumen automático del historial
    # ------------------------------------------------------------------
    def _tokens_historial(self) -> int:
        return sum(estimar_tokens(m.get("content", ""))
                   for m in self.historial)

    def _resumir_si_hace_falta(self) -> bool:
        """Si el historial supera el umbral de tokens, lo comprime a un resumen."""
        if self._tokens_historial() <= _umbral_resumen():
            return False
        self._comprimir_historial()
        return True

    def _resumir_por_longitud(self) -> bool:
        """Si el historial supera ``max_historial`` iteraciones, lo comprime.

        v6.9.0: cada iteración añade ~2 entradas al historial; al superar el
        límite configurable (por defecto 20 iteraciones) se genera un resumen
        LLM para acotar el uso de memoria en sesiones largas.
        """
        if len(self.historial) <= 2 * self.max_historial:
            return False
        self._comprimir_historial()
        return True

    def _comprimir_historial(self) -> None:
        """Comprime ``self.historial`` a un resumen LLM del trabajo previo."""
        if not self.historial:
            return
        sistema = self.historial[0]
        cuerpo = self.historial[1:]
        pedido = [
            {"role": "system",
             "content": "Resume en español, en máximo 500 palabras y en "
                        "tercera persona, todo este trabajo del agente: "
                        "decisiones tomadas, archivos tocados, comandos "
                        "ejecutados y resultados de las pruebas. Conserva "
                        "los datos imprescindibles para continuar."},
            {"role": "user",
             "content": json.dumps(cuerpo, ensure_ascii=False)[:60000]},
        ]
        try:
            resumen = self._llamar_llm(pedido, timeout=180)
        except Exception:                                # noqa: BLE001
            resumen = (cuerpo[-1].get("content", "") if cuerpo else "")
        nuevo_historial = [sistema, {
            "role": "user",
            "content": f"[RESUMEN DEL TRABAJO PREVIO]\n{resumen}\n\n"
                       "[FIN DEL RESUMEN] Continúa la tarea.",
        }]
        # v6.11.0: si el proveedor soporta Prompt Caching, se preservan las
        # marcas cache_control en el resumen para mantener el ahorro de tokens
        # entre turnos (el sistema ya se marca al enviarse).
        try:
            if (sc._soporta_prompt_caching(self.proveedor)
                    and sc._resolver_prompt_caching(self.prompt_caching)):
                for _m in nuevo_historial:
                    _m.setdefault("cache_control", {"type": "ephemeral"})
        except Exception:                                # noqa: BLE001
            pass
        self.historial = nuevo_historial

    # ------------------------------------------------------------------
    # Bucle principal ReAct
    # ------------------------------------------------------------------
    def ejecutar(self, consulta: str) -> dict:
        """Ejecuta el bucle pensamiento → acción → observación.

        Devuelve ``{"ok", "resultado", "iteraciones", "abortado"}``.
        """
        self.historial = [
            {"role": "system", "content": self._construir_prompt_sistema()},
            {"role": "user", "content": f"TAREA: {consulta}"},
        ]
        sandbox_txt = ", sandbox 🐳" if getattr(sc, "_SANDBOX_ACTIVO", False) \
            else ""
        sc.info(f"🧠 Modo ReAct activado ({self.proveedor}, máx. "
                f"{self.max_iteraciones} iteraciones{sandbox_txt}).")
        # v6.11.0: informa del estado del Prompt Caching al inicio de la sesión.
        _msg_cache = sc._mensaje_caching_inicio(self.proveedor)
        if _msg_cache:
            sc.info(_msg_cache)
        # v6.4.0: si se pidió --sandbox-session, iniciar la sesión Docker
        # persistente (un único contenedor reutilizado para toda la tarea).
        if self.sesion_docker and getattr(sc, "_SANDBOX_ACTIVO", False):
            try:
                sc._asegurar_sesion_docker(self.directorio)
            except Exception as exc:                     # noqa: BLE001
                sc.aviso(f"[ReAct] No se pudo iniciar la sesión Docker: {exc}")
        try:
            for iteracion in range(1, self.max_iteraciones + 1):
                self._resumir_si_hace_falta()
                try:
                    decision = self._pedir_decision(list(self.historial))
                except Exception as exc:                 # noqa: BLE001
                    return {"ok": False, "resultado": f"error del LLM: {exc}",
                            "iteraciones": iteracion - 1, "abortado": True}
                if decision is None:
                    return {"ok": False,
                            "resultado": "el LLM no devolvió JSON válido tras "
                                         f"{MAX_REINTENTOS_JSON} reintentos",
                            "iteraciones": iteracion, "abortado": True}
                accion = decision["accion"]
                _wi = self._wi
                if _wi is not None:
                    _wi.enviar_paso_react(iteracion, "pensamiento",
                                          decision.get("pensamiento", ""))
                    _wi.enviar_estado("Pensando...",
                                      decision.get("pensamiento", "")[:200])
                if self.mostrar_razonamiento:
                    # v6.2.0: razonamiento COMPLETO del modelo (no el resumido).
                    _raz = sc._extraer_razonamiento(
                        getattr(self, "_ultima_respuesta_bruta", "")) \
                        or decision["pensamiento"]
                    ui.mostrar_razonamiento(_raz)
                sc.info(f"[{iteracion}/{self.max_iteraciones}] 💭 "
                        f"{decision['pensamiento'][:200]}")
                if accion == "finalizar":
                    resumen = str(decision["argumentos"].get("resumen", "")
                                  or decision["pensamiento"] or "Tarea finalizada.")
                    sc.exito("🏁 ReAct: " + resumen[:300])
                    return {"ok": True, "resultado": resumen,
                            "iteraciones": iteracion, "abortado": False}
                if not self.auto:
                    respuesta = self._preguntar_continuar(decision)
                    if respuesta == "a":
                        return {"ok": False,
                                "resultado": "abortado por el usuario",
                                "iteraciones": iteracion, "abortado": True}
                    if respuesta == "s":
                        self.historial.append({
                            "role": "user",
                            "content": f"[OBSERVACIÓN] Acción '{accion}' omitida "
                                       "por el usuario."})
                        continue
                resultado_accion = self._ejecutar_accion(
                    accion, decision["argumentos"])
                if self._wi is not None:
                    self._wi.enviar_paso_react(iteracion, "accion", accion,
                                               argumentos=json.dumps(
                                                   decision.get("argumentos",
                                                                {}),
                                                   ensure_ascii=False)[:2000])
                    self._wi.enviar_estado("Ejecutando acción", accion)
                if resultado_accion is None:
                    observacion = (f"ACCION DESCONOCIDA: '{accion}'. Válidas: "
                                   f"{', '.join(sorted(self.herramientas))}.")
                    ok_accion = False
                else:
                    observacion = self._observar_resultado(resultado_accion)
                    ok_accion = bool(resultado_accion.get("ok"))
                icono = "✅" if ok_accion else "⚠️"
                sc.info(f"   {icono} Acción: {accion}")
                sc.exito(observacion.splitlines()[0])
                if self._wi is not None:
                    self._wi.enviar_paso_react(
                        iteracion, "error" if not ok_accion else "observacion",
                        observacion[:4000])
                self.historial.append({"role": "assistant",
                                       "content": json.dumps(
                                           decision, ensure_ascii=False)})
                self.historial.append({
                    "role": "user",
                    "content": f"[OBSERVACIÓN de '{accion}']\n{observacion}"})
                # v6.9.0: si el historial acumulado supera max_historial
                # iteraciones, se resume automáticamente (acota la memoria).
                if self._resumir_por_longitud():
                    sc.info(f"[ReAct] Historial resumido tras superar "
                            f"{self.max_historial} iteraciones de contexto.")
            return {"ok": False,
                    "resultado": f"Límite de {self.max_iteraciones} iteraciones "
                                 "alcanzado sin finalizar.",
                    "iteraciones": self.max_iteraciones, "abortado": False}
        finally:
            # v6.4.0: destruir la sesión al terminar (éxito, aborto o excepción).
            if self.sesion_docker:
                try:
                    sc._destruir_sesion_si_aplica()
                except Exception:                        # noqa: BLE001
                    pass
            # v6.10.0: cerrar el navegador (sesión persistente por tarea).
            if self.browser:
                try:
                    import mcp_tools_browser as btool
                    btool.browser_cerrar()
                except Exception:                        # noqa: BLE001
                    pass

    def _preguntar_continuar(self, decision: dict) -> str:
        """Pregunta continuar/abortar/saltar en modo interactivo."""
        from ui import preguntar_interactivo                     # noqa: E402
        return preguntar_interactivo(
            [("c", "Continuar"), ("a", "Abortar"),
             ("s", "Saltar esta acción")],
            f"Acción propuesta: '{decision['accion']}'. ¿Continuar?",
            defecto="c")

    # ------------------------------------------------------------------
    # Ejecución y observación
    # ------------------------------------------------------------------
    def _ejecutar_accion(self, accion: str,
                         argumentos: dict) -> Optional[dict]:
        """Mapea la acción del LLM a una herramienta real.

        Devuelve el dict de resultado, o ``None`` si la acción no existe.
        """
        # Resolución dinámica por nombre: permite que los tests parcheen
        # las herramientas a nivel de clase (_tool_*). v6.10.0: se registra
        # la acción actual (usada por la pasarela _tool_browser) y se cae
        # al mapeo self.herramientas si no existe el método _tool_<accion>.
        if accion not in self.herramientas:
            return None
        self._accion_actual = accion
        try:
            metodo = getattr(self, f"_tool_{accion}", None)
            if metodo is None:
                metodo = self.herramientas[accion]
            resultado = metodo(argumentos or {})
        except Exception as exc:                         # noqa: BLE001
            resultado = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return resultado if isinstance(resultado, dict) else {"ok": True}
