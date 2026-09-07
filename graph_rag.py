#!/usr/bin/env python3
"""Grafo de conocimiento (Graph RAG) de SnapContext — v5.5.0.

Combina **AST** (estructura: archivos, funciones, clases, imports, llamadas,
herencia) con la **búsqueda semántica** (embeddings) para que el agente
entienda la arquitectura del proyecto: cuando la búsqueda semántica devuelve
archivos relevantes, el grafo añade archivos *relacionados* (quienes los
importan y de quienes dependen).

Uso típico::

    import graph_rag as gr
    grafo = gr.construir_grafo(".")
    archivos = gr.expandir_contexto(["servicios/pagos.py"], grafo)

Solo Python en esta versión (tree-sitter para otros lenguajes llega en v5.6.0).
El grafo se persiste en ``~/.snapcontext/graph_cache.pkl`` y solo se reconstruye
cuando cambia algún ``.py`` (fingerprint por mtime + tamaño).

Se activa con ``--graph-rag`` o la variable de entorno
``SNAPCONTEXT_GRAPH_RAG=1``. Es completamente opcional: sin él, SnapContext
se comporta exactamente igual que en 5.4.0.
"""

from __future__ import annotations

import ast
import os
import pickle
from pathlib import Path

# Directorios que nunca se analizan (ruido, dependencias, artefactos).
_DIRECTORIOS_IGNORADOS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".dart_tool",
    "build",
    "dist",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "site-packages",
    ".snapcontext",
    "_backups",
    "out",
}

# Formato del cache: {"version": int, "fingerprint": {...}, "grafo": {...}}
_VERSION_CACHE = 1


def _ruta_cache_defecto() -> Path:
    """Ruta del cache por defecto: ~/.snapcontext/graph_cache.pkl."""
    return Path.home() / ".snapcontext" / "graph_cache.pkl"


def graph_rag_activo(flag: bool | None = None) -> bool:
    """True si Graph RAG debe activarse.

    Prioridad: flag explícito (``--graph-rag``) > ``SNAPCONTEXT_GRAPH_RAG=1``.
    """
    if flag is not None:
        return bool(flag)
    return os.environ.get("SNAPCONTEXT_GRAPH_RAG", "").strip() == "1"


# ---------------------------------------------------------------------------
# Fingerprint (para invalidar el cache solo cuando cambia el código)
# ---------------------------------------------------------------------------
def _fingerprint(directorio: str) -> dict[str, tuple[int, int]]:
    """{ruta_relativa: (mtime_ns, tamaño)} de todos los ``.py`` del proyecto."""
    huella: dict[str, tuple[int, int]] = {}
    raiz = Path(directorio)
    if not raiz.is_dir():
        return huella
    for camino in sorted(raiz.rglob("*.py")):
        if any(part in _DIRECTORIOS_IGNORADOS for part in camino.parts):
            continue
        try:
            stat = camino.stat()
        except OSError:
            continue
        huella[camino.relative_to(raiz).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return huella


def _archivos_python(directorio: str) -> list[Path]:
    """Lista de ``.py`` del proyecto (sin dependencias ni artefactos)."""
    raiz = Path(directorio)
    if not raiz.is_dir():
        return []
    return [
        camino
        for camino in sorted(raiz.rglob("*.py"))
        if not any(part in _DIRECTORIOS_IGNORADOS for part in camino.parts)
    ]


# ---------------------------------------------------------------------------
# Extracción de nodos y aristas (AST)
# ---------------------------------------------------------------------------
def _archivo_de_modulo(
    rel: str, modulo: str, archivos: dict[str, str], paquetes: dict[str, str]
) -> str | None:
    """Resuelve ``modulo`` (p. ej. ``servicios.pagos``) a un archivo del
    proyecto. Devuelve la ruta relativa o None si es externo."""
    if not modulo:
        return None
    if modulo in paquetes:  # paquete → su __init__.py
        return paquetes[modulo]
    if modulo in archivos:  # módulo simple
        return archivos[modulo]
    # Coincidencia por nombre de módulo final (p. ej. "from .pagos import X"
    # dentro del mismo paquete, o "import pagos" desde cualquier sitio).
    final = modulo.rsplit(".", 1)[-1]
    if final in archivos:
        return archivos[final]
    if final in paquetes:
        return paquetes[final]
    return None


def _extraer_nodos_y_aristas(directorio: str) -> dict:  # noqa: C901  (refactor de complejidad: Fase 10c)
    """Extrae el grafo del proyecto con AST.

    - **Nodos**: cada ``.py`` (``tipo: archivo``) y cada función/clase
      (``tipo: funcion``/``clase``, id ``archivo::nombre``).
    - **Aristas**: ``{"origen", "destino", "tipo"}`` con tipo
      ``import`` | ``llamada`` | ``herencia``.

    Nunca lanza excepciones: los archivos con sintaxis inválida se omiten
    (solo aportan el nodo de archivo).
    """
    raiz = Path(directorio)
    caminos = _archivos_python(directorio)

    # Mapas de resolución: módulo dotted y nombre de stem → ruta relativa.
    archivos: dict[str, str] = {}  # "servicios.pagos" → "servicios/pagos.py"
    paquetes: dict[str, str] = {}  # "servicios" → "servicios/__init__.py"
    rels: list[str] = []
    for camino in caminos:
        rel = camino.relative_to(raiz).as_posix()
        rels.append(rel)
        partes = rel[:-3].replace("/", ".").split(".")
        if partes[-1] == "__init__":
            paquetes[".".join(partes[:-1])] = rel
        else:
            archivos[".".join(partes)] = rel

    nodos: dict[str, dict] = {}
    aristas: list[dict] = []
    vistos: set[tuple[str, str, str]] = set()

    # Pasada 1: nodos de archivo + mapa global de definiciones (funciones y
    # clases de TODO el proyecto) para poder resolver herencia/llamadas
    # entre archivos (p. ej. class Perro(Animal) con Animal en otro módulo).
    definiciones: dict[str, list[str]] = {}  # nombre func/clase → ids nodo
    arboles: dict[str, ast.AST] = {}
    for rel in rels:
        nodos[rel] = {"tipo": "archivo", "archivo": rel}
        try:
            contenido = (raiz / rel.replace("/", os.sep)).read_text(
                encoding="utf-8", errors="replace"
            )
            arbol = ast.parse(contenido)
        except (OSError, SyntaxError, ValueError):
            continue  # sintaxis inválida
        arboles[rel] = arbol
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ident = f"{rel}::{nodo.name}"
                nodos[ident] = {"tipo": "funcion", "archivo": rel, "linea": nodo.lineno}
                definiciones.setdefault(nodo.name, []).append(ident)
            elif isinstance(nodo, ast.ClassDef):
                ident = f"{rel}::{nodo.name}"
                nodos[ident] = {"tipo": "clase", "archivo": rel, "linea": nodo.lineno}
                definiciones.setdefault(nodo.name, []).append(ident)

    def _enlazar(origen: str, destino: str | None, tipo: str) -> None:
        if destino and destino != origen:
            clave = (origen, destino, tipo)
            if clave not in vistos:
                vistos.add(clave)
                aristas.append({"origen": origen, "destino": destino, "tipo": tipo})

    # Pasada 2: aristas (imports, llamadas, herencia). Para las llamadas se
    # rastrea la función contenedora (p. ej. cobrar → procesar) con un
    # recorrido recursivo que mantiene el contexto.
    for rel, arbol in arboles.items():  # type: ignore[assignment]
        # `arbol` es el AST parseado del archivo (tipo `ast.Module`, subclase de `ast.AST`).

        def _visitar(  # noqa: C901  (refactor de complejidad: Fase 10d)
            nodo: ast.AST, contexto: str, _rel: str = rel
        ) -> None:
            # `_rel` vincula el `rel` de esta iteración (B023: la closure se
            # define dentro del bucle; el binding por defecto la fija).
            if isinstance(nodo, ast.ClassDef):
                for base in nodo.bases:
                    nombre = getattr(base, "id", None) or getattr(base, "attr", None)
                    if nombre and nombre in definiciones:
                        _enlazar(f"{_rel}::{nodo.name}", definiciones[nombre][0], "herencia")
                for hijo in ast.iter_child_nodes(nodo):
                    _visitar(hijo, contexto)
                return
            if isinstance(nodo, ast.Import):
                for alias in nodo.names:
                    _enlazar(
                        _rel, _archivo_de_modulo(_rel, alias.name, archivos, paquetes), "import"
                    )
            elif isinstance(nodo, ast.ImportFrom):
                _enlazar(
                    _rel, _archivo_de_modulo(_rel, nodo.module or "", archivos, paquetes), "import"
                )
            elif isinstance(nodo, ast.Call):
                funcion = getattr(nodo.func, "id", None) or getattr(nodo.func, "attr", None)
                destino: str | None = None
                if funcion and funcion in definiciones:
                    destino = definiciones[funcion][0]
                else:
                    # p. ej. pagos.procesar(...) → módulo pagos
                    modulo = getattr(getattr(nodo.func, "value", None), "id", None)
                    if modulo:
                        destino = _archivo_de_modulo(_rel, modulo, archivos, paquetes)
                if destino:
                    _enlazar(contexto, destino, "llamada")
            for hijo in ast.iter_child_nodes(nodo):
                if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _visitar(hijo, f"{_rel}::{hijo.name}")
                else:
                    _visitar(hijo, contexto)

        _visitar(arbol, rel)
    return {"nodos": nodos, "aristas": aristas}


# ---------------------------------------------------------------------------
# Construcción y persistencia del grafo
# ---------------------------------------------------------------------------
def _mapas_resolucion(directorio):
    """``(archivos, paquetes, rels)``: mapas globales para resolver imports."""
    raiz = Path(directorio)
    caminos = _archivos_python(directorio)
    archivos: dict[str, str] = {}
    paquetes: dict[str, str] = {}
    rels: list[str] = []
    for camino in caminos:
        rel = camino.relative_to(raiz).as_posix()
        rels.append(rel)
        partes = rel[:-3].replace("/", ".").split(".")
        if partes[-1] == "__init__":
            paquetes[".".join(partes[:-1])] = rel
        else:
            archivos[".".join(partes)] = rel
    return archivos, paquetes, rels


def _nodos_y_defs_archivo(raiz, rel: str) -> tuple[dict, dict[str, list[str]]]:
    """(nodos, definiciones) de UN archivo (sin aristas)."""
    nodos: dict = {rel: {"tipo": "archivo", "archivo": rel}}
    defs: dict[str, list[str]] = {}
    try:
        contenido = (raiz / rel.replace("/", os.sep)).read_text(encoding="utf-8", errors="replace")
        arbol = ast.parse(contenido)
    except (OSError, SyntaxError, ValueError):
        return nodos, defs
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ident = f"{rel}::{nodo.name}"
            nodos[ident] = {"tipo": "funcion", "archivo": rel, "linea": nodo.lineno}
            defs.setdefault(nodo.name, []).append(ident)
        elif isinstance(nodo, ast.ClassDef):
            ident = f"{rel}::{nodo.name}"
            nodos[ident] = {"tipo": "clase", "archivo": rel, "linea": nodo.lineno}
            defs.setdefault(nodo.name, []).append(ident)
    return nodos, defs


def _aristas_archivo(  # noqa: C901  (refactor de complejidad: Fase 10c)
    raiz,
    rel: str,
    archivos: dict[str, str],
    paquetes: dict[str, str],
    definiciones: dict[str, list[str]],
) -> list[dict]:
    """Aristas de UN archivo (import/llamada/herencia), v6.9.0 incremental."""
    try:
        contenido = (raiz / rel.replace("/", os.sep)).read_text(encoding="utf-8", errors="replace")
        arbol = ast.parse(contenido)
    except (OSError, SyntaxError, ValueError):
        return []
    aristas: list[dict] = []
    vistos: set[tuple[str, str, str]] = set()

    def _enlazar(origen: str, destino: str | None, tipo: str) -> None:
        if destino and destino != origen:
            clave = (origen, destino, tipo)
            if clave not in vistos:
                vistos.add(clave)
                aristas.append({"origen": origen, "destino": destino, "tipo": tipo})

    def _visitar(nodo: ast.AST, contexto: str) -> None:  # noqa: C901  (refactor de complejidad: Fase 10c)
        if isinstance(nodo, ast.ClassDef):
            for base in nodo.bases:
                nombre = getattr(base, "id", None) or getattr(base, "attr", None)
                if nombre and nombre in definiciones:
                    _enlazar(f"{rel}::{nodo.name}", definiciones[nombre][0], "herencia")
            for hijo in ast.iter_child_nodes(nodo):
                _visitar(hijo, contexto)
            return
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                _enlazar(rel, _archivo_de_modulo(rel, alias.name, archivos, paquetes), "import")
        elif isinstance(nodo, ast.ImportFrom):
            _enlazar(rel, _archivo_de_modulo(rel, nodo.module or "", archivos, paquetes), "import")
        elif isinstance(nodo, ast.Call):
            funcion = getattr(nodo.func, "id", None) or getattr(nodo.func, "attr", None)
            destino: str | None = None
            if funcion and funcion in definiciones:
                destino = definiciones[funcion][0]
            else:
                modulo = getattr(getattr(nodo.func, "value", None), "id", None)
                if modulo:
                    destino = _archivo_de_modulo(rel, modulo, archivos, paquetes)
            if destino:
                _enlazar(contexto, destino, "llamada")
        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _visitar(hijo, f"{rel}::{hijo.name}")
            else:
                _visitar(hijo, contexto)

    _visitar(arbol, rel)
    return aristas


def _grafo_incremental(directorio: str, cache: dict):
    """Reconstruye solo los archivos cambiados (v6.9.0).

    Reutiliza de la caché los nodos/aristas de los archivos cuyo fingerprint
    (mtime+tamaño) no cambió y solo re-parsea los modificados/añadidos, lo que
    reduce el tiempo de construcción tras un cambio a una fracción del total.
    """
    raiz = Path(directorio)
    huella = _fingerprint(directorio)
    vieja_huella = cache.get("fingerprint") or {}
    viejo_por = cache.get("por_archivo") or {}
    archivos, paquetes, rels = _mapas_resolucion(directorio)
    # Archivos cuyo fingerprint cambió o aparecieron nuevos.
    cambiados = {rel for rel in rels if huella.get(rel) != vieja_huella.get(rel)}

    # 1) nodos + definiciones (reutiliza los no cambiados, parsea los nuevos).
    contribs: dict[str, dict] = {}
    definiciones: dict[str, list[str]] = {}
    for rel in rels:
        if rel not in cambiados and viejo_por.get(rel):
            contribs[rel] = viejo_por[rel]
        else:
            nodos, defs = _nodos_y_defs_archivo(raiz, rel)
            contribs[rel] = {"nodos": nodos, "defs": defs}
        for nombre, ids in contribs[rel].get("defs", {}).items():
            definiciones.setdefault(nombre, [])
            definiciones[nombre].extend(ids)

    # 2) aristas (solo se recalculan las del archivo cambiado; el resto se
    #    reutiliza tal cual).
    grafo: dict[str, dict] = {}
    aristas: list[dict] = []
    vistos: set[tuple[str, str, str]] = set()
    por_archivo_cambios: dict[str, dict] = {}
    for rel in rels:
        contrib = contribs[rel]
        if rel in cambiados:
            contrib["aristas"] = _aristas_archivo(raiz, rel, archivos, paquetes, definiciones)
        for ident, nd in contrib.get("nodos", {}).items():
            grafo[ident] = nd
        for arista in contrib.get("aristas", []):
            clave = (arista["origen"], arista["destino"], arista["tipo"])
            if clave not in vistos:
                vistos.add(clave)
                aristas.append(arista)
        por_archivo_cambios[rel] = {
            "nodos": contrib.get("nodos", {}),
            "defs": contrib.get("defs", {}),
            "aristas": contrib.get("aristas", []),
        }
    return {"nodos": grafo, "aristas": aristas}, por_archivo_cambios


def construir_grafo(directorio: str, forzar: bool = False, ruta_cache: str | None = None) -> dict:  # noqa: C901  (refactor de complejidad: Fase 10c)
    """Devuelve el grafo del proyecto, usando el cache si sigue válido.

    - Si ``~/.snapcontext/graph_cache.pkl`` existe, no se fuerza y el
      fingerprint (mtime + tamaño de cada ``.py``) coincide → se carga.
    - Si el fingerprint cambió pero existe la caché incremental (v6.9.0), solo
      se re-parsean los archivos modificados en lugar de todo el proyecto.
    - En cualquier otro caso se reconstruye y se persiste, guardando también la
      contribución por archivo para acelerar futuros builds incrementales.
    Nunca lanza excepciones por problemas de cache: se reconstruye.
    """
    ruta = Path(ruta_cache) if ruta_cache else _ruta_cache_defecto()
    huella = _fingerprint(directorio)
    if not forzar:
        try:
            if ruta.is_file():
                with open(ruta, "rb") as manejador:
                    cache = pickle.load(manejador)
                if (
                    isinstance(cache, dict)
                    and cache.get("version") == _VERSION_CACHE
                    and isinstance(cache.get("grafo"), dict)
                ):
                    if cache.get("fingerprint") == huella:
                        result = cache["grafo"]
                        if isinstance(result, dict):
                            return result
                    # v6.9.0: caché incremental — solo archivos cambiados.
                    if isinstance(cache.get("por_archivo"), dict):
                        grafo, por = _grafo_incremental(directorio, cache)
                        result = grafo
                        try:
                            ruta.parent.mkdir(parents=True, exist_ok=True)
                            with open(ruta, "wb") as manejador2:
                                pickle.dump(
                                    {
                                        "version": _VERSION_CACHE,
                                        "fingerprint": huella,
                                        "grafo": grafo,
                                        "por_archivo": por,
                                    },
                                    manejador2,
                                )
                        except Exception:
                            pass
                        if isinstance(result, dict):
                            return result
        except Exception:
            pass  # cache ilegible → rebuild
    grafo = _extraer_nodos_y_aristas(directorio)
    # v6.9.0: persiste también la contribución por archivo para poder hacer
    # builds incrementales en cambios posteriores.
    por_cambios: dict[str, dict] = {}
    raiz = Path(directorio)
    archivos_m, paquetes_m, rels = _mapas_resolucion(directorio)
    defs_global: dict[str, list[str]] = {}
    for rel in rels:
        nodos, defs = _nodos_y_defs_archivo(raiz, rel)
        por_cambios[rel] = {"nodos": nodos, "defs": defs}
        for nombre, ids in defs.items():
            defs_global.setdefault(nombre, [])
            defs_global[nombre].extend(ids)
    for rel in rels:
        por[rel]["aristas"] = _aristas_archivo(raiz, rel, archivos_m, paquetes_m, defs_global)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "wb") as manejador:
            pickle.dump(
                {
                    "version": _VERSION_CACHE,
                    "fingerprint": huella,
                    "grafo": grafo,
                    "por_archivo": por,
                },
                manejador,
            )
    except Exception:
        pass  # persistencia best-effort
    return grafo


# ---------------------------------------------------------------------------
# Expansión de contexto
# ---------------------------------------------------------------------------
def _archivo_de_nodo(ident: str) -> str:
    """Convierte un id de nodo a su archivo (``a/b.py::f`` → ``a/b.py``)."""
    return ident.split("::", 1)[0]


def expandir_contexto(  # noqa: C901  (refactor de complejidad: Fase 10c)
    archivos_relevantes: list[str], grafo: dict, max_adicionales: int = 3, notificar: bool = True
) -> list[str]:
    """Amplía ``archivos_relevantes`` con archivos relacionados del grafo.

    Para cada archivo añade (en este orden de prioridad):
      1. Dependencias **entrantes**: archivos que lo importan/usan.
      2. Dependencias **salientes**: archivos que él importa/usa.

    Prioriza los archivos con más conexiones y nunca supera
    ``max_adicionales`` añadidos. Devuelve la lista ampliada (los originales
    mantienen su orden al principio). Nunca lanza excepciones.
    """
    try:
        originales = [a for a in (archivos_relevantes or []) if a]
        if not originales or not isinstance(grafo, dict) or max_adicionales <= 0:
            return list(originales or [])
        aristas = grafo.get("aristas") or []
        if not aristas:
            return list(originales)

        entrantes: dict[str, dict[str, int]] = {}
        salientes: dict[str, dict[str, int]] = {}
        for arista in aristas:
            origen = _archivo_de_nodo(str(arista.get("origen", "")))
            destino = _archivo_de_nodo(str(arista.get("destino", "")))
            if not origen or not destino or origen == destino:
                continue
            salientes.setdefault(origen, {})
            salientes[origen][destino] = salientes[origen].get(destino, 0) + 1
            entrantes.setdefault(destino, {})
            entrantes[destino][origen] = entrantes[destino].get(origen, 0) + 1

        seleccion = list(originales)
        vistos: set[str] = set(seleccion)
        for archivo in originales:
            # Entrantes primero (quien usa el archivo relevante suele
            # necesitar verse junto a él), luego salientes.
            vecinos = sorted(entrantes.get(archivo, {}).items(), key=lambda kv: (-kv[1], kv[0]))
            vecinos += sorted(salientes.get(archivo, {}).items(), key=lambda kv: (-kv[1], kv[0]))
            for relativo, _conteo in vecinos:
                if len(seleccion) - len(originales) >= max_adicionales:
                    break
                if relativo not in vistos:
                    vistos.add(relativo)
                    seleccion.append(relativo)
            if len(seleccion) - len(originales) >= max_adicionales:
                break

        nuevos = len(seleccion) - len(originales)
        if nuevos and notificar:
            mensaje = (
                f"🔗 Grafo de conocimiento: expandiendo contexto con "
                f"{nuevos} archivo(s) relacionado(s)."
            )
            try:
                import snapcontext as sc

                sc.info(mensaje)
            except Exception:
                print(mensaje)
        return seleccion
    except Exception:
        return list(archivos_relevantes or [])
