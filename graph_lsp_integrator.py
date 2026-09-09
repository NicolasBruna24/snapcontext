#!/usr/bin/env python3
"""
Integración Graph RAG + LSP (v6.33.0) — contexto preciso por símbolos.

El Graph RAG (v5.5.0) mapea dependencias entre archivos; el cliente LSP
(v6.14.0) resuelve definiciones/referencias exactas. Este módulo los une:

1. Usa LSP para obtener símbolos exactos (definiciones, referencias, tipos).
2. Usa Graph RAG para priorizar qué símbolos son más relevantes.
3. Inyecta solo los símbolos relevantes en el prompt (no archivos completos).

Es 100 % opcional (``--graph-rag-lsp``): sin el flag el comportamiento es
idéntico al actual. Las llamadas al LSP son perezosas y con caché.
"""

import ast
import re
import threading
from pathlib import Path
from typing import Any

# Timeout global (en segundos) para las llamadas al LSP (Fase 16). Si el LSP
# no responde en este tiempo, se degrada a búsqueda por regex sin bloquear.
TIMEOUT_LSP_DEGRADACION = 2.0

# Centinela interno para marcar "la llamada al LSP agotó el tiempo".
_TIMEOUT = object()

# Directorios que nunca se usan en la búsqueda por regex de respaldo.
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

__all__ = [
    "TIMEOUT_LSP_DEGRADACION",
    "GraphLSPIntegrator",
    "configuracion_graph_lsp",
    "inyectar_contexto_preciso",
    "obtener_contexto_preciso",
    "simbolos_defecto",
]

# Configuración por defecto.
simbolos_defecto: dict[str, Any] = {
    "activo": False,
    "profundidad": 2,
    "simbolos_max": 10,
}


def configuracion_graph_lsp(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Devuelve la configuración efectiva Graph RAG + LSP (v6.33.0).

    Fusiona los valores por defecto con ``config["graph_lsp"]`` si existe.
    """
    efectiva = dict(simbolos_defecto)
    if not isinstance(config, dict):
        return efectiva
    seccion = config.get("graph_lsp")
    if not isinstance(seccion, dict):
        return efectiva
    if "activo" in seccion:
        efectiva["activo"] = bool(seccion["activo"])
    if "profundidad" in seccion:
        try:
            efectiva["profundidad"] = max(1, int(seccion["profundidad"]))
        except (TypeError, ValueError):
            pass
    if "simbolos_max" in seccion:
        try:
            efectiva["simbolos_max"] = max(1, int(seccion["simbolos_max"]))
        except (TypeError, ValueError):
            pass
    return efectiva


class GraphLSPIntegrator:
    """Integra Graph RAG y LSP para obtener contexto preciso (v6.33.0).

    Uso típico::

        integ = GraphLSPIntegrator(grafo, config)
        simbolos = integ.obtener_contexto_preciso("main.py", 42, "funcion")
        prompt = integ.inyectar_contexto_preciso(contexto_actual, simbolos)
    """

    def __init__(
        self,
        grafo: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        proveedor_lsp: Any | None = None,
    ):
        self.grafo = grafo or {}
        self.config = configuracion_graph_lsp(config)
        self.proveedor_lsp = proveedor_lsp
        self._cache_simbolos: dict[str, Any] = {}

    def obtener_contexto_preciso(
        self,
        archivo: str,
        linea: int | None = None,
        tipo: str = "funcion",
        max_simbolos: int | None = None,
    ) -> list[dict[str, Any]]:
        """Obtiene símbolos precisos de un archivo (v6.33.0).

        Usa LSP para definiciones/referencias y Graph RAG para expandir el
        contexto con dependencias. Devuelve una lista de símbolos con su
        contenido, priorizados por relevancia (frecuencia de llamadas).
        """
        max_sim = max_simbolos or self.config.get("simbolos_max", 10)
        profundidad = self.config.get("profundidad", 2)
        cache_key = f"{archivo}:{linea}:{tipo}:{max_sim}:{profundidad}"
        if cache_key in self._cache_simbolos:
            return self._cache_simbolos[cache_key]  # type: ignore[no-any-return]
        simbolos: list[dict[str, Any]] = []
        lsp = self._obtener_simbolos_lsp(archivo, linea)
        if lsp:
            simbolos.extend(lsp)
        expandidos = self._expandir_con_grafo(archivo, profundidad, max_sim)
        nombres_existentes = {s.get("nombre") for s in simbolos}
        for sim in expandidos:
            if sim.get("nombre") not in nombres_existentes:
                simbolos.append(sim)
                nombres_existentes.add(sim.get("nombre"))
        simbolos.sort(key=lambda s: s.get("relevancia", 0), reverse=True)
        resultado = simbolos[:max_sim]
        self._cache_simbolos[cache_key] = resultado
        return resultado

    def _obtener_simbolos_lsp(
        self,
        archivo: str,
        linea: int | None,
    ) -> list[dict[str, Any]]:
        """Obtiene símbolos vía LSP con caché (v6.33.0).

        Fase 16: si el LSP no responde en ``TIMEOUT_LSP_DEGRADACION`` segundos o
        lanza una excepción, se degrada silenciosamente a la búsqueda por regex
        (y embeddings sintácticos ligeros si están disponibles) y se emite un
        único aviso informativo. Nunca propaga excepciones.
        """
        cache_key = f"{archivo}:{linea}"
        if cache_key in self._cache_simbolos:
            return self._cache_simbolos[cache_key]  # type: ignore[no-any-return]
        crudos, degradado = self._intentar_lsp(archivo, linea)
        simbolos = self._normalizar_simbolos(crudos, archivo)
        # Modo degradado: el LSP falló o no devolvió nada.
        if not simbolos:
            simbolos = self._buscar_por_regex(archivo, linea)
            if not simbolos:
                simbolos = self._buscar_por_embeddings_ligeros(archivo, linea)
            if degradado:
                self._avisar_degradacion(archivo)
        self._cache_simbolos[cache_key] = simbolos
        return simbolos

    def _intentar_lsp(self, archivo: str, linea: int | None) -> tuple[list[Any], bool]:
        """Llama al LSP con timeout; devuelve ``(crudos, degradado)``."""
        degradado = False
        crudos: list[Any] = []
        try:
            import lsp_client as lsp

            llamada = None
            if self.proveedor_lsp is not None and hasattr(self.proveedor_lsp, "obtener_simbolos"):
                llamada = lambda: self.proveedor_lsp.obtener_simbolos(archivo, linea)  # noqa: E731  (lambda asignada: refactor en Fase 2b)
            elif hasattr(lsp, "obtener_simbolos"):
                llamada = lambda: lsp.obtener_simbolos(archivo, linea)  # noqa: E731  (lambda asignada: refactor en Fase 2b)
            if llamada is None:
                degradado = True
                return crudos, degradado
            resultado = _con_timeout(llamada, timeout=TIMEOUT_LSP_DEGRADACION)
            if resultado is _TIMEOUT:
                degradado = True
                return crudos, degradado
            if resultado:
                crudos = list(resultado)
        except Exception:
            degradado = True
            crudos = []
        return crudos, degradado

    @staticmethod
    def _normalizar_simbolos(crudos: list[Any], archivo: str) -> list[dict[str, Any]]:
        """Convierte la respuesta cruda del LSP al formato común de símbolos."""
        simbolos: list[dict[str, Any]] = []
        for raw in crudos if isinstance(crudos, (list, tuple)) else []:
            if isinstance(raw, dict):
                simbolos.append(raw)
            elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
                simbolos.append(
                    {
                        "nombre": str(raw[0]),
                        "linea": int(raw[1]) if len(raw) > 1 else 0,
                        "archivo": archivo,
                        "tipo": str(raw[2]) if len(raw) > 2 else "simbolo",
                        "relevancia": int(raw[3]) if len(raw) > 3 else 0,
                    }
                )
        return simbolos

    # ------------------------------------------------------------------
    # Fallbacks del LSP (Fase 16: degradación elegante)
    # ------------------------------------------------------------------
    def _avisar_degradacion(self, archivo: str) -> None:
        """Avisa una única vez por archivo; nunca muestra un traceback."""
        if getattr(self, "_degradados_avisados", None) is None:
            self._degradados_avisados: set[str] = set()
        clave = str(archivo)
        if clave in self._degradados_avisados:
            return
        self._degradados_avisados.add(clave)
        _aviso("LSP no disponible, usando búsqueda por regex.")

    def _buscar_por_regex(  # noqa: C901  (refactor de complejidad: Fase 10c)
        self, archivo: str, linea: int | None, max_coincidencias: int = 25
    ) -> list[dict[str, Any]]:
        """Búsqueda por regex: rápida y sin dependencias externas.

        Si ``linea`` se conoce, extrae el identificador de código de esa línea
        y busca sus apariciones en los archivos del proyecto. Si no, devuelve
        las definiciones ``def/class`` del propio archivo. Resultado en el
        mismo formato que el LSP (``nombre``, ``linea``, ``archivo``, ``tipo``).
        """
        ruta = Path(str(archivo))
        try:
            lineas = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        simbolos: list[dict[str, Any]] = []
        if linea is not None:
            texto_linea = lineas[linea - 1] if 0 < linea <= len(lineas) else ""
            token = _identificador_en_linea(texto_linea)
            if not token:
                return []
            proyecto = _raiz_proyecto(ruta)
            _patron = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])")
            for camino in _archivos_a_buscar(proyecto):
                try:
                    texto = camino.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for numero, contenido in enumerate(texto.splitlines(), 1):
                    if _patron.search(contenido):
                        simbolos.append(
                            {
                                "nombre": token,
                                "linea": numero,
                                "columna": 1,
                                "archivo": _a_relativo(camino, proyecto),
                                "tipo": "referencia",
                                "contenido": contenido.strip(),
                                "relevancia": 1,
                            }
                        )
                        if len(simbolos) >= max_coincidencias:
                            return simbolos
            return simbolos
        # Sin línea concreta: definiciones def/class de este archivo.
        _patron_def = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
        for numero, contenido in enumerate(lineas, 1):
            m = _patron_def.match(contenido)
            if m:
                simbolos.append(
                    {
                        "nombre": m.group(1),
                        "linea": numero,
                        "columna": 1,
                        "archivo": str(ruta),
                        "tipo": "simbolo",
                        "contenido": contenido.strip(),
                        "relevancia": 1,
                    }
                )
        return simbolos

    def _buscar_por_embeddings_ligeros(
        self, archivo: str, linea: int | None
    ) -> list[dict[str, Any]]:
        """Embeddings sintácticos ligeros (AST + tree-sitter si hay).

        Extrae la estructura sintáctica del archivo (funciones/clases) sin
        recurrir al LSP. Usa ``ast`` para Python y ``tree-sitter`` (si está
        instalado) para el resto de lenguajes; sino, devuelve ``[]``.
        """
        ruta = Path(str(archivo))
        simbolos: list[dict[str, Any]] = []
        # 1) Python nativo: esencialmente gratis, sin dependencias.
        if ruta.suffix.lower() == ".py":
            try:
                arbol = ast.parse(ruta.read_text(encoding="utf-8", errors="replace"))
                for nodo in ast.walk(arbol):
                    if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        simbolos.append(
                            {
                                "nombre": nodo.name,
                                "linea": int(nodo.lineno),
                                "columna": 1,
                                "archivo": str(ruta),
                                "tipo": "funcion",
                                "contenido": "",
                                "relevancia": 1,
                            }
                        )
                    elif isinstance(nodo, ast.ClassDef):
                        simbolos.append(
                            {
                                "nombre": nodo.name,
                                "linea": int(nodo.lineno),
                                "columna": 1,
                                "archivo": str(ruta),
                                "tipo": "clase",
                                "contenido": "",
                                "relevancia": 1,
                            }
                        )
            except (OSError, SyntaxError, ValueError):
                simbolos = []
        # 2) tree-sitter para el resto de lenguajes (opcional).
        if not simbolos:
            simbolos = _simbolos_tree_sitter(ruta)
        return simbolos

    def _expandir_con_grafo(
        self,
        archivo: str,
        profundidad: int,
        max_sim: int,
    ) -> list[dict[str, Any]]:
        """Usa Graph RAG para expandir contexto con dependencias (v6.33.0)."""
        simbolos: list[dict[str, Any]] = []
        try:
            import graph_rag as gr

            if not self.grafo or not hasattr(gr, "expandir_contexto"):
                return simbolos
            vecinos = []
            if hasattr(gr, "obtener_vecinos"):
                vecinos = gr.obtener_vecinos(self.grafo, archivo, profundidad)  # type: ignore[attr-defined]
            elif hasattr(gr, "expandir_contexto"):
                vecinos = gr.expandir_contexto([archivo], self.grafo, max_adicionales=max_sim)
                vecinos = [v for v in vecinos if v != archivo]
            for vecino in vecinos[:max_sim]:
                simbolos.append(
                    {
                        "nombre": vecino,
                        "archivo": vecino,
                        "linea": 1,
                        "tipo": "dependencia",
                        "contenido": "",
                        "relevancia": 1,
                    }
                )
        except Exception:
            pass
        return simbolos

    def inyectar_contexto_preciso(
        self,
        contexto_actual: str,
        simbolos: list[dict[str, Any]],
    ) -> str:
        """Inyecta símbolos precisos en el contexto (v6.33.0).

        Reemplaza archivos completos por los símbolos extraídos. Si no hay
        símbolos, devuelve el contexto_actual sin cambios.
        """
        if not simbolos:
            return contexto_actual
        lineas = ["# Contexto preciso (Graph RAG + LSP):"]
        for sim in simbolos:
            nombre = sim.get("nombre", "?")
            archivo = sim.get("archivo", "?")
            linea = sim.get("linea", 1)
            tipo = sim.get("tipo", "simbolo")
            contenido = sim.get("contenido", "")
            prefijo = f"  - {tipo} {nombre} ({archivo}:{linea})"
            if contenido:
                contenido_lineas = str(contenido).splitlines()[:5]
                prefijo += ":\n      " + "\n      ".join(contenido_lineas)
            lineas.append(prefijo)
        lineas.append("")
        if contexto_actual:
            lineas.append("# Contexto previo (parcial):")
            lineas.append(contexto_actual[:500])
        return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Funciones standalone
# ---------------------------------------------------------------------------
def obtener_contexto_preciso(
    archivo: str,
    linea: int | None = None,
    tipo: str = "funcion",
    grafo: dict[str, Any] | None = None,
    max_simbolos: int = 10,
    config: dict[str, Any] | None = None,
    proveedor_lsp: Any | None = None,
) -> list[dict[str, Any]]:
    """Función standalone: obtiene símbolos precisos (v6.33.0)."""
    integ = GraphLSPIntegrator(grafo=grafo, config=config, proveedor_lsp=proveedor_lsp)
    return integ.obtener_contexto_preciso(archivo, linea, tipo, max_simbolos)


def inyectar_contexto_preciso(
    contexto_actual: str,
    simbolos: list[dict[str, Any]],
) -> str:
    """Función standalone: inyecta símbolos precisos (v6.33.0)."""
    integ = GraphLSPIntegrator()
    return integ.inyectar_contexto_preciso(contexto_actual, simbolos)


# ---------------------------------------------------------------------------
# Helpers internos (degradación elegante del LSP, Fase 16)
# ---------------------------------------------------------------------------
def _con_timeout(llamada, timeout: float, *args, **kwargs):
    """Ejecuta ``llamada`` con un límite de tiempo duro (hilo daemon).

    Devuelve ``_TIMEOUT`` si no termina en ``timeout`` segundos. El hilo daemon
    terminado puede seguir corriendo en segundo plano (el cliente LSP ya tiene
    su propio timeout) pero nunca bloquea al llamante.
    """
    caja: dict[str, Any] = {}

    def _run() -> None:
        try:
            caja["resultado"] = llamada(*args, **kwargs)
        except Exception as exc:
            caja["error"] = exc

    hilo = threading.Thread(target=_run, daemon=True)
    hilo.start()
    hilo.join(timeout)
    if hilo.is_alive():
        return _TIMEOUT
    if "error" in caja:
        raise caja["error"]  # type: ignore[misc]
    return caja.get("resultado", _TIMEOUT)


def _aviso(mensaje: str) -> None:
    """Emite un aviso informativo mediante la capa UI de snapcontext (si existe)."""
    try:
        import snapcontext as sc

        avisar = getattr(sc, "aviso", None)
        if callable(avisar):
            avisar(mensaje)
            return
    except Exception:
        pass
    print(mensaje)


def _identificador_en_linea(linea: str) -> str:
    """Devuelve el identificador de código más significativo de ``linea``."""
    coincidencias = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", linea)
    if not coincidencias:
        return ""
    # Preferimos el identificador más largo (suele ser el símbolo relevante).
    return max(coincidencias, key=lambda s: (len(s), s))  # type: ignore[no-any-return]


def _raiz_proyecto(ruta: Path) -> Path:
    """Raíz del proyecto que contiene ``ruta``.

    Reconoce marcadores VCS/manifiesto y limita la subida a un máximo de
    niveles para que la búsqueda por regex de respaldo siga siendo rápida
    (nunca escala a todo el sistema). Si no encuentra marcador, usa el
    directorio inmediato del archivo.
    """
    actual = ruta.resolve()
    if actual.is_file():
        actual = actual.parent
    _marcadores = (
        ".git",
        ".hg",
        ".svn",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
        "requirements.txt",
    )
    _max_profundidad = 6
    for _ in range(_max_profundidad):
        if any((actual / m).exists() for m in _marcadores):
            return actual
        padre = actual.parent
        if padre == actual or padre == Path.home():
            return actual
        actual = padre
    return actual


def _archivos_a_buscar(raiz: Path, max_archivos: int = 1500):
    """Archivos de código del proyecto, ignorando dependencias/artefactos."""
    extensiones = (
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".cs",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".rb",
        ".php",
    )
    resultado: list[Path] = []
    if not raiz.is_dir():
        return resultado
    for camino in raiz.rglob("*"):
        if len(resultado) >= max_archivos:
            break
        if not camino.is_file():
            continue
        if camino.suffix.lower() not in extensiones:
            continue
        if any(parte in _DIRECTORIOS_IGNORADOS for parte in camino.parts):
            continue
        resultado.append(camino)
    return resultado


def _a_relativo(camino: Path, proyecto: Path) -> str:
    """Ruta relativa al proyecto si ``camino`` está dentro; si no, la absoluta."""
    try:
        return camino.resolve().relative_to(proyecto.resolve()).as_posix()
    except ValueError:
        return str(camino)


def _simbolos_tree_sitter(ruta: Path) -> list[dict[str, Any]]:
    """Extrae símbolos estructurales con tree-sitter (opcional).

    Si ``tree_sitter`` y los parsers de lenguaje no están instalados, devuelve
    ``[]`` silenciosamente (best-effort; nunca rompe el flujo).
    """
    simbolos: list[dict[str, Any]] = []
    try:
        import tree_sitter  # type: ignore[import-not-found]

        lenguaje = _tree_sitter_lenguaje(ruta)
        if lenguaje is None or not hasattr(tree_sitter, "Parser"):
            return simbolos
        parser = tree_sitter.Parser(lenguaje)
        arbol = parser.parse(ruta.read_bytes())
        for nombre, linea in genericos_tree_sitter(arbol):
            simbolos.append(
                {
                    "nombre": nombre,
                    "linea": linea,
                    "columna": 1,
                    "archivo": str(ruta),
                    "tipo": "simbolo",
                    "contenido": "",
                    "relevancia": 1,
                }
            )
    except Exception:
        simbolos = []
    return simbolos


def _tree_sitter_lenguaje(ruta: Path):
    """Devuelve un objeto Language de tree-sitter para la extensión, o None."""
    try:
        import tree_sitter_python  # type: ignore[import-not-found]

        return tree_sitter_python.language()
    except Exception:
        return None


def genericos_tree_sitter(arbol) -> list[tuple[str, int]]:
    """Recorre el árbol de tree-sitter y devuelve ``(nombre, linea)`` de nodos.

    Implementación genérica basada en el tipo de nodo y campos que expone
    tree-sitter. Si no se puede recorrer, devuelve ``[]``.
    """
    resultado: list[tuple[str, int]] = []
    tipos = (
        "function_definition",
        "class_definition",
        "function_item",
        "method_definition",
        "class_declaration",
        "method_declaration",
    )

    def _visitar(nodo) -> None:
        try:
            if getattr(nodo, "tipo", None) in tipos:
                nombre = _obtener_nombre_declaracion(nodo)
                if nombre:
                    inicio = getattr(nodo, "start_point", None)
                    linea = (inicio.row if inicio is not None else 0) + 1
                    resultado.append((nombre, int(linea)))
            for hijo in getattr(nodo, "children", []):
                _visitar(hijo)
        except Exception:
            return

    try:
        _visitar(arbol.root_node)
    except Exception:
        pass
    return resultado


def _obtener_nombre_declaracion(nodo) -> str:
    """Extrae el nombre de un nodo de declaración de tree-sitter."""
    try:
        for hijo in getattr(nodo, "children", []):
            tipo = getattr(hijo, "tipo", "")
            if tipo in ("identifier", "name", "type_identifier"):
                try:
                    return hijo.text.decode("utf-8", "replace")  # type: ignore[no-any-return]
                except Exception:
                    return ""
    except Exception:
        return ""
    return ""
