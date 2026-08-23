#!/bin/bash
# ============================================================================
# SnapContext — Empaquetado del ejecutable (v1.5.0) — Linux / macOS / CI
#
# Genera dist/snapcontext con PyInstaller y (si makensis está disponible,
# p. ej. apt install nsis en Windows-cross o wine) el instalador NSIS.
#
# Uso:
#   ./scripts/empaquetar_exe.sh            # versión ligera
#   SNAPCONTEXT_EXE_FULL=1 ./scripts/...   # incluir extras pesados
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
echo "▶ SnapContext v$VERSION"

command -v pyinstaller >/dev/null || { echo "Instalando pyinstaller..."; pip install pyinstaller; }

echo "▶ Limpiando compilaciones anteriores..."
rm -rf build dist/snapcontext dist/SnapContext-Setup-*.exe

echo "▶ Ejecutando PyInstaller..."
pyinstaller snapcontext.spec --noconfirm

test -x "dist/snapcontext" || { echo "✖ PyInstaller falló."; exit 1; }
./dist/snapcontext --version | head -1
echo "✔ dist/snapcontext generado"

if command -v makensis >/dev/null; then
    echo "▶ Generando instalador NSIS..."
    # En Linux/macOS el .exe de Windows no aplica: se empaqueta el binario nativo.
    sed 's/dist\\snapcontext.exe/dist\/snapcontext/' installer.nsi > installer-posix.nsi
    makensis installer-posix.nsi
    rm installer-posix.nsi
    echo "✔ dist/SnapContext-Setup-$VERSION.exe generado"
else
    echo "⚠ makensis no disponible; se omite el instalador NSIS."
fi

echo ""
echo "  ✔ Empaquetado completado."
