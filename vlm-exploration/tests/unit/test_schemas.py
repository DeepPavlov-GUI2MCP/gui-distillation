# tests/unit/test_schemas.py
"""
Unit tests for Pydantic schemas in src/skills/schemas.py

Covers:
- field validation (bounds, required fields)
- enum coercion / closed-set behavior
- action parameter requirements
- dataset vs runtime schemas

Note:
- These tests assume the updated schemas.py that exposes:
    BBox
    AtomicTask, AtomicTaskType
    GroundingInputRuntime, GroundingOutput
    ActionInputRuntime, ActionOutput, Action, ActionType
    StateInputRuntime, StateOutput, SemanticState, ChangeType
"""

import pytest
from pydantic import ValidationError

from src.skills.schemas import (
    BBox,
    AtomicTask,
    AtomicTaskType,
    GroundingInputRuntime,
    GroundingOutput,
    ActionInputRuntime,
    ActionOutput,
    Action,
    ActionType,
    StateInputRuntime,
    StateOutput,
    SemanticState,
    ChangeType,
)


# sample_screenshot fixture is defined in tests/conftest.py

# -----------------------------
# BBox
# -----------------------------
class TestBBox:
    def test_valid_bbox(self):
        bbox = BBox(x_min=0.1, y_min=0.2, x_max=0.3, y_max=0.4)
        assert bbox.x_min == 0.1
        assert bbox.area() == pytest.approx(0.04)  # (0.3-0.1)*(0.4-0.2)=0.2*0.2=0.04

    def test_invalid_range(self):
        with pytest.raises(ValidationError):
            BBox(x_min=-0.1, y_min=0.2, x_max=0.3, y_max=0.4)

    def test_invalid_order(self):
        with pytest.raises(ValidationError):
            BBox(x_min=0.5, y_min=0.2, x_max=0.3, y_max=0.4)


# -----------------------------
# AtomicTask
# -----------------------------
class TestAtomicTask:
    def test_valid_task_with_param(self):
        task = AtomicTask(task_type=AtomicTaskType.ENTER_NUMBER, value="42")
        assert task.task_type == AtomicTaskType.ENTER_NUMBER
        assert task.value == "42"

    def test_task_without_param_compute_ok(self):
        task = AtomicTask(task_type=AtomicTaskType.COMPUTE, value=None)
        assert task.task_type == AtomicTaskType.COMPUTE
        assert task.value is None

    def test_task_missing_required_value_fails(self):
        with pytest.raises(ValidationError):
            AtomicTask(task_type=AtomicTaskType.OPERATION, value=None)

    def test_invalid_task_type(self):
        with pytest.raises(ValidationError):
            AtomicTask(task_type="invalid_task", value=None)


# -----------------------------
# Grounding
# -----------------------------
class TestGroundingIO:
    def test_grounding_input_runtime(self, sample_screenshot):
        inp = GroundingInputRuntime(screenshot=sample_screenshot, instruction="кнопка 5")
        assert inp.instruction == "кнопка 5"

    def test_grounding_output_ok(self):
        out = GroundingOutput(
            bbox=BBox(x_min=0.35, y_min=0.50, x_max=0.40, y_max=0.55),
            confidence=0.98,
            element_type="digit_button",
        )
        assert out.confidence == 0.98
        assert out.element_type.value == "digit_button"

    def test_grounding_confidence_range(self):
        with pytest.raises(ValidationError):
            GroundingOutput(
                bbox=BBox(x_min=0.35, y_min=0.50, x_max=0.40, y_max=0.55),
                confidence=1.5,
                element_type="digit_button",
            )


# -----------------------------
# Action
# -----------------------------
class TestActionIO:
    def test_action_input_runtime(self, sample_screenshot):
        bbox = BBox(x_min=0.35, y_min=0.50, x_max=0.40, y_max=0.55)
        inp = ActionInputRuntime(
            screenshot=sample_screenshot,
            instruction="нажми 5",
            target_bbox=bbox,
            element_type="digit_button",
        )
        assert inp.instruction == "нажми 5"

    def test_action_output_click_ok(self):
        action = Action(
            action_type=ActionType.CLICK,
            target_bbox=BBox(x_min=0.35, y_min=0.50, x_max=0.40, y_max=0.55),
            button="left",
            clicks=1,
        )
        out = ActionOutput(action=action, confidence=0.99)
        assert out.action.action_type == ActionType.CLICK
        assert out.action.clicks == 1

    def test_action_output_hover_ok(self):
        action = Action(
            action_type=ActionType.HOVER,
            target_bbox=BBox(x_min=0.35, y_min=0.50, x_max=0.40, y_max=0.55),
        )
        out = ActionOutput(action=action, confidence=0.95)
        assert out.action.action_type == ActionType.HOVER

    def test_action_type_requires_text(self):
        with pytest.raises(ValidationError):
            Action(action_type=ActionType.TYPE, target_bbox=None, text=None)

    def test_action_press_requires_key(self):
        with pytest.raises(ValidationError):
            Action(action_type=ActionType.PRESS, target_bbox=None, key=None)

    def test_action_hotkey_requires_keys(self):
        with pytest.raises(ValidationError):
            Action(action_type=ActionType.HOTKEY, target_bbox=None, keys=[])


# -----------------------------
# State
# -----------------------------
class TestStateIO:
    def test_state_input_runtime(self, sample_screenshot):
        action = Action(
            action_type=ActionType.CLICK,
            target_bbox=BBox(x_min=0.35, y_min=0.50, x_max=0.40, y_max=0.55),
        )
        inp = StateInputRuntime(
            screenshot_before=sample_screenshot,
            action=action,
            screenshot_after=sample_screenshot,
        )
        assert inp.action.action_type == ActionType.CLICK

    def test_state_output_success(self):
        state = SemanticState(
            success=True,
            change_type=ChangeType.DISPLAY_UPDATED,
            display_before="0",
            display_after="5",
            description="На дисплее было 0, стало 5",
        )
        out = StateOutput(state=state, confidence=0.97)
        assert out.state.success is True
        assert out.state.change_type == ChangeType.DISPLAY_UPDATED

    def test_state_output_failure(self):
        state = SemanticState(
            success=False,
            change_type=ChangeType.ERROR_OCCURRED,
            display_before="5",
            display_after="Error",
            description="Деление на ноль",
        )
        out = StateOutput(state=state, confidence=0.95)
        assert out.state.success is False
        assert out.state.change_type == ChangeType.ERROR_OCCURRED
