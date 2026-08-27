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


UMBRAL_RESUMEN_TOKENS_DEFAULT = 8000   # dispara resumen automático del historial
MAX_REINTENTOS_JSON = 3                # reintentos si el LLM no devuelve JSON válido
LLM_TIMEOUT_SEG = int(os.environ.get("REACT_LLM_TIMEOUT", "300"))
MAX_SALIDA_OBSERVACION = 4000          # caracteres máx. de una observación


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
    )

    def __init__(self, directorio: str = ".", auto: bool = False,
                 max_iter: int = 15, proveedor: Optional[str] = None,
                 modelo: Optional[str] = None):
        self.directorio = str(Path(directorio).resolve())
        self.auto = bool(auto)
        self.max_iteraciones = max(int(max_iter), 1)
        self.historial: List[Dict[str, str]] = []
        self.proveedor = proveedor
        self.modelo = modelo
        # Herramientas disponibles: nombre → callable(argumentos) -> dict.
        self.herramientas: Dict[str, Callable[[dict], dict]] = {
            "editar_archivo": self._tool_editar_archivo,
            "ejecutar_comando": self._tool_ejecutar_comando,
            "buscar_codigo": self._tool_buscar_codigo,
            "ejecutar_pruebas": self._tool_ejecutar_pruebas,
            "leer_archivo": self._tool_leer_archivo,
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
            self.proveedor, self.modelo, mensajes))

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
        return {"ok": True, "total": len(hallazgos),
                "coincidencias": hallazgos}

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
        for clave in ("ruta", "comando", "codigo", "total", "lineas"):
            if clave in resultado:
                partes.append(f"{clave}={resultado[clave]}")
        mensaje = " | ".join(partes)
        for clave in ("error", "stderr"):
            valor = str(resultado.get(clave) or "").strip()
            if valor:
                mensaje += f"\n{clave}: {valor[:1200]}"
        for clave in ("stdout", "coincidencias", "contenido", "diff"):
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
        """Si el historial supera el umbral, lo comprime a un resumen."""
        if self._tokens_historial() <= _umbral_resumen():
            return False
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
            resumen = cuerpo[-1].get("content", "")      # fallback seguro
        self.historial = [sistema, {
            "role": "user",
            "content": f"[RESUMEN DEL TRABAJO PREVIO]\n{resumen}\n\n"
                       "[FIN DEL RESUMEN] Continúa la tarea.",
        }]
        return True

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
        for iteracion in range(1, self.max_iteraciones + 1):
            self._resumir_si_hace_falta()
            try:
                decision = self._pedir_decision(list(self.historial))
            except Exception as exc:                     # noqa: BLE001
                return {"ok": False, "resultado": f"error del LLM: {exc}",
                        "iteraciones": iteracion - 1, "abortado": True}
            if decision is None:
                return {"ok": False,
                        "resultado": "el LLM no devolvió JSON válido tras "
                                     f"{MAX_REINTENTOS_JSON} reintentos",
                        "iteraciones": iteracion, "abortado": True}
            accion = decision["accion"]
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
            resultado_accion = self._ejecutar_accion(accion,
                                                     decision["argumentos"])
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
            self.historial.append({"role": "assistant",
                                   "content": json.dumps(
                                       decision, ensure_ascii=False)})
            self.historial.append({
                "role": "user",
                "content": f"[OBSERVACIÓN de '{accion}']\n{observacion}"})
        return {"ok": False,
                "resultado": f"Límite de {self.max_iteraciones} iteraciones "
                             "alcanzado sin finalizar.",
                "iteraciones": self.max_iteraciones, "abortado": False}

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
        # las herramientas a nivel de clase (_tool_*).
        if accion not in self.herramientas:
            return None
        try:
            resultado = getattr(self, f"_tool_{accion}")(argumentos or {})
        except Exception as exc:                         # noqa: BLE001
            resultado = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return resultado if isinstance(resultado, dict) else {"ok": True}
