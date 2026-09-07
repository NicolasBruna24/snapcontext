#!/usr/bin/env python3
"""Herramientas MCP (Model Context Protocol) — dispatcher y registro.

Extraído de ``snapcontext.py`` (Fase 8 de la fragmentación del monolito).

Este módulo centraliza:
    - El catálogo de herramientas predefinidas (``HERRAMIENTAS_PREDEFINIDAS``).
    - La carga de herramientas de usuario (``mcp_tools.json``) y de plugins.
    - El dispatcher que valida permisos y ejecuta la herramienta
      (``_ejecutar_herramienta_mcp``).
    - El formateo de resultados (``_formatear_resultado_mcp``) y el contexto
      automático (``_contexto_automatico_mcp``).

Las implementaciones concretas de cada herramienta (``_tool_*``) siguen en
``snapcontext.py``; se acceden con import diferido (lazy) para evitar ciclos
de importación. Igualmente ``_hooks`` y ``_plugins_herramientas`` se leen de
``snapcontext`` en tiempo de ejecución.
"""

import json
import re
import subprocess
from pathlib import Path

from presentacion import aviso, depurar
from sandbox_utils import es_comando_peligroso

# --- Estado global ---
MCP_TOOLS_PATH = None  # Se resuelve vía _ruta_mcp_tools()


def _ruta_mcp_tools() -> Path:
    """Devuelve la ruta de mcp_tools.json (respeta el parcheo de snapcontext)."""
    import snapcontext

    parchado = getattr(snapcontext, "MCP_TOOLS_PATH", None)
    if parchado is not None:
        return Path(parchado)
    return snapcontext.CONFIG_DIR / "mcp_tools.json"


HERRAMIENTAS_PREDEFINIDAS = {
    "grep": {
        "descripcion": "Busca un patrón en el código (rg/grep/findstr).",
        "parametros": {"patron": "str", "directorio": "str='.'"},
        "requiere_permiso": False,  # solo lectura
    },
    "read_file": {
        "descripcion": "Lee un archivo completo o un rango de líneas.",
        "parametros": {"ruta": "str", "linea_inicio": "int?", "linea_fin": "int?"},
        "requiere_permiso": False,  # solo lectura
    },
    "list_files": {
        "descripcion": "Lista archivos de una carpeta, con filtro de extensión.",
        "parametros": {"directorio": "str='.'", "extensiones": "list?", "max_archivos": "int=200"},
        "requiere_permiso": False,  # solo lectura
    },
    "ast": {
        "descripcion": "Analiza un .py y extrae imports, clases y funciones.",
        "parametros": {"ruta": "str"},
        "requiere_permiso": False,  # solo lectura
    },
    # v1.4.0: análisis sintáctico multi-lenguaje (tree-sitter) y búsqueda
    # semántica integrada en el sistema de herramientas MCP.
    "ast_avanzado": {
        "descripcion": "Análisis sintáctico multi-lenguaje con tree-sitter "
        "(funciones, clases, imports y llamadas); sin "
        "tree-sitter usa ast de Python.",
        "parametros": {"ruta": "str"},
        "requiere_permiso": False,  # solo lectura
    },
    "semantic_search": {
        "descripcion": "Búsqueda semántica por embeddings; devuelve los "
        "fragmentos/archivos más relevantes para una consulta.",
        "parametros": {"consulta": "str", "directorio": "str='.'", "max_resultados": "int=10"},
        "requiere_permiso": False,  # solo lectura
    },
    "git_status": {
        "descripcion": "Estado de Git (cambios sin commitear, rama actual).",
        "parametros": {"directorio": "str='.'"},
        "requiere_permiso": False,  # solo lectura
    },
    "git_diff": {
        "descripcion": "Muestra el diff (opcionalmente de un archivo).",
        "parametros": {"directorio": "str='.'", "archivo": "str?"},
        "requiere_permiso": False,  # solo lectura
    },
    "execute_command": {
        "descripcion": "Ejecuta cualquier comando shell (confirmación estricta).",
        "parametros": {
            "comando": "str",
            "directorio": "str='.'",
            "background": "bool=False",
            "capture_output": "bool=True",
        },
        "requiere_permiso": True,
    },
    "execute_command_status": {
        "descripcion": "Consulta el estado de un comando lanzado en segundo plano "
        "(devuelve stdout/stderr/código si terminó).",
        "parametros": {"pid": "int"},
        "requiere_permiso": False,
    },
    # v6.7.0: expansión MCP — bases de datos (solo lectura) y APIs externas.
    "db_query": {
        "descripcion": "Ejecuta una consulta SQL de SOLO LECTURA (SELECT, SHOW, "
        "DESCRIBE, EXPLAIN) sobre la base de datos conectada "
        "(conectar antes con --db-url o db_connect). Requiere "
        "confirmación del usuario en modo interactivo.",
        "parametros": {"consulta": "str", "auto": "bool=False"},
        "requiere_permiso": False,  # la validación/confirmación es interna
    },
    "db_schema": {
        "descripcion": "Devuelve el esquema de la base de datos conectada "
        "(tablas, columnas, tipos, claves).",
        "parametros": {},
        "requiere_permiso": False,  # solo lectura
    },
    "api_request": {
        "descripcion": "Hace una petición HTTP (GET/POST/PUT/PATCH/DELETE/HEAD) "
        "a una URL externa y devuelve status, cabeceras y cuerpo "
        "(JSON parseado si aplica).",
        "parametros": {
            "url": "str",
            "metodo": "str='GET'",
            "headers": "dict={}",
            "body": "str=''",
            "timeout": "float=15",
        },
        "requiere_permiso": True,
    },
    "api_inspect": {
        "descripcion": "Inspecciona una URL con GET: status, tiempo de "
        "respuesta, tamaño y tipo de contenido.",
        "parametros": {"url": "str", "timeout": "float=15"},
        "requiere_permiso": False,  # solo lectura (GET)
    },
    # v6.10.0: herramientas de navegador (Playwright) para depuración visual.
    "browser_abrir": {
        "descripcion": "Abre una URL en el navegador headless (Playwright); "
        "espera opcionalmente a que aparezca un selector.",
        "parametros": {"url": "str", "wait_for": "str?", "timeout": "int=30"},
        "requiere_permiso": False,
    },
    "browser_screenshot": {
        "descripcion": "Captura de pantalla (base64 PNG) de la página actual "
        "o de una URL; página completa o un selector concreto.",
        "parametros": {"url": "str?", "full_page": "bool=False", "selector": "str?"},
        "requiere_permiso": False,
    },
    "browser_click": {
        "descripcion": "Hace clic en un elemento de la página actual.",
        "parametros": {"selector": "str"},
        "requiere_permiso": True,
    },
    "browser_type": {
        "descripcion": "Escribe texto en un campo de entrada de la página actual.",
        "parametros": {"selector": "str", "texto": "str"},
        "requiere_permiso": True,
    },
    "browser_get_text": {
        "descripcion": "Extrae el texto de un elemento de la página actual.",
        "parametros": {"selector": "str"},
        "requiere_permiso": False,
    },
    "browser_analizar_imagen": {
        "descripcion": "Analiza una captura (base64) con un modelo de visión "
        "(Gemini 2.5 Pro / Claude 3.7 Sonnet) para detectar "
        "errores visuales.",
        "parametros": {"imagen_base64": "str", "pregunta": "str"},
        "requiere_permiso": False,
    },
    "browser_cerrar": {
        "descripcion": "Cierra el navegador y libera recursos.",
        "parametros": {},
        "requiere_permiso": False,
    },
}


def _cargar_herramientas_mcp() -> dict:
    """Devuelve las herramientas disponibles: predefinidas + las del usuario.

    Las definidas por el usuario viven en ~/.snapcontext/mcp_tools.json con
    formato::

        {"tools": [{"nombre": "build", "descripcion": "...",
                    "comando": "npm run build", "requiere_permiso": true}]}

    Cada herramienta de usuario se ejecuta como comando shell. Archivo
    corrupto o entradas inválidas se ignoran con aviso (sin romper nada).
    """
    herramientas = {nombre: dict(cfg) for nombre, cfg in HERRAMIENTAS_PREDEFINIDAS.items()}
    # v6.10.0: herramientas de navegador (Playwright), solo si Playwright
    # está instalado (import perezoso; si falta no se ofrecen).
    try:
        import mcp_tools_browser as _btool

        if _btool._importar_playwright():
            _btool.registrar_en(herramientas)
    except Exception:
        pass
    ruta = _ruta_mcp_tools()
    try:
        if ruta.is_file():
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            for cruda in datos.get("tools", []) if isinstance(datos, dict) else []:
                nombre = str(cruda.get("nombre") or "").strip()
                comando = str(cruda.get("comando") or "").strip()
                if not nombre or not comando:
                    aviso(f"[mcp] Herramienta de usuario inválida ignorada: {cruda}")
                    continue
                herramientas[nombre] = {
                    "descripcion": str(cruda.get("descripcion") or f"Comando: {comando}"),
                    "parametros": {},
                    "requiere_permiso": bool(cruda.get("requiere_permiso", True)),
                    "comando": comando,
                }
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"No se pudieron leer las herramientas MCP ({ruta}): {exc}")
    # v4.0.0: herramientas expuestas por los plugins instalados y habilitados.
    import snapcontext as _sc

    for nombre, cfg in _sc._plugins_herramientas().items():
        herramientas.setdefault(nombre, cfg)
    return herramientas


# --- Dispatcher MCP: valida permisos y ejecuta la herramienta --------------
def _ejecutar_herramienta_mcp(  # noqa: C901  (refactor de complejidad: Fase 10c)
    nombre: str, argumentos: dict | None = None, confirmar: bool | None = None
) -> dict:
    """Ejecuta una herramienta MCP por nombre con argumentos ``dict``.

    Devuelve un resultado estructurado::

        {"ok": bool, "herramienta": nombre, "resultado": <dict>,
         "error": str|None}

    Si la herramienta requiere permiso, pasa por ``_confirmar_accion``
    (tipo "herramienta"); denegada devuelve ok=False sin ejecutarla.
    """
    import snapcontext as _sc

    argumentos = argumentos or {}
    # v6.22.0: hook `before_tool_use` — puede enriquecer argumentos o abortar.
    _ctx_hook = {"herramienta": nombre, "argumentos": argumentos}
    _abortado, _ctx_hook = _sc._hooks.ejecutar_hook("before_tool_use", _ctx_hook)
    if _abortado:
        return {"ok": False, "herramienta": nombre, "error": "abortado por hook before_tool_use"}
    argumentos = _ctx_hook.get("argumentos") or argumentos
    herramientas = _cargar_herramientas_mcp()
    cfg = herramientas.get(nombre)
    if cfg is None:
        return {
            "ok": False,
            "herramienta": nombre,
            "error": f"herramienta desconocida '{nombre}'. Disponibles: "
            f"{', '.join(sorted(herramientas))}",
        }

    if cfg.get("requiere_permiso"):
        detalles = json.dumps(argumentos, ensure_ascii=False) if argumentos else None
        if not _sc._confirmar_accion(
            f"usar herramienta '{nombre}'",
            tipo="herramienta",
            detalles=detalles,
            confirmar=confirmar,
        ):
            return {"ok": False, "herramienta": nombre, "error": "denegado por el usuario"}

    depurar(f"[mcp] Ejecutando herramienta '{nombre}' con {argumentos}")
    try:
        if nombre == "grep":
            resultado = _sc._tool_grep(
                str(argumentos.get("patron", "")),
                str(argumentos.get("directorio", ".")),
                int(argumentos.get("max_resultados", 50)),
            )
        elif nombre == "read_file":
            resultado = _sc._tool_read_file(
                str(argumentos.get("ruta", "")),
                _entero_opcional(argumentos.get("linea_inicio")),
                _entero_opcional(argumentos.get("linea_fin")),
            )
        elif nombre == "list_files":
            resultado = _sc._tool_list_files(
                str(argumentos.get("directorio", ".")),
                argumentos.get("extensiones"),
                int(argumentos.get("max_archivos", 200)),
            )
        elif nombre == "ast":
            resultado = _sc._tool_ast(str(argumentos.get("ruta", "")))
        elif nombre == "ast_avanzado":
            resultado = _sc._tool_ast_avanzado(str(argumentos.get("ruta", "")))
        elif nombre == "semantic_search":
            resultado = _sc._tool_semantic_search(
                str(argumentos.get("consulta", "")),
                str(argumentos.get("directorio", ".")),
                _entero_opcional(argumentos.get("max_resultados")) or 10,
            )
        elif nombre == "git_status":
            resultado = _sc._tool_git_status(str(argumentos.get("directorio", ".")))
        elif nombre == "git_diff":
            archivo = argumentos.get("archivo")
            resultado = _sc._tool_git_diff(
                str(argumentos.get("directorio", ".")), str(archivo) if archivo else None
            )
        elif nombre == "execute_command":
            resultado = _sc._tool_execute_command(
                str(argumentos.get("comando", "")),
                str(argumentos.get("directorio", ".")),
                background=bool(argumentos.get("background", False)),
                capture_output=bool(argumentos.get("capture_output", True)),
            )
        elif nombre == "execute_command_status":
            pid = _entero_opcional(argumentos.get("pid"))
            if pid is None:
                resultado = {"error": "parámetro 'pid' obligatorio", "codigo": -1}
            else:
                resultado = _sc._estado_proceso_fondo(pid)
        elif nombre == "db_query":
            try:
                import mcp_tools_db as _dbt

                resultado = _dbt.db_query(
                    str(argumentos.get("consulta", "")), auto=bool(argumentos.get("auto", False))
                )
            except ImportError as exc:
                resultado = {"ok": False, "error": f"mcp_tools_db no disponible: {exc}"}
        elif nombre == "db_schema":
            try:
                import mcp_tools_db as _dbt

                resultado = _dbt.db_schema()
            except ImportError as exc:
                resultado = {"ok": False, "error": f"mcp_tools_db no disponible: {exc}"}
        elif nombre == "api_request":
            try:
                import mcp_tools_api as _apit

                resultado = _apit.api_request(
                    str(argumentos.get("url", "")),
                    metodo=str(argumentos.get("metodo", "GET")),
                    headers=dict(argumentos.get("headers") or {}),
                    body=str(argumentos.get("body", "")),
                    timeout=_entero_opcional(argumentos.get("timeout")) or 15,
                )
            except ImportError as exc:
                resultado = {"ok": False, "error": f"mcp_tools_api no disponible: {exc}"}
        elif nombre == "api_inspect":
            try:
                import mcp_tools_api as _apit

                resultado = _apit.api_inspect(
                    str(argumentos.get("url", "")),
                    timeout=_entero_opcional(argumentos.get("timeout")) or 15,
                )
            except ImportError as exc:
                resultado = {"ok": False, "error": f"mcp_tools_api no disponible: {exc}"}
        elif nombre.startswith("browser_"):
            # v6.10.0: herramientas de navegador (Playwright, modo --browser).
            try:
                import mcp_tools_browser as _btool
            except ImportError as exc:
                resultado = {"ok": False, "error": f"mcp_tools_browser no disponible: {exc}"}
            else:
                accion = nombre[len("browser_") :]
                if accion == "abrir":
                    resultado = _btool.browser_abrir(
                        str(argumentos.get("url", "")),
                        wait_for=(
                            str(argumentos["wait_for"]) if argumentos.get("wait_for") else None
                        ),
                        timeout=_entero_opcional(argumentos.get("timeout")) or 30,
                    )
                elif accion == "screenshot":
                    resultado = _btool.browser_screenshot(
                        str(argumentos.get("url", "") or ""),
                        full_page=bool(argumentos.get("full_page", False)),
                        selector=(
                            str(argumentos["selector"]) if argumentos.get("selector") else None
                        ),
                    )
                elif accion == "click":
                    resultado = _btool.browser_click(str(argumentos.get("selector", "")))
                elif accion == "type":
                    resultado = _btool.browser_type(
                        str(argumentos.get("selector", "")), str(argumentos.get("texto", ""))
                    )
                elif accion == "get_text":
                    resultado = _btool.browser_get_text(str(argumentos.get("selector", "")))
                elif accion == "analizar_imagen":
                    resultado = _btool.browser_analizar_imagen(
                        str(argumentos.get("imagen_base64", "")),
                        str(argumentos.get("pregunta", "")),
                    )
                elif accion == "cerrar":
                    resultado = _btool.browser_cerrar()
                else:
                    resultado = {"ok": False, "error": f"acción desconocida: {nombre}"}
        else:
            # Herramienta de usuario definida en mcp_tools.json → comando.
            if cfg.get("plugin"):
                # v4.0.0: los plugins reciben los argumentos como JSON por
                # stdin y responden un JSON {"ok": ..., ...} por stdout.
                try:
                    comando_plugin = str(cfg.get("comando") or "")
                    # seguridad: se valida el peligro antes de ejecutar
                    # el comando de un plugin definido por el usuario.
                    if es_comando_peligroso(comando_plugin):
                        resultado = {
                            "ok": False,
                            "error": "Comando del plugin bloqueado (detección de peligro).",
                        }
                    else:
                        # seguridad: helper seguro. Los plugins reciben
                        # los argumentos por stdin y responden JSON por stdout.
                        import sandbox_utils as _su

                        proceso = _su.ejecutar_comando_con_politica(
                            comando_plugin,
                            timeout=120,
                            entrada=json.dumps(argumentos, ensure_ascii=False),
                        )
                        lineas = (proceso.stdout or "").strip().splitlines()
                        analizado = json.loads(lineas[-1]) if lineas else None
                        if isinstance(analizado, dict):
                            analizado.setdefault("ok", proceso.returncode == 0)
                            resultado = analizado
                        else:
                            resultado = {
                                "ok": proceso.returncode == 0,
                                "codigo_retorno": proceso.returncode,
                                "stdout": (proceso.stdout or "").strip(),
                                "stderr": (proceso.stderr or "").strip(),
                            }
                except subprocess.TimeoutExpired:
                    resultado = {"ok": False, "error": "el plugin excedió el tiempo límite"}
                except Exception as exc:
                    resultado = {"ok": False, "error": str(exc)}
            else:
                resultado = _sc._tool_execute_command(
                    cfg["comando"], str(argumentos.get("directorio", "."))
                )
    except Exception as exc:  # blindaje del agente
        resultado = {"ok": False, "error": f"excepción: {exc}"}
    _salida = {"ok": bool(resultado.get("ok")), "herramienta": nombre, "resultado": resultado}
    # v6.22.0: hook `after_tool_use` — observabilidad / auditoría post-llamada.
    try:
        _sc._hooks.ejecutar_hook(
            "after_tool_use",
            {"herramienta": nombre, "argumentos": argumentos, "resultado": _salida},
        )
    except Exception:
        pass
    return _salida


def _entero_opcional(valor) -> int | None:
    """Convierte a int o devuelve None (para argumentos de herramientas)."""
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _formatear_resultado_mcp(llamada: dict, max_lineas: int = 40) -> str:
    """Convierte el resultado de una llamada MCP en texto legible."""
    if not llamada.get("ok"):
        return f"✖ {llamada.get('herramienta', 'herramienta')}: {llamada.get('error', 'fallo')}"
    res = llamada.get("resultado", {})
    partes: list[str] = []
    for clave, valor in res.items():
        if clave in ("contenido", "diff") and isinstance(valor, str):
            lineas = valor.splitlines()
            muestra = "\n".join(lineas[:max_lineas])
            extra = f"\n… (+{len(lineas) - max_lineas} líneas)" if len(lineas) > max_lineas else ""
            partes.append(f"{clave}:\n{muestra}{extra}")
        elif isinstance(valor, list):
            muestra = ", ".join(map(str, valor[:20]))
            extra = " …" if len(valor) > 20 else ""
            partes.append(f"{clave} ({len(valor)}): {muestra}{extra}")
        else:
            partes.append(f"{clave}: {valor}")
    return "\n".join(partes) or "(sin datos)"


def _contexto_automatico_mcp(mensaje: str, max_llamadas: int = 2) -> str:
    """Uso automático de herramientas de solo lectura según el mensaje.

    Heurística ligera: si el usuario pregunta dónde está algo, el estado del
    repo o qué archivos hay, se ejecutan hasta ``max_llamadas`` herramientas
    de solo lectura y se devuelve un bloque de contexto (str) para añadir al
    prompt del proveedor. Cadena vacía si no aplica.
    """
    texto = mensaje.lower()
    llamadas: list[tuple] = []

    if any(
        p in texto
        for p in ("busca ", "buscar ", "dónde está", "donde esta", "grep", "quién usa", "quien usa")
    ):
        # Términos demasiado genéricos para usar como patrón de búsqueda.
        paradas = {
            "busca",
            "buscar",
            "dónde",
            "donde",
            "está",
            "esta",
            "quién",
            "quien",
            "usa",
            "usan",
            "usado",
            "usar",
            "usos",
        }
        candidatos = [
            p for p in re.findall(r"\w+", mensaje) if len(p) >= 3 and p.lower() not in paradas
        ]
        if candidatos:
            # El término más largo suele ser el identificador relevante.
            llamadas.append(("grep", {"patron": max(candidatos, key=len)}))
    if any(
        p in texto for p in ("estado de git", "git status", "sin commitear", "cambios pendientes")
    ):
        llamadas.append(("git_status", {}))
    if any(
        p in texto
        for p in ("lista los archivos", "list_files", "qué archivos hay", "que archivos hay")
    ):
        llamadas.append(("list_files", {"max_archivos": 50}))

    bloques: list[str] = []
    import snapcontext as _sc

    for nombre, argumentos in llamadas[:max_llamadas]:
        llamada = _sc._ejecutar_herramienta_mcp(nombre, argumentos)
        bloques.append(f"[{nombre}] " + _formatear_resultado_mcp(llamada, max_lineas=15))
    return "\n".join(bloques)
