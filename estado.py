"""Gestión de estado global de procesos del núcleo (Fase 2).

Extraído del monolito: registro de subprocesos activos (cierre limpio
por señales) y procesos en segundo plano de ``execute_command`` con su
consultor. Los globals del monolito (sandbox, sesión Docker) se leen
como ``_sc.X`` en runtime para mantener la compatibilidad con los tests
(mock.patch sobre snapcontext) y con el rebinding por ``global``.
"""

from pathlib import Path

from presentacion import info

# ---------------------------------------------------------------------------
# Señales y cierre limpio (Ctrl+C / SIGTERM) — multiplataforma
# ---------------------------------------------------------------------------
# Registro de subprocesos activos (servidores Flutter...) para poder cerrarlos
# desde el manejador de señales y no dejar procesos huérfanos.
_PROCESOS_ACTIVOS: set = set()


def _apagar_subprocesos() -> None:
    """Termina todos los subprocesos registrados (no espera a que salgan)."""
    for proceso in list(_PROCESOS_ACTIVOS):
        try:
            if proceso.poll() is None:
                proceso.terminate()
        except (AttributeError, OSError, ValueError):
            pass


# --- Procesos en segundo plano para execute_command (v2.3.0) -----------------
_PROCESOS_FONDO: dict = {}  # pid → estado (para ejecución en background)


def _lanzar_proceso_fondo(comando: str, directorio: str = ".", capture_output: bool = True) -> dict:
    """Lanza ``comando`` en segundo plano (Popen). Devuelve un registro con el PID.

    El proceso queda registrado en ``_PROCESOS_FONDO`` para poder consultarlo
    después con :func:`_estado_proceso_fondo`. Nunca lanza excepciones.
    """
    import snapcontext as _sc

    raiz = Path(directorio).expanduser()
    if not raiz.is_dir():
        return {"ok": False, "error": f"El directorio no existe: {raiz}"}
    # v4.3.0: los procesos en segundo plano también respetan --sandbox.
    if _sc._SANDBOX_ACTIVO:
        # v6.4.0: con --sandbox-session se lanzan dentro del contenedor de sesión.
        if _sc._SESION_DOCKER_SOLICITADA:
            import sandbox_session as ss

            if _sc._asegurar_sesion_docker(str(raiz)):
                info(f"🐳 Ejecutando en sesión Docker (background): {comando}")
                comando = ss.comando_en_sesion(comando)
                raiz = Path.cwd()
            else:
                info(f"[sandbox] Ejecutando en contenedor (background): {comando}")
                comando = _sc._envolver_sandbox(comando, str(raiz))
                raiz = Path.cwd()
        else:
            info(f"[sandbox] Ejecutando en contenedor (background): {comando}")
            comando = _sc._envolver_sandbox(comando, str(raiz))
            raiz = Path.cwd()
    # seguridad: en background no hay confirmación interactiva útil;
    # si no hay sandbox y el comando es peligroso, se rechaza sin lanzarlo.
    if not _sc._SANDBOX_ACTIVO and _sc._es_comando_peligroso(comando):
        return {"ok": False, "error": f"Comando peligroso rechazado (sin sandbox): {comando}"}
    try:
        # seguridad (A1): lanzamiento en background vía helper con política
        # (shell=False para comandos simples; shell=True solo para pipes,
        #  tras validar que no es peligroso — ya pre-filtrado arriba).
        import sandbox_utils as _su

        proc = _su.lanzar_proceso_fondo_seguro(
            comando, cwd=str(raiz), capturar_salida=capture_output
        )
        registro = {
            "ok": True,
            "pid": proc.pid,
            "proceso": proc,
            "estado": "ejecutando",
            "comando": comando,
            "codigo_retorno": None,
            "stdout": "",
            "stderr": "",
        }
        _PROCESOS_FONDO[proc.pid] = registro
        return {"ok": True, "pid": proc.pid, "comando": comando}
    except OSError as exc:
        return {"ok": False, "error": f"Error lanzando '{comando}': {exc}"}


def _estado_proceso_fondo(pid: int) -> dict:
    """Consulta el estado de un proceso lanzado en segundo plano.

    Si ya terminó, captura su stdout/stderr (si se pidió captura) y lo marca
    como finalizado. Devuelve un dict con ``estado``, ``pid`` y (si terminó)
    ``codigo_retorno``, ``stdout`` y ``stderr``.
    """
    registro = _PROCESOS_FONDO.get(pid)
    if registro is None:
        return {
            "ok": False,
            "estado": "desconocido",
            "pid": pid,
            "error": f"no hay proceso en segundo plano con pid {pid}",
        }
    proc = registro.get("proceso")
    if proc is None:
        return {"ok": True, "estado": registro.get("estado", "desconocido"), "pid": pid}
    if proc.poll() is None:
        registro["estado"] = "ejecutando"
        return {"ok": True, "estado": "ejecutando", "pid": pid}
    # Ya terminó: capturar salida si se pidió.
    if proc.stdout is not None:
        try:
            registro["stdout"] = (proc.stdout.read() or "") if proc.stdout else ""
        except Exception:
            registro["stdout"] = ""
    if proc.stderr is not None:
        try:
            registro["stderr"] = (proc.stderr.read() or "") if proc.stderr else ""
        except Exception:
            registro["stderr"] = ""
    registro["codigo_retorno"] = proc.returncode
    registro["estado"] = "finalizado"
    return {
        "ok": True,
        "estado": "finalizado",
        "pid": pid,
        "codigo_retorno": proc.returncode,
        "stdout": registro["stdout"],
        "stderr": registro["stderr"],
    }
