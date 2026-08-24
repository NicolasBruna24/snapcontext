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
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
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

RAW_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
MAJOR=$(echo "$RAW_VERSION" | cut -d. -f1)
MINOR=$(echo "$RAW_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
    err "Se requiere Python ≥ 3.9. Versión actual: $RAW_VERSION"
    echo "  Actualiza Python (por ej.: brew upgrade python o el gestor de tu distro)."
    exit 1
fi
ok "Python $RAW_VERSION encontrado ($PYTHON)"

# ─── 2. Instalar uv si no está ────────────────────────────────────────────
UV_EXISTS=false
if command -v uv &>/dev/null; then
    UV_EXISTS=true
    ok "uv ya está instalado"
else
    info "Instalando uv (gestor rápido de paquetes Python)..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
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
if command -v uv &>/dev/null && uv tool list 2>/dev/null | grep -q '^snapcontext'; then
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

# ─── 3. Instalar SnapContext ──────────────────────────────────────────────
info "Instalando SnapContext..."
INSTALADO=false

if [ "$UV_EXISTS" = true ]; then
    if uv tool install snapcontext --upgrade 2>&1 | tail -1; then
        INSTALADO=true
    else
        warn "La instalación con uv falló; probando con pip..."
    fi
    asegurar_path_perfil
fi

if [ "$INSTALADO" = false ]; then
    # ── Fallback robusto a pip ──
    info "Instalando con pip..."
    "$PYTHON" -m pip install --upgrade pip 2>/dev/null || true
    if ! "$PYTHON" -m pip install --upgrade snapcontext 2>&1 | tail -1; then
        err "No se pudo instalar SnapContext ni con uv ni con pip."
        echo "  Prueba manualmente:"
        echo "    $PYTHON -m pip install --upgrade snapcontext"
        exit 1
    fi
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
echo "  Sin API key, SnapContext 3.1.0 usa Ollama en modo offline"
echo "  automáticamente. Instálalo desde https://ollama.com y ejecuta:"
echo "    ollama pull llama3.2"
echo ""
echo "  Verifica tu instalación:"
echo "    snapcontext --diagnostico"
echo ""
