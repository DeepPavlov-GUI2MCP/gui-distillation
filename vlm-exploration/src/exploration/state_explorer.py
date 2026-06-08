"""
UI State Exploration Pipeline v2 (VLM-driven, single-call).

One VLM call per step: describe screen + decide where to click.
VLM determines state identity (is_new_screen) instead of label matching.

Usage:
    python -m src.exploration.state_explorer \
        --max-steps 30 \
        --teacher-config config/teachers/gpt54mini_proxy.yaml \
        --prompt explore_step_v2.md \
        --output data/exploration/state_graphs/calc_v2 \
        --display :99 -v
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from src.core.automation import GUIAutomation
from src.teachers.openai_client import OpenAIAnnotatorClient, StructuredResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schema — single VLM call
# ---------------------------------------------------------------------------

ElementType = Literal[
    "button", "input", "dropdown", "toggle", "radio",
    "menu_item", "label", "link", "other",
]


class UIElementInfo(BaseModel):
    """Rich description of a single UI element."""
    name: str = Field(description="Visible label or accessible name")
    type: ElementType = Field(description="Element type")
    value: Optional[str] = Field(
        None,
        description="Current value: display text, input content, "
                    "selected dropdown item, toggle on/off",
    )
    enabled: bool = Field(True, description="False if greyed out / non-interactive")


class ExploreStep(BaseModel):
    # Stage 1: Observe
    screen_label: str = Field(description="Unique short name for this screen state")
    elements: List[UIElementInfo] = Field(description="ALL visible UI elements with types and current values")
    description: str = Field(description="1-2 sentences about available functionality")
    is_new_screen: bool = Field(description="True if this screen differs from all previously discovered screens")

    # Stage 2: Reason (BEFORE deciding where to click)
    reasoning: str = Field(
        description="Think step by step: (1) what areas/dialogs/menus have been explored so far, "
                    "(2) what is still unexplored or unclicked, "
                    "(3) are there any open popups/dialogs to close first, "
                    "(4) is there anything left to discover — if nothing, set exploration_done=true"
    )
    exploration_done: bool = Field(description="True ONLY when reasoning concludes nothing is left to explore")

    # Stage 3: Act (only if exploration_done is false)
    click_x: int = Field(description="Absolute pixel X coordinate on 1280x1024 screen")
    click_y: int = Field(description="Absolute pixel Y coordinate on 1280x1024 screen")
    target_name: str = Field(description="Name of element to click")


# ---------------------------------------------------------------------------
# Explorer
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    return Path(f"config/prompts/{name}").read_text(encoding="utf-8")


class StateExplorer:
    def __init__(
        self,
        automation: GUIAutomation,
        vlm_client: OpenAIAnnotatorClient,
        display: str,
        output_dir: Path,
        prompt_file: str = "explore_step_v3.md",
    ):
        self.automation = automation
        self.vlm = vlm_client
        self.display = display
        self.output_dir = output_dir
        self.prompt_file = prompt_file
        self._step = 0

        # Discovered data
        self.screens: Dict[str, dict] = {}  # label -> {description, elements, buttons, screenshot}
        self.transitions: List[dict] = []
        self.explored_actions: List[str] = []  # "clicked X from Y"

        # Token usage accumulator
        self._tokens_input = 0
        self._tokens_output = 0

        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "screenshots").mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------

    def _screenshot(self, name: str) -> Path:
        """Take fullscreen screenshot via scrot."""
        path = self.output_dir / "screenshots" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "DISPLAY": self.display}
        subprocess.run(["scrot", "-o", str(path)], env=env, check=False, timeout=10)
        return path

    def _click(self, x: int, y: int) -> None:
        """Click at absolute pixel coordinates."""
        env = {**os.environ, "DISPLAY": self.display}
        subprocess.run(["xdotool", "mousemove", str(x), str(y)], env=env, check=False, timeout=5)
        time.sleep(0.1)
        subprocess.run(["xdotool", "click", "1"], env=env, check=False, timeout=5)
        time.sleep(1.0)
        logger.info("Clicked (%d,%d)", x, y)

    def _press_escape(self) -> None:
        env = {**os.environ, "DISPLAY": self.display}
        subprocess.run(["xdotool", "key", "Escape"], env=env, check=False, timeout=5)
        time.sleep(0.5)

    def _ensure_app_running(self) -> None:
        """Check the target app is running (must be pre-launched)."""
        app_name = self.automation.app_name
        r = subprocess.run(["pgrep", "-f", app_name], capture_output=True, timeout=5)
        if r.returncode != 0:
            raise RuntimeError(
                f"{app_name} not running! "
                f"Launch it before the pipeline: DISPLAY={self.display} {app_name} &"
            )
        logger.info("%s running (PID: %s)", app_name, r.stdout.decode().strip().split()[0])

    # ------------------------------------------------------------------
    # VLM call
    # ------------------------------------------------------------------

    def _explore_step(self, screenshot_path: Path) -> ExploreStep:
        """Single VLM call: describe screen + decide next action."""
        discovered = "\n".join(
            f"- {label}: {info['description']} [{len(info['elements'])} elements]"
            for label, info in self.screens.items()
        ) or "(none yet)"

        explored = "\n".join(
            f"- {a}" for a in self.explored_actions[-30:]
        ) or "(none yet)"

        prompt = _load_prompt(self.prompt_file).format(
            app_name=self.automation.app_name,
            screen_width=getattr(self.automation, "screen_width", 1280),
            screen_height=getattr(self.automation, "screen_height", 1024),
            discovered_screens=discovered,
            explored_actions=explored,
        )

        result: StructuredResult = self.vlm.infer_structured_with_usage(
            prompt,
            response_model=ExploreStep,
            image_paths=[screenshot_path],
        )

        # Accumulate token usage
        if result.usage:
            self._tokens_input += result.usage.get("prompt_tokens", 0) or result.usage.get("input_tokens", 0) or 0
            self._tokens_output += result.usage.get("completion_tokens", 0) or result.usage.get("output_tokens", 0) or 0

        return result.parsed

    # ------------------------------------------------------------------
    # State recording
    # ------------------------------------------------------------------

    @staticmethod
    def _elements_to_dict(elements: List[UIElementInfo]) -> Dict[Tuple[str, str], UIElementInfo]:
        """Index elements by (name, type) for merge operations."""
        result: Dict[Tuple[str, str], UIElementInfo] = {}
        for el in elements:
            key = (el.name, el.type)
            result[key] = el  # last-wins if duplicate (name, type)
        return result

    @staticmethod
    def _elements_to_buttons(elements: List[UIElementInfo]) -> List[str]:
        """Backward-compat: extract sorted unique names of interactive elements."""
        interactive = {"button", "input", "dropdown", "toggle", "radio", "menu_item", "link"}
        return sorted({el.name for el in elements if el.type in interactive})

    def _record_screen(self, step: ExploreStep, screenshot_path: Path) -> bool:
        """Record screen if new. Returns True if new."""
        label = step.screen_label

        if not step.is_new_screen and label in self.screens:
            # VLM says not new + label exists → merge elements by (name, type)
            existing = self._elements_to_dict(
                [UIElementInfo(**e) for e in self.screens[label]["elements"]]
            )
            incoming = self._elements_to_dict(step.elements)
            merged = {**existing, **incoming}  # incoming updates values
            if len(merged) > len(existing):
                new_count = len(merged) - len(existing)
                elements_list = sorted(merged.values(), key=lambda e: (e.type, e.name))
                self.screens[label]["elements"] = [e.model_dump() for e in elements_list]
                self.screens[label]["buttons"] = self._elements_to_buttons(elements_list)
                logger.info("  Updated '%s' with %d new elements", label, new_count)
            return False

        if label in self.screens and not step.is_new_screen:
            return False

        # New screen
        elements_sorted = sorted(step.elements, key=lambda e: (e.type, e.name))
        self.screens[label] = {
            "description": step.description,
            "elements": [e.model_dump() for e in elements_sorted],
            "buttons": self._elements_to_buttons(elements_sorted),
            "screenshot": str(screenshot_path.relative_to(self.output_dir)),
        }
        logger.info("  NEW screen '%s': %d elements", label, len(step.elements))
        return True

    # ------------------------------------------------------------------
    # Step persistence
    # ------------------------------------------------------------------

    def _save_step(self, step_num: int, step: ExploreStep) -> None:
        """Save individual step JSON to disk."""
        steps_dir = self.output_dir / "steps"
        steps_dir.mkdir(exist_ok=True)
        path = steps_dir / f"step_{step_num:03d}.json"
        path.write_text(
            json.dumps(step.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def explore(self, max_steps: int = 30) -> dict:
        # Calculator must be pre-launched by Makefile/shell script
        self._ensure_app_running()

        # pending_transition: we clicked something last step, need to see where we landed
        pending_from: Optional[str] = None
        pending_action: Optional[str] = None
        pending_click: Optional[List[int]] = None

        for step_num in range(max_steps):
            self._step = step_num + 1

            # 1. Screenshot
            ss_path = self._screenshot(f"step_{step_num:03d}")

            # 2. Single VLM call
            try:
                step = self._explore_step(ss_path)
            except Exception as e:
                logger.error("Step %d VLM failed: %s", self._step, e)
                self._press_escape()
                continue

            # 3. Record screen
            self._record_screen(step, ss_path)

            # 4. Complete pending transition (from previous step's click)
            if pending_from is not None:
                self.transitions.append({
                    "from": pending_from,
                    "action": pending_action,
                    "click": pending_click,
                    "to": step.screen_label,
                })
                pending_from = None

            # 5. Save step to disk
            self._save_step(step_num, step)

            # 6. Done?
            if step.exploration_done:
                logger.info("Step %d: exploration complete. Reasoning: %s", self._step, step.reasoning[:200])
                break

            # 7. Log and click
            logger.info(
                "Step %d [%s]: click '%s' (%d,%d)",
                self._step, step.screen_label,
                step.target_name, step.click_x, step.click_y,
            )
            logger.debug("  Reasoning: %s", step.reasoning[:300])

            self._click(step.click_x, step.click_y)

            action_desc = f"clicked '{step.target_name}' from '{step.screen_label}'"
            self.explored_actions.append(action_desc)

            # Remember for next step to fill "to"
            pending_from = step.screen_label
            pending_action = step.target_name
            pending_click = [step.click_x, step.click_y]

        return self._save_graph()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_graph(self) -> dict:
        # Build paths via BFS
        paths: Dict[str, List[str]] = {}
        for label in self.screens:
            path = self._find_path(label)
            paths[label] = path if path is not None else []

        # Deduplicate transitions
        seen = set()
        unique_transitions = []
        for t in self.transitions:
            key = (t.get("from", ""), t.get("action", ""), t.get("to", ""))
            if key not in seen:
                seen.add(key)
                unique_transitions.append(t)

        graph: Dict[str, Any] = {
            "app": self.automation.app_name,
            "exploration_date": time.strftime("%Y-%m-%d"),
            "model": getattr(self.vlm, "model", None),
            "total_steps": self._step,
            "total_screens": len(self.screens),
            "total_transitions": len(unique_transitions),
            "usage": {
                "tokens_input": self._tokens_input,
                "tokens_output": self._tokens_output,
                "tokens_total": self._tokens_input + self._tokens_output,
            },
            "screens": {},
            "transitions": unique_transitions,
        }

        for label, info in self.screens.items():
            graph["screens"][label] = {
                **info,
                "path_from_root": paths.get(label, []),
            }

        # Compute metrics (dedup available offline via CLI: python -m src.exploration.metrics --dedup)
        from src.exploration.metrics import compute_metrics
        graph["metrics"] = compute_metrics(graph)

        output_path = self.output_dir / "state_graph.json"
        output_path.write_text(
            json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("State graph saved to %s", output_path)
        logger.info(
            "Metrics: discovery_rate=%.2f elements_per_step=%.1f elements_per_1k_tokens=%s",
            graph["metrics"]["discovery_rate"],
            graph["metrics"]["elements_per_step"],
            graph["metrics"].get("elements_per_1k_tokens", "n/a"),
        )
        return graph

    def _find_path(self, to_label: str) -> Optional[List[str]]:
        """BFS from first discovered screen to target."""
        if not self.screens:
            return None
        root = next(iter(self.screens))
        if root == to_label:
            return []

        adj: Dict[str, List[tuple]] = {}
        for t in self.transitions:
            f = t.get("from", "")
            to = t.get("to", "")
            action = t.get("action", "")
            if f and to:
                adj.setdefault(f, []).append((action, to))

        from collections import deque
        queue = deque([(root, [])])
        visited = {root}
        while queue:
            current, path = queue.popleft()
            for action, next_label in adj.get(current, []):
                if next_label == to_label:
                    return path + [action]
                if next_label not in visited:
                    visited.add(next_label)
                    queue.append((next_label, path + [action]))
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser("state_explorer")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--teacher-config", default="config/teachers/gpt54mini_proxy.yaml")
    parser.add_argument("--settings", default="config/settings.yaml")
    parser.add_argument("--app-name", default="gnome-calculator", help="Process name of the target application")
    parser.add_argument("--app-config", default="config/apps/calculator.yaml")
    parser.add_argument("--prompt", default="explore_step_v3.md")
    parser.add_argument("--output", required=True)
    parser.add_argument("--display", default=":99")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    os.environ["DISPLAY"] = args.display
    os.environ["XAUTHORITY"] = os.path.expanduser("~/.Xauthority")

    vlm_client = OpenAIAnnotatorClient(
        settings_path=args.settings,
        teacher_config_path=args.teacher_config,
    )

    automation = GUIAutomation(
        app_name=args.app_name,
        settings_path=args.settings,
        app_config_path=args.app_config,
        output_dir=args.output,
        display=args.display,
    )

    explorer = StateExplorer(
        automation=automation,
        vlm_client=vlm_client,
        display=args.display,
        output_dir=Path(args.output),
        prompt_file=args.prompt,
    )

    graph = explorer.explore(max_steps=args.max_steps)
    print(
        f"\nDone: {graph['total_screens']} screens, "
        f"{graph['total_transitions']} transitions, "
        f"{graph['total_steps']} steps",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
