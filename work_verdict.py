#!/usr/bin/env python3
"""Work Verification — entradas estructuradas para la validez de verdicts.

B15-B materializa las **dos entradas** definidas en
`docs/B15-A-verification-verdict-validity-boundary.md`, y nada más:

* :class:`GitAnchor` — qué se verificó, según la evidencia de B13-B.
* :class:`Verdict` — el resultado estructurado de una verificación.
* :class:`CurrentGitState` — el estado factual del repositorio.
* :func:`leer_estado_git` — el adapter observacional que produce lo último.

Fronteras respetadas (B15-A §11-§13):

* Los tres modelos son **datos puros**: no ejecutan Git, no leen ficheros, no
  consultan `WorkState`, MCP ni el agente.
* Solo el adapter habla con Git, y solo **lee**.
* `state.md` **no** se parsea para fabricar un `Verdict` (§6 de B15-B).
* **No existe** `ValidityResult` ni evaluación `VALID`/`OBSOLETE`/`UNDETERMINED`:
  pertenecen a B15-C.
* `git_base` / `git_actual` / `git_rama` / `git_pr_issue` de `WorkState` **no**
  son anchors y no se importan aquí: este módulo no depende de `work_context`.

Este módulo no importa `snapcontext`: mantiene la independencia que permite
que el futuro evaluador sea testeable sin el monolito.

B15-H añade :func:`producir_verdict`, el **productor canónico** decidido en
`docs/B15-G-canonical-verification-producer-decision.md`: una función pura que
convierte un resultado de verificación ya ejecutado más el :class:`CurrentGitState`
que el llamador capturó **antes** de ejecutar, en un :class:`Verdict`. No ejecuta
Git, no evalúa validez y no persiste.
"""

from __future__ import annotations

from dataclasses import dataclass

from exceptions import EstadoGitIndisponibleError

__all__ = [
    "OBSOLETE",
    "RESULTADO_FALLA",
    "RESULTADO_PASA",
    "SCOPE_NO_DEFINIDO",
    "VALID",
    "CurrentGitState",
    "GitAnchor",
    "ValidityResult",
    "Verdict",
    "evaluar_validez_verdict",
    "leer_estado_git",
    "producir_verdict",
]

#: Comandos Git que este adapter puede ejecutar. Todos son de **solo lectura**
#: (B15-A §13). La lista es explícita: no existe forma de pasar otro comando.
_COMANDO_ES_REPO = ("git", "rev-parse", "--is-inside-work-tree")
_COMANDO_HEAD = ("git", "rev-parse", "HEAD")
_COMANDO_STATUS = ("git", "status", "--porcelain")
_COMANDO_ADD_TODO = ("git", "add", "-A")
_COMANDO_WRITE_TREE = ("git", "write-tree")

#: Tiempo máximo por comando Git. La lectura es local e instantánea; el límite
#: evita que un repositorio colgado bloquee al consumidor.
_TIMEOUT_GIT = 30.0


@dataclass(frozen=True)
class GitAnchor:
    """Identidad de lo que se verificó, según la evidencia de B13-B.

    B13-B demostró que un anchor se compone de **dos** hechos, no de uno:

    * ``commit`` — SHA completo de ``git rev-parse HEAD``;
    * ``working_tree_clean`` — si ``git status --porcelain`` estaba vacío.

    Un veredicto cuyo anchor es ``(X, True)`` dejó de corresponder cuando el
    working tree pasó a ``(X, False)`` **sin cambiar de commit**. Por eso ambos
    componentes son necesarios: el commit solo no habría detectado ese caso.

    **No** contiene rama, ni ``git_base``, ni ``git_actual``, ni ``git_pr_issue``:
    la evidencia de B13-B no demuestra semántica de validez para ninguno, y
    B15-A §5 los excluyó explícitamente del anchor.

    B15-E corrigió el contrato: la **identidad** es ``tree_sha`` (el contenido
    efectivo verificado), y ``commit`` / ``working_tree_clean`` son **metadatos**.
    Con el árbol sucio —el estado normal del bucle agéntico— el par histórico es
    constante mientras el contenido cambia, por lo que no puede ser identidad.
    """

    tree_sha: str
    commit: str
    working_tree_clean: bool

    def __post_init__(self) -> None:
        # `None` se rechaza explícitamente: `str(None)` es "None", que no está
        # vacío y dejaría pasar un ancla sin identificador real.
        for nombre, valor in (("tree_sha", self.tree_sha), ("commit", self.commit)):
            if valor is None:
                raise ValueError(f"GitAnchor.{nombre} no puede ser None")
            limpio = str(valor).strip()
            if not limpio:
                raise ValueError(f"GitAnchor.{nombre} no puede estar vacío")
            object.__setattr__(self, nombre, limpio)
        object.__setattr__(self, "working_tree_clean", bool(self.working_tree_clean))


@dataclass(frozen=True)
class CurrentGitState:
    """Estado factual del repositorio en el momento de la observación.

    Es la **contraparte factual** del :class:`GitAnchor`. El futuro evaluador
    comparará ambos; este bloque solo los observa.

    Deliberadamente mínimo. B15-E §K lo alineó: ``tree_sha`` es el estado de
    **contenido** observado; ``commit`` y ``working_tree_clean`` son metadatos.
    """

    tree_sha: str
    commit: str
    working_tree_clean: bool

    def __post_init__(self) -> None:
        # `None` se rechaza explícitamente (ver `GitAnchor.__post_init__`).
        for nombre, valor in (("tree_sha", self.tree_sha), ("commit", self.commit)):
            if valor is None:
                raise ValueError(f"CurrentGitState.{nombre} no puede ser None")
            limpio = str(valor).strip()
            if not limpio:
                raise ValueError(f"CurrentGitState.{nombre} no puede estar vacío")
            object.__setattr__(self, nombre, limpio)
        object.__setattr__(self, "working_tree_clean", bool(self.working_tree_clean))


@dataclass(frozen=True)
class Verdict:
    """Resultado estructurado de una verificación.

    Los campos proceden **de la evidencia de B13-B**: la línea de veredicto que
    el experimento registró contiene exactamente resultado, comando, scope,
    anchor, autor e instante. No se añade ningún campo sin respaldo.

    **No** contiene ``validity``, ``obsolete`` ni ``current_git_state``: son
    conceptos del futuro evaluador (B15-A §4, §11), no del resultado de una
    verificación. Quien quiera determinar la validez compara
    ``verdict.git_anchor`` con un :class:`CurrentGitState` — en B15-C.

    Limitación conocida (B15-B §6): **no existe todavía un productor** de este
    objeto. Se construye donde una verificación se ejecuta; no se reconstruye
    parseando ``state.md``, porque ``ultimo_veredicto`` es texto opaco por
    contrato (B14-B/B14-C) y parsearlo volvería frágil ese acuerdo.

    B15-H resolvió esa limitación con :func:`producir_verdict`.
    """

    resultado: str
    comando: str
    scope: str
    git_anchor: GitAnchor
    autor: str | None = None
    instante: str | None = None

    def __post_init__(self) -> None:
        for campo in ("resultado", "comando", "scope"):
            valor = str(getattr(self, campo)).strip()
            if not valor:
                raise ValueError(f"Verdict.{campo} no puede estar vacío")
            object.__setattr__(self, campo, valor)
        if not isinstance(self.git_anchor, GitAnchor):
            raise TypeError("Verdict.git_anchor debe ser un GitAnchor")


# ---------------------------------------------------------------------------
# Adapter observacional de Git
# ---------------------------------------------------------------------------
def _correr_git(
    argv: tuple[str, ...], directorio: str, indice: str | None = None
) -> tuple[int, str]:
    """Ejecuta un comando Git de solo lectura y devuelve ``(rc, stdout)``.

    Reutiliza :func:`sandbox_utils.ejecutar_comando_seguro`, que acepta **una
    lista de argv** y ejecuta con ``shell=False``. Se evita deliberadamente
    ``snapcontext._ejecutar_comando`` (usa ``shell=True`` y arrastra decisiones
    de sandbox) y se evita la dependencia del monolito.

    ``indice`` permite apuntar ``GIT_INDEX_FILE`` a un índice **temporal**, para
    que :func:`_tree_sha_working_tree` no toque el índice real del usuario.

    No distingue *por qué* falló Git: eso lo decide :func:`leer_estado_git` con
    comprobaciones explícitas, en vez de analizar el texto de `stderr` (que está
    **localizado** y no es estable).
    """
    import os

    from sandbox_utils import ejecutar_comando_seguro

    entorno = None
    if indice is not None:
        entorno = dict(os.environ, GIT_INDEX_FILE=str(indice))
    try:
        proceso = ejecutar_comando_seguro(
            list(argv), cwd=str(directorio), timeout=_TIMEOUT_GIT, env=entorno
        )
    except FileNotFoundError as exc:
        # `cwd` inexistente o ejecutable ausente: se distingue en el llamador.
        raise EstadoGitIndisponibleError(str(exc), "directorio_inexistente") from exc
    except OSError as exc:
        raise EstadoGitIndisponibleError(
            f"no se pudo ejecutar Git: {exc}", "git_no_disponible"
        ) from exc
    return proceso.returncode, (getattr(proceso, "stdout", "") or "").strip()


def _tree_sha_working_tree(directorio: str) -> str:
    """Devuelve el tree SHA del **contenido efectivo del working tree**.

    Es la identidad del verdict según B15-E: no el contenido de ``HEAD``, sino
    el que se verificaría en este instante, **incluidos cambios sin commitear**.

    Mecanismo (B15-E §5, verificado empíricamente):

    1. Se crea un índice Git **temporal** fuera del repositorio.
    2. ``GIT_INDEX_FILE=<temporal> git add -A`` puebla ese índice con el
       working tree, respetando ``.gitignore`` (Git es la autoridad de qué entra
       al árbol; no se reimplementa su lógica en Python).
    3. ``git write-tree`` calcula el tree SHA.

    Garantías:

    * el **índice real no se modifica** (apuntamos a otro archivo);
    * el **working tree no se modifica** (solo se leen sus archivos);
    * los temporales se limpian siempre, incluso ante error.
    """
    import tempfile
    from pathlib import Path

    # El índice temporal vive FUERA del repositorio: si viviera dentro, el
    # propio `git add -A` lo añadiría al árbol y contaminaría el tree SHA.
    with tempfile.TemporaryDirectory(prefix="snapcontext-tree-") as temporal:
        indice = Path(temporal) / "indice"
        codigo, _ = _correr_git(_COMANDO_ADD_TODO, directorio, indice=str(indice))
        if codigo != 0:
            raise EstadoGitIndisponibleError(
                "no se pudo calcular el árbol del working tree (`git add -A`)",
                "git_no_disponible",
            )
        codigo, tree = _correr_git(_COMANDO_WRITE_TREE, directorio, indice=str(indice))
    if codigo != 0 or not tree:
        raise EstadoGitIndisponibleError(
            "no se pudo calcular el tree SHA del working tree", "git_no_disponible"
        )
    return tree


def _exigir_repo(directorio: str) -> None:
    """Verifica que ``directorio`` está dentro de un repositorio Git.

    Usa el mismo criterio que ``snapcontext._es_repo_git()`` (B15-A §6 lo señaló
    como predicado ya disponible) pero contra ``_correr_git``, para no arrastrar
    el monolito. La comparación es con ``"true"`` y **no** con el texto de
    error, que depende del idioma del Git instalado.
    """
    from pathlib import Path

    if not Path(directorio).is_dir():
        raise EstadoGitIndisponibleError(
            f"el directorio no existe: {directorio}", "directorio_inexistente"
        )
    codigo, salida = _correr_git(_COMANDO_ES_REPO, directorio)
    if codigo != 0 or salida.strip().lower() != "true":
        raise EstadoGitIndisponibleError(
            f"'{directorio}' no es un repositorio Git", "sin_repositorio"
        )


def leer_estado_git(directorio: str = ".") -> CurrentGitState:
    """Observa el repositorio y devuelve su :class:`CurrentGitState`.

    Única función del módulo que habla con Git, y lo hace **solo leyendo**:

    * ``git rev-parse --is-inside-work-tree`` → ¿es repositorio?
    * ``git rev-parse HEAD``                → ``commit`` (metadato)
    * ``git status --porcelain``            → ``working_tree_clean`` (metadato)
    * índice temporal + ``git add -A`` + ``git write-tree``
                                           → ``tree_sha`` (**identidad**, B15-E)

    No ejecuta ningún comando mutante sobre el repositorio: ni ``commit``,
    ``reset``, ``restore``, ``checkout``, ``clean`` ni ``rm`` (B15-A §13). El
    ``git add -A`` opera **exclusivamente** sobre un índice temporal, por lo que
    ni el índice real ni el working tree se ven afectados.

    No evalúa verdicts ni conoce su existencia. No decide si un anchor sigue
    siendo válido: esa relación es de B15-C.

    Raises:
        EstadoGitIndisponibleError: con ``motivo`` en ``directorio_inexistente``,
            ``sin_repositorio``, ``head_no_disponible`` o ``git_no_disponible``.
    """
    _exigir_repo(directorio)
    codigo, commit = _correr_git(_COMANDO_HEAD, directorio)
    if codigo != 0 or not commit:
        # Incluye el repositorio sin commits: HEAD no resuelve.
        raise EstadoGitIndisponibleError(
            "`git rev-parse HEAD` no resolvió ningún commit", "head_no_disponible"
        )
    codigo_status, cambios = _correr_git(_COMANDO_STATUS, directorio)
    if codigo_status != 0:
        raise EstadoGitIndisponibleError("`git status --porcelain` falló", "git_no_disponible")
    return CurrentGitState(
        tree_sha=_tree_sha_working_tree(directorio),
        commit=commit,
        working_tree_clean=not cambios,
    )


# ---------------------------------------------------------------------------
# B15-C — Evaluador puro de validez
# ---------------------------------------------------------------------------
#: El estado Git actual sigue coincidiendo con el anchor del verdict.
VALID = "VALID"
#: El estado Git actual ya no coincide con el anchor del verdict.
OBSOLETE = "OBSOLETE"

# Motivos. No son un estado nuevo: explican **por qué** se dio el estado, y son
# un conjunto cerrado y determinista. Con identidad de contenido hay una sola
# causa posible, así que no hay precedencia que arbitrar (B15-F §14).
MOTIVO_COINCIDE = "tree_coincidente"
MOTIVO_TREE_DISTINTO = "tree_distinto"


@dataclass(frozen=True)
class ValidityResult:
    """Resultado **derivado** de comparar un verdict con el estado Git actual.

    Contiene solo lo necesario para comunicar el veredicto: ``status`` y
    ``motivo``. Deliberadamente **no** incluye el `CurrentGitState`, el
    `Verdict`, el `WorkState` ni ninguna salida de Git: el resultado es una
    observación breve, no un reporte.

    Es derivado y no muta nada: ni el verdict, ni `state.md`, ni el repositorio.
    """

    status: str
    motivo: str

    @property
    def es_valido(self) -> bool:
        """Atajo de lectura para el consumidor. No introduce semántica nueva."""
        return self.status == VALID


def evaluar_validez_verdict(verdict: Verdict, current_git_state: CurrentGitState) -> ValidityResult:
    """Determina si ``verdict`` sigue describiendo el estado Git actual.

    **Función pura**: recibe dos datos ya construidos, no lee ficheros, no
    ejecuta Git, no consulta `state.md`, `WorkState`, MCP ni el agente, y no
    muta sus entradas. Determinista: mismas entradas → mismo resultado.

    Semántica (B15-E, sobre B13-B):

    * ``VALID`` si ``anchor.tree_sha == current.tree_sha``: el contenido efectivo
      del árbol actual es **idéntico** al que produjo la verificación.
    * ``OBSOLETE`` en cuanto ese contenido difiere.

    ``commit`` y ``working_tree_clean`` son **metadatos**: no participan. Con el
    árbol sucio —el estado normal del bucle agéntico— el par histórico es
    constante mientras el contenido cambia, por lo que no puede ser identidad
    (B15-E §C). En particular, ``(Y, clean)`` con el mismo ``tree_sha`` que el
    anchor sigue siendo ``VALID``: el contenido no cambió, solo su historia.

    No se juzga la *calidad* del cambio ni se analiza el diff: ``OBSOLETE``
    significa únicamente que el estado actual dejó de corresponder con lo
    verificado (§10, §11, §14).

    ``UNDETERMINED`` **no forma parte de este evaluador``: sus tres condiciones
    de B15-A §7.1 son inalcanzables a través de estas entradas. Un anchor
    ausente se rechaza al construir ``GitAnchor``/``Verdict`` (B15-B), y un Git
    inobservable falla en :func:`leer_estado_git` con
    ``EstadoGitIndisponibleError``, que ocurre *antes* de esta llamada
    (B15-A §10). Ninguna de las dos puede llegar aquí.

    Args:
        verdict: el resultado estructurado de una verificación.
        current_git_state: el estado factual observado por :func:`leer_estado_git`.

    Returns:
        ValidityResult con ``status`` ``VALID`` u ``OBSOLETE``, y su ``motivo``.
    """
    if not isinstance(verdict, Verdict):
        raise TypeError("verdict debe ser un Verdict")
    if not isinstance(current_git_state, CurrentGitState):
        raise TypeError("current_git_state debe ser un CurrentGitState")

    anchor = verdict.git_anchor
    if anchor.tree_sha != current_git_state.tree_sha:
        return ValidityResult(status=OBSOLETE, motivo=MOTIVO_TREE_DISTINTO)
    return ValidityResult(status=VALID, motivo=MOTIVO_COINCIDE)


# ---------------------------------------------------------------------------
# Productor canónico de Verdict (B15-H)
# ---------------------------------------------------------------------------
#: Valores de ``Verdict.resultado``. Reutilizan la taxonomía **declarativa** que
#: B15-A §"Verdict" ya fijó (``"pasa" | "falla"``) y la convención que los tests
#: de B15-B ya usaban. No se introduce un estado nuevo ni se mezcla la validez
#: histórica (`VALID`/`OBSOLETE`), que es de otro evaluador (§13 del bloque).
RESULTADO_PASA = "pasa"
RESULTADO_FALLA = "falla"

#: `Verdict.scope` es un campo **obligatorio y no vacío** (B15-B). B15-G decidió
#: que `scope` es opcional en el productor porque hoy ningún flujo tiene noción
#: de scope. Se materializa con esta constante explícita en vez de inventar una
#: clasificación: el nombre dice la verdad y el consumidor puede compararla.
SCOPE_NO_DEFINIDO = "no definido"


def producir_verdict(
    exito: bool,
    comando: str,
    estado: CurrentGitState,
    *,
    scope: str | None = None,
    autor: str | None = None,
    instante: str | None = None,
) -> Verdict:
    """Construye el :class:`Verdict` canónico a partir de una verificación hecha.

    **Función pura** respecto de sus entradas (B15-G §K, §M): recibe datos ya
    existentes, construye el anchor y devuelve el verdict. No ejecuta nada, no
    consulta Git, no lee ni escribe ficheros, no toca `WorkState`, MCP ni el
    agente, y no persiste.

    La separación obligatoria es::

        leer_estado_git()  ->  CurrentGitState  ->  producir_verdict()  ->  Verdict
             (adapter)           (observación)        (productor)

    El `tree_sha` **ya viene capturado**: el llamador observa **antes** de
    verificar (B15-G §J), porque solo en ese instante se puede afirmar qué
    contenido se comprobó. Este productor no vuelve a calcular el árbol, no
    compara el estado actual y no decide validez; eso es
    :func:`evaluar_validez_verdict`.

    ``commit`` y ``working_tree_clean`` se copian al anchor como **metadatos
    observacionales** (B15-E/F). Un árbol sucio no convierte nada en
    ``OBSOLETE`` aquí: la validez es del evaluador.

    Args:
        exito: resultado de la verificación ya ejecutada. ``True`` → ``"pasa"``,
            ``False`` → ``"falla"``.
        comando: comando ejecutado. Se conserva tal cual; no se ejecuta ni se
            reinterpreta.
        estado: estado Git observado por el llamador **antes** de verificar.
        scope: ámbito de la verificación. ``None`` → :data:`SCOPE_NO_DEFINIDO`,
            porque ``Verdict.scope`` es obligatorio y no vacío. No se infiere
            del nombre del comando (B15-G §I dejó esa semántica abierta).
        autor: autoría, si el llamador la conoce. No se infiere de Git ni del
            sistema: si no se pasa, queda ``None``.
        instante: marca temporal, si el llamador la conoce. **No** se genera
            aquí: introducir una política temporal sería una decisión nueva.

    Returns:
        Verdict cuyo ``git_anchor.tree_sha`` es exactamente ``estado.tree_sha``.

    Raises:
        TypeError: si ``estado`` no es un :class:`CurrentGitState`.
        ValueError: si el estado no cumple el contrato de ``CurrentGitState``
            (``tree_sha`` vacío o ``None``), o si ``scope`` se pasa vacío.
    """
    if not isinstance(estado, CurrentGitState):
        raise TypeError("estado debe ser un CurrentGitState")

    if scope is None:
        scope_resuelto = SCOPE_NO_DEFINIDO
    else:
        # No se reescribe ni se clasifica: solo se delega la validación que
        # `Verdict.__post_init__` ya hace sobre el campo.
        scope_resuelto = scope

    anchor = GitAnchor(
        tree_sha=estado.tree_sha,
        commit=estado.commit,
        working_tree_clean=estado.working_tree_clean,
    )

    return Verdict(
        resultado=RESULTADO_PASA if exito else RESULTADO_FALLA,
        comando=comando,
        scope=scope_resuelto,
        git_anchor=anchor,
        autor=autor,
        instante=instante,
    )
