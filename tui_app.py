#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TUI inmersiva de SnapContext (v6.12.0) basada en Textual.

Complementa la interfaz web y el CLI tradicional con una terminal interactiva:
- Pestañas: Logs, Control, Diffs; árbol de archivos del proyecto.
- Actualización en tiempo real vía la cola de ``tui_hub`` (no bloqueante).

Si Textual no está instalado, la importación degrada y ``--tui`` muestra un
mensaje de error claro.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

import tui_hub as hub

try:  # Textual es opcional (grupo [tui]).
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import (Button, DirectoryTree, Footer, Header,
                                 Label, RichLog, Static, TabbedContent,
                                 TabPane)
    TEXTUAL_DISPONIBLE = True
except Exception:  # pragma: no cover - degradación sin Textual
    App = object  # type: ignore
    TEXTUAL_DISPONIBLE = False


CSS = '''
Screen { layout: vertical; }
#cuerpo { height: 1fr; }
#izquierda { width: 1/3; border: round $primary; padding: 0 1; }
#derecha { width: 2/3; border: round $accent; }
.titulo { text-style: bold; color: $accent; margin-bottom: 1; }
#arbol { height: 1fr; }
RichLog { height: 1fr; border: round $panel; }
#control { height: 1fr; padding: 0 1; }
#estado { text-style: bold; color: $success; }
#estado.ejecutando { color: $warning; }
#estado.error { color: $error; }
.botonera { height: 3; align: left middle; }
Button { margin: 0 1 0 0; }
'''

COLORES_NIVEL = {"info": "cyan", "warning": "yellow", "error": "red"}
COLORES_FASE = {
    "pensamiento": "magenta",
    "accion": "cyan",
    "observacion": "green",
    "error": "red",
    "estado": "yellow",
}
ESTADOS_AGENTE = ("inactivo", "pensando", "ejecutando", "esperando", "error")


def _texto_nivel(nivel: str) -> str:
    """Texto Rich coloreado para un nivel de log."""
    color = COLORES_NIVEL.get(nivel, "cyan")
    marca = {"info": "INFO ", "warning": "WARN ",
             "error": "ERROR"}.get(nivel, "INFO ")
    return "[bold %s]%s[/]" % (color, marca)


def _escapar(linea: str) -> str:
    """Escapa el marcado Rich de una línea de diff."""
    return linea.replace("[", "\\[")


# ---------------------------------------------------------------------- app --
if TEXTUAL_DISPONIBLE:

    class SnapContextTUI(App):  # type: ignore[misc]
        """TUI inmersiva de SnapContext.

        Consume la cola de ``tui_hub`` de forma asíncrona y muestra logs,
        pasos ReAct, diffs y estado del agente en tiempo real.
        """

        BINDINGS = [
            ("ctrl+q", "quit", "Salir"),
            ("ctrl+l", "limpiar_logs", "Limpiar logs"),
            ("ctrl+t", "toggle_arbol", "Árbol"),
            ("ctrl+d", "limpiar_diffs", "Limpiar diffs"),
        ]

        def __init__(self, consulta: str = "",
                     tarea: Optional[threading.Thread] = None,
                     version: str = "6.12.0") -> None:
            super().__init__()
            self.consulta = consulta
            self.tarea_agente = tarea
            self.version = version
            self.title = f"SnapContext v{version}"
            self.sub_title = "TUI inmersiva - Ctrl+Q salir · Ctrl+L limpiar logs"
            self._inicio = time.time()
            self._pasos = 0

        def compose(self) -> "ComposeResult":
            yield Header(show_clock=True)
            with Horizontal(id="cuerpo"):
                with Vertical(id="izquierda"):
                    yield Label("Archivos", classes="titulo")
                    yield DirectoryTree(".", id="arbol")
                with Vertical(id="derecha"):
                    with TabbedContent(initial="tab-logs"):
                        with TabPane("Logs", id="tab-logs"):
                            yield RichLog(id="logs", highlight=True,
                                          markup=True, wrap=True)
                        with TabPane("Control", id="tab-control"):
                            with Vertical(id="control"):
                                yield Static("", id="estado")
                                yield Static("", id="metricas")
                                with Horizontal(classes="botonera"):
                                    yield Button("Pausar", id="btn-pausar")
                                    yield Button("Reanudar", id="btn-reanudar")
                                    yield Button("Cancelar", id="btn-cancelar",
                                                 variant="error")
                        with TabPane("Diffs", id="tab-diffs"):
                            yield RichLog(id="diffs", highlight=True,
                                          markup=True, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            self._refrescar_estado("inactivo")
            log = self.query_one("#logs", RichLog)
            log.write("[bold cyan]SnapContext TUI iniciada.[/]")
            if self.consulta:
                log.write(f"[bold]Consulta:[/] {self.consulta}")
            self.refrescar_cola()
            self.set_interval(0.5, self.refrescar_cola)
            self.set_interval(1.0, self._refrescar_metricas)
            self.lanzar_agente()

        def refrescar_cola(self) -> None:
            """Drena la cola de eventos del hub (sin bloquear)."""
            cola = hub.cola_eventos()
            if cola is None:
                return
            drenados = 0
            while drenados < 200:  # límite por tick para no bloquear la UI
                try:
                    evento = cola.get_nowait()
                except queue.Empty:
                    break
                drenados += 1
                try:
                    self._procesar_evento(evento)
                except Exception:
                    pass
            if drenados:
                try:
                    self.query_one("#logs", RichLog).scroll_end(animate=False)
                except Exception:
                    pass

        def _procesar_evento(self, evento: dict) -> None:
            tipo = evento.get("tipo", "")
            if tipo == "log":
                self._agregar_log(str(evento.get("nivel", "info")),
                                  str(evento.get("texto", "")))
            elif tipo == "react_step":
                try:
                    self._pasos = max(self._pasos,
                                      int(evento.get("iteracion", 0) or 0))
                except (TypeError, ValueError):
                    pass
                fase = str(evento.get("fase", "observacion"))
                color = COLORES_FASE.get(fase, "white")
                icono = {"pensamiento": "(T)", "accion": "(A)",
                         "observacion": "(O)", "error": "(!)",
                         "estado": "(?)"}.get(fase, "*")
                self._agregar_log(
                    "info",
                    f"[bold {color}]{icono} #{evento.get('iteracion', 0)} "
                    f"{fase}[/] {evento.get('contenido', '')}")
            elif tipo == "estado":
                self._refrescar_estado(str(evento.get("estado", "inactivo")),
                                       str(evento.get("detalle", "")))
            elif tipo == "diff":
                self._agregar_diff(str(evento.get("ruta", "")),
                                   str(evento.get("diff", "")))
            elif tipo == "fin":
                ok = bool(evento.get("ok", True))
                self._refrescar_estado("inactivo" if ok else "error")
                self._agregar_log("info" if ok else "error",
                                  f"Agente finalizado. "
                                  f"{evento.get('resultado', '')}")

        def _agregar_log(self, nivel: str, texto: str) -> None:
            self.query_one("#logs", RichLog).write(
                f"{_texto_nivel(nivel)} {texto}")

        def _agregar_diff(self, ruta: str, diff: str) -> None:
            panel = self.query_one("#diffs", RichLog)
            panel.write(f"[bold magenta]{_escapar(ruta)}[/]")
            for linea in diff.splitlines():
                if linea.startswith(("+++", "---")):
                    panel.write(f"[bold]{_escapar(linea)}[/]")
                elif linea.startswith("+"):
                    panel.write(f"[green]{_escapar(linea)}[/]")
                elif linea.startswith("-"):
                    panel.write(f"[red]{_escapar(linea)}[/]")
                elif linea.startswith("@@"):
                    panel.write(f"[cyan]{_escapar(linea)}[/]")
                else:
                    panel.write(_escapar(linea))

        def _refrescar_estado(self, estado: str, detalle: str = "") -> None:
            widget = self.query_one("#estado", Static)
            iconos = {"inactivo": "o", "pensando": "T", "ejecutando": "A",
                      "esperando": "?", "error": "!"}
            texto = f"[{iconos.get(estado, '*')}] Estado: [bold]{estado}[/]"
            if detalle:
                texto += f" - {detalle[:200]}"
            widget.update(texto)
            widget.remove_class("ejecutando")
            widget.remove_class("error")
            if estado in ("ejecutando", "pensando"):
                widget.add_class("ejecutando")
            elif estado == "error":
                widget.add_class("error")

        def _refrescar_metricas(self) -> None:
            transcurrido = time.time() - self._inicio
            mm, ss = divmod(int(transcurrido), 60)
            self.query_one("#metricas", Static).update(
                f"Tiempo: {mm:02d}:{ss:02d}   Pasos: {self._pasos}")

        def action_limpiar_logs(self) -> None:
            self.query_one("#logs", RichLog).clear()

        def action_limpiar_diffs(self) -> None:
            self.query_one("#diffs", RichLog).clear()

        def action_toggle_arbol(self) -> None:
            arbol = self.query_one("#izquierda", Vertical)
            arbol.display = not arbol.display

        def on_button_pressed(self, evento) -> None:
            boton = getattr(evento.button, "id", "") or ""
            log = self.query_one("#logs", RichLog)
            if boton == "btn-pausar":
                hub.enviar_estado("esperando", "pausa solicitada desde la TUI")
                log.write("[yellow]Pausa solicitada.[/]")
            elif boton == "btn-reanudar":
                hub.enviar_estado("ejecutando", "reanudado desde la TUI")
                log.write("[green]Reanudado.[/]")
            elif boton == "btn-cancelar":
                hub.enviar_estado("error", "cancelado desde la TUI")
                log.write("[red]Cancelación solicitada.[/]")

        def lanzar_agente(self) -> None:
            """Arranca la tarea del agente en hilo demonio (si existe)."""
            tarea = self.tarea_agente
            if tarea is not None and not tarea.is_alive():
                tarea.daemon = True
                tarea.start()


else:  # pragma: no cover - Textual no instalado: stub para importación segura

    class SnapContextTUI:  # type: ignore[no-redef]
        """Stub que permite importar el módulo sin Textual instalado."""

        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError(
                "Textual no está instalado. Instálalo con: "
                "pip install snapcontext[tui]  (o pip install textual>=0.50.0)")


# ------------------------------------------------------------- punto entrada --
def ejecutar_tui(consulta: str = "", tarea: Optional[threading.Thread] = None,
                 version: str = "6.12.0") -> int:
    """Lanza la TUI de SnapContext. Devuelve el código de salida.

    - Si Textual no está instalado, imprime un error claro y devuelve 2.
    - ``tarea`` es el hilo que ejecuta al agente; se arranca al montar la app.
    """
    if not TEXTUAL_DISPONIBLE:
        print("Textual no está instalado. Instala el grupo opcional:")
        print("   pip install snapcontext[tui]")
        print("   (o directamente: pip install 'textual>=0.50.0')")
        return 2
    hub.reiniciar()
    hub.activar()
    app = SnapContextTUI(consulta=consulta, tarea=tarea, version=version)
    try:
        app.run()
    finally:
        hub.desactivar()
    return 0


__all__ = [
    "SnapContextTUI", "ejecutar_tui", "TEXTUAL_DISPONIBLE",
    "COLORES_NIVEL", "COLORES_FASE", "ESTADOS_AGENTE",
]
