"""Frontera de autenticación / autorización del Web Gateway (B9.59 → B9.60).

Este módulo concentra, como única fuente de verdad, los mecanismos de seguridad
del gateway que B9.59/B9.60 decidieron cerrar:

- **Credential lifecycle**: carga en frío (``preparar_credencial_inicio``, único
  punto donde se GENERA) vs. autenticación runtime (``cargar_credencial``, que
  NUNCA genera). Fuente de verdad: ``configuracion.CONFIG_PATH`` → clave
  ``api_key`` (mismo origen que ``configuracion.cargar_configuracion``),
  con override ``SNAPCONTEXT_API_KEY`` y token explícito de arranque.
- **Fail-closed**: permisos distintos de 0600, JSON corrupto, ``api_key`` no
  string/vacía/<32, ausencia total → la credencial no es utilizable (401/1008);
  jamás se degrada a «sin autenticación».
- **Comparación en tiempo constante**: ``secrets.compare_digest`` (nunca una
  comparación simple ``!=`` sobre el secreto).
- **Rotación manual M1**: ``rotar_credencial`` invalida la anterior de
  inmediato (relectura por request) y ejecuta los hooks registrados (cierre de
  WebSockets existentes). La credencial nueva se entrega UNA sola vez por
  stdout; nunca en logs, errores, URLs ni respuestas.
- **Exposición**: ``clasificar_exposicion`` → ``loopback`` / ``lan`` /
  ``internet``; reenvío (X-Forwarded-For/Forwarded/X-Forwarded-Host) o Host
  no-local == LAN (B9.59 §11); cliente no verificable == no-loopback.
- **Host (TrustedHost básico)**: solo ``localhost`` e IP literales (anti
  DNS-rebinding); el resto → 400 (HTTP) / 1008 (WS).
- **Origin en WebSockets**: reglas 1-3 de B9.60 §13 (``origin_admitido``).
- **Capability model cerrado**: ``CAPABILIDADES`` (las 9 de B9.59 §7) y
  ``DeclaracionSuperficie`` que alimenta ``CAPABILITY_MAP`` (en ``web.app``).
- **Límite básico de fallos de autenticación** (``LimitadorFallos``).
- **``MiddlewareSeguridad``**: cobertura de superficies — toda ruta registrada
  debe tener entrada en ``CAPABILITY_MAP``; sin entrada → denegación
  (invariancia: una ruta nueva sin declaración no es ejecutable).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Match, WebSocketRoute

logger = logging.getLogger("snapcontext.web.seguridad")

Exposicion = Literal["loopback", "lan", "internet"]

#: Longitud mínima exigida a una credencial persistida o explícita (B9.59 §9).
MIN_LONGITUD_CREDENCIAL = 32

#: Umbral documentado del rate básico de autenticación (B9.59 §14): 20 fallos
#: por ventana de 60 s y cliente → 429 (HTTP) / 1008 (WS) hasta expirar la
#: ventana. El valor numérico es diferible, aquí queda fijado y medible.
UMBRAL_FALLOS_AUTH = 20
VENTANA_FALLOS_AUTH = 60.0

#: Cabeceras que delatan reenvío / reverse proxy (B9.59 §11: reenvío == LAN).
CABECERAS_REENVIO = ("x-forwarded-for", "forwarded", "x-forwarded-host")

#: Las 9 capabilities cerradas de B9.59 §7 / B9.60 §7. Nada fuera de este
#: conjunto es una capability conocida; cualquier otro nombre se deniega.
CAPABILIDADES: frozenset[str] = frozenset(
    {
        "READ_WORKSPACE",
        "WRITE_WORKSPACE",
        "EXECUTE_REMOTE",
        "MANAGE_TASKS",
        "MANAGE_DAEMON",
        "USE_PLUGINS",
        "USE_INTERACTIVE",
        "USE_CHAT",
        "WEBHOOK_RECEIVE",
    }
)


@dataclass(frozen=True)
class DeclaracionSuperficie:
    """Entrada cerrada de ``CAPABILITY_MAP`` para una superficie (ruta o tipo WS).

    - ``capacidad``: capability requerida (None si la superficie es pública
      mínima o de liveness); debe pertenecer a ``CAPABILIDADES``.
    - ``publica``: True si no exige credencial del gateway (páginas/health).
    - ``requiere_auth``: True si la credencial del gateway es obligatoria.
    - ``solo_loopback``: True si fuera de loopback se deniega (M1).
    """

    capacidad: str | None = None
    publica: bool = False
    requiere_auth: bool = False
    solo_loopback: bool = False


# ---------------------------------------------------------------------------
# Credential lifecycle (B9.59 §9, B9.60 §4)
# ---------------------------------------------------------------------------
# Los hooks de rotación no son credenciales: son el boundary de notificación
# que B9.61-D usará para marcar tasks pendientes ``cancelada-por-rotacion``;
# B9.61-A solo lo entrega como hook (cierre de WS), sin tocar la task queue.
_HOOKS_ROTACION: list[Callable[[], None]] = []
_CANDADO_HOOKS = threading.Lock()


def registrar_hook_rotacion(callback: Callable[[], None]) -> None:
    """Registra un callback a ejecutar tras una rotación de credencial."""
    with _CANDADO_HOOKS:
        _HOOKS_ROTACION.append(callback)


def _permisos_config_validos() -> bool:
    """True si el archivo de configuración tiene permisos seguros (0600).

    - Archivo ausente: no es un problema de permisos (lo resuelve el estado
      de ausencia); devuelve True.
    - Unix: ``st_mode & 0o077 != 0`` → inválido (fail-closed).
    - Windows: ``os.chmod`` es limitado (mismo criterio que
      ``configuracion._asegurar_permisos_config``); se acepta y la protección
      real depende de las ACLs del usuario.
    """
    import configuracion

    ruta = configuracion.CONFIG_PATH
    try:
        estado = ruta.stat()
    except FileNotFoundError:
        return True
    except OSError:
        return False  # estado ambiguo → no se confía (fail-closed)
    if os.name == "nt":
        return True
    return (estado.st_mode & 0o077) == 0


def _credencial_config() -> tuple[str | None, str]:
    """Lee ``api_key`` de la configuración (mismo CONFIG_PATH que
    ``configuracion.cargar_configuracion``).

    Devuelve ``(valor, estado)`` con estado en
    ``{"valida", "ausente", "corrupta", "permisos"}``; distingue así el
    arranque inicial (puede generar) de la autenticación runtime (nunca).
    """
    import configuracion

    ruta = configuracion.CONFIG_PATH
    try:
        if not ruta.exists():
            return None, "ausente"
        if not ruta.is_file():
            return None, "corrupta"
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "ausente"
    except (OSError, ValueError):
        return None, "corrupta"
    if not isinstance(datos, dict):
        return None, "corrupta"
    if "api_key" not in datos:
        return None, "ausente"
    valor = datos["api_key"]
    if not isinstance(valor, str) or len(valor.strip()) < MIN_LONGITUD_CREDENCIAL:
        return None, "corrupta"
    if not _permisos_config_validos():
        return None, "permisos"
    return valor.strip(), "valida"


def cargar_credencial(*, token_explicito: str | None = None) -> str | None:
    """Autenticación runtime: resuelve la credencial vigente (por request).

    **Nunca genera** una credencial; la ausencia o corrupción produce ``None``
    (→ 401/1008, fail-closed). Relee la configuración en cada llamada para
    que una rotación invalide la clave anterior de inmediato.

    Cadena de resolución: token explícito de arranque → override de entorno
    ``SNAPCONTEXT_API_KEY`` (sin persistir) → ``config.json``.
    """
    if token_explicito:
        return token_explicito
    entorno = os.environ.get("SNAPCONTEXT_API_KEY", "").strip()
    if entorno:
        return entorno
    valor, estado = _credencial_config()
    if estado == "permisos":
        logger.warning("Credencial denegada: permisos de config.json inseguros (se exige 0600).")
        return None
    if estado == "corrupta":
        logger.warning("Credencial denegada: api_key de config.json corrupta.")
        return None
    return valor  # "valida" → valor; "ausente" → None


def generar_credencial() -> str | None:
    """Genera ``secrets.token_urlsafe(32)``, la persiste con 0600 y la entrega
    UNA única vez por stdout (B9.59 §9).

    Devuelve ``None`` si no pudo persistirse o verificarse (fail-closed: no
    se entrega una credencial no almacenada de forma segura).
    """
    import configuracion

    clave = secrets.token_urlsafe(MIN_LONGITUD_CREDENCIAL)
    if not configuracion._actualizar_clave_configuracion("api_key", clave):
        logger.warning("No se pudo persistir la API key generada; credencial no usable.")
        return None
    if not _permisos_config_validos():
        logger.warning("No se garantizaron permisos 0600 para la API key; credencial no usable.")
        return None
    print(
        "🔑 Nueva API key generada y guardada en config.json (permisos 0600). "
        "Se muestra UNA sola vez; guárdala ahora:\n" + clave
    )
    return clave


def preparar_credencial_inicio(
    *, token_explicito: str | None = None, permitir_generar: bool
) -> str | None:
    """Initial setup (único punto de generación; se llama al construir la app).

    - Token explícito / override de entorno: se usa tal cual (advertencia si
      tiene <32 caracteres: se admite solo en loopback, B9.59 §9).
    - ``config.json`` válido → se usa.
    - Permisos inseguros → DENY sin regenerar (no se pisa el archivo; hasta
      corregir a 0600 toda superficie autenticada queda 401/1008).
    - Ausente/corrupta → generar solo si ``permitir_generar`` (arranque en
      loopback); en no-loopback se devuelve ``None`` y el arranque aborta.
    """
    if token_explicito:
        if len(token_explicito) < MIN_LONGITUD_CREDENCIAL:
            print("⚠ API key manual corta (<32): se admite solo en loopback; se rechazará fuera.")
        return token_explicito
    entorno = os.environ.get("SNAPCONTEXT_API_KEY", "").strip()
    if entorno:
        if len(entorno) < MIN_LONGITUD_CREDENCIAL:
            print(
                "⚠ SNAPCONTEXT_API_KEY corta (<32): se admite solo en loopback; se rechazará fuera."
            )
        return entorno
    valor, estado = _credencial_config()
    if estado == "valida":
        return valor
    if estado == "permisos":
        logger.warning(
            "Arranque con api_key DENEGADA: permisos de config.json inseguros (se exige 0600)."
        )
        return None
    if estado == "corrupta":
        print(
            "⚠ api_key de config.json corrupta o demasiado corta; se ignora "
            "(sin mostrar su contenido)."
        )
        if not permitir_generar:
            return None
    if not permitir_generar:
        return None
    return generar_credencial()


def verificar_credencial(proveerda: Any, esperada: str | None) -> bool:
    """Comparación de secretos en tiempo constante (``secrets.compare_digest``),
    fail-closed ante cualquier entrada no utilizable."""
    if not isinstance(proveerda, str) or not proveerda:
        return False
    if not esperada:
        return False
    try:
        return secrets.compare_digest(proveerda, esperada)
    except (TypeError, ValueError):
        return False


def rotar_credencial() -> str:
    """Rotación manual M1 (B9.59 §9 / B9.60 §4).

    Invalida la credencial anterior de inmediato (las próximas requests la
    rechazan por relectura), entrega la nueva UNA vez por stdout y ejecuta
    los hooks registrados (p. ej. cierre de WebSockets con la clave anterior
    en <60 s). La integración con tasks pendientes se hará vía estos hooks en
    B9.61-D (``cancelada-por-rotacion``); B9.61-A no toca la task queue.
    """
    clave = generar_credencial()
    if clave is None:
        raise RuntimeError("No se pudo rotar la credencial (persistencia fallida).")
    with _CANDADO_HOOKS:
        hooks = list(_HOOKS_ROTACION)
    for callback in hooks:
        try:
            callback()
        except Exception:  # un hook no debe impedir la rotación
            logger.error("Hook de rotación falló (sin detalles de la credencial).")
    return clave


# ---------------------------------------------------------------------------
# Clasificación de exposición / Host / Origin (B9.59 §8-§11, B9.60 §13-§14)
# ---------------------------------------------------------------------------
def _ip_desde_cliente(cliente: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Extrae la IP del par ASGI ``scope["client"]``; ``None`` si no es IP."""
    if not cliente:
        return None
    host = cliente[0] if isinstance(cliente, (tuple, list)) else str(cliente)
    if host == "testclient":
        return ipaddress.ip_address("127.0.0.1")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _hostname_desde_host(header_host: str | None) -> str | None:
    """Normaliza la cabecera ``Host`` a hostname (sin puerto, sin corchetes)."""
    if not header_host:
        return None
    nombre = header_host.strip().lower()
    if nombre.startswith("["):
        cierre = nombre.find("]")
        if cierre == -1:
            return None
        return nombre[1:cierre] or None
    if nombre.count(":") == 1:
        base, _, puerto = nombre.rpartition(":")
        if puerto.isdigit():
            return base or None
    return nombre or None


def es_loopback_hostname(header_host: str | None) -> bool:
    """True si el ``Host`` identifica loopback (``localhost``, ``testserver`` o IP loopback)."""
    nombre = _hostname_desde_host(header_host)
    if not nombre:
        return False
    if nombre in ("localhost", "testserver"):
        return True
    try:
        return ipaddress.ip_address(nombre).is_loopback
    except ValueError:
        return False


def es_bind_loopback(host: str) -> bool:
    """True si el bind declarado del servidor es loopback (127.0.0.1/::1/localhost)."""
    nombre = (host or "").strip().lower()
    if nombre == "localhost":
        return True
    try:
        return ipaddress.ip_address(nombre).is_loopback
    except ValueError:
        return False


def host_valido(cabeceras: Mapping[str, str]) -> bool:
    """TrustedHost básico M1 (B9.59 §11 / B9.60 §13 regla 4).

    Solo ``localhost``, ``testserver`` (ASGI in-process) e IP literales:
    bloquea DNS-rebinding (Host de un dominio ajeno) sin impedir el acceso
    por IP en LAN. Ausente o no local → inválido (fail-closed).
    """
    nombre = _hostname_desde_host(cabeceras.get("host"))
    if not nombre:
        return False
    if nombre in ("localhost", "testserver"):
        return True
    try:
        ipaddress.ip_address(nombre)
        return True
    except ValueError:
        return False


def clasificar_exposicion(cliente: Any, cabeceras: Mapping[str, str]) -> Exposicion:
    """Clasifica la exposición real del request (B9.59 §11, B9.60 §14).

    - Cualquier cabecera de reenvío (X-Forwarded-For/Forwarded/
      X-Forwarded-Host) → ``lan`` (reenvío == LAN, nunca privilegio loopback).
    - Cliente sin IP verificable → ``lan`` (fail-closed: nunca loopback).
    - IP loopback + Host local → ``loopback``; IP loopback + Host no-local →
      ``lan`` (Host no-local es indicio de exposición, B9.59 §11).
    - IP global → ``internet``; IP privada/reservada → ``lan``.
    """
    if any(cabeceras.get(nombre) for nombre in CABECERAS_REENVIO):
        return "lan"
    ip = _ip_desde_cliente(cliente)
    if ip is None:
        return "lan"
    if ip.is_loopback:
        return "loopback" if es_loopback_hostname(cabeceras.get("host")) else "lan"
    return "internet" if ip.is_global else "lan"


def origin_admitido(origin: str | None, host: str | None, exposicion: Exposicion) -> bool:
    """Reglas 1-3 de Origin del contrato B9.60 §13 (handshake WebSocket).

    1. Origin presente y coincidente (mismo host:puerto que ``Host``) → OK.
    2. Origin ausente → DENY si no-loopback; permitir+log si loopback
       (clientes no-navegadores envían handshake sin ``Origin``).
    3. Origin presente e inválido (``null``, sin netloc, esquema no web o
       desigual de ``Host``) → DENY siempre.
    """
    if not origin:
        permitido = exposicion == "loopback"
        if permitido:
            logger.warning(
                "Origin ausente en loopback: permitido con credencial válida (B9.59 §8(3))."
            )
        else:
            logger.warning("Origin ausente en no-loopback → DENY (B9.59 §8(3)).")
        return permitido
    origen = origin.strip()
    if origen.lower() == "null":
        logger.warning("Origin 'null' rechazado (B9.60 §13 regla 3).")
        return False
    try:
        partes = urlsplit(origen)
    except ValueError:
        logger.warning("Origin malformado rechazado (B9.60 §13 regla 3).")
        return False
    if partes.scheme not in ("http", "https") or not partes.netloc:
        logger.warning("Origin sin esquema/Host válido rechazado (B9.60 §13).")
        return False
    if not host:
        return False
    if partes.netloc.lower() != host.strip().lower():
        logger.warning(
            "Origin %r no coincide con Host %r → DENY (B9.60 §13 regla 3).",
            partes.netloc,
            host.strip(),
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Rate básico de autenticación (B9.59 §14; umbral documentado arriba)
# ---------------------------------------------------------------------------
class LimitadorFallos:
    """Ventana deslizante de fallos de autenticación por cliente."""

    def __init__(
        self,
        umbral: int = UMBRAL_FALLOS_AUTH,
        ventana: float = VENTANA_FALLOS_AUTH,
    ) -> None:
        self._umbral = umbral
        self._ventana = ventana
        self._fallos: dict[str, list[float]] = {}
        self._candado = threading.Lock()

    @staticmethod
    def clave_cliente(cliente: Any) -> str:
        ip = _ip_desde_cliente(cliente)
        return str(ip) if ip is not None else "desconocido"

    def _limpiar(self, clave: str, ahora: float) -> None:
        recientes = [t for t in self._fallos.get(clave, []) if ahora - t < self._ventana]
        if recientes:
            self._fallos[clave] = recientes
        else:
            self._fallos.pop(clave, None)

    def bloqueado(self, cliente: Any) -> bool:
        """True si el cliente superó el umbral de fallos en la ventana."""
        clave = self.clave_cliente(cliente)
        ahora = time.monotonic()
        with self._candado:
            self._limpiar(clave, ahora)
            return len(self._fallos.get(clave, [])) >= self._umbral

    def registrar_fallo(self, cliente: Any) -> None:
        clave = self.clave_cliente(cliente)
        ahora = time.monotonic()
        with self._candado:
            self._limpiar(clave, ahora)
            self._fallos.setdefault(clave, []).append(ahora)


# ---------------------------------------------------------------------------
# Política de capability (cerrada, default-deny) — B9.59 §7 / B9.60 §5-§7
# ---------------------------------------------------------------------------
def exigir_politica(decl: DeclaracionSuperficie, exposicion: Exposicion) -> None:
    """Aplica la política de una declaración; levanta ``HTTPException(403)``.

    - Capability desconocida (fuera de ``CAPABILIDADES``) → DENY.
    - Exposición ``internet`` → DENY en M1 (solo tras proxy declarado, que
      aparece como reenvío/LAN, B9.59 §4/§11).
    - ``solo_loopback`` fuera de loopback → DENY.
    """
    from fastapi import HTTPException

    if decl.capacidad is not None and decl.capacidad not in CAPABILIDADES:
        logger.warning("Capability desconocida en declaración → DENY (fail-closed).")
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    if exposicion == "internet":
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    if decl.solo_loopback and exposicion != "loopback":
        raise HTTPException(status_code=403, detail="Acceso denegado.")


def clave_de_ruta(ruta: Any) -> str:
    """Clave de una ruta registrada dentro de ``CAPABILITY_MAP``.

    WebSocket → ``"WS <path>"``; HTTP/Mount → ``<path>`` de la plantilla.
    """
    if isinstance(ruta, WebSocketRoute):
        return f"WS {ruta.path}"
    return str(getattr(ruta, "path", "") or "")


def tiene_proteccion_central(ruta: Any) -> bool:
    """True si la ruta fue registrada con la dependencia central de autorización."""
    for dependencia in getattr(ruta, "dependencies", None) or []:
        objetivo = getattr(dependencia, "dependency", dependencia)
        if getattr(objetivo, "_proteccion_central", False):
            return True
    return False


def _primera_ruta_full(rutas: Sequence[Any], scope: Any) -> Any:
    """Primera ruta que coincide FULL con el scope (misma semántica que el router)."""
    for ruta in rutas:
        try:
            coincidencia, _ = ruta.matches(scope)
        except Exception:  # ruta exótica → no cubierta; se evalúa aparte
            continue
        if coincidencia == Match.FULL:
            return ruta
    return None


class MiddlewareSeguridad:
    """Cobertura de superficies + TrustedHost + log de exposición.

    Envuelve el router (HTTP y WebSocket) y garantiza la invariancia de
    B9.59 §7: **toda** ruta registrada debe tener entrada en
    ``CAPABILITY_MAP``; una ruta nueva sin declaración es denegada (403/1008)
    antes de llegar a su handler, y una ruta autenticada que no esté cableada
    a la dependencia central también (fail-closed, sin fallback).
    """

    def __init__(
        self,
        app: Any,
        *,
        mapa: Mapping[str, DeclaracionSuperficie],
        obtener_rutas: Callable[[], Sequence[Any]],
    ) -> None:
        self.app = app
        self.mapa = mapa
        self._obtener_rutas = obtener_rutas

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        cabeceras = Headers(scope=scope)
        if not host_valido(cabeceras):
            await self._denegar(scope, receive, send, "Host no permitido.", 400, 1008)
            return
        exposicion = clasificar_exposicion(scope.get("client"), cabeceras)
        if exposicion != "loopback":
            logger.warning(
                "EXPOSING/NON-LOOPBACK-EQUIVALENT exposicion=%s ruta=%s",
                exposicion,
                scope.get("path", ""),
            )
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        ruta = _primera_ruta_full(self._obtener_rutas(), scope)
        if ruta is None:
            await self.app(scope, receive, send)  # sin coincidencia → 404/1008 del router
            return
        decl = self.mapa.get(clave_de_ruta(ruta))
        if decl is None:
            # Ruta desconocida para el mapa cerrado → denegar (invariancia).
            await self._denegar(scope, receive, send, "Acceso denegado.", 403, 1008)
            return
        if scope["type"] == "websocket":
            # En WS la autenticación es pre-accept en el handler; aquí solo
            # cobertura + política de exposición de la superficie (handshake).
            if exposicion == "internet" or (decl.solo_loopback and exposicion != "loopback"):
                await self._denegar(scope, receive, send, "No autorizado.", 403, 1008)
                return
            await self.app(scope, receive, send)
            return
        # HTTP: toda superficie autenticada debe estar cableada al centro.
        central = tiene_proteccion_central(ruta)
        if decl.requiere_auth and not central:
            await self._denegar(scope, receive, send, "Acceso denegado.", 403, 1008)
            return
        if not central:
            # Superficies sin dependencia central (docs, estáticos): la
            # política de exposición se aplica aquí mismo.
            if exposicion == "internet" or (decl.solo_loopback and exposicion != "loopback"):
                await self._denegar(scope, receive, send, "Acceso denegado.", 403, 1008)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _denegar(
        scope: Any,
        receive: Any,
        send: Any,
        detalle: str,
        codigo_http: int,
        codigo_ws: int,
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": codigo_ws, "reason": ""})
            return
        respuesta = JSONResponse({"detail": detalle}, status_code=codigo_http)
        await respuesta(scope, receive, send)
