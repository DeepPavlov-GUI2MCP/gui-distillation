#!/usr/bin/env bash
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-:99}"
RESOLUTION="${RESOLUTION:-1280x1024x24}"

echo "=============================================="
echo " Reset Xvfb ${DISPLAY_NUM} (${RESOLUTION})"
echo "=============================================="

# 0) Убить старые процессы, если они остались
pkill -f "Xvfb ${DISPLAY_NUM}" || true
pkill -f "openbox" || true

# 1) Снять lock (если остался)
sudo rm -f "/tmp/.X${DISPLAY_NUM#:}-lock" || true

# 2) Почистить переменную (в текущей оболочке)
unset DISPLAY || true

# 3) Поднять чистый Xvfb
Xvfb "${DISPLAY_NUM}" -screen 0 "${RESOLUTION}" -ac &
sleep 1

# 4) Экспорт DISPLAY
export DISPLAY="${DISPLAY_NUM}"

# 5) Проверка, что дисплей живой
xdpyinfo | grep dimensions

echo "✅ Xvfb ready: DISPLAY=${DISPLAY}"
echo ""
echo "Now run (in same shell):"
echo "  gnome-calculator &"
echo "  sleep 1"
echo "  xlsclients"
echo "  python scripts/tools/test_automation.py --display ${DISPLAY_NUM} --app gnome-calculator -v"
