"""
DFS-based exploration runner for GUI automation.

Implements a tree-structured exploration strategy inspired by GUI-explorer
(arXiv:2505.16827). From the terminal state of each completed task, the LLM
generates child tasks, building a DFS tree of diverse calculator interactions.

Key features:
  - Global step numbering across all branches (compatible with annotator_runner)
  - Rollback via app restart + action replay
  - dfs_context injected into each step's metadata.json
  - dfs_tree.json captures the full tree structure

Usage:
    python -m src.exploration.dfs_runner \\
        --root-tasks "Calculate 2 + 3" "Compute 7 * 8" \\
        --branching-factor 3 --max-depth 2 --max-steps-per-task 10 \\
        --settings config/settings.yaml \\
        --app-config config/apps/calculator.yaml \\
        --teacher-config config/teachers/openai_gpt.yaml \\
        --output data/exploration/dfs_runs/run_001 \\
        --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.automation import GUIAutomation
from src.core.exceptions import ActionExecutionError
from src.exploration.task_generator import TaskGenerator
from src.teachers.json_parser import RobustJSONParser
from src.teachers.openai_client import OpenAIAnnotatorClient
from src.teachers.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]

_DONE_SENTINEL = "__done__"


@dataclass
class DFSNode:
    """A single node in the DFS exploration tree."""

    node_id: str
    depth: int
    task: str
    parent_id: Optional[str]
    children_ids: List[str] = field(default_factory=list)
    steps: List[int] = field(default_factory=list)
    task_complete: bool = False
    action_replay: List[JsonDict] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "depth": self.depth,
            "task": self.task,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "steps": self.steps,
            "task_complete": self.task_complete,
        }


class DFSExplorer:
    """
    DFS tree exploration of a GUI application.

    From each completed task's terminal state, generates child tasks via LLM
    and recurses, building a tree of diverse interactions.
    """

    def __init__(
        self,
        automation: GUIAutomation,
        llm_client: OpenAIAnnotatorClient,
        task_prompt_template: str,
        task_generator: TaskGenerator,
        branching_factor: int = 3,
        max_depth: int = 3,
        max_steps_per_task: int = 10,
        replay_delay: float = 0.3,
    ) -> None:
        self.automation = automation
        self.llm_client = llm_client
        self.task_prompt_template = task_prompt_template
        self.task_generator = task_generator
        self.branching_factor = branching_factor
        self.max_depth = max_depth
        self.max_steps_per_task = max_steps_per_task
        self.replay_delay = replay_delay

        self._parser = RobustJSONParser()
        self._buttons: Dict[str, List[int]] = dict(
            automation.app_config.get("buttons", {})
        )

        self._global_step: int = 0
        self._nodes: Dict[str, DFSNode] = {}
        self._step_reports: List[JsonDict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explore(self, root_tasks: List[str]) -> JsonDict:
        """
        Run DFS exploration starting from the given root tasks.

        Each root task becomes a separate subtree (depth=0).
        The app is launched once and closed at the end.
        """
        self._global_step = 0
        self._nodes = {}
        self._step_reports = []

        self.automation.launch_app()
        try:
            for i, task in enumerate(root_tasks):
                if i > 0:
                    self._rollback([])
                self._dfs(
                    task=task,
                    depth=0,
                    node_id=str(i),
                    parent_id=None,
                    replay_actions=[],
                )
        finally:
            self.automation.close_app()

        return self._build_report(root_tasks)

    # ------------------------------------------------------------------
    # DFS core
    # ------------------------------------------------------------------

    def _dfs(
        self,
        task: str,
        depth: int,
        node_id: str,
        parent_id: Optional[str],
        replay_actions: List[JsonDict],
    ) -> None:
        logger.info(
            "=== DFS node %s (depth=%d) task: %s ===",
            node_id, depth, task[:80],
        )

        # 1. Execute the task
        result = self._execute_task(task, node_id, depth, parent_id)

        # 2. Record the node
        node = DFSNode(
            node_id=node_id,
            depth=depth,
            task=task,
            parent_id=parent_id,
            children_ids=[],
            steps=result["step_ids"],
            task_complete=result["task_complete"],
            action_replay=replay_actions + result["actions"],
        )
        self._nodes[node_id] = node

        # 3. Check depth limit
        if depth >= self.max_depth:
            logger.info("Node %s: max depth %d reached, not branching", node_id, self.max_depth)
            return

        # 4. Generate child tasks
        children = self._generate_children(task, node_id)
        if not children:
            logger.info("Node %s: no child tasks generated", node_id)
            return

        # 5. Recurse into children
        for i, child_task in enumerate(children):
            child_id = f"{node_id}.{i}"
            node.children_ids.append(child_id)

            if i > 0:
                self._rollback(node.action_replay)

            self._dfs(
                task=child_task,
                depth=depth + 1,
                node_id=child_id,
                parent_id=node_id,
                replay_actions=node.action_replay,
            )

    # ------------------------------------------------------------------
    # Task execution (follows TaskRunner.run pattern)
    # ------------------------------------------------------------------

    def _execute_task(
        self,
        task: str,
        node_id: str,
        depth: int,
        parent_id: Optional[str],
    ) -> JsonDict:
        """
        Execute a single task using the LLM step loop.

        Returns dict with step_ids, actions (for replay), and task_complete flag.
        """
        history: List[JsonDict] = []
        step_ids: List[int] = []
        actions: List[JsonDict] = []
        task_complete = False

        for step_within_task in range(self.max_steps_per_task):
            step_id = self._global_step

            # 1. Screenshot for LLM
            tmp_screenshot = self.automation.output_dir / f"_llm_input_{step_id:04d}.png"
            self.automation.take_screenshot(tmp_screenshot)

            # 2. Build prompt and call LLM
            prompt = self._build_task_prompt(task, history)
            t0 = time.perf_counter()
            try:
                raw = self.llm_client.infer(prompt, image_paths=[tmp_screenshot])
            except Exception:
                logger.exception("LLM call failed at step %d", step_id)
                tmp_screenshot.unlink(missing_ok=True)
                break
            latency_s = time.perf_counter() - t0

            # 3. Parse response
            parse_result = self._parser.parse(raw.text)
            if not parse_result.ok or not isinstance(parse_result.data, dict):
                logger.warning(
                    "Step %d: parse failed (%s): %s",
                    step_id, parse_result.mode, parse_result.error,
                )
                self._step_reports.append({
                    "step_id": step_id,
                    "node_id": node_id,
                    "error": f"parse_failed: {parse_result.error}",
                    "raw_response": raw.text[:500],
                    "latency_s": round(latency_s, 3),
                    "usage": raw.usage,
                })
                tmp_screenshot.unlink(missing_ok=True)
                self._global_step += 1
                continue

            data: JsonDict = parse_result.data
            button_id = str(data.get("button_id", "")).strip()
            rationale = str(data.get("rationale", ""))
            task_complete = bool(data.get("task_complete", False))

            report_entry: JsonDict = {
                "step_id": step_id,
                "node_id": node_id,
                "button_id": button_id,
                "rationale": rationale,
                "parse_mode": parse_result.mode,
                "latency_s": round(latency_s, 3),
                "usage": raw.usage,
                "task_complete": task_complete,
            }

            # 4. Check for completion
            if task_complete or button_id == _DONE_SENTINEL:
                task_complete = True
                logger.info(
                    "Node %s task complete at step %d: %s",
                    node_id, step_id, rationale,
                )
                self._step_reports.append(report_entry)
                tmp_screenshot.unlink(missing_ok=True)
                break

            # 5. Validate button_id
            coords = self.automation.get_button_coordinates(button_id)
            if coords is None:
                error_msg = f"'{button_id}' is not a valid button ID."
                logger.warning("Step %d: unknown button_id '%s'", step_id, button_id)
                report_entry["error"] = f"unknown_button_id: {button_id}"
                self._step_reports.append(report_entry)
                tmp_screenshot.unlink(missing_ok=True)
                history.append({
                    "button_id": button_id,
                    "rationale": rationale,
                    "error": error_msg,
                })
                self._global_step += 1
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
                artifacts = self.automation.run_step(step_id, action_config)
            except ActionExecutionError as exc:
                logger.error("Step %d: action failed: %s", step_id, exc)
                report_entry["error"] = f"action_failed: {exc}"
                self._step_reports.append(report_entry)
                tmp_screenshot.unlink(missing_ok=True)
                self._global_step += 1
                continue

            # 8. Inject dfs_context into metadata.json
            self._inject_dfs_context(
                artifacts.metadata,
                node_id=node_id,
                depth=depth,
                task=task,
                step_within_task=step_within_task,
                parent_node_id=parent_id,
                rationale=rationale,
            )

            # 9. Bookkeeping
            tmp_screenshot.unlink(missing_ok=True)
            step_ids.append(step_id)
            actions.append(action_config)
            history.append({"button_id": button_id, "rationale": rationale})
            self._step_reports.append(report_entry)
            self._global_step += 1

            logger.info(
                "Step %d [%s]: %s — %s",
                step_id, node_id, button_id, rationale,
            )

        return {
            "step_ids": step_ids,
            "actions": actions,
            "task_complete": task_complete,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_task_prompt(self, task: str, history: List[JsonDict]) -> str:
        if not history:
            history_text = "  (no actions yet)"
        else:
            lines = []
            for i, entry in enumerate(history):
                if entry.get("error"):
                    lines.append(f"  Step {i}: tried '{entry['button_id']}' — ERROR: {entry['error']}")
                else:
                    lines.append(f"  Step {i}: clicked {entry['button_id']} — {entry['rationale']}")
            history_text = "\n".join(lines)

        return self.task_prompt_template.format(
            task=task,
            button_list=self._button_list_text(),
            history=history_text,
        )

    def _button_list_text(self) -> str:
        return ", ".join(self._buttons.keys())

    def _generate_children(self, completed_task: str, node_id: str) -> List[str]:
        screenshot = self.automation.output_dir / f"_dfs_gen_{node_id.replace('.', '_')}.png"
        self.automation.take_screenshot(screenshot)

        ancestor_chain = self._ancestor_tasks(node_id)

        children = self.task_generator.generate(
            screenshot_path=screenshot,
            completed_task=completed_task,
            branching_factor=self.branching_factor,
            button_list=self._button_list_text(),
            max_steps=self.max_steps_per_task,
            ancestor_tasks=ancestor_chain,
        )

        screenshot.unlink(missing_ok=True)
        return children

    def _ancestor_tasks(self, node_id: str) -> str:
        """Build a text list of ancestor tasks for the given node."""
        ancestors: List[str] = []
        current = node_id
        while current in self._nodes:
            node = self._nodes[current]
            ancestors.append(node.task)
            if node.parent_id is None:
                break
            current = node.parent_id
        ancestors.reverse()
        if not ancestors:
            return "(none)"
        return "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(ancestors))

    def _rollback(self, replay_actions: List[JsonDict]) -> None:
        """Close app, relaunch, and silently replay actions to restore state."""
        logger.info("Rollback: restarting app + replaying %d actions", len(replay_actions))
        self.automation.close_app()
        self.automation.launch_app()
        for action in replay_actions:
            self.automation.perform_action(action)
            time.sleep(self.replay_delay)

    @staticmethod
    def _inject_dfs_context(
        metadata_path: Path,
        *,
        node_id: str,
        depth: int,
        task: str,
        step_within_task: int,
        parent_node_id: Optional[str],
        rationale: str,
    ) -> None:
        """Add dfs_context field to an existing metadata.json file."""
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        meta["dfs_context"] = {
            "node_id": node_id,
            "depth": depth,
            "task": task,
            "step_within_task": step_within_task,
            "parent_node_id": parent_node_id,
            "rationale": rationale,
        }
        metadata_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _build_report(self, root_tasks: List[str]) -> JsonDict:
        """Save dfs_tree.json and dfs_run_report.json, return the report."""
        # Tree structure
        tree: JsonDict = {
            "config": {
                "branching_factor": self.branching_factor,
                "max_depth": self.max_depth,
                "max_steps_per_task": self.max_steps_per_task,
            },
            "root_tasks": root_tasks,
            "total_nodes": len(self._nodes),
            "total_steps": self._global_step,
            "nodes": {
                nid: node.to_dict() for nid, node in self._nodes.items()
            },
        }

        tree_path = self.automation.output_dir / "dfs_tree.json"
        tree_path.write_text(
            json.dumps(tree, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("DFS tree saved: %s (%d nodes)", tree_path, len(self._nodes))

        # Full run report
        report: JsonDict = {
            "root_tasks": root_tasks,
            "config": tree["config"],
            "total_nodes": len(self._nodes),
            "total_steps": self._global_step,
            "steps": self._step_reports,
        }

        report_path = self.automation.output_dir / "dfs_run_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("DFS report saved: %s", report_path)

        return report


# =========================================================
# CLI
# =========================================================

def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="DFS tree exploration for GUI automation",
    )
    parser.add_argument(
        "--root-tasks", nargs="+", required=True,
        help="One or more root tasks (each becomes a subtree)",
    )
    parser.add_argument("--branching-factor", type=int, default=3)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-steps-per-task", type=int, default=10)
    parser.add_argument("--replay-delay", type=float, default=0.3)
    parser.add_argument("--settings", default="config/settings.yaml")
    parser.add_argument("--app-config", default="config/apps/calculator.yaml")
    parser.add_argument("--teacher-config", default="config/teachers/openai_gpt.yaml")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--app", default="gnome-calculator")
    parser.add_argument("--display", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Load prompt templates
    prompt_dir = Path("config/prompts")
    loader = PromptLoader(prompt_dir)

    task_prompt = loader.load("action_task_v1.md")
    generator_prompt = loader.load("task_generator_v1.md")

    # Init components
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

    task_gen = TaskGenerator(
        llm_client=llm_client,
        prompt_template=generator_prompt,
    )

    explorer = DFSExplorer(
        automation=automation,
        llm_client=llm_client,
        task_prompt_template=task_prompt,
        task_generator=task_gen,
        branching_factor=args.branching_factor,
        max_depth=args.max_depth,
        max_steps_per_task=args.max_steps_per_task,
        replay_delay=args.replay_delay,
    )

    report = explorer.explore(args.root_tasks)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
