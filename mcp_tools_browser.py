#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_tools_browser.py — Herramientas MCP de navegador (v6.10.0).

Expone herramientas para controlar un navegador con **Playwright** y permitir
que el agente ReAct *vea* la interfaz de una aplicación en ejecución:

  • ``browser_abrir``          : navega a una URL (espera opcional a selector).
  • ``browser_screenshot``     : captura de pantalla (página completa o elemento).
  • ``browser_click``          : clic en un selector CSS.
  • ``browser_type``           : escribe texto en un campo de entrada.
  • ``browser_get_text``       : extrae el texto de un elemento.
  • ``browser_analizar_imagen``: análisis visual con un modelo de visión
                                 (Gemini/Claude). Si el modelo no soporta
                                 visión devuelve un error claro.
  • ``browser_cerrar``         : cierra el navegador y libera recursos.

Diseño:
  - **Opcional**: solo se activa con ``--browser`` (o ``browser_activar()``).
    Sin activar, todas las herramientas devuelven un error descriptivo y
    Playwright nunca se importa (lazy import).
  - **Sesión persistente**: una única instancia de navegador/página se
    reutiliza durante toda la tarea (similar a la sesión Docker). Si el
    navegador muere inesperadamente, se reinicia automáticamente.
  - **Headless por defecto** para no interferir con el usuario.
  - Nunca lanza excepciones: todas las herramientas devuelven dicts.
"""

import base64
from typing import Any, Dict, Optional

# Estado de sesión del navegador (persistente durante la tarea)
_BROWSER_ACTIVO = False      # activado con --browser (o browser_activar)
_PLAYWRIGHT = None           # instancia sync_playwright (lazy)
_NAVEGADOR = None            # instancia de Browser
_CONTEXTO = None             # contexto de navegador (cookies, viewport)
_PAGINA = None               # página actual
_HEADLESS = True             # seguridad: sin interfaz gráfica por defecto


def browser_activar(headless: bool = True) -> None:
    """Activa el modo navegador (equivale al flag ``--browser``)."""
    global _BROWSER_ACTIVO, _HEADLESS
    _BROWSER_ACTIVO = True
    _HEADLESS = bool(headless)


def browser_desactivar() -> None:
    """Desactiva el modo navegador y libera recursos."""
    global _BROWSER_ACTIVO
    browser_cerrar()
    _BROWSER_ACTIVO = False


def browser_activo() -> bool:
    """Indica si el modo navegador está activado (``--browser``)."""
    return _BROWSER_ACTIVO


def _info(mensaje: str) -> None:
    """Mensaje informativo (degrada a print sin snapcontext)."""
    try:
        import snapcontext as sc
        sc.info(mensaje)
    except Exception:                                    # noqa: BLE001
        try:
            print(mensaje)
        except Exception:                                # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Gestión de sesión (persistente + autorreinicio)
# ---------------------------------------------------------------------------
def _cerrar_interno() -> None:
    """Cierra navegador/contexto sin tocar el flag de activación."""
    global _NAVEGADOR, _CONTEXTO, _PAGINA, _PLAYWRIGHT
    for recurso in (_CONTEXTO, _NAVEGADOR):
        try:
            if recurso is not None:
                recurso.close()
        except Exception:                                # noqa: BLE001
            pass
    _NAVEGADOR = _CONTEXTO = _PAGINA = None
    try:
        if _PLAYWRIGHT is not None:
            _PLAYWRIGHT.stop()
    except Exception:                                    # noqa: BLE001
        pass
    _PLAYWRIGHT = None


def _importar_playwright() -> bool:
    """Import perezoso de Playwright. True si está disponible."""
    try:
        import playwright.sync_api                       # noqa: F401
        return True
    except ImportError:
        return False


def _navegador_vivo() -> bool:
    """True si hay página activa y el navegador no se ha cerrado."""
    try:
        if _PAGINA is None or _NAVEGADOR is None:
            return False
        # Si el navegador se cerró inesperadamente, esto lanza.
        return not _NAVEGADOR.is_closed()
    except Exception:                                    # noqa: BLE001
        return False


def _exito(mensaje: str) -> None:
    """Mensaje de éxito (degrada a print sin snapcontext)."""
    try:
        import snapcontext as sc
        sc.exito(mensaje)
    except Exception:                                    # noqa: BLE001
        try:
            print(mensaje)
        except Exception:                                # noqa: BLE001
            pass


def _asegurar_navegador() -> Optional[Any]:
    """Devuelve la página activa, (re)iniciando el navegador si hace falta.

    Devuelve ``None`` si Playwright no está disponible o falla el arranque
    (con instrucciones de instalación en el error del llamante).
    """
    global _CONTEXTO, _PAGINA, _NAVEGADOR, _PLAYWRIGHT
    if not _importar_playwright():
        return None
    if _navegador_vivo():
        return _PAGINA
    # (Re)inicio automático: sesión persistente por tarea.
    _cerrar_interno()
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        navegador = pw.chromium.launch(headless=_HEADLESS)
        contexto = navegador.new_context()
        pagina = contexto.new_page()
    except Exception:                                    # noqa: BLE001
        return None
    _PLAYWRIGHT = pw
    _NAVEGADOR = navegador
    _CONTEXTO = contexto
    _PAGINA = pagina
    return pagina


def _activo_ok() -> Optional[Dict[str, Any]]:
    """Valida que el modo navegador esté activado (--browser)."""
    if not _BROWSER_ACTIVO:
        return {"ok": False,
                "error": "modo navegador no activado: reinicia con --browser"}
    return None


def _playwright_ok() -> Optional[Dict[str, Any]]:
    """Valida que Playwright esté instalado (con instrucciones si no)."""
    if not _importar_playwright():
        return {"ok": False,
                "error": "Playwright no instalado. Ejecuta:\n"
                         "  pip install 'snapcontext[browser]'\n"
                         "  playwright install chromium"}
    return None


# ---------------------------------------------------------------------------
# Herramientas MCP
# ---------------------------------------------------------------------------
def browser_abrir(url: str, wait_for: Optional[str] = None,
                  timeout: int = 30) -> Dict[str, Any]:
    """Abre ``url`` en el navegador (espera ``wait_for`` si se indica)."""
    gate = _activo_ok()
    if gate:
        return gate
    url = str(url or "").strip()
    if not url or not (url.startswith("http://")
                       or url.startswith("https://")
                       or url.startswith("file://")):
        return {"ok": False, "error": f"URL inválida: {url!r}"}
    gate = _playwright_ok()
    if gate:
        return gate
    _info("🌐 Abriendo navegador...")
    pagina = _asegurar_navegador()
    if pagina is None:
        return {"ok": False, "url": url,
                "error": "no se pudo iniciar el navegador "
                         "(¿playwright install chromium?)"}
    try:
        pagina.goto(url, timeout=int(timeout) * 1000)
        if wait_for:
            pagina.wait_for_selector(str(wait_for),
                                     timeout=int(timeout) * 1000)
        titulo = pagina.title()
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "url": url,
                "error": f"{type(exc).__name__}: {exc}"}
    _exito("✅ Navegador listo")
    return {"ok": True, "url": pagina.url, "titulo": titulo}


def browser_screenshot(url: str = "", full_page: bool = False,
                       selector: Optional[str] = None) -> Dict[str, Any]:
    """Captura la página actual (o navega a ``url`` antes) en base64 PNG."""
    if url:
        previo = browser_abrir(url)
        if not previo.get("ok"):
            return previo
    gate = _activo_ok() or _playwright_ok()
    if gate:
        return gate
    pagina = _asegurar_navegador()
    if pagina is None:
        return {"ok": False, "error": "navegador no iniciado"}
    objetivo = pagina
    if selector:
        try:
            objetivo = pagina.query_selector(str(selector))
        except Exception as exc:                         # noqa: BLE001
            return {"ok": False, "selector": selector,
                    "error": f"{type(exc).__name__}: {exc}"}
        if objetivo is None:
            return {"ok": False, "selector": selector,
                    "error": f"selector no encontrado: {selector}"}
    _info(f"📸 Capturando pantalla de {pagina.url}...")
    try:
        datos = objetivo.screenshot(full_page=bool(full_page)
                                    and objetivo is pagina)
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _exito("✅ Captura guardada")
    return {"ok": True, "url": pagina.url, "selector": selector,
            "imagen": base64.b64encode(datos).decode("ascii"),
            "formato": "png"}


def browser_click(selector: str) -> Dict[str, Any]:
    """Hace clic en el elemento ``selector`` de la página actual."""
    gate = _activo_ok() or _playwright_ok()
    if gate:
        return gate
    selector = str(selector or "").strip()
    if not selector:
        return {"ok": False, "error": "falta 'selector'"}
    pagina = _asegurar_navegador()
    if pagina is None:
        return {"ok": False, "error": "navegador no iniciado"}
    _info(f"🖱️ Haciendo clic en {selector}...")
    try:
        pagina.click(selector, timeout=10000)
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "selector": selector,
                "error": f"{type(exc).__name__}: {exc}"}
    _exito("✅ Click realizado")
    return {"ok": True, "selector": selector}


def browser_type(selector: str, texto: str) -> Dict[str, Any]:
    """Escribe ``texto`` en el campo ``selector`` de la página actual."""
    gate = _activo_ok() or _playwright_ok()
    if gate:
        return gate
    selector = str(selector or "").strip()
    if not selector:
        return {"ok": False, "error": "falta 'selector'"}
    pagina = _asegurar_navegador()
    if pagina is None:
        return {"ok": False, "error": "navegador no iniciado"}
    _info(f"⌨️ Escribiendo en {selector}...")
    try:
        pagina.fill(selector, str(texto), timeout=10000)
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "selector": selector,
                "error": f"{type(exc).__name__}: {exc}"}
    _exito("✅ Texto escrito")
    return {"ok": True, "selector": selector, "texto": str(texto)}


def browser_get_text(selector: str) -> Dict[str, Any]:
    """Extrae el texto del elemento ``selector`` de la página actual."""
    gate = _activo_ok() or _playwright_ok()
    if gate:
        return gate
    selector = str(selector or "").strip()
    if not selector:
        return {"ok": False, "error": "falta 'selector'"}
    pagina = _asegurar_navegador()
    if pagina is None:
        return {"ok": False, "error": "navegador no iniciado"}
    try:
        elemento = pagina.query_selector(selector)
        texto = elemento.inner_text() if elemento is not None else None
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "selector": selector,
                "error": f"{type(exc).__name__}: {exc}"}
    if texto is None:
        return {"ok": False, "selector": selector,
                "error": f"selector no encontrado: {selector}"}
    return {"ok": True, "selector": selector, "texto": texto}


def browser_cerrar() -> Dict[str, Any]:
    """Cierra el navegador y libera todos los recursos."""
    _cerrar_interno()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Multimodalidad: análisis visual de capturas (solo modelos con visión)
# ---------------------------------------------------------------------------
_MODELOS_VISION = (
    "gemini",        # Gemini 1.5/2.x/2.5 Pro
    "claude-3",      # Claude 3/3.5/3.7 Sonnet y posteriores
    "claude-4",
    "claude-sonnet",
    "claude-opus",
)


def modelo_soporta_vision(proveedor: Optional[str] = None,
                          modelo: Optional[str] = None) -> bool:
    """True si el proveedor/modelo activo soporta imágenes (visión)."""
    texto = f"{proveedor or ''} {modelo or ''}".lower()
    if not texto.strip():
        try:
            import snapcontext as sc
            config = sc.cargar_configuracion()
            texto = (f"{config.get('provider', '')} "
                     f"{config.get('model', '')}").lower()
        except Exception:                                # noqa: BLE001
            return False
    return any(clave in texto for clave in _MODELOS_VISION)


def browser_analizar_imagen(imagen_base64: str, pregunta: str,
                            proveedor: Optional[str] = None,
                            modelo: Optional[str] = None,
                            llamar_llm: Optional[Any] = None) -> Dict[str, Any]:
    """Envía una captura al modelo de visión con ``pregunta`` y responde.

    ``llamar_llm`` es un callable opcional ``mensajes -> str`` (por ejemplo
    ``ReactAgent._llamar_llm``); si no se pasa, se usa
    ``snapcontext._enviar_al_proveedor`` con el proveedor/modelo activo.
    Solo funciona con modelos multimodales (Gemini 2.5 Pro, Claude 3.7
    Sonnet, ...). Si el modelo no soporta visión devuelve ``ok: False`` con
    un mensaje claro (nunca lanza).
    """
    gate = _activo_ok()
    if gate:
        return gate
    if not modelo_soporta_vision(proveedor, modelo):
        return {"ok": False,
                "error": "el modelo activo no soporta visión; usa Gemini "
                         "(2.5 Pro) o Claude (3.7 Sonnet+) para analizar "
                         "capturas de pantalla"}
    imagen = str(imagen_base64 or "").strip()
    if not imagen:
        return {"ok": False, "error": "falta 'imagen_base64'"}
    pregunta = str(pregunta or "").strip() or \
        "Describe la interfaz y lista cualquier error visual que veas."
    try:
        base64.b64decode(imagen, validate=True)      # valida el base64
        pedido = [{
            "role": "user",
            "content": (
                f"[ANÁLISIS VISUAL]\n{pregunta}\n\n"
                "[IMAGEN ADJUNTA (captura de pantalla del navegador, PNG en "
                "base64)]:\n" + imagen)
        }]
        if callable(llamar_llm):
            respuesta = llamar_llm(pedido)
        else:
            import snapcontext as sc
            # Resolver proveedor/modelo efectivo (config.json si no hay
            # explícito).
            proveedor_efectivo = proveedor
            modelo_efectivo = modelo
            if not proveedor_efectivo:
                try:
                    config = sc.cargar_configuracion()
                    proveedor_efectivo = (config.get("provider")
                                          or sc.PROVEEDOR_DEFECTO)
                    modelo_efectivo = modelo_efectivo or config.get("model")
                except Exception:                        # noqa: BLE001
                    proveedor_efectivo = sc.PROVEEDOR_DEFECTO
            respuesta = sc._enviar_al_proveedor(
                proveedor_efectivo, modelo_efectivo, pedido)
        return {"ok": True, "analisis": str(respuesta)}
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "error": f"análisis visual falló: {exc}"}


# ---------------------------------------------------------------------------
# Registro en el sistema MCP de SnapContext
# ---------------------------------------------------------------------------
def registrar_en(predefinidas: Dict[str, Dict[str, Any]]) -> None:
    """Añade las herramientas de navegador a un dict de herramientas MCP."""
    predefinidas.setdefault("browser_abrir", {
        "descripcion": "Abre una URL en el navegador headless (Playwright); "
                       "espera opcionalmente a que aparezca un selector.",
        "parametros": {"url": "str", "wait_for": "str?", "timeout": "int=30"},
        "requiere_permiso": False,
    })
    predefinidas.setdefault("browser_screenshot", {
        "descripcion": "Captura de pantalla (base64 PNG) de la página actual "
                       "o de una URL; página completa o un selector concreto.",
        "parametros": {"url": "str?", "full_page": "bool=False",
                       "selector": "str?"},
        "requiere_permiso": False,
    })
    predefinidas.setdefault("browser_click", {
        "descripcion": "Hace clic en un elemento de la página actual.",
        "parametros": {"selector": "str"},
        "requiere_permiso": True,
    })
    predefinidas.setdefault("browser_type", {
        "descripcion": "Escribe texto en un campo de entrada de la página "
                       "actual.",
        "parametros": {"selector": "str", "texto": "str"},
        "requiere_permiso": True,
    })
    predefinidas.setdefault("browser_get_text", {
        "descripcion": "Extrae el texto de un elemento de la página actual.",
        "parametros": {"selector": "str"},
        "requiere_permiso": False,
    })
    predefinidas.setdefault("browser_analizar_imagen", {
        "descripcion": "Analiza una captura (base64) con un modelo de visión "
                       "(Gemini 2.5 Pro / Claude 3.7 Sonnet) para detectar "
                       "errores visuales.",
        "parametros": {"imagen_base64": "str", "pregunta": "str"},
        "requiere_permiso": False,
    })
    predefinidas.setdefault("browser_cerrar", {
        "descripcion": "Cierra el navegador y libera recursos.",
        "parametros": {},
        "requiere_permiso": False,
    })

