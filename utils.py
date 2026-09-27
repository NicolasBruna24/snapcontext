"""Utilidades generales y puras de SnapContext (Fase 2, refactor del monolito).

Funciones de propósito general extraídas de ``snapcontext.py``: normalización
de texto, tokenización, resolución de rutas y edición segura de archivos. No
dependen del estado global del agente.

Solo dos funciones dependen de símbolos de la fachada ``snapcontext``
(``PALABRAS_VACIAS`` y ``aviso``); se resuelven con import diferido para evitar
imports circulares, igual que en ``planificador.py``.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from exceptions import RutaInseguraError

__all__ = [
    "RutaInseguraError",
    "_esta_dentro",
    "_leer_archivo",
    "_normalizar_relativa",
    "_validar_ruta_segura",
    "encontrar_raiz_git",
    "escribir_archivo_seguro",
    "normalizar",
    "resolver_raiz",
    "tokenizar",
]


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos. 'botón' -> 'boton' (clave para buscar en español)."""
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def tokenizar(consulta: str) -> list[str]:
    """Convierte la consulta en palabras clave útiles (sin stopwords)."""
    import snapcontext as _sc  # perezoso: evita import circular (PALABRAS_VACIAS)

    tokens = re.findall(r"[a-z0-9_]+", normalizar(consulta))
    return [t for t in tokens if len(t) > 1 and t not in _sc.PALABRAS_VACIAS]


def encontrar_raiz_git(inicio: Path) -> Path | None:
    """Busca hacia arriba un directorio .git partiendo de `inicio`."""
    actual = inicio
    while True:
        if (actual / ".git").exists():
            return actual
        if actual.parent == actual:
            return None
        actual = actual.parent


def resolver_raiz(directorio: str) -> Path:
    """Resuelve el directorio objetivo.

    - Si el usuario pasa `--directorio` explícito, se usa esa ruta tal cual
      (solo se comporta como repo git si contiene .git directamente). Así un
      directorio suelto (p. ej. una copia en %TEMP%) no "hereda" repos git
      de carpetas padre (como el home de usuario).
    - Si no se pasa directorio (por defecto: '.'), se busca la raíz del repo
      git hacia arriba, para que el escaneo funcione desde cualquier subcarpeta
      del proyecto.
    """
    ruta = Path(directorio).expanduser().resolve()
    if not ruta.is_dir():
        raise RuntimeError(f"El directorio no existe: {directorio}")
    if directorio not in (".", ""):
        return ruta
    return encontrar_raiz_git(ruta) or ruta


def _normalizar_relativa(ruta: str) -> str:
    """Normaliza una ruta relativa a POSIX sin '.' ni '..' ni dobles '//'.

    Se usa para que los archivos que pasan a Aider (o que añade el usuario)
    sean siempre rutas limpias relativas al repositorio.
    """
    limpia = ruta.replace("\\", "/").strip()
    if limpia.startswith("./"):
        limpia = limpia[2:]
    partes: list[str] = []
    for p in limpia.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if partes:
                partes.pop()
            continue
        partes.append(p)
    return "/".join(partes)


def _esta_dentro(raiz: Path, relativa: str) -> bool:
    """True si `raiz / relativa` resuelve dentro de `raiz` (bloquea '..')."""
    try:
        (raiz / relativa).resolve().relative_to(raiz.resolve())
        return True
    except ValueError:
        return False


def _validar_ruta_segura(ruta: Path | str, proyecto_base: Path | str) -> Path:
    """Valida que ``ruta`` esté dentro de ``proyecto_base`` (M1, v6.34.13).

    Defensa contra *path traversal* en las escrituras del editor, siguiendo el
    patrón defensivo de :mod:`lsp_client` y :mod:`sandbox_session`:

      1. Resuelve la ruta absoluta (``.resolve()``, que normaliza ``..``,
         enlaces simbólicos y separadores).
      2. Comprueba que quede dentro de ``proyecto_base`` (también resuelto)
         mediante ``Path.relative_to``.
      3. Si está fuera, lanza ``ValueError`` con un mensaje claro.

    Devuelve la ruta resuelta y validada, lista para usar en la escritura.
    """
    base = Path(proyecto_base).resolve()
    destino = Path(ruta).resolve()
    try:
        destino.relative_to(base)
    except ValueError:
        raise ValueError(
            f"Intento de escritura fuera del proyecto: {destino} (el proyecto es {base})"
        ) from None
    return destino


def _leer_archivo(ruta: str | Path) -> str | None:
    """Lee un archivo (ruta relativa o absoluta) y devuelve su contenido.

    Devuelve ``None`` si no existe, es un directorio o falla la lectura
    (el error se registra con ``aviso``). Pensado para ser usado por el chat,
    el orquestador y futuros planificadores autónomos.
    """
    import snapcontext as _sc  # perezoso: evita import circular (aviso)

    try:
        camino = Path(ruta).expanduser()
        if not camino.is_absolute():
            camino = Path.cwd() / camino
        camino = camino.resolve()
        if not camino.is_file():
            _sc.aviso(f"_leer_archivo: no existe o no es archivo: {camino}")
            return None
        return camino.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _sc.aviso(f"_leer_archivo: error leyendo '{ruta}': {exc}")
        return None


def _verificar_padre_seguro(destino: Path, proyecto_root: Path) -> None:
    """Valida que el directorio padre resuelto esté dentro del proyecto
    y no sea un symlink (defensa en profundidad contra TOCTOU en directorios).

    Lanza :class:`RutaInseguraError` si el padre escapa del proyecto o es symlink.
    """
    padre = destino.parent.resolve()
    try:
        if padre != proyecto_root and proyecto_root not in padre.parents:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise RutaInseguraError(f"directorio padre fuera del proyecto: '{padre}'") from exc
    try:
        real_padre = padre.resolve(strict=False)
        if real_padre != padre and real_padre.is_symlink():
            raise RutaInseguraError(f"directorio padre es symlink: '{padre}'")
    except OSError as exc:
        raise RutaInseguraError(
            f"no se pudo verificar el directorio padre '{padre}': {exc}"
        ) from exc


def escribir_archivo_seguro(
    ruta_relativa: str,
    contenido: str,
    proyecto_root: Path,
    *,
    datos_binarios: bytes | None = None,
) -> None:
    """Escribe ``contenido`` (texto) o ``datos_binarios`` (bytes) en ``ruta_relativa``
    dentro de ``proyecto_root`` de forma atómica contra TOCTOU.

    Validación y apertura son una sola operación: se resuelve la ruta completa,
    se verifica que quede dentro del proyecto, y se abre el archivo con
    ``O_NOFOLLOW`` para rechazar symlinks en el momento de la apertura (no
    en un paso previo separado).

    - Si el archivo no existe (creación nueva), valida que el directorio padre
      resuelto esté dentro de proyecto_root y no sea symlink.
    - Lanza :class:`RutaInseguraError` si cualquier check falla; nunca
      falla silenciosamente ni continúa con un path parcialmente validado.

    Args:
        ruta_relativa: ruta relativa al proyecto (p. ej. ``src/main.py``).
        contenido: texto a escribir (codificación utf-8). Ignorado si
            ``datos_binarios`` no es None.
        proyecto_root: directorio raíz del proyecto (debe existir).
        datos_binarios: bytes a escribir. Si no es None, tiene prioridad
            sobre ``contenido``.

    Raises:
        RutaInseguraError: si la ruta escapa del proyecto o apunta a un symlink.
        FileNotFoundError: si el directorio padre no existe y no se puede crear.
        OSError: si falla la escritura subyacente.
    """
    from stat import S_IRUSR, S_IWUSR

    if not proyecto_root.exists():
        raise RutaInseguraError(f"proyecto_root no existe: '{proyecto_root}'")

    # Rechazar explicitamente paths con ".." que intenten escapar, y rutas
    # absolutas (que no estan dentro del proyecto por definion).
    crudo = str(ruta_relativa).replace("\\", "/")
    if crudo.startswith("/"):
        raise RutaInseguraError(f"acceso denegado: '{ruta_relativa}' es una ruta absoluta")
    if ".." in crudo.split("/"):
        raise RutaInseguraError(
            f"acceso denegado: '{ruta_relativa}' contiene '..' (posible path traversal)"
        )

    limpia = _normalizar_relativa(str(ruta_relativa))
    if not limpia:
        raise RutaInseguraError(f"ruta no válida: '{ruta_relativa}'")

    destino = proyecto_root / limpia
    try:
        destino_resuelto = _validar_ruta_segura(destino, proyecto_root.resolve())
    except ValueError as exc:
        raise RutaInseguraError(f"acceso denegado: '{limpia}' fuera del proyecto") from exc

    crear = not destino_resuelto.exists()
    if crear:
        _verificar_padre_seguro(destino_resuelto, proyecto_root.resolve())
        try:
            destino_resuelto.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RutaInseguraError(
                f"no se pudo crear directorio padre para '{limpia}': {exc}"
            ) from exc

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if crear:
        flags |= os.O_EXCL

    fd = None
    try:
        fd = os.open(destino_resuelto, flags, S_IRUSR | S_IWUSR)
        if datos_binarios is not None:
            os.write(fd, datos_binarios)
            os.close(fd)
        else:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=True) as f:
                f.write(contenido)
    except FileExistsError as exc:
        raise RutaInseguraError(
            f"TOCTOU detectado: '{limpia}' cambió a symlink durante la escritura"
        ) from exc
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise RutaInseguraError(f"no se pudo escribir '{limpia}': {exc}") from exc
