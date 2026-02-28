#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# KVM VM Manager — Installer
# https://github.com/YOUR_USERNAME/kvm-vm-manager
#
# Usage:
#   bash install.sh          # install for the current user
#   bash install.sh --remove # uninstall
# ══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${GREEN}  ✓${NC}  $*"; }
warn()    { echo -e "${YELLOW}  ⚠${NC}  $*"; }
error()   { echo -e "${RED}  ✗  $*${NC}"; exit 1; }
step()    { echo -e "\n${CYAN}${BOLD}──  $*${NC}"; }
banner()  {
    echo -e "${BLUE}${BOLD}"
    echo "  ██╗  ██╗██╗   ██╗███╗   ███╗"
    echo "  ██║ ██╔╝██║   ██║████╗ ████║"
    echo "  █████╔╝ ██║   ██║██╔████╔██║"
    echo "  ██╔═██╗ ╚██╗ ██╔╝██║╚██╔╝██║"
    echo "  ██║  ██╗ ╚████╔╝ ██║ ╚═╝ ██║"
    echo "  ╚═╝  ╚═╝  ╚═══╝  ╚═╝     ╚═╝"
    echo -e "  VM Manager Installer${NC}\n"
}

# ── Detect real user (works even if script is run with sudo) ──────────────────
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    REAL_UID="$(id -u "$SUDO_USER")"
elif [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" || -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    REAL_USER="$USER"
    REAL_HOME="$HOME"
    REAL_UID="$(id -u)"
else
    REAL_USER="$(logname 2>/dev/null || echo "$USER")"
    REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
    REAL_UID="$(id -u "$REAL_USER")"
fi

INSTALL_DIR="$REAL_HOME/.local/bin"
APP_FILE="$INSTALL_DIR/VMManager.py"
LAUNCHER_FILE="$INSTALL_DIR/launch-vmmanager.sh"
DESKTOP_SRC="$REAL_HOME/.local/share/applications/kvm-vm-manager.desktop"
DESKTOP_DEST="$REAL_HOME/Desktop/KVM VM Manager.desktop"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--remove" ]]; then
    step "Removing KVM VM Manager"
    rm -f "$APP_FILE" "$LAUNCHER_FILE" "$DESKTOP_SRC" "$DESKTOP_DEST"
    info "Removed all files."
    info "Your VM configurations in libvirt are untouched."
    exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────
banner
echo -e "  Installing for user : ${BOLD}$REAL_USER${NC}"
echo -e "  Home directory      : ${BOLD}$REAL_HOME${NC}"
echo -e "  App location        : ${BOLD}$APP_FILE${NC}"
echo -e "  Desktop shortcut    : ${BOLD}$DESKTOP_DEST${NC}\n"

# ── Check we can write to the user's home ─────────────────────────────────────
if [[ ! -d "$REAL_HOME" ]]; then
    error "Home directory $REAL_HOME does not exist."
fi

# ── Helper: run a command as the real user (safe even if we're root) ──────────
run_as_user() {
    if [[ "$(id -u)" == "0" ]]; then
        sudo -u "$REAL_USER" "$@"
    else
        "$@"
    fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Check dependencies
# ═══════════════════════════════════════════════════════════════════════════════
step "Checking dependencies"

MISSING_PKGS=()
MISSING_PIP=()

# Python 3.10+
if ! command -v python3 &>/dev/null; then
    MISSING_PKGS+=("python3")
elif python3 -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    PY_VER=$(python3 --version)
    info "Python: $PY_VER"
else
    warn "Python 3.10+ recommended. Found: $(python3 --version)"
fi

# PyQt6
if python3 -c "import PyQt6" 2>/dev/null; then
    info "PyQt6: installed"
else
    warn "PyQt6 not found — will attempt to install"
    MISSING_PIP+=("PyQt6")
fi

# virsh / libvirt
if command -v virsh &>/dev/null; then
    info "virsh: $(virsh --version 2>/dev/null || echo 'found')"
else
    MISSING_PKGS+=("libvirt-client" "qemu-kvm")
    warn "virsh not found — will attempt to install libvirt"
fi

# lspci
if command -v lspci &>/dev/null; then
    info "lspci: found"
else
    MISSING_PKGS+=("pciutils")
fi

# virt-viewer (optional, but needed for SPICE console)
if command -v virt-viewer &>/dev/null; then
    info "virt-viewer: found"
else
    warn "virt-viewer not found (optional, needed for SPICE console view)"
    MISSING_PKGS+=("virt-viewer")
fi

# ── Install missing system packages ───────────────────────────────────────────
if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    step "Installing missing packages: ${MISSING_PKGS[*]}"

    # Detect package manager
    if command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
        INSTALL_CMD="dnf install -y"
    elif command -v apt &>/dev/null; then
        PKG_MGR="apt"
        INSTALL_CMD="apt install -y"
        # Map package names for apt
        MAPPED=()
        for pkg in "${MISSING_PKGS[@]}"; do
            case "$pkg" in
                libvirt-client) MAPPED+=("libvirt-clients") ;;
                qemu-kvm)       MAPPED+=("qemu-kvm") ;;
                *)              MAPPED+=("$pkg") ;;
            esac
        done
        MISSING_PKGS=("${MAPPED[@]}")
    elif command -v pacman &>/dev/null; then
        PKG_MGR="pacman"
        INSTALL_CMD="pacman -S --noconfirm"
    elif command -v zypper &>/dev/null; then
        PKG_MGR="zypper"
        INSTALL_CMD="zypper install -y"
    else
        warn "No supported package manager found (dnf/apt/pacman/zypper)."
        warn "Please manually install: ${MISSING_PKGS[*]}"
        MISSING_PKGS=()
    fi

    if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
        if [[ "$(id -u)" == "0" ]]; then
            $INSTALL_CMD "${MISSING_PKGS[@]}" || warn "Some packages failed to install — continuing."
        else
            sudo $INSTALL_CMD "${MISSING_PKGS[@]}" || warn "Some packages failed to install — continuing."
        fi
    fi
fi

# ── Install missing pip packages ──────────────────────────────────────────────
if [[ ${#MISSING_PIP[@]} -gt 0 ]]; then
    step "Installing Python packages: ${MISSING_PIP[*]}"
    python3 -m pip install --user "${MISSING_PIP[@]}" \
        || python3 -m pip install "${MISSING_PIP[@]}" --break-system-packages \
        || warn "pip install failed — try: pip install PyQt6 --break-system-packages"
    info "PyQt6 installed"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Create directories
# ═══════════════════════════════════════════════════════════════════════════════
step "Creating directories"

run_as_user mkdir -p "$INSTALL_DIR"
run_as_user mkdir -p "$REAL_HOME/.local/share/applications"
[[ -d "$REAL_HOME/Desktop" ]] || run_as_user mkdir -p "$REAL_HOME/Desktop"
info "Directories ready"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Copy VMManager.py
# ═══════════════════════════════════════════════════════════════════════════════
step "Installing VMManager.py → $APP_FILE"

if [[ ! -f "$SCRIPT_DIR/VMManager.py" ]]; then
    error "VMManager.py not found next to install.sh in: $SCRIPT_DIR"
fi

cp "$SCRIPT_DIR/VMManager.py" "$APP_FILE"
chown "$REAL_USER":"$(id -gn "$REAL_USER")" "$APP_FILE" 2>/dev/null || true
chmod 755 "$APP_FILE"
info "VMManager.py installed"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Detect display server and write the launcher script
# ═══════════════════════════════════════════════════════════════════════════════
step "Creating launcher script → $LAUNCHER_FILE"

# Detect whether the user's session is Wayland or X11
SESSION_TYPE="${XDG_SESSION_TYPE:-}"
if [[ -z "$SESSION_TYPE" ]]; then
    # Try to detect from running processes
    if pgrep -x "kwin_wayland" &>/dev/null || pgrep -x "sway" &>/dev/null \
            || pgrep -x "gnome-shell" &>/dev/null && [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
        SESSION_TYPE="wayland"
    else
        SESSION_TYPE="x11"
    fi
fi

info "Detected session type: $SESSION_TYPE"

cat > "$LAUNCHER_FILE" << LAUNCHER
#!/usr/bin/env bash
# KVM VM Manager — Launcher
# Auto-generated by install.sh — re-run install.sh to regenerate.
# ─────────────────────────────────────────────────────────────────
# Detects display server and sets required environment variables
# before launching VMManager.py with sudo.

# ── Identify the real user running this session ──────────────────
REAL_USER="\${SUDO_USER:-\${USER}}"
REAL_UID="\$(id -u "\$REAL_USER" 2>/dev/null || id -u)"

# ── XDG_RUNTIME_DIR ──────────────────────────────────────────────
export XDG_RUNTIME_DIR="\${XDG_RUNTIME_DIR:-/run/user/\$REAL_UID}"

# ── Display detection: prefer Wayland, fall back to X11 ──────────
if [[ -n "\${WAYLAND_DISPLAY:-}" ]]; then
    export WAYLAND_DISPLAY="\$WAYLAND_DISPLAY"
    export QT_QPA_PLATFORM="wayland"
    export DISPLAY="\${DISPLAY:-:0}"
elif [[ -n "\${DISPLAY:-}" ]]; then
    export QT_QPA_PLATFORM="xcb"
else
    # Last resort: probe from /proc for a running session
    for pid_env in /proc/*/environ; do
        if env_raw=\$(cat "\$pid_env" 2>/dev/null); then
            if echo "\$env_raw" | tr '\\0' '\\n' | grep -q '^WAYLAND_DISPLAY='; then
                WD=\$(echo "\$env_raw" | tr '\\0' '\\n' | grep '^WAYLAND_DISPLAY=' | head -1 | cut -d= -f2-)
                export WAYLAND_DISPLAY="\$WD"
                export QT_QPA_PLATFORM="wayland"
                break
            elif echo "\$env_raw" | tr '\\0' '\\n' | grep -q '^DISPLAY='; then
                DP=\$(echo "\$env_raw" | tr '\\0' '\\n' | grep '^DISPLAY=' | head -1 | cut -d= -f2-)
                export DISPLAY="\$DP"
                export QT_QPA_PLATFORM="xcb"
                break
            fi
        fi
    done
fi

# ── libvirt URI ───────────────────────────────────────────────────
export LIBVIRT_DEFAULT_URI="qemu:///system"

# ── Log file ─────────────────────────────────────────────────────
LOG_FILE="/tmp/vmmanager-\$(date +%Y%m%d).log"

# ── Launch ────────────────────────────────────────────────────────
exec sudo -E python3 "$APP_FILE" "\$@" 2>&1 | tee "\$LOG_FILE"
LAUNCHER

chown "$REAL_USER":"$(id -gn "$REAL_USER")" "$LAUNCHER_FILE" 2>/dev/null || true
chmod 755 "$LAUNCHER_FILE"
info "Launcher script created"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Create .desktop shortcut
# ═══════════════════════════════════════════════════════════════════════════════
step "Creating desktop shortcut"

# Prefer kdesu (no terminal) → konsole (terminal, visible) → xterm fallback
if command -v kdesu &>/dev/null; then
    EXEC_CMD="kdesu -c 'python3 $APP_FILE'"
    info "Using kdesu for graphical sudo prompt"
elif command -v konsole &>/dev/null; then
    EXEC_CMD="konsole --noclose -e $LAUNCHER_FILE"
    info "Using konsole terminal for sudo prompt"
elif command -v gnome-terminal &>/dev/null; then
    EXEC_CMD="gnome-terminal -- $LAUNCHER_FILE"
    info "Using gnome-terminal for sudo prompt"
elif command -v xterm &>/dev/null; then
    EXEC_CMD="xterm -e $LAUNCHER_FILE"
    info "Using xterm for sudo prompt"
else
    EXEC_CMD="$LAUNCHER_FILE"
    warn "No terminal emulator found — launcher will run directly"
fi

DESKTOP_CONTENT="[Desktop Entry]
Type=Application
Version=1.0
Name=KVM VM Manager
GenericName=Virtual Machine Manager
Comment=KVM Gaming VM Manager with GPU Passthrough
Exec=$EXEC_CMD
Icon=computer
Terminal=false
Categories=System;Emulator;Virtualization;
Keywords=kvm;vm;virtual;machine;gpu;passthrough;qemu;libvirt;
StartupNotify=true
StartupWMClass=KVM VM Manager"

# Write to applications dir (for app menu)
echo "$DESKTOP_CONTENT" > "$DESKTOP_SRC"
chown "$REAL_USER":"$(id -gn "$REAL_USER")" "$DESKTOP_SRC" 2>/dev/null || true
chmod 644 "$DESKTOP_SRC"

# Write to Desktop
echo "$DESKTOP_CONTENT" > "$DESKTOP_DEST"
chown "$REAL_USER":"$(id -gn "$REAL_USER")" "$DESKTOP_DEST" 2>/dev/null || true
chmod 755 "$DESKTOP_DEST"

# Mark as trusted (KDE Plasma requirement)
if command -v gio &>/dev/null; then
    run_as_user gio set "$DESKTOP_DEST" metadata::trusted true 2>/dev/null \
        && info "Desktop shortcut marked as trusted" \
        || warn "Could not mark as trusted — right-click the icon → Allow Launching"
else
    warn "gio not available — right-click the desktop icon → Allow Launching"
fi

# Update desktop database
if command -v update-desktop-database &>/dev/null; then
    run_as_user update-desktop-database "$REAL_HOME/.local/share/applications/" 2>/dev/null || true
fi

info "Desktop shortcut created"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6 — libvirt group membership
# ═══════════════════════════════════════════════════════════════════════════════
step "Checking libvirt group membership"

if groups "$REAL_USER" 2>/dev/null | grep -qE '\blibvirt\b'; then
    info "$REAL_USER is already in the libvirt group"
else
    warn "$REAL_USER is not in the libvirt group"
    echo -e "     Adding $REAL_USER to the libvirt group..."
    if [[ "$(id -u)" == "0" ]]; then
        usermod -aG libvirt "$REAL_USER" && info "Added to libvirt group" \
            || warn "Could not add to libvirt group — run: sudo usermod -aG libvirt $REAL_USER"
    else
        sudo usermod -aG libvirt "$REAL_USER" && info "Added to libvirt group" \
            || warn "Could not add to libvirt group — run: sudo usermod -aG libvirt $REAL_USER"
    fi
    warn "Log out and back in for the group change to take effect."
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Enable and start libvirtd
# ═══════════════════════════════════════════════════════════════════════════════
step "Checking libvirtd service"

if systemctl is-active --quiet libvirtd 2>/dev/null; then
    info "libvirtd is running"
else
    warn "libvirtd is not running — attempting to start"
    if [[ "$(id -u)" == "0" ]]; then
        systemctl enable --now libvirtd && info "libvirtd started" \
            || warn "Could not start libvirtd — run: sudo systemctl enable --now libvirtd"
    else
        sudo systemctl enable --now libvirtd && info "libvirtd started" \
            || warn "Could not start libvirtd — run: sudo systemctl enable --now libvirtd"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Installation complete!${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Double-click${NC} the 'KVM VM Manager' icon on your Desktop."
echo -e "  Or run from terminal:  ${CYAN}bash $LAUNCHER_FILE${NC}"
echo ""
echo -e "  ${YELLOW}If this is your first time:${NC}"
echo -e "  1. Open the app → Overview tab → read the Quick Start guide"
echo -e "  2. Run Actions → Health Check to verify your system is ready"
echo ""
if groups "$REAL_USER" 2>/dev/null | grep -qv '\blibvirt\b' 2>/dev/null; then
    echo -e "  ${YELLOW}⚠  Remember to log out and back in (libvirt group change).${NC}"
    echo ""
fi
