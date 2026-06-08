# src/skills/schemas.py
"""
Pydantic models for all four skills (Simplifier, Grounding, Action, State).

These schemas define the data contracts for the entire project.
They are designed to be:
- JSON-serializable for datasets (Phase 1–2)
- strict and validated (no silent coercions where it matters)
- decoupled from heavy runtime objects (PIL images are NOT stored in datasets)

Important design decision:
- For skill I/O schemas we support TWO representations of images:
  1) For runtime/inference: PIL Image objects (Image.Image) via *Runtime* schemas
  2) For datasets/on-disk: file paths (str) via *Dataset* schemas

Phase: 0.2 Skill Formalization
Target App: GNOME Calculator
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, ConfigDict, model_validator


# =========================================================
# 0. COMMON TYPES
# =========================================================

NormalizedFloat = float


class BBox(BaseModel):
    """
    Bounding box in normalized coordinates [0, 1].

    Convention:
      (x_min, y_min) = top-left corner
      (x_max, y_max) = bottom-right corner
    """

    x_min: NormalizedFloat = Field(..., ge=0.0, le=1.0, description="Left edge (normalized)")
    y_min: NormalizedFloat = Field(..., ge=0.0, le=1.0, description="Top edge (normalized)")
    x_max: NormalizedFloat = Field(..., ge=0.0, le=1.0, description="Right edge (normalized)")
    y_max: NormalizedFloat = Field(..., ge=0.0, le=1.0, description="Bottom edge (normalized)")

    @model_validator(mode="after")
    def _validate_order(self) -> "BBox":
        if self.x_max <= self.x_min:
            raise ValueError("x_max must be greater than x_min")
        if self.y_max <= self.y_min:
            raise ValueError("y_max must be greater than y_min")
        return self

    def area(self) -> float:
        """Area in normalized coordinates."""
        return (self.x_max - self.x_min) * (self.y_max - self.y_min)

    def to_absolute(self, img_width: int, img_height: int) -> Tuple[int, int, int, int]:
        """Convert to absolute pixel coordinates (x1, y1, x2, y2)."""
        x1 = int(round(self.x_min * img_width))
        y1 = int(round(self.y_min * img_height))
        x2 = int(round(self.x_max * img_width))
        y2 = int(round(self.y_max * img_height))
        return x1, y1, x2, y2

    @classmethod
    def from_list(cls, bbox: List[float]) -> "BBox":
        """Convenience constructor from [x_min, y_min, x_max, y_max]."""
        if len(bbox) != 4:
            raise ValueError("bbox list must have 4 elements: [x_min, y_min, x_max, y_max]")
        return cls(x_min=bbox[0], y_min=bbox[1], x_max=bbox[2], y_max=bbox[3])

    def to_list(self) -> List[float]:
        """Convert to [x_min, y_min, x_max, y_max]."""
        return [self.x_min, self.y_min, self.x_max, self.y_max]


# =========================================================
# 1. SKILL 0: SIMPLIFIER (Instruction -> Atomic plan)
# =========================================================

class AtomicTaskType(str, Enum):
    """
    Atomic task vocabulary (should match vocabularies/atomic_tasks.yaml).

    Note: We keep it as Enum to enforce closed-set behavior.
    If the vocabulary evolves, update this Enum in sync.
    """

    ENTER_NUMBER = "enter_number"
    ENTER_DIGIT = "enter_digit"
    OPERATION = "operation"
    UNARY = "unary"
    COMPUTE = "compute"
    CLEAR = "clear"
    CLEAR_ENTRY = "clear_entry"
    TOGGLE_SIGN = "toggle_sign"
    ADD_DECIMAL = "add_decimal"


class AtomicTask(BaseModel):
    """
    One atomic task.

    Examples:
      {"task_type": "enter_digit", "value": 5}
      {"task_type": "operation", "value": "+"}
      {"task_type": "compute", "value": null}
    """

    task_type: AtomicTaskType
    value: Optional[Union[str, float, int]] = Field(
        default=None,
        description="Task parameter (digit/number/op/etc.), if required by task_type",
    )

    @model_validator(mode="after")
    def _validate_value_requirements(self) -> "AtomicTask":
        needs_value = {
            AtomicTaskType.ENTER_NUMBER,
            AtomicTaskType.ENTER_DIGIT,
            AtomicTaskType.OPERATION,
            AtomicTaskType.UNARY,
        }
        if self.task_type in needs_value and self.value is None:
            raise ValueError(f"value is required for task_type={self.task_type}")
        return self


class SimplifierInput(BaseModel):
    instruction: str = Field(..., min_length=1, description="High-level instruction, e.g. '2+2'")


class SimplifierOutput(BaseModel):
    tasks: List[AtomicTask] = Field(..., min_length=1, description="Sequence of atomic tasks")
    confidence: float = Field(..., ge=0.0, le=1.0)


# =========================================================
# 2. SKILL A: GROUNDING (Screenshot + instruction -> bbox)
# =========================================================

class ElementType(str, Enum):
    """Calculator UI element types (extendable)."""

    DIGIT_BUTTON = "digit_button"
    OPERATION_BUTTON = "operation_button"
    EQUALS_BUTTON = "equals_button"
    CLEAR_BUTTON = "clear_button"
    CLEAR_ENTRY_BUTTON = "clear_entry_button"
    SIGN_BUTTON = "sign_button"
    DECIMAL_BUTTON = "decimal_button"
    DISPLAY = "display"
    UNKNOWN = "unknown"


# Runtime schema (PIL Image) – used during inference only
class GroundingInputRuntime(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    screenshot: Any = Field(..., description="PIL.Image.Image in runtime")
    instruction: str = Field(..., min_length=1, description="What to find, e.g. 'кнопка 5'")


# Dataset schema (path) – used for saved JSONL/Parquet
class GroundingInputDataset(BaseModel):
    screenshot_path: str = Field(..., min_length=1, description="Path to screenshot image on disk")
    instruction: str = Field(..., min_length=1)


class GroundingOutput(BaseModel):
    bbox: BBox
    confidence: float = Field(..., ge=0.0, le=1.0)
    element_type: Optional[ElementType] = None


# =========================================================
# 3. SKILL B: ACTION (Screenshot + instruction + bbox -> action)
# =========================================================

class ActionType(str, Enum):
    """
    Action types (should match vocabularies/action_types.yaml).

    Keep small/closed in Phase 0; can be extended later.
    """

    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    HOVER = "hover"
    TYPE = "type"
    PRESS = "press"
    HOTKEY = "hotkey"
    MOVE_TO = "move_to"


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class Action(BaseModel):
    """
    Action representation for the skill outputs (model-level).

    - target_bbox is always provided for GUI-oriented actions.
    - For typing/keypress actions, target_bbox may still be useful
      (e.g., focus a field), but can be optional.

    We model it as a single schema with optional parameters rather
    than a union of many action-specific schemas (Phase 0 simplicity).
    """

    action_type: ActionType
    target_bbox: Optional[BBox] = Field(
        default=None,
        description="Target element bbox (normalized). Optional for global actions.",
    )

    # Click-related
    button: Optional[MouseButton] = Field(default=MouseButton.LEFT)
    clicks: Optional[int] = Field(default=1, ge=1, le=3)

    # Move/hover
    duration: Optional[float] = Field(default=None, ge=0.0, description="Duration for move/hover/drag-like actions")

    # Keyboard / typing
    text: Optional[str] = Field(default=None, description="Text for TYPE actions")
    key: Optional[str] = Field(default=None, description="Key for PRESS actions (e.g. 'enter')")
    keys: Optional[List[str]] = Field(default=None, description="Keys for HOTKEY actions (e.g. ['ctrl','c'])")

    @model_validator(mode="after")
    def _validate_action_params(self) -> "Action":
        if self.action_type in {ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK, ActionType.HOVER, ActionType.MOVE_TO}:
            if self.target_bbox is None:
                raise ValueError(f"target_bbox is required for action_type={self.action_type}")

        if self.action_type == ActionType.TYPE and (self.text is None or self.text == ""):
            raise ValueError("text is required for action_type=type")

        if self.action_type == ActionType.PRESS and (self.key is None or self.key == ""):
            raise ValueError("key is required for action_type=press")

        if self.action_type == ActionType.HOTKEY:
            if not self.keys or not isinstance(self.keys, list):
                raise ValueError("keys (non-empty list) is required for action_type=hotkey")

        return self


# Runtime schema (PIL Image)
class ActionInputRuntime(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    screenshot: Any = Field(..., description="PIL.Image.Image in runtime")
    instruction: str = Field(..., min_length=1)
    target_bbox: BBox
    element_type: Optional[ElementType] = None


# Dataset schema (paths)
class ActionInputDataset(BaseModel):
    screenshot_path: str = Field(..., min_length=1)
    instruction: str = Field(..., min_length=1)
    target_bbox: BBox
    element_type: Optional[ElementType] = None


class ActionOutput(BaseModel):
    action: Action
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: Optional[str] = Field(default=None, description="Debug-only; should not be used for training targets")


# =========================================================
# 4. SKILL C: STATE (Before + action + after -> semantic state)
# =========================================================

class ChangeType(str, Enum):
    DISPLAY_UPDATED = "display_updated"
    ERROR_OCCURRED = "error_occurred"
    ERROR_CLEARED = "error_cleared"
    MODE_CHANGED = "mode_changed"
    NO_CHANGE = "no_change"
    UNEXPECTED_CHANGE = "unexpected_change"


ErrorType = Literal[
    "element_not_found",
    "no_reaction",
    "wrong_result",
    "unexpected_error",
]


class SemanticState(BaseModel):
    success: bool
    change_type: ChangeType
    description: str = Field(..., min_length=1)

    display_before: Optional[str] = None
    display_after: Optional[str] = None

    error_type: Optional[ErrorType] = None

    @model_validator(mode="after")
    def _validate_error_type(self) -> "SemanticState":
        if self.error_type is not None and self.success:
            raise ValueError("error_type must be None when success=True")
        return self


# Runtime schema (PIL Images)
class StateInputRuntime(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    screenshot_before: Any = Field(..., description="PIL.Image.Image in runtime")
    action: Action
    screenshot_after: Any = Field(..., description="PIL.Image.Image in runtime")


# Dataset schema (paths)
class StateInputDataset(BaseModel):
    screenshot_before_path: str = Field(..., min_length=1)
    action: Action
    screenshot_after_path: str = Field(..., min_length=1)


class StateOutput(BaseModel):
    state: SemanticState
    confidence: float = Field(..., ge=0.0, le=1.0)


# =========================================================
# 5. AUXILIARY MODELS (raw exploration artifacts / teacher outputs)
# =========================================================

class StepMetadata(BaseModel):
    """
    Metadata stored by automation.py for each step.

    NOTE: This should align with src/core/automation.py metadata.json structure.
    We keep it flexible by allowing extra fields.
    """

    model_config = ConfigDict(extra="allow")

    step_id: int
    timestamp: float
    app: str
    display: Optional[str] = None
    screen: Optional[Dict[str, int]] = None
    action: Dict[str, Any]
    hashes: Optional[Dict[str, Optional[str]]] = None
    changed: Optional[bool] = None


class TeacherInventoryItem(BaseModel):
    bbox: BBox
    element_type: ElementType
    text: Optional[str] = None
    functional_description: str = Field(..., min_length=1)
    supported_actions: List[ActionType] = Field(default_factory=list)


class TeacherInventory(BaseModel):
    items: List[TeacherInventoryItem]
    screen_id: str = Field(..., min_length=1)
