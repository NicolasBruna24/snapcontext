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

echo "==> Instalando dependencias de la extensión (TypeScript)…"
cd "$raiz/vscode"
if [ ! -d node_modules ]; then
    npm install
fi

echo "==> Compilando TypeScript (tsc -p ./)…"
npm run compile
if [ ! -f out/extension.js ]; then
    echo "   ERROR: no se generó out/extension.js tras compilar." >&2
    exit 1
fi

echo "==> Generando .vsix con vsce…"
npx @vscode/vsce package --no-dependencies

echo ""
echo "✔ Listo. El .vsix está en vscode/. Instálalo con:"
echo "    code --install-extension snapcontext-vscode-1.0.0.vsix"
