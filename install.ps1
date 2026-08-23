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
function Write-Err   { Write-Color "✖ $args" "Red" }

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
    Write-Err "Python no encontrado. Instala Python 3.9+ desde https://python.org"
    exit 1
}

$rawVersion = & $python --version 2>&1
if ($rawVersion -match "Python (\d+)\.(\d+)") {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
        Write-Err "Se requiere Python ≥ 3.9. Versión actual: $rawVersion"
        exit 1
    }
    Write-OK "Python $($Matches[1]).$($Matches[2]) encontrado"
} else {
    Write-Err "No se pudo determinar la versión de Python"
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

# ─── 2b. Limpiar instalaciones previas de SnapContext con uv ──────────────
# Un snapcontext antiguo instalado con 'uv tool' vive en %USERPROFILE%\.local\bin
# y puede tapar al nuevo en el PATH (bug conocido de la v1.2.0).
$uvPrevio = $false
if ($uvExists) {
    try {
        $lista = & uv tool list 2>$null
        if ($lista -match '^snapcontext') { $uvPrevio = $true }
    } catch { }
}
if (-not $uvPrevio -and (Test-Path "$env:USERPROFILE\.local\bin\snapcontext.exe")) {
    $uvPrevio = $true
}

if ($uvPrevio) {
    Write-Warn "Se detectó una instalación previa de SnapContext vía 'uv tool'."
    $respuesta = "s"
    if (-not [Console]::IsInputRedirected) {
        $respuesta = Read-Host "¿Eliminarla para evitar conflictos de PATH? (s/n)"
    } else {
        Write-Info "Ejecución no interactiva: se elimina automáticamente."
    }
    if ($respuesta -notmatch '^[nN]') {
        if ($uvExists) {
            & uv tool uninstall snapcontext 2>&1 | Select-Object -Last 1
        } else {
            Remove-Item "$env:USERPROFILE\.local\bin\snapcontext.exe" -Force `
                -ErrorAction SilentlyContinue
        }
        Write-OK "Instalación previa con uv eliminada."
    } else {
        Write-Warn "Se conserva la instalación con uv; puede haber conflictos de versión."
    }
}

# ─── 3. Instalar SnapContext ──────────────────────────────────────────────
Write-Info "Instalando SnapContext..."
if ($uvExists) {
    & uv tool install snapcontext 2>&1 | Select-Object -Last 1
    $userBin = "$env:USERPROFILE\.local\bin"
    if (Test-Path $userBin) { $env:PATH = "$userBin;$env:PATH" }

    # Persistir %USERPROFILE%\.local\bin en el PATH del usuario si falta,
    # para que 'snapcontext' funcione en terminales nuevas.
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$userBin*") {
        [Environment]::SetEnvironmentVariable("Path", "$userBin;$userPath", "User")
        Write-OK "✓ Ruta '$userBin' añadida al PATH del usuario"
    }
} else {
    & $python -m pip install --upgrade pip 2>&1 | Out-Null
    & $python -m pip install snapcontext 2>&1 | Select-Object -Last 1
}

# ─── Añadir automáticamente al PATH del usuario (pip) ──────────────────────
if (-not $uvExists) {
    Write-Info "Añadiendo carpeta de ejecutable al PATH del usuario permanentemente..."
    
    # Obtener la ruta del sitio de paquetes de usuario
    try {
        $sitePackages = & $python -c "import site; print(site.getusersitepackages())" 2>&1 | Select-Object -First 1 -Trim
        
        if (-not [string]::IsNullOrWhiteSpace($sitePackages)) {
            # Reemplazar 'site-packages' por 'Scripts' para obtener la ruta del ejecutable
            $scriptsPath = $sitePackages.Replace('site-packages', 'Scripts')
            
            # Verificar si el ejecutable existe
            if (Test-Path "$scriptsPath\snapcontext.exe") {
                # Añadir al PATH del usuario permanentemente (evitando duplicados)
                $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
                
                if ($currentPath -notlike "*$scriptsPath*") {
                    $newPath = "$scriptsPath;$currentPath"
                    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
                    Write-OK "✓ Ruta '$scriptsPath' añadida al PATH de usuario"
                } else {
                    Write-Info "Ruta ya presente en el PATH del usuario (sin cambios necesarios)"
                }
            } else {
                Write-Warn "El ejecutable no se encuentra en: $scriptsPath\snapcontext.exe"
                Write-Host "  Intentando localizar snapcontext.exe..."
                
                # Fallback: buscar en rutas comunes de pip
                foreach ($candidate in @("$env:APPDATA\Python\Scripts", "$env:LOCALAPPDATA\Programs\Python\Python3*\Scripts", $scriptsPath)) {
                    if (Test-Path "$candidate\snapcontext.exe") {
                        # Añadir al PATH si no está presente
                        $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
                        if ($currentPath -notlike "*$candidate*") {
                            $newPath = "$candidate;$currentPath"
                            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
                            Write-OK "✓ Ruta '$candidate' añadida al PATH de usuario (fallback)"
                        } else {
                            Write-Info "Ruta ya presente en el PATH del usuario"
                        }
                        break
                    }
                }
            }
        } else {
            Write-Warn "No se pudo obtener la ruta del sitio de paquetes. Se omitirá la configuración automática."
            Write-Host "  Nota: Para instalar sin usar el one-liner, usa 'snapcontext --setup-path' después de pip install"
        }
    } catch {
        Write-Warn "Error detectando PATH: $_"
        Write-Info "La instalación funcionará normalmente; solo omite la configuración automática del PATH."
    }
}

# ─── 4. Verificar ──────────────────────────────────────────────────────────
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

if (Get-Command snapcontext -ErrorAction SilentlyContinue) {
    Write-OK "SnapContext instalado correctamente"
    
    # Verificar si PATH se configuró automáticamente
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $uvExists) {
        $sitePackages = & $python -c "import site; print(site.getusersitepackages())" 2>&1 | Select-Object -First 1 -Trim
        $scriptsPath = $sitePackages.Replace('site-packages', 'Scripts')
        
        if ($currentPath -like "*$scriptsPath*") {
            Write-OK "✓ El PATH del usuario incluye la carpeta de SnapContext"
            Write-Info "Reinicia tu terminal para que los cambios surtan efecto."
            Write-Info "O ejecuta 'refreshenv' (si tienes Chocolatey instalado)."
        } else {
            Write-Info "ℹ SnapContext funciona, pero la carpeta no se encontró automáticamente en el PATH."
            Write-Host "  Puedes añadir manualmente '$scriptsPath' al PATH de usuario:"
            Write-Host '  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";$scriptsPath", "User")'
        }
    } else {
        Write-OK "✓ El PATH del usuario incluye la carpeta de SnapContext (ya configurado por uv)"
        Write-Info "Reinicia tu terminal para que los cambios surtan efecto."
        Write-Info "O ejecuta 'refreshenv' (si tienes Chocolatey instalado)."
    }
    
    $versionLine = & snapcontext --version 2>&1 | Select-Object -First 1
    Write-Info "Versión: $versionLine"
} else {
    Write-Warn "snapcontext no está en el PATH de la sesión actual."
    if (-not $uvExists) {
        Write-Host "  Intenta ejecutar: snapcontext --setup-path"
    } else {
        Write-Host "  Añade esta línea a tu perfil de PowerShell (`$PROFILE):"
        Write-Host '    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"'
        Write-Host "  Luego abre una nueva terminal."
    }
}

Write-Host ""
Write-Host "  Ejemplos de uso:"
Write-Host "    snapcontext --version"
Write-Host '    snapcontext "el botón de pago no funciona"'
Write-Host '    snapcontext "revisar login" --experto'
Write-Host ""
Write-Host "  Configura tu API key:"
Write-Host '    $env:GEMINI_API_KEY = "tu_clave"'
Write-Host ""