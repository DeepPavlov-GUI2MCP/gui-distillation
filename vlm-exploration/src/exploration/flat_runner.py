"""
Flat exploration runner with diverse task generation.

Generates batches of easy/medium/hard tasks via structured output,
executes them sequentially, tracks button coverage, and generates
new batches until the step budget is exhausted.

Usage:
    python -m src.exploration.flat_runner \
        --n-easy 5 --n-medium 8 --n-hard 3 \
        --max-steps 100 \
        --settings config/settings.yaml \
        --app-config config/apps/calculator.yaml \
        --teacher-config config/teachers/vllm.yaml \
        --output data/exploration/flat_runs/run_001 \
        --display :99 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel

from src.core.automation import GUIAutomation
from src.teachers.openai_client import OpenAIAnnotatorClient
from src.teachers.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

_DONE_SENTINEL = "__done__"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TaskBatch(BaseModel):
    easy: List[str]
    medium: List[str]
    hard: List[str]


class StepResponse(BaseModel):
    button_id: str
    rationale: str
    task_complete: bool


# ---------------------------------------------------------------------------
# Task batch generator
# ---------------------------------------------------------------------------

class BatchTaskGenerator:
    def __init__(self, llm_client: OpenAIAnnotatorClient, prompt_template: str) -> None:
        self.llm_client = llm_client
        self.prompt_template = prompt_template

    def generate(
        self,
        button_list: str,
        used_buttons: List[str],
        previous_tasks: List[str],
        all_buttons: List[str],
        button_counter: dict,
        n_easy: int = 5,
        n_medium: int = 8,
        n_hard: int = 3,
    ) -> TaskBatch:
        # Build usage summary: show count per button, highlight zeros
        usage_lines = []
        for btn in all_buttons:
            count = button_counter.get(btn, 0)
            marker = " ← NEVER USED, MUST INCLUDE!" if count == 0 else ""
            usage_lines.append(f"  {btn}: {count}{marker}")
        used_str = "\n".join(usage_lines)
        prev_str = "; ".join(previous_tasks[-20:]) if previous_tasks else "(none)"
        prompt = self.prompt_template.format(
            button_list=button_list,
            used_buttons=used_str,
            previous_tasks=prev_str,
            n_easy=n_easy,
            n_medium=n_medium,
            n_hard=n_hard,
        )
        try:
            result = self.llm_client.infer_structured(prompt, response_model=TaskBatch)
            logger.info(
                "Generated batch: %d easy, %d medium, %d hard",
                len(result.easy), len(result.medium), len(result.hard),
            )
            return result
        except Exception:
            logger.exception("Batch task generation failed, returning empty batch")
            return TaskBatch(easy=[], medium=[], hard=[])


# ---------------------------------------------------------------------------
# Flat runner
# ---------------------------------------------------------------------------

class FlatExplorer:
    def __init__(
        self,
        automation: GUIAutomation,
        llm_client: OpenAIAnnotatorClient,
        task_prompt: str,
        generator_prompt: str,
        n_easy: int = 5,
        n_medium: int = 8,
        n_hard: int = 3,
        max_steps_per_task: int = 20,
        calc_mode: str = "basic",
    ) -> None:
        self.automation = automation
        self.llm_client = llm_client
        self.task_prompt = task_prompt
        self.calc_mode = calc_mode
        self.n_easy = n_easy
        self.n_medium = n_medium
        self.n_hard = n_hard
        self.max_steps_per_task = max_steps_per_task
        self.generator = BatchTaskGenerator(llm_client, generator_prompt)
        self._global_step = 0
        self._button_counter: Counter = Counter()
        self._all_tasks: List[str] = []
        self._task_results: List[dict] = []

    def _button_list_text(self) -> str:
        return ", ".join(self.automation.app_config.get("buttons", {}).keys())

    def _get_next_batch(self) -> List[str]:
        all_buttons = list(self.automation.app_config.get("buttons", {}).keys())
        batch = self.generator.generate(
            button_list=self._button_list_text(),
            used_buttons=list(self._button_counter.keys()),
            previous_tasks=self._all_tasks,
            all_buttons=all_buttons,
            button_counter=dict(self._button_counter),
            n_easy=self.n_easy,
            n_medium=self.n_medium,
            n_hard=self.n_hard,
        )
        tasks = batch.easy + batch.medium + batch.hard
        self._all_tasks.extend(tasks)
        return tasks

    def _execute_task(self, task: str, task_idx: int) -> dict:
        history = []
        step_ids = []
        task_complete = False

        for step_within_task in range(self.max_steps_per_task):
            step_id = self._global_step
            tmp_screenshot = self.automation.output_dir / f"_tmp_{step_id}.png"
            self.automation.take_screenshot(tmp_screenshot)

            history_text = "\n".join(
                f"  Step {i}: clicked {h['button_id']} — {h['rationale']}"
                for i, h in enumerate(history)
            ) or "  (no actions yet)"

            prompt = self.task_prompt.format(
                task=task,
                button_list=self._button_list_text(),
                history=history_text,
            )

            t0 = time.perf_counter()
            try:
                resp = self.llm_client.infer_structured(
                    prompt,
                    response_model=StepResponse,
                    image_paths=[tmp_screenshot],
                )
            except Exception as e:
                logger.warning("Step %d LLM failed: %s", step_id, e)
                tmp_screenshot.unlink(missing_ok=True)
                self._global_step += 1
                # Timeout likely means this request hung the server;
                # skip the rest of this task and cool down so the server recovers.
                if "timed out" in str(e).lower() or "timeout" in type(e).__name__.lower():
                    logger.warning("Timeout detected — skipping rest of task %d, cooling down 30s", task_idx)
                    time.sleep(30)
                    break
                continue
            latency_s = time.perf_counter() - t0

            button_id = resp.button_id.strip()
            rationale = resp.rationale
            task_complete = resp.task_complete

            if task_complete or button_id == _DONE_SENTINEL:
                logger.info("Task %d complete at step %d: %s", task_idx, step_id, rationale)
                tmp_screenshot.unlink(missing_ok=True)
                break

            coords = self.automation.get_button_coordinates(button_id)
            if coords is None:
                logger.warning("Step %d: unknown button_id '%s'", step_id, button_id)
                tmp_screenshot.unlink(missing_ok=True)
                self._global_step += 1
                history.append({"button_id": button_id, "rationale": rationale, "error": "unknown_button"})
                continue

            action_config = {
                "action_type": "click",
                "coordinates": list(coords),
                "parameters": {"button": "left", "clicks": 1},
                "button_id": button_id,
            }

            try:
                self.automation.run_step(step_id, action_config)
            except Exception as e:
                logger.error("Step %d action failed: %s", step_id, e)
                tmp_screenshot.unlink(missing_ok=True)
                self._global_step += 1
                continue

            tmp_screenshot.unlink(missing_ok=True)
            step_ids.append(step_id)
            self._button_counter[button_id] += 1
            history.append({"button_id": button_id, "rationale": rationale})
            logger.info("Step %d [task %d]: %s — %s", step_id, task_idx, button_id, rationale[:60])
            self._global_step += 1

        return {"task": task, "step_ids": step_ids, "task_complete": task_complete}

    def _get_calc_window_id(self) -> str | None:
        """Return the xdotool window ID of the running gnome-calculator, or None."""
        import subprocess, os
        env = {**os.environ, "DISPLAY": self.automation.display}
        for search_arg in (["--name", "Calculator"], ["--name", "gnome-calculator"], ["--class", "gnome-calculator"]):
            try:
                result = subprocess.run(
                    ["xdotool", "search"] + search_arg,
                    capture_output=True, text=True, env=env, timeout=3,
                )
                ids = result.stdout.strip().split()
                if ids:
                    return ids[0]
            except Exception:
                pass
        return None

    def _clear_display(self) -> None:
        """Send Escape to the calculator window to clear any expression or result."""
        import subprocess, os
        env = {**os.environ, "DISPLAY": self.automation.display}
        wid = self._get_calc_window_id()
        cmd = ["xdotool", "key", "--clearmodifiers"]
        if wid:
            cmd += ["--window", wid]
        cmd += ["Escape"]
        try:
            subprocess.run(cmd, env=env, check=False, timeout=3)
            time.sleep(0.2)
            subprocess.run(cmd, env=env, check=False, timeout=3)
            time.sleep(0.2)
        except Exception as e:
            logger.warning("_clear_display failed: %s", e)

    def explore(self, max_total_steps: int) -> dict:
        import subprocess, os
        # Force the configured calculator mode before starting.
        subprocess.run(
            ["gsettings", "set", "org.gnome.calculator", "button-mode", self.calc_mode],
            check=False, timeout=5,
        )
        # Kill any stale calculator instances before starting a clean session.
        subprocess.run(["pkill", "-9", "gnome-calculator"], check=False, timeout=5)
        time.sleep(0.8)
        self.automation.launch_app()
        time.sleep(1.0)
        self._clear_display()
        task_idx = 0

        try:
            while self._global_step < max_total_steps:
                tasks = self._get_next_batch()
                if not tasks:
                    logger.warning("Empty batch, stopping")
                    break

                for task in tasks:
                    if self._global_step >= max_total_steps:
                        break
                    self._clear_display()
                    logger.info("=== Task %d: %s ===", task_idx, task)
                    result = self._execute_task(task, task_idx)
                    self._task_results.append(result)
                    task_idx += 1
        finally:
            self.automation.close_app()
            import subprocess as _sp
            _sp.run(["pkill", "-9", "gnome-calculator"], check=False, timeout=5)

        return self._save_report()

    def _save_report(self) -> dict:
        report = {
            "total_tasks": len(self._task_results),
            "total_steps": self._global_step,
            "task_complete_rate": sum(r["task_complete"] for r in self._task_results) / max(len(self._task_results), 1),
            "button_coverage": dict(self._button_counter.most_common()),
            "tasks": self._task_results,
        }
        out = self.automation.output_dir / "flat_run_report.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        logger.info("Report saved to %s", out)
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    parser = argparse.ArgumentParser("flat_runner")
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--n-easy", type=int, default=5)
    parser.add_argument("--n-medium", type=int, default=8)
    parser.add_argument("--n-hard", type=int, default=3)
    parser.add_argument("--max-steps-per-task", type=int, default=20)
    parser.add_argument("--settings", default="config/settings.yaml")
    parser.add_argument("--app-config", default="config/apps/calculator.yaml")
    parser.add_argument("--teacher-config", default="config/teachers/vllm.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--display", default=":99")
    parser.add_argument("--task-batch-prompt", default="config/prompts/task_batch_v1.md")
    parser.add_argument("--calc-mode", default="basic", choices=["basic", "advanced", "programming"])
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    import os
    if args.display:
        os.environ["DISPLAY"] = args.display

    llm = OpenAIAnnotatorClient(
        settings_path=args.settings,
        teacher_config_path=args.teacher_config,
    )

    task_prompt = PromptLoader(Path("config/prompts")).load("action_task_v1.md")
    gen_prompt = Path(args.task_batch_prompt).read_text()

    automation = GUIAutomation(
        app_name="gnome-calculator",
        settings_path=args.settings,
        app_config_path=args.app_config,
        output_dir=args.output,
        display=args.display,
    )

    explorer = FlatExplorer(
        automation=automation,
        llm_client=llm,
        task_prompt=task_prompt,
        generator_prompt=gen_prompt,
        n_easy=args.n_easy,
        n_medium=args.n_medium,
        n_hard=args.n_hard,
        max_steps_per_task=args.max_steps_per_task,
        calc_mode=args.calc_mode,
    )

    report = explorer.explore(max_total_steps=args.max_steps)
    print(f"\nDone: {report['total_tasks']} tasks, {report['total_steps']} steps, "
          f"completion rate: {report['task_complete_rate']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
