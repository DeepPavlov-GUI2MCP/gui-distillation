#!/usr/bin/env python3
# scripts/tools/find_coordinates.py
"""
Interactive tool to find and record button coordinates for GUI applications.

Features:
- Shows current mouse position in real-time.
- Press 'p' to record current coordinates.
- Press 'q' to quit and save/print results.
- Can save to YAML (+ JSON backup).

Notes (Linux/X11):
- Uses `pynput` for keyboard listening (no sudo required in most X11 sessions).
- Requires: pyautogui, pynput, pyyaml

Phase: 0.3 Infrastructure Setup (calibration helper)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pyautogui

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

try:
    from pynput import keyboard  # type: ignore
except Exception:  # pragma: no cover
    keyboard = None  # type: ignore


@dataclass(frozen=True)
class Point:
    x: int
    y: int


class CoordinateFinder:
    """
    Interactive coordinate finder.

    Hotkeys:
      - 'p' record current mouse position
      - 'q' quit and print/save

    This class uses a background keyboard listener to avoid Linux permission
    issues common with the `keyboard` package.
    """

    DEFAULT_BUTTON_ORDER: List[str] = [
        "backspace", "paren_open", "paren_close", "mod", "pi",
        "digit_7", "digit_8", "digit_9", "divide", "sqrt",
        "digit_4", "digit_5", "digit_6", "multiply", "square",
        "digit_1", "digit_2", "digit_3", "minus", "equals",
        "digit_0", "decimal", "percent", "plus",
    ]

    def __init__(
        self,
        output_file: Optional[str] = None,
        debounce_seconds: float = 0.35,
        refresh_hz: float = 20.0,
    ) -> None:
        self.output_file = output_file
        self.debounce_seconds = debounce_seconds
        self.refresh_sleep = 1.0 / max(refresh_hz, 1.0)

        self.recorded: List[Point] = []
        self.running = True
        self._last_record_ts = 0.0

        self._listener: Optional["keyboard.Listener"] = None  # type: ignore[name-defined]

    def print_instructions(self) -> None:
        print("\n" + "=" * 70)
        print("COORDINATE FINDER TOOL")
        print("=" * 70)
        print("\nHotkeys:")
        print("  🅿️  Press 'p' to record current coordinates")
        print("  🆀  Press 'q' to quit and save/print")
        print("\nTips:")
        print("  - Keep the app window in a stable position.")
        print("  - Coordinates are absolute pixels for current resolution.")
        print("  - Suggested order (calculator): digits 0-9, then operations.")
        print("  - Run in X11 session. If using Xvfb: export DISPLAY=:99.")
        print("\n" + "-" * 70)

    def _suggest_button_name(self, index: int) -> Optional[str]:
        if 0 <= index < len(self.DEFAULT_BUTTON_ORDER):
            return self.DEFAULT_BUTTON_ORDER[index]
        return None

    def _on_key_press(self, key: "keyboard.Key | keyboard.KeyCode") -> None:  # type: ignore[name-defined]
        # Normalize to char keys where possible
        try:
            ch = key.char  # type: ignore[attr-defined]
        except Exception:
            ch = None

        now = time.monotonic()

        if ch == "p":
            if now - self._last_record_ts < self.debounce_seconds:
                return
            self._last_record_ts = now
            self.record_current()

        elif ch == "q":
            self.running = False

    def _start_listener(self) -> None:
        if keyboard is None:
            raise RuntimeError(
                "pynput is not installed. Install it (pip install pynput) "
                "or add it to requirements.txt."
            )

        self._listener = keyboard.Listener(on_press=self._on_key_press)
        self._listener.start()

    def run(self) -> None:
        self.print_instructions()

        # Quick sanity check for screen access
        try:
            w, h = pyautogui.size()
            print(f"📺 Screen size: {w}x{h}")
        except Exception as e:
            raise RuntimeError(
                f"Cannot access screen via PyAutoGUI: {e}. "
                "Ensure X11 session and correct DISPLAY."
            ) from e

        self._start_listener()
        print("\nReady! Move mouse; press 'p' to record; 'q' to quit.\n")

        try:
            while self.running:
                x, y = pyautogui.position()
                sys.stdout.write(
                    f"\r📍 Current position: ({x:4d}, {y:4d}) | "
                    f"Recorded: {len(self.recorded)} points"
                )
                sys.stdout.flush()
                time.sleep(self.refresh_sleep)
        finally:
            self._stop_listener()
            self._finalize()

    def _stop_listener(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass

    def record_current(self) -> None:
        x, y = pyautogui.position()
        self.recorded.append(Point(x=x, y=y))
        idx = len(self.recorded) - 1
        name = self._suggest_button_name(idx) or f"button_{idx}"
        print(f"\n✅ Recorded {idx + 1:02d}: {name} -> ({x}, {y})")

    def _finalize(self) -> None:
        print("\n\n" + "=" * 70)
        print(f"📊 RECORDED {len(self.recorded)} COORDINATES")
        print("=" * 70)

        for i, p in enumerate(self.recorded):
            button_name = self._suggest_button_name(i) or f"button_{i}"
            print(f"  {button_name:15s}: [{p.x:4d}, {p.y:4d}]")

        if self.output_file:
            self.save_to_file()
        else:
            self.print_yaml_format()

    def print_yaml_format(self) -> None:
        print("\n" + "-" * 70)
        print("YAML FORMAT (copy to config/apps/<app>.yaml):")
        print("-" * 70)
        print("buttons:")
        for i, p in enumerate(self.recorded):
            button_name = self._suggest_button_name(i) or f"button_{i}"
            print(f"  {button_name}: [{p.x}, {p.y}]")
        print("-" * 70 + "\n")

    def save_to_file(self) -> None:
        if yaml is None:
            raise RuntimeError(
                "PyYAML is not installed. Install it (pip install pyyaml) "
                "or add it to requirements.txt."
            )

        output_path = Path(self.output_file).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        screen_w, screen_h = pyautogui.size()

        payload: Dict[str, object] = {
            "metadata": {
                "app_name": "GNOME Calculator",
                "calibrated_date": datetime.now().strftime("%Y-%m-%d"),
                "screen_resolution": f"{screen_w}x{screen_h}",
                "coordinate_system": "absolute",
                "recorded_points": len(self.recorded),
            },
            "buttons": {},
        }

        buttons: Dict[str, List[int]] = {}
        for i, p in enumerate(self.recorded):
            button_name = self._suggest_button_name(i) or f"button_{i}"
            buttons[button_name] = [p.x, p.y]
        payload["buttons"] = buttons

        with output_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)

        print(f"\n✅ Saved coordinates to: {output_path}")

        # JSON backup
        json_path = output_path.with_suffix(".json")
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"✅ Backup JSON saved to: {json_path}")


def _print_button_order() -> None:
    print("\nSuggested button order for calculator:")
    for i, name in enumerate(CoordinateFinder.DEFAULT_BUTTON_ORDER):
        print(f"  {i:2d}: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive coordinate finder for GUI buttons (press 'p' to record, 'q' to quit)."
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output YAML file path (e.g., config/apps/calculator.yaml). If omitted, prints YAML to stdout.",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List suggested button order and exit.",
    )
    parser.add_argument(
        "--refresh-hz",
        type=float,
        default=20.0,
        help="UI refresh rate for printing mouse position (default: 20).",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=0.35,
        help="Debounce seconds for 'p' key (default: 0.35).",
    )

    args = parser.parse_args()

    if args.list:
        _print_button_order()
        return 0

    # Dependency checks (fail early with clear messages)
    if keyboard is None:
        print("❌ Missing dependency: pynput. Install it: pip install pynput")
        return 2
    if yaml is None and args.output:
        print("❌ Missing dependency: pyyaml. Install it: pip install pyyaml")
        return 2

    finder = CoordinateFinder(
        output_file=args.output,
        debounce_seconds=args.debounce,
        refresh_hz=args.refresh_hz,
    )

    try:
        finder.run()
        return 0
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user. Printing YAML:")
        finder.print_yaml_format()
        return 130
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
