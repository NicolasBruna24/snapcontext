#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Manejo de contexto inteligente de SnapContext (v6.1.0).

Los modelos de IA (sobre todo los locales como ``deepseek-r1:14b`` o
``llama3.2``) tienen ventanas de contexto limitadas (a menudo 4096 tokens).
Enviar un archivo completo a un modelo de 4096 tokens provoca fallos
constantes. Este módulo resuelve el problema en tres pasos:

1. :func:`estimar_tokens` — estima los tokens de un texto (``tiktoken``
   si está disponible; si no, la regla práctica 1 token ≈ 4 caracteres).
2. :func:`extraer_bloques_relevantes` — extrae funciones/clases usando
   tree-sitter (vía ``parser_universal``) cuando el lenguaje está soportado
   y, en caso contrario, cae a regex básico.
3. :func:`seleccionar_contexto` — si el archivo supera ``max_tokens``,
   devuelve un fragmento con el resumen AST + los bloques más relevantes
   (priorizando el bloque objetivo) en lugar del archivo completo.

Opcional y compatible hacia atrás: si no se usa, el editor funciona igual.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

# Umbral por defecto (tokens) a partir del cual se usa contexto selectivo.
MAX_CONTEXT_TOKENS = 3000

# ---------------------------------------------------------------------------
# Estimación de tokens
# ---------------------------------------------------------------------------
_tiktoken_enc = None
_tiktoken_buscado = False


def _cargar_tiktoken():
    """Devuelve el codificador de tiktoken (o ``None``). Memoizado."""
    global _tiktoken_enc, _tiktoken_buscado
    if _tiktoken_buscado:
        return _tiktoken_enc
    _tiktoken_buscado = True
    try:
        import tiktoken           # noqa: E402  (dependencia opcional)
        try:
            _tiktoken_enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
        except Exception:          # noqa: BLE001
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
    except Exception:              # noqa: BLE001  (no instalado → aproximación)
        _tiktoken_enc = None
    return _tiktoken_enc


def estimar_tokens(texto: str, modelo: str = "gpt-3.5-turbo") -> int:
    """Estima los tokens de ``texto`` para el modelo dado (v6.1.0).

    Usa ``tiktoken`` si está disponible; si no, aproxima con la regla
    práctica 1 token ≈ 4 caracteres. El parámetro ``modelo`` no se usa de
    forma efectiva (se conserva solo para mantener la firma).
    """
    if not texto:
        return 0
    enc = _cargar_tiktoken()
    if enc is not None:
        try:
            return len(enc.encode(texto))
        except Exception:          # noqa: BLE001  (textos raros, emojis…)
            pass
    return max(1, int(len(texto) / 4))


def estimar_tokens_de_archivo(ruta: str) -> int:
    """Estima los tokens del archivo ``ruta`` leyéndolo de disco.

    Devuelve 0 si el archivo no existe o no se puede leer.
    """
    try:
        texto = Path(ruta).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return estimar_tokens(texto)


# ---------------------------------------------------------------------------
# Extracción de bloques con tree-sitter (vía parser_universal)
# ---------------------------------------------------------------------------
_NODOS_FUNCION_TS = {"function_definition", "function_declaration",
                     "method_definition", "method_declaration",
                     "function_item", "function_signature_item",
                     "constructor_declaration", "generator_function_declaration"}
_NODOS_CLASE_TS = {"class_definition", "class_declaration",
                   "struct_item", "enum_item", "impl_item", "trait_item",
                   "interface_declaration"}


def _texto_nodo(nodo) -> str:
    try:
        return (nodo.text or b"").decode("utf-8", "replace")
    except Exception:
        return ""


def _nombre_nodo(nodo, contenido: str) -> str:
    for campo in ("name", "declarator", "identifier"):
        hijo = (nodo.child_by_field_name(campo)
                if hasattr(nodo, "child_by_field_name") else None)
        if hijo is not None:
            texto = _texto_nodo(hijo).strip()
            if texto:
                return (texto.split("(")[0].split("{")[0]
                        .strip().rstrip("=").strip() or texto)
    return "(anonimo)"


def _metadatos_tree_sitter(contenido: str, lenguaje: str) -> Optional[List[dict]]:
    """Bloques con tree-sitter vía ``parser_universal``.

    Devuelve ``None`` si el backend/lenguaje no está disponible; si no, la
    lista de dicts ``{"nombre", "tipo", "inicio", "fin"}`` (1-based).
    """
    try:
        import parser_universal as pu
        arbol = pu.parsear_archivo(contenido, lenguaje)
    except Exception:
        return None
    if arbol is None:
        return None
    metadatos: List[dict] = []
    pila: List = [arbol.root_node]
    while pila:
        nodo = pila.pop()
        tipo = nodo.type
        if tipo in _NODOS_FUNCION_TS:
            metadatos.append({
                "nombre": _nombre_nodo(nodo, contenido),
                "tipo": "funcion",
                "inicio": nodo.start_point[0] + 1,
                "fin": nodo.end_point[0] + 1,
            })
        elif tipo in _NODOS_CLASE_TS:
            metadatos.append({
                "nombre": _nombre_nodo(nodo, contenido),
                "tipo": "clase",
                "inicio": nodo.start_point[0] + 1,
                "fin": nodo.end_point[0] + 1,
            })
        elif tipo == "type_spec":
            texto = _texto_nodo(nodo)
            if "struct" in texto or "interface" in texto:
                metadatos.append({
                    "nombre": _nombre_nodo(nodo, contenido),
                    "tipo": "clase",
                    "inicio": nodo.start_point[0] + 1,
                    "fin": nodo.end_point[0] + 1,
                })
        for hijo in reversed(nodo.children):
            pila.append(hijo)
    metadatos.sort(key=lambda m: (m["inicio"], m["fin"]))
    return metadatos


# ---------------------------------------------------------------------------
# Extracción de bloques con ast de la stdlib (Python, sin tree-sitter)
# ---------------------------------------------------------------------------
def _metadatos_ast(contenido: str) -> Optional[List[dict]]:
    """Bloques de primer nivel de Python usando el ``ast`` de la stdlib.

    Devuelve ``None`` si el contenido no es Python válido (para que el
    llamador pueda probar regex); si no, la lista de metadatos 1-based.
    """
    import ast
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return None
    total = len(contenido.splitlines())
    metadatos: List[dict] = []
    for nodo in arbol.body:
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            inicio = nodo.lineno
            if getattr(nodo, "decorator_list", None):
                inicio = min(getattr(d, "lineno", inicio)
                             for d in nodo.decorator_list)
            fin = getattr(nodo, "end_lineno", nodo.lineno) or nodo.lineno
            metadatos.append({
                "nombre": nodo.name,
                "tipo": "clase" if isinstance(nodo, ast.ClassDef)
                else "funcion",
                "inicio": max(inicio, 1),
                "fin": min(fin, total),
            })
    return metadatos


# ---------------------------------------------------------------------------
# Fallback regex (lenguajes sin tree-sitter / backend no disponible)
# ---------------------------------------------------------------------------
_REGEX_FUNCIONES = {
    "python": re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)"),
    "javascript": re.compile(
        r"^(?:export\s+)?(?:async\s+)?function(?:\s+\*)?\s+([A-Za-z_$]\w*)"),
    "typescript": re.compile(
        r"^(?:export\s+)?(?:async\s+)?function(?:\s+\*)?\s+([A-Za-z_$]\w*)"),
    "tsx": re.compile(
        r"^(?:export\s+)?(?:async\s+)?function(?:\s+\*)?\s+([A-Za-z_$]\w*)"),
    "go": re.compile(r"^func(?:\s+\([^)]*\))?\s+([A-Za-z_]\w*)"),
    "rust": re.compile(
        r"^(?:pub(?:\s*\([^)]*\))?\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)"),
    "java": re.compile(
        r"^\s*(?:public|private|protected)?\s*(?:abstract\s+)?"
        r"(?:static\s+)?(?:final\s+)?[\w<>\[\],. ]+\s+([A-Za-z_]\w*)\s*\("),
    "c": re.compile(
        r"^\s*(?:(?:static\s+|const\s+|inline\s+|extern\s+)*)"
        r"[\w*]+[\w*\s]+([A-Za-z_]\w*)\s*\("),
    "cpp": re.compile(
        r"^\s*(?:(?:static\s+|const\s+|inline\s+|virtual\s+)*)"
        r"[\w*]+[\w*\s]+([A-Za-z_]\w*)\s*\("),
    "c_sharp": re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?"
        r"(?:async\s+)?[\w<>\[\],.? ]+\s+([A-Za-z_]\w*)\s*\("),
    "ruby": re.compile(r"^\s*(?:def\s+self\.|def\s+)([A-Za-z_]\w*)"),
    "php": re.compile(
        r"^\s*(?:public|private|protected)?\s*(?:static\s+)?"
        r"function\s+([A-Za-z_]\w*)\s*\("),
    "kotlin": re.compile(r"^\s*fun\s+([A-Za-z_]\w*)\s*\("),
    "swift": re.compile(r"^\s*func\s+([A-Za-z_]\w*)\s*\("),
    "bash": re.compile(r"^\s*([A-Za-z_]\w*)\s*\(\)\s*\{?"),
    "lua": re.compile(r"^\s*function\s+[A-Za-z_.]+\s*\(|"
                      r"^\s*local\s+function\s+[A-Za-z_.]+\s*\("),
    "elixir": re.compile(r"^\s*defp?\s+[A-Za-z_]\w*\s*\("),
}
_REGEX_FUNCION_GENERICA = re.compile(
    r"^(?:(?:async\s+|export\s+)?(?:def|function|func|fn)\s+)"
    r"([A-Za-z_$]\w*)")


_REGEX_CLASES = {
    "python": re.compile(r"^class\s+([A-Za-z_]\w*)"),
    "javascript": re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$]\w*)"),
    "typescript": re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$]\w*)"),
    "tsx": re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$]\w*)"),
    "go": re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"),
    "rust": re.compile(
        r"^(?:pub(?:\s*\([^)]*\))?\s+)?"
        r"(?:struct|enum|trait|impl(?:<[^>]*>)?)\s+([A-Za-z_]\w*)"),
    "java": re.compile(
        r"^\s*(?:public|private|protected)?\s*(?:abstract\s+)?"
        r"(?:final\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)"),
    "dart": re.compile(r"^\s*(?:abstract\s+)?class\s+([A-Za-z_]\w*)"),
    "kotlin": re.compile(
        r"^\s*(?:data\s+|sealed\s+|open\s+|abstract\s+)?"
        r"(?:class|interface|enum)\s+([A-Za-z_]\w*)"),
    "c_sharp": re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*"
        r"(?:abstract\s+)?(?:sealed\s+)?class\s+([A-Za-z_]\w*)"),
    "ruby": re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),
    "php": re.compile(
        r"^\s*(?:abstract\s+|final\s+)?(?:class|interface)\s+([A-Za-z_]\w*)"),
    "swift": re.compile(
        r"^\s*(?:private\s+|public\s+|internal\s+|open\s+)?"
        r"class\s+([A-Za-z_]\w*)"),
    "c": re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_]\w*)"),
    "cpp": re.compile(
        r"^\s*(?:class\s+|struct\s+|enum\s+|union\s+)([A-Za-z_]\w*)"),
    "elixir": re.compile(r"^\s*defmodule\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"),
}
_REGEX_CLASE_GENERICA = re.compile(
    r"^(?:(?:export\s+|abstract\s+|final\s+)?(?:class|interface)|"
    r"(?:struct|enum))\s+([A-Za-z_$]\w*)")


def _regex_para(lenguaje: str) -> Tuple[Optional[re.Pattern], Optional[re.Pattern]]:
    """Devuelve las regex (función, clase) para ``lenguaje``."""
    clave = (lenguaje or "").strip().lower()
    return (_REGEX_FUNCIONES.get(clave) or _REGEX_FUNCION_GENERICA,
            _REGEX_CLASES.get(clave) or _REGEX_CLASE_GENERICA)


def _metadatos_regex(contenido: str, lenguaje: str) -> List[dict]:
    """Extrae bloques con regex básico (fallback sin tree-sitter/ast)."""
    f_re, c_re = _regex_para(lenguaje)
    lineas = contenido.splitlines()
    hits: List[tuple] = []            # (indice, indent, tipo, nombre)
    for i, linea in enumerate(lineas):
        indent = len(linea) - len(linea.lstrip(" \t"))
        st = linea.strip()
        if not st:
            continue
        tipo, nombre = None, None
        m = f_re.match(st) if f_re else None
        if m:
            tipo, nombre = "funcion", m.group(1)
        else:
            m2 = c_re.match(st) if c_re else None
            if m2:
                tipo, nombre = "clase", m2.group(1)
        if tipo is not None:
            hits.append((i, indent, tipo, nombre))
    if not hits:
        return []
    # Solo definiciones de primer nivel (indent mínimo) para evitar duplicar
    # bloques anidados (métodos dentro de clases, etc.).
    indent_min = min(h[1] for h in hits)
    if indent_min == 0:
        hits = [h for h in hits if h[1] == 0]
    total = len(lineas)
    metadatos: List[dict] = []
    for k, (i, _ind, tipo, nombre) in enumerate(hits):
        fin = hits[k + 1][0] - 1 if k + 1 < len(hits) else total - 1
        while fin > i and not lineas[fin].strip():
            fin -= 1
        metadatos.append({"nombre": nombre, "tipo": tipo,
                          "inicio": i + 1, "fin": fin + 1})
    return metadatos


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def _extraer_metadatos(contenido: str, lenguaje: str) -> List[dict]:
    """Lista de bloques ``{"nombre", "tipo", "inicio", "fin"}`` (best-effort).

    Prioridad: tree-sitter (vía ``parser_universal``) → ``ast`` de la stdlib
    (Python) → regex básico. Nunca lanza excepciones.
    """
    try:
        ts = _metadatos_tree_sitter(contenido, lenguaje)
        if ts is not None:
            return ts
    except Exception:
        pass
    if (lenguaje or "").strip().lower() == "python":
        try:
            ast_metas = _metadatos_ast(contenido)
            if ast_metas is not None:
                return ast_metas
        except Exception:
            pass
    try:
        return _metadatos_regex(contenido, lenguaje)
    except Exception:
        return []


def _texto_resumen(contenido: str, lenguaje: str,
                   metadatos: List[dict]) -> str:
    """Texto resumen (nombres de funciones/clases) para el prompt."""
    lineas = contenido.splitlines() if contenido else []
    funciones = [m["nombre"] for m in metadatos if m["tipo"] == "funcion"]
    clases = [m["nombre"] for m in metadatos if m["tipo"] == "clase"]
    partes = [
        "[RESUMEN DEL ARCHIVO (AST)]:",
        f"(lenguaje: {lenguaje or '?'}, {len(lineas)} líneas)",
    ]
    if clases:
        partes.append(f"Clases: {', '.join(clases[:80])}")
    if funciones:
        partes.append(f"Funciones: {', '.join(funciones[:80])}")
    if not funciones and not clases:
        partes.append("(sin funciones/clases detectadas)")
    return "\n".join(partes)


def extraer_bloques_relevantes(contenido: str, lenguaje: str,
                               objetivo: str = None) -> Tuple[str, List[str]]:
    """Extrae funciones/clases de ``contenido`` (v6.1.0).

    Devuelve ``(resumen_ast, bloques)``:

    - ``resumen_ast`` : texto con los nombres de funciones/clases detectadas.
    - ``bloques``     : lista de fragmentos de código de cada función/clase.

    Usa tree-sitter (vía ``parser_universal``) si el lenguaje está soportado;
    si no, ``ast`` (Python) o regex básico. Si se pasa ``objetivo`` (nombre
    de la función/clase a editar), ese bloque se prioriza y se coloca el
    primero de la lista.
    """
    metadatos = _extraer_metadatos(contenido, lenguaje)
    resumen = _texto_resumen(contenido, lenguaje, metadatos)
    if objetivo and metadatos:
        indice = next((i for i, m in enumerate(metadatos)
                       if m["nombre"] == objetivo), None)
        if indice is not None:
            metadatos = [metadatos.pop(indice)] + metadatos
    lineas = contenido.splitlines() if contenido else []
    bloques = ["\n".join(lineas[m["inicio"] - 1:m["fin"]])
               for m in metadatos]
    return resumen, bloques


def _seleccionar_bloques(metadatos: List[dict], objetivo: Optional[str],
                         lineas: List[str], presupuesto: int) -> List[dict]:
    """Elige los bloques que caben en ``presupuesto`` (v6.1.0).

    El bloque ``objetivo`` (si se encuentra) siempre se incluye completo y el
    primero. Después se añaden los más relevantes: los más cercanos al
    objetivo (por distancia de líneas) o, si no hay objetivo, los más grandes
    primero.
    """
    def costo(m: dict) -> int:
        inicio, fin = m["inicio"], m["fin"]
        if inicio > fin:
            inicio, fin = fin, inicio
        return estimar_tokens("\n".join(lineas[max(inicio - 1, 0):fin]))

    elegidos: List[dict] = []
    indice = next((i for i, m in enumerate(metadatos)
                   if m["nombre"] == objetivo), None)
    if indice is not None:
        elegidos.append(metadatos[indice])
        presupuesto -= costo(metadatos[indice])
    resto = [m for i, m in enumerate(metadatos) if i != indice]
    if indice is not None:
        # Más relevantes = más cercanos al objetivo.
        ancla = metadatos[indice]["inicio"]
        resto.sort(key=lambda m: min(abs(m["inicio"] - ancla),
                                     abs(m["fin"] - ancla)))
    else:
        # Sin objetivo: los bloques más grandes primero.
        resto.sort(key=lambda m: -((m["fin"] - m["inicio"])))
    seleccionados: List[dict] = []
    for m in resto:
        if presupuesto <= 0:
            break
        c = costo(m)
        if c > presupuesto:
            continue          # bloque demasiado grande para el presupuesto
        seleccionados.append(m)
        presupuesto -= c
    return elegidos + seleccionados


def seleccionar_contexto(contenido: str, lenguaje: str, objetivo: str = None,
                         max_tokens: int = MAX_CONTEXT_TOKENS) -> str:
    """Selecciona el contexto a enviar al modelo (v6.1.0).

    - Si ocupa ≤ ``max_tokens``, devuelve el contenido completo.
    - Si no, extrae los bloques y devuelve un fragmento con el resumen AST,
      el bloque ``objetivo`` (si existe) y los bloques más relevantes hasta
      llenar ``max_tokens``.
    """
    if not contenido:
        return contenido
    if estimar_tokens(contenido) <= max_tokens:
        return contenido
    metadatos = _extraer_metadatos(contenido, lenguaje)
    resumen = _texto_resumen(contenido, lenguaje, metadatos)
    lineas = contenido.splitlines() if contenido else []
    total = len(lineas)

    partes = [resumen, "", "[CÓDIGO RELEVANTE A EDITAR]:"]
    if not metadatos:
        # Último recurso: cabecera del archivo dentro del presupuesto.
        partes.append("# (sin funciones/clases detectadas; cabecera del archivo)")
        presupuesto = max_tokens - estimar_tokens("\n".join(partes))
        usadas = 0
        cabecera: List[str] = []
        for linea in lineas:
            t = estimar_tokens(linea) + 1
            if usadas + t > presupuesto:
                break
            cabecera.append(linea)
            usadas += t
        partes.extend(cabecera or lineas[:min(40, total)])
    else:
        presupuesto = max_tokens - estimar_tokens("\n".join(partes))
        for m in _seleccionar_bloques(metadatos, objetivo, lineas,
                                      presupuesto):
            inicio, fin = m["inicio"], m["fin"]
            if inicio > fin:
                inicio, fin = fin, inicio
            inicio = max(inicio, 1)
            fin = min(fin, total)
            partes.append(
                f"# ── {m['tipo'].title()} {m['nombre']} "
                f"(líneas {inicio}-{fin} de {total}) ──")
            partes.extend(lineas[inicio - 1:fin])

    partes.append("")
    partes.append(
        "[RESTRICCIÓN]: El resto del archivo no se muestra por límites de "
        "contexto. Genera el parche/código SOLO para el bloque mostrado. En "
        "modo <<<ANTES>>>/<<<DESPUES>>>, ANTES debe contener EXCLUSIVAMENTE "
        "el código del bloque, sin los comentarios ── de cabecera. NO "
        "reescribas el archivo completo.")
    return "\n".join(partes)


def objetivo_en_mensaje(contenido: str, lenguaje: str,
                        mensaje: str) -> Optional[str]:
    """Devuelve el nombre del primer bloque mencionado en ``mensaje``.

    Útil para que el editor priorice el bloque correcto cuando la tarea hace
    referencia a una función/clase concreta. ``None`` si ningún bloque
    coincide con el mensaje.
    """
    if not mensaje:
        return None
    metadatos = _extraer_metadatos(contenido, lenguaje)
    texto = (mensaje or "").lower()
    for m in metadatos:
        if m["nombre"] and m["nombre"].lower() in texto:
            return m["nombre"]
    return None


def es_error_contexto(exc: Exception) -> bool:
    """¿El error del proveedor parece de límite de contexto? (v6.1.0)

    Los SDK de proveedores (Anthropic ``exceed_context_size_error``, OpenAI
    ``maximum context length``, Ollama/DeepSeek ``context length exceeded``,
    Groq ``token limit``, …) describen el fallo por contexto en el mensaje de
    la excepción. Con esto el editor sabe que debe reintentar con el archivo
    completo (el modelo real puede tener más contexto que la estimación).
    """
    texto = f"{exc}".lower()
    claves = (
        "exceed_context_size_error",      # Anthropic
        "context length",                 # Ollama / DeepSeek / OpenAI
        "maximum context",                # OpenAI / Anthropic
        "context_window", "context window",   # Gemini
        "context_size", "context size",
        "contexto",                       # errores locales en español
        "too many tokens", "token limit", "input token",
        "max context", "longitud",
    )
    return any(c in texto for c in claves)