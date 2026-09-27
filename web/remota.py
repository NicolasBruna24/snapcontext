#!/usr/bin/env python3
"""Política de ejecución remota del Web Gateway (B9.61-C).

Implementa las decisiones ratificadas en
``docs/B9.61-C-DECISIONES-RATIFICADAS.md``:

- **OD-1** — La allowlist remota de M1 está **vacía**: toda ejecución remota
  se deniega. La capability ``EXECUTE_REMOTE`` sigue existiendo (modelo
  cerrado de B9.61-A), pero no habilita ningún comando.
- **OD-2** — La identidad de un comando es ``(ruta ejecutable resuelta, argv
  normalizado)``. Nunca ``basename`` ni un ``PATH`` mutable.
- **OD-3** — Shells e intérpretes están denegados; no existe vía ``shell=True``.
- **OD-4** — Los procesos remotos reciben un entorno mínimo construido desde
  cero, nunca ``os.environ``.
- **OD-5** — ``EXECUTE_REMOTE`` no concede red: los binarios de red se
  deniegan y la política no abre salida de red.

Alcance estrictamente limitado a la baseline de seguridad. NO implementa
allowlist poblada, signing, sandbox de red ni workers distribuidos.

La política local (C2, :mod:`sandbox_utils`) **no** autoriza por sí sola una
operación remota: son conjuntos separados, y la remota es un subconjunto
estricto (REX-11).
"""

from __future__ import annotations

import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from web.filesystem import ErrorFrontera, _invalidar

logger = logging.getLogger("snapcontext.web.remota")

#: PATH cerrado para la resolución de ejecutables remotos (OD-2). No se hereda
#: de ``os.environ`` y no incluye el cwd del proceso ni el workspace, para que
#: un atacante no pueda decidir qué binario se encuentra.
PATH_REMOTO: tuple[str, ...] = (
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
)

#: Shells e intérpretes: denegados siempre en remoto (OD-3). Lista acotada a
#: los que aparecen de facto en un host; no se amplía por completitud.
BINARIOS_DENEGADOS: frozenset[str] = frozenset(
    {
        # shells
        "sh",
        "bash",
        "zsh",
        "fish",
        "dash",
        "ksh",
        "csh",
        "tcsh",
        "busybox",
        "cmd",
        "cmd.exe",
        "powershell",
        "pwsh",
        # intérpretes
        "python",
        "python2",
        "python3",
        "pythonw",
        "pypy",
        "pypy3",
        "perl",
        "ruby",
        "node",
        "lua",
        "php",
        "deno",
        "bun",
        # gestores de paquetes (ejecutan código de terceros)
        "npm",
        "npx",
        "pnpm",
        "yarn",
        "pip",
        "pip3",
        "uv",
        "poetry",
        # lanzadores de otros comandos
        "xargs",
        "env",
        "eval",
        "exec",
        "source",
        "nice",
        "timeout",
        "setsid",
        "stdbuf",
        "script",
        "su",
        "sudo",
        "doas",
        # red (OD-5: EXECUTE_REMOTE no concede red)
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "telnet",
        "ftp",
        "sftp",
        "ssh",
        "scp",
        "rsync",
    }
)

#: Clave de configuración de la allowlist remota (B9.59 §3 la nombró).
CLAVE_ALLOWLIST_REMOTA = "sandbox_allowlist_remota"

#: Variables que nunca se heredan al proceso remoto (OD-4). El entorno se
#: construye por lista blanca; esto documenta lo excluido para que la
#: frontera sea auditable.
VARIABLES_EXCLUIDAS: frozenset[str] = frozenset(
    {
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "OLLAMA_URL",
        "SNAPCONTEXT_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CLIENT_SECRET",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "NODE_PATH",
        "NODE_OPTIONS",
        "NPM_CONFIG_PREFIX",
        "GIT_CONFIG",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_SSH_COMMAND",
        "BASH_ENV",
        "ENV",
        "IFS",
        "SHELLOPTS",
        "PERL5LIB",
        "RUBYLIB",
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    }
)


@dataclass(frozen=True)
class IdentidadComandoRemoto:
    """Identidad de un comando remoto (OD-2).

    ``nombre_logico`` es informativo; lo que autoriza es ``ruta_resuelta``
    más ``argv``, nunca el ``basename`` por sí solo.
    """

    nombre_logico: str
    ruta_resuelta: str
    argv: tuple[str, ...]

    def __str__(self) -> str:  # pragma: no cover - solo representacion
        return f"{self.nombre_logico} ({self.ruta_resuelta})"


def _bajo(raiz: Path, candidato: Path) -> bool:
    """Contencion por componentes (nunca comparacion de prefijos de texto)."""
    return candidato == raiz or raiz in candidato.parents


def _which_cerrado(nombre: str) -> str | None:
    """Busca ``nombre`` solo en :data:`PATH_REMOTO` (no en el entorno)."""
    for directorio in PATH_REMOTO:
        ruta = Path(directorio) / nombre
        if ruta.is_file() and os.access(ruta, os.X_OK):
            return str(ruta)
    return None


def resolver_ejecutable(nombre: str) -> str:
    """Resuelve y verifica el ejecutable antes de autorizar (OD-2, REX-12).

    Rechaza nombres con separadores o absolutos (ejecutable relativo o
    controlado por el cliente), resuelve con el PATH cerrado, y comprueba que
    la ruta real siga bajo un directorio de sistema y sea un archivo regular
    ejecutable. Un symlink que apunte fuera del PATH cerrado se rechaza
    (anti PATH hijacking).
    """
    if not nombre or (os.sep in nombre) or (os.altsep and os.altsep in nombre):
        raise _invalidar("invalid_path", "Comando no permitido.")
    if os.path.isabs(nombre):
        raise _invalidar("invalid_path", "Comando no permitido.")
    resuelto = _which_cerrado(nombre)
    if not resuelto:
        raise _invalidar("not_found", "Comando no disponible.")
    real = Path(os.path.realpath(resuelto))
    if not any(_bajo(Path(d), real) for d in PATH_REMOTO):
        raise _invalidar("invalid_path", "Comando no permitido.")
    if not real.is_file() or not os.access(real, os.X_OK):
        raise _invalidar("invalid_path", "Comando no permitido.")
    return str(real)


def normalizar_argv(argv: object) -> tuple[str, ...]:
    """Valida el argv como lista estructurada (OD-2). Nunca un string."""
    if isinstance(argv, (str, bytes)):
        # Un string obligaría a decidir cómo se parte: en remoto no se acepta.
        raise _invalidar("invalid_path", "Comando no permitido.")
    if not isinstance(argv, (list, tuple)) or not argv:
        raise _invalidar("invalid_path", "Comando no permitido.")
    if not all(isinstance(a, str) and a for a in argv):
        raise _invalidar("invalid_path", "Comando no permitido.")
    return tuple(argv)


def entorno_minimo(home: Path, tmpdir: Path) -> dict[str, str]:
    """Entorno minimo para un proceso remoto (OD-4, REX-4).

    Se construye **desde cero**: no se parte de ``os.environ`` y ninguna
    variable de :data:`VARIABLES_EXCLUIDAS` puede aparecer.
    """
    return {
        "PATH": os.pathsep.join(PATH_REMOTO),
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def allowlist_remota_efectiva() -> tuple[dict, ...]:
    """Allowlist remota efectiva: **vacia en M1 => DENY ALL** (OD-1).

    La clave ``sandbox_allowlist_remota`` se lee y valida para que la
    estructura quede preparada, pero su contenido NO habilita nada en M1: hasta
    que exista una decisión de producto ratificada, no se ejecuta ningún
    comando remoto. Ausente, vacia o corrupta produce el mismo resultado
    (fail-closed, REX-9).
    """
    try:
        from configuracion import cargar_configuracion

        crudo = (cargar_configuracion() or {}).get(CLAVE_ALLOWLIST_REMOTA)
        if crudo is not None and not isinstance(crudo, list):
            logger.warning("REMOTA allowlist corrupta: se ignora (M1 = DENY ALL)")
    except Exception:  # la config no debe romper la frontera
        logger.debug("REMOTA allowlist no disponible; M1 = DENY ALL")
    return ()


def autorizar_comando_remoto(
    argv: object,
) -> tuple[bool, str, IdentidadComandoRemoto | None]:
    """Punto unico de autorizacion de ejecucion remota (OD-1, OD-2, OD-3).

    Devuelve ``(permitido, motivo, identidad)``. Con la allowlist M1 vacia el
    resultado es siempre ``(False, motivo, None)``: **DENY ALL**.

    Orden fail-closed, sin fallback en ningun paso:

    1. argv estructurado y no vacio (un string se rechaza);
    2. binario no denegado (shells, interpretes, red);
    3. identidad del ejecutable resuelta y verificada;
    4. coincidencia exacta en la allowlist remota (vacia => fin).
    """
    try:
        argv_norm = normalizar_argv(argv)
    except ErrorFrontera as exc:
        return False, exc.mensaje, None

    nombre = Path(argv_norm[0]).name
    if nombre in BINARIOS_DENEGADOS:
        return False, f"binario '{nombre}' no permitido remotamente", None

    try:
        ruta = resolver_ejecutable(nombre)
    except ErrorFrontera as exc:
        return False, exc.mensaje, None

    identidad = IdentidadComandoRemoto(nombre_logico=nombre, ruta_resuelta=ruta, argv=argv_norm)

    permitidos = allowlist_remota_efectiva()
    if not permitidos:
        logger.info("REMOTA/DENY identidad=%s (política M1: DENY ALL)", identidad)
        return False, "ejecución remota deshabilitada (política M1)", None

    for entrada in permitidos:
        if entrada.get("binario") != nombre:
            continue
        if not _argv_permitido(entrada, argv_norm[1:]):
            return False, "subcomando no permitido remotamente", None
        return True, "autorizado por política remota", identidad
    return False, f"binario '{nombre}' fuera de la allowlist remota", None


def _argv_permitido(entrada: dict, argv: tuple[str, ...]) -> bool:
    """Compara el argv contra la entrada de allowlist (OD-2).

    Con M1 vacia no llega aqui; se mantiene DENY por defecto ante cualquier
    forma no reconocida, para que una futura poblacion no abra por descuido.
    """
    permitidos = set(entrada.get("argv_permitidos") or ())
    if not permitidos:
        return False
    return bool(argv) and argv[0] in permitidos


def descripcion_auditoria(argv: object) -> str:
    """Descripcion segura de un comando para logs (nunca secretos)."""
    try:
        argv_norm = normalizar_argv(argv)
    except ErrorFrontera:
        return "<argv invalido>"
    return " ".join(shlex.quote(a) for a in argv_norm[:3])


__all__ = [
    "BINARIOS_DENEGADOS",
    "CLAVE_ALLOWLIST_REMOTA",
    "PATH_REMOTO",
    "VARIABLES_EXCLUIDAS",
    "ErrorFrontera",
    "IdentidadComandoRemoto",
    "allowlist_remota_efectiva",
    "autorizar_comando_remoto",
    "descripcion_auditoria",
    "entorno_minimo",
    "normalizar_argv",
    "resolver_ejecutable",
]
