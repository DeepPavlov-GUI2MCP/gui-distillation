#!/usr/bin/env python3
# scripts/tools/find_coordinates_bboxes.py
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pyautogui

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class BBox:
    x1: int
    y1: int
    x2: int
    y2: int


class BBoxFinder:
    DEFAULT_ELEMENT_ORDER: List[str] = [
        "display",
        "backspace", "paren_open", "paren_close", "mod", "pi",
        "digit_7", "digit_8", "digit_9", "divide", "sqrt",
        "digit_4", "digit_5", "digit_6", "multiply", "square",
        "digit_1", "digit_2", "digit_3", "minus", "equals",
        "digit_0", "decimal", "percent", "plus",
    ]

    def __init__(self, output_file: Optional[str] = None) -> None:
        self.output_file = output_file
        self.recorded: Dict[str, BBox] = {}

    def print_instructions(self) -> None:
        print("\n" + "=" * 72)
        print("BBOX FINDER TOOL (ENTER-BASED)")
        print("=" * 72)
        print("\nProtocol for each element:")
        print("  1. Move mouse to TOP-LEFT corner")
        print("  2. Press Enter")
        print("  3. Move mouse to BOTTOM-RIGHT corner")
        print("  4. Press Enter")
        print("\nCommands:")
        print("  - Press Enter to record current corner")
        print("  - Type 'q' and press Enter to quit early")
        print("\n" + "-" * 72)

    def run(self) -> None:
        self.print_instructions()

        try:
            w, h = pyautogui.size()
            print(f"📺 Screen size: {w}x{h}")
        except Exception as e:
            raise RuntimeError(
                f"Cannot access screen via PyAutoGUI: {e}. Ensure X11 and correct DISPLAY."
            ) from e

        for name in self.DEFAULT_ELEMENT_ORDER:
            print(f"\n=== Element: {name} ===")

            cmd = input(f"Move mouse to TOP-LEFT of '{name}' and press Enter (or 'q' to quit): ").strip().lower()
            if cmd == "q":
                break
            x1, y1 = pyautogui.position()
            print(f"  TOP-LEFT recorded: ({x1}, {y1})")

            cmd = input(f"Move mouse to BOTTOM-RIGHT of '{name}' and press Enter (or 'q' to quit): ").strip().lower()
            if cmd == "q":
                break
            x2, y2 = pyautogui.position()
            print(f"  BOTTOM-RIGHT recorded: ({x2}, {y2})")

            bx1, by1 = min(x1, x2), min(y1, y2)
            bx2, by2 = max(x1, x2), max(y1, y2)

            if bx2 <= bx1 or by2 <= by1:
                print(f"❌ Invalid bbox for {name}: [{bx1}, {by1}, {bx2}, {by2}]")
                print("   Skipping this element.")
                continue

            self.recorded[name] = BBox(bx1, by1, bx2, by2)
            print(f"✅ Saved {name}: [{bx1}, {by1}, {bx2}, {by2}]")

        self._finalize()

    def _finalize(self) -> None:
        print("\n" + "=" * 72)
        print(f"📊 RECORDED {len(self.recorded)} BBOXES")
        print("=" * 72)

        for name in self.DEFAULT_ELEMENT_ORDER:
            if name in self.recorded:
                b = self.recorded[name]
                print(f"  {name:15s}: [{b.x1:4d}, {b.y1:4d}, {b.x2:4d}, {b.y2:4d}]")

        if self.output_file:
            self.save_to_file()
        else:
            self.print_yaml_format()

    def print_yaml_format(self) -> None:
        print("\n" + "-" * 72)
        print("YAML FORMAT:")
        print("-" * 72)
        print("metadata:")
        print("  app_name: GNOME Calculator")
        print("  calibrated_date: 'YYYY-MM-DD'")
        print("  screen_resolution: 1280x1024")
        print("  coordinate_system: absolute")
        print(f"  recorded_bboxes: {len(self.recorded)}")
        print("  bbox_format: '[x1, y1, x2, y2]'")
        print("regions:")
        if "display" in self.recorded:
            b = self.recorded["display"]
            print(f"  display: [{b.x1}, {b.y1}, {b.x2}, {b.y2}]")
        print("buttons:")
        for name in self.DEFAULT_ELEMENT_ORDER:
            if name == "display":
                continue
            if name in self.recorded:
                b = self.recorded[name]
                print(f"  {name}: [{b.x1}, {b.y1}, {b.x2}, {b.y2}]")
        print("-" * 72 + "\n")

    def save_to_file(self) -> None:
        if yaml is None:
            raise RuntimeError("PyYAML is not installed. Install it: pip install pyyaml")

        output_path = Path(self.output_file).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        screen_w, screen_h = pyautogui.size()

        payload: Dict[str, object] = {
            "metadata": {
                "app_name": "GNOME Calculator",
                "calibrated_date": datetime.now().strftime("%Y-%m-%d"),
                "screen_resolution": f"{screen_w}x{screen_h}",
                "coordinate_system": "absolute",
                "recorded_bboxes": len(self.recorded),
                "bbox_format": "[x1, y1, x2, y2]",
            },
            "regions": {},
            "buttons": {},
        }

        regions: Dict[str, List[int]] = {}
        buttons: Dict[str, List[int]] = {}

        for name in self.DEFAULT_ELEMENT_ORDER:
            if name not in self.recorded:
                continue
            b = self.recorded[name]
            vals = [b.x1, b.y1, b.x2, b.y2]
            if name == "display":
                regions[name] = vals
            else:
                buttons[name] = vals

        payload["regions"] = regions
        payload["buttons"] = buttons

        with output_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)

        print(f"\n✅ Saved bboxes to: {output_path}")

        json_path = output_path.with_suffix(".json")
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"✅ Backup JSON saved to: {json_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive bbox finder (Enter-based).")
    parser.add_argument(
        "--output",
        "-o",
        default="config/apps/calculator_bboxes.yaml",
        help="Output YAML file path (default: config/apps/calculator_bboxes.yaml).",
    )
    args = parser.parse_args()

    if yaml is None:
        print("❌ Missing dependency: pyyaml. Install it: pip install pyyaml")
        return 2

    finder = BBoxFinder(output_file=args.output)

    try:
        finder.run()
        return 0
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user.")
        finder.print_yaml_format()
        return 130
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
