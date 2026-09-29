#!/usr/bin/env python3
"""Gate `verify-release` de B9.63-B: comprueba el contrato de una release.

Fail-closed y determinista. NO publica nada, NO crea tags y NO modifica el
repositorio: trabaja sobre copias temporales y sobre el artefacto construido.

Cada invariante (I1..I12) es una función independiente `iN_*` que devuelve
``(ok, mensaje)``, para poder testearlas y ejecutarlas por separado:

    python3 scripts/verify_release.py --tag v6.35.3
    python3 scripts/verify_release.py --only I1 I5 --tag v6.35.3

Uso previsto en CI: antes de cualquier job de publicación.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import version_sync

RE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")

#: Módulos runtime que DEBEN estar en el wheel (contrato I10; F-01).
MODULOS_WHEEL = (
    "web/__init__.py",
    "web/app.py",
    "web/interactive.py",
    "web/seguridad.py",
    "web/filesystem.py",
    "web/remota.py",
    "snapcontext.py",
    "task_queue.py",
    "github_gateway.py",
    # B9.63-B: módulos de los que dependen los anteriores. `utils` importa
    # `exceptions` a nivel de módulo: sin él el wheel no importa siquiera.
    "utils.py",
    "exceptions.py",
    "prompt_profiles.py",
    # Canal WORK (v6.37.0): los importa `snapcontext` (work_state /
    # work_state_update) y `react_agent` (F4) en runtime.
    "work_context.py",
    "work_verdict.py",
    "work_obsolencia.py",
)

#: Tests de regresión de seguridad de B9.61 (I8; F-02).
TESTS_SEGURIDAD = (
    "tests/test_gateway_seguridad.py",
    "tests/test_gateway_filesystem.py",
    "tests/test_gateway_remota.py",
    "tests/test_web_riesgo.py",
    "tests/test_task_queue.py",
    "tests/test_github_gateway.py",
)


class Ctx:
    """Estado compartido entre invariantes (construye los artefactos una vez)."""

    def __init__(self, tag: str | None) -> None:
        self.tag = tag
        self.version = version_sync.leer_version()
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.wheel: Path | None = None
        self.sdist: Path | None = None
        self.venv: Path | None = None

    @property
    def tmp(self) -> Path:
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="verify-release-")
        return Path(self._tmp.name)

    def cleanup(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


# --------------------------------------------------------------------------- #
# I1 — tag == VERSION (fail-closed)
# --------------------------------------------------------------------------- #
def i1_tag(ctx: Ctx) -> tuple[bool, str]:
    if not ctx.tag:
        return False, "sin tag: no se publica fuera de un tag vX.Y.Z (fail-closed)"
    m = RE_TAG.match(ctx.tag)
    if not m:
        return False, f"tag {ctx.tag!r} no tiene formato vX.Y.Z"
    if m.group(1) != ctx.version:
        return False, f"tag {ctx.tag} != VERSION {ctx.version}"
    return True, f"tag {ctx.tag} == VERSION {ctx.version}"


# --------------------------------------------------------------------------- #
# I2 / I9 — construcción
# --------------------------------------------------------------------------- #
def construir(ctx: Ctx) -> tuple[bool, str]:
    if ctx.wheel is not None:
        return True, "artefactos ya construidos"
    dist = ctx.tmp / "dist"
    r = _run([sys.executable, "-m", "build", "--outdir", str(dist)], cwd=RAIZ)
    if r.returncode != 0:
        return False, f"build falló: {r.stderr[-400:]}"
    ctx.sdist = next(iter(dist.glob("*.tar.gz")), None)
    ctx.wheel = next(iter(dist.glob("*.whl")), None)
    if ctx.sdist is None or ctx.wheel is None:
        return False, "build no produjo sdist+wheel"
    return True, f"construido {ctx.wheel.name} y {ctx.sdist.name}"


def i2_package_version(ctx: Ctx) -> tuple[bool, str]:
    ok, msg = construir(ctx)
    if not ok:
        return False, msg
    esperado = f"snapcontext-{ctx.version}-"
    assert ctx.wheel is not None
    if not ctx.wheel.name.startswith(esperado):
        return False, f"wheel {ctx.wheel.name} no corresponde a {esperado}"
    return True, f"el artefacto corresponde a snapcontext-{ctx.version}"


def i9_sdist_a_wheel(ctx: Ctx) -> tuple[bool, str]:
    """El sdist debe poder producir un wheel sin intervención manual."""
    ok, msg = construir(ctx)
    if not ok:
        return False, msg
    out = ctx.tmp / "from-sdist"
    r = _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(ctx.sdist)])
    if r.returncode != 0:
        return False, f"sdist no reconstruible: {r.stderr[-400:]}"
    if not list(out.glob("*.whl")):
        return False, "el sdist no produjo ningún wheel"
    return True, "sdist → wheel sin intervención"


# --------------------------------------------------------------------------- #
# I10 — contenido real del wheel
# --------------------------------------------------------------------------- #
def i10_wheel_contents(ctx: Ctx) -> tuple[bool, str]:
    ok, msg = construir(ctx)
    if not ok:
        return False, msg
    assert ctx.wheel is not None
    with zipfile.ZipFile(ctx.wheel) as z:
        nombres = set(z.namelist())
    faltan = [m for m in MODULOS_WHEEL if m not in nombres]
    if faltan:
        return False, f"wheel sin módulos requeridos: {faltan}"
    if not any(n.startswith("web/static/") for n in nombres):
        return False, "el wheel no contiene web/static/*"
    return True, f"wheel contiene los {len(MODULOS_WHEEL)} módulos runtime + web/static"


# --------------------------------------------------------------------------- #
# I11 / I3 / I4 / I12 — instalación limpia FUERA del source tree
# --------------------------------------------------------------------------- #
_SNIPPET = """
import sys, pathlib
from importlib.metadata import version
import snapcontext
import web.app, web.filesystem, web.seguridad, web.remota
esperado = sys.argv[1]
assert snapcontext.VERSION == esperado, (snapcontext.VERSION, esperado)
assert version("snapcontext") == esperado, (version("snapcontext"), esperado)
base = pathlib.Path(web.app.__file__).parent
assert (base / "static" / "index.html").exists(), "falta web/static/index.html"
assert not snapcontext.__file__.startswith(RAIZ), "importado desde el source tree"
print("OK", snapcontext.VERSION, web.app.__file__)
"""


def _venv_limpia(ctx: Ctx) -> tuple[bool, str]:
    ok, msg = construir(ctx)
    if not ok:
        return False, msg
    venv = ctx.tmp / "venv"
    r = _run([sys.executable, "-m", "venv", str(venv)])
    if r.returncode != 0:
        return False, f"no se pudo crear la venv: {r.stderr[-300:]}"
    ctx.venv = venv
    return True, str(venv)


def _instalar_wheel(ctx: Ctx) -> Path | None:
    """Instala SOLO el wheel; usa el extra `web` si sus dependencias resuelven."""
    assert ctx.venv is not None
    venv_py = ctx.venv / "bin" / "python"
    r = _run([str(venv_py), "-m", "pip", "install", "--quiet", f"{ctx.wheel}[web]"])
    if r.returncode != 0:
        r = _run([str(venv_py), "-m", "pip", "install", "--quiet", "--no-deps", str(ctx.wheel)])
        if r.returncode != 0:
            return None
    return venv_py


def i11_clean_install(ctx: Ctx) -> tuple[bool, str]:
    """Detecta F-01: los módulos de web deben importarse desde el wheel."""
    ok, msg = _venv_limpia(ctx)
    if not ok:
        return False, msg
    venv_py = _instalar_wheel(ctx)
    if venv_py is None:
        return False, "no se pudo instalar el wheel en la venv limpia"
    fuera = ctx.tmp / "cwd-externo"
    fuera.mkdir(exist_ok=True)
    script = fuera / "smoke.py"
    script.write_text(f"RAIZ = {str(RAIZ)!r}\n" + _SNIPPET, encoding="utf-8")
    import os as _os

    previo = _os.environ.copy()
    _os.environ.update({"PYTHONPATH": "", "HOME": str(ctx.tmp)})
    try:
        r = _run([str(venv_py), str(script), ctx.version], cwd=fuera)
    finally:
        _os.environ.clear()
        _os.environ.update(previo)
    if r.returncode != 0:
        return False, f"smoke falló: {r.stdout[-200:]}{r.stderr[-500:]}"
    return True, f"instalación limpia OK → {r.stdout.strip().splitlines()[-1]}"


def i3_runtime(ctx: Ctx) -> tuple[bool, str]:
    ok, msg = i11_clean_install(ctx)
    return (True, "snapcontext.VERSION del wheel instalado == VERSION") if ok else (False, msg)


def i4_metadata(ctx: Ctx) -> tuple[bool, str]:
    ok, msg = i11_clean_install(ctx)
    return (True, "importlib.metadata.version == VERSION") if ok else (False, msg)


def i12_static_assets(ctx: Ctx) -> tuple[bool, str]:
    ok, msg = i11_clean_install(ctx)
    return (True, "web/static/* presente en el paquete instalado") if ok else (False, msg)


# --------------------------------------------------------------------------- #
# I5 / I6 — VS Code y JetBrains
# --------------------------------------------------------------------------- #
def i5_vscode(ctx: Ctx) -> tuple[bool, str]:
    fallos = [f for f in version_sync.comprobar(ctx.version) if "vscode" in f]
    if fallos:
        return False, "; ".join(fallos)
    return True, f"vscode/package.json == package-lock.json == {ctx.version}"


def i6_jetbrains(ctx: Ctx) -> tuple[bool, str]:
    fallos = [f for f in version_sync.comprobar(ctx.version) if "jetbrains" in f]
    if fallos:
        return False, "; ".join(fallos)
    return True, f"gradle pluginVersion == build.gradle.kts == plugin.xml == {ctx.version}"


# --------------------------------------------------------------------------- #
# I7 — CHANGELOG
# --------------------------------------------------------------------------- #
def i7_changelog(ctx: Ctx) -> tuple[bool, str]:
    texto = (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## \[{re.escape(ctx.version)}\]", texto, re.MULTILINE):
        return False, f"CHANGELOG sin entrada publicada para {ctx.version} ('Unreleased' no cuenta)"
    return True, f"CHANGELOG contiene entrada publicada para {ctx.version}"


# --------------------------------------------------------------------------- #
# I8 — tests de seguridad presentes (F-02)
# --------------------------------------------------------------------------- #
def i8_security_tests(ctx: Ctx) -> tuple[bool, str]:
    faltan = [t for t in TESTS_SEGURIDAD if not (RAIZ / t).is_file()]
    if faltan:
        return False, f"faltan tests de seguridad de B9.61: {faltan}"
    return True, f"los {len(TESTS_SEGURIDAD)} tests de seguridad están presentes"


INVARIANTES = {
    "I1": i1_tag,
    "I2": i2_package_version,
    "I3": i3_runtime,
    "I4": i4_metadata,
    "I5": i5_vscode,
    "I6": i6_jetbrains,
    "I7": i7_changelog,
    "I8": i8_security_tests,
    "I9": i9_sdist_a_wheel,
    "I10": i10_wheel_contents,
    "I11": i11_clean_install,
    "I12": i12_static_assets,
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", default=None, help="tag que disparó el workflow")
    p.add_argument("--only", nargs="*", default=None, help="subconjunto de invariantes")
    p.add_argument(
        "--allow-no-tag",
        action="store_true",
        help="omite I1 (uso local; NO usar en el gate de publicación)",
    )
    args = p.parse_args(argv)

    selected = args.only or list(INVARIANTES)
    ctx = Ctx(args.tag)
    fallo = False
    try:
        if not args.allow_no_tag and args.tag is None and "I1" not in selected:
            print("✖ I1 omitido y sin --tag: fail-closed")
            return 2
        for nombre in selected:
            fn = INVARIANTES.get(nombre)
            if fn is None:
                print(f"✖ invariante desconocido: {nombre}")
                fallo = True
                continue
            ok, msg = fn(ctx)
            print(f"{'✔' if ok else '✖'} {nombre}: {msg}")
            fallo = fallo or not ok
    finally:
        ctx.cleanup()
    return 1 if fallo else 0


if __name__ == "__main__":
    raise SystemExit(main())
