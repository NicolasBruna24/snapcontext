#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hooks / lifecycle events (v6.22.0) — puntos de extensión del agente.

Eventos soportados (ver :data:`EVENTOS`):

- ``before_tool_use`` / ``after_tool_use``      → dispatcher MCP.
- ``before_plan_step`` / ``after_plan_step``    → planificador.
- ``session_start`` / ``session_end``           → sesión ReAct o plan.
- ``before_react_iteration`` / ``after_react_iteration`` → bucle ReAct.

API::

    from hooks import registrar_hook, ejecutar_hook

    def filtrar(contexto):                       # hook síncrono
        if contexto.get("accion") == "ejecutar_comando":
            return {"abort": True, "razon": "no se permiten comandos"}
        return None                              # continuar

    registrar_hook("before_tool_use", filtrar, prioridad=10)

Cada hook recibe un ``contexto`` (dict) y puede:

- devolver ``None`` → continuar sin cambios;
- devolver un ``dict`` → fusiona sus claves en el contexto;
- devolver ``{"abort": True, "razon": "..."}`` → aborta el flujo llamador.

Los hooks asíncronos (corutinas) se resuelven con ``asyncio`` de forma
transparente. Ningún error de un hook rompe el flujo principal. Sin hooks
registrados el sistema no añade latencia: el flujo es idéntico al de siempre.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Eventos del ciclo de vida (v6.22.0). Los planificadores/agentes usan
# exactamente estos nombres; añadir nuevos es retrocompatible.
EVENTOS = (
    "before_tool_use",
    "after_tool_use",
    "before_plan_step",
    "after_plan_step",
    "session_start",
    "session_end",
    "before_react_iteration",
    "after_react_iteration",
)

DIR_PLUGINS = Path.home() / ".snapcontext" / "plugins"
DIR_HOOKS = Path(os.environ.get(
    "SNAPCONTEXT_HOOKS_DIR", str(Path.home() / ".snapcontext" / "hooks")))

try:                                                     # salida con color
    import snapcontext as _sc                            # noqa: E402
except Exception:                                        # pragma: no cover
    _sc = None


def _depurar(texto: str) -> None:
    if _sc is not None:
        try:
            _sc.depurar(texto)
            return
        except Exception:                                # noqa: BLE001
            pass
    if os.environ.get("SNAPCONTEXT_DEPURAR"):
        print(f"[hooks] {texto}")


def _aviso(texto: str) -> None:
    if _sc is not None:
        try:
            _sc.aviso(texto)
            return
        except Exception:                                # noqa: BLE001
            pass
    print(f"⚠ {texto}")


_ACTIVO = True
_CARGADO = False        # carga perezosa ya realizada (v6.22.0)


class HookManager:
    """Registro y ejecución de hooks por evento, ordenados por prioridad."""

    def __init__(self) -> None:
        self._hooks: Dict[str, List[dict]] = {e: [] for e in EVENTOS}
        self._candado = threading.RLock()

    # -- registro -------------------------------------------------------
    def registrar(self, evento: str, funcion: Callable,
                  prioridad: int = 0, origen: str = "interno") -> bool:
        """Registra ``funcion`` para ``evento``. False si el evento es inválido."""
        if evento not in self._hooks or not callable(funcion):
            return False
        with self._candado:
            self._hooks[evento].append(
                {"funcion": funcion, "prioridad": int(prioridad),
                 "origen": origen})
            # Prioridad mayor se ejecuta antes; empates → orden de registro.
            self._hooks[evento].sort(key=lambda h: -h["prioridad"])
        return True

    def desregistrar(self, evento: str, funcion: Callable) -> bool:
        """Elimina un hook concreto (útil en tests y recargas de plugins)."""
        with self._candado:
            antes = len(self._hooks.get(evento, []))
            self._hooks[evento] = [
                h for h in self._hooks.get(evento, [])
                if h["funcion"] is not funcion]
            return len(self._hooks[evento]) < antes

    def limpiar(self, evento: Optional[str] = None) -> None:
        """Vacía el registro (todos los eventos si ``evento`` es None)."""
        with self._candado:
            if evento is None:
                for clave in self._hooks:
                    self._hooks[clave] = []
            elif evento in self._hooks:
                self._hooks[evento] = []

    # -- consulta -------------------------------------------------------
    def hooks_de(self, evento: str) -> List[dict]:
        """Copia de los hooks registrados para ``evento`` (orden ejecución)."""
        with self._candado:
            return list(self._hooks.get(evento, []))

    def listar(self) -> Dict[str, List[dict]]:
        """Mapa evento → [{origen, prioridad, funcion}] (para --hook-list)."""
        with self._candado:
            return {evento: [
                {"origen": h["origen"], "prioridad": h["prioridad"],
                 "funcion": h["funcion"]}
                for h in ganchos]
                for evento, ganchos in self._hooks.items() if ganchos}

    def total(self) -> int:
        return sum(len(g) for g in self._hooks.values())

    # -- ejecución ------------------------------------------------------
    def ejecutar(self, evento: str, contexto: Optional[dict] = None,
                 **extra) -> tuple:
        """Ejecuta los hooks de ``evento`` en orden de prioridad.

        Devuelve ``(abortado: bool, contexto: dict)``. El contexto viaja por
        referencia y los hooks pueden enriquecerlo devolviendo un dict (se
        fusiona). Un hook que devuelva ``{"abort": True, "razon": ...}``
        detiene la cadena. Los errores de hooks individuales se registran y
        no interrumpen el resto.
        """
        ctx = contexto if isinstance(contexto, dict) else {}
        ctx.update(extra)
        for hook in self.hooks_de(evento):
            funcion, origen = hook["funcion"], hook["origen"]
            try:
                resultado = funcion(ctx)
                if asyncio.iscoroutine(resultado):
                    resultado = asyncio.run(resultado)
            except Exception as exc:                     # noqa: BLE001
                _depurar(f"[hooks] Error en hook '{evento}' ({origen}): {exc}")
                continue
            _depurar(f"🔗 Hook ejecutado: {evento} desde {origen}")
            if isinstance(resultado, dict):
                if resultado.get("abort"):
                    _aviso(f"❌ Hook abortó la ejecución: "
                           f"{resultado.get('razon', 'sin razón')}")
                    return True, ctx
                # Fusión superficial: el hook puede modificar el contexto.
                for clave, valor in resultado.items():
                    ctx[clave] = valor
        return False, ctx


# Singleton global (idempotente: recargar el módulo reutiliza la instancia
# previa si ya existe en sys.modules del intérprete).
MANAGER = HookManager()


def activar() -> None:
    """Reactiva la ejecución de hooks (flag ``--hooks``)."""
    global _ACTIVO
    _ACTIVO = True


def desactivar() -> None:
    """Desactiva globalmente la ejecución de hooks (flag ``--no-hooks``)."""
    global _ACTIVO
    _ACTIVO = False


def activar() -> None:
    """Reactiva el sistema de hooks (por defecto está activo)."""
    global _ACTIVO
    _ACTIVO = True


def desactivar() -> None:
    """Desactiva temporalmente el sistema de hooks (``--no-hooks``)."""
    global _ACTIVO
    _ACTIVO = False


def activo() -> bool:
    return _ACTIVO


def registrar_hook(evento: str, funcion: Callable, prioridad: int = 0,
                   origen: str = "interno") -> bool:
    """Registra un hook en el gestor global. False si el evento no existe."""
    return MANAGER.registrar(evento, funcion, prioridad=prioridad,
                             origen=origen)


def ejecutar_hook(evento: str, contexto: Optional[dict] = None,
                  **extra) -> tuple:
    """Ejecuta los hooks de ``evento`` → ``(abortado, contexto)``.

    Si el sistema está desactivado (``desactivar()``) no ejecuta nada.
    """
    if not _ACTIVO:
        return False, (contexto if isinstance(contexto, dict) else {})
    return MANAGER.ejecutar(evento, contexto, **extra)


# Atajos con nombre legible en los puntos de integración.
limpiar_hooks = MANAGER.limpiar
listar_hooks = MANAGER.listar
hooks_de = MANAGER.hooks_de


def _listar_hooks_texto() -> str:
    """Representación textual de los hooks registrados (para ``--hook-list``)."""
    registro = listar_hooks()
    if not registro:
        return "No hay hooks registrados."
    lineas = ["🔗 Hooks registrados:"]
    for evento, ganchos in registro.items():
        for h in ganchos:
            nombre = getattr(h["funcion"], "__name__", repr(h["funcion"]))
            lineas.append(
                f"  • {evento}: {nombre} "
                f"(origen: {h['origen']}, prioridad: {h['prioridad']})")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Carga de hooks desde plugins y archivos de configuración
# ---------------------------------------------------------------------------

def _ejecutar_script_shell(ruta: Path, contexto: dict) -> Optional[dict]:
    """Ejecuta un hook de shell con el contexto como JSON por stdin.

    Si el script escribe un JSON con ``"abort": true`` en stdout, se aborta.
    Devuelve el dict parseado (o None). Seguridad: sin shell intérprete
    (lista de args) y con timeout de 30 s.
    """
    comando = ["bash", str(ruta)] if ruta.suffix == ".sh" else [
        "cmd", "/c", str(ruta)]
    try:
        proc = subprocess.run(
            comando, input=json.dumps(contexto, ensure_ascii=False),
            capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        _depurar(f"[hooks] Intérprete no disponible para {ruta.name}; omitido")
        return None
    except Exception as exc:                             # noqa: BLE001
        _depurar(f"[hooks] Error ejecutando {ruta.name}: {exc}")
        return None
    salida = (proc.stdout or "").strip()
    if not salida:
        return None
    try:
        datos = json.loads(salida)
        return datos if isinstance(datos, dict) else None
    except json.JSONDecodeError:
        return None


def _cargar_modulo_python(ruta: Path):
    """Importa un módulo Python desde una ruta (hook de plugin o directorio)."""
    nombre = f"_hook_{ruta.stem}_{abs(hash(str(ruta)))}"
    spec = importlib.util.spec_from_file_location(nombre, str(ruta))
    if spec is None or spec.loader is None:
        return None
    modulo = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(modulo)
    except Exception as exc:                             # noqa: BLE001
        _depurar(f"[hooks] Error importando {ruta.name}: {exc}")
        return None
    return modulo


def _registrar_desde_manifiesto(ruta_plugin: Path) -> int:
    """Registra los hooks declarados en el ``plugin.json`` de ``ruta_plugin``.

    Formato en el manifest::

        "hooks": {"before_tool_use": "scripts/before_tool.py", ...}

    Los scripts ``.py`` deben exportar ``ejecutar(contexto)``; los shell
    reciben el contexto por stdin y pueden responder con JSON.
    """
    manifiesto = ruta_plugin / "plugin.json"
    if not manifiesto.exists():
        return 0
    try:
        datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    seccion = datos.get("hooks") or {}
    if not isinstance(seccion, dict):
        return 0
    nombre_plugin = datos.get("name") or ruta_plugin.name
    registrados = 0
    for evento, relativo in seccion.items():
        if evento not in EVENTOS:
            _depurar(f"[hooks] Evento desconocido '{evento}' en "
                     f"{nombre_plugin}; omitido")
            continue
        script = ruta_plugin / str(relativo)
        if not script.exists():
            _depurar(f"[hooks] Script de hook inexistente: {script}")
            continue
        if script.suffix == ".py":
            modulo = _cargar_modulo_python(script)
            funcion = getattr(modulo, "ejecutar", None) if modulo else None
            if not callable(funcion):
                _depurar(f"[hooks] {script.name} no exporta 'ejecutar'")
                continue
            if registrar_hook(evento, funcion, origen=nombre_plugin):
                registrados += 1
        else:
            # Hook de shell: se cierra sobre la ruta para llamarlo por evento.
            def _fabricar(ruta=script):
                def _hook(contexto):
                    return _ejecutar_script_shell(ruta, contexto)
                _hook.__name__ = f"shell:{ruta.name}"
                return _hook
            if registrar_hook(evento, _fabricar(), origen=nombre_plugin):
                registrados += 1
    return registrados


def cargar_hooks_desde_plugins() -> int:
    """Escanea ``~/.snapcontext/plugins/`` y registra los hooks declarados
    en la sección ``hooks`` de cada ``plugin.json``. Devuelve el nº añadido."""
    total = 0
    try:
        candidatos = list(DIR_PLUGINS.iterdir())
    except OSError:
        return 0
    for carpeta in candidatos:
        if carpeta.is_dir():
            total += _registrar_desde_manifiesto(carpeta)
    return total


def cargar_hooks_desde_archivos(directorio: Optional[Path] = None) -> int:
    """Registra hooks desde scripts sueltos en ``~/.snapcontext/hooks/``.

    Formato del nombre de archivo: ``<evento>[__<prioridad>].<ext>``
    (ej: ``before_tool_use.py``, ``after_plan_step__10.sh``). Los ``.py``
    deben exportar ``ejecutar(contexto)``; los shell reciben el contexto
    por stdin (JSON) y pueden responder con JSON. Devuelve el nº añadido.
    """
    base = Path(directorio) if directorio else DIR_HOOKS
    total = 0
    try:
        candidatos = sorted(base.iterdir())
    except OSError:
        return 0
    for ruta in candidatos:
        if not ruta.is_file() or ruta.suffix not in (".py", ".sh", ".cmd", ".bat"):
            continue
        stem = ruta.stem
        prioridad = 0
        if "__" in stem:
            stem, _, sufijo = stem.rpartition("__")
            try:
                prioridad = int(sufijo)
            except ValueError:
                pass
        if stem not in EVENTOS:
            _depurar(f"[hooks] Archivo '{ruta.name}' no mapea a ningún "
                     f"evento; omitido")
            continue
        if ruta.suffix == ".py":
            modulo = _cargar_modulo_python(ruta)
            funcion = getattr(modulo, "ejecutar", None) if modulo else None
            if not callable(funcion):
                _depurar(f"[hooks] {ruta.name} no exporta 'ejecutar'")
                continue
            if registrar_hook(stem, funcion, prioridad=prioridad,
                              origen="hooks/"):
                total += 1
        else:
            def _fabricar(ruta=ruta):
                def _hook(contexto):
                    return _ejecutar_script_shell(ruta, contexto)
                _hook.__name__ = f"shell:{ruta.name}"
                return _hook
            if registrar_hook(stem, _fabricar(), prioridad=prioridad,
                              origen="hooks/"):
                total += 1
    return total


def cargar_todos_los_hooks() -> int:
    """Carga hooks de plugins y del directorio de configuración (una vez)."""
    return cargar_hooks_desde_plugins() + cargar_hooks_desde_archivos()

