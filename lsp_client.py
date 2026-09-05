#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cliente LSP (Language Server Protocol) de SnapContext — v6.14.0.

Permite al agente resolver referencias globales del código mediante un
servidor LSP real (pyright/pylsp para Python, gopls para Go, tsserver para
TypeScript, rust-analyzer para Rust, jdtls para Java, OmniSharp para C#):

  - ``obtener_definicion``  : dónde se define un símbolo.
  - ``obtener_referencias`` : todos los lugares que lo usan.
  - ``obtener_tipo``        : tipo de una variable/expresión (hover).

Es 100 % opcional (``--lsp``): sin el flag el comportamiento de SnapContext
es idéntico. La comunicación es JSON-RPC 2.0 sobre stdio (framing
``Content-Length``), implementada con la librería estándar: no se necesita
``pygls``. Incluye caché en memoria + SQLite (``~/.snapcontext/lsp_cache.db``)
invalidada por hash del archivo. Si el servidor no está instalado o falla,
se informa con claridad y se continúa sin LSP.
"""

from __future__ import annotations

import hashlib
import json
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Detección de lenguaje y comandos de servidores
# ---------------------------------------------------------------------------
MAPEO_EXTENSIONES: Dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".cs": "csharp", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
}

# Candidatos por lenguaje (el primero instalado gana). Comandos seguros y de
# servidor conocidos: nunca se construyen a partir de entrada del usuario.
COMANDOS_SERVIDOR: Dict[str, List[List[str]]] = {
    "python": [["pyright-langserver", "--stdio"], ["pylsp"]],
    "javascript": [["typescript-language-server", "--stdio"],
                   ["javascript-typescript-stdio"]],
    "typescript": [["typescript-language-server", "--stdio"]],
    "go": [["gopls"]],
    "rust": [["rust-analyzer"]],
    "java": [["jdtls"]],
    "csharp": [["OmniSharp", "-lsp"]],
    "c": [["clangd"]],
    "cpp": [["clangd"]],
}

TIMEOUT_DEFECTO = 15.0  # segundos por petición LSP


def _detectar_lenguaje_por_extension(archivo: str) -> Optional[str]:
    """Mapea la extensión de ``archivo`` a un nombre de lenguaje LSP."""
    return MAPEO_EXTENSIONES.get(Path(str(archivo)).suffix.lower())


def _comando_servidor(lenguaje: str) -> Optional[List[str]]:
    """Devuelve el primer comando de servidor LSP instalado, o None.

    Comprueba con ``shutil.which`` que el ejecutable exista en el PATH.
    """
    for candidatos in COMANDOS_SERVIDOR.get(str(lenguaje), []):
        if shutil.which(candidatos[0]):
            return list(candidatos)
    return None


def _hash_archivo(archivo: str) -> str:
    """Hash MD5 del contenido del archivo (invalidación de caché)."""
    try:
        contenido = Path(archivo).read_bytes()
    except OSError:
        return "desconocido"
    return hashlib.md5(contenido).hexdigest()          # noqa: S324


class CacheLSP:
    """Caché de consultas LSP en memoria + SQLite (invalidada por hash)."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.memoria: Dict[tuple, dict] = {}
        self.db_path = Path(db_path) if db_path else _ruta_cache()
        self._mutex = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(self.db_path))
            with con:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS lsp_cache (
                        clave TEXT PRIMARY KEY,
                        hash_archivo TEXT NOT NULL,
                        respuesta TEXT NOT NULL,
                        creado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
            con.close()
        except (sqlite3.Error, OSError):
            # Sin caché persistente se sigue solo con la memoria.
            self.db_path = None                        # type: ignore[assignment]

    @staticmethod
    def _clave(archivo: str, linea: int, columna: int, tipo: str) -> tuple:
        return (str(archivo), int(linea), int(columna), str(tipo))

    def obtener(self, archivo: str, linea: int, columna: int,
                tipo: str) -> Optional[dict]:
        clave = self._clave(archivo, linea, columna, tipo)
        with self._mutex:
            if clave in self.memoria:
                entrada = self.memoria[clave]
                if entrada["hash"] == _hash_archivo(archivo):
                    return dict(entrada["respuesta"])
                del self.memoria[clave]
        if not self.db_path:
            return None
        try:
            con = sqlite3.connect(str(self.db_path))
            fila = con.execute(
                "SELECT hash_archivo, respuesta FROM lsp_cache WHERE clave = ?",
                (json.dumps(list(clave), ensure_ascii=False),)).fetchone()
            con.close()
        except sqlite3.Error:
            return None
        if not fila:
            return None
        if fila[0] != _hash_archivo(archivo):
            self.invalidar(archivo, linea, columna, tipo)
            return None
        try:
            return dict(json.loads(fila[1]))
        except (json.JSONDecodeError, TypeError):
            return None

    def guardar(self, archivo: str, linea: int, columna: int, tipo: str,
                respuesta: dict) -> None:
        clave = self._clave(archivo, linea, columna, tipo)
        hash_actual = _hash_archivo(archivo)
        with self._mutex:
            self.memoria[clave] = {"hash": hash_actual,
                                   "respuesta": dict(respuesta)}
        if not self.db_path:
            return
        try:
            con = sqlite3.connect(str(self.db_path))
            with con:
                con.execute(
                    "INSERT OR REPLACE INTO lsp_cache "
                    "(clave, hash_archivo, respuesta) VALUES (?, ?, ?)",
                    (json.dumps(list(clave), ensure_ascii=False),
                     hash_actual, json.dumps(respuesta, ensure_ascii=False)))
            con.close()
        except sqlite3.Error:
            pass

    def invalidar(self, archivo: str, linea: int, columna: int,
                  tipo: str) -> None:
        clave = self._clave(archivo, linea, columna, tipo)
        with self._mutex:
            self.memoria.pop(clave, None)
        if not self.db_path:
            return
        try:
            con = sqlite3.connect(str(self.db_path))
            with con:
                con.execute("DELETE FROM lsp_cache WHERE clave = ?",
                            (json.dumps(list(clave), ensure_ascii=False),))
            con.close()
        except sqlite3.Error:
            pass

    def limpiar(self) -> int:
        """Borra toda la caché (memoria y disco); devuelve elementos borrados."""
        with self._mutex:
            borrados = len(self.memoria)
            self.memoria.clear()
        if self.db_path:
            try:
                con = sqlite3.connect(str(self.db_path))
                with con:
                    cur = con.execute("DELETE FROM lsp_cache")
                    borrados += cur.rowcount
                con.close()
            except sqlite3.Error:
                pass
        return borrados


# ---------------------------------------------------------------------------
# Cliente LSP (JSON-RPC 2.0 sobre stdio)
# ---------------------------------------------------------------------------
class LSPClient:
    """Cliente LSP: lanza el servidor, habla JSON-RPC y cachea consultas.

    Uso::

        with LSPClient() as cliente:
            if cliente.iniciar("python", "."):
                cliente.obtener_definicion("mod.py", 10, 4)
    """

    def __init__(self, cache: Optional[CacheLSP] = None,
                 timeout: float = TIMEOUT_DEFECTO) -> None:
        self.lenguaje: Optional[str] = None
        self.ruta_proyecto: Optional[str] = None
        self.proceso: Optional[subprocess.Popen] = None
        self.contador_peticiones = 0
        self.cache = cache if cache is not None else CacheLSP()
        self.timeout = float(timeout)
        self._cola: "queue.Queue[dict]" = queue.Queue()
        self._lector: Optional[threading.Thread] = None
        self._abierto = False

    # -- Ciclo de vida -----------------------------------------------------
    def iniciar(self, lenguaje: Optional[str] = None,
                ruta_proyecto: str = ".") -> bool:
        """Detecta y lanza el servidor LSP (perezoso). True si está listo."""
        import snapcontext as sc                       # noqa: E402
        lenguaje = lenguaje or self.lenguaje or "python"
        comando = _comando_servidor(lenguaje)
        if not comando:
            sc.aviso(f"LSP no disponible para este lenguaje ({lenguaje}): "
                     "no se encontró ningún servidor instalado.")
            return False
        sc.info(f"Conectando a LSP para {lenguaje}...")
        try:
            self._arrancar_proceso(comando, ruta_proyecto)
        except (OSError, subprocess.SubprocessError) as exc:
            sc.aviso(f"LSP: no se pudo lanzar '{comando[0]}': {exc}")
            return False
        self.lenguaje = lenguaje
        self.ruta_proyecto = str(Path(ruta_proyecto).resolve())
        if not self._inicializar_sesion():
            sc.aviso("LSP: el servidor no respondió al 'initialize'.")
            self.cerrar()
            return False
        self._abierto = True
        sc.exito("LSP listo.")
        return True

    def _arrancar_proceso(self, comando: List[str],
                          ruta_proyecto: str) -> None:
        """Lanza el subproceso del servidor (separado para poder mockear)."""
        self.proceso = subprocess.Popen(               # noqa: S603
            comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(Path(ruta_proyecto).resolve()),
            bufsize=0)
        self._lector = threading.Thread(target=self._bucle_lector,
                                        daemon=True)
        self._lector.start()

    def _bucle_lector(self) -> None:
        """Lee mensajes con framing Content-Length y los mete en la cola."""
        assert self.proceso is not None and self.proceso.stdout is not None
        flujo = self.proceso.stdout
        try:
            while True:
                longitudes: Dict[str, int] = {}
                while True:
                    linea = flujo.readline()
                    if not linea:
                        return
                    texto = linea.decode("utf-8", "replace").strip()
                    if not texto:
                        break                      # fin de cabeceras
                    if ":" in texto:
                        clave, valor = texto.split(":", 1)
                        longitudes[clave.strip().lower()] = valor.strip()
                total = int(longitudes.get("content-length", 0))
                if total <= 0:
                    continue
                cuerpo = flujo.read(total)
                try:
                    mensaje = json.loads(cuerpo.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                self._cola.put(mensaje)
        except (OSError, ValueError):
            return

    def _inicializar_sesion(self) -> bool:
        """Handshake initialize/initialized + didOpen implícito por consulta."""
        raiz = self.ruta_proyecto or "."
        respuesta = self.enviar_peticion("initialize", {
            "processId": None,
            "rootUri": Path(raiz).as_uri(),
            "capabilities": {},
        }, usar_cache=False)
        if respuesta is None:
            return False
        self.enviar_notificacion("initialized", {})
        return True

    def __enter__(self) -> "LSPClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.cerrar()

    # -- Transporte JSON-RPC ------------------------------------------------
    def _siguiente_id(self) -> int:
        self.contador_peticiones += 1
        return self.contador_peticiones

    def _enviar_mensaje(self, mensaje: dict) -> None:
        """Escribe un mensaje con framing ``Content-Length`` en stdin."""
        assert self.proceso is not None and self.proceso.stdin is not None
        cuerpo = json.dumps(mensaje, ensure_ascii=False).encode("utf-8")
        try:
            self.proceso.stdin.write(
                f"Content-Length: {len(cuerpo)}\r\n\r\n".encode("ascii")
                + cuerpo)
            self.proceso.stdin.flush()
        except (OSError, ValueError):
            pass

    def enviar_notificacion(self, metodo: str, params: dict) -> None:
        """Envía una notificación JSON-RPC (sin respuesta esperada)."""
        self._enviar_mensaje({"jsonrpc": "2.0", "method": metodo,
                              "params": params})

    def enviar_peticion(self, metodo: str, params: dict,
                        usar_cache: bool = False,
                        clave_cache: Optional[tuple] = None) -> Optional[dict]:
        """Envía una petición JSON-RPC y espera su respuesta (con timeout).

        Devuelve el campo ``result`` o ``None`` si hay error/timeout. Con
        ``usar_cache`` consulta/escribe la caché usando ``clave_cache``.
        """
        if usar_cache and clave_cache is not None:
            cacheado = self.cache.obtener(*clave_cache)
            if cacheado is not None:
                return cacheado
        if self.proceso is None or self.proceso.poll() is not None:
            return None
        peticion_id = self._siguiente_id()
        self._enviar_mensaje({"jsonrpc": "2.0", "id": peticion_id,
                              "method": metodo, "params": params})
        limite = time.monotonic() + self.timeout
        while time.monotonic() < limite:
            restante = max(0.05, limite - time.monotonic())
            try:
                mensaje = self._cola.get(timeout=restante)
            except queue.Empty:
                break
            if mensaje.get("id") != peticion_id:
                continue                          # notificación/otra petición
            if "error" in mensaje:
                return None
            resultado = mensaje.get("result")
            if usar_cache and clave_cache is not None and resultado is not None:
                self.cache.guardar(*clave_cache, dict(resultado))
            return resultado if isinstance(resultado, dict) else None
        return None

    # -- Consultas de alto nivel ---------------------------------------------
    def _preparar_archivo(self, archivo: str) -> Optional[dict]:
        """Valida el archivo, detecta el lenguaje y hace ``didOpen``."""
        ruta = Path(archivo).resolve()
        if not ruta.exists():
            return None
        lenguaje = _detectar_lenguaje_por_extension(str(ruta))
        if not lenguaje:
            return None
        if lenguaje != self.lenguaje:
            # El servidor actual no corresponde: reinicio perezoso.
            if not self.iniciar(lenguaje, self.ruta_proyecto or "."):
                return None
        try:
            texto = ruta.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        self.enviar_notificacion("textDocument/didOpen", {
            "textDocument": {
                "uri": ruta.as_uri(), "languageId": lenguaje,
                "version": 1, "text": texto}})
        return {"uri": ruta.as_uri(), "ruta": str(ruta), "texto": texto}

    def obtener_definicion(self, archivo: str, linea: int,
                           columna: int) -> Optional[dict]:
        """Resuelve dónde se define el símbolo en (linea, columna) 1-based.

        Devuelve ``{archivo, linea, columna}`` o ``None``.
        """
        return self._consulta_posicional(
            "textDocument/definition", archivo, linea, columna, "definicion")

    def obtener_referencias(self, archivo: str, linea: int,
                            columna: int) -> Optional[dict]:
        """Todas las referencias al símbolo (incluida la declaración).

        Devuelve ``{referencias: [...], total}`` o ``None``.
        """
        return self._consulta_posicional(
            "textDocument/references", archivo, linea, columna,
            "referencias", contexto=True)

    def obtener_tipo(self, archivo: str, linea: int,
                     columna: int) -> Optional[dict]:
        """Tipo de la variable/expresión (hover). Devuelve ``{tipo}``."""
        return self._consulta_posicional(
            "textDocument/hover", archivo, linea, columna, "tipo")

    def _consulta_posicional(self, metodo: str, archivo: str, linea: int,
                             columna: int, tipo: str,
                             contexto: bool = False) -> Optional[dict]:
        clave = (str(archivo), int(linea), int(columna), tipo)
        doc = self._preparar_archivo(archivo)
        if doc is None:
            return None
        # v6.14.0: caché en _consulta_posicional (memoria + SQLite).
        if self.cache is not None:
            cacheado = self.cache.obtener(*clave)
            if cacheado is not None:
                return cacheado
        params: Dict[str, Any] = {
            "textDocument": {"uri": doc["uri"]},
            "position": {"line": int(linea) - 1, "character": int(columna) - 1},
        }
        if contexto:
            params["context"] = {"includeDeclaration": True}
        respuesta = self.enviar_peticion(metodo, params)
        resultado = self._parsear_respuesta(respuesta, tipo)
        if resultado is not None and self.cache is not None:
            self.cache.guardar(*clave, dict(resultado))
        return resultado

    @staticmethod
    def _parsear_respuesta(respuesta: Optional[dict],
                           tipo: str) -> Optional[dict]:
        """Convierte la respuesta LSP cruda en un dict simple y útil."""
        if not respuesta:
            return None

        def _ubicacion(loc: dict) -> dict:
            rango = loc.get("range") or {}
            inicio = rango.get("start") or {}
            uri = loc.get("uri") or ""
            ruta = str(uri).replace("file:///", "").replace("file://", "")
            return {"archivo": ruta,
                    "linea": int(inicio.get("line", 0)) + 1,
                    "columna": int(inicio.get("character", 0)) + 1}

        if tipo == "tipo":
            contenido = respuesta.get("contents")
            if isinstance(contenido, dict):
                contenido = contenido.get("value") or ""
            elif isinstance(contenido, list):
                partes = [p.get("value", "") if isinstance(p, dict) else str(p)
                          for p in contenido]
                contenido = "".join(partes)
            texto = str(contenido or "").strip()
            return {"tipo": texto} if texto else None
        if tipo == "referencias":
            ubicaciones = respuesta if isinstance(respuesta, list) else []
            refs = [_ubicacion(u) for u in ubicaciones if isinstance(u, dict)]
            return {"referencias": refs, "total": len(refs)}
        # definición: Location | Location[] | LocationLink[] | null
        if isinstance(respuesta, list):
            if not respuesta:
                return None
            primero = respuesta[0]
            if "targetUri" in primero:
                primero = {"uri": primero.get("targetUri", ""),
                           "range": (primero.get("targetSelectionRange")
                                     or primero.get("targetRange") or {})}
            return _ubicacion(primero)
        return _ubicacion(respuesta)

    # -- Cierre ---------------------------------------------------------------
    def cerrar(self) -> None:
        """Finaliza el proceso del servidor LSP de forma ordenada."""
        self._abierto = False
        if self.proceso is None:
            return
        try:
            self.enviar_notificacion("shutdown", {})
            self.enviar_notificacion("exit", {})
        except Exception:                              # noqa: BLE001
            pass
        try:
            if self.proceso.stdin:
                self.proceso.stdin.close()
            esperar = getattr(self.proceso, "wait", None)
            if callable(esperar):
                esperar(timeout=3)
        except Exception:                              # noqa: BLE001
            matar = getattr(self.proceso, "kill", None)
            if callable(matar):
                try:
                    matar()
                except Exception:                      # noqa: BLE001
                    pass
        finally:
            self.proceso = None


# ---------------------------------------------------------------------------
# Cliente global (singleton perezoso) y helpers de integración
# ---------------------------------------------------------------------------
_CLIENTE_GLOBAL: Optional["LSPClient"] = None
_MUTEX_GLOBAL = threading.Lock()


def _ruta_cache() -> Path:
    import os                                        # noqa: E402
    base = Path(os.environ.get("SNAPCONTEXT_HOME", str(Path.home())))
    return base / ".snapcontext" / "lsp_cache.db"


def cliente_lsp_activo(flag: Optional[bool] = None) -> bool:
    """True si el modo LSP debe activarse (``--lsp`` o ``SNAPCONTEXT_LSP=1``)."""
    if flag is not None:
        return bool(flag)
    import os                                        # noqa: E402
    return os.environ.get("SNAPCONTEXT_LSP", "").strip() == "1"


def obtener_cliente_lsp(ruta_proyecto: str = ".",
                        lenguaje: Optional[str] = None) -> Optional[LSPClient]:
    """Devuelve el cliente LSP global, iniciándolo perezosamente si hace falta.

    Devuelve ``None`` si el LSP no está disponible (el llamante continúa sin
    él; nunca lanza).
    """
    global _CLIENTE_GLOBAL
    with _MUTEX_GLOBAL:
        if _CLIENTE_GLOBAL is not None and _CLIENTE_GLOBAL._abierto:
            return _CLIENTE_GLOBAL
        cliente = LSPClient()
        if not cliente.iniciar(lenguaje, ruta_proyecto):
            return None
        _CLIENTE_GLOBAL = cliente
        return _CLIENTE_GLOBAL


def cerrar_cliente_lsp() -> None:
    """Cierra el cliente LSP global (fin de sesión)."""
    global _CLIENTE_GLOBAL
    with _MUTEX_GLOBAL:
        if _CLIENTE_GLOBAL is not None:
            _CLIENTE_GLOBAL.cerrar()
            _CLIENTE_GLOBAL = None


def lsp_disponible(lenguaje: str) -> bool:
    """True si hay un servidor LSP instalado para ``lenguaje``."""
    return _comando_servidor(lenguaje) is not None


def soporta_lsp(lenguaje: str) -> bool:
    """True si el lenguaje está soportado por algún servidor LSP (v6.33.0).

    Alias semántico de ``lsp_disponible``; incluye tanto la detección por
    extensión como la disponibilidad del servidor en el PATH.
    """
    if lenguaje in COMANDOS_SERVIDOR:
        return _comando_servidor(lenguaje) is not None
    return False


def obtener_simbolos(archivo: str, linea: Optional[int] = None) -> List[Dict[str, Any]]:
    """Obtiene los símbolos de un archivo usando LSP (v6.33.0).

    Si ``linea`` se especifica, devuelve la definición y referencias del
    símbolo en esa línea. Si no, devuelve todos los símbolos del archivo
    (document symbols) si el servidor lo soporta.

    Devuelve una lista de dicts: ``[{"nombre", "linea", "archivo", "tipo",
    "contenido"}]``. Nunca lanza; devuelve ``[]`` si LSP no está disponible.
    """
    lenguaje = _detectar_lenguaje_por_extension(archivo)
    if not lenguaje or not lsp_disponible(lenguaje):
        return []
    cliente = obtener_cliente_lsp(str(Path(archivo).parent), lenguaje)
    if cliente is None:
        return []
    simbolos: List[Dict[str, Any]] = []
    try:
        doc = cliente._preparar_archivo(archivo)
        if doc is None:
            return []
        # Intentar documentSymbol (lista de símbolos del archivo).
        if linea is None:
            respuesta = cliente.enviar_peticion(
                "textDocument/documentSymbol",
                {"textDocument": {"uri": doc["uri"]}})
            if isinstance(respuesta, list):
                for raw in respuesta:
                    if not isinstance(raw, dict):
                        continue
                    rango = raw.get("range") or raw.get("location", {}).get("range", {})
                    inicio = rango.get("start", {})
                    simbolos.append({
                        "nombre": raw.get("name", ""),
                        "linea": int(inicio.get("line", 0)) + 1,
                        "columna": int(inicio.get("character", 0)) + 1,
                        "archivo": archivo,
                        "tipo": raw.get("kind", "simbolo"),
                        "contenido": raw.get("detail", ""),
                    })
        else:
            # Definición + referencias en la línea.
            definicion = cliente.obtener_definicion(archivo, linea, 1)
            if definicion:
                simbolos.append({
                    "nombre": definicion.get("archivo", archivo),
                    "linea": definicion.get("linea", linea),
                    "columna": definicion.get("columna", 1),
                    "archivo": definicion.get("archivo", archivo),
                    "tipo": "definicion",
                    "contenido": "",
                })
            refs = cliente.obtener_referencias(archivo, linea, 1)
            if refs and isinstance(refs, dict):
                for ref in refs.get("referencias", []):
                    simbolos.append({
                        "nombre": "",
                        "linea": ref.get("linea", 0),
                        "columna": ref.get("columna", 1),
                        "archivo": ref.get("archivo", archivo),
                        "tipo": "referencia",
                        "contenido": "",
                    })
    except Exception:  # noqa: BLE001
        pass
    return simbolos


__all__ = ["MAPEO_EXTENSIONES", "COMANDOS_SERVIDOR", "CacheLSP", "LSPClient",
           "obtener_cliente_lsp", "cerrar_cliente_lsp", "cliente_lsp_activo",
           "lsp_disponible", "soporta_lsp", "obtener_simbolos",
           "_detectar_lenguaje_por_extension",
           "_comando_servidor", "TIMEOUT_DEFECTO"]

