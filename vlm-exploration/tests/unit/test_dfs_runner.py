"""
Unit tests for DFS exploration runner.

Runs the full DFS logic with mocked automation and LLM — no GUI, no API calls.
Can be executed on any machine (Mac, Linux, CI).

Usage:
    pytest tests/unit/test_dfs_runner.py -v
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from src.exploration.dfs_runner import DFSExplorer, DFSNode
from src.exploration.task_generator import TaskGenerator


# ---------------------------------------------------------------
# Fake automation that writes real files but doesn't touch GUI
# ---------------------------------------------------------------

class FakeAutomation:
    """Mimics GUIAutomation without any real GUI interaction."""

    def __init__(self, output_dir: Path, buttons: Dict[str, List[int]]):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.app_config = {"buttons": buttons}
        self.app_name = "fake-calculator"
        self.screen_width = 1280
        self.screen_height = 1024
        self.screenshot_delay = 0.0
        self.step_file_before = "before.png"
        self.step_file_after = "after.png"
        self.step_file_metadata = "metadata.json"
        self.step_file_action = "action.json"
        self.save_action_file = True
        self.save_dhash = False
        self.hash_algorithm = "md5"
        self.compare_hashes = False

        self._launched = False
        self._launch_count = 0
        self._close_count = 0
        self._actions_performed: List[Dict[str, Any]] = []

    def launch_app(self) -> bool:
        self._launched = True
        self._launch_count += 1
        return True

    def close_app(self) -> bool:
        self._launched = False
        self._close_count += 1
        return True

    def take_screenshot(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a tiny valid PNG (1x1 pixel)
        path.write_bytes(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00'
            b'\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00'
            b'\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        return path

    def get_button_coordinates(self, button_name: str) -> Optional[tuple]:
        coords = self.app_config["buttons"].get(button_name)
        if coords and isinstance(coords, list) and len(coords) == 2:
            return (coords[0], coords[1])
        return None

    def perform_action(self, action_config: dict) -> None:
        self._actions_performed.append(action_config)

    def run_step(self, step_id: int, action_config: dict, **kwargs):
        step_dir = self.output_dir / f"step_{step_id:04d}"
        step_dir.mkdir(parents=True, exist_ok=True)

        before_path = step_dir / self.step_file_before
        after_path = step_dir / self.step_file_after
        meta_path = step_dir / self.step_file_metadata
        action_path = step_dir / self.step_file_action

        self.take_screenshot(before_path)
        self.take_screenshot(after_path)

        action_path.write_text(
            json.dumps(action_config, indent=2), encoding="utf-8"
        )

        metadata = {
            "step_id": step_id,
            "timestamp": 1700000000.0 + step_id,
            "app": self.app_name,
            "display": ":99",
            "screen": {"width": self.screen_width, "height": self.screen_height},
            "action": action_config,
            "hashes": {"before": f"hash_before_{step_id}", "after": f"hash_after_{step_id}"},
            "dhashes": {"before": None, "after": None, "hamming_distance": None},
            "changed": True,
            "timing": {"action_delay": 0.0, "screenshot_delay": 0.0},
        }
        meta_path.write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        self._actions_performed.append(action_config)

        return MagicMock(
            step_dir=step_dir,
            before=before_path,
            after=after_path,
            metadata=meta_path,
            action=action_path,
        )


# ---------------------------------------------------------------
# Fake LLM responses
# ---------------------------------------------------------------

class FakeLLMClient:
    """Returns scripted LLM responses for task execution and generation."""

    def __init__(self):
        self._call_count = 0
        self._responses: List[str] = []

    def set_responses(self, responses: List[str]):
        self._responses = list(responses)
        self._call_count = 0

    def infer(self, prompt_text, image_paths=None, prefer_json=True):
        idx = min(self._call_count, len(self._responses) - 1)
        text = self._responses[idx] if self._responses else '{"button_id": "__done__", "rationale": "done", "task_complete": true}'
        self._call_count += 1

        return MagicMock(
            text=text,
            model="fake-model",
            latency_s=0.1,
            request_id="fake-req",
            usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )

    def infer_structured(self, prompt_text, response_model=None, image_paths=None):
        """Structured output mock — parses scripted JSON into response_model."""
        raw = self.infer(prompt_text, image_paths=image_paths, prefer_json=True)
        data = json.loads(raw.text)
        return response_model(**data)


# ---------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------

BUTTONS = {
    "digit_2": [183, 383],
    "digit_3": [251, 383],
    "digit_5": [183, 336],
    "plus": [315, 480],
    "minus": [317, 433],
    "equals": [385, 453],
    "backspace": [112, 286],
}

TASK_PROMPT = textwrap.dedent("""\
    Task: {task}
    Buttons: {button_list}
    History: {history}
""")

GENERATOR_PROMPT = textwrap.dedent("""\
    Completed: {completed_task}
    Ancestors: {ancestor_tasks}
    Buttons: {button_list}
    Generate {branching_factor} tasks ({max_steps} steps max).
""")


@pytest.fixture
def output_dir(tmp_path):
    return tmp_path / "dfs_test_output"


@pytest.fixture
def fake_automation(output_dir):
    return FakeAutomation(output_dir, BUTTONS)


@pytest.fixture
def fake_llm():
    return FakeLLMClient()


# ---------------------------------------------------------------
# Tests
# ---------------------------------------------------------------

class TestDFSNode:
    def test_to_dict(self):
        node = DFSNode(
            node_id="0.1",
            depth=1,
            task="Add 2 and 3",
            parent_id="0",
            children_ids=["0.1.0"],
            steps=[5, 6, 7],
            task_complete=True,
        )
        d = node.to_dict()
        assert d["depth"] == 1
        assert d["task"] == "Add 2 and 3"
        assert d["parent_id"] == "0"
        assert d["steps"] == [5, 6, 7]
        assert d["task_complete"] is True
        # action_replay is internal, not in to_dict
        assert "action_replay" not in d


class TestDFSExplorerSimple:
    """Test DFS with a single root task, depth=0 (no branching)."""

    def test_single_task_completes(self, fake_automation, fake_llm, output_dir):
        # LLM: press digit_2, then press plus, then press digit_3, then press equals, then done
        fake_llm.set_responses([
            '{"button_id": "digit_2", "rationale": "enter 2", "task_complete": false}',
            '{"button_id": "plus", "rationale": "add", "task_complete": false}',
            '{"button_id": "digit_3", "rationale": "enter 3", "task_complete": false}',
            '{"button_id": "equals", "rationale": "compute", "task_complete": false}',
            '{"button_id": "__done__", "rationale": "result visible", "task_complete": true}',
        ])

        explorer = DFSExplorer(
            automation=fake_automation,
            llm_client=fake_llm,
            task_prompt_template=TASK_PROMPT,
            task_generator=TaskGenerator(fake_llm, GENERATOR_PROMPT),
            branching_factor=2,
            max_depth=0,  # no branching
            max_steps_per_task=10,
        )

        report = explorer.explore(["Calculate 2 + 3"])

        # Check tree
        assert report["total_nodes"] == 1
        tree_path = output_dir / "dfs_tree.json"
        assert tree_path.exists()
        tree = json.loads(tree_path.read_text())
        assert "0" in tree["nodes"]
        assert tree["nodes"]["0"]["task"] == "Calculate 2 + 3"
        assert tree["nodes"]["0"]["task_complete"] is True
        assert tree["nodes"]["0"]["children_ids"] == []  # max_depth=0

        # Check step artifacts exist
        assert (output_dir / "step_0000" / "before.png").exists()
        assert (output_dir / "step_0000" / "metadata.json").exists()

        # Check dfs_context in metadata
        meta = json.loads((output_dir / "step_0000" / "metadata.json").read_text())
        assert "dfs_context" in meta
        assert meta["dfs_context"]["node_id"] == "0"
        assert meta["dfs_context"]["depth"] == 0
        assert meta["dfs_context"]["task"] == "Calculate 2 + 3"
        assert meta["dfs_context"]["step_within_task"] == 0

    def test_app_lifecycle(self, fake_automation, fake_llm, output_dir):
        fake_llm.set_responses([
            '{"button_id": "__done__", "rationale": "done", "task_complete": true}',
        ])

        explorer = DFSExplorer(
            automation=fake_automation,
            llm_client=fake_llm,
            task_prompt_template=TASK_PROMPT,
            task_generator=TaskGenerator(fake_llm, GENERATOR_PROMPT),
            max_depth=0,
        )
        explorer.explore(["Test task"])

        assert fake_automation._launch_count == 1
        assert fake_automation._close_count == 1


class TestDFSExplorerBranching:
    """Test DFS with branching (depth > 0)."""

    def test_depth_1_branching_2(self, fake_automation, fake_llm, output_dir):
        """
        Tree should look like:
        0 (root: "Add 2+3")
        ├── 0.0 ("Multiply by 5")
        └── 0.1 ("Subtract 1")
        """
        call_count = 0

        def fake_infer(prompt_text, image_paths=None, prefer_json=True):
            nonlocal call_count
            call_count += 1

            # Task execution responses (all tasks complete in 1 step)
            if "Task:" in prompt_text or "task" in prompt_text.lower()[:20]:
                return MagicMock(
                    text='{"button_id": "digit_2", "rationale": "do it", "task_complete": false}',
                    model="fake", latency_s=0.1, request_id="r", usage={"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
                )

            # Task generation response
            return MagicMock(
                text='{"tasks": ["Multiply result by 5", "Subtract 1 from result"]}',
                model="fake", latency_s=0.1, request_id="r", usage={"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            )

        fake_llm.infer = fake_infer

        # We need the task execution to actually complete.
        # Let's use a smarter response pattern.
        response_queue = []

        # Root task: 2 steps then done
        response_queue.append('{"button_id": "digit_2", "rationale": "enter 2", "task_complete": false}')
        response_queue.append('{"button_id": "__done__", "rationale": "done", "task_complete": true}')
        # Generator for root: returns 2 children
        response_queue.append('{"tasks": ["Multiply result by 5", "Subtract 1 from result"]}')
        # Child 0.0: 1 step then done
        response_queue.append('{"button_id": "digit_5", "rationale": "enter 5", "task_complete": false}')
        response_queue.append('{"button_id": "__done__", "rationale": "done", "task_complete": true}')
        # Child 0.1: 1 step then done (after rollback)
        response_queue.append('{"button_id": "minus", "rationale": "subtract", "task_complete": false}')
        response_queue.append('{"button_id": "__done__", "rationale": "done", "task_complete": true}')

        call_idx = 0
        def sequential_infer(prompt_text, image_paths=None, prefer_json=True):
            nonlocal call_idx
            text = response_queue[min(call_idx, len(response_queue) - 1)]
            call_idx += 1
            return MagicMock(
                text=text, model="fake", latency_s=0.1, request_id="r",
                usage={"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            )

        fake_llm.infer = sequential_infer

        explorer = DFSExplorer(
            automation=fake_automation,
            llm_client=fake_llm,
            task_prompt_template=TASK_PROMPT,
            task_generator=TaskGenerator(fake_llm, GENERATOR_PROMPT),
            branching_factor=2,
            max_depth=1,
            max_steps_per_task=5,
        )

        report = explorer.explore(["Add 2 + 3"])

        # Should have 3 nodes: root + 2 children
        assert report["total_nodes"] == 3

        tree = json.loads((output_dir / "dfs_tree.json").read_text())
        assert "0" in tree["nodes"]
        assert "0.0" in tree["nodes"]
        assert "0.1" in tree["nodes"]

        # Root has 2 children
        assert tree["nodes"]["0"]["children_ids"] == ["0.0", "0.1"]
        assert tree["nodes"]["0.0"]["parent_id"] == "0"
        assert tree["nodes"]["0.1"]["parent_id"] == "0"

        # Children at depth 1, no grandchildren (max_depth=1)
        assert tree["nodes"]["0.0"]["depth"] == 1
        assert tree["nodes"]["0.1"]["depth"] == 1
        assert tree["nodes"]["0.0"]["children_ids"] == []
        assert tree["nodes"]["0.1"]["children_ids"] == []

    def test_rollback_called_for_second_child(self, fake_automation, fake_llm, output_dir):
        """Rollback (close + launch) should happen before the 2nd child."""
        response_queue = [
            # Root: immediate done
            '{"button_id": "__done__", "rationale": "done", "task_complete": true}',
            # Generator: 2 children
            '{"tasks": ["Child A", "Child B"]}',
            # Child A: immediate done
            '{"button_id": "__done__", "rationale": "done", "task_complete": true}',
            # Child B: immediate done (after rollback)
            '{"button_id": "__done__", "rationale": "done", "task_complete": true}',
        ]

        call_idx = 0
        def sequential_infer(prompt_text, image_paths=None, prefer_json=True):
            nonlocal call_idx
            text = response_queue[min(call_idx, len(response_queue) - 1)]
            call_idx += 1
            return MagicMock(
                text=text, model="fake", latency_s=0.1, request_id="r",
                usage={"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            )

        fake_llm.infer = sequential_infer

        explorer = DFSExplorer(
            automation=fake_automation,
            llm_client=fake_llm,
            task_prompt_template=TASK_PROMPT,
            task_generator=TaskGenerator(fake_llm, GENERATOR_PROMPT),
            branching_factor=2,
            max_depth=1,
            max_steps_per_task=5,
        )

        explorer.explore(["Root task"])

        # Initial launch + 1 rollback for child B = 2 launches, 2 closes
        # (rollback = close + launch, plus final close)
        assert fake_automation._launch_count == 2  # initial + rollback
        assert fake_automation._close_count == 2  # rollback + final


class TestDFSExplorerEdgeCases:
    def test_invalid_button_id_skipped(self, fake_automation, fake_llm, output_dir):
        """LLM returns invalid button_id — should skip and continue."""
        fake_llm.set_responses([
            '{"button_id": "nonexistent_button", "rationale": "oops", "task_complete": false}',
            '{"button_id": "digit_2", "rationale": "enter 2", "task_complete": false}',
            '{"button_id": "__done__", "rationale": "done", "task_complete": true}',
        ])

        explorer = DFSExplorer(
            automation=fake_automation,
            llm_client=fake_llm,
            task_prompt_template=TASK_PROMPT,
            task_generator=TaskGenerator(fake_llm, GENERATOR_PROMPT),
            max_depth=0,
            max_steps_per_task=5,
        )

        report = explorer.explore(["Test"])
        # Should have completed despite the error
        assert report["total_nodes"] == 1

        # The error step should be in step_reports
        error_steps = [s for s in report["steps"] if "error" in s]
        assert len(error_steps) >= 1
        assert "unknown_button_id" in error_steps[0]["error"]

    def test_empty_root_tasks(self, fake_automation, fake_llm, output_dir):
        """Empty root_tasks list should produce empty report."""
        explorer = DFSExplorer(
            automation=fake_automation,
            llm_client=fake_llm,
            task_prompt_template=TASK_PROMPT,
            task_generator=TaskGenerator(fake_llm, GENERATOR_PROMPT),
            max_depth=0,
        )

        report = explorer.explore([])
        assert report["total_nodes"] == 0
        assert report["total_steps"] == 0

    def test_max_steps_per_task_limit(self, fake_automation, fake_llm, output_dir):
        """Task should stop after max_steps_per_task even if not complete."""
        # LLM never says task_complete
        fake_llm.set_responses([
            '{"button_id": "digit_2", "rationale": "clicking", "task_complete": false}',
        ])

        explorer = DFSExplorer(
            automation=fake_automation,
            llm_client=fake_llm,
            task_prompt_template=TASK_PROMPT,
            task_generator=TaskGenerator(fake_llm, GENERATOR_PROMPT),
            max_depth=0,
            max_steps_per_task=3,
        )

        report = explorer.explore(["Infinite task"])
        tree = json.loads((output_dir / "dfs_tree.json").read_text())
        # Should have exactly 3 steps
        assert len(tree["nodes"]["0"]["steps"]) == 3
        assert tree["nodes"]["0"]["task_complete"] is False


class TestTaskGenerator:
    def test_generate_parses_tasks(self, fake_llm):
        fake_llm.set_responses([
            '{"tasks": ["Do addition", "Do subtraction", "Do multiplication"]}',
        ])
        gen = TaskGenerator(fake_llm, GENERATOR_PROMPT)
        tasks = gen.generate(
            screenshot_path=Path("/fake/screenshot.png"),
            completed_task="Test",
            branching_factor=3,
            button_list="digit_1, plus, equals",
            max_steps=10,
        )
        assert tasks == ["Do addition", "Do subtraction", "Do multiplication"]

    def test_generate_truncates_to_branching_factor(self, fake_llm):
        fake_llm.set_responses([
            '{"tasks": ["A", "B", "C", "D", "E"]}',
        ])
        gen = TaskGenerator(fake_llm, GENERATOR_PROMPT)
        tasks = gen.generate(
            screenshot_path=Path("/fake/screenshot.png"),
            completed_task="Test",
            branching_factor=2,
            button_list="digit_1",
            max_steps=5,
        )
        assert len(tasks) == 2

    def test_generate_handles_parse_failure(self, fake_llm):
        fake_llm.set_responses(["This is not JSON at all"])
        gen = TaskGenerator(fake_llm, GENERATOR_PROMPT)
        tasks = gen.generate(
            screenshot_path=Path("/fake/screenshot.png"),
            completed_task="Test",
            branching_factor=3,
            button_list="digit_1",
            max_steps=5,
        )
        assert tasks == []

    def test_generate_handles_empty_tasks(self, fake_llm):
        fake_llm.set_responses(['{"tasks": []}'])
        gen = TaskGenerator(fake_llm, GENERATOR_PROMPT)
        tasks = gen.generate(
            screenshot_path=Path("/fake/screenshot.png"),
            completed_task="Test",
            branching_factor=3,
            button_list="digit_1",
            max_steps=5,
        )
        assert tasks == []
