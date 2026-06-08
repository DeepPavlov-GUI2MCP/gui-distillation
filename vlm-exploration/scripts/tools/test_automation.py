#!/usr/bin/env python3
# scripts/tools/test_automation.py
"""
GUI automation infrastructure test (Phase 0.3).

What it verifies:
1) DISPLAY is accessible (Xvfb / X11)
2) App can be launched
3) Screenshots can be taken and saved
4) Click actions execute
5) Step runner produces artifacts: before.png, after.png, metadata.json
6) Optional: calculator digit test if coordinates exist in app config

This tool is intentionally integration-ish: it touches GUI, filesystem, and process management.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

# Ensure repo root is importable
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.automation import GUIAutomation  # noqa: E402
from src.core.exceptions import AppLaunchError, ScreenshotError  # noqa: E402

logger = logging.getLogger("automation_tester")


@dataclass
class TestStats:
    passed: int = 0
    failed: int = 0
    skipped: int = 0


class AutomationTester:
    def __init__(
        self,
        repo_root: Path,
        app_name: str,
        display: str,
        outdir: Path,
        app_config_path: Optional[Path] = None,
    ) -> None:
        self.repo_root = repo_root
        self.app_name = app_name
        self.display = display
        self.outdir = outdir
        self.app_config_path = app_config_path

        self.stats = TestStats()
        self.automation: Optional[GUIAutomation] = None

    # -------------------------
    # Pretty printing
    # -------------------------
    def header(self, title: str) -> None:
        print("\n" + "=" * 70)
        print(f" {title}")
        print("=" * 70)

    def ok(self, name: str, msg: str = "") -> None:
        self.stats.passed += 1
        print(f"  ✅ {name}: {msg}" if msg else f"  ✅ {name}")

    def fail(self, name: str, msg: str) -> None:
        self.stats.failed += 1
        print(f"  ❌ {name}: {msg}")

    def skip(self, name: str, reason: str) -> None:
        self.stats.skipped += 1
        print(f"  ⏭️  {name}: {reason}")

    def summary(self) -> None:
        print("\n" + "=" * 70)
        print(" TEST SUMMARY")
        print("=" * 70)
        print(f"  ✅ Passed:  {self.stats.passed}")
        print(f"  ❌ Failed:  {self.stats.failed}")
        print(f"  ⏭️  Skipped: {self.stats.skipped}")
        if self.stats.failed == 0:
            print("\n  🎉 ALL TESTS PASSED! Infrastructure is ready.")
        else:
            print("\n  ⚠️  Some tests failed. See details above.")

    # -------------------------
    # Tests
    # -------------------------
    def test_display(self) -> None:
        try:
            import pyautogui  # local import to avoid import-time crashes elsewhere

            current = os.environ.get("DISPLAY", "not set")
            w, h = pyautogui.size()
            self.ok("Display check", f"DISPLAY={current}, screen={w}x{h}")
        except Exception as e:
            self.fail("Display check", str(e))

    def test_init(self) -> None:
        try:
            # Ensure output dir exists
            self.outdir.mkdir(parents=True, exist_ok=True)

            self.automation = GUIAutomation(
                app_name=self.app_name,
                output_dir=self.outdir,
                app_config_path=str(self.app_config_path) if self.app_config_path else None,
                display=self.display,
            )
            self.ok("Automation init")
        except Exception as e:
            self.fail("Automation init", str(e))

    def test_launch(self) -> None:
        if not self.automation:
            self.skip("Launch app", "No automation instance")
            return

        try:
            self.automation.launch_app()
            pid = getattr(self.automation, "app_process", None)
            pid_val = pid.pid if pid else "unknown"
            self.ok("Launch app", f"PID={pid_val}")
        except AppLaunchError as e:
            self.fail("Launch app", str(e))
        except Exception as e:
            self.fail("Launch app", f"Unexpected: {e}")

    def test_screenshot(self) -> None:
        if not self.automation:
            self.skip("Screenshot", "No automation instance")
            return

        try:
            p = self.automation.take_screenshot("test_screenshot")
            if p.exists() and p.stat().st_size > 0:
                self.ok("Screenshot", f"Saved: {p} ({p.stat().st_size} bytes)")
            else:
                self.fail("Screenshot", "File not created or empty")
        except ScreenshotError as e:
            self.fail("Screenshot", str(e))
        except Exception as e:
            self.fail("Screenshot", f"Unexpected: {e}")

    def test_click(self) -> None:
        if not self.automation:
            self.skip("Click", "No automation instance")
            return

        try:
            import pyautogui

            w, h = pyautogui.size()
            x, y = w // 2, h // 2
            self.automation.perform_action(
                {
                    "action_type": "click",
                    "coordinates": (x, y),
                    "parameters": {"button": "left", "clicks": 1},
                }
            )
            self.ok("Click", f"Clicked at ({x}, {y})")
        except Exception as e:
            self.fail("Click", str(e))

    def test_step_artifacts(self) -> None:
        if not self.automation:
            self.skip("Before/After step", "No automation instance")
            return

        try:
            import pyautogui

            w, h = pyautogui.size()
            x, y = w // 2, h // 2

            step = self.automation.run_step(
                step_id=0,
                action_config={
                    "action_type": "click",
                    "coordinates": (x, y),
                    "parameters": {"button": "left", "clicks": 1},
                },
            )

            # Expect step to have .step_dir OR be a dict-like with "step_dir"
            step_dir = getattr(step, "step_dir", None)
            if step_dir is None and isinstance(step, dict):
                step_dir = step.get("step_dir")

            if not step_dir:
                # fallback: assume output_dir/step_0000
                step_dir = self.outdir / "step_0000"
            else:
                step_dir = Path(step_dir)

            before = step_dir / "before.png"
            after = step_dir / "after.png"
            meta = step_dir / "metadata.json"

            missing = [p.name for p in (before, after, meta) if not p.exists()]
            if missing:
                self.fail("Before/After step", f"Missing artifacts: {missing} in {step_dir}")
                return

            # Validate metadata structure
            with open(meta, "r", encoding="utf-8") as f:
                meta_obj = json.load(f)

            # We only require presence of some keys; structure may evolve.
            if "changed" not in meta_obj:
                # some implementations nest it (e.g., {"metadata": {...}})
                changed = meta_obj.get("metadata", {}).get("changed", None)
            else:
                changed = meta_obj.get("changed")

            self.ok("Before/After step", "Artifacts ok" + (f", changed={changed}" if changed is not None else ""))

        except Exception as e:
            self.fail("Before/After step", str(e))

    def test_close(self) -> None:
        if not self.automation:
            self.skip("Close app", "No automation instance")
            return
        try:
            self.automation.close_app()
            self.ok("Close app")
        except Exception as e:
            self.fail("Close app", str(e))

    def test_calculator_basic(self) -> None:
        """Run 2 + 2 = using calibrated coordinates as a deterministic smoke test."""
        if not self.automation:
            self.skip("Calculator basic (2+2)", "No automation instance")
            return

        required = ["digit_2", "plus", "equals"]
        missing = [k for k in required if self.automation.get_button_coordinates(k) is None]
        if missing:
            self.skip("Calculator basic (2+2)", f"Missing coords in app-config: {missing}")
            return

        try:
            sequence = ["clear", "digit_2", "plus", "digit_2", "equals"]
            actions = []
            for key in sequence:
                coords = self.automation.get_button_coordinates(key)
                if coords is None:
                    continue  # clear is optional
                actions.append(
                    {"action_type": "click", "coordinates": coords, "parameters": {"button": "left", "clicks": 1}}
                )
            self.automation.run_sequence(actions, start_id=200)
            self.ok("Calculator basic (2+2)", f"{len(actions)} clicks completed")
        except Exception as e:
            self.fail("Calculator basic (2+2)", str(e))

    def test_calculator_digit_5(self) -> None:
        if not self.automation:
            self.skip("Calculator digit_5 test", "No automation instance")
            return

        # Requires config with digit_5
        coords = None
        try:
            coords = self.automation.get_button_coordinates("digit_5")  # type: ignore[attr-defined]
        except Exception:
            pass

        if not coords:
            self.skip("Calculator digit_5 test", "No digit_5 coordinates available")
            return

        try:
            # Before
            before = self.automation.take_screenshot("calc_digit5_before")
            # Click
            self.automation.perform_action(
                {
                    "action_type": "click",
                    "coordinates": tuple(coords),
                    "parameters": {"button": "left", "clicks": 1},
                }
            )
            time.sleep(0.5)
            after = self.automation.take_screenshot("calc_digit5_after")

            # Compare file sizes as a weak sanity check (hashing handled inside step pipeline)
            changed = before.stat().st_size != after.stat().st_size
            self.ok("Calculator digit_5 test", f"clicked at {coords}, weak_changed={changed}")
        except Exception as e:
            self.fail("Calculator digit_5 test", str(e))

    def run(self) -> int:
        self.header("GUI AUTOMATION TEST SUITE (Phase 0.3)")
        print(f"Repo:    {self.repo_root}")
        print(f"App:     {self.app_name}")
        print(f"Display: {self.display}")
        print(f"Outdir:  {self.outdir}")

        self.test_display()
        self.test_init()
        self.test_launch()
        self.test_screenshot()
        self.test_click()
        self.test_step_artifacts()
        self.test_calculator_basic()
        self.test_calculator_digit_5()
        self.test_close()

        self.summary()
        return 1 if self.stats.failed > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Test GUI automation infrastructure (Phase 0.3)")
    parser.add_argument("--app", default="gnome-calculator", help="Application binary name")
    parser.add_argument("--display", default=":99", help="X11 display (e.g. :99)")
    parser.add_argument("--outdir", default="data/test_automation", help="Output directory (relative to repo)")
    parser.add_argument(
        "--app-config",
        default="config/apps/calculator.yaml",
        help="App coordinates config YAML (relative to repo). Optional.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logs")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    repo_root = REPO_ROOT
    outdir = (repo_root / args.outdir).resolve()
    app_config_path = (repo_root / args.app_config).resolve() if args.app_config else None
    if app_config_path and not app_config_path.exists():
        app_config_path = None

    tester = AutomationTester(
        repo_root=repo_root,
        app_name=args.app,
        display=args.display,
        outdir=outdir,
        app_config_path=app_config_path,
    )
    raise SystemExit(tester.run())


if __name__ == "__main__":
    main()
