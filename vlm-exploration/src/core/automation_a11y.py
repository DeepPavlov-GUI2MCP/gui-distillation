# src/core/automation_a11y.py
"""
Pipeline: per-step A11Y tree capture + dHash with Hamming distance.

At every action step the A11Y tree is captured fresh into the step directory,
button coordinates are taken from that snapshot, and the action is executed.
MD5 + dHash are computed before and after each click; Hamming distance between
the two dHash values is written into step_NNNN/metadata.json under
dhashes.hamming_distance.

No root-level A11Y files are saved (the per-step XMLs are the source of truth).
No dhash_comparison.json is written (Hamming distance in metadata is sufficient).

Pipeline steps:
  1. Launch GNOME Calculator
  2. Take screenshot_start, compute MD5 + dHash
  3. Execute 2 hardcoded calculations; for each button click:
       a. Capture A11Y tree → step_NNNN/a11y_tree.xml + step_NNNN/a11y_buttons.txt
       b. Look up button coordinates from the freshly captured tree
       c. Run step → before.png / after.png / action.json / metadata.json
          (metadata includes dhashes.hamming_distance)
  4. Switch calculator mode in two sub-steps (open popup → pick unchecked mode);
     each sub-step captures its own A11Y tree
  5. Run 2 more calculations using the same per-step A11Y strategy
  6. Take screenshot_final, compute MD5 + dHash
  7. Save run_summary.json (steps only — details are in per-step metadata.json)

Usage:
    python -m src.core.automation_a11y [--output <dir>] [--display :99] [--verbose]

Output structure:
    run_a11y_001/
      screenshot_start.png
      screenshot_final.png
      run_summary.json
      step_0000/
        a11y_tree.xml       ← A11Y tree captured before this step's action
        a11y_buttons.txt
        before.png
        after.png
        action.json
        metadata.json       ← includes dhashes.hamming_distance
      step_0001/ ...
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.automation import GUIAutomation
from src.core.a11y_capture import A11YCapture

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


def _get_current_calculator_mode() -> str:
    """Read the current GNOME Calculator mode from gsettings. Returns e.g. 'basic', 'advanced'."""
    import subprocess
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.calculator", "button-mode"],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip().strip("'\"")
    except Exception as exc:
        logger.warning("Could not read calculator mode from gsettings: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Calculation sequences — button labels as they appear in the A11Y tree
# ---------------------------------------------------------------------------
CALCULATIONS: List[JsonDict] = [
    {
        "description": "3 + 5 = 8",
        "buttons": ["3", "+", "5", "="],
    },
    {
        "description": "7 × 4 = 28",
        "buttons": ["7", "×", "4", "="],
    },
]

CALCULATIONS_AFTER: List[JsonDict] = [
    {
        "description": "9 − 2 = 7",
        "buttons": ["9", "−", "2", "="],
    },
    {
        "description": "6 ÷ 3 = 2",
        "buttons": ["6", "÷", "3", "="],
    },
]

MODE_SWITCH_CANDIDATES = ["Basic", "Programming", "Financial", "Keyboard", "Advanced"]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class A11YPipeline:
    """
    Pipeline with per-step A11Y tree capture and Hamming-distance state tracking.
    """

    def __init__(
        self,
        output_dir: str = "data/exploration/task_runs/run_a11y_001",
        settings_path: str = "config/settings.yaml",
        display: Optional[str] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = settings_path
        self.display = display
        self.a11y = A11YCapture()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> JsonDict:
        """Execute the full pipeline. Returns a summary dict."""
        logger.info("=" * 60)
        logger.info("A11Y Pipeline — start")
        logger.info("Output dir : %s", self.output_dir)
        logger.info("=" * 60)

        summary: JsonDict = {
            "output_dir": str(self.output_dir),
            "success": False,
            "screenshot_start": None,
            "screenshot_final": None,
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

    # ------------------------------------------------------------------
    # Internal pipeline stages
    # ------------------------------------------------------------------

    def _run_pipeline(self, auto: GUIAutomation, summary: JsonDict) -> None:

        # ── Stage 1: Launch ──────────────────────────────────────────
        logger.info("[Stage 1] Launching GNOME Calculator...")
        auto.launch_app()
        time.sleep(1.5)  # extra settle time for AT-SPI bridge

        # ── Stage 2: Baseline screenshot + hashes ────────────────────
        logger.info("[Stage 2] Taking screenshot_start, computing hashes...")
        start_path = self.output_dir / "screenshot_start.png"
        auto.take_screenshot(start_path)
        md5_start = auto.compute_image_hash(start_path)
        dhash_start = auto.compute_dhash(start_path)
        logger.info("  MD5   : %s", md5_start)
        logger.info("  dHash : %s", dhash_start)
        summary["screenshot_start"] = {
            "path": str(start_path),
            "md5": md5_start,
            "dhash": dhash_start,
        }

        # ── Stage 3: Run 2 calculations ──────────────────────────────
        step_id = 0
        for calc in CALCULATIONS:
            logger.info("[Stage 3] Calculation: %s", calc["description"])
            step_id = self._run_calculation(auto, calc, step_id, summary)
            time.sleep(0.5)

        # ── Stage 4: Switch calculator mode ──────────────────────────
        logger.info("[Stage 4] Switching calculator mode...")
        step_id = self._switch_mode(auto, step_id, summary)

        # ── Stage 5: 2 more calculations ─────────────────────────────
        logger.info("[Stage 5] Running post-mode-change calculations...")
        for calc in CALCULATIONS_AFTER:
            logger.info("  Calculation: %s", calc["description"])
            step_id = self._run_calculation(auto, calc, step_id, summary)
            time.sleep(0.5)

        # ── Stage 6: Final screenshot ─────────────────────────────────
        logger.info("[Stage 6] Taking screenshot_final, computing hashes...")
        final_path = self.output_dir / "screenshot_final.png"
        auto.take_screenshot(final_path)
        md5_final = auto.compute_image_hash(final_path)
        dhash_final = auto.compute_dhash(final_path)
        logger.info("  MD5   : %s", md5_final)
        logger.info("  dHash : %s", dhash_final)
        summary["screenshot_final"] = {
            "path": str(final_path),
            "md5": md5_final,
            "dhash": dhash_final,
        }

        summary["success"] = True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _capture_step_a11y(self, step_dir: Path) -> Path:
        """
        Capture A11Y tree into the step directory before the action is executed.

        Saves:
            step_dir/a11y_tree.xml
            step_dir/a11y_buttons.txt

        Returns xml_path (used for button coordinate lookups).
        """
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
    ) -> int:
        """
        Execute a multi-button calculation with per-step A11Y capture.

        For every button:
          1. Pre-create step_NNNN/ directory
          2. Capture A11Y → a11y_tree.xml + a11y_buttons.txt
          3. Look up button coordinates from the fresh tree
          4. run_step() → before.png / action → after.png / metadata.json
             (metadata.dhashes.hamming_distance written by automation.py)

        Returns next available step_id.
        """
        step_id = start_step_id
        for btn_label in calc["buttons"]:
            step_dir = auto.output_dir / f"step_{step_id:04d}"
            step_dir.mkdir(parents=True, exist_ok=True)

            xml_path = self._capture_step_a11y(step_dir)

            coords = None
            if xml_path.exists():
                coords = self.a11y.find_button_by_name(xml_path, btn_label)

            if coords is None:
                logger.warning("  Button '%s' not found in A11Y tree — skipping", btn_label)
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
            }

            artifacts = auto.run_step(step_id=step_id, action_config=action)

            summary["steps"].append({
                "step_id": step_id,
                "button": btn_label,
                "calculation": calc["description"],
                "coords": [cx, cy],
                "step_dir": str(artifacts.step_dir),
            })

            logger.info(
                "  step_%04d: clicked '%s' at (%d, %d)", step_id, btn_label, cx, cy
            )
            step_id += 1
            time.sleep(0.2)

        return step_id

    def _switch_mode(
        self,
        auto: GUIAutomation,
        step_id: int,
        summary: JsonDict,
    ) -> int:
        """
        Switch calculator mode in two sub-steps, each with its own A11Y capture.

          Step A — click "Mode selection" toggle to open the popup.
          Step B — A11Y re-scan (popup visible), click an unchecked mode button.

        Returns next available step_id.
        """
        # ── Step A: open the mode popup ──────────────────────────────
        step_dir_a = auto.output_dir / f"step_{step_id:04d}"
        step_dir_a.mkdir(parents=True, exist_ok=True)
        xml_path_a = self._capture_step_a11y(step_dir_a)

        toggle_coords = None
        if xml_path_a.exists():
            toggle_coords = self.a11y.find_button_by_name(xml_path_a, "Mode selection")

        if toggle_coords:
            x, y, w, h = toggle_coords
            cx, cy = x + w // 2, y + h // 2
            logger.info("  [A] Clicking 'Mode selection' toggle at (%d, %d)", cx, cy)
            action_toggle: JsonDict = {
                "action_type": "click",
                "coordinates": [cx, cy],
                "parameters": {"button": "left", "clicks": 1},
                "button_name": "mode_selection_toggle",
                "description": "Open mode selection popup",
            }
            artifacts_a = auto.run_step(step_id=step_id, action_config=action_toggle)
            summary["steps"].append({
                "step_id": step_id,
                "button": "mode_selection_toggle",
                "description": "Open mode selection popup",
                "coords": [cx, cy],
                "step_dir": str(artifacts_a.step_dir),
            })
            logger.info("  step_%04d: mode popup opened", step_id)
            step_id += 1
            time.sleep(0.6)  # wait for popup + AT-SPI re-registration
        else:
            logger.warning("  'Mode selection' toggle not found in A11Y tree — skipping")

        # ── Step B: re-scan A11Y (popup visible), click unchecked mode ──
        step_dir_b = auto.output_dir / f"step_{step_id:04d}"
        step_dir_b.mkdir(parents=True, exist_ok=True)
        logger.info("  [B] Capturing A11Y tree with popup visible...")
        xml_path_b = self._capture_step_a11y(step_dir_b)

        current_mode = _get_current_calculator_mode()
        logger.info("  Current calculator mode (gsettings): %s", current_mode)

        ordered = [c for c in MODE_SWITCH_CANDIDATES if c.lower() != current_mode.lower()]
        logger.info("  Target candidates (excl. current): %s", ordered)

        target = None
        if xml_path_b.exists():
            target = self.a11y.find_unchecked_mode_button(xml_path_b, ordered)

        if target:
            found_candidate, x, y, w, h = target
            cx, cy = x + w // 2, y + h // 2
            logger.info("  [B] Clicking '%s' mode button at (%d, %d)", found_candidate, cx, cy)
            action_mode: JsonDict = {
                "action_type": "click",
                "coordinates": [cx, cy],
                "parameters": {"button": "left", "clicks": 1},
                "button_name": f"mode_{found_candidate.lower()}",
                "description": f"Select {found_candidate} calculator mode",
            }
        else:
            logger.warning("  No unchecked mode button found in popup — falling back to F10")
            action_mode = {
                "action_type": "hotkey",
                "parameters": {"keys": ["f10"]},
                "button_name": "F10_fallback",
                "description": "Open GNOME app menu (mode button not found in popup A11Y)",
            }
            cx, cy = 0, 0

        artifacts_b = auto.run_step(step_id=step_id, action_config=action_mode)
        summary["steps"].append({
            "step_id": step_id,
            "button": action_mode["button_name"],
            "description": action_mode.get("description", "select mode"),
            "coords": [cx, cy],
            "step_dir": str(artifacts_b.step_dir),
        })
        logger.info("  step_%04d: mode button clicked", step_id)
        step_id += 1

        return step_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-step A11Y capture + Hamming-distance state tracking (GNOME Calculator demo)"
    )
    parser.add_argument(
        "--output",
        default="data/exploration/task_runs/run_a11y_001",
        help="Output directory for all artifacts (default: data/exploration/task_runs/run_a11y_001)",
    )
    parser.add_argument(
        "--settings",
        default="config/settings.yaml",
        help="Path to settings.yaml",
    )
    parser.add_argument(
        "--display",
        default=None,
        help="X11 display override, e.g. :99 (default: read from settings.yaml)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    pipeline = A11YPipeline(
        output_dir=args.output,
        settings_path=args.settings,
        display=args.display,
    )
    summary = pipeline.run()
    return 0 if summary.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
