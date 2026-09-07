#!/usr/bin/env python3
"""Análisis estático del asesor de SnapContext (seguridad y calidad).

Módulo extraído de ``snapcontext.py`` (v6.34.5, refactor fase 1). Contiene las
heurísticas puras de análisis (sin I/O de presentación):

* Detectores de calidad: funciones largas, clases grandes, nombres cortos,
  patrones obsoletos, código duplicado.
* Detectores de seguridad (v4.2.0): inyección de comandos/SQL, ``eval``/``exec``,
  path traversal, XSS, secretos embebidos.
* Detectores de rendimiento (v4.2.0): bucles anidados O(n²), concatenación en
  bucle, ``range(len(...))``, N+1, etc.
* Orquestadores: :func:`_asesor_analizar` (completo) y los envoltorios
  :func:`_analizar_seguridad` / :func:`_analizar_rendimiento`.

Las funciones de presentación (``_asesor_mostrar``), auto-aplicación
(``_asesor_aplicar_automaticas``) y el entrypoint del CLI
(``_ejecutar_asesor``) permanecen en ``snapcontext.py``.
"""

import ast
import os
import re
from pathlib import Path

ASESOR_EXTENSIONES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".dart": "dart",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}
ASESOR_CARPETAS_IGNORADAS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".pytest_cache",
}
ASESOR_UMBRALES_DEFECTO = {
    "funcion_larga": 20,  # máx. líneas por función
    "clase_metodos": 10,  # máx. métodos por clase
    "duplicado_lineas": 6,  # tamaño mínimo de un bloque duplicado
}

# Nombres cortos legítimos (índices de bucle, coordenadas...) que el detector
# de nombres poco descriptivos ignora.
_NOMBRES_CORTOS_VALIDOS = {
    "i",
    "j",
    "k",
    "x",
    "y",
    "z",
    "_",
    "ok",
    "id",
    "ex",
    "ax",
    "ay",
    "bx",
    "by",
}

# Diccionario de nombres descriptivos propuestos para abreviaturas comunes
# (usado solo como sugerencia; el usuario puede rechazarla).
_NOMBRES_SUGERIDOS = {
    "d": "datos",
    "n": "numero",
    "s": "texto",
    "t": "temporal",
    "f": "archivo",
    "e": "error",
    "m": "mensaje",
    "r": "resultado",
    "l": "lista",
    "p": "parametro",
    "c": "contador",
    "v": "valor",
    "b": "bandera",
    "w": "ruta",
    "q": "cola",
    "g": "grafo",
    "h": "diccionario",
    "df": "dataframe",
    "fn": "funcion",
    "cb": "callback",
    "tmp": "temporal",
}

_PRIORIDAD_ORDEN = {"alta": 0, "media": 1, "baja": 2}


def _asesor_umbrales() -> dict:
    """Umbrales del asesor: defectos sobrescritos por ``~/.snapcontext/
    config.json`` bajo la clave ``"asesor"`` (p. ej. ``{"funcion_larga": 30}``)."""
    umbrales = dict(ASESOR_UMBRALES_DEFECTO)
    try:
        # import diferido: evitar dependencia circular con snapcontext
        from snapcontext import cargar_configuracion

        config = cargar_configuracion()
        personal = config.get("asesor")
        if isinstance(personal, dict):
            for clave, valor in personal.items():
                if clave in umbrales and isinstance(valor, int):
                    umbrales[clave] = valor
    except Exception:
        pass
    return umbrales


def _detectar_funciones_largas(contenido: str, umbral: int) -> list[dict]:
    """Funciones/métodos con más de ``umbral`` líneas (AST de Python)."""
    hallazgos: list[dict] = []
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return hallazgos
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fin = getattr(nodo, "end_lineno", nodo.lineno) or nodo.lineno
            lineas = fin - nodo.lineno + 1
            if lineas > umbral:
                hallazgos.append({"nombre": nodo.name, "linea": nodo.lineno, "lineas": lineas})
    return hallazgos


def _detectar_clases_grandes(contenido: str, max_metodos: int) -> list[dict]:
    """Clases con demasiadas responsabilidades (> ``max_metodos`` métodos)."""
    hallazgos: list[dict] = []
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return hallazgos
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ClassDef):
            metodos = sum(
                1 for hijo in nodo.body if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            if metodos > max_metodos:
                hallazgos.append({"nombre": nodo.name, "linea": nodo.lineno, "metodos": metodos})
    return hallazgos


def _detectar_nombres_cortos(contenido: str) -> list[dict]:
    """Variables/funciones con nombres poco descriptivos (≤ 2 caracteres)."""
    hallazgos: list[dict] = []
    try:
        arbol = ast.parse(contenido)
    except SyntaxError:
        return hallazgos
    vistos: dict[str, int] = {}
    for nodo in ast.walk(arbol):
        nombre = None
        linea = getattr(nodo, "lineno", 1)
        if isinstance(nodo, ast.Name) and isinstance(nodo.ctx, ast.Store):
            nombre = nodo.id
        elif isinstance(nodo, ast.arg):
            nombre = nodo.arg
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nombre = nodo.name
        if not nombre or nombre in _NOMBRES_CORTOS_VALIDOS:
            continue
        if len(nombre) <= 2 and nombre not in vistos:
            vistos[nombre] = linea
    for nombre, linea in sorted(vistos.items(), key=lambda kv: kv[1]):
        sugerido = _NOMBRES_SUGERIDOS.get(nombre.lower(), f"{nombre}_descriptivo")
        hallazgos.append({"nombre": nombre, "linea": linea, "sugerido": sugerido})
    return hallazgos


_PATRONES_OBSOLETOS = [
    (
        re.compile(r"^\s*except\s*:\s*(#.*)?$"),
        "'except:' desnudo captura todo; especifica la excepción (p. ej. 'except ValueError:')",
    ),
    (re.compile(r"==\s*None\b"), "usa 'is None' en lugar de '== None'"),
    (re.compile(r"\bNone\s*=="), "usa 'is None' en lugar de 'None =='"),
    (re.compile(r"\.has_key\("), "'.has_key()' es de Python 2; usa 'in'"),
]


def _detectar_patrones_obsoletos(contenido: str) -> list[dict]:
    """Líneas con patrones obsoletos o antipatrones (heurística por regex)."""
    hallazgos: list[dict] = []
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        codigo = linea.split("#", 1)[0]  # ignora comentarios
        for patron, mensaje in _PATRONES_OBSOLETOS:
            if patron.search(codigo):
                hallazgos.append({"linea": numero, "mensaje": mensaje, "codigo": codigo.strip()})
                break
    return hallazgos


def _normalizar_linea_duplicado(linea: str) -> str:
    """Normaliza una línea para comparación de bloques duplicados."""
    return " ".join(linea.strip().split())


def _detectar_duplicados(contenidos: dict[str, str], min_lineas: int) -> list[dict]:
    """Bloques de ``min_lineas`` líneas normalizadas repetidos entre archivos.

    Heurística por ventanas deslizantes: dos bloques son duplicados si todas
    sus líneas normalizadas coinciden. Devuelve como máximo una sugerencia por
    par de archivos (limitada a 20 para no saturar la salida).
    """
    huellas: dict[str, tuple] = {}
    hallazgos: list[dict] = []
    vistos_par: set = set()
    for archivo in sorted(contenidos):
        lineas = [_normalizar_linea_duplicado(x) for x in contenidos[archivo].splitlines()]
        for inicio in range(0, max(0, len(lineas) - min_lineas + 1)):
            bloque = lineas[inicio : inicio + min_lineas]
            if any(not x for x in bloque):
                continue
            clave = "\n".join(bloque)
            previo = huellas.get(clave)
            if previo is None:
                huellas[clave] = (archivo, inicio + 1)
                continue
            par = (previo[0], archivo)
            if par in vistos_par:
                continue
            vistos_par.add(par)
            hallazgos.append(
                {
                    "archivo": archivo,
                    "linea": inicio + 1,
                    "original": f"{previo[0]}:{previo[1]}",
                    "lineas": min_lineas,
                }
            )
            if len(hallazgos) >= 20:
                return hallazgos
    return hallazgos


# ---------------------------------------------------------------------------
# Análisis de seguridad y rendimiento del asesor (v4.2.0)
# ---------------------------------------------------------------------------

# Patrones de vulnerabilidades comunes (regex sobre código sin comentarios).
_VULNERABILIDADES_PATRONES = [
    (
        re.compile(r"\bos\.system\s*\("),
        "Command injection: 'os.system' con entrada no sanitizada.",
        "Usa 'subprocess.run' con lista de argumentos y shell=False.",
        "alta",
    ),
    (
        re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
        "Command injection: 'subprocess' con shell=True permite inyección.",
        "Usa shell=False y pasa los argumentos como lista.",
        "alta",
    ),
    (
        re.compile(r"\beval\s*\("),
        "Uso inseguro de 'eval': ejecuta código dinámico arbitrario.",
        "Sustitúyelo por 'ast.literal_eval' o lógica explícita.",
        "alta",
    ),
    (
        re.compile(r"\bexec\s*\("),
        "Uso inseguro de 'exec': ejecuta código dinámico arbitrario.",
        "Evita 'exec'; refactoriza el código dinámico en funciones.",
        "alta",
    ),
    (
        re.compile(
            r"(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)[^\n]*"
            r"(\+|%|\bf\"|\.format\()",
            re.IGNORECASE,
        ),
        "Posible inyección SQL: consulta construida por concatenación.",
        "Usa consultas parametrizadas ('?' o '%s') u ORM.",
        "alta",
    ),
    (
        re.compile(r"open\s*\(\s*[^)]*\"\.\./"),
        "Posible path traversal: ruta con '../' construida dinámicamente.",
        "Valida y normaliza la ruta (resolve + comprobar base).",
        "alta",
    ),
    (
        re.compile(r"innerHTML\s*="),
        "Posible XSS: asignación directa a innerHTML.",
        "Usa textContent o sanea la entrada antes de insertarla.",
        "media",
    ),
    (
        re.compile(r"dangerouslySetInnerHTML"),
        "Posible XSS React: uso de dangerouslySetInnerHTML.",
        "Sanea el HTML (DOMPurify) o usa componentes seguros.",
        "alta",
    ),
]

# Nombres de variables que sugieren secretos embebidos.
_SECRETES_RE = re.compile(
    r"^\s*([A-Z0-9_]*(?:API_KEY|SECRET|PASSWORD|PASSWD|TOKEN|ACCESS_KEY)"
    r"[A-Z0-9_]*)\s*=\s*[\"']([^\"']{8,})[\"']",
    re.IGNORECASE,
)


def _detectar_vulnerabilidades(contenido: str, lenguaje: str = "") -> list[dict]:
    """Detecta vulnerabilidades comunes por heurísticas propias (v4.2.0).

    No requiere herramientas externas (bandit etc.); devuelve hallazgos con
    ``linea``, ``mensaje``, ``solucion`` y ``prioridad``.
    """
    hallazgos: list[dict] = []
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        codigo = linea.split("#", 1)[0]
        if not codigo.strip():
            continue
        for patron, mensaje, solucion, prioridad in _VULNERABILIDADES_PATRONES:
            if patron.search(codigo):
                hallazgos.append(
                    {
                        "linea": numero,
                        "mensaje": mensaje,
                        "solucion": solucion,
                        "prioridad": prioridad,
                    }
                )
        coincidencia = _SECRETES_RE.match(codigo)
        if coincidencia:
            hallazgos.append(
                {
                    "linea": numero,
                    "mensaje": f"Hardcoded secret en '{coincidencia.group(1)}'.",
                    "solucion": "Muévelo a una variable de entorno o gestor de "
                    "secretos; nunca al repositorio.",
                    "prioridad": "alta",
                }
            )
    return hallazgos


_RENDIMIENTO_PATRONES = [
    (
        re.compile(r"for\s+\w+\s+in\s+range\s*\(\s*len\s*\("),
        "'range(len(...))': patrón innecesario y propenso a recalcular.",
        "Itera directamente sobre la secuencia o usa enumerate().",
        "media",
    ),
    (
        re.compile(r"\.read\(\)\s*$"),
        "Lectura completa del archivo en memoria.",
        "Procesa línea a línea ('for linea in fichero') si es grande.",
        "media",
    ),
    (
        re.compile(r"\.objects\.get\s*\("),
        "Posible consulta N+1: acceso al ORM dentro de un bucle.",
        "Usa select_related/prefetch_related o una consulta por lotes.",
        "alta",
    ),
]


def _detectar_rendimiento(contenido: str, lenguaje: str = "") -> list[dict]:  # noqa: C901  (refactor de complejidad: Fase 10c)
    """Detecta problemas comunes de rendimiento por heurísticas (v4.2.0)."""
    hallazgos: list[dict] = []
    lineas_codigo = [
        (n, ln.split("#", 1)[0]) for n, ln in enumerate(contenido.splitlines(), start=1)
    ]

    for indice, (numero, codigo) in enumerate(lineas_codigo):
        if not codigo.strip():
            continue

        # Bucles anidados (O(n²)): un 'for' seguido de otro más indentado.
        coincide_for = re.match(r"^(\s*)for\s+", codigo)
        if coincide_for:
            sangria = len(coincide_for.group(1))
            for _, codigo2 in lineas_codigo[indice + 1 :]:
                if not codigo2.strip():
                    continue
                coincide2 = re.match(r"^(\s*)for\s+", codigo2)
                if coincide2:
                    if len(coincide2.group(1)) > sangria:
                        hallazgos.append(
                            {
                                "linea": numero,
                                "mensaje": "Bucles anidados: coste cuadrático O(n²).",
                                "solucion": "Considera sets/dicts para búsquedas "
                                "(O(1)) o reformula el algoritmo.",
                                "prioridad": "media",
                            }
                        )
                    break
                break

        # Concatenación de cadenas con '+=' dentro de un bucle cercano.
        if re.search(r"^\s*\w+\s*\+=\s*[\"']", codigo) and any(
            re.match(r"^\s*(for|while)\s+", c)
            for _, c in lineas_codigo[max(0, indice - 5) : indice]
        ):
            hallazgos.append(
                {
                    "linea": numero,
                    "mensaje": "Concatenación de cadenas con '+=' en bucle: copias repetidas.",
                    "solucion": "Acumula en una lista y usa ''.join(lista).",
                    "prioridad": "media",
                }
            )

        for patron, mensaje, solucion, prioridad in _RENDIMIENTO_PATRONES:
            if patron.search(codigo):
                hallazgos.append(
                    {
                        "linea": numero,
                        "mensaje": mensaje,
                        "solucion": solucion,
                        "prioridad": prioridad,
                    }
                )
    return hallazgos


def _asesor_analizar(  # noqa: C901  (refactor de complejidad: Fase 10c)
    directorio: str = ".",
    umbral_funcion: int | None = None,
    max_archivos: int = 400,
    profundo: bool = False,
) -> list[dict]:
    """Analiza el proyecto y devuelve sugerencias de mejora ordenadas.

    Cada sugerencia es un dict con ``descripcion``, ``archivo``, ``linea``,
    ``solucion``, ``prioridad`` (alta|media|baja) y, si se puede aplicar de
    forma segura, ``operaciones`` + ``auto=True``.

    Con ``profundo=True`` (v4.2.0, ``--asesor-profundo``) añade análisis de
    seguridad (🗝 tipos ``vulnerabilidad``) y rendimiento (⚡ tipo
    ``rendimiento``).
    """
    umbrales = _asesor_umbrales()
    if umbral_funcion:
        umbrales["funcion_larga"] = max(3, int(umbral_funcion))

    raiz = Path(directorio).resolve()
    contenidos: dict[str, str] = {}
    for camino in sorted(raiz.rglob("*")):
        if not camino.is_file() or camino.suffix not in ASESOR_EXTENSIONES:
            continue
        if any(parte in ASESOR_CARPETAS_IGNORADAS for parte in camino.parts):
            continue
        try:
            contenidos[str(camino.relative_to(raiz)).replace(os.sep, "/")] = camino.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        if len(contenidos) >= max_archivos:
            break

    sugerencias: list[dict] = []
    for relativo, contenido in sorted(contenidos.items()):
        lenguaje = ASESOR_EXTENSIONES[Path(relativo).suffix]

        # Patrones obsoletos: disponibles para todos los lenguajes (regex).
        for hallazgo in _detectar_patrones_obsoletos(contenido):
            auto = "is None" in hallazgo["mensaje"] and lenguaje == "python"
            sugerencias.append(
                {
                    "tipo": "patron_obsoleto",
                    "descripcion": f"Patrón obsoleto: {hallazgo['mensaje']}",
                    "archivo": relativo,
                    "linea": hallazgo["linea"],
                    "solucion": hallazgo["mensaje"],
                    "prioridad": "alta" if "except" in hallazgo["mensaje"] else "media",
                    "auto": auto,
                }
            )

        # v4.2.0: seguridad y rendimiento solo en modo profundo.
        if profundo:
            for hallazgo in _detectar_vulnerabilidades(contenido, lenguaje):
                sugerencias.append(
                    {
                        "tipo": "vulnerabilidad",
                        "descripcion": f"🔒 Vulnerabilidad: {hallazgo['mensaje']}",
                        "archivo": relativo,
                        "linea": hallazgo["linea"],
                        "solucion": hallazgo["solucion"],
                        "prioridad": hallazgo["prioridad"],
                    }
                )
            for hallazgo in _detectar_rendimiento(contenido, lenguaje):
                sugerencias.append(
                    {
                        "tipo": "rendimiento",
                        "descripcion": f"⚡ Rendimiento: {hallazgo['mensaje']}",
                        "archivo": relativo,
                        "linea": hallazgo["linea"],
                        "solucion": hallazgo["solucion"],
                        "prioridad": hallazgo["prioridad"],
                    }
                )

        if lenguaje != "python":
            continue  # AST detallado solo para Python; resto heurísticas.

        for hallazgo in _detectar_funciones_largas(contenido, umbrales["funcion_larga"]):
            sugerencias.append(
                {
                    "tipo": "funcion_larga",
                    "descripcion": (
                        f"La función '{hallazgo['nombre']}' tiene "
                        f"{hallazgo['lineas']} líneas (> {umbrales['funcion_larga']})."
                    ),
                    "archivo": relativo,
                    "linea": hallazgo["linea"],
                    "solucion": "Extrae bloques coherentes en funciones auxiliares.",
                    "prioridad": "media",
                }
            )

        for hallazgo in _detectar_clases_grandes(contenido, umbrales["clase_metodos"]):
            sugerencias.append(
                {
                    "tipo": "clase_grande",
                    "descripcion": (
                        f"La clase '{hallazgo['nombre']}' tiene "
                        f"{hallazgo['metodos']} métodos "
                        f"(> {umbrales['clase_metodos']}): posibles demasiadas "
                        "responsabilidades."
                    ),
                    "archivo": relativo,
                    "linea": hallazgo["linea"],
                    "solucion": (
                        "Divide la clase en clases más pequeñas con una responsabilidad única."
                    ),
                    "prioridad": "media",
                }
            )

        for hallazgo in _detectar_nombres_cortos(contenido):
            operaciones = [
                {"tipo": "renombrar", "nombre": hallazgo["nombre"], "nuevo": hallazgo["sugerido"]}
            ]
            sugerencias.append(
                {
                    "tipo": "nombre_poco_descriptivo",
                    "descripcion": (f"El nombre '{hallazgo['nombre']}' no es descriptivo."),
                    "archivo": relativo,
                    "linea": hallazgo["linea"],
                    "solucion": f"Renómbralo a algo como '{hallazgo['sugerido']}'.",
                    "prioridad": "baja",
                    "operaciones": operaciones,
                    "auto": True,
                }
            )

    min_dup = umbrales["duplicado_lineas"]
    for hallazgo in _detectar_duplicados(contenidos, min_dup):
        sugerencias.append(
            {
                "tipo": "codigo_duplicado",
                "descripcion": (
                    f"Bloque duplicado de {hallazgo['lineas']} líneas "
                    f"(original en {hallazgo['original']})."
                ),
                "archivo": hallazgo["archivo"],
                "linea": hallazgo["linea"],
                "solucion": "Extrae el bloque común a una función compartida.",
                "prioridad": "media",
            }
        )

    sugerencias.sort(
        key=lambda s: (_PRIORIDAD_ORDEN.get(s["prioridad"], 3), s["archivo"], s["linea"])
    )
    return sugerencias


def _asesor_analizar_por_tipo(directorio: str, tipos: tuple) -> list[dict]:
    """Ejecuta el análisis profundo y devuelve solo los ``tipos`` pedidos."""
    return [s for s in _asesor_analizar(directorio, profundo=True) if s.get("tipo") in tipos]


def _analizar_seguridad(directorio: str = ".") -> list[dict]:
    """Análisis de seguridad del proyecto (🗝 tipo 'vulnerabilidad')."""
    return _asesor_analizar_por_tipo(directorio, ("vulnerabilidad",))


def _analizar_rendimiento(directorio: str = ".") -> list[dict]:
    """Análisis de rendimiento del proyecto (⚡ tipo 'rendimiento')."""
    return _asesor_analizar_por_tipo(directorio, ("rendimiento",))
