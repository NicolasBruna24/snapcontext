#!/usr/bin/env python3
"""Frontera de filesystem del Web Gateway (B9.59 §4 / B9.60 §9 / B9.61-B).

Este módulo es el **único** punto por el que pasa toda ruta controlada por el
cliente del Web Gateway antes de tocar el filesystem. La cadena es siempre:

```text
input → validación de forma → resolve (realpath) → containment semántico
      → política (raíz del workspace, tipo de operación) → operación
```

Propiedades garantizadas:

* **Una sola frontera**: no existe una solución por endpoint. Lecturas,
  escrituras, directorios, exploración y ``cwd`` de subprocesos usan las
  mismas primitivas de este módulo.
* **Contención semántica**: ``relative_to`` / ``parents``, nunca
  ``str(...).startswith(str(root))`` (que confundiría ``/workspace`` con
  ``/workspace-other``).
* **Raíz de workspace explícita fuera de loopback**: sin ``--workspace-root``
  el arranque en LAN/Internet **aborta** (fail-closed); nunca se degrada al
  cwd, a ``/`` o a un temporal. Raíces prohibidas: ``/``, ``/home``, el home
  del usuario y cualquier ancestro del home.
* **Symlinks**: la contención se evalúa sobre la ruta **resuelta**; un
  symlink que apunta fuera de la raíz se deniega (archivo, directorio,
  anidado, padre o roto). Un symlink que apunta **dentro** de la raíz se
  permite y se resuelve a su destino real.
* **Fail-closed**: ante cualquier ambigüedad (raíz inválida, ruta inválida,
  absoluta no permitida, traversal, escape por symlink, no resoluble,
  operación desconocida) el resultado es ``DENY``. No hay fallback a cwd,
  a ``/``, a ``/home`` ni a un directorio temporal.
* **Errores sin fuga**: los mensajes públicos son estables y no contienen
  rutas absolutas reales, secretos ni configuración interna; el código de
  error sí distingue ``invalid_path`` / ``outside_workspace`` /
  ``permission_denied`` / ``not_found`` / ``symlink``.

C3 (``utils.escribir_archivo_seguro``) y C2 (``sandbox_utils.clasificar_comando``)
no se sustituyen ni se debilitan: la escritura del gateway sigue delegando
en el safe writer original; este módulo solo decide **qué** ruta se le pasa.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

logger = logging.getLogger("snapcontext.web.filesystem")

#: ``directorio`` por defecto cuando la UI no lo indica (cwd/repo actual),
#: igual que antes de B9.61-B. Solo se usa como *base de la operación*; nunca
#: como raíz de workspace en modo expuesto.
_DIRECTORIO_DEFECTO = "."


class ErrorFrontera(ValueError):
    """Frontera de filesystem denegada. Expone un código estable y público.

    Hereda de :class:`ValueError` (no de ``Exception``) para preservar la
    convención ya establecida por ``utils._validar_ruta_segura`` y no romper
    a los llamadores existentes que capturan ``ValueError``.

    ``str(exc)`` NUNCA contiene la ruta real resuelta ni secretos: solo un
    mensaje corto apto para el cliente.
    """

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.mensaje


class WorkspaceRootInvalido(RuntimeError):
    """Raíz de workspace inválida: el arranque debe abortar (fail-closed).

    Hereda de :class:`RuntimeError` para que ``arrancar_api`` la capture
    junto al resto de fallos de arranque de B9.61-A.
    """


CodigoError = Literal[
    "invalid_path",
    "outside_workspace",
    "not_found",
    "permission_denied",
    "symlink",
    # B9.61-C (C-R1-02): origen remoto con esquema no soportado (`http://`,
    # `ftp://`, `file://`, `a://`…). No se degrada a `LocalPath`.
    "unsupported_origin",
]


def _invalidar(codigo: CodigoError, mensaje: str) -> ErrorFrontera:
    """Construye (y registra) un rechazo de la frontera. Fail-closed."""
    logger.warning("FILESYSTEM/DENY codigo=%s detalle=%s", codigo, mensaje)
    return ErrorFrontera(codigo, mensaje)


# ---------------------------------------------------------------------------
# Validación de la raíz del workspace (B9.59 §4, B9.60 §9)
# ---------------------------------------------------------------------------
def raiz_servidor_por_defecto() -> Path:
    """Raíz de workspace del SERVIDOR para loopback sin ``--workspace-root``.

    Se deriva del entorno confiable (cwd/repo del proceso), **nunca** de un
    request. Se resuelve y canonicaliza una sola vez al arrancar y luego queda
    congelada en la :class:`Frontera`.

    Es la lectura de B9.60 §9 «Raíz = la actual: ``resolver_raiz(directorio)``
    (o cwd/repo)» aplicada al lado servidor: el boundary lo fija el servidor, no
    el cliente. Si el cwd no fuera utilizable, se deniega (fail-closed) en lugar
    de degradar a otro directorio.
    """
    from utils import resolver_raiz

    try:
        return Path(os.path.realpath(resolver_raiz(_DIRECTORIO_DEFECTO)))
    except (RuntimeError, OSError) as exc:
        raise WorkspaceRootInvalido(
            "No se pudo determinar la raíz de workspace del servidor "
            f"({exc.__class__.__name__}). Arranque abortado."
        ) from None


def raiz_prohibida(real: Path) -> bool:
    """``True`` si ``real`` no puede usarse como raíz de workspace.

    Nunca válidas (B9.59 §4): ``/``, ``/home``, el home del usuario y
    cualquier ancestro del home (p. ej. ``/home/otro``), ni la raíz del
    sistema de ficheros.
    """
    if real == Path(real.anchor) or len(real.parts) <= 1:
        return True
    if real in (Path("/home"), Path("/root"), Path("/Users")):
        return True
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):  # home no resoluble: no se valida
        return True
    return real == home or real in home.parents


def resolver_raiz_workspace(valor: str | Path | None, exposicion: str) -> Path:
    """Valida y congela la raíz del workspace. Falla cerrado (fail-closed).

    - ``valor`` vacío/``None``: permitido solo en ``loopback`` (donde la raíz
      efectiva es el ``directorio`` de cada request, según B9.60 §9). Fuera de
      loopback → :class:`WorkspaceRootInvalido` (arranque abortado).
    - Resuelve con ``os.path.realpath`` (normaliza ``..`` y symlinks) y exige
      que exista y sea directorio.
    - Una raíz dada como symlink se resuelve y la raíz **efectiva** es su
      ``realpath`` (contención real, no nominal): si ese ``realpath`` cae en
      una raíz prohibida, la raíz es inválida (B9.60 §9.2). Un symlink roto o
      no resoluble es inválido.
    - Sin permisos de recorrido/lectura → inválida (nunca degradar).

    Devuelve la raíz efectiva ya resuelta (congelada por el llamante).
    """
    crudo = str(valor).strip() if valor is not None else ""
    if not crudo:
        raise WorkspaceRootInvalido(
            "Falta --workspace-root: se exige una raíz de workspace explícita y "
            "confinada. Arranque abortado."
        )
    # B9.59 §4: la raíz explícita del operador debe ser absoluta; una ruta
    # relativa depende del cwd del proceso y no es una frontera estable.
    if not Path(os.path.expanduser(crudo)).is_absolute():
        raise WorkspaceRootInvalido(
            f"--workspace-root debe ser una ruta absoluta: '{crudo}'. "
            "Arranque abortado."
        )
    try:
        real = Path(os.path.realpath(os.path.expanduser(crudo)))
    except (OSError, ValueError) as exc:
        raise WorkspaceRootInvalido(
            f"--workspace-root no resoluble: {crudo} ({exc.__class__.__name__}). Arranque abortado."
        ) from None
    if not real.exists():
        raise WorkspaceRootInvalido(f"--workspace-root no existe: {crudo}. Arranque abortado.")
    if not real.is_dir():
        raise WorkspaceRootInvalido(
            f"--workspace-root no es un directorio: {crudo}. Arranque abortado."
        )
    if raiz_prohibida(real):
        raise WorkspaceRootInvalido(
            f"--workspace-root prohibido (demasiado amplio): {crudo}. Arranque abortado."
        )
    if not os.access(real, os.R_OK | os.X_OK):
        raise WorkspaceRootInvalido(
            f"--workspace-root sin permisos de lectura/recorrido: {crudo}. Arranque abortado."
        )
    return real


# ---------------------------------------------------------------------------
# Primitivas de contención
# ---------------------------------------------------------------------------
def _contener(raiz: Path, candidato: Path) -> bool:
    """Contención **semántica** (nunca comparación de prefijos de texto).

    ``/workspace-other`` NO queda dentro de ``/workspace`` porque se comparan
    componentes de ruta, no cadenas.
    """
    return candidato == raiz or raiz in candidato.parents


def _rechazar_codificacion(ruta: str) -> None:
    """Rechaza traversal codificado en URL o escapes ofuscados.

    Decodifica hasta 3 veces y, si la forma decodificada cambia el
    significado de la ruta (contiene ``..``, pasa a absoluta o trae esquema),
    la deniega. Cierra el bypass de ``%2e%2e%2f`` y variantes.
    """
    actual = ruta
    for _ in range(3):
        try:
            decodificada = unquote(actual)
        except Exception:  # unquote no debería fallar; ante la duda, fail-closed
            raise _invalidar("invalid_path", "Ruta no válida.") from None
        if decodificada == actual:
            break
        actual = decodificada
    if actual != ruta:
        partes = actual.replace("\\", "/").split("/")
        if ".." in partes or actual.startswith("/") or "://" in actual:
            raise _invalidar("invalid_path", "Ruta no válida.")


def _validar_forma_ruta(ruta: object) -> str:
    """Valida la forma bruta de una ruta de cliente (sin tocar el disco)."""
    if not isinstance(ruta, str):
        raise _invalidar("invalid_path", "Ruta no válida.")
    limpia = ruta.strip()
    if not limpia:
        raise _invalidar("invalid_path", "Falta la ruta.")
    if "\x00" in limpia:
        raise _invalidar("invalid_path", "Ruta no válida.")
    _rechazar_codificacion(limpia)
    if limpia.startswith(("/", "\\", "~")) or Path(limpia).is_absolute():
        # La operación del gateway exige siempre rutas relativas a la base.
        raise _invalidar("invalid_path", "Ruta absoluta no permitida.")
    if ".." in Path(limpia).parts:
        raise _invalidar("outside_workspace", "Ruta fuera del workspace.")
    return limpia


def _validar_forma_directorio(directorio: object) -> str:
    """Valida la forma del ``directorio`` de cliente (relativo o absoluto)."""
    if directorio is None:
        return _DIRECTORIO_DEFECTO
    if not isinstance(directorio, (str, Path)):
        raise _invalidar("invalid_path", "Directorio no válido.")
    crudo = str(directorio).strip()
    if not crudo or crudo == _DIRECTORIO_DEFECTO:
        return _DIRECTORIO_DEFECTO
    if "\x00" in crudo:
        raise _invalidar("invalid_path", "Directorio no válido.")
    _rechazar_codificacion(crudo)
    if ".." in Path(crudo).parts:
        raise _invalidar("outside_workspace", "Directorio fuera del workspace.")
    return crudo


# ---------------------------------------------------------------------------
# La frontera
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Frontera:
    """Frontera de filesystem del gateway (inmutable y reutilizable).

    ``raiz`` es **siempre** la raíz de seguridad del servidor, nunca un valor
    derivado del request:

    * LAN/Internet o ``--workspace-root`` explícito → la raíz indicada por el
      operador (B9.59 §4), validada y congelada.
    * loopback sin ``--workspace-root`` → la raíz del **servidor**
      (``resolver_raiz(".")``: cwd/repo), resuelta y congelada al arrancar.

    El campo ``directorio`` de un request es siempre **input atacante**
    (B9.58 §7): se interpreta como subruta de ``raiz`` y nunca la sustituye.
    Un ``directorio`` absoluto solo se acepta si ya cae dentro de la raíz.
    """

    exposicion: str = "loopback"
    raiz: Path | None = None

    def raiz_efectiva(self) -> Path:
        """Raíz de seguridad vigente. Falla cerrado si no hay ninguna."""
        if self.raiz is None:
            raise _invalidar(
                "invalid_path",
                "Raíz de workspace no configurada: la operación se deniega.",
            )
        return self.raiz

    def base_operacion(self, directorio: object = None) -> Path:
        """Resuelve el ``directorio`` de cliente dentro de la raíz del servidor.

        ``directorio`` es input atacante y **nunca** define la frontera:

        * ``None``/``"."`` → la raíz del servidor;
        * relativo (``"src"``) → subruta de la raíz;
        * absoluto (``"/etc"``) → solo si ya está contenido en la raíz; si no,
          ``outside_workspace`` (nunca se convierte en la raíz).
        """
        raiz = self.raiz_efectiva()
        crudo = _validar_forma_directorio(directorio)
        candidato = Path(crudo).expanduser()
        if not candidato.is_absolute():
            candidato = raiz / candidato
        try:
            real = Path(os.path.realpath(candidato))
        except (OSError, ValueError):
            raise _invalidar("invalid_path", "Directorio no válido.") from None
        if not _contener(raiz, real):
            raise _invalidar("outside_workspace", "Directorio fuera del workspace.")
        if not real.is_dir():
            raise _invalidar("not_found", "Directorio no encontrado.")
        return real

    def resolver_ruta(self, ruta: object, base: Path) -> Path:
        """Resuelve una ruta de cliente a un ``Path`` real contenido en ``base``.

        Orden: forma → ``realpath`` → contención semántica. Un symlink (de
        archivo, directorio, anidado, de padre o roto) que resuelva fuera de
        ``base``/raíz se deniega; uno que resuelva dentro se acepta.
        """
        limpia = _validar_forma_ruta(ruta)
        try:
            real = Path(os.path.realpath(base / limpia))
        except (OSError, ValueError):
            raise _invalidar("invalid_path", "Ruta no válida.") from None
        if not _contener(base, real):
            raise _invalidar("outside_workspace", "Ruta fuera del workspace.")
        if not _contener(self.raiz_efectiva(), real):
            raise _invalidar("outside_workspace", "Ruta fuera del workspace.")
        return real

    def resolver_ruta_en(self, ruta: object, directorio: object = None) -> Path:
        """Atajo ``resolver_ruta`` que resuelve antes la base de la operación."""
        return self.resolver_ruta(ruta, self.base_operacion(directorio))

    def resolver_cwd(self, directorio: object = None) -> Path:
        """Directorio de trabajo validado por la frontera (B9.61-B §9).

        Un ``cwd`` controlado por el cliente es una frontera filesystem: solo
        puede ser un directorio que la frontera ha resuelto y confinado.
        """
        return self.base_operacion(directorio)

    # -- lecturas seguras ----------------------------------------------------
    def leer(self, ruta: object, directorio: object = None) -> str:
        """Lee un archivo del workspace de forma segura (B9.58-R F9).

        Tras la resolución de la frontera, la apertura se hace con
        ``O_NOFOLLOW`` (defensa en profundidad frente a un cambio de la ruta
        entre la resolución y el ``open``) y exige que sea un archivo regular.
        """
        base = self.base_operacion(directorio)
        destino = self.resolver_ruta(ruta, base)
        if not destino.exists():
            raise _invalidar("not_found", "Archivo no encontrado.")
        if not destino.is_file():
            raise _invalidar("invalid_path", "No es un archivo.")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(destino, flags)
        except PermissionError:
            raise _invalidar("permission_denied", "Permiso denegado.") from None
        except (FileNotFoundError, NotADirectoryError):
            raise _invalidar("not_found", "Archivo no encontrado.") from None
        except OSError:
            raise _invalidar("symlink", "Ruta no permitida.") from None
        try:
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            raise _invalidar("permission_denied", "No se pudo leer el archivo.") from None

    # -- escrituras seguras --------------------------------------------------
    def escribir(
        self,
        ruta: object,
        contenido: str,
        directorio: object = None,
        *,
        datos_binarios: bytes | None = None,
    ) -> Path:
        """Escribe en el workspace preservando C3 (``escribir_archivo_seguro``).

        El safe writer original **no** se sustituye: sigue protegiendo contra
        symlink replacement (``O_NOFOLLOW``), TOCTOU (``O_EXCL`` en creación),
        padre inseguro y overwrite controlado. Aquí solo se decide *qué* ruta
        relativa se le entrega, ya resuelta y contenida.
        """
        from utils import escribir_archivo_seguro

        base = self.base_operacion(directorio)
        destino = self.resolver_ruta(ruta, base)
        try:
            relativa = destino.relative_to(base).as_posix()
        except ValueError:  # pragma: no cover - ya garantizado por la contención
            raise _invalidar("outside_workspace", "Ruta fuera del workspace.") from None
        escribir_archivo_seguro(relativa, contenido, base, datos_binarios=datos_binarios)
        return destino


# ---------------------------------------------------------------------------
# Frontera activa del proceso
# ---------------------------------------------------------------------------
_CANDADO = threading.Lock()
#: Frontera por defecto: raíz del SERVIDOR (cwd/repo) confinada y congelada.
#: ``crear_app`` la reemplaza por la raíz del operador cuando se indica.
_ACTIVA: Frontera = Frontera(exposicion="loopback", raiz=raiz_servidor_por_defecto())


def frontera_activa() -> Frontera:
    """Frontera vigente para las superficies del Web Gateway."""
    return _ACTIVA


def fijar_frontera(frontera: Frontera) -> Frontera:
    """Fija (y devuelve) la frontera activa del proceso. Usado por ``crear_app``."""
    global _ACTIVA
    with _CANDADO:
        _ACTIVA = frontera
    logger.info(
        "FILESYSTEM/ROOT exposicion=%s raiz_fija=%s",
        frontera.exposicion,
        frontera.raiz is not None,
    )
    return frontera


def configurar_frontera(*, exposicion: str, workspace_root: str | Path | None) -> Frontera:
    """Construye y fija la frontera a partir del modo de exposición.

    - ``loopback`` sin ``--workspace-root``: raíz dinámica por request
      (preserva el uso local existente; no introduce una restricción arbitraria).
    - ``loopback`` **con** ``--workspace-root``: raíz congelada explícita.
    - ``lan``/``internet`` sin raíz → :class:`WorkspaceRootInvalido` (aborta).
    - ``lan``/``internet`` con raíz → raíz congelada y validada.
    """
    if str(workspace_root or "").strip():
        raiz = resolver_raiz_workspace(workspace_root, exposicion)
    elif exposicion == "loopback":
        # Sin --workspace-root en loopback: la raíz la fija el SERVIDOR
        # (cwd/repo), no el `directorio` del request (F-01).
        raiz = raiz_servidor_por_defecto()
    else:
        raise WorkspaceRootInvalido(
            "Modo no-loopback sin --workspace-root: se exige una raíz de "
            "workspace explícita y confinada. Arranque abortado."
        )
    fijar_frontera(Frontera(exposicion=exposicion, raiz=raiz))
    return frontera_activa()


def reiniciar_frontera() -> Frontera:
    """Vuelve a la frontera por defecto: raíz del servidor (uso de tests)."""
    fijar_frontera(Frontera(exposicion="loopback", raiz=raiz_servidor_por_defecto()))
    return frontera_activa()


__all__ = [
    "ErrorFrontera",
    "Frontera",
    "WorkspaceRootInvalido",
    "configurar_frontera",
    "fijar_frontera",
    "frontera_activa",
    "raiz_prohibida",
    "raiz_servidor_por_defecto",
    "reiniciar_frontera",
    "resolver_raiz_workspace",
]
