#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parser universal multi-lenguaje de SnapContext (v5.6.0) — Tree-sitter.

Permite que el editor propio (transaccional, fuzzy matching, análisis de
impacto y contexto selectivo) funcione con lenguajes no-Python:
JavaScript/TypeScript, Go, Rust, Java, C/C++, C#, Ruby, PHP, Dart, …

API principal:

- ``detectar_lenguaje_por_extension(archivo)`` → nombre de gramática
  tree-sitter (``python``, ``javascript``, ``typescript``, ``go``, ``rust``,
  ``java``, …) o ``None``.
- ``detectar_lenguaje(contenido, archivo=None)`` → detección por extensión
  con heurística por contenido como respaldo (shebang, marcadores).
- ``parsear_archivo(contenido, lenguaje)`` → árbol de tree-sitter o ``None``.
- ``extraer_nodos(archivo, contenido, tipo_nodo="todos")`` → funciones,
  clases, métodos e imports con sus posiciones (para análisis de impacto y
  contexto selectivo).
- ``aplicar_parche_arbol(contenido, nodo_viejo, nodo_nuevo)`` → reemplazo
  seguro por byte-span del nodo AST (o ``None`` si no es válido).

Compatibilidad: Python sigue usando el ``ast`` de la stdlib en el editor; este
módulo también soporta Python si se prefiere uniformar el motor.

Si no hay backend disponible, todas las funciones devuelven ``None``/``[]`` y
el editor cae elegantemente a las estrategias parche/sobrescritura.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Mapa de extensiones → gramática tree-sitter
# ---------------------------------------------------------------------------
_EXTENSIONES_LENGUAJE: Dict[str, str] = {
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".hh": "cpp", ".hxx": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".dart": "dart",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".lua": "lua",
    ".sql": "sql",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown",
    ".ex": "elixir", ".exs": "elixir",
    ".scala": "scala",
    ".hs": "haskell",
    ".zig": "zig",
    ".vue": "vue", ".svelte": "svelte",
}

# Nodos AST (tree-sitter) que representan definiciones.
_NODOS_FUNCION = {"function_definition", "function_declaration",
                  "method_definition", "method_declaration",
                  "function_item", "function_signature_item",
                  "constructor_declaration", "generator_function_declaration"}
_NODOS_CLASE = {"class_definition", "class_declaration",
                "struct_item", "enum_item", "impl_item", "trait_item",
                "interface_declaration"}
# Go: los structs/interfaces son `type_declaration > type_spec`; el nodo
# contenedor no lleva nombre, así que se clasifica el `type_spec` (v5.6.0).
_NODO_TYPE_SPEC = "type_spec"

_MARCADORES_CONTENIDO = (
    ("def ", "python"), ("import ", "python"),
    ("function ", "javascript"), ("const ", "javascript"),
    ("fn ", "rust"), ("let mut ", "rust"),
    ("func ", "go"), ("package main", "go"),
    ("public class ", "java"), ("public static void main", "java"),
)


def detectar_lenguaje_por_extension(archivo: str) -> Optional[str]:
    """Nombre de gramática tree-sitter para ``archivo`` (por extensión)."""
    if not archivo:
        return None
    return _EXTENSIONES_LENGUAJE.get(Path(str(archivo)).suffix)


def detectar_lenguaje(contenido: str,
                      archivo: Optional[str] = None) -> Optional[str]:
    """Detecta el lenguaje: extensión primero, contenido como respaldo."""
    por_extension = detectar_lenguaje_por_extension(archivo or "")
    if por_extension:
        return por_extension
    texto = (contenido or "").lstrip()
    if texto.startswith("#!"):
        primera = texto.splitlines()[0] if texto else ""
        if "python" in primera:
            return "python"
        if "bash" in primera or "/sh" in primera:
            return "bash"
        if "node" in primera:
            return "javascript"
    for marcador, lenguaje in _MARCADORES_CONTENIDO:
        if marcador in texto:
            return lenguaje
    return None


# ---------------------------------------------------------------------------
# Carga perezosa de backends tree-sitter
# ---------------------------------------------------------------------------
_estado: Dict[str, Optional[object]] = {
    "backend": None, "buscado": False, "get_parser": None,
}


def _cargar_backend():
    """Devuelve ``obtener_parser(lenguaje)`` o ``None`` (memoizado).

    Prueba ``tree_sitter_language_pack`` → ``tree_sitter_languages``.
    """
    if _estado["buscado"]:
        return _estado["get_parser"]
    _estado["buscado"] = True
    try:                                   # 1) language pack moderno
        from tree_sitter_language_pack import get_parser   # type: ignore
        _estado["get_parser"] = get_parser
        _estado["backend"] = "tree_sitter_language_pack"
        return get_parser
    except Exception:                      # noqa: BLE001
        pass
    try:                                   # 2) paquete clásico
        import tree_sitter_languages as _tsl               # type: ignore
        from tree_sitter import Language, Parser           # type: ignore

        def _obtener_clasico(lenguaje: str):
            idioma = Language(_tsl.get_language(lenguaje))
            parser = Parser()
            try:
                parser.set_language(idioma)                # API < 0.22
            except Exception:                              # noqa: BLE001
                parser.language = idioma                   # API >= 0.22
            return parser

        _estado["get_parser"] = _obtener_clasico
        _estado["backend"] = "tree_sitter_languages"
        return _obtener_clasico
    except Exception:                      # noqa: BLE001
        return None


def backend_disponible() -> bool:
    """True si hay algún backend tree-sitter utilizable."""
    return _cargar_backend() is not None


def backend_activo() -> Optional[str]:
    """Nombre del backend en uso (o ``None``). Fuerza la carga perezosa."""
    _cargar_backend()
    return _estado["backend"]


def parsear_archivo(contenido: str, lenguaje: str):
    """Parsea ``contenido`` con la gramática ``lenguaje``.

    Devuelve el árbol tree-sitter o ``None`` si no hay backend, el lenguaje
    no está disponible o el parseo falla (nunca lanza excepciones).
    """
    if not contenido or not lenguaje:
        return None
    obtener = _cargar_backend()
    if obtener is None:
        return None
    try:
        parser = obtener(lenguaje)
        if parser is None:
            return None
        return parser.parse(contenido.encode("utf-8"))
    except Exception:                      # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Extracción de símbolos (funciones/clases/imports) — análisis de impacto y
# contexto selectivo multi-lenguaje
# ---------------------------------------------------------------------------
def _texto_nodo(nodo) -> str:
    """Texto de la primera línea con significado de un nodo tree-sitter."""
    try:
        texto = (nodo.text or b"").decode("utf-8", "replace")
    except Exception:                      # noqa: BLE001
        return ""
    primera = texto.strip().splitlines()[0] if texto.strip() else ""
    return primera[:120]


def _nombre_definicion(nodo, contenido: str) -> str:
    """Nombre de una definición según la gramática (heurística por campo)."""
    for campo in ("name", "declarator", "identifier"):
        hijo = nodo.child_by_field_name(campo) if hasattr(nodo, "child_by_field_name") else None
        if hijo is not None:
            try:
                texto = (hijo.text or b"").decode("utf-8", "replace").strip()
            except Exception:              # noqa: BLE001
                texto = ""
            if texto:
                # C/Java/Rust: el declarator envuelve el nombre → último token.
                return texto.split("(")[0].split("{")[0].strip().rstrip("=").strip() or texto
    return "(anónimo)"


def extraer_nodos(archivo: str, contenido: str, tipo_nodo: str = "todos") -> Optional[dict]:
    """Extrae funciones/clases/imports de ``contenido`` con tree-sitter.

    Devuelve un dict::

        {
          "lenguaje": "go", "motor": "tree-sitter",
          "funciones": [{"nombre", "linea", "inicio", "fin", "texto"}],
          "clases":    [{"nombre", "linea", "inicio", "fin", "texto"}],
          "imports":   [{"nombre", "linea"}],
        }

    o ``None`` si no hay backend/lenguaje/parseo (el editor cae entonces a
    parche/sobrescritura, como exige la cadena de estrategias).
    ``tipo_nodo`` filtra: "todos" | "funciones" | "clases" | "imports".
    """
    lenguaje = detectar_lenguaje_por_extension(archivo)
    if not lenguaje:
        return None
    arbol = parsear_archivo(contenido, lenguaje)
    if arbol is None:
        return None

    funciones: List[dict] = []
    clases: List[dict] = []
    imports: List[dict] = []
    pila: List = [arbol.root_node]
    while pila:
        nodo = pila.pop()
        tipo = nodo.type
        if tipo in _NODOS_FUNCION:
            funciones.append({
                "nombre": _nombre_definicion(nodo, contenido),
                "linea": nodo.start_point[0] + 1,
                "inicio": nodo.start_point[0] + 1,
                "fin": nodo.end_point[0] + 1,
                "texto": _texto_nodo(nodo),
            })
        elif tipo in _NODOS_CLASE or (
                tipo == _NODO_TYPE_SPEC
                and ("struct" in (nodo.text or b"").decode("utf-8", "replace")
                     or "interface" in (nodo.text or b"").decode(
                         "utf-8", "replace"))):
            clases.append({
                "nombre": _nombre_definicion(nodo, contenido),
                "linea": nodo.start_point[0] + 1,
                "inicio": nodo.start_point[0] + 1,
                "fin": nodo.end_point[0] + 1,
                "texto": _texto_nodo(nodo),
            })
        elif tipo in ("import_statement", "import_declaration",
                      "import_spec", "use_declaration", "package_clause",
                      "using_declaration", "include_statement",
                      "require", "import_from_statement"):
            imports.append({
                "nombre": _texto_nodo(nodo),
                "linea": nodo.start_point[0] + 1,
            })
        for hijo in reversed(nodo.children):
            pila.append(hijo)

    resultado = {"lenguaje": lenguaje, "motor": "tree-sitter"}
    if tipo_nodo in ("todos", "funciones"):
        resultado["funciones"] = funciones
    if tipo_nodo in ("todos", "clases"):
        resultado["clases"] = clases
    if tipo_nodo in ("todos", "imports"):
        resultado["imports"] = imports
    return resultado


def resumen_archivo(archivo: str, contenido: str) -> Optional[dict]:
    """Resumen compatible con ``_resumen_ast_python`` usando tree-sitter.

    Devuelve el mismo formato de claves (``ok``, ``motor``, ``lenguaje``,
    ``funciones``, ``clases``, ``imports``, ``variables``, ``error``) o
    ``None`` si tree-sitter no puede procesar el archivo.
    """
    nodos = extraer_nodos(archivo, contenido)
    if nodos is None:
        return None
    return {
        "ok": True, "motor": "tree-sitter",
        "lenguaje": nodos.get("lenguaje"),
        "funciones": nodos.get("funciones", []),
        "clases": nodos.get("clases", []),
        "imports": nodos.get("imports", []),
        "variables": [], "error": None,
    }


def extraer_bloques(archivo: str, contenido: str) -> List[dict]:
    """Bloques de primer nivel (funciones/clases) para contexto selectivo.

    Formato idéntico a ``_extraer_bloques_ast``: dicts ``{"tipo", "nombre",
    "inicio", "fin"}`` con líneas 1-based inclusivas. [] si no se puede.
    """
    nodos = extraer_nodos(archivo, contenido)
    if nodos is None:
        return []
    bloques: List[dict] = []
    for tipo, clave in (("funcion", "funciones"), ("clase", "clases")):
        for simbolo in nodos.get(clave, []):
            bloques.append({
                "tipo": tipo.title(),
                "nombre": simbolo["nombre"],
                "inicio": simbolo["inicio"],
                "fin": simbolo["fin"],
            })
    bloques.sort(key=lambda b: (b["inicio"], b["fin"]))
    return bloques


# ---------------------------------------------------------------------------
# Parches sobre el AST (reemplazo seguro por byte-span)
# ---------------------------------------------------------------------------
def aplicar_parche_arbol(contenido: str, nodo_viejo: str, nodo_nuevo: str,
                         archivo: Optional[str] = None) -> Optional[str]:
    """Reemplaza ``nodo_viejo`` (código exacto de un nodo del AST) por
    ``nodo_nuevo`` en ``contenido``, validando ambos con tree-sitter.

    Devuelve el nuevo contenido, o ``None`` si:
    - el lenguaje/backend no está disponible,
    - ``nodo_viejo`` no corresponde exactamente a un nodo del árbol,
    - ``nodo_nuevo`` no parsea (operación no válida → transacción segura).
    """
    lenguaje = detectar_lenguaje_por_extension(archivo or "") or \
        detectar_lenguaje(contenido)
    if not lenguaje:
        return None
    arbol = parsear_archivo(contenido, lenguaje)
    if arbol is None:
        return None
    objetivo = nodo_viejo.encode("utf-8")
    reemplazo = nodo_nuevo.encode("utf-8")

    # El código debe coincidir EXACTAMENTE con el span de un nodo del árbol.
    destino = None
    pila: List = [arbol.root_node]
    while pila:
        actual = pila.pop()
        if bytes(actual.text or b"") == objetivo:
            destino = actual
            break                      # el nodo más profundo/anidado primero
        for hijo in reversed(actual.children):
            pila.append(hijo)
    if destino is None:
        return None

    # El nuevo código debe ser sintácticamente válido en el lenguaje.
    arbol_nuevo = parsear_archivo(nodo_nuevo, lenguaje)
    if arbol_nuevo is None or arbol_nuevo.root_node.has_error:
        return None

    inicio, fin = destino.start_byte, destino.end_byte
    datos = contenido.encode("utf-8")
    nuevo = datos[:inicio] + reemplazo + datos[fin:]
    try:
        return nuevo.decode("utf-8")
    except UnicodeDecodeError:
        return None


def validar_sintaxis(archivo: str, contenido: str) -> Optional[bool]:
    """Valida ``contenido`` con tree-sitter para su lenguaje.

    Devuelve ``True`` (válido), ``False`` (errores de sintaxis) o ``None``
    si no hay backend/lenguaje (no se puede validar → no bloquear).
    """
    lenguaje = detectar_lenguaje_por_extension(archivo)
    if not lenguaje:
        return None
    arbol = parsear_archivo(contenido, lenguaje)
    if arbol is None:
        return None
    return not arbol.root_node.has_error