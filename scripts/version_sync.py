#!/usr/bin/env python3
"""Sincroniza y verifica la versión del producto en todas las superficies.

B9.63-B: `VERSION` (raíz) es la ÚNICA fuente de verdad. Dos modos, ambos
deterministas y sin dependencias externas:

  * ``check`` (por defecto) — **solo lectura**. Compara `VERSION` con cada
    superficie y sale con código != 0 ante cualquier divergencia. Es lo que
    ejecuta el gate `verify-release` de CI.
  * ``sync`` — reescribe las superficies con el valor de `VERSION`. Operación
    explícita del mantenedor.

No publica nada, no crea tags y no toca la lógica de la aplicación.

Uso:
    python3 scripts/version_sync.py check
    python3 scripts/version_sync.py sync
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RE_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-.\w+]*)?$")
PLUGIN_XML = RAIZ / "jetbrains" / "src" / "main" / "resources" / "META-INF" / "plugin.xml"


def leer_version() -> str:
    """Única fuente de verdad."""
    bruto = (RAIZ / "VERSION").read_text(encoding="utf-8").strip()
    if not RE_VERSION.match(bruto):
        raise SystemExit(f"VERSION inválido: {bruto!r}")
    return bruto


def _reemplazar_primero(texto: str, patron: str, valor: str) -> str:
    return re.sub(patron, lambda m: m.group(1) + valor + m.group(2), texto, count=1)


def _version_json(ruta: Path) -> str:
    return str(json.loads(ruta.read_text(encoding="utf-8")).get("version", ""))


def leer_vscode_package() -> str:
    return _version_json(RAIZ / "vscode" / "package.json")


def escribir_vscode_package(v: str) -> bool:
    ruta = RAIZ / "vscode" / "package.json"
    texto = ruta.read_text(encoding="utf-8")
    if _version_json(ruta) == v:
        return False
    # Reescribe SÓLO la clave "version" de nivel superior.
    ruta.write_text(_reemplazar_primero(texto, r'("version"\s*:\s*")[^"]*(")', v), encoding="utf-8")
    return True


def leer_vscode_lock() -> str:
    return _version_json(RAIZ / "vscode" / "package-lock.json")


def escribir_vscode_lock(v: str) -> bool:
    ruta = RAIZ / "vscode" / "package-lock.json"
    texto = ruta.read_text(encoding="utf-8")
    if _version_json(ruta) == v:
        return False
    # Sólo la versión raíz del lockfile, no la de sus dependencias.
    ruta.write_text(_reemplazar_primero(texto, r'("version"\s*:\s*")[^"]*(")', v), encoding="utf-8")
    return True


def leer_gradle_properties() -> str:
    texto = (RAIZ / "jetbrains" / "gradle.properties").read_text(encoding="utf-8")
    m = re.search(r"^pluginVersion=(.+)$", texto, re.MULTILINE)
    return m.group(1).strip() if m else ""


def escribir_gradle_properties(v: str) -> bool:
    ruta = RAIZ / "jetbrains" / "gradle.properties"
    texto = ruta.read_text(encoding="utf-8")
    nuevo = re.sub(r"^pluginVersion=.*$", f"pluginVersion={v}", texto, count=1, flags=re.MULTILINE)
    if nuevo == texto:
        return False
    ruta.write_text(nuevo, encoding="utf-8")
    return True


def leer_build_gradle() -> str:
    texto = (RAIZ / "jetbrains" / "build.gradle.kts").read_text(encoding="utf-8")
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', texto, re.MULTILINE)
    return m.group(1).strip() if m else ""


def escribir_build_gradle(v: str) -> bool:
    ruta = RAIZ / "jetbrains" / "build.gradle.kts"
    texto = ruta.read_text(encoding="utf-8")
    nuevo = _reemplazar_primero(texto, r'^(\s*version\s*=\s*")[^"]+(")', v)
    if nuevo == texto:
        return False
    ruta.write_text(nuevo, encoding="utf-8")
    return True


def leer_plugin_xml() -> str:
    m = re.search(r"<version>([^<]+)</version>", PLUGIN_XML.read_text(encoding="utf-8"))
    return m.group(1).strip() if m else ""


def escribir_plugin_xml(v: str) -> bool:
    texto = PLUGIN_XML.read_text(encoding="utf-8")
    nuevo = _reemplazar_primero(texto, r"(<version>)[^<]+(</version>)", v)
    if nuevo == texto:
        return False
    PLUGIN_XML.write_text(nuevo, encoding="utf-8")
    return True


SUPERFICIES = (
    ("vscode/package.json", leer_vscode_package),
    ("vscode/package-lock.json", leer_vscode_lock),
    ("jetbrains/gradle.properties", leer_gradle_properties),
    ("jetbrains/build.gradle.kts", leer_build_gradle),
    ("jetbrains/src/main/resources/META-INF/plugin.xml", leer_plugin_xml),
)

ESCRITORES = (
    escribir_vscode_package,
    escribir_vscode_lock,
    escribir_gradle_properties,
    escribir_build_gradle,
    escribir_plugin_xml,
)


def comprobar(esperada: str) -> list[str]:
    """Divergencias detectadas (lista vacía = todo coherente)."""
    fallos = []
    for nombre, lector in SUPERFICIES:
        try:
            actual = lector()
        except Exception as exc:
            fallos.append(f"{nombre}: no se pudo leer ({exc})")
            continue
        if actual != esperada:
            fallos.append(f"{nombre}: {actual!r} != VERSION {esperada!r}")
    return fallos


def main(argv: list[str]) -> int:
    modo = argv[1] if len(argv) > 1 else "check"
    if modo not in ("check", "sync"):
        print(__doc__)
        return 2
    version = leer_version()
    if modo == "sync":
        cambiados = [f.__name__ for f in ESCRITORES if f(version)]
        print(f"VERSION={version}")
        print("sincronizado: " + (", ".join(cambiados) if cambiados else "sin cambios"))
        return 0
    fallos = comprobar(version)
    if fallos:
        print(f"✖ divergencia de versión (VERSION={version}):")
        for f in fallos:
            print(f"  - {f}")
        print("Solución: python3 scripts/version_sync.py sync")
        return 1
    print(f"✔ todas las superficies coinciden con VERSION={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
