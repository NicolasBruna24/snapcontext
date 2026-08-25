# SnapContext Installer - Windows
# Uso: irm https://NicolasBruna24.github.io/snapcontext/install.ps1 | iex
param([switch]$Help)

$ErrorActionPreference = "Stop"

function Write-Color {
    param([string]$Text, [string]$Color = "White")
    Write-Host $Text -ForegroundColor $Color
}

function Write-Info    { Write-Color "ℹ $args" "Cyan" }
function Write-OK      { Write-Color "✔ $args" "Green" }
function Write-Warn    { Write-Color "⚠ $args" "Yellow" }
function Write-Err     { Write-Color "✖ $args" "Red" }

# ─── Banner ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Color "╔══════════════════════════════════════════╗" "Blue"
Write-Color "║        SnapContext Installer             ║" "Blue"
Write-Color "╚══════════════════════════════════════════╝" "Blue"
Write-Host ""

# ─── Ayuda ─────────────────────────────────────────────────────────────────
if ($Help) {
    Write-Host "SnapContext Installer - Windows"
    Write-Host "Uso: irm https://NicolasBruna24.github.io/snapcontext/install.ps1 | iex"
    exit 0
}

# ─── 1. Verificar Python (PATH, rutas comunes y lanzador py) ───────────────
# Devuelve la ruta completa a un ejecutable de Python válido o $null.
function Find-Python {
    # 1a) En el PATH (descartando el stub de la Microsoft Store).
    foreach ($cmd in @("python", "python3")) {
        try {
            $origen = (Get-Command $cmd -ErrorAction Stop).Source
            if ($origen -and $origen -notlike "*WindowsApps*") {
                return $origen
            }
        } catch { continue }
    }
    # 1b) Lanzador oficial 'py' (instalador de python.org).
    try {
        $salida = & py -3 -c "import sys; print(sys.executable)" 2>$null |
            Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -and $salida -and (Test-Path $salida)) {
            return $salida
        }
    } catch { }
    # 1c) Rutas de instalación típicas.
    $raices = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "C:\Program Files\Python",
        "C:\Program Files (x86)\Python"
    )
    foreach ($raiz in $raices) {
        if (-not (Test-Path $raiz)) { continue }
        $exe = Get-ChildItem $raiz -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "python.exe" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($exe) { return $exe }
    }
    return $null
}

$python = Find-Python

if (-not $python) {
    Write-Err "Python no encontrado. Instala Python 3.9+ desde https://python.org"
    Write-Host ""
    Write-Host "  Durante la instalación marca la casilla:"
    Write-Color "    ☑ 'Add python.exe to PATH'" "Yellow"
    Write-Host ""
    Write-Host "  Alternativa por línea de comandos (winget):"
    Write-Host "    winget install -e --id Python.Python.3.12"
    Write-Host ""
    Write-Host "  Después vuelve a ejecutar este instalador."
    exit 1
}

# Si Python existe pero no estaba en el PATH, intentar añadirlo.
$pythonDir = Split-Path $python -Parent
$enPath = $false
foreach ($cmd in @("python", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { $enPath = $true; break }
}
if (-not $enPath) {
    Write-Warn "Python está instalado pero no está en el PATH: $python"
    try {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($userPath -notlike "*$pythonDir*") {
            [Environment]::SetEnvironmentVariable(
                "Path", "$pythonDir;$userPath", "User")
            $env:PATH = "$pythonDir;$env:PATH"
            Write-OK "Ruta de Python añadida al PATH del usuario: $pythonDir"
            Write-Warn "Abre una terminal nueva para que el cambio sea global."
        } else {
            Write-Info "La ruta de Python ya está en el PATH del usuario."
        }
    } catch {
        Write-Warn "No se pudo modificar el PATH automáticamente ($_)."
        Write-Host "  Añade manualmente '$pythonDir' al PATH del usuario:"
        Write-Host ('    [Environment]::SetEnvironmentVariable("Path", "' +
            "$pythonDir" + ';" + $env:Path + "", "User")')
    }
}

# ─── 1b. Comprobar versión de Python ───────────────────────────────────────
$rawVersion = & $python --version 2>&1
if ($rawVersion -match "Python (\d+)\.(\d+)") {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
        Write-Err "Se requiere Python ≥ 3.9. Versión actual: $rawVersion"
        Write-Host "  Actualiza Python desde https://python.org y reintenta."
        exit 1
    }
    Write-OK "Python $($Matches[1]).$($Matches[2]) encontrado"
} else {
    Write-Err "No se pudo determinar la versión de Python ($rawVersion)"
    exit 1
}

# ─── 2. Instalar uv si no está ─────────────────────────────────────────────
$uvExists = $false
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvExists = $true
    Write-OK "uv ya está instalado"
} else {
    Write-Info "Instalando uv (gestor rápido de paquetes Python)..."
    try {
        $tempScript = Join-Path $env:TEMP "install-uv.ps1"
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" `
            -OutFile $tempScript -UseBasicParsing
        # Ejecutar el instalador de uv con ExecutionPolicy Bypass: la política de
        # ejecución del sistema puede bloquear el script descargado, y eso hace
        # fallar la instalación de uv en PCs nuevos (v3.4.0).
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $tempScript
        Remove-Item $tempScript -Force -ErrorAction SilentlyContinue
        # Añadir al PATH de la sesión
        foreach ($path in @("$env:USERPROFILE\.local\bin",
                            "$env:USERPROFILE\.cargo\bin")) {
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

# ─── 2b. Limpiar instalaciones previas de SnapContext con uv ───────────────
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

# ─── Helper: añadir una ruta al PATH del usuario sin duplicados ────────────
function Add-UserPath {
    param([string]$Ruta)
    if ([string]::IsNullOrWhiteSpace($Ruta) -or -not (Test-Path $Ruta)) {
        return $false
    }
    try {
        $actual = [Environment]::GetEnvironmentVariable("Path", "User")
        $partes = @()
        if ($actual) { $partes = $actual -split ';' | Where-Object { $_ } }
        if ($partes -contains $Ruta) {
            Write-Info "'$Ruta' ya está en el PATH del usuario."
            return $true
        }
        $nuevo = ($Ruta + ';' + $actual).Trim(';')
        [Environment]::SetEnvironmentVariable("Path", $nuevo, "User")
        if ("$env:PATH" -notlike "*$Ruta*") { $env:PATH = "$Ruta;$env:PATH" }
        Write-OK "Ruta '$Ruta' añadida al PATH del usuario (permanente)."
        return $true
    } catch {
        Write-Warn "No se pudo modificar el PATH del usuario ($_)."
        return $false
    }
}

# ─── 2c. Limpiar entornos corruptos de uv (v2.3.0) ─────────────────────────
# Una instalación previa interrumpida puede dejar %APPDATA%\uv\tools\
# snapcontext vacía o sin ejecutable, y uv se niega a reinstalar sobre ella
# ("Invalid environment ... directory is empty"). La detectamos y borramos.
function Test-UvEnvCorrupto {
    param([string]$Ruta)
    if (-not (Test-Path $Ruta)) { return $false }
    $hijos = Get-ChildItem $Ruta -Force -ErrorAction SilentlyContinue
    if (-not $hijos -or $hijos.Count -eq 0) { return $true }   # carpeta vacía
    # Un entorno sano tiene un venv con python.exe y el shim de snapcontext.
    $exeVenv = @(
        (Join-Path $Ruta "Scripts\python.exe"),
        (Join-Path $Ruta "bin\python"),
        (Join-Path $Ruta "bin\python3")
    ) | Where-Object { Test-Path $_ }
    if (-not $exeVenv) { return $true }                        # sin intérprete
    $shims = @(
        (Join-Path $Ruta "Scripts\snapcontext.exe"),
        (Join-Path $Ruta "bin\snapcontext")
    ) | Where-Object { Test-Path $_ }
    if (-not $shims) { return $true }                          # sin entrypoint
    return $false
}

$rutasUvTool = @(
    (Join-Path $env:APPDATA "uv\tools\snapcontext"),
    (Join-Path $env:LOCALAPPDATA "uv\tools\snapcontext")
) | Where-Object { $_ -and (Test-Path $_) }

foreach ($ruta in $rutasUvTool) {
    if (Test-UvEnvCorrupto -Ruta $ruta) {
        Write-Info "Limpiando instalación previa corrupta de SnapContext..."
        try {
            Remove-Item $ruta -Recurse -Force -ErrorAction Stop
            Write-OK "Entorno corrupto eliminado: $ruta"
        } catch {
            Write-Warn "No se pudo eliminar '$ruta' ($_)."
            Write-Host "  Cierra otras terminales y vuelve a ejecutar el instalador,"
            Write-Host "  o borra la carpeta manualmente."
        }
    }
}

# ─── 2e. Limpiar carpetas corruptas de pip/uv en site-packages (v3.4.0) ─────
# Las instalaciones interrumpidas dejan restos '~ip', '~napcontext', ... dentro
# de site-packages. Esas carpetas temporales (cuyo nombre empieza por '~') hacen
# que 'uv tool install' falle con errores de entorno / conflicto. Se detectan y
# eliminan antes de instalar.
function Clear-SitePackagesCorruptas {
    $sitios = @()
    $res = (& $python -c "import site; print('\n'.join(site.getsitepackages()))" 2>$null)
    if ($res) { $sitios += $res -split "`n" }
    $resUser = (& $python -c "import site; print(site.getusersitepackages())" 2>$null)
    if ($resUser) { $sitios += $resUser }
    foreach ($sitio in $sitios) {
        if (-not $sitio -or -not (Test-Path $sitio)) { continue }
        $corruptos = Get-ChildItem $sitio -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like '~*' }
        foreach ($carpeta in $corruptos) {
            Write-Warn "Carpeta corrupta de instalación en site-packages: $($carpeta.Name)"
            try {
                Remove-Item $carpeta.FullName -Recurse -Force -ErrorAction Stop
                Write-OK "Carpeta corrupta eliminada: $($carpeta.FullName)"
            } catch {
                Write-Warn "No se pudo eliminar '$($carpeta.FullName)' ($_)."
                Write-Host "  Cierra otras terminales y reintenta, o bórrala manualmente."
            }
        }
    }
}
Clear-SitePackagesCorruptas

# ─── 3. Instalar SnapContext ───────────────────────────────────────────────
# ─── 3. Instalar SnapContext ───────────────────────────────────────────────
Write-Info "Instalando SnapContext..."
$instalado = $false

if ($uvExists) {
    try {
        & uv tool install snapcontext --upgrade 2>&1 | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0) { $instalado = $true }
    } catch {
        Write-Warn "'uv tool install' falló: $_"
    }
    if (-not $instalado) {
        # v3.4.0: reintento con --force por si quedó algún entorno residual.
        Write-Warn "La instalación con uv no terminó bien; reintentando con '--force'..."
        try {
            & uv tool install --force snapcontext 2>&1 | Select-Object -Last 1
            if ($LASTEXITCODE -eq 0) { $instalado = $true }
        } catch {
            Write-Warn "'uv tool install --force' también falló: $_"
        }
    }
    $userBin = "$env:USERPROFILE\.local\bin"
    if ($instalado) {
        if (Test-Path $userBin) { $env:PATH = "$userBin;$env:PATH" }
        # Persistir %USERPROFILE%\.local\bin en el PATH del usuario.
        if (-not (Add-UserPath -Ruta $userBin)) {
            Write-Warn "Añade '$userBin' al PATH manualmente o ejecuta:"
            Write-Host "    snapcontext --setup-path"
        }
    } else {
        Write-Warn "La instalación con uv no terminó bien; probando con pip..."
    }
}

if (-not $instalado) {
    # ── Fallback robusto a pip ──
    Write-Info "Instalando con pip..."
    & $python -m pip install --upgrade pip 2>&1 | Out-Null
    & $python -m pip install --upgrade snapcontext 2>&1 | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "No se pudo instalar SnapContext ni con uv ni con pip."
        Write-Host ""
        Write-Host "  Revisa tu conexión y permisos, y prueba manualmente:"
        Write-Host "    $python -m pip install --upgrade snapcontext"
        Write-Host ""
        Write-Host "  Si ya estaba instalado y solo está roto, fuerza la reinstalación:"
        Write-Host "    $python -m pip install --force-reinstall snapcontext"
        Write-Host ""
        exit 1
    }

    # Añadir la carpeta de Scripts del usuario al PATH (pip user installs).
    $sitePackages = & $python -c `
        "import site; print(site.getusersitepackages())" 2>$null |
        Select-Object -First 1
    $scriptsPath = $null
    if ($sitePackages -and "$sitePackages".Contains("site-packages")) {
        $scriptsPath = "$sitePackages".Replace("site-packages", "Scripts")
    } else {
        # Fallback: localizar snapcontext.exe en rutas típicas de pip.
        foreach ($candidato in @("$env:APPDATA\Python\Scripts",
                                 "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts",
                                 "$env:LOCALAPPDATA\Programs\Python\Python311\Scripts")) {
            if (Test-Path "$candidato\snapcontext.exe") {
                $scriptsPath = $candidato
                break
            }
        }
    }
    if ($scriptsPath -and (Test-Path "$scriptsPath\snapcontext.exe")) {
        if (-not (Add-UserPath -Ruta $scriptsPath)) {
            Write-Warn "Añade '$scriptsPath' al PATH manualmente o ejecuta:"
            Write-Host "    snapcontext --setup-path"
        }
    } else {
        Write-Warn "No se localizó snapcontext.exe tras la instalación con pip."
        Write-Host "  Ejecuta 'snapcontext --setup-path' o añade la carpeta"
        Write-Host "  de Scripts de Python al PATH manualmente."
    }
}

# ─── 4. Verificar ──────────────────────────────────────────────────────────
Write-Host ""
$snapCmd = Get-Command snapcontext -ErrorAction SilentlyContinue
if ($snapCmd) {
    Write-OK "SnapContext instalado correctamente."
    $versionLine = & snapcontext --version 2>&1 | Select-Object -First 1
    Write-Info "Versión: $versionLine"
} else {
    Write-Warn "snapcontext no está en el PATH de la sesión actual."
    Write-Host "  Abre una terminal nueva, o ejecuta manualmente:"
    Write-Host '    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"'
    Write-Host "  También puedes usar: snapcontext --setup-path"
}

# v3.4.0: verificar que el módulo de Python se importa correctamente.
Write-Info "Comprobando que el módulo se importa correctamente..."
$modVersion = & $python -m snapcontext --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-OK "Módulo de Python OK: $($modVersion | Select-Object -Last 1)"
} else {
    Write-Warn "El módulo no responde aún. Reinstala con:"
    Write-Host "    $python -m pip install --force-reinstall snapcontext"
}

# ─── 5. Mensaje final ──────────────────────────────────────────────────────
Write-Host ""
Write-Color "╔══════════════════════════════════════════╗" "Blue"
Write-Color "║     ¡Instalación completada! 🎉          ║" "Blue"
Write-Color "╚══════════════════════════════════════════╝" "Blue"
Write-Host ""

if ($snapCmd) {
    Write-OK "SnapContext instalado correctamente. Ejecuta 'snapcontext --help' para empezar."
    Write-Info "Reinicia tu terminal para que los cambios del PATH surtan efecto."
} else {
    Write-Warn "Instalación completada pero el comando aún no está disponible en esta sesión."
}

Write-Host ""
Write-Host "  Ejemplos de uso:"
Write-Host "    snapcontext --version"
Write-Host '    snapcontext "el botón de pago no funciona"'
Write-Host '    snapcontext "revisar login" --experto'
Write-Host ""
Write-Host "  Configura tu API key (opcional):"
Write-Host '    $env:GEMINI_API_KEY = "tu_clave"'
Write-Host ""
Write-Host "  Sin API key, SnapContext 3.4.0 usa Ollama en modo offline"
Write-Host "  automaticamente. Instalalo desde https://ollama.com y ejecuta:"
Write-Host "    ollama pull llama3.2"
Write-Host ""
Write-Host "  Verifica tu instalacion:"
Write-Host "    snapcontext --diagnostico"
Write-Host ""

