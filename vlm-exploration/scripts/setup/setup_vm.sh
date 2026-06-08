#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# setup_vm.sh
# Bootstrap Ubuntu VM for GUI exploration (Phase 0.3).
#
# What it does:
# - Updates apt index
# - Optionally upgrades installed packages (disabled by default)
# - Installs base CLI/dev tooling + GUI automation helpers
#
# What it DOES NOT do:
# - Does not create a Python venv
# - Does not install Python dependencies from requirements.txt
#
# Rationale:
# The repo is shared with the host via VirtioFS; creating a venv inside the
# shared folder would produce a non-portable environment between Manjaro (host)
# and Ubuntu (VM).
# -----------------------------------------------------------------------------

# Default: do NOT upgrade to keep VM deterministic.
# To enable: sudo DO_UPGRADE=1 bash scripts/setup/setup_vm.sh
DO_UPGRADE="${DO_UPGRADE:-0}"

log() { echo "[setup_vm] $*"; }

ensure_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "[setup_vm] ERROR: run as root (use sudo)" >&2
    exit 1
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[setup_vm] ERROR: missing command: $1" >&2
    exit 1
  }
}

warn_if_not_x11_session() {
  # When running via sudo, $XDG_SESSION_TYPE may be missing.
  # Try to query it from the invoking user session if possible.
  local session_type="${XDG_SESSION_TYPE:-}"

  if [[ -z "$session_type" ]] && [[ -n "${SUDO_USER:-}" ]]; then
    session_type="$(sudo -u "$SUDO_USER" bash -lc 'echo "${XDG_SESSION_TYPE:-}"' 2>/dev/null || true)"
  fi

  if [[ -z "$session_type" ]]; then
    session_type="unknown"
  fi

  if [[ "$session_type" != "x11" ]]; then
    log "WARNING: current session type is '$session_type'."
    log "For pyautogui + deterministic runs you MUST use X11."
    log "Login screen → gear icon → choose 'Ubuntu on Xorg'."
    log "Optional hard-disable: set WaylandEnable=false in /etc/gdm3/custom.conf"
  fi
}

main() {
  ensure_root
  require_cmd apt

  warn_if_not_x11_session

  log "Updating apt index..."
  apt update -y

  if [[ "$DO_UPGRADE" == "1" ]]; then
    log "Upgrading installed packages (DO_UPGRADE=1)..."
    DEBIAN_FRONTEND=noninteractive apt upgrade -y
  else
    log "Skipping apt upgrade (DO_UPGRADE=0)."
  fi

  log "Installing base packages..."
  DEBIAN_FRONTEND=noninteractive apt install -y \
    ca-certificates \
    curl \
    git \
    make \
    build-essential \
    unzip \
    jq \
    python3 \
    python3-pip \
    python3-venv

  log "Installing GUI automation helpers..."
  DEBIAN_FRONTEND=noninteractive apt install -y \
    wmctrl \
    xdotool \
    x11-utils \
    python3-pyatspi

  log "Linking system pyatspi into the project venv (not on PyPI)..."
  VENV_SITE="/home/${SUDO_USER}/gui-distill-venv/lib/python3.10/site-packages"
  PTH_FILE="${VENV_SITE}/system_dist_packages.pth"
  if [[ -d "$VENV_SITE" ]]; then
    echo "/usr/lib/python3/dist-packages" > "$PTH_FILE"
    log "  Created: $PTH_FILE"
  else
    log "  WARNING: venv not found at $VENV_SITE — skipping .pth setup"
    log "  Run manually after creating the venv:"
    log "    echo '/usr/lib/python3/dist-packages' > \$VENV_SITE/system_dist_packages.pth"
  fi

  log "Done."
  log "Next steps:"
  log "  1) sudo bash scripts/setup/install_apps.sh"
  log "  2) sudo bash scripts/setup/setup_xvfb.sh"
}

main "$@"
