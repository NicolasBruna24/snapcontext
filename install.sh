#!/bin/bash
# SnapContext Installer — Linux / macOS
# Uso: curl -LsSf https://NicolasBruna24.github.io/snapcontext/install.sh | sh
set -euo pipefail

# ─── Colores ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { printf "${CYAN}ℹ${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✔${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()   { printf "${RED}✖${NC} %s\n" "$*" >&2; }

# ─── Banner ───────────────────────────────────────────────────────────────
echo ""
printf "${BLUE}╔══════════════════════════════════════════╗${NC}\n"
printf "${BLUE}║        SnapContext Installer             ║${NC}\n"
printf "${BLUE}╚══════════════════════════════════════════╝${NC}\n"
echo ""

# ─── 1. Detectar / verificar Python ───────────────────────────────────────
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_CMD="$(command -v "$cmd")"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    err "Python no encontrado. Instala Python 3.9+ y vuelve a ejecutar este instalador."
    echo ""
    OS_UNAME="$(uname -s)"
    if [ "$OS_UNAME" = "Darwin" ]; then
        echo "  macOS (Homebrew):   brew install python"
        echo "  macOS (python.org): https://www.python.org/downloads/"
    elif command -v apt-get &>/dev/null; then
        echo "  Debian/Ubuntu:  sudo apt update && sudo apt install python3 python3-pip"
    elif command -v dnf &>/dev/null; then
        echo "  Fedora:         sudo dnf install python3 python3-pip"
    elif command -v pacman &>/dev/null; then
        echo "  Arch:           sudo pacman -S python python-pip"
    else
        echo "  Descarga Python desde https://www.python.org/downloads/"
    fi
    exit 1
fi

RAW_VERSION=$("$PYTHON_CMD" --version 2>&1 | awk '{print $2}')
MAJOR=$(echo "$RAW_VERSION" | cut -d. -f1)
MINOR=$(echo "$RAW_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
    err "Se requiere Python ≥ 3.9. Versión actual: $RAW_VERSION"
    echo "  Actualiza Python (por ej.: brew upgrade python o el gestor de tu distro)."
    exit 1
fi
ok "Python $RAW_VERSION encontrado: $PYTHON_CMD"

# ─── 1b. Inyectar ~/.local/bin en el PATH de la sesión ─────────────────────
# No dependemos del PATH global del sistema (los PCs institucionales suelen
# arrancar limpio): aseguramos que la carpeta de herramientas del usuario esté
# disponible durante esta instalación. Lo hacemos antes de cualquier comando.
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ; ok "Añadido $HOME/.local/bin al PATH de la sesión." ;;
esac

# Con Python >= 3.14 'uv' aún no crea entornos compatibles y falla. Saltamos
# 'uv' por completo y vamos directamente al proceso optimizado con pip.
SALT_UV=false
if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 14 ]; then
    SALT_UV=true
    warn "Python ≥ 3.14 detectado: se omitirá 'uv' (aún no compatible) y se usará pip directamente."
fi

# ─── 2. Instalar uv si no está ────────────────────────────────────────────
UV_EXISTS=false
if [ "$SALT_UV" = true ]; then
    warn "Instalación de 'uv' omitida (Python ≥ 3.14). Se usará pip."
elif command -v uv &>/dev/null; then
    UV_EXISTS=true
    ok "uv ya está instalado"
else
    info "Instalando uv (gestor rápido de paquetes Python)..."
    if ! command -v curl &>/dev/null; then
        warn "curl no disponible; se usará pip directamente."
    elif ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        warn "No se pudo instalar uv. Se usará pip como fallback."
    else
        # Añadir uv al PATH de la sesión actual
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        if command -v uv &>/dev/null; then
            UV_EXISTS=true
            ok "uv instalado correctamente"
        else
            warn "uv instalado pero no en PATH. Fallback a pip."
        fi
    fi
fi

# ─── 2b. Limpiar entornos corruptos de uv (v2.3.0) ────────────────────────
# Una instalación previa interrumpida puede dejar la carpeta del entorno de
# 'uv tool' vacía o sin ejecutable, bloqueando nuevas instalaciones con el
# error "Invalid environment ... directory is empty". La detectamos y borramos.
UV_TOOLS_SNAP=""
for candidato in \
    "${XDG_DATA_HOME:-$HOME/.local/share}/uv/tools/snapcontext" \
    "$HOME/.local/share/uv/tools/snapcontext" \
    "$HOME/Library/Application Support/uv/tools/snapcontext"; do
    if [ -d "$candidato" ]; then
        UV_TOOLS_SNAP="$candidato"
        break
    fi
done

if [ -n "$UV_TOOLS_SNAP" ]; then
    CORRUPTO=false
    # Carpeta vacía.
    if [ -z "$(ls -A "$UV_TOOLS_SNAP" 2>/dev/null)" ]; then
        CORRUPTO=true
    fi
    # Sin intérprete de Python dentro del venv.
    if [ "$CORRUPTO" = false ] \
        && [ ! -x "$UV_TOOLS_SNAP/bin/python" ] \
        && [ ! -x "$UV_TOOLS_SNAP/bin/python3" ] \
        && [ ! -x "$UV_TOOLS_SNAP/Scripts/python.exe" ]; then
        CORRUPTO=true
    fi
    # Sin entrypoint de snapcontext.
    if [ "$CORRUPTO" = false ] \
        && [ ! -e "$UV_TOOLS_SNAP/bin/snapcontext" ] \
        && [ ! -e "$UV_TOOLS_SNAP/Scripts/snapcontext.exe" ]; then
        CORRUPTO=true
    fi

    if [ "$CORRUPTO" = true ]; then
        info "Limpiando instalación previa corrupta de SnapContext..."
        if rm -rf "$UV_TOOLS_SNAP"; then
            ok "Entorno corrupto eliminado: $UV_TOOLS_SNAP"
        else
            warn "No se pudo eliminar '$UV_TOOLS_SNAP'. Borra la carpeta manualmente."
        fi
    fi
fi

# ─── 2c. Limpiar instalaciones previas de SnapContext con uv ──────────────
# Evita que un snapcontext antiguo (vía uv tool) en ~/.local/bin tape al nuevo.
if [ "$SALT_UV" != true ] && command -v uv >/dev/null 2>&1 && [ -n "$(uv tool list 2>/dev/null | grep '^snapcontext')" ]; then
    warn "Se detectó una instalación previa de SnapContext vía 'uv tool'."
    RESPUESTA="s"   # por defecto, eliminar (instalaciones no interactivas)
    if [ -t 0 ]; then
        printf "%s" "¿Eliminarla para evitar conflictos de PATH? (s/n): "
        read -r RESPUESTA || RESPUESTA="s"
    else
        info "Ejecución no interactiva: se elimina automáticamente."
    fi
    case "$RESPUESTA" in
        [nN]*)
            warn "Se conserva la instalación con uv; puede haber conflictos de versión en el PATH."
            ;;
        *)
            uv tool uninstall snapcontext 2>&1 | tail -1
            ok "Instalación previa con uv eliminada."
            ;;
    esac
elif [ -x "$HOME/.local/bin/snapcontext" ] && ! command -v uv &>/dev/null; then
    warn "Encontrado snapcontext residual de una instalación previa con uv."
    rm -f "$HOME/.local/bin/snapcontext" && ok "Binario residual eliminado." \
        || warn "No se pudo eliminar automáticamente: $HOME/.local/bin/snapcontext"
fi

# ─── Helper: añadir ~/.local/bin al perfil del usuario si falta ───────────
asegurar_path_perfil() {
    export PATH="$HOME/.local/bin:$PATH"
    PERFIL=""
    if [ -n "${ZSH_VERSION:-}" ] && [ -f "$HOME/.zshrc" ]; then
        PERFIL="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        PERFIL="$HOME/.bashrc"
    elif [ -f "$HOME/.profile" ]; then
        PERFIL="$HOME/.profile"
    fi
    [ -n "$PERFIL" ] || return 0
    # grep comprueba si ya existe la línea (evita duplicados).
    if ! grep -q '\.local/bin' "$PERFIL" 2>/dev/null; then
        printf '\n# Añadido por el instalador de SnapContext\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$PERFIL"
        ok "Añadido ~/.local/bin al PATH en $PERFIL (abre una terminal nueva para aplicarlo)."
    else
        info "~/.local/bin ya figura en $PERFIL."
    fi
}

# ─── 2d. Limpiar carpetas corruptas de pip/uv en site-packages (v3.4.0) ────
# Las instalaciones interrumpidas dejan restos '~ip', '~napcontext', ... en
# site-packages que hacen fallar 'uv tool install'. Se eliminan antes de instalar.
limpiar_sitepackages_corruptos() {
    local sitios carpeta
    sitios=$("$PYTHON_CMD" -c "import site; print('\n'.join(site.getsitepackages() + [site.getusersitepackages()]))" 2>/dev/null || true)
    [ -z "$sitios" ] && return 0
    printf '%s\n' "$sitios" | while IFS= read -r sitio; do
        [ -n "$sitio" ] && [ -d "$sitio" ] || continue
        for carpeta in "$sitio"/~*; do
            [ -e "$carpeta" ] || continue
            warn "Carpeta corrupta en site-packages: $(basename "$carpeta")"
            if rm -rf "$carpeta"; then
                ok "Carpeta corrupta eliminada: $carpeta"
            else
                warn "No se pudo eliminar '$carpeta'. Bórrala manualmente."
            fi
        done
    done
}
# ─── 3. Instalar SnapContext ──────────────────────────────────────────────
limpiar_sitepackages_corruptos
info "Instalando SnapContext..."
INSTALADO=false

if [ "$UV_EXISTS" = true ]; then
    # Capturar la salida sin pipe para no enmascarar el código de salida de uv.
    if UV_SALIDA=$(uv tool install snapcontext --upgrade 2>&1); then
        printf '%s\n' "$UV_SALIDA" | tail -1
        INSTALADO=true
    else
        printf '%s\n' "$UV_SALIDA" | tail -1
        warn "La instalación con uv falló; reintentando con '--force'..."
        if UV_SALIDA=$(uv tool install --force snapcontext 2>&1); then
            printf '%s\n' "$UV_SALIDA" | tail -1
            INSTALADO=true
        else
            printf '%s\n' "$UV_SALIDA" | tail -1
            warn "uv con '--force' también falló; probando con pip..."
        fi
    fi
    # la persistencia del PATH se decide por pregunta al final del instalador.
fi

if [ "$INSTALADO" = false ]; then
    # ── Fallback robusto a pip (compatible también con Python ≥ 3.14) ──
    info "Instalando con pip (intérprete: $PYTHON_CMD)..."
    # 1) Actualizar pip primero (clave en versiones nuevas de Python).
    info "Actualizando pip..."
    "$PYTHON_CMD" -m pip install --upgrade pip >/dev/null 2>&1 || true
    # 2) Instalación optimizada para evitar el bucle de 'Backtracking' de la
    #    dependencia 'google-api-core': se fija una versión mínima y se desactiva
    #    la caché. Es el comando validado (escenario INACAP).
    info "Instalando snapcontext (sin caché, google-api-core>=2.24.0)..."
    if PIP_SALIDA=$("$PYTHON_CMD" -m pip install snapcontext "google-api-core>=2.24.0" --no-cache-dir 2>&1); then
        printf '%s\n' "$PIP_SALIDA" | tail -2
        INSTALADO=true
    else
        printf '%s\n' "$PIP_SALIDA" | tail -2
        err "No se pudo instalar SnapContext ni con uv ni con pip."
        echo ""
        echo "  Prueba manualmente:"
        echo "    $PYTHON_CMD -m pip install snapcontext 'google-api-core>=2.24.0' --no-cache-dir"
        echo ""
        echo "  Si ya estaba instalado y solo está roto, fuerza la reinstalación:"
        echo "    $PYTHON_CMD -m pip install --force-reinstall snapcontext"
        echo ""
        exit 1
    fi
    # (la persistencia del PATH se decide por pregunta al final del instalador)
fi

# ─── 4. Verificar ─────────────────────────────────────────────────────────
echo ""
if command -v snapcontext &>/dev/null; then
    ok "SnapContext instalado correctamente."
    VERSION_INSTALADA=$(snapcontext --version 2>&1 | head -1)
    info "Versión: $VERSION_INSTALADA"
else
    warn "snapcontext no está en el PATH."
    echo "  Añade esta línea a tu ~/.bashrc o ~/.zshrc:"
    echo '  export PATH="$HOME/.local/bin:$PATH"'
    echo "  Luego abre una nueva terminal o ejecuta: source ~/.bashrc"
fi

# v3.4.0: verificar que el módulo de Python se importa correctamente.
info "Comprobando que el módulo se importa correctamente..."
if "$PYTHON_CMD" -m snapcontext --version >/dev/null 2>&1; then
    ok "Módulo de Python OK."
else
    warn "El módulo no responde aún. Reinstala con:"
    echo "    $PYTHON_CMD -m pip install --force-reinstall snapcontext"
fi

# ─── 4bis. Persistencia opcional del PATH (se pregunta al usuario) ────────
info "Intérprete de Python usado: $PYTHON_CMD"
# Elegir el perfil del shell (zshrc > bashrc > profile).
PERFIL=""
if [ -n "${ZSH_VERSION:-}" ] && [ -f "$HOME/.zshrc" ]; then
    PERFIL="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    PERFIL="$HOME/.bashrc"
elif [ -f "$HOME/.profile" ]; then
    PERFIL="$HOME/.profile"
fi

# Solo preguntamos si el comando aún no es global o el perfil no lo incluye.
ALREADY=false
if [ -n "$PERFIL" ] && grep -q '\.local/bin' "$PERFIL" 2>/dev/null; then
    ALREADY=true
fi

if [ "$ALREADY" = false ]; then
    echo ""
    warn "Para que 'snapcontext' esté disponible en cualquier terminal hay que añadir ~/.local/bin al PATH."
    RESP="s"
    if [ -t 0 ]; then
        printf "%s" "¿Deseas agregar estas rutas al PATH permanentemente? (s/n): "
        read -r RESP || RESP="s"
    else
        info "Ejecución no interactiva: se agregan automáticamente."
    fi
    case "$RESP" in
        [sSyY]*)
            if [ -n "$PERFIL" ]; then
                printf '\n# Añadido por el instalador de SnapContext\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$PERFIL"
                ok "Añadido ~/.local/bin al PATH en $PERFIL (abre una terminal nueva)."
            else
                warn "No se encontró ningún perfil de shell. Añade manualmente:"
                echo '    export PATH="$HOME/.local/bin:$PATH"'
            fi
            ;;
        *)
            warn "No se modificó el PATH permanentemente. Puedes hacerlo luego con:"
            warn "    snapcontext --setup-path"
            ;;
    esac
else
    info "~/.local/bin ya está configurado en el perfil del shell."
fi

# ─── 5. Mensaje final ─────────────────────────────────────────────────────
echo ""
printf "${BLUE}╔══════════════════════════════════════════╗${NC}\n"
printf "${BLUE}║     ¡Instalación completada! 🎉          ║${NC}\n"
printf "${BLUE}╚══════════════════════════════════════════╝${NC}\n"
echo ""

if command -v snapcontext &>/dev/null; then
    ok "SnapContext instalado correctamente. Ejecuta 'snapcontext --help' para empezar."
    info "Reinicia tu terminal o ejecuta 'source ~/.bashrc' para actualizar el PATH."
else
    warn "Instalación completada pero el comando aún no está disponible en esta sesión."
fi

echo ""
echo "  Ejemplos de uso:"
echo "    snapcontext --version"
echo '    snapcontext "el botón de pago no funciona"'
echo '    snapcontext "revisar login" --experto'
echo ""
echo "  Configura tu API key (opcional):"
echo '    export GEMINI_API_KEY="tu_clave"'
echo ""
echo "  Sin API key, SnapContext 3.4.0 usa Ollama en modo offline"
echo "  automáticamente. Instálalo desde https://ollama.com y ejecuta:"
echo "    ollama pull llama3.2"
echo ""
echo "  Verifica tu instalación:"
echo "    snapcontext --diagnostico"
echo ""
