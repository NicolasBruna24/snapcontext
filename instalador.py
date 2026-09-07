#!/usr/bin/env python3
"""Instalador y configuración del sistema para SnapContext.

Extraído del monolito ``snapcontext.py`` (fase 2 del refactor).

Contiene el subsistema de gestión del PATH del usuario (flag ``--setup-path``):

  - :func:`_candidatos_carpetas_scripts`  — rutas típicas de instalación.
  - :func:`_localizar_carpeta_scripts`    — localiza la carpeta de ejecutables.
  - :func:`_guardar_path_windows`         — persiste el PATH (setx / winreg).
  - :func:`snapcontext_en_path`           — consulta si el comando es accesible.
  - :func:`configurar_path`               — orquestador de ``--setup-path``.

Nota sobre dependencias circulares: este módulo NO importa ``snapcontext`` a
nivel de módulo (sería un ciclo ``snapcontext → instalador → snapcontext``).
Las funciones de presentación/UI se importan de forma diferida, dentro de
cada función (mismo patrón que :mod:`seguridad`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

__all__ = ["configurar_path", "snapcontext_en_path"]


def _candidatos_carpetas_scripts() -> list[str]:
    """Devuelve, en orden de prioridad, las carpetas donde suele instalarse el
    comando `snapcontext` (carpetas de scripts/bin de Python), sin comprobar
    todavía si existen. Prioriza el intérprete Python en uso."""
    candidatos: list[str] = []

    # 1) Carpeta de scripts del intérprete Python en uso (donde pip y
    #    `pip install -e .` registran el comando `snapcontext`). Prioridad máxima.
    try:
        import sysconfig

        candidatos.append(sysconfig.get_path("scripts"))
    except Exception:
        pass

    # 2) Carpeta Scripts hermana de python.exe.
    dir_python = os.path.dirname(sys.executable)
    candidatos.append(os.path.join(dir_python, "Scripts"))

    # 3) Rutas típicas de instalaciones de usuario en Windows.
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        candidatos.append(os.path.join(appdata, "Python", "Scripts"))
    if localappdata:
        candidatos.append(os.path.join(localappdata, "Programs", "Python", "Scripts"))
        # Python3X: localizaciones con número de versión (p. ej. Python313).
        base_prog = os.path.join(localappdata, "Programs", "Python")
        try:
            for nombre in sorted(os.listdir(base_prog)):
                if nombre.lower().startswith("python"):
                    candidatos.append(os.path.join(base_prog, nombre, "Scripts"))
        except OSError:
            pass

    # 4) Ejecutable empaquetado (PyInstaller / cx_Freeze): su propia carpeta.
    if getattr(sys, "frozen", False):
        candidatos.append(os.path.dirname(os.path.abspath(sys.executable)))

    # Eliminar vacíos y duplicados conservando el orden de prioridad.
    vistos = set()
    unicos: list[str] = []
    for c in candidatos:
        if c and c not in vistos:
            vistos.add(c)
            unicos.append(c)
    return unicos


def _localizar_carpeta_scripts() -> str | None:
    """Localiza la carpeta donde se registra el comando `snapcontext`.

    Prioriza `sysconfig.get_path("scripts")`: si esa carpeta existe se devuelve
    directamente, sin exigir que contenga el ejecutable, porque en instalaciones
    en modo editable el stub `snapcontext` puede tener otro nombre o no estar
    todavía en el mismo lugar que apunta el sysconfig.

    Si esa carpeta no existe, se devuelve la primera de las demás rutas típicas
    de Python que sí exista. Nunca devuelve el directorio del proyecto actual.
    """
    marcadores = ("snapcontext.exe", "snapcontext", "snapcontext.bat")
    intentadas: list[str] = []

    for c in _candidatos_carpetas_scripts():
        if not os.path.isdir(c):
            intentadas.append(c)
            continue
        # En ejecutables empaquetados la carpeta propia no es de scripts;
        # exigimos ahí el ejecutable para no devolver una carpeta cualquiera.
        if getattr(sys, "frozen", False):
            if any(os.path.exists(os.path.join(c, m)) for m in marcadores):
                return c
            continue
        return c

    if intentadas:
        from snapcontext import depurar  # import diferido (evita ciclo)

        depurar("--setup-path: rutas probadas sin éxito: " + "; ".join(intentadas))
    return None


def _guardar_path_windows(nuevo_path: str) -> bool:
    """Persiste el PATH en la variable de entorno de USUARIO.

    Intenta `setx PATH` (sin /M, no requiere admin) y, si no esta disponible,
    escribe directamente en HKCU\\Environment con `winreg`."""
    try:
        res = subprocess.run(
            ["setx", "PATH", nuevo_path],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if res.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as clave:
            winreg.SetValueEx(clave, "PATH", 0, winreg.REG_EXPAND_SZ, nuevo_path)
        return True
    except Exception:
        pass

    return False


def snapcontext_en_path() -> bool:
    """True si el comando 'snapcontext' es accesible desde el PATH."""
    return shutil.which("snapcontext") is not None


def configurar_path() -> int:
    """Configura el PATH del usuario en Windows (--setup-path).

    Es independiente de la consulta: localiza la carpeta de ejecutables, la
    añade al PATH persistente del usuario y sale. Código 0 = éxito.
    """
    # import diferido (evita ciclo snapcontext → instalador → snapcontext)
    from snapcontext import _CYAN, _pintar, _preguntar_si, aviso, error, exito, info

    if not sys.platform.startswith("win"):
        error("--setup-path solo funciona en Windows.")
        return 1

    info("Configurando el PATH del usuario para Windows...")
    carpeta = _localizar_carpeta_scripts()
    if not carpeta:
        error("No se pudo localizar automáticamente la carpeta de ejecutables de SnapContext.")
        aviso("Rutas típicas donde suele instalarse el comando 'snapcontext':")
        for r in _candidatos_carpetas_scripts():
            if r:
                aviso("  - " + r)

        # Fallback interactivo: ofrecer indicar la ruta manualmente.
        if _preguntar_si("¿Quieres indicar la carpeta de Scripts manualmente?"):
            try:
                manual = (
                    input(
                        _pintar(
                            "Ruta de la carpeta Scripts (p. ej. C:\\...\\Python313\\Scripts): ",
                            _CYAN,
                        )
                    )
                    .strip()
                    .strip('"')
                    .strip("'")
                )
            except EOFError:  # entrada no interactiva → abandonar
                manual = ""
            if manual and os.path.isdir(manual):
                carpeta = manual
            else:
                aviso("Ruta no válida o inexistente: no se modificará el PATH.")

        if not carpeta or not os.path.isdir(carpeta):
            aviso(
                "Añade la ruta de Scripts manualmente al PATH del usuario "
                "o vuelve a ejecutar --setup-path tras instalar SnapContext."
            )
            aviso("SnapContext seguirá funcionando con: 'python -m snapcontext'")
            return 1

    path_actual = os.environ.get("PATH", "")
    if carpeta in [p for p in path_actual.split(";") if p]:
        exito("'" + carpeta + "' ya está en el PATH del usuario (sin cambios).")
        return 0

    nuevo = carpeta + ";" + path_actual
    os.environ["PATH"] = nuevo

    if _guardar_path_windows(nuevo):
        exito("'" + carpeta + "' añadido al PATH persistente del usuario.")
        info("Reinicia tu terminal para que el cambio surta efecto.")
        info("Si instalaste Chocolatey, ejecuta 'refreshenv'.")
        return 0

    error("No se pudo guardar el PATH de forma permanente.")
    aviso("El PATH solo queda activo para esta sesion de terminal.")
    return 1
