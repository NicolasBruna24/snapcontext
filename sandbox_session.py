#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistencia de Docker por sesión (v6.4.0).

Cuando se usa ``--sandbox-session``, SnapContext ya no lanza un contenedor
efímero (``docker run --rm``) por cada comando sino que mantiene **un único
contenedor vivo** durante toda la tarea (un plan de varios pasos o un bucle
ReAct) y lo destruye al finalizar. Esto permite flujos que comparten estado
entre comandos: ``npm install`` → ``npm test``, ``pip install -r
requirements.txt`` → ``pytest``, etc., porque el sistema de archivos y las
dependencias instaladas persisten entre ``docker exec``.

Funciones públicas:

- :func:`crear_sesion`: crea el contenedor de sesión (``docker run -d ...
  tail -f /dev/null``), ejecuta un comando de preparación opcional y guarda el
  identificador en ``~/.snapcontext/session_id.txt``.
- :func:`obtener_sesion`: lee el id guardado y comprueba que el contenedor
  siga en ejecución.
- :func:`ejecutar_en_sesion`: ejecuta un comando dentro del contenedor de
  sesión con ``docker exec``.
- :func:`destruir_sesion`: detiene y elimina el contenedor y borra el id.
- :func:`limpiar_huérfanos`: elimina contenedores ``snap-session-*`` sobrantes
  de sesiones anteriores.

Seguridad: el contenedor monta **solo** el directorio del proyecto en
``/workspace`` (igual que el sandbox de ``docker run --rm``), de modo que no
tiene acceso a otros directorios del host.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Iterable, List, Optional

# Prefijo de contenedores de sesión (usado también para detectar huérfanos).
SESION_PREFIJO = "snap-session-"

# Directorio de estado de SnapContext y archivo con el id de sesión activa.
_SESION_DIR = Path.home() / ".snapcontext"
SESSION_ID_PATH = _SESION_DIR / "session_id.txt"

# Estado en memoria de la sesión (evita re-consultar a Docker por comando).
_SESION_NOMBRE: Optional[str] = None
_SESION_DIRECTORIO: Optional[str] = None


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def _informar(mensaje: str) -> None:
    """Mensaje informativo usando el logger de snapcontext (estilo 🐳)."""
    try:
        from snapcontext import info
        info(mensaje)
    except Exception:                       # noqa: BLE001 - blindaje UI
        print(mensaje)


def _avisar(mensaje: str) -> None:
    try:
        from snapcontext import aviso
        aviso(mensaje)
    except Exception:                       # noqa: BLE001 - blindaje UI
        print("⚠️ " + mensaje)


def _error(mensaje: str) -> None:
    try:
        from snapcontext import error
        error(mensaje)
    except Exception:                       # noqa: BLE001 - blindaje UI
        print("✖ " + mensaje)


# ---------------------------------------------------------------------------
# Utilidades de Docker
# ---------------------------------------------------------------------------
def _flags() -> int:
    """Flags de subprocess: sin ventana de consola en Windows."""
    return (subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _run(argv: List[str], timeout: int = 120, **kwargs) -> subprocess.CompletedProcess:
    """Ejecuta ``docker ...`` por lista (sin shell). Nunca lanza por sí solo."""
    return subprocess.run(argv, capture_output=True, text=True,
                          errors="replace", timeout=timeout,
                          creationflags=_flags(), **kwargs)


def _id_sesion(directorio: str) -> str:
    """Genera un identificador único: resumen del directorio + timestamp."""
    base = str(Path(directorio).expanduser().resolve()).encode("utf-8")
    crudo = hashlib.sha1(base).hexdigest()[:8] + time.strftime("%Y%m%d%H%M%S")
    return crudo


def _nombre_por_id(sid: str) -> str:
    return f"{SESION_PREFIJO}{sid}"


def _documento_sesion(sid: Optional[str] = None) -> Optional[str]:
    """Lee el id guardado en disco si no se pasa explícito."""
    try:
        if sid is None and SESSION_ID_PATH.exists():
            sid = SESSION_ID_PATH.read_text(encoding="utf-8").strip()
        return sid or None
    except OSError:
        return None
# ---------------------------------------------------------------------------
# Estado en memoria
# ---------------------------------------------------------------------------
def sesion_nombre() -> Optional[str]:
    """Nombre del contenedor de sesión activo en memoria (o ``None``)."""
    return _SESION_NOMBRE


def sesion_activa() -> bool:
    """True si este proceso ya registró una sesión Docker en memoria."""
    return _SESION_NOMBRE is not None


def _poner_nombre(nombre: Optional[str]) -> None:
    """Fija el estado en memoria (usado también por los tests)."""
    global _SESION_NOMBRE
    _SESION_NOMBRE = nombre


# ---------------------------------------------------------------------------
# Ciclo de vida de la sesión
# ---------------------------------------------------------------------------
def crear_sesion(directorio: str, imagen: str,
                 comando_preparacion: Optional[str] = None,
                 vars_entorno: Optional[Iterable[str]] = None) -> str:
    """Crea el contenedor de sesión persistente y devuelve su nombre.

    Lanza ``docker run -d --name snap-session-<id> [-e VAR ...]
    -v \"<directorio>:/workspace\" -w /workspace <imagen> tail -f /dev/null``
    para mantenerlo vivo, ejecuta ``comando_preparacion`` (si se indica) con
    ``docker exec`` y guarda el id en ``~/.snapcontext/session_id.txt``.

    ``vars_entorno`` son nombres de variables de entorno del host que se
    propagan al contenedor (p. ej. las ``*_API_KEY`` del sandbox). Si el
    comando de preparación falla se avisa pero la sesión se mantiene.
    """
    global _SESION_NOMBRE, _SESION_DIRECTORIO
    sid = _id_sesion(directorio)
    nombre = _nombre_por_id(sid)
    _SESION_DIR.mkdir(parents=True, exist_ok=True)
    raiz = str(Path(directorio).expanduser().resolve())

    _informar(f"🐳 Creando sesión Docker persistente (ID: {sid})...")
    argv: List[str] = ["docker", "run", "-d", "--name", nombre,
                       "-v", f"{raiz}:/workspace", "-w", "/workspace"]
    vistas = set()
    for var in (vars_entorno or []):
        if var and var not in vistas and var in os.environ:
            vistas.add(var)
            argv += ["-e", var]
    argv += [imagen, "tail", "-f", "/dev/null"]
    try:
        _run(argv, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _error(f"No se pudo crear la sesión Docker: {exc}")
        raise
# Comando de preparación (p. ej. `pip install -r requirements.txt`) dentro
    # de la sesión: sus efectos persisten para todos los comandos posteriores.
    if comando_preparacion:
        try:
            prep = _run(["docker", "exec", nombre, "sh", "-c",
                         comando_preparacion], timeout=600)
            if prep.returncode != 0:
                _avisar("El comando de preparación devolvió código "
                        f"{prep.returncode}:\n{(prep.stderr or '').strip()}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            _avisar(f"No se pudo ejecutar la preparación en la sesión: {exc}")

    try:
        SESSION_ID_PATH.write_text(sid, encoding="utf-8")
    except OSError as exc:
        _avisar(f"No se pudo guardar el id de sesión ({exc}).")

    _SESION_NOMBRE = nombre
    _SESION_DIRECTORIO = raiz
    return nombre


def obtener_sesion() -> Optional[str]:
    """Devuelve el nombre del contenedor de sesión si sigue en ejecución.

    Lee el id de ``~/.snapcontext/session_id.txt`` (o usa el estado en
    memoria) y comprueba con ``docker inspect`` que el contenedor está
    ``running``. Si no, devuelve ``None``.
    """
    global _SESION_NOMBRE
    sid = _documento_sesion()
    if not sid:
        return _SESION_NOMBRE or None
    nombre = _nombre_por_id(sid)
    try:
        proc = _run(["docker", "inspect", "-f", "{{.State.Running}}", nombre],
                    timeout=60)
        if proc.returncode == 0 and str(proc.stdout or "").strip() == "true":
            _SESION_NOMBRE = nombre
            return nombre
    except (OSError, subprocess.TimeoutExpired):
        pass
    _SESION_NOMBRE = None
    return None


def comando_en_sesion(comando: str) -> str:
    """Devuelve la cadena de shell que ejecuta ``comando`` en la sesión.

    Usa ``docker exec snap-session-<id> sh -c <comando>``. El contenedor se
    creó con ``/workspace`` como directorio de trabajo, así que ``docker exec``
    hereda ese directorio y el montaje del proyecto.
    """
    nombre = _SESION_NOMBRE or obtener_sesion()
    if not nombre:
        return comando
    return shlex.join(["docker", "exec", nombre, "sh", "-c", comando])


def ejecutar_en_sesion(comando: str, timeout: int = 120,
                       capture_output: bool = True):
    """Ejecuta ``comando`` dentro del contenedor de sesión.

    Devuelve ``(codigo_retorno, stdout, stderr)`` como :func:`snapcontext._ejecutar_comando`.
    Con ``capture_output=False`` la salida se muestra en tiempo real y los
    dos últimos campos son vacíos. Si no hay sesión activa devuelve
    ``(-1, '', 'no hay sesión Docker activa')``.
    """
    nombre = _SESION_NOMBRE or obtener_sesion()
    if not nombre:
        return (-1, "", "No hay una sesión Docker activa.")
    cmd = shlex.join(["docker", "exec", nombre, "sh", "-c", comando])
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=capture_output,
            text=bool(capture_output), errors="replace" if capture_output else None,
            timeout=timeout, creationflags=_flags())
        if not capture_output:
            return (proc.returncode, "", "")
        return (proc.returncode, proc.stdout or "", proc.stderr or "")
    except subprocess.TimeoutExpired:
        return (-1, "", f"El comando tardó demasiado en la sesión (timeout={timeout}s)")
    except OSError as exc:
        return (-1, "", f"Error ejecutando en la sesión: {exc}")
def destruir_sesion() -> bool:
    """Detiene y elimina el contenedor de sesión y borra el id guardado.

    Es idempotente: si no hay sesión activa no hace nada. Devuelve ``True`` si
    se intentó limpiar una sesión.
    """
    global _SESION_NOMBRE, _SESION_DIRECTORIO
    nombre = _SESION_NOMBRE
    if nombre is None:
        sid = _documento_sesion()
        nombre = _nombre_por_id(sid) if sid else None
    if not nombre:
        return False
    _informar("🐳 Destruyendo sesión Docker...")
    try:
        _run(["docker", "rm", "-f", nombre], timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _avisar(f"No se pudo eliminar el contenedor de sesión '{nombre}': {exc}")
    try:
        if SESSION_ID_PATH.exists():
            SESSION_ID_PATH.unlink()
    except OSError:
        pass
    _SESION_NOMBRE = None
    _SESION_DIRECTORIO = None
    return True


# ---------------------------------------------------------------------------
# Limpieza de huérfanos
# ---------------------------------------------------------------------------
def _listar_contenedores_sesion() -> List[str]:
    """Nombres de contenedores ``snap-session-*`` (detenidos o en ejecución)."""
    try:
        proc = _run(["docker", "ps", "-a",
                     "--filter", f"name={SESION_PREFIJO}",
                     "--format", "{{.Names}}"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [n for n in (proc.stdout or "").splitlines() if n.strip()]


def limpiar_huérfanos(auto: bool = False) -> int:
    """Elimina contenedores ``snap-session-*`` sobrantes y devuelve cuántos.

    En modo interactivo (``auto=False``) pide confirmación antes de eliminar
    cada contenedor; en ``--auto`` los elimina sin preguntar. Idempotente.
    """
    nombres = _listar_contenedores_sesion()
    if not nombres:
        _informar("🐳 No hay contenedores de sesión huérfanos.")
        return 0
    eliminados = 0
    for nombre in sorted(nombres):
        if not auto:
            from snapcontext import _preguntar_si          # noqa: E402 - diferido
            try:
                if not _preguntar_si(f"¿Eliminar contenedor de sesión '{nombre}'? (s/n): "):
                    continue
            except Exception:                                 # noqa: BLE001
                continue
        _informar(f"🐳 Eliminando contenedor de sesión huérfano '{nombre}'...")
        try:
            _run(["docker", "rm", "-f", nombre], timeout=120)
            eliminados += 1
        except (OSError, subprocess.TimeoutExpired) as exc:
            _avisar(f"No se pudo eliminar '{nombre}': {exc}")
    return eliminados