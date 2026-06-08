# src/core/automation_dhash.py
"""
Experimental pipeline: A11Y tree + dHash-based interface change detection.

Demonstrates that UI state changes (mode switches, layout changes) can be
reliably detected via dHash, and that updated button coordinates can be
captured from the new A11Y tree without any manual recalibration.

Pipeline steps:
  1. Launch GNOME Calculator
  2. Capture initial A11Y tree → XML + filtered TXT with button coordinates
  3. Take screenshot_before, compute MD5 + dHash
  4. Execute 2 hardcoded calculations via calibrated button coordinates
  5. Switch calculator mode (find "Basic" button in A11Y tree and click it)
  6. Compare dHash before/after → detect interface change
  7. If dHash changed: re-capture A11Y tree with updated coordinates
  8. Save dhash_comparison.json and run_summary.json

Usage:
    python -m src.core.automation_dhash [--output <dir>] [--display :99] [--verbose]

Output structure:
    run_dhash_001/
      a11y_tree_initial.xml
      a11y_buttons_initial.txt
      screenshot_start.png
      screenshot_final.png
      step_0000/   ← calc 1, button: digit_3
      step_0001/   ← calc 1, button: plus
      step_0002/   ← calc 1, button: digit_5
      step_0003/   ← calc 1, button: equals
      step_0004/   ← calc 2, button: digit_7
      step_0005/   ← calc 2, button: multiply
      step_0006/   ← calc 2, button: digit_4
      step_0007/   ← calc 2, button: equals
      step_0008/   ← mode switch click
      a11y_tree_after_mode_change.xml      (only if dHash changed)
      a11y_buttons_after_mode_change.txt   (only if dHash changed)
      screenshot_after.png
      dhash_comparison.json
      run_summary.json
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
        # Output is like: 'basic'\n  — strip quotes and whitespace
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

# Calculations to run after mode change — using new A11Y coordinates
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

# Candidate names for the mode-switch button in the A11Y tree.
# GNOME Calculator header bar typically shows "Basic", "Advanced", etc.
MODE_SWITCH_CANDIDATES = ["Basic", "Programming", "Financial", "Keyboard", "Advanced"]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
class DHashPipeline:
    """
    Experimental pipeline demonstrating dHash-based UI state change detection.
    """

    def __init__(
        self,
        output_dir: str = "data/exploration/task_runs/run_dhash_001",
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
        logger.info("DHash Pipeline — start")
        logger.info("Output dir : %s", self.output_dir)
        logger.info("=" * 60)

        summary: JsonDict = {
            "output_dir": str(self.output_dir),
            "steps": [],
            "dhash_comparison": {},
            "a11y_initial": None,
            "a11y_after": None,
            "success": False,
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

        # Persist run summary
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
        """Core pipeline logic (separated so __exit__ always fires on auto)."""

        # ── Stage 1: Launch ──────────────────────────────────────────
        logger.info("[Stage 1] Launching GNOME Calculator...")
        auto.launch_app()
        time.sleep(1.5)  # extra settle time for AT-SPI bridge

        # ── Stage 2: Initial A11Y capture ────────────────────────────
        logger.info("[Stage 2] Capturing initial A11Y tree...")
        xml_init, txt_init = self.a11y.capture_and_parse(
            app_name="gnome-calculator",
            output_dir=self.output_dir,
            suffix="initial",
            timeout=8.0,
        )
        summary["a11y_initial"] = {"xml": str(xml_init), "txt": str(txt_init)}
        logger.info("  XML : %s", xml_init)
        logger.info("  TXT : %s", txt_init)

        # ── Stage 3: Baseline screenshot + hashes ────────────────────
        logger.info("[Stage 3] Taking screenshot_start, computing hashes...")
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

        # ── Stage 4: Run 2 calculations ──────────────────────────────
        step_id = 0
        for calc in CALCULATIONS:
            logger.info("[Stage 4] Calculation: %s", calc["description"])
            step_id = self._run_calculation(auto, calc, xml_init, step_id, summary)
            time.sleep(0.5)

        # ── Stage 5: Switch calculator mode (2 steps: toggle → Keyboard) ──
        logger.info("[Stage 5] Switching calculator mode to Keyboard...")
        step_id, dhash_comparison = self._switch_mode(auto, xml_init, step_id, summary)

        summary["dhash_comparison"] = dhash_comparison
        report_path = self.output_dir / "dhash_comparison.json"
        report_path.write_text(
            json.dumps(dhash_comparison, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("  Report saved : %s", report_path)

        # ── Stage 6: Re-capture A11Y tree if mode step changed dHash ────
        dhash_changed = dhash_comparison["dhash_changed"]
        logger.info("[Stage 6] dHash changed: %s", dhash_changed)
        xml_after = None
        if dhash_changed:
            logger.info("  Interface changed — recapturing A11Y tree...")
            xml_after, txt_after = self.a11y.capture_and_parse(
                app_name="gnome-calculator",
                output_dir=self.output_dir,
                suffix="after_mode_change",
                timeout=8.0,
            )
            summary["a11y_after"] = {"xml": str(xml_after), "txt": str(txt_after)}
            logger.info("  XML : %s", xml_after)
            logger.info("  TXT : %s", txt_after)
        else:
            logger.info("  dHash unchanged — interface did not change")

        # ── Stage 7: 2 calculations using updated A11Y coords (or initial) ─
        xml_for_calc2 = xml_after if xml_after is not None else xml_init
        source = "after_mode_change" if xml_after is not None else "initial (mode unchanged)"
        logger.info("[Stage 7] Running calculations using A11Y tree: %s", source)
        for calc in CALCULATIONS_AFTER:
            logger.info("  Calculation: %s", calc["description"])
            step_id = self._run_calculation(auto, calc, xml_for_calc2, step_id, summary)
            time.sleep(0.5)

        # ── Stage 8: Final screenshot ─────────────────────────────────
        logger.info("[Stage final] Taking screenshot_final, computing hashes...")
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

    def _run_calculation(
        self,
        auto: GUIAutomation,
        calc: JsonDict,
        xml_path: Path,
        start_step_id: int,
        summary: JsonDict,
    ) -> int:
        """
        Execute a multi-button calculation using coordinates from the A11Y tree.
        Saves one step artifact per button press.

        Returns the next available step_id.
        """
        step_id = start_step_id
        for btn_label in calc["buttons"]:
            coords = self.a11y.find_button_by_name(xml_path, btn_label)
            if coords is None:
                logger.warning("  Button '%s' not found in A11Y tree — skipping", btn_label)
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
                "step_dir": str(artifacts.step_dir),
            })

            logger.info("  step_%04d: clicked '%s' at (%d, %d)", step_id, btn_label, cx, cy)
            step_id += 1
            time.sleep(0.2)

        return step_id

    def _switch_mode(
        self,
        auto: GUIAutomation,
        xml_path: Path,
        step_id: int,
        summary: JsonDict,
    ) -> tuple[int, JsonDict]:
        """
        Switch calculator mode in two steps:
          Step A — click "Mode selection" toggle to open the popup.
          Step B — re-scan A11Y tree, click "Keyboard" inside the popup.

        The dHash comparison is taken from Step B (before vs after clicking Keyboard),
        so it directly reflects whether the interface layout changed.

        Returns (next_step_id, dhash_comparison_dict).
        """
        # ── Step A: open the mode popup ──────────────────────────────
        toggle_coords = self.a11y.find_button_by_name(xml_path, "Mode selection")
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
                "step_dir": str(artifacts_a.step_dir),
            })
            logger.info("  step_%04d: mode popup opened", step_id)
            step_id += 1
            time.sleep(0.6)  # wait for popup + AT-SPI re-registration
        else:
            logger.warning("  'Mode selection' toggle not found in A11Y tree — skipping")

        # ── Step B: re-scan A11Y, find "Keyboard", click it ──────────
        logger.info("  [B] Re-scanning A11Y tree for mode options in popup...")
        xml_popup = self.output_dir / "a11y_tree_popup.xml"
        try:
            self.a11y.capture_to_xml("gnome-calculator", xml_popup, timeout=5.0)
        except Exception as exc:
            logger.warning("  A11Y popup re-scan failed: %s", exc)

        # Determine current mode from gsettings so we always pick a different one
        current_mode = _get_current_calculator_mode()
        logger.info("  Current calculator mode (gsettings): %s", current_mode)

        # Build candidates excluding current mode
        ordered = [c for c in MODE_SWITCH_CANDIDATES if c.lower() != current_mode.lower()]
        logger.info("  Target candidates (excl. current): %s", ordered)

        target = None
        if xml_popup.exists():
            target = self.a11y.find_unchecked_mode_button(xml_popup, ordered)

        if target:
            found_candidate, x, y, w, h = target
            cx, cy = x + w // 2, y + h // 2
            logger.info("  [B] Clicking '%s' mode button at (%d, %d)", found_candidate, cx, cy)
            action_keyboard: JsonDict = {
                "action_type": "click",
                "coordinates": [cx, cy],
                "parameters": {"button": "left", "clicks": 1},
                "button_name": f"mode_{found_candidate.lower()}",
                "description": f"Select {found_candidate} calculator mode",
            }
        else:
            logger.warning("  No unchecked mode button found in popup — falling back to F10")
            action_keyboard = {
                "action_type": "hotkey",
                "parameters": {"keys": ["f10"]},
                "button_name": "F10_fallback",
                "description": "Open GNOME app menu (mode button not found in popup A11Y)",
            }

        artifacts_b = auto.run_step(step_id=step_id, action_config=action_keyboard)
        summary["steps"].append({
            "step_id": step_id,
            "button": action_keyboard["button_name"],
            "description": action_keyboard.get("description", "select mode"),
            "step_dir": str(artifacts_b.step_dir),
        })
        logger.info("  step_%04d: Keyboard mode selected", step_id)
        step_id += 1

        # ── Read dHash from Step B metadata (before vs after click) ──
        meta = json.loads((artifacts_b.step_dir / "metadata.json").read_text(encoding="utf-8"))
        dhash_before_step = (meta.get("dhashes") or {}).get("before", "")
        dhash_after_step = (meta.get("dhashes") or {}).get("after", "")
        dhash_changed = bool(dhash_before_step and dhash_after_step and
                             dhash_before_step != dhash_after_step)

        logger.info("  dHash before Keyboard click : %s", dhash_before_step)
        logger.info("  dHash after  Keyboard click : %s", dhash_after_step)
        logger.info("  dHash changed               : %s", dhash_changed)

        dhash_comparison: JsonDict = {
            "step_id": step_id - 1,
            "step_dir": str(artifacts_b.step_dir),
            "screenshot_before": str(artifacts_b.before),
            "screenshot_after": str(artifacts_b.after),
            "dhash_before": dhash_before_step,
            "dhash_after": dhash_after_step,
            "dhash_changed": dhash_changed,
            "md5_before": (meta.get("hashes") or {}).get("before", ""),
            "md5_after": (meta.get("hashes") or {}).get("after", ""),
            "md5_changed": (meta.get("changed") or False),
        }
        return step_id, dhash_comparison


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="dHash-based UI change detection pipeline (GNOME Calculator demo)"
    )
    parser.add_argument(
        "--output",
        default="data/exploration/task_runs/run_dhash_001",
        help="Output directory for all artifacts (default: data/exploration/task_runs/run_dhash_001)",
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

    pipeline = DHashPipeline(
        output_dir=args.output,
        settings_path=args.settings,
        display=args.display,
    )
    summary = pipeline.run()
    return 0 if summary.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
