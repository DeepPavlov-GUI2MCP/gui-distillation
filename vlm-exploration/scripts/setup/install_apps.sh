#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# install_apps.sh
# Installs target GUI applications for the project.
# Current target: GNOME Calculator
# -----------------------------------------------------------------------------

log() { echo "[install_apps] $*"; }

ensure_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "[install_apps] ERROR: run as root (use sudo)" >&2
    exit 1
  fi
}

main() {
  ensure_root

  log "Updating apt index..."
  apt update -y

  log "Installing GNOME Calculator..."
  DEBIAN_FRONTEND=noninteractive apt install -y gnome-calculator

  log "Verifying installation..."
  if ! command -v gnome-calculator >/dev/null 2>&1; then
    echo "[install_apps] ERROR: gnome-calculator not found after install" >&2
    exit 1
  fi

  log "gnome-calculator installed: $(gnome-calculator --version 2>/dev/null || echo 'version not available via CLI')"
  log "Done."
}

main "$@"
