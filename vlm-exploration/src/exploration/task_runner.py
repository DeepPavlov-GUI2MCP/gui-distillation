"""
Task-driven online agent loop for GUI automation.

Two modes:
  - task mode (default): execute a single concrete natural-language task.
  - exploration mode (--exploration): autonomously plan and execute diverse
    sub-goals until max_steps is reached or the LLM signals task_complete.

In each step the agent:
  1. Takes a screenshot of the current UI state.
  2. Sends screenshot + context + history to an LLM.
  3. Parses the LLM response to get the next button to click.
  4. Executes the action and records all artifacts via GUIAutomation.run_step().
  5. Repeats until done.

Usage (task mode):
    python -m src.exploration.task_runner \\
        --task "add 3 and 5, then subtract 2" \\
        --max-steps 20 \\
        --settings config/settings.yaml \\
        --app-config config/apps/calculator.yaml \\
        --teacher-config config/teachers/openai_gpt.yaml \\
        --output data/exploration/task_runs/run_001

Usage (exploration mode):
    python -m src.exploration.task_runner \\
        --exploration \\
        --task "Explore the calculator by solving diverse calculations" \\
        --max-steps 50 \\
        --settings config/settings.yaml \\
        --app-config config/apps/calculator.yaml \\
        --teacher-config config/teachers/openai_gpt.yaml \\
        --output data/exploration/task_runs/explore_001
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from src.core.automation import GUIAutomation
from src.core.exceptions import ActionExecutionError
from src.teachers.json_parser import RobustJSONParser
from src.teachers.openai_client import OpenAIAnnotatorClient
from src.teachers.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]

_DONE_SENTINEL = "__done__"
_NO_GOAL = "(none yet — choose the first goal)"
_NO_COMPLETED = "(none yet)"


class TaskRunner:
    """
    Online LLM-driven agent that executes a task on a running GUI application.

    Supports two modes controlled by the `exploration` flag in run():
      - task mode:        single concrete task, stops when LLM sets task_complete.
      - exploration mode: LLM autonomously plans sub-goals; history resets between
                          goals so context stays focused; runs until max_steps.
    """

    def __init__(
        self,
        automation: GUIAutomation,
        llm_client: OpenAIAnnotatorClient,
        prompt_template: str,
        max_steps: int = 20,
    ) -> None:
        self.automation = automation
        self.llm_client = llm_client
        self.prompt_template = prompt_template
        self.max_steps = max_steps
        self._parser = RobustJSONParser()
        self._buttons: Dict[str, List[int]] = dict(
            automation.app_config.get("buttons", {})
        )

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _button_list_text(self) -> str:
        # IDs only — coordinates are looked up internally.
        return ", ".join(self._buttons.keys())

    def _history_text(self, history: List[JsonDict]) -> str:
        if not history:
            return "  (no actions yet)"
        lines = []
        for i, entry in enumerate(history):
            if entry.get("error"):
                lines.append(f"  Step {i}: tried '{entry['button_id']}' — ERROR: {entry['error']}")
            else:
                lines.append(f"  Step {i}: clicked {entry['button_id']} — {entry['rationale']}")
        return "\n".join(lines)

    def _completed_goals_text(self, completed_goals: List[str]) -> str:
        if not completed_goals:
            return _NO_COMPLETED
        return "\n".join(f"  {i + 1}. {g}" for i, g in enumerate(completed_goals))

    def _build_prompt(
        self,
        task: str,
        history: List[JsonDict],
        completed_goals: List[str] | None = None,
        current_goal: str = "",
    ) -> str:
        return self.prompt_template.format(
            task=task,
            button_list=self._button_list_text(),
            history=self._history_text(history),
            completed_goals=self._completed_goals_text(completed_goals or []),
            current_goal=current_goal or _NO_GOAL,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, task: str, exploration: bool = False) -> JsonDict:
        """
        Execute the task (or free exploration) using the already-running app.
        The caller manages app lifecycle via GUIAutomation context manager.

        Returns a report dict also saved to {output_dir}/task_run_report.json.
        """
        # history holds steps for the *current* goal (reset on goal completion
        # in exploration mode to keep the prompt focused).
        history: List[JsonDict] = []
        step_reports: List[JsonDict] = []
        completed_goals: List[str] = []
        current_goal: str = ""
        task_complete = False

        for step_id in range(self.max_steps):
            logger.info("--- Step %d (goals done: %d) ---", step_id, len(completed_goals))

            # 1. Screenshot for LLM
            tmp_screenshot = self.automation.output_dir / f"_llm_input_{step_id:04d}.png"
            self.automation.take_screenshot(tmp_screenshot)

            # 2. Build prompt and call LLM
            prompt = self._build_prompt(task, history, completed_goals, current_goal)
            t0 = time.perf_counter()
            raw = self.llm_client.infer(prompt, image_paths=[tmp_screenshot])
            latency_s = time.perf_counter() - t0
            logger.debug("LLM (%.2fs): %s", latency_s, raw.text[:200])

            # 3. Parse response
            parse_result = self._parser.parse(raw.text)
            if not parse_result.ok or not isinstance(parse_result.data, dict):
                logger.warning("Step %d: parse failed (%s): %s", step_id, parse_result.mode, parse_result.error)
                step_reports.append({
                    "step_id": step_id,
                    "error": f"parse_failed: {parse_result.error}",
                    "raw_response": raw.text[:500],
                    "latency_s": round(latency_s, 3),
                    "usage": raw.usage,
                })
                tmp_screenshot.unlink(missing_ok=True)
                continue

            data: JsonDict = parse_result.data  # type: ignore[assignment]
            button_id: str = str(data.get("button_id", "")).strip()
            rationale: str = str(data.get("rationale", ""))
            task_complete = bool(data.get("task_complete", False))
            goal_complete: bool = bool(data.get("goal_complete", False))

            # In exploration mode the LLM owns current_goal; update it each step.
            if exploration:
                new_goal = str(data.get("current_goal", "")).strip()
                if new_goal:
                    current_goal = new_goal

            report_entry: JsonDict = {
                "step_id": step_id,
                "button_id": button_id,
                "rationale": rationale,
                "parse_mode": parse_result.mode,
                "latency_s": round(latency_s, 3),
                "usage": raw.usage,
                "task_complete": task_complete,
            }
            if exploration:
                report_entry["current_goal"] = current_goal
                report_entry["goal_complete"] = goal_complete

            # 4. Check for completion
            is_done_sentinel = button_id == _DONE_SENTINEL

            # task_complete=true → full stop in both modes.
            # __done__ in task mode → full stop.
            # __done__ in exploration mode → this goal is done, no button to press;
            #   archive the goal and continue to the next one.
            if task_complete or (is_done_sentinel and not exploration):
                if exploration and current_goal:
                    completed_goals.append(current_goal)
                logger.info("Task complete at step %d: %s", step_id, rationale)
                step_reports.append(report_entry)
                tmp_screenshot.unlink(missing_ok=True)
                break

            if exploration and is_done_sentinel:
                # Goal complete, no button action needed — archive and continue.
                logger.info("Goal complete (no-op step): %s", current_goal)
                if current_goal:
                    completed_goals.append(current_goal)
                current_goal = ""
                history = []
                step_reports.append(report_entry)
                tmp_screenshot.unlink(missing_ok=True)
                continue

            # 5. Validate button_id
            coords = self.automation.get_button_coordinates(button_id)
            if coords is None:
                error_msg = f"'{button_id}' is not a valid button ID. Use only IDs from the list."
                logger.warning("Step %d: unknown button_id '%s'", step_id, button_id)
                report_entry["error"] = f"unknown_button_id: {button_id}"
                step_reports.append(report_entry)
                tmp_screenshot.unlink(missing_ok=True)
                history.append({"button_id": button_id, "rationale": rationale, "error": error_msg})
                continue

            # 6. Build action config
            action_config: JsonDict = {
                "action_type": "click",
                "coordinates": list(coords),
                "parameters": {"button": "left", "clicks": 1},
                "button_id": button_id,
                "rationale": rationale,
            }

            # 7. Execute step
            try:
                self.automation.run_step(step_id, action_config)
            except ActionExecutionError as exc:
                logger.error("Step %d: action failed: %s", step_id, exc)
                report_entry["error"] = f"action_failed: {exc}"
                step_reports.append(report_entry)
                tmp_screenshot.unlink(missing_ok=True)
                continue

            # 8. Cleanup temp screenshot
            tmp_screenshot.unlink(missing_ok=True)

            history.append({"button_id": button_id, "rationale": rationale})
            step_reports.append(report_entry)
            logger.info("Step %d: %s — %s", step_id, button_id, rationale)

            # 9. In exploration mode: on goal completion, archive goal and reset history
            if exploration and goal_complete and current_goal:
                logger.info("Goal complete: %s", current_goal)
                completed_goals.append(current_goal)
                current_goal = ""
                history = []

        report: JsonDict = {
            "task": task,
            "exploration": exploration,
            "total_steps": len(step_reports),
            "task_complete": task_complete,
            "steps": step_reports,
        }
        if exploration:
            report["completed_goals"] = completed_goals
            report["goals_completed"] = len(completed_goals)

        report_path = self.automation.output_dir / "task_run_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Report saved: %s", report_path)

        return report


# =========================================================
# CLI
# =========================================================

def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Task-driven or exploration LLM agent for GUI automation"
    )
    parser.add_argument("--task", required=True, help="Natural-language task or exploration directive")
    parser.add_argument("--exploration", action="store_true", help="Enable exploration mode (multi-goal)")
    parser.add_argument("--max-steps", type=int, default=20, help="Maximum steps (default: 20)")
    parser.add_argument("--settings", default="config/settings.yaml")
    parser.add_argument("--app-config", default="config/apps/calculator.yaml")
    parser.add_argument("--teacher-config", default="config/teachers/openai_gpt.yaml")
    parser.add_argument("--output", required=True, help="Output directory for step artifacts")
    parser.add_argument("--app", default="gnome-calculator")
    parser.add_argument("--prompt", default=None, help="Path to prompt template (auto-selected if omitted)")
    parser.add_argument("--display", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Auto-select prompt template based on mode
    if args.prompt:
        prompt_path = Path(args.prompt)
    elif args.exploration:
        prompt_path = Path("config/prompts/action_task_exploration_v1.md")
    else:
        prompt_path = Path("config/prompts/action_task_v1.md")

    if not prompt_path.exists():
        logger.error("Prompt template not found: %s", prompt_path)
        return 1

    prompt_template = PromptLoader(prompt_path.parent).load(prompt_path.name)

    automation = GUIAutomation(
        app_name=args.app,
        output_dir=args.output,
        settings_path=args.settings,
        app_config_path=args.app_config,
        display=args.display,
    )

    llm_client = OpenAIAnnotatorClient(
        settings_path=args.settings,
        teacher_config_path=args.teacher_config,
    )

    runner = TaskRunner(
        automation=automation,
        llm_client=llm_client,
        prompt_template=prompt_template,
        max_steps=args.max_steps,
    )

    with automation:
        report = runner.run(args.task, exploration=args.exploration)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
