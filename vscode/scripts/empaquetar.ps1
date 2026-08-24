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

Write-Host "==> Instalando dependencias de la extensión (TypeScript)…" -ForegroundColor Cyan
Push-Location "$raiz\vscode"
try {
    if (-not (Test-Path "node_modules")) {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install falló." }
    }

    Write-Host "==> Compilando TypeScript (tsc -p ./)…" -ForegroundColor Cyan
    npm run compile
    if ($LASTEXITCODE -ne 0) { throw "La compilación TypeScript falló." }
    if (-not (Test-Path "out/extension.js")) {
        throw "No se generó out/extension.js tras compilar."
    }

    Write-Host "==> Generando .vsix con vsce…" -ForegroundColor Cyan
    npx @vscode/vsce package --no-dependencies
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "✔ Listo. El .vsix está en vscode\. Instálalo con:" -ForegroundColor Green
Write-Host "    code --install-extension snapcontext-vscode-1.0.0.vsix"
