#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Capa de presentación de SnapContext (v4.8.0).

Centraliza TODA la lógica de UI de la terminal sobre `rich`: banner, barras de
progreso, tablas (impacto de dependencias), diffs coloreados, preguntas
interactivas y mensajes de estado/error.

Principios:
  - `snapcontext.py` solo llama a funciones de este módulo (sin tocar la
    lógica de negocio del editor/orquestador).
  - Modo no interactivo (`--auto`): activable con :func:`configurar_auto`.
    En modo auto NO se muestran barras de progreso ni se pregunta nada
    (las preguntas devuelven ``"c"`` por defecto), pero los errores y los
    diffs SÍ se muestran siempre.
  - Degradación elegante: si `rich` no está instalado, todo sigue funcionando
    con `print()` plano.
"""

from __future__ import annotations

import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:                                    # pragma: no cover - entorno sin rich
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import track
    from rich.prompt import Prompt
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text

    RICH_DISPONIBLE = True
except ImportError:                     # pragma: no cover - fallback plano
    RICH_DISPONIBLE = False

__all__ = [
    "RICH_DISPONIBLE",
    "VERSION_UI",
    "configurar_auto",
    "es_auto",
    "mostrar_banner",
    "mostrar_progreso",
    "mostrar_tabla_impacto",
    "mostrar_diff",
    "preguntar_interactivo",
    "OPCIONES_IMPACTO_DEFECTO",
    "mostrar_estado",
    "mostrar_error",
]

VERSION_UI = "4.8.0"

# Estado global del modo no interactivo (--auto / API / gateways).
MODO_AUTO = False

_console = Console() if RICH_DISPONIBLE else None   # type: ignore[misc]

# Opciones estándar del análisis de impacto (contrato idéntico a v4.7.0:
# la respuesta devuelta es la tecla 'c', 'a' o 's').
OPCIONES_IMPACTO_DEFECTO: List[Tuple[str, str]] = [
    ("c", "Continuar"),
    ("a", "Abortar"),
    ("s", "Añadir dependientes"),
]

_BANNER_ART = r"""
   ┌──────────────────────────────────────────────────────────┐
   │                                                          │
   │    ███████╗███╗   ██╗ █████╗ ██████╗  ██████╗ ██████╗   │
   │    ██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔════╝██╔════╝   │
   │    ███████╗██╔██╗ ██║███████║██████╔╝██║     ██║        │
   │    ╚════██║██║╚██╗██║██╔═══╝ ██║     ██║     ██║        │
   │    ███████║██║ ╚████║██║  ██║██║     ╚██████╗╚██████╗   │
   │    ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝      ╚═════╝ ╚═════╝   │
   │                                                          │
   │    » Selección inteligente de archivos                  │
   │    » Soporte: Gemini · Ollama · DeepSeek · Groq        │
   │                                                          │
   └──────────────────────────────────────────────────────────┘
"""

_COMANDOS_BANNER: List[Tuple[str, str]] = [
    ("snapcontext \"tarea\"", "Ejecutar una tarea con selección automática"),
    ("--plan \"tarea\"", "Planificar y ejecutar paso a paso"),
    ("--test-loop", "Bucle editar → probar hasta pasar"),
    ("--chat", "Conversar con el proveedor de IA"),
    ("--editor propio", "Editor transaccional propio (por defecto)"),
    ("--init", "Configurar claves API y proveedor"),
    ("--diagnostico", "Revisar la instalación"),
    ("--demo", "Demo autónoma sin API key"),
    ("--help", "Ayuda completa agrupada"),
]


def configurar_auto(auto: bool) -> None:
    """Activa/desactiva el modo no interactivo global de la UI."""
    global MODO_AUTO
    MODO_AUTO = bool(auto)


def es_auto() -> bool:
    """True si la UI está en modo no interactivo (`--auto`, API, gateways)."""
    return MODO_AUTO


def _imprimir(*args, **kwargs) -> None:
    """Fallback plano cuando `rich` no está disponible."""
    print(*args, **kwargs)


def mostrar_banner(version: str = VERSION_UI) -> None:
    """Banner de inicio: logotipo ASCII + versión + tabla de comandos.

    Reemplaza a los `print(_LOGO)` / `print(_LOGO_SMALL)` planos de v4.x.
    """
    if not RICH_DISPONIBLE:
        _imprimir(_BANNER_ART)
        _imprimir(f"SnapContext v{version}")
        _imprimir("Open-source · MIT · "
                  "https://github.com/NicolasBruna24/snapcontext")
        return
    _console.print(Text(_BANNER_ART, style="bold cyan"))
    tabla = Table(show_header=True, header_style="bold magenta",
                  title=f"[bold]SnapContext v{version}[/bold] · MIT")
    tabla.add_column("Comando", style="bold green", no_wrap=True)
    tabla.add_column("Descripción")
    for comando, descripcion in _COMANDOS_BANNER:
        tabla.add_row(comando, descripcion)
    _console.print(tabla)
    _console.print("[dim]https://github.com/NicolasBruna24/snapcontext[/dim]")


def mostrar_progreso(iterable: Iterable, descripcion: str):
    """Envuelve un bucle largo con barra de progreso y ETA.

    Devuelve un iterador equivalente al original. En modo `--auto` (o sin
    `rich`) devuelve el iterable SIN envolver (modo silencioso), de modo que
    el bucle de negocio no cambia en absoluto.
    """
    if MODO_AUTO or not RICH_DISPONIBLE:
        return iterable
    try:
        return track(iterable, description=descripcion, console=_console)
    except Exception:                       # pragma: no cover - blindaje UI
        return iterable


def mostrar_tabla_impacto(dependencias: Dict[str, Sequence[str]],
                          criticas: Optional[set] = None) -> None:
    """Tabla del análisis de impacto por dependencias (v4.8.0).

    ``dependencias`` mapea ``archivo editado → [archivos que dependen de él]``.
    Columnas: 📁 Archivo Afectado · 🔗 Dependencia · 📊 Acción Sugerida.
    Las filas críticas (en ``criticas``; todas si ``criticas=None``) se
    muestran en amarillo. Se muestra también en modo `--auto` (es
    información, no una pregunta).
    """
    if not dependencias:
        return
    criticas = criticas if criticas is not None else set(dependencias)
    if not RICH_DISPONIBLE:
        for archivo, deps in dependencias.items():
            _imprimir(f"⚠️ Atención: El cambio en '{archivo}' afecta a: "
                      f"[{', '.join(deps)}].")
        return
    tabla = Table(title="🔗 Análisis de Impacto Previo",
                  show_header=True, header_style="bold magenta")
    tabla.add_column("📁 Archivo Afectado", style="bold", no_wrap=True)
    tabla.add_column("🔗 Dependencia (Función/Clase)")
    tabla.add_column("📊 Acción Sugerida")
    for archivo, deps in dependencias.items():
        critico = archivo in criticas
        estilo_archivo = "yellow bold" if critico else "white"
        for dep in deps:
            tabla.add_row(
                Text(str(archivo), style=estilo_archivo),
                Text(str(dep), style="yellow" if critico else ""),
                Text("Revisar y actualizar los usos tras el cambio",
                     style="dim"),
            )
    _console.print(tabla)


def mostrar_diff(archivo: str, lines_added: int, lines_removed: int,
                 contexto: str) -> None:
    """Diff coloreado (verde +/rojo −/gris contexto) con `rich.syntax.Syntax`.

    Se muestra SIEMPRE, incluso en modo `--auto`: el usuario debe poder ver
    qué se cambió.
    """
    if not RICH_DISPONIBLE:
        _imprimir(f"{archivo} · +{lines_added} · -{lines_removed}")
        _imprimir(contexto)
        return
    _console.print(
        f"[bold]{archivo}[/bold] · "
        f"[green]+{lines_added} añadida(s)[/green] · "
        f"[red]-{lines_removed} eliminada(s)[/red]")
    try:
        _console.print(Syntax(contexto or "(sin cambios)", "diff",
                              theme="monokai", line_numbers=False))
    except Exception:                        # pragma: no cover - blindaje UI
        _console.print(contexto or "(sin cambios)")


def preguntar_interactivo(opciones: Optional[List[Tuple[str, str]]],
                          mensaje: str,
                          defecto: str = "c") -> str:
    """Pregunta interactiva estilizada. Devuelve la TECLA elegida.

    ``opciones`` es una lista de ``(tecla, texto)``, p. ej. la
    :data:`OPCIONES_IMPACTO_DEFECTO` ``[('c', 'Continuar'), …]``.

    En modo `--auto` NO pregunta: devuelve ``defecto`` (por defecto ``'c'``,
    continuar), manteniendo exactamente el contrato de v4.7.0.
    """
    opciones = list(opciones or OPCIONES_IMPACTO_DEFECTO)
    teclas_validas = [tecla for tecla, _ in opciones]
    if defecto not in teclas_validas:
        defecto = teclas_validas[0]
    if MODO_AUTO:
        return defecto
    menu = "  ".join(f"[{tecla}] {texto}" for tecla, texto in opciones)
    if not RICH_DISPONIBLE:
        _imprimir(mensaje)
        _imprimir(menu)
        respuesta = input("Elige [{}]: ".format("/".join(teclas_validas)))
        respuesta = respuesta.strip().lower()
        return respuesta if respuesta in teclas_validas else defecto
    _console.print(f"[bold cyan]{mensaje}[/bold cyan]\n[dim]{menu}[/dim]")
    eleccion = Prompt.ask(
        "Elige", choices=teclas_validas, default=defecto,
        console=_console, show_choices=False, show_default=True)
    return str(eleccion).strip().lower()


def mostrar_estado(mensaje: str, emoji: str = "⚙️") -> None:
    """Log informativo consistente (`info`/`depurar` → Rich)."""
    texto = f"{emoji} {mensaje}"
    if not RICH_DISPONIBLE:
        _imprimir(texto)
        return
    _console.print(f"[cyan]{texto}[/cyan]")


def mostrar_error(mensaje: str) -> None:
    """Error fatal: panel rojo con texto en negrita/blanco. Siempre visible."""
    if not RICH_DISPONIBLE:
        _imprimir(f"✖ {mensaje}", file=sys.stderr)
        return
    _console.print(Panel(Text(str(mensaje), style="bold white"),
                         title="✖ Error", border_style="red"))