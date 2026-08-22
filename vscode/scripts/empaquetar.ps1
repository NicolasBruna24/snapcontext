# Empaquetado de la extensión VS Code de SnapContext (PowerShell).
# Requisitos: Node.js + vsce (npm install -g @vscode/vsce)
# Uso: ./vscode/scripts/empaquetar.ps1

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Host "==> Verificando que snapcontext está instalado…" -ForegroundColor Cyan
python -c "import snapcontext; print('snapcontext', snapcontext.VERSION)" `
    2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "   snapcontext no está en el Python actual." -ForegroundColor Yellow
    Write-Host "   Instálalo con: pip install -e $raiz"
}

Write-Host "==> Generando .vsix con vsce…" -ForegroundColor Cyan
Push-Location "$raiz\vscode"
try {
    npx @vscode/vsce package --no-dependencies
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "✔ Listo. El .vsix está en vscode\. Instálalo con:" -ForegroundColor Green
Write-Host "    code --install-extension snapcontext-vscode-0.17.0.vsix"
