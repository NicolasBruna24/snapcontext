# SnapContext Installer — Windows
# Uso: irm https://NicolasBruna24.github.io/snapcontext/install.ps1 | iex
param([switch]$Help)

$ErrorActionPreference = "Stop"

function Write-Color {
    param([string]$Text, [string]$Color = "White")
    Write-Host $Text -ForegroundColor $Color
}

function Write-Info    { Write-Color "ℹ $args" "Cyan" }
function Write-OK     { Write-Color "✔ $args" "Green" }
function Write-Warn   { Write-Color "⚠ $args" "Yellow" }
function Write-Error  { Write-Color "✖ $args" "Red" }

# ─── Banner ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Color "╔══════════════════════════════════════════╗" "Blue"
Write-Color "║        SnapContext Installer            ║" "Blue"
Write-Color "╚══════════════════════════════════════════╝" "Blue"
Write-Host ""

# ─── Ayuda ────────────────────────────────────────────────────────────────
if ($Help) {
    Write-Host "SnapContext Installer - Windows"
    Write-Host "Uso: irm https://NicolasBruna24.github.io/snapcontext/install.ps1 | iex"
    exit 0
}

# ─── 1. Verificar Python ──────────────────────────────────────────────────
$python = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $python = (Get-Command $cmd -ErrorAction Stop).Source
        break
    } catch { continue }
}

if (-not $python) {
    Write-Error "Python no encontrado. Instala Python 3.9+ desde https://python.org"
    exit 1
}

$rawVersion = & $python --version 2>&1
if ($rawVersion -match "Python (\d+)\.(\d+)") {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
        Write-Error "Se requiere Python ≥ 3.9. Versión actual: $rawVersion"
        exit 1
    }
    Write-OK "Python $($Matches[1]).$($Matches[2]) encontrado"
} else {
    Write-Error "No se pudo determinar la versión de Python"
    exit 1
}

# ─── 2. Instalar uv si no está ────────────────────────────────────────────
$uvExists = $false
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvExists = $true
    Write-OK "uv ya está instalado"
} else {
    Write-Info "Instalando uv (gestor rápido de paquetes Python)..."
    try {
        $tempScript = Join-Path $env:TEMP "install-uv.ps1"
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $tempScript -UseBasicParsing
        & $tempScript
        Remove-Item $tempScript -Force -ErrorAction SilentlyContinue
        # Añadir al PATH de la sesión
        foreach ($path in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")) {
            if (Test-Path $path) { $env:PATH = "$path;$env:PATH" }
        }
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $uvExists = $true
            Write-OK "uv instalado correctamente"
        } else {
            Write-Warn "uv instalado pero no en PATH. Se usará pip."
        }
    } catch {
        Write-Warn "Error instalando uv: $_"
        Write-Warn "Se usará pip como fallback."
    }
}

# ─── 3. Instalar SnapContext ──────────────────────────────────────────────
Write-Info "Instalando SnapContext..."
if ($uvExists) {
    & uv tool install snapcontext 2>&1 | Select-Object -Last 1
    $userBin = "$env:USERPROFILE\.local\bin"
    if (Test-Path $userBin) { $env:PATH = "$userBin;$env:PATH" }
} else {
    & $python -m pip install --upgrade pip 2>&1 | Out-Null
    & $python -m pip install snapcontext 2>&1 | Select-Object -Last 1
}

# ─── 4. Verificar ─────────────────────────────────────────────────────────
Write-Host ""
if (Get-Command snapcontext -ErrorAction SilentlyContinue) {
    Write-OK "SnapContext instalado correctamente"
    $versionLine = & snapcontext --version 2>&1 | Select-Object -First 1
    Write-Info "Versión: $versionLine"
} else {
    Write-Warn "snapcontext no está en el PATH."
    Write-Host "  Añade esta línea a tu perfil de PowerShell (`$PROFILE):"
    Write-Host '    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"'
    Write-Host "  Luego abre una nueva terminal."
}

# ─── 5. Mensaje final ─────────────────────────────────────────────────────
Write-Host ""
Write-Color "╔══════════════════════════════════════════╗" "Blue"
Write-Color "║     ¡Instalación completada! 🎉         ║" "Blue"
Write-Color "╚══════════════════════════════════════════╝" "Blue"
Write-Host ""
Write-Host "  Ejemplos de uso:"
Write-Host "    snapcontext --version"
Write-Host '    snapcontext "el botón de pago no funciona"'
Write-Host '    snapcontext "revisar login" --experto'
Write-Host ""
Write-Host "  Configura tu API key:"
Write-Host '    $env:GEMINI_API_KEY = "tu_clave"'
Write-Host ""