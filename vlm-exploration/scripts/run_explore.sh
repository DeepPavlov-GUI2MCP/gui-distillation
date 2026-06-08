#!/usr/bin/env bash
# Run state exploration pipeline end-to-end.
# Execute via: tmux new-session -d -s explore './scripts/run_explore.sh 2>&1 | tee explore.log'
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-:99}"
RESOLUTION="${RESOLUTION:-1280x1024x24}"
TEACHER="${TEACHER:-config/teachers/gpt54mini_proxy.yaml}"
PROMPT="${PROMPT:-explore_step_v2.md}"
MAX_STEPS="${MAX_STEPS:-25}"
OUTPUT="${OUTPUT:-data/exploration/state_graphs/calc_latest}"

export DISPLAY="$DISPLAY_NUM"
export GDK_BACKEND=x11
export GSK_RENDERER=cairo  # GTK4 needs cairo renderer on Xvfb (no GPU)

echo "=== State Exploration Setup ==="
echo "Display: $DISPLAY_NUM"
echo "Teacher: $TEACHER"
echo "Max steps: $MAX_STEPS"
echo "Output: $OUTPUT"
echo ""

# 1. Kill stale processes
echo "--- Cleaning up ---"
killall -9 gnome-calculator 2>/dev/null || true
pkill -f openbox 2>/dev/null || true
sleep 1

# 2. Ensure Xvfb is running (NEVER restart if already running!)
if pgrep -f "Xvfb $DISPLAY_NUM" > /dev/null; then
    echo "Xvfb already running — keeping it"
else
    echo "Starting Xvfb..."
    rm -f "/tmp/.X${DISPLAY_NUM#:}-lock" 2>/dev/null || true
    Xvfb "$DISPLAY_NUM" -screen 0 "$RESOLUTION" -ac &
    sleep 2
fi
xdpyinfo | grep dimensions

# 3. No compositor needed — GSK_RENDERER=cairo handles GTK4 rendering

# 4. Launch calculator and resize (GTK4 on Xvfb creates 10x10 window without WM)
echo "--- Launching gnome-calculator ---"
gnome-calculator &
CALC_PID=$!
sleep 3
# Resize to proper dimensions
WID=$(xdotool search --name gnome-calculator 2>/dev/null | head -1)
if [ -n "$WID" ]; then
    xdotool windowsize "$WID" 400 500
    xdotool windowmove "$WID" 60 10
    sleep 1
    echo "Window resized (WID=$WID)"
fi

# 5. Verify calculator is alive
if kill -0 $CALC_PID 2>/dev/null; then
    echo "Calculator running (PID: $CALC_PID)"
else
    echo "ERROR: Calculator died!"
    exit 1
fi

# 6. Verify screenshot
echo "--- Verifying screenshot ---"
scrot -o /tmp/_verify_explore.png
SIZE=$(stat -c%s /tmp/_verify_explore.png)
echo "Screenshot: $SIZE bytes"
if [ "$SIZE" -lt 20000 ]; then
    echo "WARNING: Screenshot might be black ($SIZE bytes)"
    echo "Waiting 5 more seconds..."
    sleep 5
    scrot -o /tmp/_verify_explore.png
    SIZE=$(stat -c%s /tmp/_verify_explore.png)
    echo "Retry screenshot: $SIZE bytes"
fi

# 7. Final check — calculator still alive?
if ! kill -0 $CALC_PID 2>/dev/null; then
    echo "ERROR: Calculator died before exploration!"
    exit 1
fi

echo ""
echo "=== Setup complete, starting exploration ==="
echo ""

# 8. Clean output dir and run
rm -rf "$OUTPUT"
.venv/bin/python -u -m src.exploration.state_explorer \
    --max-steps "$MAX_STEPS" \
    --teacher-config "$TEACHER" \
    --prompt "$PROMPT" \
    --output "$OUTPUT" \
    --display "$DISPLAY_NUM" \
    -v

echo ""
echo "=== Exploration complete ==="
