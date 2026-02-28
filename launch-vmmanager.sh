#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# KVM VM Manager — Launcher
# ══════════════════════════════════════════════════════════════════════════════
#
# This script is automatically customised and placed in ~/.local/bin/
# by install.sh.  You can also run it directly from the repo:
#
#   bash launch-vmmanager.sh
#
# It handles:
#   • Detecting Wayland vs X11 and setting the right Qt platform
#   • Finding the correct XDG_RUNTIME_DIR for sudo
#   • Launching VMManager.py as root (required for GPU/VFIO operations)
#   • Logging output to /tmp/vmmanager-YYYYMMDD.log
# ══════════════════════════════════════════════════════════════════════════════

# ── Find VMManager.py ─────────────────────────────────────────────────────────
# Look in (in order): same directory as this script, ~/.local/bin/, cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/VMManager.py" ]]; then
    APP="$SCRIPT_DIR/VMManager.py"
elif [[ -f "$HOME/.local/bin/VMManager.py" ]]; then
    APP="$HOME/.local/bin/VMManager.py"
elif [[ -f "$(pwd)/VMManager.py" ]]; then
    APP="$(pwd)/VMManager.py"
else
    echo "[ERROR] Cannot find VMManager.py"
    echo "        Run install.sh first, or place VMManager.py next to this script."
    exit 1
fi

# ── Identify the real user ────────────────────────────────────────────────────
REAL_USER="${SUDO_USER:-${USER}}"
REAL_UID="$(id -u "$REAL_USER" 2>/dev/null || id -u)"

# ── XDG_RUNTIME_DIR ───────────────────────────────────────────────────────────
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$REAL_UID}"

# ── Display / Qt platform detection ──────────────────────────────────────────
if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
    # Already set — trust it
    export WAYLAND_DISPLAY="$WAYLAND_DISPLAY"
    export QT_QPA_PLATFORM="wayland"
elif [[ -n "${DISPLAY:-}" ]]; then
    # X11 session
    export QT_QPA_PLATFORM="xcb"
else
    # Neither is set (e.g. launched from a file manager without env passthrough)
    # Probe /proc for a session that has display info
    for pid_env in /proc/*/environ; do
        env_raw="$(cat "$pid_env" 2>/dev/null)" || continue
        env_lines="$(echo "$env_raw" | tr '\0' '\n')"

        if wd="$(echo "$env_lines" | grep '^WAYLAND_DISPLAY=' | head -1 | cut -d= -f2-)"; [[ -n "$wd" ]]; then
            export WAYLAND_DISPLAY="$wd"
            export QT_QPA_PLATFORM="wayland"
            # Also grab XDG_RUNTIME_DIR from this process if we don't have it
            if [[ "$XDG_RUNTIME_DIR" == "/run/user/$REAL_UID" ]]; then
                if xrd="$(echo "$env_lines" | grep '^XDG_RUNTIME_DIR=' | head -1 | cut -d= -f2-)"; [[ -n "$xrd" ]]; then
                    export XDG_RUNTIME_DIR="$xrd"
                fi
            fi
            break
        elif dp="$(echo "$env_lines" | grep '^DISPLAY=' | head -1 | cut -d= -f2-)"; [[ -n "$dp" ]]; then
            export DISPLAY="$dp"
            export QT_QPA_PLATFORM="xcb"
            break
        fi
    done
fi

# ── libvirt URI ───────────────────────────────────────────────────────────────
export LIBVIRT_DEFAULT_URI="qemu:///system"

# ── Log file ─────────────────────────────────────────────────────────────────
LOG_FILE="/tmp/vmmanager-$(date +%Y%m%d).log"

echo "[$(date '+%H:%M:%S')] Launching KVM VM Manager"
echo "[$(date '+%H:%M:%S')] App:              $APP"
echo "[$(date '+%H:%M:%S')] User:             $REAL_USER (uid $REAL_UID)"
echo "[$(date '+%H:%M:%S')] XDG_RUNTIME_DIR:  $XDG_RUNTIME_DIR"
echo "[$(date '+%H:%M:%S')] Qt platform:      ${QT_QPA_PLATFORM:-auto}"
echo "[$(date '+%H:%M:%S')] Log:              $LOG_FILE"
echo ""

# ── Launch ────────────────────────────────────────────────────────────────────
exec sudo -E python3 "$APP" "$@" 2>&1 | tee "$LOG_FILE"
