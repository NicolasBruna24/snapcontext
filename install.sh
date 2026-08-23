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
printf "${BLUE}║        SnapContext Installer            ║${NC}\n"
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
    err "Python no encontrado. Instala Python 3.9+ desde https://python.org"
    exit 1
fi

RAW_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
MAJOR=$(echo "$RAW_VERSION" | cut -d. -f1)
MINOR=$(echo "$RAW_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
    err "Se requiere Python ≥ 3.9. Versión actual: $RAW_VERSION"
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

# ─── 2b. Limpiar instalaciones previas de SnapContext con uv ──────────────
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

# ─── 3. Instalar SnapContext ──────────────────────────────────────────────
info "Instalando SnapContext..."
if [ "$UV_EXISTS" = true ]; then
    uv tool install snapcontext 2>&1 | tail -1
    export PATH="$HOME/.local/bin:$PATH"

    # Verificar que ~/.local/bin esté en el PATH persistente del shell; si no,
    # añadirlo al perfil (.bashrc o .zshrc) para terminales nuevas.
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) export PATH="$HOME/.local/bin:$PATH" ;;
    esac
    PERFIL=""
    if [ -n "${ZSH_VERSION:-}" ] && [ -f "$HOME/.zshrc" ]; then
        PERFIL="$HOME/.zshrc"
    elif [ -f "$HOME/.bashrc" ]; then
        PERFIL="$HOME/.bashrc"
    elif [ -f "$HOME/.profile" ]; then
        PERFIL="$HOME/.profile"
    fi
    if [ -n "$PERFIL" ] && ! grep -q '.local/bin' "$PERFIL" 2>/dev/null; then
        printf '\n# Añadido por el instalador de SnapContext\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$PERFIL"
        ok "Añadido ~/.local/bin al PATH en $PERFIL (abre una terminal nueva para aplicarlo)."
    fi
else
    PYTHON -m pip install --upgrade pip 2>/dev/null || true
    $PYTHON -m pip install snapcontext 2>&1 | tail -1
fi

# ─── 4. Verificar ─────────────────────────────────────────────────────────
echo ""
if command -v snapcontext &>/dev/null; then
    ok "SnapContext instalado correctamente"
    VERSION_INSTALADA=$(snapcontext --version 2>&1 | head -1)
    info "Versión: $VERSION_INSTALADA"
else
    warn "snapcontext no está en el PATH."
    echo "  Añade esta línea a tu ~/.bashrc o ~/.zshrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "  Luego abre una nueva terminal o ejecuta: source ~/.bashrc"
fi

# ─── 5. Mensaje final ─────────────────────────────────────────────────────
echo ""
printf "${BLUE}╔══════════════════════════════════════════╗${NC}\n"
printf "${BLUE}║     ¡Instalación completada! 🎉         ║${NC}\n"
printf "${BLUE}╚══════════════════════════════════════════╝${NC}\n"
echo ""
echo "  Ejemplos de uso:"
echo "    snapcontext --version"
echo '    snapcontext "el botón de pago no funciona"'
echo '    snapcontext "revisar login" --experto'
echo ""
echo "  Configura tu API key:"
echo "    export GEMINI_API_KEY=\"tu_clave\""
echo ""