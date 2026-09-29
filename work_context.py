#!/usr/bin/env python3
"""Work Context (B11) — contenedor `.work/` y escritor atómico de documentos.

Núcleo mínimo derivado del contrato arquitectónico B8/B9:

* **Definición única del contenedor** (B11-A): :data:`WORK_CONTAINER`, usada
  por la exclusión del staging automático (B11-D) y por la frontera de
  contexto PROJECT (B11-E). El literal no se repite en cada lista.
* **Escritor atómico** (B11-B): el contenido se escribe primero en un fichero
  temporal del **mismo directorio** y se sustituye el destino con
  ``os.replace``. Si algo falla antes del reemplazo, el contenido anterior
  queda intacto (B8 §9 I9 / B9 §19 C1).
* **Creación mínima de un trabajo** (B11-C): ``.work/<id>/`` con los tres
  documentos canónicos de B9 (``state.md``, ``decisions.md``,
  ``assertions.md``) y su señalización.

El canal es un artefacto **del proyecto**: no depende de SnapContext para
existir, interpretarse ni conservarse, y no sustituye a Git como fuente de
verdad de Git. Este módulo **no** implementa lifecycle, ratificación,
resolución de conflictos ni descubrimiento de trabajos: solo el formato
físico seguro (B11 §10-§11).

Nota de diseño: no se apoya en ``utils.escribir_archivo_seguro`` porque B10
§2.3 concluyó que ese helper **no** debe usarse para resolver C1 (escribe con
``O_TRUNC`` in situ y su semántica de seguridad está ratificada por tests).
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from exceptions import ContratoEstadoInvalidoError, RutaInseguraError

__all__ = [
    "DOCUMENTOS_CANONICOS",
    "DOCUMENTO_ASERCIONES",
    "DOCUMENTO_CONTENEDOR",
    "DOCUMENTO_DECISIONES",
    "DOCUMENTO_ESTADO",
    "EXCLUSION_STAGING_GIT",
    "VALIDIDAD_NO_EVALUADA",
    "WORK_CONTAINER",
    "UltimaVerificacion",
    "VeredictoObsoleto",
    "WorkState",
    "actualizar_estado",
    "crear_trabajo",
    "es_parte_contenedor",
    "escribir_documento_trabajo",
    "esta_dentro_de_trabajo",
    "leer_estado",
    "leer_ultima_verificacion",
    "leer_veredictos_obsoletos",
    "registrar_veredicto_obsoleto",
    "registrar_verificacion",
    "ruta_contenedor",
]

# ---------------------------------------------------------------------------
# B11-A — Definición única del contenedor
# ---------------------------------------------------------------------------
#: Contenedor de trabajos, relativo a la raíz del proyecto (B8/R1-R6, B9 §13).
#: Es la **única** fuente del literal: cualquier frontera que necesite saber
#: "qué es WORK y qué no" debe referenciar esta constante.
WORK_CONTAINER = ".work"

#: Pathspec de exclusión para ``git add`` (B11-D). Las comillas dobles son
#: válidas en ``sh`` (evitan que los paréntesis se interpreten como subshell)
#: y en ``cmd.exe`` (donde el intérprete no entiende comillas simples).
EXCLUSION_STAGING_GIT = f'":(exclude){WORK_CONTAINER}"'

#: Documentos canónicos por trabajo (B9 §4). El orden es el de lectura en frío.
DOCUMENTO_ESTADO = "state.md"
DOCUMENTO_DECISIONES = "decisions.md"
DOCUMENTO_ASERCIONES = "assertions.md"
DOCUMENTOS_CANONICOS = (DOCUMENTO_ESTADO, DOCUMENTO_DECISIONES, DOCUMENTO_ASERCIONES)

#: Señalización del contenedor (B9 §5.4), no canónica: describe, no es verdad.
DOCUMENTO_CONTENEDOR = "README.md"

#: Identificador de contrato documental (permite lecturas futuras compatibles).
VERSION_CONTRATO = "b9-1"

#: Identificador de trabajo: minúsculas, sin separadores de ruta ni espacios.
_RE_ID_TRABAJO = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def ruta_contenedor(raiz: str | Path) -> Path:
    """Devuelve la ruta del contenedor WORK dentro de ``raiz``."""
    return Path(raiz) / WORK_CONTAINER


def es_parte_contenedor(parte: str) -> bool:
    """True si ``parte`` (un componente de ruta) es el contenedor WORK.

    Pensado para las listas de exclusión que trabajan por componentes
    (``any(p in IGNORADOS for p in partes)``).
    """
    return parte == WORK_CONTAINER


def esta_dentro_de_trabajo(ruta: str | Path) -> bool:
    """True si algún componente de ``ruta`` es el contenedor WORK.

    Se usa para excluir WORK del contexto PROJECT **implícito** (B11-E) sin
    afectar a la lectura explícita por ruta.
    """
    texto = str(ruta).replace("\\", "/")
    return any(parte == WORK_CONTAINER for parte in texto.split("/") if parte)


# ---------------------------------------------------------------------------
# B11-B — Escritor atómico
# ---------------------------------------------------------------------------
def _normalizar_relativa(crudo: str) -> list[str]:
    """Valida una ruta relativa y devuelve sus componentes (sin ``.`` vacíos).

    Rechaza: ruta vacía, byte nulo, ruta absoluta (POSIX o con letra de
    unidad) y cualquier componente ``..`` (path traversal).
    """
    texto = str(crudo).replace("\\", "/").strip()
    if not texto:
        raise RutaInseguraError("ruta de trabajo vacía")
    if "\x00" in texto:
        raise RutaInseguraError("ruta de trabajo con byte nulo")
    if texto.startswith("/") or re.match(r"^[A-Za-z]:", texto):
        raise RutaInseguraError(f"ruta de trabajo absoluta: {crudo!r}")
    partes = [p for p in texto.split("/") if p not in ("", ".")]
    if not partes:
        raise RutaInseguraError(f"ruta de trabajo sin componentes: {crudo!r}")
    if any(p == ".." for p in partes):
        raise RutaInseguraError(f"ruta de trabajo con '..': {crudo!r}")
    return partes


def _validar_destino(ruta_relativa: str, raiz_res: Path) -> Path:
    """Resuelve y valida el destino de un documento WORK.

    Garantías: la ruta es relativa y sin ``..``; su primer componente es el
    contenedor WORK; se construye siempre desde ``raiz_res`` (no puede
    escapar del proyecto) y ningún componente existente es un symlink.
    Crea los directorios intermedios que falten dentro del contenedor.
    """
    partes = _normalizar_relativa(ruta_relativa)
    if partes[0] != WORK_CONTAINER:
        raise RutaInseguraError(
            f"los documentos de trabajo viven dentro de '{WORK_CONTAINER}/': {ruta_relativa!r}"
        )
    actual = raiz_res
    for parte in partes[:-1]:
        actual = actual / parte
        if actual.is_symlink():
            raise RutaInseguraError(f"componente symlink en la ruta de trabajo: {actual}")
        if actual.exists() and not actual.is_dir():
            raise RutaInseguraError(f"componente no es directorio: {actual}")
        actual.mkdir(exist_ok=True)
    destino = actual / partes[-1]
    if destino.is_symlink():
        # Defensa en profundidad: ``os.replace`` sustituiría el enlace, nunca
        # escribiría a través de él, pero un destino symlink es un estado
        # anómalo para un documento del canal.
        raise RutaInseguraError(f"el destino de trabajo es un symlink: {destino}")
    return destino


def _descartar_temporal(ruta: Path) -> None:
    """Elimina el fichero temporal de trabajo, ignorando errores."""
    with contextlib.suppress(OSError):
        ruta.unlink()


def _escribir_temporal(destino: Path, contenido: str, modo: int | None = None) -> Path:
    """Escribe ``contenido`` en un temporal del mismo directorio que ``destino``.

    El temporal se crea con ``mkstemp`` (``O_EXCL``, 0600), de modo que no
    colisiona con nada existente. Ante cualquier fallo se elimina el temporal
    y se propaga la excepción sin haber tocado el destino.
    """
    fd, nombre = tempfile.mkstemp(dir=str(destino.parent), prefix=".tmp-work-", suffix=".md")
    tmp = Path(nombre)
    try:
        if modo is not None:
            os.chmod(nombre, modo)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as manejador:
            manejador.write(contenido)
        return tmp
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        _descartar_temporal(tmp)
        raise


def _reemplazar(temporal: Path, destino: Path) -> None:
    """Sustitución atómica del destino por el temporal (mismo sistema de ficheros).

    Se aísla en una función propia para poder verificar de forma determinista
    el fallo "antes del reemplazo" en los tests, sin matar procesos.
    """
    os.replace(temporal, destino)


def escribir_documento_trabajo(
    ruta_relativa: str | Path,
    contenido: str,
    raiz: str | Path = ".",
) -> Path:
    """Escribe un documento WORK de forma atómica. Devuelve la ruta del destino.

    La escritura es "todo o nada" respecto al contenido anterior: el texto se
    materializa en un temporal del mismo directorio y solo entonces se
    sustituye el destino. Una interrupción (excepción, ``KeyboardInterrupt``,
    proceso terminado) antes del reemplazo deja el documento previo intacto.
    """
    raiz_res = Path(raiz).expanduser().resolve()
    if not raiz_res.is_dir():
        raise RutaInseguraError(f"la raíz del proyecto no existe: {raiz}")

    destino = _validar_destino(str(ruta_relativa), raiz_res)

    modo_previo: int | None = None
    if destino.exists():
        with contextlib.suppress(OSError):
            modo_previo = stat.S_IMODE(destino.stat().st_mode)

    temporal = _escribir_temporal(destino, contenido, modo_previo)
    try:
        _reemplazar(temporal, destino)
    except BaseException:
        _descartar_temporal(temporal)
        raise
    return destino


# ---------------------------------------------------------------------------
# B11-C — Creación mínima de un trabajo
# ---------------------------------------------------------------------------
def _instante_utc() -> str:
    """Marca temporal UTC (ISO-8601, sin depender de la máquina)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lista(items: Sequence[str] | None, vacio: str) -> str:
    """Serializa una lista de líneas de texto (o un marcador de vacío)."""
    limpias = [str(i).strip() for i in (items or []) if str(i).strip()]
    if not limpias:
        return f"- {vacio}"
    return "\n".join(f"- {linea}" for linea in limpias)


def _cabecera(kind: str, work_id: str, proyecto: str) -> str:
    """Cabecera de etiquetas fijas común a los documentos del canal (B9 §14)."""
    return f"Kind: {kind}\nContract: {VERSION_CONTRATO}\nWork: {work_id}\nProject: {proyecto}\n"


def _contenido_estado(
    work_id: str,
    proyecto: str,
    titulo: str,
    objetivo: str,
    criterios: Sequence[str] | None,
    restricciones: Sequence[str] | None,
    instante: str,
) -> str:
    """Contenido inicial de ``state.md`` (verdad vigente del trabajo)."""
    return (
        f"# Trabajo: {titulo or work_id}\n\n"
        + _cabecera("work-state", work_id, proyecto)
        + "\n## Identidad\n\n"
        f"- Id: {work_id}\n"
        f"- Título: {titulo or work_id}\n"
        f"- Declarado: {instante}\n"
        f"- Proyecto: {proyecto}\n\n"
        "## Mandato (ratificado)\n\n"
        "Solo la persona responsable ratifica cambios en esta sección: para\n"
        "modificarla se añade una entrada en `decisions.md`.\n\n"
        "### Objetivo\n\n"
        f"{objetivo.strip() or '(pendiente de declarar)'}\n\n"
        "### Criterios de aceptación\n\n"
        f"{_lista(criterios, '(pendiente de declarar)')}\n\n"
        "### Alcance incluido\n\n"
        f"{_lista(None, '(no declarado)')}\n\n"
        "### Alcance excluido\n\n"
        f"{_lista(None, '(no declarado)')}\n\n"
        "### Restricciones\n\n"
        f"{_lista(restricciones, '(ninguna declarada)')}\n\n"
        "### Dependencias de entorno declaradas\n\n"
        f"{_lista(None, '(ninguna declarada)')}\n\n"
        "## Estado operativo\n\n"
        "- Ciclo de vida: declarado\n"
        f"- Trabajo completado: {_lista(None, '(nada registrado)')}\n"
        f"- Trabajo pendiente: {_lista(None, '(nada registrado)')}\n"
        f"- Bloqueos: {_lista(None, '(ninguno)')}\n"
        "- Siguiente paso: (pendiente de declarar)\n"
        "- Comando de verificación: (pendiente de declarar)\n"
        "- Último veredicto de verificación: (no hay verificación registrada)\n\n"
        "## Referencias Git\n\n"
        "- Base: (pendiente)\n"
        "- Actual: (pendiente)\n"
        "- Rama: (pendiente)\n"
        "- PR/issue: (ninguno)\n\n"
        "## Cierre\n\n"
        "- Estado: (abierto)\n"
        "- Motivo: (n/a)\n\n"
        "## Pendiente de ratificación\n\n"
        "Las propuestas pendientes no se enumeran aquí (sería una segunda\n"
        "verdad): `assertions.md` es el documento autoritativo.\n"
    )


def _contenido_decisiones(work_id: str, proyecto: str) -> str:
    """Contenido inicial de ``decisions.md`` (ratificado + motivo, append-only)."""
    return (
        f"# Decisiones ratificadas: {work_id}\n\n"
        + _cabecera("work-decisions", work_id, proyecto)
        + "\n## Cómo se registra una decisión\n\n"
        "Documento **append-only**: las entradas no se editan. Para cambiar una\n"
        "decisión se añade otra que indique expresamente qué sustituye mediante\n"
        "el campo `Sustituye a:`. La entrada original permanece inmutable y no se\n"
        "modifica ni se elimina su contenido.\n\n"
        "Toda entrada declara: Id, Enunciado, Ámbito, Motivo, Propuesta de origen,\n"
        "Ratificado por, Instante, Sustituye a, Estado.\n\n"
        "## Entradas\n\n"
        "(ninguna todavía)\n"
    )


def _contenido_aserciones(work_id: str, proyecto: str) -> str:
    """Contenido inicial de ``assertions.md`` (observaciones y propuestas)."""
    return (
        f"# Aserciones: {work_id}\n\n"
        + _cabecera("work-assertions", work_id, proyecto)
        + "\n## Cómo se registra una aserción\n\n"
        "Documento **append-only**. Cada aserción declara: Id, Tipo\n"
        "(`observación` o `propuesta`), Autor, Instante, Objetivo, Contenido,\n"
        "Base (contra qué estado se formuló), Evidencia y Destino.\n\n"
        "Una observación o una propuesta **no** son verdad vigente: la verdad\n"
        "vigente está en `state.md` y las ratificaciones en `decisions.md`.\n\n"
        "## Aserciones\n\n"
        "(ninguna todavía)\n"
    )


def _contenido_readme_trabajo(work_id: str, titulo: str, proyecto: str, instante: str) -> str:
    """Señalización del trabajo: qué documento es autoritativo de qué (B9 §5.4).

    No contiene estado, fechas de avance ni propuestas: describe, no es fuente
    de verdad, para no crear una segunda verdad dentro del trabajo.
    """
    return (
        f"# Trabajo `{work_id}`\n\n"
        + _cabecera("work-index", work_id, proyecto)
        + "\nEste directorio contiene **un** trabajo declarado.\n\n"
        "## Documentos\n\n"
        f"- `{DOCUMENTO_ESTADO}` — verdad vigente (mandato ratificado + estado operativo).\n"
        f"- `{DOCUMENTO_DECISIONES}` — decisiones ratificadas y su motivo (append-only).\n"
        f"- `{DOCUMENTO_ASERCIONES}` — observaciones y propuestas (append-only).\n\n"
        "Ninguno de estos documentos requiere SnapContext para interpretarse: son\n"
        "texto plano, y Git sigue siendo la única fuente de verdad sobre Git.\n\n"
        "## Identidad\n\n"
        f"- Título: {titulo or work_id}\n"
        f"- Declarado: {instante}\n"
    )


def _contenido_readme_contenedor() -> str:
    """Señalización del contenedor WORK (B9 §5.4)."""
    return (
        "# Contenedor de trabajos (`.work/`)\n\n"
        f"Contract: {VERSION_CONTRATO}\n"
        "Kind: work-container\n\n"
        "Cada subdirectorio es **una unidad de trabajo declarada** y contiene su\n"
        "propio estado (`state.md`), sus decisiones ratificadas (`decisions.md`) y\n"
        "sus aserciones (`assertions.md`).\n\n"
        "Este contenedor **no** es un índice ni un registro: el listado de\n"
        "subdirectorios es el descubrimiento y no existe una fuente de verdad\n"
        "central.\n\n"
        "## Qué no es\n\n"
        "- No es contexto de proyecto: los documentos de trabajo no se incluyen\n"
        "  automáticamente en el contexto PROJECT ni en `CLAUDE.md`.\n"
        "- No es una caché: las cachés nunca son fuente de verdad.\n"
        "- No es propiedad de ninguna herramienta: se lee sin SnapContext.\n\n"
        "## Git\n\n"
        "El contenedor permanece **visible** para Git (untracked y no ignorado),\n"
        "fuera del historial por defecto y sin staging automático. Puede\n"
        "versionarse por decisión explícita del proyecto sin cambiar su\n"
        "ubicación ni su formato.\n"
    )


def _validar_id_trabajo(work_id: str) -> str:
    """Valida el identificador de trabajo (sin rutas, sin traversal)."""
    texto = str(work_id).strip()
    if not _RE_ID_TRABAJO.match(texto) or texto in (".", ".."):
        raise ValueError(
            "identificador de trabajo no válido: usa minúsculas, dígitos, "
            "'.', '_' o '-' (1-64 caracteres, empezando por letra o dígito)"
        )
    return texto


def crear_trabajo(
    work_id: str,
    directorio: str | Path = ".",
    *,
    titulo: str = "",
    objetivo: str = "",
    criterios: Sequence[str] | None = None,
    restricciones: Sequence[str] | None = None,
    instante: str | None = None,
) -> Path:
    """Crea ``.work/<work_id>/`` con los documentos del canal. Devuelve su ruta.

    Resuelve la raíz del proyecto con la resolución existente (sube hasta el
    ancestro con ``.git``), valida el identificador y **no sobrescribe** un
    trabajo existente: si su directorio ya existe, lanza ``FileExistsError``.

    No implementa lifecycle, ratificación ni descubrimiento de trabajos: solo
    el formato físico seguro (B11 §5, §10-§11).
    """
    from utils import resolver_raiz  # diferido: mantiene el módulo libre de ciclos

    raiz_res = Path(resolver_raiz(str(directorio))).resolve()
    if not raiz_res.is_dir():
        raise RutaInseguraError(f"la raíz del proyecto no existe: {directorio}")

    identificador = _validar_id_trabajo(work_id)
    proyecto = raiz_res.name
    declarado = instante or _instante_utc()

    contenedor = ruta_contenedor(raiz_res)
    if contenedor.is_symlink():
        raise RutaInseguraError(f"el contenedor de trabajo es un symlink: {contenedor}")
    contenedor.mkdir(parents=True, exist_ok=True)

    senalizacion = contenedor / DOCUMENTO_CONTENEDOR
    if not senalizacion.exists():
        escribir_documento_trabajo(
            f"{WORK_CONTAINER}/{DOCUMENTO_CONTENEDOR}", _contenido_readme_contenedor(), raiz_res
        )

    directorio_trabajo = contenedor / identificador
    directorio_trabajo.mkdir(exist_ok=False)  # no sobrescribe un trabajo existente

    documentos = {
        DOCUMENTO_ESTADO: _contenido_estado(
            identificador, proyecto, titulo, objetivo, criterios, restricciones, declarado
        ),
        DOCUMENTO_DECISIONES: _contenido_decisiones(identificador, proyecto),
        DOCUMENTO_ASERCIONES: _contenido_aserciones(identificador, proyecto),
        DOCUMENTO_CONTENEDOR: _contenido_readme_trabajo(identificador, titulo, proyecto, declarado),
    }
    for nombre, contenido in documentos.items():
        escribir_documento_trabajo(
            f"{WORK_CONTAINER}/{identificador}/{nombre}", contenido, raiz_res
        )
    return directorio_trabajo


# ---------------------------------------------------------------------------
# B14-C — Lector de `state.md`
# ---------------------------------------------------------------------------
#: ``Kind`` exacto que declara un documento de verdad vigente del canal.
KIND_ESTADO = "work-state"

#: Campos del cuerpo que se representan como lista de líneas literales.
#: `veredictos obsoletos` entra aquí en B15-L: el modelo ya lo declaraba como
#: tupla (`WorkState.veredictos_obsoletos`) y es un historial **append-only**,
#: que necesita items en líneas propias. Sigue siendo retrocompatible: un
#: documento antiguo con el valor en la misma línea se lee igual (un único item).
_CAMPOS_LISTA = frozenset(
    {
        "criterios de aceptación",
        "alcance incluido",
        "alcance excluido",
        "restricciones",
        "dependencias de entorno declaradas",
        "trabajo completado",
        "trabajo pendiente",
        "bloqueos",
        "veredictos obsoletos",
    }
)

#: Secciones H3 cuyo cuerpo es un bloque de texto libre (no clave-valor).
_SECCIONES_BLOQUE = ("objetivo",)

#: Claves del cuerpo ya contempladas por el contrato canónico.
_CAMPOS_CONOCIDOS = frozenset(
    _CAMPOS_LISTA
    | set(_SECCIONES_BLOQUE)
    | {
        "id",
        "título",
        "declarado",
        "proyecto",
        "ciclo de vida",
        "siguiente paso",
        "comando de verificación",
        "último veredicto de verificación",
        "veredictos obsoletos",
        "base",
        "actual",
        "rama",
        "pr/issue",
        "estado",
        "motivo",
    }
)

#: Línea ``- Clave: valor`` (o ``Clave: valor`` en cabecera). La clase de
#: caracteres de la clave excluye ``:``, así que el emparejamiento es lineal
#: (nada de retroceso catastrófico sobre líneas largas sin clave).
_RE_CLAVE = re.compile(r"^[\t ]*(?:[-*][\t ]+)?([^\n:#][^\n:#]{0,47}?)[\t ]*:[\t ]?(.*)$")

_RE_ENCABEZADO = re.compile(r"^(#{1,6})[\t ]+(.*)$")

#: Línea de item de lista Markdown (``- x`` / ``* x``).
_RE_ITEM = re.compile(r"^[\t ]*[-*][\t ]+(\S.*)$")


@dataclass(frozen=True)
class WorkState:
    """Representación **documental** de un `state.md` (B14-B).

    Estructura de datos pura: sin propiedades derivadas, sin métodos de
    negocio, sin interpretación. El veredicto, las referencias Git, el ciclo de
    vida y el cierre son **texto declarado por el documento**; interpretarlos
    pertenece a otros componentes.

    Un campo ausente en el documento se representa como ``None`` (o tupla vacía
    en los campos de lista). Los placeholders de ``crear_trabajo()`` se
    **preservan literalmente**: convertirlos en ausencia es una decisión
    semántica que B14-B difiere expresamente.
    """

    # Cabecera contractual
    work_id: str
    kind: str
    contrato: str
    proyecto: str | None

    # Identidad
    titulo: str | None
    declarado: str | None

    # Mandato
    objetivo: str | None
    criterios: tuple[str, ...]
    alcance_incluido: tuple[str, ...]
    alcance_excluido: tuple[str, ...]
    restricciones: tuple[str, ...]
    dependencias_entorno: tuple[str, ...]

    # Estado operativo (texto declarativo, sin interpretación)
    ciclo_de_vida: str | None
    trabajo_completado: tuple[str, ...]
    trabajo_pendiente: tuple[str, ...]
    bloqueos: tuple[str, ...]
    siguiente_paso: str | None
    comando_verificacion: str | None
    ultimo_veredicto: str | None
    veredictos_obsoletos: tuple[str, ...]

    # Referencias Git (texto; el Reader no ejecuta Git)
    git_base: str | None
    git_actual: str | None
    git_rama: str | None
    git_pr_issue: str | None

    # Cierre (texto; sin `cerrado: bool` derivado)
    cierre_estado: str | None
    cierre_motivo: str | None

    #: Claves del cuerpo no contempladas por el contrato canónico. No es una
    #: garantía de cobertura: solo recoge lo encontrado en esta lectura.
    campos_extra: dict[str, str] = field(default_factory=dict)


def _leer_texto_utf8(ruta: Path) -> str:
    """Lee ``ruta`` como UTF-8. Falla cerrado si no es un fichero legible."""
    if ruta.is_symlink() or not ruta.is_file():
        raise ContratoEstadoInvalidoError(f"no existe un documento de estado legible: {ruta.name}")
    try:
        return ruta.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ContratoEstadoInvalidoError(
            f"el documento de estado no es texto UTF-8 legible: {ruta.name}"
        ) from exc


def _normalizar_clave(clave: str) -> str:
    return " ".join(str(clave).strip().lower().split())


def _parsear_cabecera(lineas: list[str]) -> dict[str, str]:
    """Extrae la cabecera de etiquetas fijas que precede a la primera sección.

    Solo se consideran las líneas anteriores al primer encabezado Markdown: el
    cuerpo no puede competir con la identidad contractual. Una clave de cabecera
    duplicada con valores distintos produce error contractual (identidad
    ambigua); con el mismo valor se normaliza.
    """
    cabecera: dict[str, str] = {}
    for linea in lineas:
        encabezado = _RE_ENCABEZADO.match(linea)
        if encabezado:
            # El título H1 («# Trabajo: …») precede a la cabecera: se ignora.
            # Una sección H2+ marca el fin de la cabecera contractual.
            if len(encabezado.group(1)) >= 2:
                break
            continue
        if not linea.strip():
            continue
        if linea.lstrip().startswith(("-", "*", ">", "|", "`")):
            continue
        coincidencia = _RE_CLAVE.match(linea)
        if not coincidencia:
            continue
        clave = _normalizar_clave(coincidencia.group(1))
        valor = coincidencia.group(2).strip()
        if clave in cabecera and cabecera[clave] != valor:
            raise ContratoEstadoInvalidoError(
                f"cabecera ambigua: '{coincidencia.group(1).strip()}' aparece con valores distintos"
            )
        cabecera[clave] = valor
    return cabecera


def _extraer_bloque(lineas: list[str], inicio: int) -> tuple[str | None, int]:
    """Devuelve el texto libre del bloque que empieza en ``inicio`` y su fin."""
    frase: list[str] = []
    indice = inicio
    while indice < len(lineas):
        linea = lineas[indice]
        if linea.strip() and _RE_ENCABEZADO.match(linea):
            break
        if linea.strip():
            frase.append(linea.strip())
        elif frase:
            break
        indice += 1
    return ("\n".join(frase).strip() or None), indice


def _extraer_lista(
    lineas: list[str], inicio: int, primero: str | None = None
) -> tuple[tuple[str, ...], int]:
    """Recoge los items de lista literales desde ``inicio`` y devuelve el fin.

    ``primero`` es el valor que ya venía en la línea de la clave (el generador
    escribe ``- Clave: - (nada registrado)``): también se conserva literal.
    """
    elementos: list[str] = []
    if primero is not None:
        limpio = primero.lstrip("-").strip()
        if limpio:
            elementos.append(limpio)
    indice = inicio
    # La plantilla canónica separa la sección H3 de sus items con una línea
    # en blanco: se saltan los blancos iniciales, no los finales.
    while indice < len(lineas) and not lineas[indice].strip():
        indice += 1
    while indice < len(lineas):
        linea = lineas[indice]
        if not linea.strip() or _RE_ENCABEZADO.match(linea):
            break
        item = _RE_ITEM.match(linea)
        if not item:
            break
        clave_item = _RE_CLAVE.match(linea)
        if clave_item and _normalizar_clave(clave_item.group(1)) in _CAMPOS_CONOCIDOS:
            # El item abre otra clave documental (`- Siguiente paso: …`).
            break
        elementos.append(item.group(1).strip())
        indice += 1
    return tuple(e for e in elementos if e), indice


def _parsear_cuerpo(
    lineas: list[str],
) -> tuple[dict[str, str | tuple[str, ...]], dict[str, str]]:
    """Extrae las claves documentales del cuerpo.

    Tolerante por diseño: ignora texto Markdown no contractual, claves
    desconocidas (que van a ``extra``) y líneas en blanco. Las claves
    duplicadas del cuerpo se resuelven con la **última** ocurrencia.
    """
    valores: dict[str, str | tuple[str, ...]] = {}
    seccion = ""
    indice = 0
    while indice < len(lineas):
        linea = lineas[indice]
        cabecera_md = _RE_ENCABEZADO.match(linea)
        if cabecera_md:
            seccion = _normalizar_clave(cabecera_md.group(2))
            indice += 1
            if seccion in _SECCIONES_BLOQUE:
                texto, indice = _extraer_bloque(lineas, indice)
                valores[seccion] = texto
            elif seccion in _CAMPOS_LISTA:
                # Sección H3 de lista: sus items son líneas `- ...`.
                elementos, indice = _extraer_lista(lineas, indice)
                valores[seccion] = elementos
            continue

        # Solo las líneas de lista (``- Clave: valor``) son claves del cuerpo:
        # el texto Markdown en prosa no es contrato y no se interpreta.
        if not _RE_ITEM.match(linea):
            indice += 1
            continue
        coincidencia = _RE_CLAVE.match(linea)
        if not coincidencia:
            indice += 1
            continue
        clave = _normalizar_clave(coincidencia.group(1))
        resto = coincidencia.group(2).strip()

        if clave in _CAMPOS_LISTA:
            # `indice + 1`: la línea de la clave ya se consumió; sus items
            # son las líneas siguientes.
            elementos, indice = _extraer_lista(lineas, indice + 1, resto)
            valores[clave] = elementos
            continue

        valores[clave] = resto or None
        indice += 1

    extra = {
        clave: valor
        for clave, valor in valores.items()
        if clave not in _CAMPOS_CONOCIDOS and isinstance(valor, str)
    }
    return valores, extra


def _texto(valores: dict, clave: str) -> str | None:
    bruto = valores.get(clave)
    return bruto if isinstance(bruto, str) and bruto else None


def _tupla(valores: dict, clave: str) -> tuple[str, ...]:
    bruto = valores.get(clave)
    if isinstance(bruto, tuple):
        return bruto
    if isinstance(bruto, str) and bruto:
        return (bruto,)
    return ()


def leer_estado(work_id: str, directorio: str | Path = ".") -> WorkState:
    """Lee ``.work/<work_id>/state.md`` y devuelve su lectura documental (B14-C).

    El Reader responde **solo** a «qué dice actualmente el `state.md` de este
    trabajo». No lee `decisions.md`, no resuelve decisiones, no ejecuta Git, no
    interpreta el veredicto, no calcula obsolescencia y no escribe nada.

    Rechaza (contrato documental mínimo): documento inexistente o ilegible,
    documento vacío, `Kind` distinto de ``work-state``, `Contract` distinto de
    ``VERSION_CONTRATO``, `Work` distinto del ``work_id`` solicitado y cabecera
    duplicada con valores conflictivos.

    Los campos opcionales ausentes no son un error: se devuelven como ``None``
    (o tupla vacía). La seguridad de la ruta la garantiza la frontera ya
    existente: ``_validar_id_trabajo`` + ``_normalizar_relativa``, la misma del
    escritor. El Reader construye únicamente ``.work/<work_id>/state.md``.
    """
    from utils import resolver_raiz  # diferido: mantiene el módulo libre de ciclos

    raiz_res = Path(resolver_raiz(str(directorio))).resolve()
    if not raiz_res.is_dir():
        raise RutaInseguraError(f"la raíz del proyecto no existe: {directorio}")

    identificador = _validar_id_trabajo(work_id)
    ruta_relativa = f"{WORK_CONTAINER}/{identificador}/{DOCUMENTO_ESTADO}"
    # Misma frontera anti-traversal del escritor: rechaza rutas absolutas, byte
    # nulo y cualquier componente `..`.
    partes = _normalizar_relativa(ruta_relativa)
    actual = raiz_res
    for parte in partes[:-1]:
        actual = actual / parte
        if actual.is_symlink():
            raise RutaInseguraError(f"componente symlink en la ruta de trabajo: {actual}")
    destino = raiz_res.joinpath(*partes)

    texto = _leer_texto_utf8(destino)
    lineas = texto.splitlines()
    if not any(linea.strip() for linea in lineas):
        raise ContratoEstadoInvalidoError(f"el documento de estado está vacío: {ruta_relativa!r}")

    cabecera = _parsear_cabecera(lineas)
    if not cabecera:
        raise ContratoEstadoInvalidoError(
            f"el documento de estado no tiene cabecera contractual: {ruta_relativa!r}"
        )

    kind = cabecera.get("kind", "")
    if kind != KIND_ESTADO:
        raise ContratoEstadoInvalidoError(
            f"Kind inválido: se esperaba {KIND_ESTADO!r} y se encontró {kind!r}"
        )
    contrato = cabecera.get("contract", "")
    if contrato != VERSION_CONTRATO:
        raise ContratoEstadoInvalidoError(
            f"Contract inválido: se esperaba {VERSION_CONTRATO!r} y se encontró {contrato!r}"
        )
    trabajo = cabecera.get("work", "")
    if trabajo != identificador:
        raise ContratoEstadoInvalidoError(
            f"Work inválido: el documento declara {trabajo!r} y se pidió {identificador!r}"
        )

    valores, extra = _parsear_cuerpo(lineas)
    return WorkState(
        work_id=identificador,
        kind=kind,
        contrato=contrato,
        proyecto=cabecera.get("project") or _texto(valores, "proyecto"),
        titulo=_texto(valores, "título"),
        declarado=_texto(valores, "declarado"),
        objetivo=_texto(valores, "objetivo"),
        criterios=_tupla(valores, "criterios de aceptación"),
        alcance_incluido=_tupla(valores, "alcance incluido"),
        alcance_excluido=_tupla(valores, "alcance excluido"),
        restricciones=_tupla(valores, "restricciones"),
        dependencias_entorno=_tupla(valores, "dependencias de entorno declaradas"),
        ciclo_de_vida=_texto(valores, "ciclo de vida"),
        trabajo_completado=_tupla(valores, "trabajo completado"),
        trabajo_pendiente=_tupla(valores, "trabajo pendiente"),
        bloqueos=_tupla(valores, "bloqueos"),
        siguiente_paso=_texto(valores, "siguiente paso"),
        comando_verificacion=_texto(valores, "comando de verificación"),
        ultimo_veredicto=_texto(valores, "último veredicto de verificación"),
        veredictos_obsoletos=_tupla(valores, "veredictos obsoletos"),
        git_base=_texto(valores, "base"),
        git_actual=_texto(valores, "actual"),
        git_rama=_texto(valores, "rama"),
        git_pr_issue=_texto(valores, "pr/issue"),
        cierre_estado=_texto(valores, "estado"),
        cierre_motivo=_texto(valores, "motivo"),
        campos_extra=dict(extra),
    )


# ---------------------------------------------------------------------------
# B14-E — Writer declarativo de `state.md`
# ---------------------------------------------------------------------------
#: Campo de `WorkState` → clave documental tal y como aparece en `state.md`.
#: El writer solo conoce estas correspondencias: una clave fuera de este mapa
#: es un error explícito, nunca un nombre arbitrario aceptado en silencio.
_CAMPOS_ESCRIBIBLES: dict[str, str] = {
    "titulo": "Título",
    "objetivo": "Objetivo",
    "criterios": "Criterios de aceptación",
    "alcance_incluido": "Alcance incluido",
    "alcance_excluido": "Alcance excluido",
    "restricciones": "Restricciones",
    "dependencias_entorno": "Dependencias de entorno declaradas",
    "ciclo_de_vida": "Ciclo de vida",
    "trabajo_completado": "Trabajo completado",
    "trabajo_pendiente": "Trabajo pendiente",
    "bloqueos": "Bloqueos",
    "siguiente_paso": "Siguiente paso",
    "comando_verificacion": "Comando de verificación",
    "ultimo_veredicto": "Último veredicto de verificación",
    # `veredictos_obsoletos` NO aparece aquí a propósito: es una clave
    # **protegida** (B14-G/H) y su única vía de escritura es la operación
    # semántica `registrar_veredicto_obsoleto()` (B15-L). La escritura
    # declarativa genérica no puede tocar el historial de obsolescencia.
    "git_base": "Base",
    "git_actual": "Actual",
    "git_rama": "Rama",
    "git_pr_issue": "PR/issue",
    "cierre_estado": "Estado",
    "cierre_motivo": "Motivo",
}

#: Campos de `WorkState` cuyo valor es una lista de líneas literales.
_CAMPOS_ESCRIBIBLES_LISTA = frozenset(
    {
        "criterios",
        "alcance_incluido",
        "alcance_excluido",
        "restricciones",
        "dependencias_entorno",
        "trabajo_completado",
        "trabajo_pendiente",
        "bloqueos",
        "veredictos_obsoletos",
    }
)

#: Claves documentales (normalizadas) que el canal declara como **sección H3**
#: con items en líneas propias (``### Criterios de aceptación`` → ``- x``).
_CLAVES_ESCRIBIBLES_SECCION = frozenset(
    {
        "criterios de aceptación",
        "alcance incluido",
        "alcance excluido",
        "restricciones",
        "dependencias de entorno declaradas",
    }
)

#: Claves documentales que el canal declara **en línea** en una sola línea
#: (``- Trabajo completado: …``, B9 §14). Su valor es texto único: por eso
#: una secuencia se serializa uniendo sus elementos con ``; ``, que es la
#: convención que el propio canal ya usa en B12.
_CLAVES_ESCRIBIBLES_EN_LINEA = (
    frozenset(_normalizar_clave(v) for v in _CAMPOS_ESCRIBIBLES.values()) & _CAMPOS_LISTA
) - _CLAVES_ESCRIBIBLES_SECCION

#: Todas las claves escribibles cuyo valor es una colección de líneas.
_CLAVES_ESCRIBIBLES_LISTA = _CLAVES_ESCRIBIBLES_SECCION | _CLAVES_ESCRIBIBLES_EN_LINEA


def _localizar_clave(lineas: list[str], clave_doc: str) -> tuple[int, str, str]:
    """Devuelve ``(índice, nombre_original, prefijo)`` de la clave documental.

    Se conservan el nombre escrito y el prefijo exactos de la línea original
    (``- ``, sangría): el writer no reformatea el documento, solo su valor.
    """
    objetivo = _normalizar_clave(clave_doc)
    for indice, linea in enumerate(lineas):
        coincidencia = _RE_CLAVE.match(linea)
        if coincidencia and _normalizar_clave(coincidencia.group(1)) == objetivo:
            nombre = coincidencia.group(1)
            return indice, nombre, linea[: linea.index(nombre)]
    raise ContratoEstadoInvalidoError(f"el documento no declara la clave '{clave_doc}'")


#: Título H1 del documento (`# Trabajo: <titulo>`). Es la **proyección de
#: presentación** de `- Título:`, que es el campo estructurado canónico: el
#: Reader lo ignora explícitamente (B14-G-R §A). Se sincroniza en la misma
#: escritura atómica para no crear una segunda verdad divergente.
_RE_H1_TRABAJO = re.compile(r"^(\s*#\s*Trabajo:\s*)(.*)$")


def _sincronizar_h1_titulo(lineas: list[str], valor: str) -> list[str]:
    """Proyecta el nuevo título canónico sobre el H1 de presentación."""
    for indice, linea in enumerate(lineas):
        coincidencia = _RE_H1_TRABAJO.match(linea)
        if coincidencia:
            lineas[indice] = f"{coincidencia.group(1)}{valor}"
            break
    return lineas


def _lineas_actualizadas(lineas: list[str], clave_doc: str, valor) -> list[str]:
    """Devuelve ``lineas`` con la clave documental sustituida por ``valor``.

    **Edición quirúrgica**: solo se tocan las líneas de la clave pedida y sus
    continuaciones de lista. Todo lo demás —secciones no gestionadas, claves
    desconocidas, prosa Markdown y placeholders de otros campos— sobrevive
    literal, porque el documento nunca se regenera desde `WorkState`.

    Se respetan los dos formatos que el canal genera de verdad para un campo de
    lista: la **clave en línea** (``- Bloqueos: - (ninguno)``, como en
    `## Estado operativo`, donde los items van en la misma línea separados por
    ``;``) y la **sección H3** (```### Criterios de aceptación```, donde cada
    item es su propia línea ``- x``).
    """
    objetivo = _normalizar_clave(clave_doc)

    # ¿Es una sección H3 (contenido en líneas propias) o una clave en línea?
    indice_seccion = None
    for indice, linea in enumerate(lineas):
        encabezado = _RE_ENCABEZADO.match(linea)
        if encabezado and _normalizar_clave(encabezado.group(2)) == objetivo:
            indice_seccion = indice
            break

    if objetivo not in _CLAVES_ESCRIBIBLES_LISTA:
        inicio, nombre, prefijo = _localizar_clave(lineas, clave_doc)
        texto = "" if valor is None else str(valor)
        # Campo de texto: se cambia solo el valor de su línea.
        lineas[inicio] = f"{prefijo}{nombre}: {texto}"
        if objetivo == "título":
            # B14-G-R §A: el H1 es la proyección de presentación del campo
            # canónico `- Título:`. Se actualiza aquí, dentro de la misma
            # transformación, para que una sola escritura atómica los deje
            # sincronizados (nunca dos escrituras independientes).
            lineas = _sincronizar_h1_titulo(lineas, texto)
        return lineas

    if valor is None:
        elementos: list[str] = []
    elif isinstance(valor, str):
        elementos = [valor.strip()] if valor.strip() else []
    else:
        elementos = [str(v).strip() for v in valor if str(v).strip()]

    if indice_seccion is not None:
        # Sección H3: se reescribe su bloque de items, conservando el heading.
        inicio_bloque = indice_seccion + 1
        while inicio_bloque < len(lineas) and not lineas[inicio_bloque].strip():
            inicio_bloque += 1  # separadores tras el heading
        fin = inicio_bloque
        while fin < len(lineas):
            # Bloque de items hasta la línea en blanco o el siguiente heading.
            if not lineas[fin].strip() or _RE_ENCABEZADO.match(lineas[fin]):
                break
            if not _RE_ITEM.match(lineas[fin]):
                break
            fin += 1
        return lineas[:inicio_bloque] + [f"- {e}" for e in elementos] + lineas[fin:]

    # Clave en línea: los items se declaran en la misma línea, separados por
    # `; ` — el formato que el propio canal usa para `Trabajo completado`.
    inicio, nombre, prefijo = _localizar_clave(lineas, clave_doc)
    lineas[inicio] = f"{prefijo}{nombre}: {'; '.join(elementos)}"

    # Se consumen las continuaciones `- item` preexistentes: quedan sustituidas
    # por la línea única anterior.
    fin = inicio + 1
    while fin < len(lineas):
        siguiente = lineas[fin]
        if not siguiente.strip() or _RE_ENCABEZADO.match(siguiente):
            break
        if _RE_CLAVE.match(siguiente) or not _RE_ITEM.match(siguiente):
            break
        fin += 1
    return lineas[: inicio + 1] + lineas[fin:]


def _validar_tipos(cambios: Mapping[str, object]) -> None:
    """Valida que cada valor corresponda al tipo documental de su campo.

    Los campos de lista admiten una secuencia de cadenas y, por comodidad,
    también una cadena suelta (que se trata como un único elemento). El writer
    no infiere tipos ni convierte valores: un tipo incorrecto es un error
    explícito, no una coerción silenciosa.
    """
    for campo, valor in cambios.items():
        if campo in _CAMPOS_ESCRIBIBLES_LISTA:
            if valor is None:
                raise ValueError(f"'{campo}': un campo de lista no admite None")
            if isinstance(valor, str):
                continue  # un único elemento
            if not isinstance(valor, (list, tuple)):
                raise TypeError(f"'{campo}' es un campo de lista: espera una secuencia de cadenas")
            if any(not isinstance(item, str) for item in valor):
                raise TypeError(f"'{campo}' solo admite cadenas en sus elementos")
            if not [i for i in valor if i.strip()]:
                raise ValueError(
                    f"'{campo}': una lista vacía no es un estado declarativo del "
                    "canal; declara al menos un elemento"
                )
        elif valor is not None and not isinstance(valor, str):
            raise TypeError(f"'{campo}' es un campo de texto: espera una cadena o None")


def actualizar_estado(
    work_id: str,
    cambios: Mapping[str, object],
    directorio: str | Path = ".",
) -> WorkState:
    """Actualiza campos declarativos de ``state.md`` y devuelve el estado persistido.

    Complemento de :func:`leer_estado` (B14-C). Sustituye el patrón
    ``read_text().replace().write_text()`` por una operación declarativa sobre
    campos reales de :class:`WorkState`.

    * ``cambios`` usa **nombres de campo de `WorkState`** (``"siguiente_paso"``),
      no las claves del Markdown. Un nombre no soportado lanza ``ValueError``;
      el writer nunca acepta claves arbitrarias en silencio.
    * Los campos de lista aceptan una secuencia de cadenas; los de texto, una
      cadena o ``None``.
    * ``ultimo_veredicto`` se escribe **literalmente**: el writer no lo divide,
      no valida su semántica y no calcula obsolescencia (B14-A-R).
    * Las referencias Git son texto declarativo: el writer no ejecuta Git.
    * El cierre sigue siendo texto en dos campos, sin derivar ``cerrado``.
    * No crea trabajos: si ``state.md`` no existe, falla; la creación sigue
      perteneciendo a :func:`crear_trabajo`.

    Persistencia atómica vía :func:`escribir_documento_trabajo` (B11-B). Tras
    escribir, el estado devuelto se obtiene **leyendo el documento realmente
    persistido** con :func:`leer_estado`: el parsing no se duplica.
    """
    from utils import resolver_raiz  # diferido: mantiene el módulo libre de ciclos

    if not isinstance(cambios, Mapping):
        raise TypeError("'cambios' debe ser un mapping de campo → valor")
    if not cambios:
        raise ValueError("'cambios' está vacío: no hay nada que actualizar")
    desconocidos = sorted(set(cambios) - set(_CAMPOS_ESCRIBIBLES))
    if desconocidos:
        raise ValueError(
            f"campos no escribibles: {desconocidos}. Admitidos: {sorted(_CAMPOS_ESCRIBIBLES)}"
        )
    _validar_tipos(cambios)

    raiz_res = Path(resolver_raiz(str(directorio))).resolve()
    if not raiz_res.is_dir():
        raise RutaInseguraError(f"la raíz del proyecto no existe: {directorio}")

    identificador = _validar_id_trabajo(work_id)
    # Misma frontera anti-traversal que el Reader y el escritor (B11).
    partes = _normalizar_relativa(f"{WORK_CONTAINER}/{identificador}/{DOCUMENTO_ESTADO}")
    destino = raiz_res.joinpath(*partes)
    if not destino.is_file():
        # No se crea el trabajo ni el documento: la creación tiene su propia API.
        raise ContratoEstadoInvalidoError(
            f"no existe el documento de estado del trabajo '{identificador}'"
        )

    texto = _leer_texto_utf8(destino)
    lineas = texto.splitlines()
    if not any(linea.strip() for linea in lineas):
        raise ContratoEstadoInvalidoError(
            f"el documento de estado está vacío: {DOCUMENTO_ESTADO!r}"
        )

    # Solo se escribe sobre un documento contractualmente válido (mismo umbral
    # que el Reader): el writer no «repara» cabeceras que no se pueden leer.
    cabecera = _parsear_cabecera(lineas)
    if (
        not cabecera
        or cabecera.get("kind") != KIND_ESTADO
        or (cabecera.get("contract") != VERSION_CONTRATO)
        or cabecera.get("work") != identificador
    ):
        raise ContratoEstadoInvalidoError(
            "el documento de estado no cumple el contrato b9-1: no se escribe"
        )

    for campo, valor in cambios.items():
        lineas = _lineas_actualizadas(lineas, _CAMPOS_ESCRIBIBLES[campo], valor)

    escribir_documento_trabajo(
        f"{WORK_CONTAINER}/{identificador}/{DOCUMENTO_ESTADO}",
        "\n".join(lineas) + "\n",
        raiz_res,
    )
    # El retorno refleja el documento **persistido**, no la intención de entrada.
    return leer_estado(identificador, raiz_res)


# ---------------------------------------------------------------------------
# B15-K — Persistencia de la última verificación en `state.md`
# ---------------------------------------------------------------------------
#: Separador de los campos de la línea de verificación. Es el mismo `; ` que el
#: canal ya usa para las listas en línea (B14-E), reformulado aquí como
#: separador de pares clave/valor: `:` ya es el separador clave/valor del
#: propio documento.
_SEPARADOR_CAMPOS = "; "

#: Versión del formato de la línea de verificación. Permite a
#: `leer_ultima_verificacion()` distinguir una línea escrita por B15-K de un
#: veredicto de texto libre (el placeholder «(no hay verificación registrada)»
#: y los veredictos literales que escribe el agente desde B14).
VERIFICACION_FORMATO = "1"

#: Marcador explícito de «el Verdict existe pero su validez no pudo
#: determinarse» (B15-J §13: Git CURRENT inobservable). **No** es un tercer
#: estado del evaluador: `VALID`/`OBSOLETE` siguen siendo los únicos estados de
#: `ValidityResult`. Es la ausencia de estado, escrita para que el documento
#: nunca sea ambiguo ni se lea como un `VALID` implícito.
VALIDIDAD_NO_EVALUADA = "no evaluada"

#: Claves del formato de la verificación, en el orden en que se escriben.
#: `instante` y `detectado` son **opcionales** y aditivas: un lector que no las
#: conozca las ignora (el parser solo recoge claves conocidas), de modo que la
#: gramática v1 sigue siendo retrocompatible.
_CAMPOS_VERIFICACION = (
    "resultado",
    "validity",
    "motivo",
    "tree_sha",
    "commit",
    "working_tree_clean",
    "scope",
    "instante",
    "detectado",
)


@dataclass(frozen=True)
class UltimaVerificacion:
    """Última verificación conocida del trabajo, tal y como queda persistida.

    Estructura **derivada** de la línea `Último veredicto de verificación` de
    `state.md`: no se añade ningún campo a :class:`WorkState` porque el
    documento ya tiene una clave contractual para ello, cuya semántica es
    «texto declarado por el documento» (B14-B).

    Conserva **separadas** las dos dimensiones que B15-J demostró ortogonales:

    * ``resultado`` — cómo terminó la verificación (``"pasa"``/``"falla"``);
    * ``validity`` — su vigencia frente al árbol actual (``VALID``/``OBSOLETE``),
      o :data:`VALIDIDAD_NO_EVALUADA` si no pudo determinarse.

    ``resultado="falla"`` con ``validity=VALID`` es un estado legítimo y no
    ambiguo: una verificación puede fallar y seguir describiendo el árbol.
    """

    resultado: str
    validity: str
    motivo: str | None
    tree_sha: str
    commit: str | None
    working_tree_clean: bool | None
    scope: str | None
    comando: str | None
    formato: str
    #: `instante` del Verdict, si se persistió (B15-H: metadata opcional).
    instante: str | None = None
    #: Instante en que se **detectó** la obsolescencia (B15-L). No es el
    #: `instante` del Verdict: son dos hechos distintos y no se mezclan.
    detectado: str | None = None

    @property
    def es_valida(self) -> bool:
        """Atajo de lectura. No introduce semántica nueva."""
        return self.validity == "VALID"

    @property
    def es_obsoleta(self) -> bool:
        """Atajo de lectura. No introduce semántica nueva."""
        return self.validity == "OBSOLETE"


def _escapar(valor: str) -> str:
    """Escapa el separador de campos para que un valor nunca lo introduzca."""
    return str(valor).replace("\\", "\\\\").replace(";", "\\;")


def _unescapar(valor: str) -> str:
    """Invierte :func:`_escapar`."""
    return re.sub(r"\\([\\;])", r"\1", valor)


def formatear_verificacion(
    resultado: str,
    tree_sha: str,
    *,
    validity: str | None = None,
    motivo: str | None = None,
    commit: str | None = None,
    working_tree_clean: bool | None = None,
    scope: str | None = None,
    instante: str | None = None,
    detectado: str | None = None,
) -> str:
    """Serializa la última verificación como una línea de `state.md`.

    Formato (pares ``clave=valor`` separados por ``; ``, con escapes de ``;``)::

        [verificacion:v1] resultado=pasa; validity=VALID; tree_sha=<sha>

    Es texto plano del canal: sin JSON, sin YAML, sin pickle y sin archivo
    paralelo. La información es **suficientemente estructurada** para que
    :func:`leer_ultima_verificacion` la recupere sin heurísticas.
    """
    if not resultado:
        raise ValueError("'resultado' es obligatorio para registrar una verificación")
    if not tree_sha:
        raise ValueError("'tree_sha' es obligatorio: es la identidad del verificado")

    partes = [
        f"resultado={_escapar(resultado)}",
        f"validity={_escapar(validity) if validity else VALIDIDAD_NO_EVALUADA}",
    ]
    if motivo:
        partes.append(f"motivo={_escapar(motivo)}")
    partes.append(f"tree_sha={_escapar(tree_sha)}")
    if commit:
        partes.append(f"commit={_escapar(commit)}")
    if working_tree_clean is not None:
        partes.append(f"working_tree_clean={'true' if working_tree_clean else 'false'}")
    if scope:
        partes.append(f"scope={_escapar(scope)}")
    if instante:
        partes.append(f"instante={_escapar(instante)}")
    if detectado:
        partes.append(f"detectado={_escapar(detectado)}")
    cuerpo = _SEPARADOR_CAMPOS.join(partes)
    return f"[verificacion:v{VERIFICACION_FORMATO}] {cuerpo}"


def leer_ultima_verificacion(estado: WorkState) -> UltimaVerificacion | None:
    """Recupera la última verificación persistida desde un :class:`WorkState`.

    Devuelve ``None`` si la línea no está en formato B15-K: los `state.md`
    antiguos y los veredictos de texto libre del agente siguen siendo legibles
    (B15-K §18). No es un error: es la ausencia de información estructurada.

    No interpreta `VALID`/`OBSOLETE` ni decide obsolescencia: solo recupera lo
    que el documento ya dice (B14-C).
    """
    if not isinstance(estado, WorkState):
        raise TypeError("estado debe ser un WorkState")

    crudo = estado.ultimo_veredicto
    if not crudo or not crudo.strip().startswith("[verificacion:v"):
        return None

    texto = crudo.strip()
    fin_cabecera = texto.find("]")
    if fin_cabecera < 0:
        raise ContratoEstadoInvalidoError(
            "la verificación persistida declara '[verificacion:v' pero no cierra la marca"
        )
    formato = texto[len("[verificacion:v") : fin_cabecera].strip()
    cuerpo = texto[fin_cabecera + 1 :]

    valores: dict[str, str] = {}
    for trozo in cuerpo.split(_SEPARADOR_CAMPOS.strip()):
        trozo = trozo.strip()
        if not trozo or "=" not in trozo:
            continue
        clave, _, valor = trozo.partition("=")
        clave = clave.strip()
        if clave in _CAMPOS_VERIFICACION:
            valores[clave] = _unescapar(valor.strip())

    resultado = valores.get("resultado")
    tree_sha = valores.get("tree_sha")
    if not resultado or not tree_sha:
        # Se declara una verificación pero falta su identidad: es corrupción,
        # no ausencia. Inventar un Verdict sería peor que fallar.
        raise ContratoEstadoInvalidoError(
            "la verificación persistida no declara 'resultado' y/o 'tree_sha': "
            "no se puede reconstruir el veredicto"
        )

    sucio = valores.get("working_tree_clean")
    return UltimaVerificacion(
        resultado=resultado,
        validity=valores.get("validity") or VALIDIDAD_NO_EVALUADA,
        motivo=valores.get("motivo") or None,
        tree_sha=tree_sha,
        commit=valores.get("commit") or None,
        working_tree_clean=None if sucio is None else sucio == "true",
        scope=valores.get("scope") or None,
        comando=estado.comando_verificacion,
        formato=formato,
        instante=valores.get("instante") or None,
        detectado=valores.get("detectado") or None,
    )


def registrar_verificacion(
    work_id: str,
    resultado: str,
    tree_sha: str,
    *,
    comando: str | None = None,
    validity: str | None = None,
    motivo: str | None = None,
    commit: str | None = None,
    working_tree_clean: bool | None = None,
    scope: str | None = None,
    directorio: str | Path = ".",
) -> WorkState:
    """Persiste la **última** verificación en `state.md` y devuelve el estado.

    Es la escritura de B15-K. Reutiliza :func:`actualizar_estado` (y por tanto
    :func:`escribir_documento_trabajo`), de modo que la atomicidad, la
    validación de cabeceras y la edición quirúrgica son las ya existentes: no
    se introduce un segundo sistema de escritura de `state.md`.

    Solo escribe las claves permitidas al agente en B14-G/H:

    * ``comando_verificacion`` (texto) — el comando realmente ejecutado;
    * ``ultimo_veredicto`` (texto) — la línea de verificación.

    **Nunca** escribe ``veredictos_obsoletos`` (protegido) ni ninguna clave del
    mandato. La operación es de **reemplazo**, no de historial: `state.md`
    representa estado actual, así que una verificación nueva sustituye a la
    anterior (B15-K §5, §19).
    """
    if not work_id or not str(work_id).strip():
        raise ValueError("'work_id' es obligatorio: la verificación pertenece a un trabajo")
    if comando is not None and not str(comando).strip():
        raise ValueError("'comando' no admite una cadena vacía")

    linea = formatear_verificacion(
        resultado,
        tree_sha,
        validity=validity,
        motivo=motivo,
        commit=commit,
        working_tree_clean=working_tree_clean,
        scope=scope,
    )

    cambios: dict[str, object] = {"ultimo_veredicto": linea}
    if comando is not None:
        cambios["comando_verificacion"] = str(comando)
    return actualizar_estado(work_id, cambios, directorio)


# ---------------------------------------------------------------------------
# B15-L — Historial y transición explícita de obsolescencia
# ---------------------------------------------------------------------------
#: Versión de la gramática de una entrada del historial de obsolescencia.
HISTORIAL_FORMATO = "1"

#: Claves del historial, en orden de escritura. Reutiliza las de la verificación
#: más `tree_actual` y `detectado`, para que la entrada histórica sea
#: autosuficiente: identifica el veredicto que dejó de ser válido **y** el
#: árbol frente al cual dejó de serlo.
_CAMPOS_HISTORIAL = (*_CAMPOS_VERIFICACION, "tree_actual")


@dataclass(frozen=True)
class VeredictoObsoleto:
    """Entrada del historial `veredictos_obsoletos`.

    Registro **histórico** e inmutable de una verificación que dejó de describir
    el árbol. No es un `Verdict` reconstruido: son los datos que el documento ya
    conservaba de esa verificación, más el instante en que se detectó la
    transición y el árbol contra el que se comparó.
    """

    resultado: str
    tree_sha: str
    tree_actual: str
    motivo: str
    commit: str | None
    working_tree_clean: bool | None
    scope: str | None
    comando: str | None
    instante: str | None
    detectado: str | None
    formato: str

    @property
    def clave(self) -> str:
        """Identidad de la entrada, para la idempotencia del historial.

        Es el `tree_sha` del veredicto que quedó obsoleto: dos detecciones del
        **mismo** veredicto producen la misma clave, aunque se detecten en
        instantes distintos.
        """
        return self.tree_sha


def formatear_veredicto_obsoleto(
    resultado: str,
    tree_sha: str,
    tree_actual: str,
    motivo: str,
    *,
    commit: str | None = None,
    working_tree_clean: bool | None = None,
    scope: str | None = None,
    comando: str | None = None,
    instante: str | None = None,
    detectado: str | None = None,
) -> str:
    """Serializa una entrada de `veredictos_obsoletos`.

    Formato (pares `clave=valor` separados por `; `, escapando `;`)::

        [obsoleto:v1] resultado=pasa; validity=OBSOLETE; motivo=tree_distinto; \
tree_sha=<verificado>; tree_actual=<actual>; detectado=<ts>

    Es la **misma gramática** que `[verificacion:v1]` (B15-K) con dos campos
    adicionales, y no el texto libre ``OBSOLETO desde {ts}: {motivo}`` que
    apareció en el experimento B13-B: aquel texto venía del experimento, no de
    un contrato (B15-A §17.3), así que B15-L define aquí la primera gramática
    ratificada y versionada del historial.

    `validity=OBSOLETE` se escribe de forma explícita: la entrada describe un
    veredicto que ya no es vigente, y decirlo evita que un lector la confunda
    con una verificación vigente.
    """
    if not resultado:
        raise ValueError("'resultado' es obligatorio en una entrada de obsolescencia")
    if not tree_sha:
        raise ValueError("'tree_sha' es obligatorio: es la identidad del veredicto obsoleto")
    if not tree_actual:
        raise ValueError("'tree_actual' es obligatorio: es contra qué dejó de valer")
    if not motivo:
        raise ValueError("'motivo' es obligatorio: se reutiliza el del evaluador")

    partes = [
        f"resultado={_escapar(resultado)}",
        "validity=OBSOLETE",
        f"motivo={_escapar(motivo)}",
        f"tree_sha={_escapar(tree_sha)}",
        f"tree_actual={_escapar(tree_actual)}",
    ]
    if commit:
        partes.append(f"commit={_escapar(commit)}")
    if working_tree_clean is not None:
        partes.append(f"working_tree_clean={'true' if working_tree_clean else 'false'}")
    if scope:
        partes.append(f"scope={_escapar(scope)}")
    if comando:
        partes.append(f"comando={_escapar(comando)}")
    if instante:
        partes.append(f"instante={_escapar(instante)}")
    if detectado:
        partes.append(f"detectado={_escapar(detectado)}")
    return f"[obsoleto:v{HISTORIAL_FORMATO}] " + _SEPARADOR_CAMPOS.join(partes)


def _parsear_entradas(texto: str, marca: str) -> dict[str, str]:
    """Parsea una línea `[<marca>:vN] k=v; …` en sus pares clave/valor.

    Devuelve un dict vacío si la línea no lleva la marca o no tiene cuerpo.
    Los valores se recuperan sin heurísticas: solo se aceptan las claves
    conocidas de la gramática.
    """
    limpio = texto.strip()
    if not limpio.startswith(marca):
        return {}
    fin = limpio.find("]")
    if fin < 0:
        return {}
    cuerpo = limpio[fin + 1 :]
    valores: dict[str, str] = {}
    for trozo in cuerpo.split(_SEPARADOR_CAMPOS.strip()):
        trozo = trozo.strip()
        if not trozo or "=" not in trozo:
            continue
        clave, _, valor = trozo.partition("=")
        clave = clave.strip()
        if clave in _CAMPOS_HISTORIAL:
            valores[clave] = _unescapar(valor.strip())
    return valores


def leer_veredictos_obsoletos(estado: WorkState) -> tuple[VeredictoObsoleto, ...]:
    """Lee el historial `veredictos_obsoletos` de un :class:`WorkState`.

    Tolera las entradas históricas en texto libre que el canal ya aceptaba
    (B14-C las leía como texto): una línea que no lleva la marca `[obsoleto:v1]`
    se conserva en el documento pero no se interpreta como entrada estructurada.
    """
    if not isinstance(estado, WorkState):
        raise TypeError("estado debe ser un WorkState")

    entradas: list[VeredictoObsoleto] = []
    for linea in estado.veredictos_obsoletos:
        valores = _parsear_entradas(linea, "[obsoleto:v")
        if not valores:
            continue
        tree_sha = valores.get("tree_sha")
        tree_actual = valores.get("tree_actual")
        motivo = valores.get("motivo")
        resultado = valores.get("resultado")
        if not (tree_sha and tree_actual and motivo and resultado):
            continue
        sucio = valores.get("working_tree_clean")
        entradas.append(
            VeredictoObsoleto(
                resultado=resultado,
                tree_sha=tree_sha,
                tree_actual=tree_actual,
                motivo=motivo,
                commit=valores.get("commit") or None,
                working_tree_clean=None if sucio is None else sucio == "true",
                scope=valores.get("scope") or None,
                comando=valores.get("comando") or None,
                instante=valores.get("instante") or None,
                detectado=valores.get("detectado") or None,
                formato=linea.strip()[len("[obsoleto:v") : linea.strip().find("]")],
            )
        )
    return tuple(entradas)


def _lineas_con_clave_obsoletos(lineas: list[str]) -> list[str]:
    """Asegura que existe la clave `Veredictos obsoletos` en el documento.

    `crear_trabajo()` no la genera (B14 la declaró como campo del modelo, no de
    la plantilla), y el writer genérico solo edita claves existentes. Aquí se
    inserta **una sola vez**, dentro de `## Estado operativo`, que es su lugar
    semántico, justo antes de `## Referencias Git`.

    Es la única operación del canal que crea una clave nueva, y lo hace por
    necesidad estructural del historial protegido: sin la clave no hay forma de
    registrar la transición sin reescribir el documento a mano.
    """
    objetivo = "veredictos obsoletos"
    for linea in lineas:
        coincidencia = _RE_CLAVE.match(linea)
        if coincidencia and _normalizar_clave(coincidencia.group(1)) == objetivo:
            return lineas

    for indice, linea in enumerate(lineas):
        encabezado = _RE_ENCABEZADO.match(linea)
        if encabezado and _normalizar_clave(encabezado.group(2)) == "estado operativo":
            fin = indice + 1
            while fin < len(lineas) and not _RE_ENCABEZADO.match(lineas[fin]):
                fin += 1
            # Se inserta al final de la sección, antes del siguiente encabezado.
            return [*lineas[:fin], "- Veredictos obsoletos: - (ninguno)", *lineas[fin:]]

    raise ContratoEstadoInvalidoError(
        "el documento no declara la sección '## Estado operativo': no se registra historial"
    )


def _lineas_historial_obsoletos(lineas: list[str], entradas: list[str]) -> list[str]:
    """Reescribe la clave `Veredictos obsoletos` con **una entrada por línea**.

    El writer genérico serializa las listas en línea uniendo con `; `, lo que es
    correcto para `Trabajo completado` pero **no** para el historial: una
    entrada ya contiene `; ` en su propia gramática, así que unirlas produciría
    una ambigüedad imposible de parsear después.

    Aquí la clave lleva la primera entrada en la misma línea y las siguientes
    como items `- …`, que es exactamente el formato que `_extraer_lista` ya
    sabe leer (y el que usa `### Criterios de aceptación`).
    """
    objetivo = "veredictos obsoletos"
    inicio = nombre = prefijo = None
    for indice, linea in enumerate(lineas):
        coincidencia = _RE_CLAVE.match(linea)
        if coincidencia and _normalizar_clave(coincidencia.group(1)) == objetivo:
            inicio = indice
            nombre = coincidencia.group(1)
            prefijo = linea[: linea.index(nombre)]
            break
    if inicio is None:
        raise ContratoEstadoInvalidoError("el documento no declara la clave 'Veredictos obsoletos'")

    # Se consumen las continuaciones `- item` preexistentes: quedan sustituidas.
    # Una entrada del historial contiene `:` (p. ej. `[obsoleto:v1]`), así que
    # solo corta la línea si abre una clave **conocida** del documento.
    fin = inicio + 1
    while fin < len(lineas):
        siguiente = lineas[fin]
        if not siguiente.strip() or _RE_ENCABEZADO.match(siguiente):
            break
        clave = _RE_CLAVE.match(siguiente)
        if clave and _normalizar_clave(clave.group(1)) in _CAMPOS_CONOCIDOS:
            break
        if not _RE_ITEM.match(siguiente):
            break
        fin += 1

    if not entradas:
        return [*lineas[:inicio], f"{prefijo}{nombre}: - (ninguno)", *lineas[fin:]]
    return [
        *lineas[:inicio],
        f"{prefijo}{nombre}: {entradas[0]}",
        *(f"- {e}" for e in entradas[1:]),
        *lineas[fin:],
    ]


def registrar_veredicto_obsoleto(
    work_id: str,
    entrada: str,
    directorio: str | Path = ".",
) -> WorkState:
    """Añade **una** entrada al historial `veredictos_obsoletos` (append-only).

    Es la **única vía productiva** para modificar el historial de obsolescencia.
    No es un alias de :func:`actualizar_estado`: esa primitiva genérica sigue
    rechazando `veredictos_obsoletos` (protegido en B14-G/H), y esta operación
    existe precisamente porque el historial necesita una semántica propia que la
    escritura declarativa no debe poder eludir.

    Garantías:

    * **append-only**: nunca reescribe ni reordena entradas existentes;
    * **idempotente**: si el historial ya contiene una entrada con la misma
      clave (el `tree_sha` del veredicto obsoleto), no añade otra;
    * **atómica**: escribe con :func:`escribir_documento_trabajo` (temporal +
      ``os.replace``), el mismo mecanismo que usa :func:`actualizar_estado`.

    Args:
        work_id: trabajo dueño del historial.
        entrada: línea ya serializada por :func:`formatear_veredicto_obsoleto`.
        directorio: raíz del proyecto.

    Returns:
        El :class:`WorkState` tal y como queda **persistido**.

    Raises:
        ValueError: si la entrada no lleva la marca `[obsoleto:vN]`: el historial
            no admite texto libre en esta vía.
    """
    if not work_id or not str(work_id).strip():
        raise ValueError("'work_id' es obligatorio: el historial pertenece a un trabajo")
    if not entrada or not entrada.strip().startswith("[obsoleto:v"):
        raise ValueError(
            "la entrada debe venir de formatear_veredicto_obsoleto() "
            "(falta la marca '[obsoleto:vN]')"
        )

    from utils import resolver_raiz  # diferido: mantiene el módulo libre de ciclos

    raiz_res = Path(resolver_raiz(str(directorio))).resolve()
    if not raiz_res.is_dir():
        raise RutaInseguraError(f"la raíz del proyecto no existe: {directorio}")

    identificador = _validar_id_trabajo(work_id)
    partes = _normalizar_relativa(f"{WORK_CONTAINER}/{identificador}/{DOCUMENTO_ESTADO}")
    destino = raiz_res.joinpath(*partes)
    if not destino.is_file():
        raise ContratoEstadoInvalidoError(
            f"no existe el documento de estado del trabajo '{identificador}'"
        )

    lineas = _leer_texto_utf8(destino).splitlines()
    if not any(linea.strip() for linea in lineas):
        raise ContratoEstadoInvalidoError(
            f"el documento de estado está vacío: {DOCUMENTO_ESTADO!r}"
        )

    cabecera = _parsear_cabecera(lineas)
    if (
        not cabecera
        or cabecera.get("kind") != KIND_ESTADO
        or (cabecera.get("contract") != VERSION_CONTRATO)
        or cabecera.get("work") != identificador
    ):
        raise ContratoEstadoInvalidoError(
            "el documento de estado no cumple el contrato b9-1: no se escribe"
        )

    lineas = _lineas_con_clave_obsoletos(lineas)
    estado_actual = _parsear_cuerpo(lineas)
    existentes = estado_actual[0]

    # Idempotencia: la identidad de la entrada es el `tree_sha` del veredicto
    # que quedó obsoleto, no el instante de detección.
    clave_nueva = _parsear_entradas(entrada, "[obsoleto:v").get("tree_sha")
    previas = existentes.get("veredictos obsoletos") or ()
    if isinstance(previas, str):
        previas = (previas,)
    for previa in previas:
        if _parsear_entradas(previa, "[obsoleto:v").get("tree_sha") == clave_nueva:
            # Ya registrado: no se duplica. El documento no se reescribe.
            return leer_estado(identificador, raiz_res)

    lineas = _lineas_historial_obsoletos(lineas, [*list(previas), entrada])
    escribir_documento_trabajo(
        f"{WORK_CONTAINER}/{identificador}/{DOCUMENTO_ESTADO}",
        "\n".join(lineas) + "\n",
        raiz_res,
    )
    return leer_estado(identificador, raiz_res)
