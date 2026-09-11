#!/usr/bin/env python3
"""Cliente MCP estándar — conexión con servidores MCP externos (Fase 3).

Implementa la parte cliente del Model Context Protocol (MCP) sobre el
transporte stdio: cada servidor MCP es un proceso lanzado con
``subprocess.Popen`` (lista de argumentos, sin ``shell=True``) con el que se
habla JSON-RPC 2.0 por stdin/stdout.

Protocolo soportado (documentado en ``docs/MCP.md``):
  - Versión del protocolo: ``2024-11-05`` (initialize/initialized).
  - ``tools/list`` → catálogo de herramientas del servidor.
  - ``tools/call`` → ejecución de una herramienta.

Configuración: ``~/.snapcontext/mcp_servers.json`` (o ``mcp_servers.json``
en la raíz del proyecto), formato::

    {
      "servers": {
        "filesystem": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/ruta"],
          "env": {},
          "enabled": true
        }
      }
    }

Degradación elegante: un servidor que no arranca (comando inexistente,
handshake fallido) genera un ``aviso`` y queda marcado como caído para no
reintentarlo en la misma sesión; el resto del sistema sigue funcionando.

Las herramientas externas se exponen al agente con el prefijo
``mcp_<servidor>_<herramienta>`` para evitar colisiones con las nativas.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from presentacion import aviso, depurar, info

# Versión del protocolo MCP negociada en el handshake. Si el estándar
# evoluciona, actualizar aquí (el servidor puede responder con otra versión;
# se registra en ``versiones_negociadas`` para depuración).
PROTOCOLO_MCP = "2024-11-05"

_NOMBRE_FICHERO_CONFIG = "mcp_servers.json"


def ruta_config_servidores(raiz: Path | None = None) -> Path:
    """Ruta del fichero de servidores MCP (proyecto si existe, si no global)."""
    if raiz is not None:
        candidata = Path(raiz) / _NOMBRE_FICHERO_CONFIG
        if candidata.is_file():
            return candidata
    import snapcontext

    return Path(snapcontext.CONFIG_DIR) / _NOMBRE_FICHERO_CONFIG


def _leer_config(ruta: Path | None = None) -> dict:
    """Lee ``mcp_servers.json``; devuelve ``{servers: {}}`` si no existe/corrupto."""
    ruta = ruta or ruta_config_servidores()
    if not ruta.is_file():
        return {"servers": {}}
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if isinstance(datos, dict) and isinstance(datos.get("servers"), dict):
            return datos
        aviso(f"[mcp] {ruta} no tiene el formato esperado (dict con 'servers'); se ignora.")
        return {"servers": {}}
    except (json.JSONDecodeError, OSError) as exc:
        aviso(f"[mcp] No se pudo leer {ruta}: {exc}")
        return {"servers": {}}


def _guardar_config(datos: dict, ruta: Path | None = None) -> Path:
    """Escribe ``mcp_servers.json`` (crea el directorio si hace falta)."""
    ruta = ruta or ruta_config_servidores()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ruta


def servers_configurados(raiz: Path | None = None) -> dict:
    """Devuelve solo los servidores habilitados del fichero de configuración."""
    datos = _leer_config(ruta_config_servidores(raiz) if raiz else None)
    return {
        nombre: cfg
        for nombre, cfg in datos.get("servers", {}).items()
        if isinstance(cfg, dict) and cfg.get("enabled", True) and cfg.get("command")
    }


class MCPClient:
    """Cliente JSON-RPC sobre stdio para uno o más servidores MCP.

    Uso típico::

        cliente = MCPClient()          # lee la configuración global
        tools = cliente.list_tools()   # {servidor: [herramientas]}
        res = cliente.call_tool("filesystem", "read_file", {"ruta": "a.py"})
        cliente.cerrar()
    """

    def __init__(self, config: dict | None = None):
        # ``config`` opcional: {servidor: {command, args, env, enabled}}; si
        # se omite se lee el fichero de configuración (tests la parchean).
        if config is None:
            config = servers_configurados()
        self._config = {
            n: c for n, c in (config or {}).items() if isinstance(c, dict) and c.get("command")
        }
        self._procesos: dict[str, subprocess.Popen] = {}
        self._herramientas: dict[str, list] = {}
        self._caidos: set[str] = set()  # servidores que fallaron en esta sesión
        self._siguiente_id = 1
        self.versiones_negociadas: dict[str, str | None] = {}

    # --- JSON-RPC ----------------------------------------------------------
    def _peticion(self, servidor: str, metodo: str, params: dict | None = None) -> dict:
        """Envía una petición JSON-RPC y devuelve el ``result`` de la respuesta."""
        proceso = self._asegurar_proceso(servidor)
        if proceso is None:
            raise RuntimeError(f"servidor MCP '{servidor}' no disponible")
        id_peticion = self._siguiente_id
        self._siguiente_id += 1
        mensaje = {
            "jsonrpc": "2.0",
            "id": id_peticion,
            "method": metodo,
            "params": params or {},
        }
        self._enviar(servidor, mensaje)
        respuesta = self._esperar_respuesta(servidor, id_peticion)
        if "error" in respuesta:
            raise RuntimeError(f"MCP {metodo} falló: {respuesta['error']}")
        return respuesta.get("result") or {}

    def _enviar(self, servidor: str, mensaje: dict) -> None:
        """Serializa ``mensaje`` y lo escribe como una línea JSON en stdin."""
        proceso = self._procesos[servidor]
        try:
            proceso.stdin.write(json.dumps(mensaje, ensure_ascii=False) + "\n")
            proceso.stdin.flush()
        except (OSError, ValueError) as exc:
            self._caidos.add(servidor)
            raise RuntimeError(f"servidor MCP '{servidor}' no acepta entrada: {exc}") from exc

    def _esperar_respuesta(self, servidor: str, id_peticion: int) -> dict:
        """Lee líneas de stdout hasta recibir la respuesta con ``id_peticion``.

        Las notificaciones (mensajes sin ``id``) se descartan. Si el proceso
        muere o el stdout se cierra, se marca el servidor como caído.
        """
        proceso = self._procesos[servidor]
        while True:
            linea = proceso.stdout.readline()
            if not linea:
                self._caidos.add(servidor)
                raise RuntimeError(f"servidor MCP '{servidor}' cerró stdout sin responder")
            try:
                mensaje = json.loads(linea)
            except json.JSONDecodeError:
                depurar(f"[mcp:{servidor}] línea no-JSON ignorada: {linea[:120]!r}")
                continue
            if mensaje.get("id") == id_peticion:
                return mensaje
            # Notificación (sin id) o respuesta ajena: se descarta.

    # --- Gestión de procesos ----------------------------------------------
    def _asegurar_proceso(self, servidor: str) -> subprocess.Popen | None:
        """Lanza el servidor si hace falta (con handshake); None si falla."""
        if servidor in self._procesos:
            return self._procesos[servidor]
        if servidor in self._caidos:
            return None
        cfg = self._config.get(servidor)
        if cfg is None:
            return None
        comando = [str(cfg["command"]), *[str(a) for a in (cfg.get("args") or [])]]
        try:
            proceso = subprocess.Popen(
                comando,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, **(cfg.get("env") or {})},
            )
        except (OSError, ValueError) as exc:
            aviso(f"[mcp] No se pudo lanzar el servidor '{servidor}': {exc}")
            self._caidos.add(servidor)
            return None
        self._procesos[servidor] = proceso
        try:
            self._handshake(servidor)
        except (RuntimeError, json.JSONDecodeError) as exc:
            aviso(f"[mcp] Handshake fallido con '{servidor}': {exc}")
            self._terminar(servidor)
            self._caidos.add(servidor)
            return None
        return proceso

    def _handshake(self, servidor: str) -> None:
        """initialize → respuesta → notifications/initialized."""
        resultado = self._peticion(
            servidor,
            "initialize",
            {
                "protocolVersion": PROTOCOLO_MCP,
                "capabilities": {},
                "clientInfo": {"name": "snapcontext", "version": "6.34.15"},
            },
        )
        self.versiones_negociadas[servidor] = (resultado or {}).get("protocolVersion")
        # Notificación de initialized (sin id: no espera respuesta).
        self._enviar(servidor, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _terminar(self, servidor: str) -> None:
        """Cierra stdin y termina el proceso del servidor (cierre ordenado)."""
        proceso = self._procesos.pop(servidor, None)
        if proceso is None:
            return
        try:
            if proceso.stdin:
                proceso.stdin.close()
        except OSError:
            pass
        try:
            proceso.terminate()
            proceso.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proceso.kill()
        for tubo in (proceso.stdout, proceso.stderr):
            try:
                if tubo:
                    tubo.close()
            except OSError:
                pass

    def cerrar(self) -> None:
        """Cierra todos los servidores activos (llamable varias veces)."""
        for servidor in list(self._procesos):
            self._terminar(servidor)

    # --- API pública MCP ---------------------------------------------------
    def list_tools(self) -> dict:
        """``tools/list`` de cada servidor → ``{servidor: [herramientas]}``.

        Un servidor caído aparece como ``[]`` (degradación elegante, sin
        romper el resto).
        """
        catalogo: dict[str, list] = {}
        for servidor in self._config:
            if servidor in self._caidos:
                catalogo[servidor] = []
                continue
            try:
                resultado = self._peticion(servidor, "tools/list")
                herramientas = (resultado or {}).get("tools") or []
                self._herramientas[servidor] = herramientas
                catalogo[servidor] = herramientas
            except (RuntimeError, json.JSONDecodeError) as exc:
                aviso(f"[mcp] No se pudieron listar herramientas de '{servidor}': {exc}")
                self._caidos.add(servidor)
                catalogo[servidor] = []
        return catalogo

    def call_tool(self, servidor: str, herramienta: str, argumentos: dict | None = None) -> dict:
        """``tools/call`` → ``{"ok": bool, "contenido": ..., "error": ...}``."""
        try:
            resultado = self._peticion(
                servidor,
                "tools/call",
                {"name": herramienta, "arguments": argumentos or {}},
            )
        except (RuntimeError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc), "contenido": ""}
        contenido = ""
        bloque = (resultado or {}).get("content") or []
        for parte in bloque:
            if isinstance(parte, dict) and parte.get("type") == "text":
                contenido += str(parte.get("text") or "")
        fallo = bool((resultado or {}).get("isError"))
        return {"ok": not fallo, "contenido": contenido, "error": None if not fallo else contenido}

    # --- Integración con el dispatcher ------------------------------------
    def herramientas_aplanadas(self) -> dict:
        """Todas las herramientas externas con prefijo ``mcp_<servidor>_<tool>``.

        Lanza los servidores pendientes (list_tools) y devuelve el registro
        listo para mezclar en ``_cargar_herramientas_mcp``.
        """
        registro: dict[str, dict] = {}
        for servidor, herramientas in self.list_tools().items():
            for h in herramientas:
                nombre = str(h.get("name") or "").strip()
                if not nombre:
                    continue
                registro[f"mcp_{servidor}_{nombre}"] = {
                    "descripcion": str(h.get("description") or f"[mcp:{servidor}] {nombre}"),
                    "parametros": {},
                    "requiere_permiso": True,  # seguro por defecto
                    "mcp_externa": True,
                    "servidor": servidor,
                    "herramienta": nombre,
                }
        return registro


# --- Instancia compartida (una sola por proceso) ----------------------------
_CLIENTE: MCPClient | None = None


def cliente_compartido() -> MCPClient:
    """Cliente MCP global (se crea una vez; los procesos viven con la app)."""
    global _CLIENTE
    if _CLIENTE is None:
        _CLIENTE = MCPClient()
    return _CLIENTE


def cerrar_cliente() -> None:
    """Cierra el cliente compartido (cierre de la app / tests)."""
    global _CLIENTE
    if _CLIENTE is not None:
        _CLIENTE.cerrar()
        _CLIENTE = None


def ejecutar_herramienta_externa(nombre: str, argumentos: dict | None = None) -> dict:
    """Ejecuta una herramienta con prefijo ``mcp_<servidor>_<herramienta>``.

    Usada por el dispatcher de ``mcp_tools.py``. Devuelve el resultado
    estructurado estándar ``{"ok", "herramienta", "resultado", "error"}``.
    """
    partes = nombre.split("_", 2)
    if len(partes) < 3 or partes[0] != "mcp":
        return {"ok": False, "herramienta": nombre, "error": f"prefijo inválido: {nombre}"}
    servidor, herramienta = partes[1], partes[2]
    resultado = cliente_compartido().call_tool(servidor, herramienta, argumentos)
    return {
        "ok": bool(resultado.get("ok")),
        "herramienta": nombre,
        "resultado": resultado,
    }


def _cargar_herramientas_mcp_externas() -> dict:
    """Registro de herramientas externas (prefijadas) para el dispatcher.

    Devuelve ``{}`` si no hay servidores configurados o si todos fallan al
    arrancar (degradación elegante: nunca lanza).
    """
    try:
        if not servers_configurados():
            return {}
        return cliente_compartido().herramientas_aplanadas()
    except Exception as exc:  # blindaje total del agente
        aviso(f"[mcp] No se pudieron cargar servidores MCP externos: {exc}")
        return {}


# --- CLI: snapcontext mcp <list|add|remove> ---------------------------------
def _mcp_accion_add(subargv: list[str], ruta: Path) -> int:
    """`snapcontext mcp add <nombre> <comando> [args...]`."""
    if len(subargv) < 3:
        aviso("Uso: snapcontext mcp add <nombre> <comando> [args...]")
        return 1
    nombre, comando, argumentos = subargv[1], subargv[2], subargv[3:]
    datos = _leer_config(ruta)
    datos.setdefault("servers", {})[nombre] = {
        "command": comando,
        "args": argumentos,
        "env": {},
        "enabled": True,
    }
    _guardar_config(datos, ruta)
    info(f"Servidor MCP '{nombre}' añadido en {ruta}.")
    return 0


def _mcp_accion_remove(subargv: list[str], ruta: Path) -> int:
    """`snapcontext mcp remove <nombre>`."""
    if len(subargv) < 2:
        aviso("Uso: snapcontext mcp remove <nombre>")
        return 1
    nombre = subargv[1]
    datos = _leer_config(ruta)
    if nombre not in datos.get("servers", {}):
        aviso(f"'{nombre}' no está en {ruta}.")
        return 1
    del datos["servers"][nombre]
    _guardar_config(datos, ruta)
    info(f"Servidor MCP '{nombre}' eliminado.")
    return 0


def _mcp_accion_list(ruta: Path) -> int:
    """`snapcontext mcp list` — servidores y herramientas disponibles."""
    datos = _leer_config(ruta)
    servidores = datos.get("servers", {})
    if not servidores:
        info(f"No hay servidores MCP configurados ({ruta}).")
        info(
            "Ejemplo: snapcontext mcp add filesystem npx -y "
            "@modelcontextprotocol/server-filesystem /ruta/al/proyecto"
        )
        return 0
    info(f"Servidores MCP ({ruta}):")
    activos = servers_configurados()
    for nombre, cfg in servidores.items():
        estado = "habilitado" if nombre in activos else "deshabilitado"
        info(f"  {nombre} [{estado}] → {cfg.get('command')} {' '.join(cfg.get('args') or [])}")
    # Herramientas: lanza los servidores habilitados (degradación elegante).
    if activos:
        info("Herramientas disponibles:")
        cliente = cliente_compartido()
        for nombre_h, herramientas in cliente.list_tools().items():
            if herramientas:
                for h in herramientas:
                    info(f"  mcp_{nombre_h}_{h.get('name')} — {h.get('description') or ''}")
            else:
                info(f"  ({nombre_h}: sin herramientas — servidor caído o vacío)")
        cliente.cerrar()
        cerrar_cliente()
    return 0


def _ejecutar_comando_mcp(subargv: list[str]) -> int:
    """Despacha ``snapcontext mcp <accion> [...]`` (Fase 3)."""
    accion = (subargv[0] if subargv else "list").strip().lower()
    if accion in ("-h", "--help", "help"):
        info(
            "Uso: snapcontext mcp <list|add|remove> [...]\n"
            "  list                      → servidores configurados y sus herramientas\n"
            "  add <nombre> <cmd> [args...] → añade un servidor (habilitado)\n"
            "  remove <nombre>           → elimina un servidor"
        )
        return 0
    ruta = ruta_config_servidores()
    if accion == "add":
        return _mcp_accion_add(subargv, ruta)
    if accion == "remove":
        return _mcp_accion_remove(subargv, ruta)
    if accion != "list":
        aviso(f"Acción MCP desconocida: '{accion}'. Usa list, add o remove.")
        return 1
    return _mcp_accion_list(ruta)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_ejecutar_comando_mcp(sys.argv[1:]))
