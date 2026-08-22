#!/usr/bin/env bash
# Empaquetado de la extensión VS Code de SnapContext (Linux/macOS).
# Requisitos: Node.js + vsce (npm install -g @vscode/vsce)
# Uso: ./vscode/scripts/empaquetar.sh
set -euo pipefail

raiz="$(cd "$(dirname "$0")/../.." && pwd)"

echo "==> Verificando que snapcontext está instalado…"
if ! python3 -c "import snapcontext" 2>/dev/null; then
    echo "   snapcontext no está en el Python actual." >&2
    echo "   Instálalo con: pip install -e $raiz" >&2
fi

echo "==> Generando .vsix con vsce…"
cd "$raiz/vscode"
npx @vscode/vsce package --no-dependencies

echo ""
echo "✔ Listo. El .vsix está en vscode/. Instálalo con:"
echo "    code --install-extension snapcontext-vscode-1.0.0.vsix"
