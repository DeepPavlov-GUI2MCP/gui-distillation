# src/core/automation.py
"""
GUI Automation core module.

Phase: 0.3 -> extended for Phase 1 readiness

Responsibilities:
- Application lifecycle (launch, close)
- Screenshot capture (before/after)
- Execute simple actions (click/type/press/move_to/hotkey)
- Save step artifacts in a reproducible structure
- NEW (Phase 1 prep):
  - Optional action.json per step
  - Optional perceptual hashes (dHash) for state tracking
  - Random exploration mode with:
      * random (anywhere on screen)
      * random-buttons (bounded to button area derived from app_config)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pyautogui

from src.core.exceptions import (
    ActionExecutionError,
    AppCloseError,
    AppLaunchError,
    AppNotFoundError,
    CoordinatesError,
    DisplayNotFoundError,
    ScreenshotError,
)
from src.skills.schemas import StepMetadata

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Coords = Tuple[int, int]


@dataclass(frozen=True)
class StepArtifacts:
    """Paths to step artifacts on disk."""
    step_dir: Path
    before: Optional[Path]
    after: Optional[Path]
    metadata: Path
    action: Optional[Path] = None


class GUIAutomation:
    """
    Main class for GUI automation.

    Config precedence:
    - settings.yaml (base)
    - settings.yaml overrides[runtime.profile] deep-merged
    - explicit constructor args override config
    """

    def __init__(
        self,
        app_name: str,
        output_dir: Optional[str] = None,
        display: Optional[str] = None,
        settings_path: str = "config/settings.yaml",
        app_config_path: Optional[str] = None,
    ) -> None:
        self.settings_path = Path(settings_path)
        self.settings = self._load_settings(self.settings_path)

        self.app_name = app_name

        # Runtime / display
        cfg_display = self._get(self.settings, "environment.display.display_num", ":99")
        self.display = display or cfg_display

        # Output directory
        cfg_default_out = self._get(self.settings, "paths.raw_data", "data/raw")
        self.output_dir = Path(output_dir or cfg_default_out)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Coordinate system and screen geometry
        self.coordinate_system = self._get(self.settings, "automation.mouse.coordinate_system", "absolute")
        self.validate_bounds = bool(self._get(self.settings, "automation.mouse.validate_bounds", True))

        # If settings contains explicit screen size, use it; otherwise fall back.
        self.screen_width = int(self._get(self.settings, "environment.display.screen.width", 1280))
        self.screen_height = int(self._get(self.settings, "environment.display.screen.height", 1024))

        # Timing
        self.action_delay = float(self._get(self.settings, "automation.timing.action_delay", 0.5))
        self.screenshot_delay = float(self._get(self.settings, "automation.timing.screenshot_delay", 1.0))
        self.launch_delay = float(self._get(self.settings, "automation.timing.launch_delay", 2.0))

        # Screenshot naming / format
        self.screenshot_format = str(self._get(self.settings, "automation.screenshots.format", "png")).lower()
        if self.screenshot_format not in {"png", "jpg", "jpeg"}:
            raise ValueError(f"Unsupported screenshot format: {self.screenshot_format}")

        self.naming_pattern = str(
            self._get(self.settings, "automation.screenshots.naming_pattern", "step_{step_id:04d}_{suffix}.{ext}")
        )

        # Step filenames (Phase-1 compatible)
        self.step_file_before = str(self._get(self.settings, "data.step_files.before", "before.png"))
        self.step_file_after = str(self._get(self.settings, "data.step_files.after", "after.png"))
        self.step_file_metadata = str(self._get(self.settings, "data.step_files.metadata", "metadata.json"))
        # NEW: action file (optional)
        self.step_file_action = str(self._get(self.settings, "data.step_files.action", "action.json"))
        self.save_action_file = bool(self._get(self.settings, "data.step_files.save_action", True))
        # NEW: dHash (for state tracking)
        self.save_dhash = bool(self._get(self.settings, "automation.hash.save_dhash", True))
        self.dhash_size = int(self._get(self.settings, "automation.hash.dhash_size", 8))  # dHash grid size

        # Hash
        self.hash_algorithm = str(self._get(self.settings, "automation.hash.algorithm", "md5")).lower()
        self.compare_hashes = bool(self._get(self.settings, "automation.hash.compare", True))

        # Random exploration settings
        self.random_margin = int(self._get(self.settings, "automation.random.margin", 50))
        self.random_buttons_padding = int(self._get(self.settings, "automation.random_buttons.padding", 15))

        # App config (button coordinates)
        self.app_config: JsonDict = {}
        if app_config_path:
            self.app_config = self._load_yaml(Path(app_config_path))

        # process handle
        self.app_process: Optional[subprocess.Popen[Any]] = None

        self._ensure_display()
        self._configure_pyautogui()

        logger.info("GUIAutomation initialized")
        logger.info("  app_name=%s", self.app_name)
        logger.info("  display=%s", self.display)
        logger.info("  output_dir=%s", self.output_dir)
        logger.info("  coordinate_system=%s", self.coordinate_system)
        logger.info("  screenshot_format=%s", self.screenshot_format)

    # -----------------------------
    # Settings loading / helpers
    # -----------------------------
    def _load_settings(self, path: Path) -> JsonDict:
        if not path.exists():
            logger.warning("settings.yaml not found at %s; using empty settings", path)
            return {}

        data = self._load_yaml(path)

        profile = self._get(data, "runtime.profile", None)
        overrides = self._get(data, "overrides", {}) if isinstance(data, dict) else {}
        if profile and isinstance(overrides, dict) and profile in overrides:
            base = dict(data)
            merged = self._deep_merge(base, overrides[profile])
            return merged

        return data

    @staticmethod
    def _deep_merge(base: JsonDict, override: JsonDict) -> JsonDict:
        """Deep-merge override into base (dicts only). Does not mutate inputs."""
        result = dict(base)
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = GUIAutomation._deep_merge(result[k], v)  # type: ignore[arg-type]
            else:
                result[k] = v
        return result

    @staticmethod
    def _get(dct: JsonDict, dotted_path: str, default: Any) -> Any:
        cur: Any = dct
        for part in dotted_path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @staticmethod
    def _load_yaml(path: Path) -> JsonDict:
        try:
            import yaml  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("PyYAML is required to load YAML configs. Install pyyaml.") from e

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data or {}

    # -----------------------------
    # Environment / display
    # -----------------------------
    def _ensure_display(self) -> None:
        os.environ["DISPLAY"] = self.display

        try:
            w, h = pyautogui.size()
            logger.info("Display reachable: %sx%s (DISPLAY=%s)", w, h, self.display)

            # Prefer actual runtime geometry if available
            self.screen_width = int(w)
            self.screen_height = int(h)
        except Exception as e:
            raise DisplayNotFoundError(self.display) from e

    def _configure_pyautogui(self) -> None:
        pyautogui.FAILSAFE = bool(self._get(self.settings, "automation.pyautogui.failsafe", True))
        pyautogui.PAUSE = float(self._get(self.settings, "automation.pyautogui.pause", 0.5))

    # -----------------------------
    # App lifecycle
    # -----------------------------
    def launch_app(self) -> bool:
        """Launch the application and wait for it to become ready. Returns True on success."""
        logger.info("Launching %s...", self.app_name)

        try:
            which_result = subprocess.run(["which", self.app_name], capture_output=True, text=True)
            if which_result.returncode != 0:
                raise AppNotFoundError(self.app_name)

            self.app_process = subprocess.Popen(
                [self.app_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )

            time.sleep(self.launch_delay)

            if self.app_process.poll() is not None:
                raise AppLaunchError(self.app_name, "Process exited immediately after launch")

            logger.info("%s launched (PID=%s)", self.app_name, self.app_process.pid)
            return True

        except AppNotFoundError:
            raise
        except Exception as e:
            raise AppLaunchError(self.app_name, str(e)) from e

    def close_app(self) -> bool:
        """Terminate the application gracefully, falling back to SIGKILL if needed. Returns True on success."""
        if self.app_process is None:
            logger.warning("close_app called but no process exists")
            return False

        logger.info("Closing %s...", self.app_name)

        try:
            self.app_process.terminate()

            graceful_timeout = float(self._get(self.settings, f"app.{self.app_name}.close.graceful_timeout", 5))
            end = time.monotonic() + graceful_timeout
            while time.monotonic() < end:
                if self.app_process.poll() is not None:
                    logger.info("Application closed gracefully")
                    self.app_process = None
                    return True
                time.sleep(float(self._get(self.settings, "automation.timing.poll_interval", 0.1)))

            force_kill = bool(self._get(self.settings, f"app.{self.app_name}.close.force_kill", True))
            if force_kill:
                logger.warning("Application did not close gracefully; killing...")
                self.app_process.kill()
                kill_wait = float(self._get(self.settings, "automation.timing.kill_wait", 2.0))
                self.app_process.wait(timeout=kill_wait)
                logger.info("Application killed")
                self.app_process = None
                return True

            raise AppCloseError(self.app_name, "Graceful close timed out and force_kill=false")

        except Exception as e:
            raise AppCloseError(self.app_name, str(e)) from e

    # -----------------------------
    # Screenshots / hashing
    # -----------------------------
    def take_screenshot(self, path: Union[str, Path]) -> Path:
        """
        Take a screenshot and save to the specified path.

        If path is relative, it is interpreted relative to project root,
        NOT re-prefixed with output_dir again.
        """
        try:
            out_path = Path(path)

            if out_path.suffix == "":
                out_path = out_path.with_suffix(f".{self.screenshot_format}")

            out_path.parent.mkdir(parents=True, exist_ok=True)

            img = pyautogui.screenshot()

            pil_format = "PNG" if self.screenshot_format == "png" else "JPEG"
            save_kwargs: Dict[str, Any] = {}

            if pil_format == "JPEG":
                quality = self._get(self.settings, "automation.screenshots.quality", 95)
                save_kwargs["quality"] = int(quality)

            img.save(out_path, format=pil_format, **save_kwargs)
            return out_path

        except Exception as e:
            raise ScreenshotError(str(e)) from e

    def compute_image_hash(self, image_path: Path) -> str:
        """Return a cryptographic hash (configured algorithm) of the image file as a hex string."""
        algo = self.hash_algorithm
        h = hashlib.new(algo)
        with image_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def compute_dhash(self, image_path: Path, size: int = 8) -> str:
        """
        Compute a simple perceptual hash (dHash) for state tracking.
        Returns hex string.
        """
        try:
            from PIL import Image  # Pillow is a dependency via pyautogui
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Pillow is required for perceptual hashing.") from e

        with Image.open(image_path) as raw:
            img = raw.convert("L").resize((size + 1, size))
        pixels = list(img.tobytes())  # L-mode: one byte per pixel
        # rows of length (size+1)
        diff_bits: List[int] = []
        for y in range(size):
            row_start = y * (size + 1)
            for x in range(size):
                left = pixels[row_start + x]
                right = pixels[row_start + x + 1]
                diff_bits.append(1 if left > right else 0)

        # pack bits into hex
        # 64 bits if size=8
        value = 0
        for b in diff_bits:
            value = (value << 1) | b
        width = (size * size + 3) // 4  # hex digits
        return f"{value:0{width}x}"

    @staticmethod
    def hamming_distance(hash1: Optional[str], hash2: Optional[str]) -> Optional[int]:
        """
        Compute the Hamming distance between two dHash hex strings.
        Returns None if either hash is missing or invalid.
        """
        if not hash1 or not hash2:
            return None
        try:
            return bin(int(hash1, 16) ^ int(hash2, 16)).count("1")
        except ValueError:
            return None

    # -----------------------------
    # Coordinates utilities
    # -----------------------------
    def _to_absolute_coords(self, coords: Union[Coords, Tuple[float, float]]) -> Coords:
        """
        Convert coordinates to absolute pixels depending on coordinate_system.
        - If coordinate_system == "absolute": expects ints
        - If coordinate_system == "normalized": expects floats in [0,1]
        """
        if self.coordinate_system == "absolute":
            x, y = coords
            return int(x), int(y)

        x_f, y_f = coords
        x = int(round(float(x_f) * (self.screen_width - 1)))
        y = int(round(float(y_f) * (self.screen_height - 1)))
        return x, y

    def _validate_coords(self, x: int, y: int) -> None:
        """Raise CoordinatesError if (x, y) are out of screen bounds (when validate_bounds is enabled)."""
        if not self.validate_bounds:
            return
        if x < 0 or y < 0:
            raise CoordinatesError(x, y, "negative coordinates")
        if x >= self.screen_width or y >= self.screen_height:
            raise CoordinatesError(x, y, f"outside screen bounds ({self.screen_width}x{self.screen_height})")

    @staticmethod
    def _normalize_coords(coords: Any) -> Union[Coords, Tuple[float, float]]:
        """Accept (x,y) tuple, [x,y] list, or {"x":x,"y":y} dict and return a (x, y) pair."""
        if isinstance(coords, tuple) and len(coords) == 2:
            return coords[0], coords[1]
        if isinstance(coords, list) and len(coords) == 2:
            return coords[0], coords[1]
        if isinstance(coords, dict) and "x" in coords and "y" in coords:
            return coords["x"], coords["y"]
        raise ValueError(f"Unsupported coordinates format: {coords!r}")

    # -----------------------------
    # Actions
    # -----------------------------
    def perform_action(self, action_config: JsonDict) -> None:
        """
        Execute an action.

        Preferred schema:
          {
            "action_type": "click",
            "coordinates": [x, y] | (x, y) | {"x":x,"y":y},
            "parameters": {"button": "left", "clicks": 1}
          }

        Backward-compatible fields:
          - button, clicks at top-level
          - text/key/keys
        """
        action_type = str(action_config.get("action_type", "click"))
        params = dict(action_config.get("parameters", {}))

        # Backward compat
        if "button" in action_config and "button" not in params:
            params["button"] = action_config["button"]
        if "clicks" in action_config and "clicks" not in params:
            params["clicks"] = action_config["clicks"]
        if "text" in action_config and "text" not in params:
            params["text"] = action_config["text"]
        if "key" in action_config and "key" not in params:
            params["key"] = action_config["key"]
        if "keys" in action_config and "keys" not in params:
            params["keys"] = action_config["keys"]
        if "duration" in action_config and "duration" not in params:
            params["duration"] = action_config["duration"]

        coords_any = action_config.get("coordinates")
        coords: Optional[Coords] = None
        if coords_any is not None:
            norm = self._normalize_coords(coords_any)
            coords = self._to_absolute_coords(norm)
            self._validate_coords(coords[0], coords[1])

        logger.info("Executing action: %s (coords=%s)", action_type, coords)

        try:
            if action_type == "click":
                button = str(params.get("button", "left"))
                clicks = int(params.get("clicks", 1))
                if coords:
                    pyautogui.click(x=coords[0], y=coords[1], button=button, clicks=clicks)
                else:
                    pyautogui.click(button=button, clicks=clicks)

            elif action_type == "double_click":
                button = str(params.get("button", "left"))
                if coords:
                    pyautogui.doubleClick(x=coords[0], y=coords[1], button=button)
                else:
                    pyautogui.doubleClick(button=button)

            elif action_type == "right_click":
                if coords:
                    pyautogui.rightClick(x=coords[0], y=coords[1])
                else:
                    pyautogui.rightClick()

            elif action_type == "move_to":
                if coords is None:
                    raise ActionExecutionError(action_type, "move_to requires coordinates")
                duration = float(params.get("duration", self._get(self.settings, "automation.timing.move_duration", 0.5)))
                pyautogui.moveTo(coords[0], coords[1], duration=duration)

            elif action_type == "type":
                text = str(params.get("text", ""))
                interval = float(self._get(self.settings, "automation.timing.type_delay", 0.1))
                pyautogui.write(text, interval=interval)

            elif action_type == "press":
                key = str(params.get("key", "enter"))
                pyautogui.press(key)

            elif action_type == "hotkey":
                keys = params.get("keys", [])
                if not isinstance(keys, list) or not keys:
                    raise ActionExecutionError(action_type, "hotkey requires parameters.keys as non-empty list")
                pyautogui.hotkey(*[str(k) for k in keys])

            else:
                raise ActionExecutionError(action_type, f"Unsupported action_type: {action_type}")

            time.sleep(self.screenshot_delay)

        except CoordinatesError:
            raise
        except ActionExecutionError:
            raise
        except Exception as e:
            raise ActionExecutionError(action_type, str(e)) from e

    # -----------------------------
    # Step execution
    # -----------------------------
    def run_step(
        self,
        step_id: int,
        action_config: JsonDict,
        take_before: bool = True,
        take_after: bool = True,
    ) -> StepArtifacts:
        """
        Run a single step:
          - create step directory
          - optionally take before screenshot
          - write action.json (optional)
          - perform action
          - optionally take after screenshot
          - write metadata.json
        """
        step_dir = self.output_dir / f"step_{step_id:04d}"
        step_dir.mkdir(parents=True, exist_ok=True)

        before_path = step_dir / self.step_file_before if take_before else None
        after_path = step_dir / self.step_file_after if take_after else None
        meta_path = step_dir / self.step_file_metadata

        action_path = step_dir / self.step_file_action if self.save_action_file else None

        before_hash = None
        after_hash = None
        before_dhash = None
        after_dhash = None

        if before_path:
            self.take_screenshot(before_path)
            before_hash = self.compute_image_hash(before_path)
            if self.save_dhash:
                before_dhash = self.compute_dhash(before_path, size=self.dhash_size)

        if action_path is not None:
            action_path.write_text(json.dumps(action_config, ensure_ascii=False, indent=2), encoding="utf-8")

        self.perform_action(action_config)

        if after_path:
            self.take_screenshot(after_path)
            after_hash = self.compute_image_hash(after_path)
            if self.save_dhash:
                after_dhash = self.compute_dhash(after_path, size=self.dhash_size)

        changed = None
        if self.compare_hashes and before_hash and after_hash:
            changed = before_hash != after_hash

        metadata = {
            "step_id": step_id,
            "timestamp": time.time(),
            "app": self.app_name,
            "display": self.display,
            "screen": {"width": self.screen_width, "height": self.screen_height},
            "action": action_config,
            "hashes": {"before": before_hash, "after": after_hash},
            "dhashes": {
                "before": before_dhash,
                "after": after_dhash,
                "hamming_distance": self.hamming_distance(before_dhash, after_dhash),
            },
            "changed": changed,
            "timing": {"action_delay": self.action_delay, "screenshot_delay": self.screenshot_delay},
        }

        meta_path.write_text(
            json.dumps(
                StepMetadata.model_validate(metadata).model_dump(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Step %s completed (changed=%s) -> %s", step_id, changed, step_dir)

        return StepArtifacts(step_dir=step_dir, before=before_path, after=after_path, metadata=meta_path, action=action_path)

    def run_sequence(self, actions: List[JsonDict], start_id: int = 0) -> List[StepArtifacts]:
        """Run multiple actions as consecutive steps, pausing action_delay between each."""
        out: List[StepArtifacts] = []
        for i, action in enumerate(actions):
            out.append(self.run_step(start_id + i, action))
            if i < len(actions) - 1:
                time.sleep(self.action_delay)
        return out

    # -----------------------------
    # App config helpers
    # -----------------------------
    def get_button_coordinates(self, button_name: str) -> Optional[Coords]:
        """Return absolute (x, y) for a named button from app_config, or None if not found."""
        buttons = self.app_config.get("buttons", {}) if isinstance(self.app_config, dict) else {}
        coords = buttons.get(button_name)
        if coords is None:
            return None
        if isinstance(coords, list) and len(coords) == 2:
            return int(coords[0]), int(coords[1])
        if isinstance(coords, tuple) and len(coords) == 2:
            return int(coords[0]), int(coords[1])
        return None

    def click_button(self, button_name: str, clicks: int = 1) -> bool:
        """Click a named button from app_config. Returns False if the button has no configured coordinates."""
        coords = self.get_button_coordinates(button_name)
        if not coords:
            return False
        self.perform_action(
            {
                "action_type": "click",
                "coordinates": coords,
                "parameters": {"button": "left", "clicks": clicks},
            }
        )
        return True

    def _button_area_bbox(self) -> Optional[Tuple[int, int, int, int]]:
        """
        Compute a bounding box that covers all configured button coordinates.
        Returns (x_min, y_min, x_max, y_max) in absolute pixels, padded.
        """
        if not isinstance(self.app_config, dict):
            return None
        buttons = self.app_config.get("buttons")
        if not isinstance(buttons, dict) or not buttons:
            return None

        xs: List[int] = []
        ys: List[int] = []
        for _, v in buttons.items():
            if isinstance(v, (list, tuple)) and len(v) == 2:
                try:
                    xs.append(int(v[0]))
                    ys.append(int(v[1]))
                except (TypeError, ValueError):
                    continue

        if not xs or not ys:
            return None

        pad = int(self.random_buttons_padding)
        x_min = max(0, min(xs) - pad)
        y_min = max(0, min(ys) - pad)
        x_max = min(self.screen_width - 1, max(xs) + pad)
        y_max = min(self.screen_height - 1, max(ys) + pad)
        if x_min >= x_max or y_min >= y_max:
            return None
        return x_min, y_min, x_max, y_max

    # -----------------------------
    # Context manager
    # -----------------------------
    def __enter__(self) -> "GUIAutomation":
        self.launch_app()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        try:
            self.close_app()
        except Exception:
            logger.exception("Failed to close app during context manager exit")


# =========================================================
# CLI
# =========================================================
def _cli() -> int:
    parser = argparse.ArgumentParser(description="GUI Automation Tool (Phase 0.3 -> Phase 1 prep)")

    # Core config
    parser.add_argument("--app", default="gnome-calculator", help="Application to automate")
    parser.add_argument("--settings", default="config/settings.yaml", help="Path to settings.yaml")

    # App config (button coordinates)
    parser.add_argument("--app-config", default=None, help="Path to app config YAML (button coordinates)")
    parser.add_argument("--config", dest="app_config", default=None, help=argparse.SUPPRESS)  # backward-compat

    parser.add_argument("--output", default=None, help="Output directory (overrides settings)")
    parser.add_argument("--display", default=None, help="X11 display (overrides settings)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--seed", type=int, default=None, help="Random seed (optional)")

    # Modes
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--random", action="store_true", help="Run random click steps on the whole screen")
    mode.add_argument(
        "--random-buttons",
        action="store_true",
        help="Run random click steps bounded to button area derived from app-config",
    )

    # Common params
    parser.add_argument(
        "--steps",
        type=int,
        default=20,
        help="Number of steps for random modes (default: 20)",
    )
    parser.add_argument("--start-step-id", type=int, default=0, help="Start step_id offset")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if args.seed is not None:
        random.seed(args.seed)

    # Resolve app_config from either flag
    app_config_path = args.app_config or getattr(args, "app_config", None)

    # Default mode: random-buttons if app-config provided, else random
    if not args.random and not args.random_buttons:
        if app_config_path:
            args.random_buttons = True
        else:
            args.random = True

    with GUIAutomation(
        app_name=args.app,
        output_dir=args.output,
        settings_path=args.settings,
        app_config_path=app_config_path,
        display=args.display,
    ) as auto:
        # Random modes
        width, height = pyautogui.size()

        if args.random_buttons:
            if not app_config_path:
                logger.error("--random-buttons requires --app-config with button coordinates.")
                return 2
            bbox = auto._button_area_bbox()
            if bbox is None:
                logger.error("Cannot derive button area bbox from app-config. Ensure 'buttons:' coords exist.")
                return 2
            x_min, y_min, x_max, y_max = bbox
            logger.info("Random-buttons bbox: (%d,%d) - (%d,%d)", x_min, y_min, x_max, y_max)

            for i in range(args.steps):
                x = random.randint(x_min, x_max)
                y = random.randint(y_min, y_max)
                auto.run_step(
                    step_id=args.start_step_id + i,
                    action_config={
                        "action_type": "click",
                        "coordinates": (x, y),
                        "parameters": {"button": "left", "clicks": 1},
                    },
                )
                if i < args.steps - 1:
                    time.sleep(auto.action_delay)
            return 0

        # args.random (whole screen)
        margin = int(auto.random_margin)
        x_lo = max(0, margin)
        y_lo = max(0, margin)
        x_hi = max(x_lo, width - 1 - margin)
        y_hi = max(y_lo, height - 1 - margin)

        for i in range(args.steps):
            x = random.randint(x_lo, x_hi)
            y = random.randint(y_lo, y_hi)
            auto.run_step(
                step_id=args.start_step_id + i,
                action_config={
                    "action_type": "click",
                    "coordinates": (x, y),
                    "parameters": {"button": "left", "clicks": 1},
                },
            )
            if i < args.steps - 1:
                time.sleep(auto.action_delay)

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
