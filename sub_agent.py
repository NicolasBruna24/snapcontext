#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sub-agentes dinámicos de SnapContext — v6.20.0.

Permite al Supervisor (``multi_agent.py``) instanciar agentes ReAct
especializados bajo demanda, cada uno con su propio historial (contexto
aislado), un rol predefinido con prompt y herramientas restringidas, un
buzón de mensajes y ejecución en paralelo controlada.

v6.20.0 añade:
  - ``SubAgentRegistry``: registro consultable y extensible de sub-agentes
    (``registrar`` / ``obtener`` / ``listar``) con un registro por defecto
    (scout, debugger, reviewer, documentador).
  - Configuración dinámica: ``SubAgente`` acepta un ``config`` propio, por lo
    que se pueden definir roles nuevos sin modificar ``ROLES``.
  - Prompts canónicos en ``sub_agent_prompts.py`` (importados aquí).

Se activa con ``--sub-agents`` junto a ``--multi-agent``. Sin el flag, el
comportamiento de SnapContext es exactamente el mismo de siempre.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from sub_agent_prompts import PROMPTS as _PROMPTS

# ---------------------------------------------------------------------------
# Roles predefinidos
# ---------------------------------------------------------------------------
ROLES: Dict[str, Dict[str, Any]] = {
    "scout": {
        "emoji": "SCOUT",
        "descripcion": "Lee documentación, busca información y explora el código.",
        "prompt": (
            "Eres Scout, un sub-agente investigador. Tu única misión es "
            "LEER y RECOPILAR información (documentación, código, APIs) y "
            "devolver un resumen claro y factual. NO edites archivos ni "
            "ejecutes comandos que modifiquen el proyecto."),
        "herramientas": ["leer_archivo", "buscar_codigo", "api_inspect",
                         "browser_get_text", "finalizar"],
        "max_iter": 8,
    },
    "debugger": {
        "emoji": "DEBUGGER",
        "descripcion": "Analiza errores y logs, y diagnostica soluciones.",
        "prompt": (
            "Eres Debugger, un sub-agente especializado en diagnosticar "
            "errores. Analiza logs, trazas y código para localizar la causa "
            "raíz y proponla con precisión. Prioriza leer sobre editar."),
        "herramientas": ["leer_archivo", "buscar_codigo", "ejecutar_comando",
                         "ejecutar_pruebas", "finalizar"],
        "max_iter": 10,
    },
    "frontender": {
        "emoji": "FRONTENDER",
        "descripcion": "Revisa CSS/HTML/interfaz y sugiere mejoras de diseño.",
        "prompt": (
            "Eres Frontender, un sub-agente especialista en interfaces. "
            "Revisa HTML/CSS/JS y, si el navegador está disponible, usa las "
            "herramientas browser_* para inspeccionar la UI y proponer "
            "mejoras de diseño concretas."),
        "herramientas": ["leer_archivo", "buscar_codigo", "editar_archivo",
                         "browser_abrir", "browser_screenshot",
                         "browser_get_text", "finalizar"],
        "max_iter": 10,
    },
    "tester": {
        "emoji": "TESTER",
        "descripcion": "Ejecuta pruebas de forma aislada e informa del resultado.",
        "prompt": (
            "Eres Tester, un sub-agente de calidad. Ejecuta las pruebas del "
            "proyecto, interpreta los fallos y devuelve un informe conciso "
            "(qué pasa, qué falla y por qué). NO apliques correcciones."),
        "herramientas": ["ejecutar_pruebas", "ejecutar_comando",
                         "leer_archivo", "buscar_codigo", "finalizar"],
        "max_iter": 8,
    },
    "documentador": {
        "emoji": "DOCUMENTADOR",
        "descripcion": "Genera o actualiza documentación (README, CLAUDE.md).",
        "prompt": (
            "Eres Documentador, un sub-agente técnico. Genera o actualiza "
            "documentación del proyecto (README.md, CLAUDE.md, docstrings) "
            "a partir del código real. Sé preciso y no inventes APIs."),
        "herramientas": ["leer_archivo", "buscar_codigo", "editar_archivo",
                         "finalizar"],
        "max_iter": 8,
    },
    "reviewer": {
        "emoji": "REVIEWER",
        "descripcion": "Revisa cambios en PRs, analiza impacto y sugiere mejoras.",
        "prompt": _PROMPTS["reviewer"],
        "herramientas": ["leer_archivo", "buscar_codigo", "ejecutar_comando",
                         "ejecutar_pruebas", "finalizar"],
        "max_iter": 8,
    },
    "qa_tester": {
        "emoji": "QA TESTER",
        "descripcion": "Revisión adversarial de código (seguridad, rendimiento, lógica).",
        "prompt": _PROMPTS.get("qa_tester", "Eres QA Tester, revisor adversarial."),
        "herramientas": ["leer_archivo", "buscar_codigo", "ejecutar_comando",
                         "ejecutar_pruebas", "editar_archivo", "finalizar"],
        "max_iter": 10,
    },
}

# v6.20.0: los prompts de los roles canónicos provienen de sub_agent_prompts.py
# (fuente única). frontender/tester conservan su definición local.
for _rol_prompt, _texto in _PROMPTS.items():
    if _rol_prompt in ROLES:
        ROLES[_rol_prompt]["prompt"] = _texto

ROLES_VALIDOS = tuple(ROLES.keys())


def rol_valido(rol: str) -> bool:
    """True si ``rol`` es un rol de sub-agente conocido."""
    return str(rol).strip().lower() in ROLES


def listar_roles() -> List[str]:
    """Lista ordenada de roles disponibles."""
    return sorted(ROLES.keys())


# ---------------------------------------------------------------------------
# Registro de sub-agentes (v6.20.0)
# ---------------------------------------------------------------------------
class SubAgentRegistry:
    """Registro consultable y extensible de sub-agentes.

    Guarda la configuración de cada sub-agente (descripción, prompt, rol
    subyacente, herramientas permitidas y límite de iteraciones) para poder
    instanciarlos bajo demanda y listarlos. Admite roles nuevos: ``registrar``
    no exige que el nombre exista en :data:`ROLES`.

    - :meth:`registrar`: añade o sobrescribe un sub-agente.
    - :meth:`obtener`: devuelve la configuración (``KeyError`` si no existe).
    - :meth:`listar`: nombres ordenados de los registrados.
    """

    # Registro por defecto: scout, debugger, reviewer, documentador, qa_tester.
    ROLES_POR_DEFECTO = ("scout", "debugger", "reviewer", "documentador", "qa_tester")

    def __init__(self, predefinidos: bool = True) -> None:
        self._registro: Dict[str, Dict[str, Any]] = {}
        if predefinidos:
            for rol in self.ROLES_POR_DEFECTO:
                cfg = dict(ROLES[rol])
                cfg["rol"] = rol
                cfg["nombre"] = rol
                self._registro[rol] = cfg

    def registrar(self, nombre: str, config: Dict[str, Any]) -> None:
        """Registra (o sobrescribe) un sub-agente con ``config``."""
        nombre = str(nombre).strip().lower()
        cfg = dict(config or {})
        cfg["nombre"] = nombre
        cfg["rol"] = str(cfg.get("rol") or nombre).strip().lower()
        if ROLES.get(cfg["rol"]):
            base = dict(ROLES[cfg["rol"]])
            base.update({k: v for k, v in cfg.items() if v is not None})
            cfg = base
        cfg.setdefault("prompt", _PROMPTS.get(nombre) or f"Eres {nombre}.")
        cfg.setdefault("herramientas",
                       ["leer_archivo", "buscar_codigo", "finalizar"])
        cfg.setdefault("max_iter", 8)
        cfg.setdefault("descripcion", "")
        self._registro[nombre] = cfg

    def obtener(self, nombre: str) -> Dict[str, Any]:
        """Configuración copiada de un sub-agente; ``KeyError`` si no existe."""
        clave = str(nombre).strip().lower()
        if clave not in self._registro:
            raise KeyError(
                f"Sub-agente no registrado: '{nombre}'. Disponibles: "
                f"{', '.join(sorted(self._registro))}")
        return dict(self._registro[clave])

    def listar(self) -> List[str]:
        """Nombres ordenados de los sub-agentes registrados."""
        return sorted(self._registro.keys())

    def __contains__(self, nombre: str) -> bool:
        return str(nombre).strip().lower() in self._registro


# Registro por defecto (compartido por la CLI, el Supervisor y el agente ReAct).
REGISTRO_SUB_AGENTES = SubAgentRegistry()


class SubAgente:
    """Agente ReAct aislado con rol especializado (v6.13.0).

    Encapsula un ``ReactAgent`` al que restringe las herramientas según el
    rol y al que añade el prompt de sistema del rol. El historial es propio:
    no comparte contexto con el agente principal ni con otros sub-agentes
    (la comunicación se realiza exclusivamente por mensajes).
    """

    def __init__(self, rol: str, nombre: Optional[str] = None,
                 directorio: str = ".", proveedor: Optional[str] = None,
                 modelo: Optional[str] = None,
                 max_iteraciones: Optional[int] = None,
                 buzon: Optional[Any] = None, auto: bool = True,
                 browser: bool = False, lsp: bool = False,
                 config: Optional[Dict[str, Any]] = None) -> None:
        # v6.20.0: roles dinámicos. Si se pasa un ``config`` válido, se usa ese
        # prompt/herramientas/límite aunque el ``rol`` no esté en ROLES.
        if config and isinstance(config, dict):
            self.config_rol = {
                "emoji": config.get("emoji") or str(rol).upper(),
                "descripcion": str(config.get("descripcion") or ""),
                "prompt": str(config.get("prompt")
                              or ROLES.get(rol, {}).get("prompt", "")),
                "herramientas": list(config.get("herramientas")
                                     or ROLES.get(rol, {}).get("herramientas",
                                                               ["leer_archivo",
                                                                "finalizar"])),
                "max_iter": int(config.get("max_iter")
                                or ROLES.get(rol, {}).get("max_iter", 8)),
            }
        else:
            rol = str(rol).strip().lower()
            if rol not in ROLES:
                raise ValueError(
                    f"Rol de sub-agente desconocido: '{rol}'. Válidos: "
                    f"{', '.join(ROLES_VALIDOS)}")
            self.config_rol = ROLES[rol]
        self.rol = str(rol).strip().lower()
        self.nombre = nombre or f"{self.rol}-{id(self) % 10000:04d}"
        self.buzon = buzon
        self.auto = bool(auto)
        self.max_iteraciones = int(max_iteraciones
                                   or self.config_rol["max_iter"])
        self._entrada: List[dict] = []          # buzón individual de entrada
        self._mutex = threading.Lock()
        from react_agent import ReactAgent                # noqa: E402
        self.agente = ReactAgent(
            directorio=directorio, auto=self.auto,
            max_iter=self.max_iteraciones, proveedor=proveedor,
            modelo=modelo, browser=browser, lsp=lsp)
        permitidas = set(self.config_rol["herramientas"])
        self.herramientas: Dict[str, Callable[[dict], dict]] = {
            nombre_h: fn for nombre_h, fn in self.agente.herramientas.items()
            if nombre_h in permitidas or nombre_h == "finalizar"}
        self.agente.herramientas = self.herramientas

    # ------------------------------------------------------------------
    # Prompt de sistema propio del rol
    # ------------------------------------------------------------------
    def _prompt_sistema(self) -> str:
        base = self.agente._construir_prompt_sistema()
        acciones = ", ".join(sorted(self.herramientas.keys()))
        return (f"{self.config_rol['prompt']}\n\n{base}\n\n"
                f"[ROL SUB-AGENTE: {self.rol}] Herramientas permitidas: "
                f"{acciones}.")

    # ------------------------------------------------------------------
    # Comunicación
    # ------------------------------------------------------------------
    def enviar_mensaje(self, mensaje: Any,
                       remitente: str = "supervisor") -> None:
        """Recibe un mensaje de otro agente (buzón de entrada)."""
        with self._mutex:
            self._entrada.append({"remitente": remitente,
                                  "contenido": mensaje})

    def recibir_mensajes(self) -> List[dict]:
        """Devuelve y vacía los mensajes pendientes de la entrada."""
        with self._mutex:
            mensajes = list(self._entrada)
            self._entrada.clear()
        return mensajes

    def _contexto_mensajes(self) -> str:
        mensajes = self.recibir_mensajes()
        if not mensajes:
            return ""
        partes = [f"- [{m['remitente']}] {m['contenido']}" for m in mensajes]
        return ("\n\nMENSAJES RECIBIDOS DE OTROS AGENTES:\n"
                + "\n".join(partes))

    # ------------------------------------------------------------------
    # Ejecución
    # ------------------------------------------------------------------
    def ejecutar(self, consulta: str) -> dict:
        """Ejecuta el bucle ReAct aislado para ``consulta``.

        Inyecta los mensajes recibidos, sustituye el prompt de sistema por
        el del rol, publica el resultado en el ``buzon`` (si hay) y devuelve
        ``{ok, resultado, iteraciones, abortado, rol, nombre}``.
        """
        import snapcontext as sc                       # noqa: E402
        sc.info(f"🧠 Creando sub-agente: {self.nombre}...")
        consulta_final = str(consulta) + self._contexto_mensajes()
        self.agente._construir_prompt_sistema = self._prompt_sistema  # type: ignore[method-assign]
        try:
            resultado = self.agente.ejecutar(consulta_final)
        except Exception as exc:                       # noqa: BLE001
            resultado = {"ok": False, "resultado": f"Error: {exc}",
                         "iteraciones": 0, "abortado": False}
        resultado = dict(resultado)
        resultado["rol"] = self.rol
        resultado["nombre"] = self.nombre
        if self.buzon is not None:
            try:
                self.buzon.publicar(self.nombre, "resultado_sub_agente",
                                    {"rol": self.rol,
                                     "ok": bool(resultado.get("ok")),
                                     "resultado": resultado.get("resultado")})
            except Exception:                          # noqa: BLE001
                pass
        if resultado.get("ok"):
            sc.exito(f"✅ Sub-agente {self.nombre} completado: "
                     f"{resultado.get('resultado')}")
        else:
            sc.aviso(f"Sub-agente {self.nombre} terminó sin éxito.")
        return resultado


# ---------------------------------------------------------------------------
# Ejecución en paralelo (threading + semáforo)
# ---------------------------------------------------------------------------
def ejecutar_sub_agentes_paralelo(
        especificaciones: List[dict],
        max_parallel: int = 3,
        buzon: Optional[Any] = None) -> List[dict]:
    """Lanza varios sub-agentes en paralelo con límite ``max_parallel``.

    ``especificaciones`` es una lista de dicts con al menos ``rol`` y
    ``consulta``. Devuelve los resultados en el mismo orden. Los errores por
    hilo se capturan y no abortan al resto.
    """
    import snapcontext as sc                       # noqa: E402
    resultados: List[Optional[dict]] = [None] * len(especificaciones)
    if not especificaciones:
        return []
    if len(especificaciones) > 1:
        sc.info(f"Ejecutando {len(especificaciones)} sub-agentes en "
                f"paralelo (max. {max(1, int(max_parallel))})...")
    semaforo = threading.Semaphore(max(1, int(max_parallel)))
    errores: Dict[int, str] = {}

    def _trabajador(indice: int, espec: dict) -> None:
        with semaforo:
            try:
                sub = SubAgente(
                    espec["rol"],
                    directorio=espec.get("directorio", "."),
                    proveedor=espec.get("proveedor"),
                    modelo=espec.get("modelo"),
                    buzon=buzon,
                    browser=bool(espec.get("browser", False)),
                    lsp=bool(espec.get("lsp", False)))
                resultados[indice] = sub.ejecutar(espec["consulta"])
            except Exception as exc:               # noqa: BLE001
                errores[indice] = str(exc)
                resultados[indice] = {"ok": False, "rol": espec.get("rol"),
                                      "resultado": f"Error: {exc}",
                                      "iteraciones": 0, "abortado": False}

    hilos = [threading.Thread(target=_trabajador, args=(i, espec),
                              daemon=True)
             for i, espec in enumerate(especificaciones)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    finales: List[dict] = []
    for i, r in enumerate(resultados):
        if isinstance(r, dict):
            finales.append(dict(r))
        else:
            finales.append({"ok": False,
                            "rol": especificaciones[i].get("rol"),
                            "resultado": errores.get(i, "sin resultado"),
                            "iteraciones": 0, "abortado": False})
    return finales


# ---------------------------------------------------------------------------
# Integración con la cola de tareas (v6.8.0)
# ---------------------------------------------------------------------------
def encolar_sub_agente(rol: str, consulta: str, directorio: str = ".",
                       db_path: Optional[Any] = None,
                       proveedor: Optional[str] = None,
                       modelo: Optional[str] = None) -> int:
    """Encola la ejecución de un sub-agente como tarea asíncrona (v6.8.0)."""
    import task_queue as tq                        # noqa: E402
    return tq.encolar_tarea("sub_agente", {
        "rol": rol, "consulta": consulta, "directorio": directorio,
        "proveedor": proveedor, "modelo": modelo}, db_path=db_path)


def ejecutar_tarea_sub_agente(datos: Dict[str, Any]) -> Dict[str, Any]:
    """Handler para la cola de tareas: ejecuta un SubAgente con ``datos``."""
    sub = SubAgente(str(datos.get("rol", "scout")),
                    directorio=str(datos.get("directorio", ".")),
                    proveedor=datos.get("proveedor"),
                    modelo=datos.get("modelo"))
    return sub.ejecutar(str(datos.get("consulta", "")))


__all__ = ["ROLES", "ROLES_VALIDOS", "SubAgente", "SubAgentRegistry",
           "REGISTRO_SUB_AGENTES", "rol_valido", "listar_roles",
           "ejecutar_sub_agentes_paralelo", "encolar_sub_agente",
           "ejecutar_tarea_sub_agente"]
