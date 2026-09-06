"""Configuración inicial, proveedores de IA y asistente interactivo (--init).

Extraído de ``snapcontext.py`` (Fase 4 de la fragmentación del monolito).
Contiene la gestión de ``config.json``, la selección de proveedores y el
asistente de configuración inicial.
"""

import json
import os
import secrets
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from presentacion import (  # noqa: E402
    _CYAN,
    _emitir,
    _pintar,
    aviso,
    depurar,
    error,
    exito,
    info,
)

# Globales perezosos de snapcontext (módulos IA y helpers) — PEP 562.
_PROXY = (
    "_importar_anthropic",
    "_importar_genai",
    "_importar_openai",
    "_resolver_url_openai",
    "anthropic",
    "genai",
    "openai",
    "MODELOS_LIGEROS_OLLAMA",
)


def __getattr__(name: str):  # noqa: D103
    if name in _PROXY:
        import snapcontext as _sc  # lazy: evita ciclo en import time

        return getattr(_sc, name)
    raise AttributeError(f"módulo 'configuracion' no tiene el atributo {name!r}")


# --- PROVEEDOR_DEFECTO (487-487) ---
PROVEEDOR_DEFECTO = os.environ.get("SNAPCONTEXT_PROVIDER", "gemini")

# --- CONFIG_DIR (491-491) ---
CONFIG_DIR = Path.home() / ".snapcontext"

# --- CONFIG_PATH (492-492) ---
CONFIG_PATH = CONFIG_DIR / "config.json"

# --- PROVEEDORES (510-564) ---
PROVEEDORES = {
    "gemini": {
        "nombre": "Gemini",
        "tipo": "gemini",
        "clave_env": "GEMINI_API_KEY",
        "requiere_clave": True,
        "modelo_default": "gemini-2.5-flash",
    },
    "ollama": {
        "nombre": "Ollama",
        "tipo": "openai",
        "clave_env": "OLLAMA_API_KEY",       # opcional: servidor local
        "requiere_clave": False,
        "url_env": "OLLAMA_URL",
        "url_default": "http://localhost:11434",
        "modelo_default": "llama3.2",
    },
    "deepseek": {
        "nombre": "DeepSeek",
        "tipo": "openai",
        "clave_env": "DEEPSEEK_API_KEY",
        "requiere_clave": True,
        "base_url": "https://api.deepseek.com",     # API compatible con OpenAI
        "modelo_default": "deepseek-chat",
        # v6.11.0: DeepSeek soporta marcas cache_control (ephemeral).
        "soporta_caching": True,
    },
    "groq": {
        "nombre": "Groq",
        "tipo": "openai",
        "clave_env": "GROQ_API_KEY",
        "requiere_clave": True,
        "base_url": "https://api.groq.com/openai/v1",
        "modelo_default": "llama-3.3-70b-versatile",
    },
    "anthropic": {
        "nombre": "Claude",
        "tipo": "anthropic",                 # SDK oficial `anthropic`
        "clave_env": "ANTHROPIC_API_KEY",
        "requiere_clave": True,
        "url_base": None,                    # se usa la URL oficial por defecto
        "modelo_default": "claude-3-5-sonnet-20241022",
        # v6.11.0: Anthropic (Claude) soporta marcas cache_control (ephemeral).
        "soporta_caching": True,
    },
    # v6.34.0: soporte para GPUs Intel XPU (Intel Arc) vía IPEX.
    "xpu": {
        "nombre": "Intel XPU",
        "tipo": "xpu",                       # backend local con IPEX
        "clave_env": None,
        "requiere_clave": False,
        "modelo_default": "Qwen/Qwen3.5-35B-A3B",
        "soporta_caching": False,
    },
}

# --- MENSAJE_OPENAI_FALTANTE (636-639) ---
MENSAJE_OPENAI_FALTANTE = (
    "Este proveedor usa la librería 'openai' (API compatible con OpenAI).\n"
    "Instálala con:  pip install openai"
)

# --- MENSAJE_ANTHROPIC_FALTANTE (640-644) ---
MENSAJE_ANTHROPIC_FALTANTE = (
    "Este proveedor usa la librería 'anthropic' (API oficial de Claude).\n"
    "Instálala con:  pip install snapcontext[anthropic]\n"
    "  (o directamente: pip install anthropic>=0.30.0)"
)

# --- cargar_configuracion (1258-1284) ---
def cargar_configuracion() -> dict:
    """Lee la configuración guardada en ~/.snapcontext/config.json.

    El archivo es un JSON con las claves 'provider' y, opcionalmente, 'model'.
    Si no existe o está corrupto, se devuelve un dict vacío.

    CORRECCIÓN 0.6.0: Manejo explícito de FileNotFoundError y json.JSONDecodeError
    para evitar silenciar errores importantes sin aviso.
    """
    try:
        if CONFIG_PATH.is_file():
            datos = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(datos, dict):
                return datos
        elif not CONFIG_PATH.exists():
            # Archivo no existe aún; devolver vacío sin error
            return {}
    except FileNotFoundError:
        aviso(f"Archivo de configuración no encontrado: {CONFIG_PATH}")
        pass  # Devolver {} si el directorio/config no existe aún
    except json.JSONDecodeError as exc:
        error(f"Configuración corrupta en {CONFIG_PATH}: {exc}")
        pass  # No intentar recuperar, devolver {} para evitar estado inconsistente
    except (OSError, ValueError) as exc:
        aviso(f"Error leyendo configuración: {type(exc).__name__}: {exc}")
        pass  # Opcional: continuar sin la configuración previa
    return {}

# --- guardar_configuracion (1287-1314) ---
def guardar_configuracion(provider: str, model: Optional[str] = None,
                          api_keys: Optional[dict] = None) -> bool:
    """Guarda el proveedor preferido, modelo opcional y claves API.

    Recibe además `api_keys` (dict {proveedor: clave}) que se mezcla con las
    existentes, de modo que guardar solo el proveedor (como hace
    `_determinador_proveedor`) no borre las claves ya configuradas con --init.
    Devuelve True si se escribió correctamente en ~/.snapcontext/config.json.
    """
    try:
        existente = cargar_configuracion()
        claves = dict(existente.get("api_keys") or {})
        if api_keys:
            claves.update({k: v for k, v in api_keys.items() if v})

        datos: dict = {"provider": provider}
        if model:
            datos["model"] = model
        if claves:
            datos["api_keys"] = claves

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError:
        return False

# --- _actualizar_clave_configuracion (1317-1331) ---
def _actualizar_clave_configuracion(clave: str, valor) -> bool:
    """Actualiza una clave arbitraria de ~/.snapcontext/config.json.

    A diferencia de :func:`guardar_configuracion` (que reescribe solo
    proveedor/modelo/claves), preserva el resto del JSON (asesor, api_key...).
    """
    try:
        datos = cargar_configuracion()
        datos[clave] = valor
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False

# --- _generar_clave_api (1334-1345) ---
def _generar_clave_api(guardar: bool = True) -> str:
    """Genera una clave API segura (url-safe, 32 bytes) para la API v3.6.0.

    Si ``guardar`` es True, la persiste en ``~/.snapcontext/config.json``
    bajo la clave ``"api_key"``.
    """
    import secrets

    clave = secrets.token_urlsafe(32)
    if guardar:
        _actualizar_clave_configuracion("api_key", clave)
    return clave

# --- _importar_questionary (1348-1354) ---
def _importar_questionary():
    """Devuelve el módulo 'questionary' o None si no está instalado."""
    try:
        import questionary
        return questionary
    except ImportError:  # pragma: no cover
        return None

# --- _listar_modelos_ollama (1357-1385) ---
def _listar_modelos_ollama() -> tuple:
    """Devuelve (modelos, error) consultando los modelos locales vía `ollama list`.

    La primera columna de cada fila (la cabecera se ignora) es el nombre del
    modelo. Si `ollama` no está o falla, devuelve ([], mensaje de error).
    """
    try:
        proc = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError:
        return [], "No se encontró 'ollama' en el PATH. ¿Está instalado?"
    except subprocess.TimeoutExpired:
        return [], "El comando 'ollama list' tardó demasiado (60 s)."
    except OSError as exc:
        return [], f"No se pudo ejecutar 'ollama list': {exc}"

    if proc.returncode != 0:
        fallo = (proc.stderr or proc.stdout or "").strip()
        return [], fallo or "El comando 'ollama list' devolvió un error."

    modelos: List[str] = []
    for num_linea, linea in enumerate((proc.stdout or "").splitlines()):
        if num_linea == 0:          # cabecera: ID  NAME  SIZE  MODIFIED
            continue
        partes = linea.split()
        if partes:
            modelos.append(partes[0])
    return modelos, None

# --- seleccionar_proveedor_interactivo (1388-1445) ---
def seleccionar_proveedor_interactivo() -> tuple:
    """Menú interactivo (questionary) para elegir proveedor y, si es Ollama,
    su modelo local. Devuelve (provider, model).

    - Pregunta primero si se quiere elegir el proveedor ahora.
    - Si se elige Ollama, se auto-detectan los modelos con `ollama list`.
      Sin modelos / sin ollama instalado, se avisa y se ofrece volver al menú
      de proveedores o usar Gemini por defecto.
    - Sin questionary se avisa y se usa PROVEEDOR_DEFECTO (gemini), model None.
    """
    questionary = _importar_questionary()
    if questionary is None:
        _emitir(
            sys.stdout,
            "💡 Para usar el modo interactivo, instala: pip install questionary",
        )
        return (PROVEEDOR_DEFECTO, None)

    if not questionary.confirm("¿Deseas seleccionar el proveedor de IA ahora?").ask():
        return (PROVEEDOR_DEFECTO, None)

    while True:
        opciones = [
            questionary.Choice("Gemini (Google)", value="gemini"),
            questionary.Choice("Claude (Anthropic)", value="anthropic"),
            questionary.Choice("Ollama (local)", value="ollama"),
            questionary.Choice("DeepSeek (API)", value="deepseek"),
            questionary.Choice("Groq (API)", value="groq"),
        ]
        proveedor = questionary.select(
            "🤗 Selecciona el proveedor de IA:",
            choices=opciones,
        ).ask() or PROVEEDOR_DEFECTO

        # Ollama → auto-detección de modelos locales (Mejora 2).
        if proveedor == "ollama":
            modelos, error = _listar_modelos_ollama()
            if modelos:
                elegido = questionary.select(
                    "🤗 Selecciona el modelo de Ollama:",
                    choices=list(modelos),
                ).ask()
                return ("ollama", elegido or modelos[0])

            if error:
                aviso(f"No se pudieron listar modelos de Ollama: {error}")
            else:
                aviso("Ollama no tiene modelos instalados. "
                      "Prueba: ollama pull llama3.2")
            usar_gemini = questionary.confirm(
                "¿Quieres usar Gemini por defecto? (No = volver al proveedor)"
            ).ask()
            if usar_gemini:
                return ("gemini", None)
            # Si responde "no": vuelve al menú de proveedores.
            continue

        return (proveedor, None)

# --- _preguntar_guardar_config (1448-1461) ---
def _preguntar_guardar_config() -> bool:
    """Pregunta si guardar el proveedor elegido como predeterminado.

    Solo hace la pregunta si questionary está instalada; si no, devuelve False
    y no se persiste nada (comportamiento elegante sin dependencia extra).
    """
    questionary = _importar_questionary()
    if questionary is None:
        return False
    return bool(
        questionary.confirm(
            "¿Guardar este proveedor como predeterminado?"
        ).ask()
    )

# --- _probar_conexion_proveedor (1464-1526) ---
def _probar_conexion_proveedor(provider: str, model: Optional[str] = None) -> bool:
    """Comprueba la conexión con la API del proveedor elegido (usado por --init).

    Reutiliza la clave guardada en la configuración o, como plan B, la variable
    de entorno correspondiente. Hace una llamada mínima y devuelve True si ok.
    """
    cfg = PROVEEDORES[provider]
    api_keys = cargar_configuracion().get("api_keys") or {}

    if provider == "gemini":
        if _importar_genai() is None:
            aviso("Falta google-generativeai. Instala: pip install google-generativeai")
            return False
        clave = (api_keys.get("gemini") or "").strip() \
            or os.environ.get("GEMINI_API_KEY", "").strip()
        if not clave:
            aviso("No se encontró ninguna clave de Gemini.")
            return False
        try:
            genai.configure(api_key=clave)
            genai.GenerativeModel(model or cfg["modelo_default"]).generate_content("responde ok")
            return True
        except Exception:
            return False

    # Claude (Anthropic): SDK oficial, distinto de la API estilo OpenAI.
    if provider == "anthropic":
        if _importar_anthropic() is None:
            aviso(MENSAJE_ANTHROPIC_FALTANTE)
            return False
        clave = (api_keys.get("anthropic") or "").strip() \
            or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not clave:
            aviso("No se encontró ninguna clave de Anthropic.")
            return False
        try:
            cliente = anthropic.Anthropic(api_key=clave)
            cliente.messages.create(
                model=model or cfg["modelo_default"],
                max_tokens=5,
                messages=[{"role": "user", "content": "responde ok"}],
            )
            return True
        except Exception:
            return False

    # Proveedores con API estilo OpenAI (Groq, DeepSeek y Ollama).
    if _importar_openai() is None:
        aviso(MENSAJE_OPENAI_FALTANTE)
        return False
    clave = (api_keys.get(provider) or "").strip() \
        or os.environ.get(cfg["clave_env"], "").strip()
    base_url = _resolver_url_openai(cfg)
    try:
        cliente = openai.OpenAI(api_key=clave or "ollama", base_url=base_url)
        cliente.chat.completions.create(
            model=model or cfg["modelo_default"],
            messages=[{"role": "user", "content": "responde ok"}],
            max_tokens=5,
        )
        return True
    except Exception:
        return False

# --- asistente_configuracion_inicial (1529-1641) ---
def asistente_configuracion_inicial() -> int:
    """Asistente interactivo de configuración inicial (SNAPCONTEXT --init).

    Guía en la configuración de claves API y el proveedor/modelo favorito en
    ~/.snapcontext/config.json. Devuelve el código de salida (0 = éxito).
    """
    questionary = _importar_questionary()
    if questionary is None:
        aviso(
            "El asistente requiere questionary. "
            "Instálalo con: pip install questionary"
            "  (o: pip install snapcontext[interactive])"
        )
        return 1

    if CONFIG_PATH.exists() and not questionary.confirm(
        "¿Ya existe una configuración. ¿Quieres sobrescribirla?"
    ).ask():
        aviso("Configuración no modificada.")
        return 0

    exito("Configuración inicial de SnapContext")
    api_keys: dict = dict(cargar_configuracion().get("api_keys") or {})

    clave = questionary.password(
        "Clave de API de Gemini (GEMINI_API_KEY):",
        default=api_keys.get("gemini", ""),
    ).ask()
    if clave and clave.strip():
        api_keys["gemini"] = clave.strip()

    if questionary.confirm(
        "¿Quieres configurar otros proveedores (Groq, DeepSeek)?"
    ).ask():
        for prov in ("groq", "deepseek"):
            env = PROVEEDORES[prov]["clave_env"]
            # CORRECCIÓN 0.6.0: Usar questionary.password() en lugar de text(password=True)
            valor = questionary.password(
                f"Clave de API de {PROVEEDORES[prov]['nombre']} ({env}):",
                default=api_keys.get(prov, ""),
            ).ask()
            if valor and valor.strip():
                api_keys[prov] = valor.strip()
        aviso("Ollama es local y no necesita clave (opcional: OLLAMA_API_KEY).")

    aviso("Ahora elige tu proveedor y modelo favoritos (con las flechas).")
    proveedor, modelo = seleccionar_proveedor_interactivo()

    if not guardar_configuracion(proveedor, modelo, api_keys):
        error(f"No se pudo escribir la configuración en {CONFIG_PATH}")
        return 1
    exito(f"Configuración guardada en {CONFIG_PATH}")

    if questionary.confirm("¿Quieres probar la conexión con la API ahora?").ask():
        if _probar_conexion_proveedor(proveedor, modelo):
            exito("¡Conexión con la API verificada correctamente!")
        else:
            error("No se pudo conectar con la API. Revisa la clave.")
            return 1

    # ── v3.1.0: Ollama, proyecto de prueba y tutorial ─────────────────────
    if questionary.confirm(
        "¿Quieres configurar Ollama (modo offline, sin API key)?"
    ).ask():
        estado_ol = _estado_ollama()
        if estado_ol["modelos"]:
            ligero = _elegir_modelo_ligero(estado_ol["modelos"])
            exito(f"Ollama ya está listo (modelo más ligero: '{ligero}').")
            if questionary.confirm(
                "¿Usar Ollama como proveedor por defecto?"
            ).ask():
                guardar_configuracion("ollama", ligero, api_keys)
                proveedor, modelo = "ollama", ligero
                exito(f"Proveedor guardado: ollama / {ligero}.")
        else:
            aviso("Ollama no está instalado o no tiene modelos descargados.")
            info("Descárgalo desde https://ollama.com y después ejecuta:")
            info("  ollama pull llama3.2")
            try:
                import webbrowser
                if questionary.confirm(
                    "¿Abrir https://ollama.com en el navegador?"
                ).ask():
                    webbrowser.open("https://ollama.com")
            except Exception:
                pass

    if questionary.confirm(
        "¿Quieres crear un proyecto de prueba para empezar?"
    ).ask():
        try:
            destino = input(_pintar(
                "Carpeta del proyecto de prueba "
                "(Enter = ./snapcontext-prueba): ", _CYAN)).strip() or \
                "snapcontext-prueba"
        except EOFError:
            destino = ""
        if destino:
            ruta = Path(destino).expanduser().resolve()
            try:
                _crear_demo_proyecto(ruta)
                exito(f"Proyecto de prueba creado en: {ruta}")
                info("Pruébalo con:")
                info(f'  cd "{ruta}" && snapcontext '
                     '"describe este proyecto" --vista-previa --local')
            except OSError as exc:
                error(f"No se pudo crear el proyecto: {exc}")

    if questionary.confirm(
        "¿Quieres ejecutar el tutorial interactivo ahora (--bienvenida)?"
    ).ask():
        return _tutorial_interactivo()
    return 0

# --- hay_api_key_configurada (1647-1664) ---
def hay_api_key_configurada() -> bool:
    """True si hay alguna clave de API en el entorno o en la configuración.

    Comprueba las variables GEMINI_API_KEY / ANTHROPIC_API_KEY /
    DEEPSEEK_API_KEY / GROQ_API_KEY / OPENAI_API_KEY y, además, las claves
    guardadas en ~/.snapcontext/config.json (sección 'api_keys').
    """
    for env in CLAVES_API_CONOCIDAS:
        if (os.environ.get(env) or "").strip():
            return True
    try:
        claves = cargar_configuracion().get("api_keys") or {}
    except Exception:
        claves = {}
    for valor in claves.values():
        if isinstance(valor, str) and valor.strip():
            return True
    return False

# --- _estado_ollama (1667-1674) ---
def _estado_ollama() -> dict:
    """Devuelve {'instalado': bool, 'modelos': [str], 'error': str|None}."""
    modelos, fallo = _listar_modelos_ollama()
    return {
        "instalado": bool(modelos) or (fallo is not None and "PATH" not in fallo),
        "modelos": modelos,
        "error": fallo,
    }

# --- _elegir_modelo_ligero (1677-1693) ---
def _elegir_modelo_ligero(modelos: List[str]) -> Optional[str]:
    """Elige el modelo más ligero disponible según MODELOS_LIGEROS_OLLAMA.

    Devuelve None si la lista está vacía.
    """
    if not modelos:
        return None
    for preferido in MODELOS_LIGEROS_OLLAMA:
        for m in modelos:
            if m == preferido or m.startswith(preferido + ":"):
                return m
    # Coincidencia parcial (p. ej. "llama3.2:latest").
    for preferido in MODELOS_LIGEROS_OLLAMA:
        for m in modelos:
            if preferido in m:
                return m
    return modelos[0]

# --- _tutorial_interactivo (12567-12603) ---
def _tutorial_interactivo() -> int:
    """Tutorial interactivo (--bienvenida): guía de primeros pasos."""
    info("=== SnapContext · Tutorial interactivo ===")
    pasos = [
        ("1. Comprueba tu instalación",
         "  Ejecuta 'snapcontext --version' y 'snapcontext --diagnostico'\n"
         "  para verificar que todo está listo."),
        ("2. Configura tu cerebro",
         "  Sin API key, SnapContext usa Ollama local automáticamente.\n"
         "  Con clave: 'snapcontext --init' guarda tu proveedor favorito."),
        ("3. Tu primera tarea",
         '  En tu proyecto ejecuta:\n'
         '    snapcontext "describe brevemente este proyecto" --vista-previa\n'
         "  Verás qué archivos seleccionaría la IA sin tocar nada."),
        ("4. Deja que trabaje",
         "  Quita --vista-previa y SnapContext usará Aider para editar.\n"
         "  Añade --test-loop para que verifique con tus pruebas."),
        ("5. Aprende más",
         "  'snapcontext --help' (ayuda agrupada), 'snapcontext --demo'\n"
         '  y \'snapcontext --plan "tarea"\' (planificador).'),
    ]
    for titulo, detalle in pasos:
        exito(titulo)
        print(detalle)
        print()
        try:
            respuesta = input(
                _pintar("  [Enter] continuar ('q' para salir)... ",
                        _CYAN)).strip().lower()
        except EOFError:
            break
        if respuesta in ("q", "quit", "salir"):
            info("Tutorial interrumpido. Puedes volver a verlo con "
                 "'snapcontext --bienvenida'.")
            return 0
    exito("¡Tutorial completado! Bienvenido a SnapContext 🎉")
    return 0

# --- _crear_demo_proyecto (12609-12636) ---
def _crear_demo_proyecto(directorio: Path) -> None:
    """Crea un proyecto Python de ejemplo (con un bug) en ``directorio``.

    Estructura:
      - ``src/main.py``: ``saludar(nombre)`` con un error (usa ``name``).
      - ``tests/test_main.py``: test que falla con el bug.
      - ``src/__init__.py``: hace ``src`` importable para el comando de prueba.

    La carpeta ``src``/``tests`` hace que la auto-detección clasifique la demo
    como proyecto Python y que el escaneo (--local) encuentre los archivos.
    """
    (directorio / "src").mkdir(parents=True, exist_ok=True)
    (directorio / "tests").mkdir(parents=True, exist_ok=True)
    # Archivo identificador: fuerza la auto-detección como proyecto Python
    # (evita que `src/` haga que se clasifique como Node en el respaldo por carpetas).
    (directorio / "requirements.txt").write_text("", encoding="utf-8")
    (directorio / "src" / "__init__.py").write_text("", encoding="utf-8")
    (directorio / "src" / "main.py").write_text(
        "def saludar(nombre):\n"
        '    return f"Hola, {name}"  # bug: debería ser {nombre}\n',
        encoding="utf-8",
    )
    (directorio / "tests" / "test_main.py").write_text(
        "from src.main import saludar\n\n\n"
        "def test_saludo():\n"
        '    assert saludar("Mundo") == "Hola, Mundo"\n',
        encoding="utf-8",
    )

