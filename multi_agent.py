#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sistema multi-agente de SnapContext — v6.0.0.

Un **Supervisor** coordina a un equipo de agentes especializados que trabajan
sobre una tarea de desarrollo:

  🧠 ``Arquitecto``  : usa el LLM para diseñar la solución (plan en JSON con
                       objetivo, módulos y archivos afectados).
  💻 ``Programador`` : implementa el código usando el editor propio de
                       SnapContext (cadena AST → parche → sobrescritura).
  🧪 ``Tester``      : ejecuta las pruebas (detección automática de v5.3.0)
                       y devuelve éxito/fallo con la salida.

Los agentes se comunican a través de un bu‌zón de mensajes (``Buzon``). En esta
primera versión el flujo es un *pipeline* secuencial: el Arquitecto pasa el
plan al Programador, este pasa el código al Tester y el Tester devuelve los
resultados al Supervisor. Si una prueba falla, el Supervisor realimenta el
error al Programador (bucle de retroalimentación) hasta lograr éxito o agotar
``max_reintentos``.

Se activa con ``--multi-agent`` o la variable ``SNAPCONTEXT_MULTI_AGENT=1``.
Es completamente opcional: sin él, SnapContext se comporta exactamente igual.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


def multi_agent_activo(flag: Optional[bool] = None) -> bool:
    """True si el modo multi-agente debe activarse.

    Prioridad: flag explícito (``--multi-agent``) > ``SNAPCONTEXT_MULTI_AGENT=1``.
    """
    if flag is not None:
        return bool(flag)
    return os.environ.get("SNAPCONTEXT_MULTI_AGENT", "").strip() == "1"


def _proveedor_efectivo(proveedor: Optional[str] = None) -> str:
    """Proveedor por defecto de SnapContext (config > env > PROVEEDOR_DEFECTO)."""
    import snapcontext as sc
    try:
        cfg = sc.cargar_configuracion()
    except Exception:                                    # noqa: BLE001
        cfg = {}
    return (proveedor or cfg.get("provider")
            or os.environ.get("SNAPCONTEXT_PROVIDER")
            or getattr(sc, "PROVEEDOR_DEFECTO", "gemini"))


def _llamar_llm(proveedor: str, modelo: Optional[str],
                mensajes: List[Dict[str, str]]) -> str:
    """Envía ``mensajes`` al proveedor de IA y devuelve el texto de respuesta."""
    import snapcontext as sc
    return str(sc._enviar_al_proveedor(proveedor, modelo, mensajes))


def _extraer_json_objeto(texto: Optional[str]) -> Optional[dict]:
    """Extrae el primer objeto JSON válido de ``texto`` (tolera ```json```)."""
    if not texto:
        return None
    limpio = re.sub(r"```(?:json)?", "", str(texto)).strip().strip("`").strip()
    inicio = limpio.find("{")
    fin = limpio.rfind("}")
    if inicio == -1 or fin == -1 or fin <= inicio:
        return None
    try:
        datos = json.loads(limpio[inicio:fin + 1])
    except json.JSONDecodeError:
        return None
    return datos if isinstance(datos, dict) else None


class Buzon:
    """Buzón de mensajes entre agentes (thread-safe, FIFO).

    Cada mensaje es un dict ``{remitente, tipo, contenido}``. Es el canal de
    comunicación entre los roles del equipo multi-agente.
    """

    def __init__(self) -> None:
        self._cola: "queue.Queue[dict]" = queue.Queue()
        self._mutex = threading.Lock()
        self._historial: List[dict] = []

    def publicar(self, remitente: str, tipo: str, contenido: Any) -> None:
        """Publica un mensaje ``(remitente, tipo, contenido)`` en el buzón."""
        mensaje = {"remitente": remitente, "tipo": tipo, "contenido": contenido}
        with self._mutex:
            self._historial.append(dict(mensaje))
        self._cola.put(mensaje)

    def recibir(self) -> Optional[dict]:
        """Extrae el mensaje más antiguo (o ``None`` si el buzón está vacío)."""
        try:
            return self._cola.get_nowait()
        except queue.Empty:                              # noqa: PERF203
            return None

    def vaciar(self) -> List[dict]:
        """Vacía el buzón y devuelve los mensajes pendientes."""
        pendientes: List[dict] = []
        while True:
            mensaje = self.recibir()
            if mensaje is None:
                break
            pendientes.append(mensaje)
        return pendientes

    def historial(self) -> List[dict]:
        """Todos los mensajes publicados hasta ahora (solo lectura)."""
        with self._mutex:
            return list(self._historial)


class Arquitecto:
    """🧠 Diseña la solución: genera un plan en JSON con el LLM."""

    ROL = "arquitecto"

    def __init__(self, proveedor: Optional[str] = None,
                 modelo: Optional[str] = None,
                 buzon: Optional[Buzon] = None) -> None:
        self.proveedor = proveedor or _proveedor_efectivo(proveedor)
        self.modelo = modelo
        self.buzon = buzon

    def generar_plan(self, tarea: str, directorio: str = ".") -> Dict[str, Any]:
        """Genera un plan detallado (dict) a partir de la tarea.

        Estructura del plan: ``{objetivo, descripcion, modulos, archivos,
        pasos, dependencias}``. Si el LLM no devuelve JSON válido se degrada a
        un plan mínimo con la tarea, de modo que el flujo nunca se rompe.
        Publica el plan en el buzón (si existe) y lo devuelve.
        """
        import snapcontext as sc
        sc.info("🧠 Arquitecto: generando plan...")
        mensajes = [
            {"role": "system",
             "content": (
                 "Eres el Arquitecto de un equipo de desarrollo multi-agente. "
                 "Diseñas la solución de alto nivel de la tarea. Devuelve SOLO "
                 "un objeto JSON válido (sin texto fuera) con esta estructura:\n"
                 "{\n"
                 '  "objetivo": "meta principal",\n'
                 '  "descripcion": "explicación breve del enfoque",\n'
                 '  "modulos": ["módulo1", "módulo2"],\n'
                 '  "archivos": ["ruta/a/archivo1.ext", "ruta/a/archivo2.ext"],\n'
                 '  "pasos": [{"descripcion": "...", "accion": '
                 '"editar|crear|ejecutar", "archivos": ["..."]}],\n'
                 '  "dependencias": ["archivo que debe existir antes"]\n'
                 "}\n"
                 "La clave 'archivos' debe listar las rutas concretas a tocar.")},
            {"role": "user",
             "content": f"TAREA: {tarea}\nDIRECTORIO: {directorio}\n\n"
                        "Genera el plan."},
        ]
        texto = _llamar_llm(self.proveedor, self.modelo, mensajes)
        datos = _extraer_json_objeto(texto)
        if datos is None:
            datos = {
                "objetivo": tarea,
                "descripcion": (str(texto)[:400] if texto else tarea),
                "modulos": [],
                "archivos": [],
                "pasos": [{"descripcion": tarea, "accion": "editar",
                           "archivos": []}],
                "dependencias": [],
            }
        datos.setdefault("objetivo", tarea)
        datos.setdefault("descripcion", "")
        datos.setdefault("modulos", [])
        datos.setdefault("archivos", [])
        datos.setdefault("pasos", [])
        datos.setdefault("dependencias", [])
        if self.buzon is not None:
            self.buzon.publicar(self.ROL, "plan", datos)
        return datos


class Programador:
    """💻 Implementa el código siguiendo el plan usando el editor propio."""

    ROL = "programador"

    def __init__(self, buzon: Optional[Buzon] = None,
                 modelo: Optional[str] = None,
                 proveedor: Optional[str] = None) -> None:
        self.buzon = buzon
        self.modelo = modelo
        self.proveedor = proveedor

    @staticmethod
    def _mensaje_implementacion(tarea: str, plan: Dict[str, Any],
                                intento: int, error_msj: str = "") -> str:
        """Crea el mensaje que recibe el editor propio (tarea + plan + error)."""
        partes = [
            f"TAREA ORIGINAL: {tarea}\n",
            f"PLAN DEL ARQUITECTO: {json.dumps(plan, ensure_ascii=False)}",
        ]
        if intento > 1:
            partes.append(f"\nIntento #{intento}: corrige el código para que "
                          "pasen las pruebas.")
        if error_msj:
            partes.append(f"\nERROR A CORREGIR:\n{error_msj}")
        return "\n".join(partes)

    def implementar(self, tarea: str, plan: Dict[str, Any],
                    archivos: List[str], directorio: str = ".",
                    intento: int = 1, error_msj: str = "") -> Dict[str, Any]:
        """Aplica los cambios con ``agentes.AgenteEditorPropio``.

        Devuelve ``{"ok": bool, "archivos": [...], "intento": n}`` y publica el
        resultado en el buzón (si existe). Si el plan no define ``archivos`` ni
        se pasan, se considera un no-op exitoso (nada que editar).
        """
        import snapcontext as sc
        sc.info("💻 Programador: escribiendo código...")
        archivos = [a for a in (archivos or list(plan.get("archivos") or []))
                    if a]
        mensaje = self._mensaje_implementacion(tarea, plan, intento, error_msj)
        if not archivos:
            resultado = {"ok": True, "archivos": [], "intento": intento,
                         "sin_archivos": True}
        else:
            try:
                import agentes as ag
                editor = ag.AgenteEditorPropio()
                aplicado = editor.ejecutar(
                    archivos, mensaje, directorio=directorio,
                    modo_edicion="auto", modelo=self.modelo,
                    proveedor=self.proveedor, auto=True)
                resultado = {"ok": bool(aplicado), "archivos": archivos,
                             "intento": intento}
            except Exception as exc:                       # noqa: BLE001
                resultado = {"ok": False, "archivos": archivos,
                             "intento": intento, "error": str(exc)}
        if self.buzon is not None:
            self.buzon.publicar(self.ROL, "resultado_edicion", resultado)
        return resultado


class Tester:
    """🧪 Ejecuta las pruebas y devuelve éxito/fallo con la salida."""

    ROL = "tester"

    def __init__(self, buzon: Optional[Buzon] = None) -> None:
        self.buzon = buzon

    def _resolver_comando(self, directorio: str,
                          comando: Optional[str] = None) -> Optional[str]:
        """Comando de test: explícito > env > detección automática (v5.3.0)."""
        import detector_tests as det
        if comando and str(comando).strip():
            return str(comando).strip()
        env = os.environ.get("SNAPCONTEXT_COMANDO_TEST", "").strip()
        if env:
            return env
        return det.resolver_comando_test(directorio)

    def ejecutar(self, directorio: str = ".",
                 archivos: Optional[List[str]] = None,
                 comando: Optional[str] = None) -> Dict[str, Any]:
        """Ejecuta la suite de pruebas y devuelve el resumen.

        Devuelve ``{"ok", "codigo", "comando", "stdout", "stderr",
        "detectado"}``. Si no se puede detectar ningún comando de test, vuelve
        con ``ok=False`` y ``detectado=False`` (el Supervisor lo interpreta
        como "no hay pruebas en el proyecto").
        """
        import snapcontext as sc
        sc.info("🧪 Tester: ejecutando pruebas...")
        comando_resuelto = self._resolver_comando(directorio, comando)
        if not comando_resuelto:
            resultado = {"ok": False, "codigo": -1, "comando": None,
                         "stdout": "",
                         "stderr": "No se pudo detectar el comando de test "
                                   "en este proyecto.",
                         "detectado": False}
            if self.buzon is not None:
                self.buzon.publicar(self.ROL, "resultado_pruebas", resultado)
            return resultado
        codigo, stdout, stderr = sc._ejecutar_comando(
            comando_resuelto, directorio, timeout=600)
        resultado = {"ok": codigo == 0, "codigo": codigo,
                     "comando": comando_resuelto, "stdout": stdout,
                     "stderr": stderr, "detectado": True}
        if self.buzon is not None:
            self.buzon.publicar(self.ROL, "resultado_pruebas", resultado)
        return resultado
class Supervisor:
    """🤖 Coordina el equipo multi-agente (pipeline con realimentación)."""

    def __init__(self, directorio: str = ".", tarea: str = "",
                 auto: bool = False, proveedor: Optional[str] = None,
                 modelo: Optional[str] = None, max_reintentos: int = 3,
                 buzon: Optional[Buzon] = None,
                 archivos: Optional[List[str]] = None,
                 comando_test: Optional[str] = None,
                 sub_agents: bool = False, max_parallel: int = 3,
                 lsp: bool = False) -> None:
        self.directorio = str(Path(directorio).resolve())
        self.tarea = tarea
        self.auto = bool(auto)
        self.proveedor = proveedor
        self.modelo = modelo
        self.max_reintentos = max(int(max_reintentos), 1)
        self.buzon = buzon if buzon is not None else Buzon()
        self.archivos = list(archivos or [])
        self.comando_test = comando_test
        # v6.13.0: sub-agentes dinámicos (--sub-agents / --max-parallel).
        self.sub_agents = bool(sub_agents)
        self.max_parallel = max(1, int(max_parallel))
        self.lsp = bool(lsp)
        self.sub_agentes: List[Any] = []

    # ------------------------------------------------------------------
    # Sub-agentes dinámicos (v6.13.0)
    # ------------------------------------------------------------------
    def crear_sub_agente(self, rol: str, consulta: str = "",
                         browser: bool = False) -> Any:
        """Instancia un ``SubAgente`` del rol solicitado y lo registra."""
        import snapcontext as sc                       # noqa: E402
        from sub_agent import SubAgente                # noqa: E402
        sub = SubAgente(rol, directorio=self.directorio,
                        proveedor=self.proveedor, modelo=self.modelo,
                        buzon=self.buzon, auto=self.auto, browser=browser,
                        lsp=self.lsp)
        self.sub_agentes.append(sub)
        if consulta:
            sub.enviar_mensaje(consulta)
        sc.info(f"Sub-agente '{rol}' instanciado.")
        return sub

    @staticmethod
    def _detectar_sub_tareas(plan: Dict[str, Any]) -> List[dict]:
        """Detecta pasos del plan delegables a roles de sub-agente.

        Heurística por palabras clave sobre la descripción de cada paso del
        plan (y del objetivo). Devuelve especificaciones ``{rol, consulta}``
        sin duplicados, en orden de aparición.
        """
        from sub_agent import ROLES                    # noqa: E402
        claves: Dict[str, tuple] = {
            "scout": ("documentaci", "investiga", "busca", "explora",
                      "revisa la api", "leer", "estudia"),
            "debugger": ("error", "fallo", "bug", "excepci", "depura",
                         "corrige el error"),
            "frontender": ("css", "html", "interfaz", "ui", "estilo",
                           "dise\u00f1o", "frontend"),
            "tester": ("prueba", "test", "verifica"),
            "documentador": ("documenta", "readme", "claude.md",
                             "comenta", "docstring"),
        }
        textos: List[str] = []
        objetivo = str(plan.get("objetivo") or "")
        if objetivo:
            textos.append(objetivo.lower())
        for paso in (plan.get("pasos") or []):
            if isinstance(paso, dict):
                desc = str(paso.get("descripcion") or "").lower()
            else:
                desc = str(paso).lower()
            if desc:
                textos.append(desc)
        especificaciones: List[dict] = []
        vistos = set()
        for texto in textos:
            for rol, palabras in claves.items():
                if rol in vistos:
                    continue
                if any(p in texto for p in palabras):
                    especificaciones.append({"rol": rol, "consulta": texto})
                    vistos.add(rol)
        return especificaciones

    def ejecutar_sub_tareas(self, plan: Dict[str, Any]) -> List[dict]:
        """Detecta y ejecuta en paralelo las sub-tareas delegables del plan.

        Solo actúa con ``--sub-agents``; en caso contrario devuelve ``[]``
        y el pipeline se comporta exactamente como antes. Los resultados se
        publican en el buzón como ``resultado_sub_agente``.
        """
        import snapcontext as sc                       # noqa: E402
        if not self.sub_agents:
            return []
        especificaciones = self._detectar_sub_tareas(plan)
        if not especificaciones:
            sc.info("Supervisor: no hay sub-tareas delegables a sub-agentes.")
            return []
        for espec in especificaciones:
            espec.setdefault("directorio", self.directorio)
            espec.setdefault("proveedor", self.proveedor)
            espec.setdefault("modelo", self.modelo)
            espec.setdefault("lsp", self.lsp)
        from sub_agent import ejecutar_sub_agentes_paralelo  # noqa: E402
        resultados = ejecutar_sub_agentes_paralelo(
            especificaciones, max_parallel=self.max_parallel,
            buzon=self.buzon)
        for r in resultados:
            self.buzon.publicar("supervisor", "sub_tarea_completada", r)
        return resultados

    # ------------------------------------------------------------------
    # Mostrar plan y confirmación
    # ------------------------------------------------------------------
    def _mostrar_plan(self, plan: Dict[str, Any]) -> None:
        import snapcontext as sc
        sc.exito("📋 Plan del Arquitecto:")
        ob = str(plan.get("objetivo") or "")
        sc.info("  Objetivo: " + ob)
        desc = str(plan.get("descripcion") or "")
        if desc and desc != ob:
            sc.info("  " + desc)
        archivos = plan.get("archivos") or []
        if archivos:
            sc.info("  Archivos a tocar: " + ", ".join(str(a) for a in archivos))

    def _confirmar_plan(self) -> bool:
        """Pide confirmación del plan en modo interactivo (--auto lo omite)."""
        if self.auto:
            return True
# ------------------------------------------------------------------
    # Pipeline principal
    # ------------------------------------------------------------------
    def ejecutar(self) -> Dict[str, Any]:
        """Pipeline Arquitecto → Programador → Tester (con realimentación).

        Devuelve ``{"ok", "plan", "reintentos", "resultados", "error"}``.
        """
        import snapcontext as sc
        if not self.tarea:
            return {"ok": False, "error": "La tarea está vacía.", "plan": {},
                    "reintentos": 0, "resultados": []}

        # 1) Arquitecto: plan de alto nivel.
        arquitecto = Arquitecto(proveedor=self.proveedor,
                                modelo=self.modelo, buzon=self.buzon)
        try:
            plan = arquitecto.generar_plan(self.tarea, self.directorio)
        except Exception as exc:                          # noqa: BLE001
            return {"ok": False, "error": f"El Arquitecto falló: {exc}",
                    "plan": {}, "reintentos": 0, "resultados": []}
        self._mostrar_plan(plan)

        # 2) Confirmación del plan (omita en --auto).
        if not self._confirmar_plan():
            sc.aviso("Plan cancelado por el usuario.")
            return {"ok": False, "error": "cancelado por el usuario",
                    "plan": plan, "reintentos": 0, "resultados": []}

        # 3) v6.13.0: sub-agentes dinámicos (solo con --sub-agents).
        self.resultados_sub_tareas = self.ejecutar_sub_tareas(plan)

        # 4) Bucle Programador → Tester con realimentación.
        resultados: List[Dict[str, Any]] = []
        archivos = list(self.archivos) or list(plan.get("archivos") or [])
        error_msj = ""
        for intento in range(1, self.max_reintentos + 1):
            programador = Programador(buzon=self.buzon, modelo=self.modelo,
                                      proveedor=self.proveedor)
            r_prog = programador.implementar(
                self.tarea, plan, archivos, self.directorio,
                intento=intento, error_msj=error_msj)
            resultados.append({"fase": "programador", "intento": intento,
                               "ok": bool(r_prog.get("ok"))})
            if not r_prog.get("ok"):
                error_msj = "El Programador no pudo aplicar los cambios según " \
                            "el plan."
                detalle = str(r_prog.get("error") or "")
                if detalle:
                    error_msj += " Detalle: " + detalle
                sc.aviso(f"💻 Programador: falló la edición (intento "
                         f"{intento}/{self.max_reintentos}).")
                continue

            tester = Tester(buzon=self.buzon)
            r_test = tester.ejecutar(self.directorio, archivos,
                                     self.comando_test)
            test_ok = bool(r_test.get("ok"))
            resultados.append({"fase": "tester", "intento": intento,
                               "ok": test_ok,
                               "comando": r_test.get("comando"),
                               "codigo": r_test.get("codigo")})
            if test_ok:
                sc.exito("🧪 Tester: pruebas en verde ✅")
                return {"ok": True, "plan": plan,
                        "reintentos": intento, "resultados": resultados,
                        "archivos": archivos}
            if r_test.get("detectado") is False:
                # No hay pruebas en el proyecto → se da por completado.
                sc.exito("🧪 Tester: sin pruebas detectadas; se da por "
                         "completado.")
                return {"ok": True, "plan": plan,
                        "reintentos": intento, "resultados": resultados,
                        "archivos": archivos, "sin_pruebas": True}
            error_msj = (str(r_test.get("stderr") or "")
                         or str(r_test.get("stdout") or "")
                         or f"Las pruebas fallaron (código "
                            f"{r_test.get('codigo')}).")
            sc.aviso(f"🧪 Tester: pruebas fallidas (intento "
                     f"{intento}/{self.max_reintentos}). Realimentando al "
                     "Programador...")

        return {"ok": False,
                "error": f"Las pruebas no pasaron tras "
                         f"{self.max_reintentos} reintento(s).",
                "plan": plan, "reintentos": self.max_reintentos,
                "resultados": resultados, "archivos": archivos,
                "detalle_test": error_msj}
        import ui
        opciones = [("e", "Ejecutar el plan"), ("c", "Cancelar")]
        eleccion = ui.preguntar_interactivo(
            opciones, "¿Quieres que el equipo ejecute este plan?", defecto="e")
        return eleccion == "e"