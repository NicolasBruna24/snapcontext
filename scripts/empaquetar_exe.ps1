# ============================================================================
# SnapContext — Empaquetado del instalador .exe para Windows (v1.5.0)
#
# Ejecuta PyInstaller (snapcontext.spec) y después NSIS (installer.nsi) para
# generar dist\SnapContext-Setup-<version>.exe, sin necesidad de Python en
# la máquina del usuario final.
#
# Uso:
#   .\scripts\empaquetar_exe.ps1              # versión estándar (ligera)
#   .\scripts\empaquetar_exe.ps1 -Full        # incluye sentence-transformers,
#                                             # tree-sitter (exe mucho mayor)
# Requisitos:
#   - pip install pyinstaller  (+ dependencias del proyecto)
#   - NSIS (makensis) en el PATH: https://nsis.sourceforge.io
# ============================================================================
param(
    [switch]$Full    # incluir extras pesados (embeddings + tree-sitter)
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

function Write-Paso($texto) { Write-Host "▶ $texto" -ForegroundColor Cyan }
function Write-Bien($texto) { Write-Host "✔ $texto" -ForegroundColor Green }

# ─── 0. Leer la versión desde pyproject.toml ────────────────────────────────
$version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"') `
    .Matches[0].Groups[1].Value
Write-Bien "SnapContext v$version"

# ─── 1. Verificar herramientas ──────────────────────────────────────────────
Write-Paso "Verificando PyInstaller..."
if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "  PyInstaller no encontrado; instalando..." -ForegroundColor Yellow
    python -m pip install pyinstaller
}
Write-Paso "Verificando NSIS (makensis)..."
if (-not (Get-Command makensis -ErrorAction SilentlyContinue)) {
    # Ruta típica de instalación de NSIS no incluida en PATH.
    $candidato = "$env:ProgramFiles(x86)\NSIS\makensis.exe"
    if (Test-Path $candidato) {
        Set-Alias makensis $candidato
        Write-Bien "makensis localizado en: $candidato"
    } else {
        Write-Error "NSIS no encontrado. Instálalo desde https://nsis.sourceforge.io"
        exit 1
    }
}

# ─── 2. Limpiar compilaciones anteriores ────────────────────────────────────
Write-Paso "Limpiando build/ y dist/snapcontext.exe..."
Remove-Item build -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item dist\snapcontext.exe -Force -ErrorAction SilentlyContinue

# ─── 3. PyInstaller ─────────────────────────────────────────────────────────
if ($Full) {
    Write-Paso "Ejecutando PyInstaller (modo FULL con extras pesados)..."
    $env:SNAPCONTEXT_EXE_FULL = "1"
} else {
    Write-Paso "Ejecutando PyInstaller (versión ligera)..."
    Remove-Item Env:\SNAPCONTEXT_EXE_FULL -ErrorAction SilentlyContinue
}
pyinstaller snapcontext.spec --noconfirm
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "dist\snapcontext.exe")) {
    Write-Error "PyInstaller falló."
    exit 1
}
$tamanoExe = "{0:N1} MB" -f ((Get-Item dist\snapcontext.exe).Length / 1MB)
Write-Bien "dist\snapcontext.exe generado ($tamanoExe)"

# Comprobación rápida de que el ejecutable responde.
Write-Paso "Verificando snapcontext.exe --version ..."
$salida = & dist\snapcontext.exe --version 2>&1 | Out-String
if ($salida -notmatch [regex]::Escape($version)) {
    Write-Warning "El ejecutable no reportó la versión esperada ($version):"
    Write-Host $salida -ForegroundColor Yellow
}

# ─── 4. NSIS ────────────────────────────────────────────────────────────────
Write-Paso "Generando instalador con NSIS..."
makensis installer.nsi
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "dist\SnapContext-Setup-$version.exe")) {
    Write-Error "NSIS falló."
    exit 1
}
$tamanoSetup = "{0:N1} MB" -f ((Get-Item "dist\SnapContext-Setup-$version.exe").Length / 1MB)
Write-Bien "dist\SnapContext-Setup-$version.exe generado ($tamanoSetup)"

# ─── 5. Resumen ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Blue
Write-Host "║  ¡Empaquetado completado! 🎉                     ║" -ForegroundColor Blue
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Blue
Write-Host ""
Write-Host "  Instalador : dist\SnapContext-Setup-$version.exe"
Write-Host "  Ejecutable : dist\snapcontext.exe"
Write-Host ""
Write-Host "  Sube el instalador a GitHub Releases (o el marketplace)."
Write-Host "  El usuario final NO necesita Python ni Aider preinstalados" -ForegroundColor Gray
Write-Host "  (aider-chat se instala aparte si se usa el modo edición)." -ForegroundColor Gray
