SHELL := /bin/bash
DISPLAY_NUM := :99
RESOLUTION := 1280x1024x24
VENV := .venv/bin/python
CALC_WIDTH := 400
CALC_HEIGHT := 500
CALC_X := 60
CALC_Y := 10

# ============================================================================
# Environment setup
# ============================================================================

.PHONY: setup-xvfb start-calc stop-calc restart-calc check verify-screenshot

## Start Xvfb if not running (NEVER kills existing one)
setup-xvfb:
	@if pgrep -f "Xvfb $(DISPLAY_NUM)" > /dev/null; then \
		echo "Xvfb already running — keeping it"; \
	else \
		echo "--- Starting Xvfb ---"; \
		rm -f /tmp/.X$(subst :,,$(DISPLAY_NUM))-lock 2>/dev/null || true; \
		Xvfb $(DISPLAY_NUM) -screen 0 $(RESOLUTION) -ac & \
		sleep 2; \
	fi
	@DISPLAY=$(DISPLAY_NUM) xdpyinfo | grep dimensions
	@echo "Xvfb ready on $(DISPLAY_NUM)"

## Launch gnome-calculator and resize to proper dimensions
## GTK4 on Xvfb creates 10x10 window without WM — xdotool fixes it
start-calc:
	@echo "--- Launching gnome-calculator ---"
	@killall -9 gnome-calculator 2>/dev/null || true
	@sleep 1
	DISPLAY=$(DISPLAY_NUM) gnome-calculator &
	@echo "Waiting for calculator window..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		WID=$$(DISPLAY=$(DISPLAY_NUM) xdotool search --name gnome-calculator 2>/dev/null | head -1); \
		if [ -n "$$WID" ]; then \
			DISPLAY=$(DISPLAY_NUM) xdotool windowsize $$WID $(CALC_WIDTH) $(CALC_HEIGHT); \
			DISPLAY=$(DISPLAY_NUM) xdotool windowmove $$WID $(CALC_X) $(CALC_Y); \
			sleep 1; \
			echo "Calculator ready, window $(CALC_WIDTH)x$(CALC_HEIGHT) (WID=$$WID, attempt $$i)"; \
			break; \
		fi; \
		sleep 1; \
	done

## Kill all calculator instances
stop-calc:
	@killall -9 gnome-calculator 2>/dev/null || true
	@echo "Calculator stopped"

## Kill and relaunch
restart-calc: stop-calc start-calc

## Health check: Xvfb + calculator + screenshot
check:
	@echo "=== Health Check ==="
	@echo -n "Xvfb: "; pgrep -f "Xvfb $(DISPLAY_NUM)" > /dev/null && echo "OK" || echo "NOT RUNNING"
	@echo -n "Calculator: "; pgrep -f gnome-calculator > /dev/null \
		&& echo "OK (PID: $$(pgrep -f gnome-calculator | head -1))" \
		|| echo "NOT RUNNING"
	@echo -n "Window: "; DISPLAY=$(DISPLAY_NUM) xwininfo -root -children 2>/dev/null \
		| grep gnome-calculator || echo "NO WINDOW"
	@echo -n "Screenshot: "; DISPLAY=$(DISPLAY_NUM) scrot -o /tmp/_health.png \
		&& SIZE=$$(stat -c%s /tmp/_health.png) \
		&& echo "$$SIZE bytes -> /tmp/_health.png"

## Take screenshot and print path
verify-screenshot:
	@DISPLAY=$(DISPLAY_NUM) scrot -o /tmp/_verify.png
	@echo "/tmp/_verify.png ($$(stat -c%s /tmp/_verify.png) bytes)"

# ============================================================================
# Pipelines
# ============================================================================

.PHONY: explore flat-run

## State exploration — sets up everything and runs pipeline
explore: setup-xvfb start-calc
	@echo ""
	@$(MAKE) check
	@echo ""
	@echo "=== Starting exploration ==="
	DISPLAY=$(DISPLAY_NUM) $(VENV) -u -m src.exploration.state_explorer \
		--max-steps 25 \
		--teacher-config config/teachers/gpt54mini_proxy.yaml \
		--prompt explore_step_v2.md \
		--output data/exploration/state_graphs/calc_latest \
		--display $(DISPLAY_NUM) -v

## Flat task runner
flat-run: setup-xvfb start-calc
	@echo ""
	@$(MAKE) check
	@echo ""
	DISPLAY=$(DISPLAY_NUM) $(VENV) -u -m src.exploration.flat_runner \
		--max-steps 2000 \
		--n-easy 5 --n-medium 8 --n-hard 3 \
		--max-steps-per-task 20 \
		--settings config/settings.yaml \
		--app-config config/apps/calculator.yaml \
		--teacher-config config/teachers/vllm.yaml \
		--output data/exploration/flat_runs/run_latest \
		--display $(DISPLAY_NUM) -v

# ============================================================================
# Convenience
# ============================================================================

.PHONY: full-setup help

## Complete setup: Xvfb + calc + check (without running pipeline)
full-setup: setup-xvfb start-calc check

help:
	@echo "Targets:"
	@echo "  full-setup        - Xvfb + calculator + health check"
	@echo "  explore           - Full exploration pipeline (setup + run)"
	@echo "  flat-run          - Flat task exploration (setup + run)"
	@echo "  setup-xvfb        - Start Xvfb (if not running)"
	@echo "  start-calc        - Launch + resize gnome-calculator"
	@echo "  stop-calc         - Kill gnome-calculator"
	@echo "  restart-calc      - Restart gnome-calculator"
	@echo "  check             - Health check"
	@echo "  verify-screenshot - Take a screenshot"
	@echo ""
	@echo "Usage:"
	@echo "  make full-setup              # setup only"
	@echo "  make explore                 # setup + explore"
	@echo "  tmux new -d -s exp 'make explore 2>&1 | tee explore.log'  # background"
