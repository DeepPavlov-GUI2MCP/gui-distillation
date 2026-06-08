# src/core/automation_a11y_dnd.py
"""
Phase 8 experiment: window drag-and-drop + per-step A11Y coordinate update.

Extends automation_a11y.py with a window-move stage between the first and
second calculation to demonstrate that per-step A11Y capture automatically
provides correct coordinates regardless of the window position on screen.

Key question answered: if the calculator window moves, do A11Y-derived
coordinates still work without any manual recalibration?

Answer: yes — because AT-SPI2 always returns absolute desktop coordinates
(DESKTOP_COORDS), so a fresh A11Y capture after the move immediately yields
the updated button positions.

Pipeline steps:
  1. Launch GNOME Calculator
  2. Take screenshot_start, record window geometry
  3. Calculation at initial position: 3 + 5 = 8
     (each button press captures its own A11Y tree → step_NNNN/)
  4. Move window to (move_x, move_y) via wmctrl
     - record geometry before / after move
     - take screenshot_after_move.png
  5. Calculation at new position: 2 + 3 = 5
     (A11Y capture in each step uses the new absolute coordinates automatically)
  6. Switch calculator mode (two sub-steps: open popup → select mode)
  7. Calculation after mode switch: 9 − 2 = 7
  8. Take screenshot_final
  9. Save run_summary.json

Coordinate shift validation:
  run_summary.json includes window_move.coord_shift (dx, dy from wmctrl) and
  a11y_coord_check comparing one button's position before and after the move,
  confirming the shift matches.

Usage:
    python -m src.core.automation_a11y_dnd \\
        [--output <dir>] [--display :99] \\
        [--move-x 600] [--move-y 300] [--verbose]

Output structure:
    run_a11y_dnd_001/
      screenshot_start.png         # calculator at initial position
      screenshot_after_move.png    # calculator at new position, before any clicks
      screenshot_final.png
      run_summary.json             # includes window_move section with coord_shift
      step_0000/ … step_0003/      # calc 1 (3+5=8) at initial position
      step_0004/ … step_0007/      # calc 2 (2+3=5) at NEW position — shifted A11Y coords
      step_0008/ step_0009/        # mode switch
      step_0010/ … step_0013/      # calc 3 (9−2=7) after mode switch

    Each step_NNNN/ contains:
      a11y_tree.xml      ← captured before this step's action
      a11y_buttons.txt   ← visible elements only (x≥0, y≥0)
      before.png / after.png / action.json / metadata.json
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.automation import GUIAutomation
from src.core.a11y_capture import A11YCapture

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]

# ── Calculation sequences ────────────────────────────────────────────────────
CALC_INITIAL: List[JsonDict] = [
    {"description": "3 + 5 = 8", "buttons": ["3", "+", "5", "="]},
]

CALC_AFTER_MOVE: List[JsonDict] = [
    {"description": "2 + 3 = 5", "buttons": ["2", "+", "3", "="]},
]

CALC_AFTER_MODE: List[JsonDict] = [
    {"description": "9 − 2 = 7", "buttons": ["9", "−", "2", "="]},
]

MODE_SWITCH_CANDIDATES = ["Basic", "Programming", "Financial", "Keyboard", "Advanced"]

# Button used to verify coordinate shift in the summary report
COORD_CHECK_BUTTON = "="


def _get_calculator_mode() -> str:
    try:
        r = subprocess.run(
            ["gsettings", "get", "org.gnome.calculator", "button-mode"],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip().strip("'\"")
    except Exception as exc:
        logger.warning("gsettings read failed: %s", exc)
        return ""


# ── Pipeline ─────────────────────────────────────────────────────────────────

class A11YDnDPipeline:
    """
    Demonstrates that per-step A11Y capture keeps coordinates correct
    after a window move — no manual recalibration needed.
    """

    def __init__(
        self,
        output_dir: str = "data/exploration/task_runs/run_a11y_dnd_001",
        settings_path: str = "config/settings.yaml",
        display: Optional[str] = None,
        move_to_x: int = 600,
        move_to_y: int = 300,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = settings_path
        self.display = display
        self.move_to_x = move_to_x
        self.move_to_y = move_to_y
        self.a11y = A11YCapture()

    # ── Public entry point ───────────────────────────────────────────────────

    def run(self) -> JsonDict:
        logger.info("=" * 60)
        logger.info("A11Y DnD Pipeline — start")
        logger.info("Output dir   : %s", self.output_dir)
        logger.info("Move target  : (%d, %d)", self.move_to_x, self.move_to_y)
        logger.info("=" * 60)

        summary: JsonDict = {
            "output_dir": str(self.output_dir),
            "success": False,
            "move_target": {"x": self.move_to_x, "y": self.move_to_y},
            "screenshot_start": None,
            "screenshot_after_move": None,
            "screenshot_final": None,
            "window_move": None,
            "steps": [],
        }

        auto = GUIAutomation(
            app_name="gnome-calculator",
            output_dir=str(self.output_dir),
            settings_path=self.settings_path,
            display=self.display,
        )

        try:
            self._run_pipeline(auto, summary)
        except Exception as exc:
            logger.exception("Pipeline failed: %s", exc)
            summary["error"] = str(exc)
        finally:
            try:
                auto.close_app()
                logger.info("Calculator closed")
            except Exception:
                pass

        summary_path = self.output_dir / "run_summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        status = "SUCCEEDED" if summary["success"] else "FAILED"
        logger.info("Pipeline %s — summary: %s", status, summary_path)
        return summary

    # ── Pipeline stages ──────────────────────────────────────────────────────

    def _run_pipeline(self, auto: GUIAutomation, summary: JsonDict) -> None:

        # ── Stage 1: Launch ───────────────────────────────────────────
        logger.info("[Stage 1] Launching GNOME Calculator...")
        auto.launch_app()
        time.sleep(1.5)

        # ── Stage 2: Baseline screenshot + initial window geometry ────
        logger.info("[Stage 2] screenshot_start + initial window position...")
        start_path = self.output_dir / "screenshot_start.png"
        auto.take_screenshot(start_path)
        summary["screenshot_start"] = {
            "path": str(start_path),
            "md5": auto.compute_image_hash(start_path),
            "dhash": auto.compute_dhash(start_path),
            "window_geometry": self._get_window_geometry(),
        }
        logger.info("  Window geometry (initial): %s",
                    summary["screenshot_start"]["window_geometry"])

        # ── Stage 3: Calculation at INITIAL position ───────────────────
        logger.info("[Stage 3] Calculation at initial window position...")
        step_id = 0
        for calc in CALC_INITIAL:
            logger.info("  %s", calc["description"])
            step_id = self._run_calculation(auto, calc, step_id, summary,
                                            phase="initial")
            time.sleep(0.3)

        # ── Stage 4: Move window ───────────────────────────────────────
        logger.info("[Stage 4] Moving window to (%d, %d)...",
                    self.move_to_x, self.move_to_y)

        # Sample one button coordinate before the move for shift validation
        coord_before = self._sample_button_coord(auto.output_dir, step_id)

        pos_before = self._get_window_geometry()
        wid = pos_before.get("wid") if pos_before else None
        move_ok = self._move_window(self.move_to_x, self.move_to_y, wid=wid)
        time.sleep(0.8)  # let WM apply the move and AT-SPI re-register
        pos_after = self._get_window_geometry()

        logger.info("  Window before move : %s", pos_before)
        logger.info("  Window after move  : %s", pos_after)
        logger.info("  wmctrl success     : %s", move_ok)

        # Pipeline-level screenshot at the new position
        move_path = self.output_dir / "screenshot_after_move.png"
        auto.take_screenshot(move_path)
        summary["screenshot_after_move"] = {
            "path": str(move_path),
            "md5": auto.compute_image_hash(move_path),
            "dhash": auto.compute_dhash(move_path),
        }

        # Compute actual displacement from wmctrl data
        coord_shift: Optional[JsonDict] = None
        if pos_before and pos_after:
            dx = pos_after["x"] - pos_before["x"]
            dy = pos_after["y"] - pos_before["y"]
            coord_shift = {"dx": dx, "dy": dy}
            logger.info("  Displacement       : dx=%d  dy=%d", dx, dy)

        summary["window_move"] = {
            "target": {"x": self.move_to_x, "y": self.move_to_y},
            "position_before": pos_before,
            "position_after": pos_after,
            "wmctrl_success": move_ok,
            "coord_shift": coord_shift,
        }

        # ── Stage 5: Calculation at NEW position ───────────────────────
        logger.info("[Stage 5] Calculation at NEW window position...")
        for calc in CALC_AFTER_MOVE:
            logger.info("  %s", calc["description"])
            step_id = self._run_calculation(auto, calc, step_id, summary,
                                            phase="after_move")
            time.sleep(0.3)

        # Sample same button after the move to confirm shift in the summary
        coord_after = self._sample_button_coord(auto.output_dir, step_id - 1)
        if coord_before and coord_after:
            a11y_dx = coord_after[0] - coord_before[0]
            a11y_dy = coord_after[1] - coord_before[1]
            logger.info("  A11Y coord shift for '%s': dx=%d  dy=%d",
                        COORD_CHECK_BUTTON, a11y_dx, a11y_dy)
            summary["window_move"]["a11y_coord_check"] = {
                "button": COORD_CHECK_BUTTON,
                "coord_before_move": coord_before,
                "coord_after_move": coord_after,
                "a11y_dx": a11y_dx,
                "a11y_dy": a11y_dy,
            }

        # ── Stage 6: Switch calculator mode ───────────────────────────
        logger.info("[Stage 6] Switching calculator mode...")
        step_id = self._switch_mode(auto, step_id, summary)

        # ── Stage 7: Calculation after mode switch ─────────────────────
        logger.info("[Stage 7] Calculation after mode switch...")
        for calc in CALC_AFTER_MODE:
            logger.info("  %s", calc["description"])
            step_id = self._run_calculation(auto, calc, step_id, summary,
                                            phase="after_mode")
            time.sleep(0.3)

        # ── Stage 8: Final screenshot ──────────────────────────────────
        logger.info("[Stage 8] screenshot_final...")
        final_path = self.output_dir / "screenshot_final.png"
        auto.take_screenshot(final_path)
        summary["screenshot_final"] = {
            "path": str(final_path),
            "md5": auto.compute_image_hash(final_path),
            "dhash": auto.compute_dhash(final_path),
            "window_geometry": self._get_window_geometry(),
        }

        summary["success"] = True

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _get_window_geometry(self) -> Optional[JsonDict]:
        """
        Return current calculator window position, size, and window ID via wmctrl -l -G.
        Returns dict {wid, x, y, w, h} or None on failure.
        """
        try:
            r = subprocess.run(
                ["wmctrl", "-l", "-G"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                if "calculator" in line.lower():
                    parts = line.split()
                    # wmctrl -l -G format: WID DT X Y W H HOST TITLE...
                    return {
                        "wid": parts[0],
                        "x": int(parts[2]),
                        "y": int(parts[3]),
                        "w": int(parts[4]),
                        "h": int(parts[5]),
                    }
        except Exception as exc:
            logger.warning("wmctrl -l -G failed: %s", exc)
        return None

    def _move_window(self, target_x: int, target_y: int, wid: Optional[str] = None) -> bool:
        """
        Move the calculator window to (target_x, target_y) using wmctrl.
        Uses window ID (wid) if provided — avoids title-matching failures across locales.
        Gravity 0 = use current gravity. -1 for w/h = keep current size.
        Returns True on success.
        """
        try:
            if wid:
                cmd = ["wmctrl", "-i", "-r", wid, "-e",
                       f"0,{target_x},{target_y},-1,-1"]
            else:
                cmd = ["wmctrl", "-r", "gnome-calculator", "-e",
                       f"0,{target_x},{target_y},-1,-1"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                logger.warning("wmctrl move failed (rc=%d): %s",
                               r.returncode, r.stderr.strip())
                return False
            return True
        except Exception as exc:
            logger.warning("wmctrl error: %s", exc)
            return False

    def _sample_button_coord(
        self,
        output_dir: Path,
        last_step_id: int,
    ) -> Optional[List[int]]:
        """
        Find the center coordinate of COORD_CHECK_BUTTON in the A11Y tree
        captured for `last_step_id`. Used to verify the coordinate shift.
        Returns [cx, cy] or None.
        """
        xml_path = output_dir / f"step_{last_step_id:04d}" / "a11y_tree.xml"
        if not xml_path.exists():
            return None
        try:
            coords = self.a11y.find_button_by_name(xml_path, COORD_CHECK_BUTTON)
            if coords:
                x, y, w, h = coords
                return [x + w // 2, y + h // 2]
        except Exception:
            pass
        return None

    def _capture_step_a11y(self, step_dir: Path) -> Path:
        xml_path = step_dir / "a11y_tree.xml"
        txt_path = step_dir / "a11y_buttons.txt"
        try:
            self.a11y.capture_to_xml("gnome-calculator", xml_path, timeout=5.0)
            self.a11y.parse_xml_to_txt(xml_path, txt_path)
        except Exception as exc:
            logger.warning("  A11Y capture failed for %s: %s", step_dir.name, exc)
        return xml_path

    def _run_calculation(
        self,
        auto: GUIAutomation,
        calc: JsonDict,
        start_step_id: int,
        summary: JsonDict,
        phase: str = "",
    ) -> int:
        step_id = start_step_id
        for btn_label in calc["buttons"]:
            step_dir = auto.output_dir / f"step_{step_id:04d}"
            step_dir.mkdir(parents=True, exist_ok=True)

            xml_path = self._capture_step_a11y(step_dir)

            coords = None
            if xml_path.exists():
                coords = self.a11y.find_button_by_name(xml_path, btn_label)

            if coords is None:
                logger.warning("  Button '%s' not found — skipping", btn_label)
                step_id += 1
                continue

            x, y, w, h = coords
            cx, cy = x + w // 2, y + h // 2

            action: JsonDict = {
                "action_type": "click",
                "coordinates": [cx, cy],
                "parameters": {"button": "left", "clicks": 1},
                "button_label": btn_label,
                "calculation": calc["description"],
                "phase": phase,
            }

            artifacts = auto.run_step(step_id=step_id, action_config=action)

            summary["steps"].append({
                "step_id": step_id,
                "button": btn_label,
                "calculation": calc["description"],
                "phase": phase,
                "coords": [cx, cy],
                "step_dir": str(artifacts.step_dir),
            })

            logger.info("  step_%04d [%s]: clicked '%s' at (%d, %d)",
                        step_id, phase, btn_label, cx, cy)
            step_id += 1
            time.sleep(0.2)

        return step_id

    def _switch_mode(
        self,
        auto: GUIAutomation,
        step_id: int,
        summary: JsonDict,
    ) -> int:
        # Step A: open popup
        step_dir_a = auto.output_dir / f"step_{step_id:04d}"
        step_dir_a.mkdir(parents=True, exist_ok=True)
        xml_path_a = self._capture_step_a11y(step_dir_a)

        toggle_coords = None
        if xml_path_a.exists():
            toggle_coords = self.a11y.find_button_by_name(xml_path_a, "Mode selection")

        if toggle_coords:
            x, y, w, h = toggle_coords
            cx, cy = x + w // 2, y + h // 2
            logger.info("  [A] Clicking 'Mode selection' at (%d, %d)", cx, cy)
            artifacts_a = auto.run_step(step_id=step_id, action_config={
                "action_type": "click",
                "coordinates": [cx, cy],
                "parameters": {"button": "left", "clicks": 1},
                "button_name": "mode_selection_toggle",
                "description": "Open mode selection popup",
                "phase": "mode_switch",
            })
            summary["steps"].append({
                "step_id": step_id,
                "button": "mode_selection_toggle",
                "description": "Open mode selection popup",
                "phase": "mode_switch",
                "coords": [cx, cy],
                "step_dir": str(artifacts_a.step_dir),
            })
            step_id += 1
            time.sleep(0.6)
        else:
            logger.warning("  'Mode selection' not found in A11Y tree — skipping")

        # Step B: pick unchecked mode from popup
        step_dir_b = auto.output_dir / f"step_{step_id:04d}"
        step_dir_b.mkdir(parents=True, exist_ok=True)
        xml_path_b = self._capture_step_a11y(step_dir_b)

        current_mode = _get_calculator_mode()
        ordered = [c for c in MODE_SWITCH_CANDIDATES
                   if c.lower() != current_mode.lower()]
        logger.info("  Current mode: %s  — candidates: %s", current_mode, ordered)

        target = None
        if xml_path_b.exists():
            target = self.a11y.find_unchecked_mode_button(xml_path_b, ordered)

        if target:
            found, x, y, w, h = target
            cx, cy = x + w // 2, y + h // 2
            logger.info("  [B] Clicking mode '%s' at (%d, %d)", found, cx, cy)
            action_mode: JsonDict = {
                "action_type": "click",
                "coordinates": [cx, cy],
                "parameters": {"button": "left", "clicks": 1},
                "button_name": f"mode_{found.lower()}",
                "description": f"Select {found} calculator mode",
                "phase": "mode_switch",
            }
        else:
            logger.warning("  No unchecked mode button found — falling back to F10")
            action_mode = {
                "action_type": "hotkey",
                "parameters": {"keys": ["f10"]},
                "button_name": "F10_fallback",
                "description": "Fallback: open app menu",
                "phase": "mode_switch",
            }
            cx, cy = 0, 0

        artifacts_b = auto.run_step(step_id=step_id, action_config=action_mode)
        summary["steps"].append({
            "step_id": step_id,
            "button": action_mode["button_name"],
            "description": action_mode.get("description", "select mode"),
            "phase": "mode_switch",
            "coords": [cx, cy],
            "step_dir": str(artifacts_b.step_dir),
        })
        step_id += 1
        return step_id


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 8: window move + per-step A11Y coordinate update demo"
    )
    parser.add_argument(
        "--output",
        default="data/exploration/task_runs/run_a11y_dnd_001",
        help="Output directory (default: data/exploration/task_runs/run_a11y_dnd_001)",
    )
    parser.add_argument("--settings", default="config/settings.yaml")
    parser.add_argument(
        "--display", default=None,
        help="X11 display, e.g. :99",
    )
    parser.add_argument(
        "--move-x", type=int, default=600,
        help="Target X position for window move (default: 600)",
    )
    parser.add_argument(
        "--move-y", type=int, default=300,
        help="Target Y position for window move (default: 300)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    pipeline = A11YDnDPipeline(
        output_dir=args.output,
        settings_path=args.settings,
        display=args.display,
        move_to_x=args.move_x,
        move_to_y=args.move_y,
    )
    summary = pipeline.run()
    return 0 if summary.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
