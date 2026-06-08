#!/usr/bin/env python3
"""
Calibrate GNOME Calculator button coordinates for all modes.

Uses system python3 + pyatspi to auto-discover button center coordinates
for basic, advanced, and programming modes, plus the mode-switch dropdown.

Usage (on VM with DISPLAY=:99):
    python3 scripts/tools/calibrate_all_modes.py
    python3 scripts/tools/calibrate_all_modes.py --output config/apps/calculator_multimode.yaml
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

try:
    import pyatspi
except ImportError:
    sys.exit("pyatspi not found. Run with system python3: sudo apt install python3-pyatspi")


# ---------------------------------------------------------------------------
# AT-SPI helpers
# ---------------------------------------------------------------------------

def _get_calc_app(timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        desktop = pyatspi.Registry.getDesktop(0)
        for app in desktop:
            if app and "calc" in (app.name or "").lower():
                return app
        time.sleep(0.3)
    return None


def _collect_buttons(app) -> dict[str, tuple[int, int]]:
    """Walk A11Y tree, collect push buttons with valid coords → {name: (cx, cy)}."""
    results: dict[str, tuple[int, int]] = {}
    skip_names = {
        "Undo", "New Window", "Number format", "Preferences",
        "Keyboard Shortcuts", "Help", "About Calculator",
        "Mode selection", "Primary menu",
    }

    def walk(node):
        try:
            role = node.getRoleName()
            name = (node.name or "").strip()
            if role == "push button" and name and name not in skip_names:
                try:
                    ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                    x, y, w, h = ext.x, ext.y, ext.width, ext.height
                    # Filter out off-screen / hidden buttons
                    if w > 0 and h > 0 and x > -9000 and y > -9000:
                        cx, cy = x + w // 2, y + h // 2
                        results[name] = (cx, cy)
                except Exception:
                    pass
            for child in node:
                walk(child)
        except Exception:
            pass

    walk(app)
    return results


def _find_mode_toggle(app) -> tuple[int, int] | None:
    """Find the 'Mode selection' toggle button center coords."""
    def walk(node):
        try:
            role = node.getRoleName()
            name = (node.name or "").strip()
            if role in ("toggle button", "push button") and name == "Mode selection":
                ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                if ext.width > 0 and ext.height > 0:
                    return ext.x + ext.width // 2, ext.y + ext.height // 2
            for child in node:
                result = walk(child)
                if result:
                    return result
        except Exception:
            pass
        return None
    return walk(app)


def _click(cx: int, cy: int):
    subprocess.run(["xdotool", "mousemove", str(cx), str(cy),
                    "click", "1"], check=False)
    time.sleep(0.1)


def _set_mode(mode: str):
    subprocess.run(["gsettings", "set", "org.gnome.calculator",
                    "button-mode", mode], check=True)
    time.sleep(1.2)


# ---------------------------------------------------------------------------
# Name → button_id mapping
# ---------------------------------------------------------------------------

# Basic mode: symbol/digit → button_id (existing calculator.yaml names)
BASIC_NAME_MAP: dict[str, str] = {
    "⌫": "backspace",
    "(": "paren_open",
    ")": "paren_close",
    "mod": "mod",
    "π": "pi",
    "7": "digit_7",
    "8": "digit_8",
    "9": "digit_9",
    "÷": "divide",
    "√": "sqrt",
    "4": "digit_4",
    "5": "digit_5",
    "6": "digit_6",
    "×": "multiply",
    "x²": "square",
    "1": "digit_1",
    "2": "digit_2",
    "3": "digit_3",
    "−": "minus",
    "=": "equals",
    "0": "digit_0",
    ".": "decimal",
    "%": "percent",
    "+": "plus",
    # Some GNOME versions use text labels
    "Exponent": "exponent",
}

# Advanced mode: AT-SPI name → button_id
ADVANCED_NAME_MAP: dict[str, str] = {
    "sin": "adv_sin",
    "cos": "adv_cos",
    "tan": "adv_tan",
    "sin⁻¹": "adv_asin",
    "cos⁻¹": "adv_acos",
    "tan⁻¹": "adv_atan",
    "sinh": "adv_sinh",
    "cosh": "adv_cosh",
    "tanh": "adv_tanh",
    "sinh⁻¹": "adv_asinh",
    "cosh⁻¹": "adv_acosh",
    "tanh⁻¹": "adv_atanh",
    "log": "adv_log",
    "ln": "adv_ln",
    "log₂": "adv_log2",
    "e": "adv_e",
    "x!": "adv_factorial",
    "xʸ": "adv_x_pow_y",
    "ʸ√x": "adv_x_root_y",
    "|x|": "adv_abs",
    "Ans": "adv_ans",
}

# Programming mode: AT-SPI name → button_id
PROGRAMMING_NAME_MAP: dict[str, str] = {
    "AND": "prog_and",
    "OR": "prog_or",
    "XOR": "prog_xor",
    "NOT": "prog_not",
    "«": "prog_lsh",
    "»": "prog_rsh",
    "A": "prog_A",
    "B": "prog_B",
    "C": "prog_C",
    "D": "prog_D",
    "E": "prog_E",
    "F": "prog_F",
    "Hexadecimal": "prog_hex",
    "Octal": "prog_oct",
    "Decimal": "prog_dec",
    "Binary": "prog_bin",
    "2's": "prog_twos_complement",
    "1's": "prog_ones_complement",
}

# Shared mode popup item names
MODE_POPUP_MAP: dict[str, str] = {
    "Basic": "mode_basic",
    "Advanced": "mode_advanced",
    "Financial": "mode_financial",
    "Programming": "mode_programming",
}


def _map_name(name: str, mode: str) -> str | None:
    """Map AT-SPI button name to our button_id. Returns None if unknown/skip."""
    # Mode popup items (visible when dropdown is open)
    if name in MODE_POPUP_MAP:
        return MODE_POPUP_MAP[name]

    if mode == "basic":
        mapped = BASIC_NAME_MAP.get(name)
        if mapped:
            return mapped
        # Digits are single chars 0-9
        if len(name) == 1 and name.isdigit():
            return f"digit_{name}"
        return None

    if mode == "advanced":
        # Advanced keeps all basic buttons + adds new ones
        mapped = ADVANCED_NAME_MAP.get(name) or BASIC_NAME_MAP.get(name)
        if mapped:
            return mapped
        if len(name) == 1 and name.isdigit():
            return f"digit_{name}"
        return None

    if mode == "programming":
        mapped = PROGRAMMING_NAME_MAP.get(name) or BASIC_NAME_MAP.get(name)
        if mapped:
            return mapped
        if len(name) == 1 and name.isdigit():
            return f"digit_{name}"
        return None

    return None


# ---------------------------------------------------------------------------
# Main calibration
# ---------------------------------------------------------------------------

def calibrate(output_path: Path | None = None):
    print("=== GNOME Calculator Multi-Mode Calibration ===\n")

    # Ensure calculator is running
    subprocess.run(["pkill", "-9", "gnome-calculator"], check=False)
    time.sleep(0.5)
    subprocess.Popen(["gnome-calculator"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    time.sleep(2.0)

    app = _get_calc_app()
    if app is None:
        sys.exit("Could not find gnome-calculator in A11Y tree. Is DISPLAY set?")

    all_buttons: dict[str, list[int]] = {}
    modes_filter: dict[str, list[str]] = {}

    # ── Collect mode_switch (the "Basic ▾" toggle) ──────────────────────────
    mode_toggle_coords = _find_mode_toggle(app)
    if mode_toggle_coords:
        all_buttons["mode_switch"] = list(mode_toggle_coords)
        print(f"  mode_switch: {list(mode_toggle_coords)}")
    else:
        print("  WARNING: mode_switch toggle not found in A11Y tree")

    # ── Capture popup items (click mode_switch, then scan) ──────────────────
    if mode_toggle_coords:
        print("\n-- Opening mode dropdown to capture popup items --")
        _click(*mode_toggle_coords)
        time.sleep(0.8)  # wait for popup + AT-SPI update
        app = _get_calc_app()  # refresh
        if app:
            popup_buttons = _collect_buttons(app)
            for name, coords in popup_buttons.items():
                bid = MODE_POPUP_MAP.get(name)
                if bid:
                    all_buttons[bid] = list(coords)
                    print(f"  {bid}: {list(coords)}  (from '{name}')")
        # Close popup by pressing Escape
        subprocess.run(["xdotool", "key", "Escape"], check=False)
        time.sleep(0.5)

    # ── Calibrate each mode ──────────────────────────────────────────────────
    for mode in ["basic", "advanced", "programming"]:
        print(f"\n-- Mode: {mode} --")
        _set_mode(mode)
        app = _get_calc_app()
        if app is None:
            print(f"  WARNING: Could not access A11Y tree in {mode} mode")
            continue

        raw_buttons = _collect_buttons(app)
        mode_ids: list[str] = []

        for name, coords in sorted(raw_buttons.items()):
            bid = _map_name(name, mode)
            if bid is None:
                print(f"  [UNMAPPED] '{name}' at {list(coords)}")
                continue
            if bid not in all_buttons:
                all_buttons[bid] = list(coords)
            mode_ids.append(bid)
            print(f"  {bid}: {list(coords)}  (from '{name}')")

        modes_filter[mode] = sorted(mode_ids)

    # ── Reset to basic ───────────────────────────────────────────────────────
    _set_mode("basic")
    subprocess.run(["pkill", "-9", "gnome-calculator"], check=False)

    # ── Generate YAML ────────────────────────────────────────────────────────
    yaml_lines = [
        "metadata:",
        "  app_name: GNOME Calculator",
        "  calibrated_date: '2026-04-11'",
        "  screen_resolution: 1280x1024",
        "  coordinate_system: absolute",
        f"  recorded_points: {len(all_buttons)}",
        "  modes:",
    ]
    for mode, ids in modes_filter.items():
        yaml_lines.append(f"    {mode}:")
        for bid in ids:
            yaml_lines.append(f"      - {bid}")

    yaml_lines.append("")
    yaml_lines.append("buttons:")
    yaml_lines.append("  # Mode switch (always available)")
    if "mode_switch" in all_buttons:
        yaml_lines.append(f"  mode_switch: {all_buttons['mode_switch']}")
    for bid in ["mode_basic", "mode_advanced", "mode_financial", "mode_programming"]:
        if bid in all_buttons:
            yaml_lines.append(f"  {bid}: {all_buttons[bid]}")

    yaml_lines.append("")
    yaml_lines.append("  # Basic mode buttons")
    basic_ids = modes_filter.get("basic", [])
    for bid in basic_ids:
        if bid in all_buttons:
            yaml_lines.append(f"  {bid}: {all_buttons[bid]}")

    yaml_lines.append("")
    yaml_lines.append("  # Advanced mode extra buttons")
    adv_ids = [b for b in modes_filter.get("advanced", []) if b not in basic_ids]
    for bid in adv_ids:
        if bid in all_buttons:
            yaml_lines.append(f"  {bid}: {all_buttons[bid]}")

    yaml_lines.append("")
    yaml_lines.append("  # Programming mode extra buttons")
    prog_ids = [b for b in modes_filter.get("programming", []) if b not in basic_ids]
    for bid in prog_ids:
        if bid in all_buttons:
            yaml_lines.append(f"  {bid}: {all_buttons[bid]}")

    yaml_content = "\n".join(yaml_lines) + "\n"

    print("\n\n=== Generated YAML ===\n")
    print(yaml_content)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_content, encoding="utf-8")
        print(f"Saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser("calibrate_all_modes")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Write YAML to this path (e.g. config/apps/calculator_multimode.yaml)")
    args = parser.parse_args()
    calibrate(args.output)
