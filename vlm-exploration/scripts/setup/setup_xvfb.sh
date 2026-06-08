#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# setup_xvfb.sh
# Installs and configures tools needed for headless GUI runs:
# - Xvfb virtual display
# - scrot screenshot tool
# Provides a simple "smoke test" command sequence.
# -----------------------------------------------------------------------------

log() { echo "[setup_xvfb] $*"; }

ensure_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "[setup_xvfb] ERROR: run as root (use sudo)" >&2
    exit 1
  fi
}

main() {
  ensure_root

  log "Updating apt index..."
  apt update -y

  log "Installing Xvfb and screenshot tools..."
  DEBIAN_FRONTEND=noninteractive apt install -y \
    xvfb \
    scrot \
    x11-utils

  log "Done."

cat <<'EOF'

[setup_xvfb] Smoke test (run as normal user):

  # 0) Clean up stale processes/locks (optional but recommended)
  killall gnome-calculator Xvfb 2>/dev/null || true
  rm -f /tmp/.X99-lock

  # 1) Start Xvfb (virtual display :99)
  Xvfb :99 -screen 0 1280x1024x24 -ac &
  sleep 1

  # 2) Verify display is reachable
  DISPLAY=:99 xdpyinfo | grep dimensions

  # 3) Launch target app on Xvfb (force X11 backend even if session is Wayland)
  env -u WAYLAND_DISPLAY -u WAYLAND_SOCKET DISPLAY=:99 GDK_BACKEND=x11 \
    gnome-calculator >/tmp/calc.log 2>&1 &
  sleep 2

  # 4) Verify the app is connected to :99
  DISPLAY=:99 xlsclients

  # 5) Take a screenshot (explicit path recommended)
  DISPLAY=:99 scrot -d 1 /mnt/repo/smoke_calc.png

If it fails:
  - check Xvfb: ps aux | grep Xvfb
  - check DISPLAY: echo $DISPLAY (or run commands with DISPLAY=:99 prefix)
  - check calculator log: tail -n 200 /tmp/calc.log
  - remove stale lock: rm -f /tmp/.X99-lock

EOF
}

main "$@"
