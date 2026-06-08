# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**GUI Distillation** — A research project that distills GUI navigation skills from multimodal LLMs (MLLMs) into specialized compact models. The proof-of-concept application is GNOME Calculator. The current active phase is **Phase 1** (data collection and semantic annotation).

## Commands

### Setup (VM environment)
```bash
sudo bash scripts/setup/setup_vm.sh        # Install Python, pip, venv, git
sudo bash scripts/setup/install_apps.sh    # Install GNOME Calculator
sudo bash scripts/setup/setup_xvfb.sh      # Install Xvfb and X11 tools
bash scripts/setup/reset_display.sh        # Start/restart virtual display on :99
```

### Running Tests
```bash
pytest                                     # Run all tests
pytest -q tests/unit/test_schemas.py       # Run a single test file
pytest -v                                  # Verbose output
```

### Main Workflows

**Online task execution** (LLM drives a live GUI session):
```bash
# Single task mode
python -m src.exploration.task_runner \
  --task "add 3 and 5, then subtract 2" \
  --max-steps 20 \
  --settings config/settings.yaml \
  --app-config config/apps/calculator.yaml \
  --teacher-config config/teachers/openai_gpt.yaml \
  --output data/exploration/task_runs/run_001

# Autonomous exploration mode
python -m src.exploration.task_runner \
  --exploration \
  --task "Explore GNOME Calculator..." \
  --max-steps 50 \
  --output data/exploration/task_runs/explore_001
```

**Offline annotation** (annotate existing step artifacts with LLM):
```bash
# Output filename is derived from the model in teacher config:
#   openai_gpt.yaml        -> observation_grounded_gpt-4.1.json
#   openai_gpt-5.4.yaml    -> observation_grounded_gpt-5.4.json
#   openai_gpt-5.4-mini.yaml -> observation_grounded_gpt-5.4-mini.json
python -m src.exploration.annotator_runner \
  --steps-root data/exploration/phase_1_debug \
  --settings config/settings.yaml \
  --teacher-config config/teachers/openai_gpt.yaml \
  --max-steps 5
```

**IoU evaluation** (compare grounded bboxes against calibrated gold standard):
```bash
# Output filename mirrors the input: observation_grounded_gpt-4.1.json -> iou_eval_gpt-4.1.json
python -m src.exploration.evaluate_iou \
  --predicted data/exploration/phase_1_debug/step_0000/observation_grounded_gpt-4.1.json
```

**dHash interface-change detection experiment** (A11Y tree + dHash pipeline):
```bash
# Requires virtual display running and DISPLAY=:99
python -m src.core.automation_dhash \
  --output data/exploration/task_runs/run_dhash_001 \
  --display :99 \
  --verbose
```

**A11Y per-step experiment** (A11Y tree captured on every step):
```bash
python -m src.core.automation_a11y \
  --output data/exploration/task_runs/run_a11y_001 \
  --display :99 \
  --verbose
```

**A11Y + window move experiment** (proves A11Y coords auto-update after window relocation):
```bash
DISPLAY=:0 python -m src.core.automation_a11y_dnd \
  --output data/exploration/task_runs/run_a11y_dnd_001 \
  --display :0 \
  --move-x 600 --move-y 300 \
  --verbose
```

**Post-run visualization**:
```bash
# For run_a11y_001 (Hamming chart + heatmap + state graph)
python scripts/tools/visualize_run.py \
  --run-dir data/exploration/task_runs/run_a11y_001

# For run_a11y_dnd_001 (Hamming chart + heatmap)
python scripts/tools/visualize_run_dnd.py \
  --run-dir data/exploration/task_runs/run_a11y_dnd_001
```

**Infrastructure test**:
```bash
python scripts/tools/test_automation.py --display :99 --app gnome-calculator -v
```

**Calibration tools**:
```bash
python scripts/tools/find_coordinates.py        # Calibrate button center coords
python scripts/tools/find_coordinates_bboxes.py # Calibrate button bounding boxes
```

## Architecture

The system has two pipelines that share a common artifact format:

### Step Artifact Format
Every action produces a step directory:
```
step_0000/
  before.png          # Screenshot before action
  after.png           # Screenshot after action
  action.json         # What action was taken
  metadata.json       # Timing, hashing (MD5/SHA1/SHA256/dHash), etc.
  # Added by offline annotation:
  observation.json                    # UI element inventory with IDs and confidence
  observation_grounded_{model}.json   # UI elements with bounding boxes (model = gpt-4.1, gpt-5.4, etc.)
  delta.json                          # Description of state change
```

### Online Pipeline (`src/exploration/task_runner.py`)
`TaskRunner` runs a live GNOME Calculator session. At each step it:
1. Captures a screenshot + retrieves the button list from `config/apps/calculator.yaml`
2. Sends screenshot + history to LLM with a prompt from `config/prompts/`
3. LLM returns the next button to click
4. `GUIAutomation` executes the click and saves the step artifact

Two modes: **task mode** (execute a specific goal) and **exploration mode** (LLM autonomously invents sub-goals, history resets between goals).

### Offline Pipeline (`src/exploration/annotator_runner.py`)
`AnnotatorRunner` processes pre-existing step artifacts without a running GUI. For each step it calls the LLM three times (observation, grounded observation, delta) and saves the JSON annotations back into the step directory. The grounded observation filename is derived from the `model` field in the teacher config YAML. Generates `annotator_debug_report.json` on completion.

### A11Y Experiment Pipelines (`src/core/automation_a11y*.py` + `src/core/a11y_capture.py`)

Three experimental pipelines sharing `A11YCapture` — all use A11Y tree coordinates exclusively, no dependency on `calculator.yaml`.

**`automation_dhash.py`** — original dHash experiment. Captures A11Y tree once at start and again after mode switch if dHash signals a layout change. Saves `dhash_comparison.json`.

**`automation_a11y.py`** — extended version. Captures A11Y tree **before every step** — coordinates always reflect current UI state. Each `step_NNNN/` contains `a11y_tree.xml` + `a11y_buttons.txt`. `metadata.json` includes `dhashes.hamming_distance` (Hamming distance between before/after dHash).

**`automation_a11y_dnd.py`** — window relocation experiment. After initial calculations, moves the calculator window via `wmctrl` (using window ID to avoid locale-dependent title matching), then runs further calculations. Proves A11Y coordinates auto-update after window move — no recalibration needed. `run_summary.json` includes a `window_move` section with `position_before`, `position_after`, `wmctrl_success`, and `coord_shift {dx, dy}`.

`A11YCapture` (`src/core/a11y_capture.py`) uses `pyatspi` (AT-SPI2) to traverse the live accessibility tree, serialize it to XML (with element roles, names, coordinates, and states), and filter it to a readable TXT (~28 visible elements; hidden/off-screen elements with INT32_MIN coordinates are filtered out). `find_unchecked_mode_button()` reads AT-SPI states to avoid re-selecting the already-active mode.

**Key findings:**
- Basic→Programming mode switch: 27→96 buttons, all coordinates changed, dHash signal fired correctly
- Window move by (dx=1112, dy=592): A11Y coordinates shifted by the same amount in the very next step — zero recalibration required

**`pyatspi` setup note:** `python3-pyatspi` is a system package (not on PyPI). `setup_vm.sh` installs it via apt and creates `system_dist_packages.pth` in the venv's site-packages to make it importable.

**Post-run visualization tools** (`scripts/tools/`):
- `visualize_run.py` — for `automation_a11y` runs: Hamming bar chart, click heatmap, state-transition graph
- `visualize_run_dnd.py` — for `automation_a11y_dnd` runs: Hamming bar chart (4 phases, window-move separator), click heatmap

### Core Automation (`src/core/automation.py`)
`GUIAutomation` owns all interaction with the OS: app launch/close, screenshot capture, action dispatch (click, type, key press, hotkey, mouse move), and artifact persistence. It normalizes coordinates and computes perceptual hashes (dHash via `imagehash`) for state change detection.

### LLM Layer (`src/teachers/`)
- `openai_client.py` — OpenAI API wrapper with retry logic and image encoding
- `prompt_loader.py` — Loads markdown prompt templates from `config/prompts/`
- `json_parser.py` — Robust JSON extraction with fallback modes (handles fenced code blocks, partial responses)

### Schemas (`src/skills/`)
- `schemas.py` — Pydantic v2 models for the four distillation skills: **Grounding** (locate elements), **Action** (predict next click), **State** (compare UI states), **Simplifier** (NL → action sequence). `BBox` uses normalized [0,1] coordinates.
- `teacher_schemas.py` — Pydantic models for LLM output: `ObservationResponse`, `GroundedObservationResponse`, `DeltaResponse`.

## Configuration

**`config/settings.yaml`** — Master config covering paths, display/screen geometry (1280×1024×24 on `:99`), automation timing, LLM defaults, exploration protocol, dataset engineering, training, and feature flags (e.g., `use_grounded_observation`).

**`config/apps/calculator.yaml`** — Calibrated absolute pixel coordinates for 24 GNOME Calculator buttons. Used by `TaskRunner` to provide LLM with the available actions and to execute clicks accurately. This is authoritative for coordinates; MLLM-predicted bboxes have weak accuracy (~18% IoU).

**`config/teachers/openai_gpt.yaml`** (also `openai_gpt-5.4.yaml`, `openai_gpt-5.4-mini.yaml`) — Teacher model configs (model name, temperature, max_tokens, image_detail, timeout, retries). The `model` field is used to derive the grounded observation output filename.

**`config/prompts/*.md`** — Prompt templates loaded at runtime. Naming convention: `{purpose}_v{N}.md`.

## Environment

Requires a running X display (Xvfb on `:99` in VM, native display on host). Set `DISPLAY=:99` or rely on `config/settings.yaml` profile overrides. API keys are loaded via `python-dotenv` from a `.env` file (not committed).
