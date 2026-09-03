#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marketplace MCP (v6.21.0) — repositorio central de plugins instalables.

Capa de ecosistema sobre el sistema de plugins local (v4.0.0):

- ``obtener_index()``: descarga ``index.json`` del repositorio central
  (cacheable en ``~/.snapcontext/marketplace_cache.json`` con TTL de 1 h).
- ``buscar_plugins(termino)``: búsqueda por nombre, descripción, autor o tags.
- ``instalar_plugin(nombre_o_url)``: resuelve nombres contra el índice y delega
  la instalación en ``snapcontext._plugin_instalar`` (confirmación incluida).
- ``instalar_dependencias(manifest)``: instala dependencias pip en modo usuario
  (``pip install --user``); si falla, deshabilita el plugin por seguridad.
- ``cargar_plugins_instalados()``: garantiza dependencias y devuelve las
  herramientas MCP de los plugins habilitados (el registro en el sistema MCP
  ya ocurre de forma perezosa vía ``_plugins_herramientas``).

Sin plugins instalados, nada de esto se ejecuta: el CLI es idéntico.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Índice central (JSON). Puede sobrescribirse con la variable de entorno
# SNAPCONTEXT_MARKETPLACE_INDEX (útil para tests y despliegues privados).
URL_INDEX = os.environ.get(
    "SNAPCONTEXT_MARKETPLACE_INDEX",
    "https://raw.githubusercontent.com/NicolasBruna24/snapcontext-plugins/"
    "main/index.json")

RUTA_CACHE = Path.home() / ".snapcontext" / "marketplace_cache.json"
TTL_CACHE_SEG = 3600  # 1 hora de validez de la caché local


def _sc():
    """Import perezoso de snapcontext (evita dependencia circular)."""
    import snapcontext
    return snapcontext


def _aviso(texto: str) -> None:
    try:
        _sc().aviso(texto)
    except Exception:                                    # noqa: BLE001
        print(f"⚠ {texto}")


def _info(texto: str) -> None:
    try:
        _sc().info(texto)
    except Exception:                                    # noqa: BLE001
        print(texto)


def _exito(texto: str) -> None:
    try:
        _sc().exito(texto)
    except Exception:                                    # noqa: BLE001
        print(texto)


def _error(texto: str) -> None:
    try:
        _sc().error(texto)
    except Exception:                                    # noqa: BLE001
        print(f"✖ {texto}")


def obtener_index(forzar: bool = False,
                  url: Optional[str] = None) -> List[dict]:
    """Devuelve la lista de plugins del índice central.

    Usa la caché local (``~/.snapcontext/marketplace_cache.json``) si tiene
    menos de :data:`TTL_CACHE_SEG`; ``forzar=True`` re-descarga siempre.
    Si la descarga falla pero existe caché (aunque caducada), se usa la caché
    con un aviso — el marketplace nunca rompe el flujo por falta de red.
    """
    ruta_url = url or URL_INDEX
    ahora = datetime.now()
    if not forzar and RUTA_CACHE.exists():
        try:
            datos = json.loads(RUTA_CACHE.read_text(encoding="utf-8"))
            moment = datetime.fromisoformat(datos.get("descargado", ""))
            if ahora - moment < timedelta(seconds=TTL_CACHE_SEG):
                return list(datos.get("index") or [])
        except Exception:                                # noqa: BLE001
            pass                                         # caché corrupta: red
    _info("📦 Descargando índice de plugins...")
    try:
        with urllib.request.urlopen(ruta_url, timeout=15) as respuesta:
            indice = json.loads(respuesta.read().decode("utf-8"))
        if not isinstance(indice, list):
            indice = list((indice or {}).get("plugins") or [])
    except Exception as exc:                             # noqa: BLE001
        # Red caída o índice inaccesible: caché caducada si existe.
        if RUTA_CACHE.exists():
            try:
                datos = json.loads(RUTA_CACHE.read_text(encoding="utf-8"))
                _aviso(f"No se pudo actualizar el índice ({exc}); "
                       "se usa la caché local.")
                return list(datos.get("index") or [])
            except Exception:                            # noqa: BLE001
                pass
        _aviso(f"No se pudo descargar el índice de plugins: {exc}")
        return []
    try:
        RUTA_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RUTA_CACHE.write_text(json.dumps(
            {"descargado": ahora.isoformat(), "index": indice},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass                                             # caché best-effort
    return list(indice)


def buscar_plugins(termino: str, index: Optional[List[dict]] = None
                   ) -> List[dict]:
    """Busca plugins del índice por nombre, descripción, autor o tags."""
    if index is None:
        index = obtener_index()
    termino = (termino or "").strip().lower()
    if not termino:
        return list(index)
    resultados = []
    for entrada in index:
        if not isinstance(entrada, dict):
            continue
        texto = " ".join([
            str(entrada.get("nombre", "")),
            str(entrada.get("name", "")),
            str(entrada.get("descripcion", "")),
            str(entrada.get("description", "")),
            str(entrada.get("autor", "")),
            str(entrada.get("author", "")),
            " ".join(str(t) for t in (entrada.get("tags") or [])),
        ]).lower()
        if termino in texto:
            resultados.append(entrada)
    return resultados


def resolver_plugin(nombre: str, index: Optional[List[dict]] = None
                    ) -> Optional[dict]:
    """Resuelve un nombre de plugin contra el índice → entrada o ``None``."""
    if index is None:
        index = obtener_index()
    objetivo = nombre.strip().lower()
    for entrada in index:
        if isinstance(entrada, dict) and str(
                entrada.get("nombre", entrada.get("name", ""))).lower() == \
                objetivo:
            return entrada
    return None


def _parece_ruta_o_repo(origen: str) -> bool:
    """True si ``origen`` es una ruta local, URL o slug (no un nombre simple)."""
    if Path(origen).expanduser().exists():
        return True
    return ("://" in origen or "/" in origen or "\\" in origen
            or ":" in origen or origen.lower().endswith(".zip"))


def instalar_plugin(nombre_o_url: str) -> int:
    """Instala un plugin del marketplace o desde una URL/ruta.

    - Si ``nombre_o_url`` es una URL, slug ``usuario/repo`` o ruta local, se
      delega directamente en ``snapcontext._plugin_instalar``.
    - Si es un nombre simple, se resuelve contra el índice central y se usa el
      ``repositorio``/``url`` declarado. Si no está en el índice → error.
    Devuelve el código de salida (0 = éxito).
    """
    sc = _sc()
    origen = (nombre_o_url or "").strip()
    if not origen:
        _error("Uso: snapcontext plugin install <nombre | url | ruta>")
        return 1
    if _parece_ruta_o_repo(origen):
        return int(sc._plugin_instalar(origen))
    entrada = resolver_plugin(origen)
    if entrada is None:
        _error(f"Plugin '{origen}' no encontrado en el marketplace.")
        return 1
    destino = (entrada.get("repositorio") or entrada.get("repository")
               or entrada.get("url") or "")
    if not destino:
        _error(f"El plugin '{origen}' no declara repositorio en el índice.")
        return 1
    codigo = int(sc._plugin_instalar(destino))
    if codigo == 0:
        _exito(f"✅ Plugin {origen} instalado correctamente.")
        manifest = _leer_manifest(origen)
        if manifest:
            instalar_dependencias(manifest, nombre=origen)
    else:
        _error(f"❌ Error instalando plugin {origen}.")
    return codigo


def _leer_manifest(nombre: str) -> Optional[dict]:
    """Lee el ``plugin.json`` de un plugin instalado (o ``None``)."""
    try:
        instalados = _sc()._plugins_instalados()
        for clave, manifest in instalados.items():
            if clave.lower() == nombre.strip().lower():
                return manifest
    except Exception:                                    # noqa: BLE001
        pass
    return None


def instalar_dependencias(manifest: dict, nombre: str = "") -> bool:
    """Instala las dependencias pip del manifest en modo usuario.

    Si alguna falla, el plugin se deshabilita por seguridad y se avisa.
    Devuelve ``True`` si todas las dependencias están disponibles.
    """
    deps = list((manifest or {}).get("dependencies") or [])
    if not deps:
        return True
    nombre = nombre or str(manifest.get("name") or manifest.get("nombre") or "")
    _info(f"📦 Instalando dependencias de '{nombre}': {', '.join(deps)}")
    for dep in deps:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", dep],
                capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or "")[-300:])
        except Exception as exc:                          # noqa: BLE001
            _error(f"❌ Error instalando dependencia '{dep}': {exc}")
            _aviso(f"El plugin '{nombre}' se deshabilita por seguridad.")
            try:
                _sc()._plugin_cambiar_estado(nombre, habilitar=False)
            except Exception:                             # noqa: BLE001
                pass
            return False
    return True


def desinstalar_plugin(nombre: str) -> int:
    """Desinstala un plugin (delega en ``snapcontext._plugin_remove``)."""
    return int(_sc()._plugin_remove(nombre))


def listar_plugins() -> List[dict]:
    """Lista los plugins instalados con su estado (habilitado/deshabilitado)."""
    sc = _sc()
    resultado = []
    for clave, manifest in (sc._plugins_instalados() or {}).items():
        resultado.append({
            "nombre": clave,
            "version": manifest.get("version", ""),
            "descripcion": manifest.get("description",
                                        manifest.get("descripcion", "")),
            "habilitado": bool(manifest.get("enabled", True)),
            "herramientas": [t.get("name") for t in
                             (manifest.get("tools") or [])
                             if isinstance(t, dict)],
        })
    return resultado


def habilitar_plugin(nombre: str) -> int:
    return int(_sc()._plugin_cambiar_estado(nombre, habilitar=True))


def deshabilitar_plugin(nombre: str) -> int:
    return int(_sc()._plugin_cambiar_estado(nombre, habilitar=False))


def actualizar_plugin(nombre: str = "") -> int:
    """Actualiza un plugin (o todos los instalados si ``nombre`` es vacío)."""
    sc = _sc()
    nombres = ([nombre] if nombre
               else list((sc._plugins_instalados() or {}).keys()))
    if not nombres:
        _aviso("No hay plugins instalados para actualizar.")
        return 0
    codigo_global = 0
    for nom in nombres:
        codigo = int(sc._plugin_update(nom))
        codigo_global = codigo_global or codigo
    return codigo_global


def cargar_plugins_instalados() -> Dict[str, List[dict]]:
    """Garantiza dependencias y devuelve las herramientas MCP habilitadas.

    No registra nada duplicado: el registro en el sistema MCP ocurre de forma
    perezosa vía ``snapcontext._plugins_herramientas`` (idempotente por diseño).
    """
    sc = _sc()
    herramientas: Dict[str, List[dict]] = {}
    for clave, manifest in (sc._plugins_instalados() or {}).items():
        if not manifest.get("enabled", True):
            continue
        if not instalar_dependencias(manifest, nombre=clave):
            continue
        tools = [t for t in (manifest.get("tools") or [])
                 if isinstance(t, dict) and t.get("name")]
        if tools:
            herramientas[clave] = tools
    return herramientas

