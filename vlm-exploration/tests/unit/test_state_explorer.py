"""
Unit tests for state_explorer.py (VLM-driven UI state exploration).

Mocks VLM client and OS-level operations (scrot, xdotool, pgrep).
Can run on any machine without GUI.

Usage:
    pytest tests/unit/test_state_explorer.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

try:
    from src.exploration.state_explorer import (
        ExploreStep, StateExplorer, UIElementInfo,
    )
    from src.teachers.openai_client import StructuredResult
except ImportError:
    from state_explorer import ExploreStep, StateExplorer, UIElementInfo
    from dataclasses import dataclass as _dc

    @_dc
    class StructuredResult:
        parsed: object
        usage: dict = None
        latency_s: float = 0.0


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _btn(name: str) -> UIElementInfo:
    """Shortcut: create a button element."""
    return UIElementInfo(name=name, type="button")


def _el(name: str, typ: str, value: str | None = None, enabled: bool = True) -> UIElementInfo:
    """Shortcut: create an element with explicit type/value."""
    return UIElementInfo(name=name, type=typ, value=value, enabled=enabled)


def _step(
    label: str,
    elements: List[UIElementInfo],
    is_new: bool = True,
    click_x: int = 100,
    click_y: int = 100,
    target: str = "x",
    done: bool = False,
) -> ExploreStep:
    """Shortcut: create an ExploreStep."""
    return ExploreStep(
        screen_label=label,
        elements=elements,
        description=f"{label} screen",
        is_new_screen=is_new,
        reasoning="test reasoning",
        exploration_done=done,
        click_x=click_x,
        click_y=click_y,
        target_name=target,
    )


# -------------------------------------------------------------------
# ExploreStep & UIElementInfo schema
# -------------------------------------------------------------------

class TestUIElementInfo:
    def test_button(self):
        el = UIElementInfo(name="7", type="button")
        assert el.value is None
        assert el.enabled is True

    def test_label_with_value(self):
        el = UIElementInfo(name="Display", type="label", value="42")
        assert el.value == "42"

    def test_dropdown_with_value(self):
        el = UIElementInfo(name="Angle units", type="dropdown", value="Degrees")
        assert el.type == "dropdown"
        assert el.value == "Degrees"

    def test_toggle(self):
        el = UIElementInfo(name="Thousands separators", type="toggle", value="off")
        assert el.value == "off"

    def test_disabled(self):
        el = UIElementInfo(name="Undo", type="button", enabled=False)
        assert el.enabled is False


class TestExploreStep:
    def test_valid_step(self):
        step = _step("basic_mode", [_btn("1"), _btn("2"), _btn("+"), _btn("=")])
        assert step.screen_label == "basic_mode"
        assert len(step.elements) == 4
        assert step.is_new_screen is True

    def test_exploration_done(self):
        step = _step("basic_mode", [_btn("1")], done=True)
        assert step.exploration_done is True

    def test_mixed_element_types(self):
        step = _step("advanced", [
            _el("Display", "label", "0"),
            _btn("7"),
            _el("Degrees", "dropdown", "Degrees"),
            _el("Thousands sep", "toggle", "off"),
        ])
        assert len(step.elements) == 4
        types = {e.type for e in step.elements}
        assert types == {"label", "button", "dropdown", "toggle"}


# -------------------------------------------------------------------
# Fake infrastructure
# -------------------------------------------------------------------

class FakeVLMClient:
    """Returns scripted ExploreStep responses."""

    def __init__(self):
        self._responses: List[ExploreStep] = []
        self._call_count = 0

    def set_responses(self, responses: List[ExploreStep]):
        self._responses = list(responses)
        self._call_count = 0

    def infer_structured(self, prompt_text, response_model=None, image_paths=None):
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]

    def infer_structured_with_usage(self, prompt_text, response_model=None, image_paths=None):
        parsed = self.infer_structured(prompt_text, response_model, image_paths)
        return StructuredResult(
            parsed=parsed,
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            latency_s=0.1,
        )


def _make_explorer(tmp_path: Path, vlm: FakeVLMClient) -> StateExplorer:
    """Create a StateExplorer with mocked automation and VLM."""
    automation = MagicMock()
    automation.app_name = "gnome-calculator"

    explorer = StateExplorer(
        automation=automation,
        vlm_client=vlm,
        display=":99",
        output_dir=tmp_path / "output",
        prompt_file="explore_step_v3.md",
    )
    explorer._screenshot = lambda name: _fake_screenshot(tmp_path / "output", name)
    explorer._click = lambda x, y: None
    explorer._press_escape = lambda: None
    explorer._ensure_app_running = lambda: None
    return explorer


_screenshot_counter = 0

def _fake_screenshot(output_dir: Path, name: str) -> Path:
    """Create a unique 64x64 PNG with distinct pattern so dHash differentiates screens."""
    global _screenshot_counter
    _screenshot_counter += 1
    from PIL import Image, ImageDraw
    path = output_dir / "screenshots" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each call produces a visually distinct image
    bg = ((_screenshot_counter * 60) % 256, (_screenshot_counter * 90) % 256, (_screenshot_counter * 130) % 256)
    img = Image.new("RGB", (64, 64), bg)
    draw = ImageDraw.Draw(img)
    # Draw unique rectangles to make dHash distinct
    offset = (_screenshot_counter * 11) % 32
    draw.rectangle([offset, offset, offset + 20, offset + 20], fill=(255, 255, 255))
    draw.rectangle([offset + 5, 0, offset + 10, 64], fill=(0, 0, 0))
    img.save(path)
    return path


@pytest.fixture(autouse=True)
def mock_load_prompt(monkeypatch):
    """Provide a dummy prompt template."""
    try:
        import src.exploration.state_explorer as mod
    except ImportError:
        import state_explorer as mod
    monkeypatch.setattr(
        mod, "_load_prompt",
        lambda name: "App: {app_name} ({screen_width}x{screen_height})\nDiscovered:\n{discovered_screens}\nExplored:\n{explored_actions}",
    )


# -------------------------------------------------------------------
# _record_screen tests
# -------------------------------------------------------------------

class TestRecordScreen:
    def test_new_screen_recorded(self, tmp_path):
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        ss_path = _fake_screenshot(tmp_path / "output", "test")

        step = _step("basic_mode", [
            _el("Display", "label", "0"),
            _btn("1"), _btn("2"), _btn("3"), _btn("+"), _btn("="),
        ])
        is_new = explorer._record_screen(step, ss_path)

        assert is_new is True
        assert "basic_mode" in explorer.screens
        assert len(explorer.screens["basic_mode"]["elements"]) == 6
        # buttons = only interactive elements
        assert len(explorer.screens["basic_mode"]["buttons"]) == 5

    def test_existing_screen_merges_elements(self, tmp_path):
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        ss_path = _fake_screenshot(tmp_path / "output", "test")

        step1 = _step("basic_mode", [_btn("1"), _btn("2"), _btn("+")])
        explorer._record_screen(step1, ss_path)

        # Revisit with extra elements
        step2 = _step("basic_mode", [
            _btn("1"), _btn("2"), _btn("+"), _btn("="), _btn("-"),
        ], is_new=False)
        is_new = explorer._record_screen(step2, ss_path)

        assert is_new is False
        assert len(explorer.screens["basic_mode"]["buttons"]) == 5

    def test_merge_by_name_type_key(self, tmp_path):
        """Elements are merged by (name, type) — same name different type stays separate."""
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        ss_path = _fake_screenshot(tmp_path / "output", "test")

        step1 = _step("prefs", [
            _el("Angle units", "dropdown", "Degrees"),
            _el("OK", "button"),
        ])
        explorer._record_screen(step1, ss_path)

        step2 = _step("prefs", [
            _el("Angle units", "dropdown", "Radians"),  # same (name,type), updated value
            _el("Angle units", "label", "Select angle unit"),  # different type
            _el("Cancel", "button"),
        ], is_new=False)
        explorer._record_screen(step2, ss_path)

        screen = explorer.screens["prefs"]
        elements = screen["elements"]
        # Should have: dropdown Angle units, label Angle units, OK, Cancel = 4
        assert len(elements) == 4
        # Dropdown value should be updated to "Radians"
        dropdown = next(e for e in elements if e["name"] == "Angle units" and e["type"] == "dropdown")
        assert dropdown["value"] == "Radians"

    def test_label_not_in_buttons(self, tmp_path):
        """Label elements should not appear in the backward-compat buttons list."""
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        ss_path = _fake_screenshot(tmp_path / "output", "test")

        step = _step("basic", [
            _el("Display", "label", "0"),
            _el("Result history", "label", ""),
            _btn("7"),
            _btn("8"),
        ])
        explorer._record_screen(step, ss_path)

        assert explorer.screens["basic"]["buttons"] == ["7", "8"]

    def test_new_screen_with_fresh_label(self, tmp_path):
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        ss_path = _fake_screenshot(tmp_path / "output", "test")

        step = _step("advanced_mode", [
            _btn("sin"), _btn("cos"), _btn("tan"),
        ])
        is_new = explorer._record_screen(step, ss_path)

        assert is_new is True
        assert "advanced_mode" in explorer.screens


# -------------------------------------------------------------------
# explore() main loop
# -------------------------------------------------------------------

class TestExplore:
    def test_basic_exploration_3_steps(self, tmp_path):
        vlm = FakeVLMClient()
        vlm.set_responses([
            _step("basic_mode", [_btn("1"), _btn("2"), _btn("+"), _btn("=")],
                  target="mode_dropdown"),
            _step("dropdown_open", [
                _el("Basic", "menu_item"), _el("Advanced", "menu_item"),
                _el("Programming", "menu_item"),
            ], target="Advanced"),
            _step("advanced_mode", [
                _btn("sin"), _btn("cos"), _btn("tan"), _btn("1"), _btn("2"),
            ], done=True),
        ])

        explorer = _make_explorer(tmp_path, vlm)
        graph = explorer.explore(max_steps=10)

        assert graph["total_screens"] == 3
        assert graph["total_transitions"] == 2
        assert graph["total_steps"] == 3
        assert "basic_mode" in graph["screens"]
        assert "dropdown_open" in graph["screens"]
        assert "advanced_mode" in graph["screens"]

    def test_exploration_done_stops_early(self, tmp_path):
        vlm = FakeVLMClient()
        vlm.set_responses([
            _step("basic_mode", [_btn("1")], done=True),
        ])

        explorer = _make_explorer(tmp_path, vlm)
        graph = explorer.explore(max_steps=100)

        assert graph["total_steps"] == 1
        assert graph["total_screens"] == 1

    def test_max_steps_respected(self, tmp_path):
        vlm = FakeVLMClient()
        vlm.set_responses([
            _step("basic_mode", [_btn("1")], is_new=False),
        ])

        explorer = _make_explorer(tmp_path, vlm)
        graph = explorer.explore(max_steps=5)

        assert graph["total_steps"] == 5

    def test_vlm_failure_continues(self, tmp_path):
        """VLM raising an exception on one step should not crash the loop."""
        call_count = 0

        def flaky_infer(prompt_text, response_model=None, image_paths=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("VLM timeout")
            return StructuredResult(
                parsed=_step(
                    "basic_mode", [_btn("1")],
                    is_new=call_count == 1,
                    done=call_count >= 3,
                ),
                usage={"prompt_tokens": 100, "completion_tokens": 50},
                latency_s=0.1,
            )

        vlm = FakeVLMClient()
        vlm.infer_structured_with_usage = flaky_infer

        explorer = _make_explorer(tmp_path, vlm)
        graph = explorer.explore(max_steps=5)

        assert graph["total_screens"] >= 1


# -------------------------------------------------------------------
# _save_graph output format
# -------------------------------------------------------------------

class TestSaveGraph:
    def test_graph_json_structure(self, tmp_path):
        vlm = FakeVLMClient()
        vlm.set_responses([
            _step("basic", [_btn("1"), _btn("2"), _el("Display", "label", "0")],
                  target="dropdown"),
            _step("advanced", [_btn("sin"), _btn("cos")], done=True),
        ])

        explorer = _make_explorer(tmp_path, vlm)
        graph = explorer.explore(max_steps=10)

        graph_path = tmp_path / "output" / "state_graph.json"
        assert graph_path.exists()

        saved = json.loads(graph_path.read_text())
        assert saved["app"] == "gnome-calculator"
        assert "exploration_date" in saved
        assert isinstance(saved["screens"], dict)
        assert isinstance(saved["transitions"], list)
        assert saved["total_screens"] == len(saved["screens"])
        assert saved["total_transitions"] == len(saved["transitions"])

        for label, info in saved["screens"].items():
            assert "description" in info
            assert "elements" in info
            assert "buttons" in info  # backward-compat
            assert "screenshot" in info
            assert "path_from_root" in info
            # elements are dicts with required keys
            for el in info["elements"]:
                assert "name" in el
                assert "type" in el

    def test_elements_in_graph(self, tmp_path):
        """Graph should contain rich element data, not just names."""
        vlm = FakeVLMClient()
        vlm.set_responses([
            _step("basic", [
                _el("Display", "label", "0"),
                _btn("7"),
                _el("Mode", "dropdown", "Basic"),
            ], done=True),
        ])

        explorer = _make_explorer(tmp_path, vlm)
        graph = explorer.explore(max_steps=5)

        screen = graph["screens"]["basic"]
        elements = screen["elements"]
        assert len(elements) == 3

        display = next(e for e in elements if e["name"] == "Display")
        assert display["type"] == "label"
        assert display["value"] == "0"

        mode = next(e for e in elements if e["name"] == "Mode")
        assert mode["type"] == "dropdown"
        assert mode["value"] == "Basic"

        # buttons list should only have interactive elements
        assert screen["buttons"] == ["7", "Mode"]

    def test_steps_saved_to_disk(self, tmp_path):
        """Each step should be saved as a JSON file."""
        vlm = FakeVLMClient()
        vlm.set_responses([
            _step("basic", [_btn("1")], target="menu"),
            _step("menu", [_el("Prefs", "menu_item")], done=True),
        ])

        explorer = _make_explorer(tmp_path, vlm)
        explorer.explore(max_steps=10)

        step0 = tmp_path / "output" / "steps" / "step_000.json"
        step1 = tmp_path / "output" / "steps" / "step_001.json"
        assert step0.exists()
        assert step1.exists()

        data = json.loads(step0.read_text())
        assert data["screen_label"] == "basic"
        assert "reasoning" in data
        assert "elements" in data

    def test_reasoning_in_step(self, tmp_path):
        """Step should contain reasoning field."""
        step = _step("basic", [_btn("1")])
        assert hasattr(step, "reasoning")
        assert step.reasoning == "test reasoning"

    def test_transitions_deduplicated(self, tmp_path):
        vlm = FakeVLMClient()
        vlm.set_responses([
            _step("basic", [_btn("1")], target="1"),
            _step("basic", [_btn("1")], is_new=False, target="1"),
            _step("basic", [_btn("1")], is_new=False, done=True),
        ])

        explorer = _make_explorer(tmp_path, vlm)
        graph = explorer.explore(max_steps=10)

        assert graph["total_transitions"] == 1


# -------------------------------------------------------------------
# _find_path BFS
# -------------------------------------------------------------------

class TestFindPath:
    def _screen(self) -> dict:
        return {"description": "", "elements": [], "buttons": [], "screenshot": ""}

    def test_path_to_self_is_empty(self, tmp_path):
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        explorer.screens["basic"] = self._screen()

        assert explorer._find_path("basic") == []

    def test_path_one_hop(self, tmp_path):
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        explorer.screens["basic"] = self._screen()
        explorer.screens["advanced"] = self._screen()
        explorer.transitions = [
            {"from": "basic", "action": "click_advanced", "to": "advanced"},
        ]

        assert explorer._find_path("advanced") == ["click_advanced"]

    def test_path_two_hops(self, tmp_path):
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        explorer.screens["A"] = self._screen()
        explorer.screens["B"] = self._screen()
        explorer.screens["C"] = self._screen()
        explorer.transitions = [
            {"from": "A", "action": "go_B", "to": "B"},
            {"from": "B", "action": "go_C", "to": "C"},
        ]

        assert explorer._find_path("C") == ["go_B", "go_C"]

    def test_unreachable_returns_none(self, tmp_path):
        vlm = FakeVLMClient()
        explorer = _make_explorer(tmp_path, vlm)
        explorer.screens["basic"] = self._screen()
        explorer.screens["isolated"] = self._screen()

        assert explorer._find_path("isolated") is None
