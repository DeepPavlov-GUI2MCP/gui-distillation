from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, model_validator


# -------------------------------------------------------------------
# Canonical ID mappings
# -------------------------------------------------------------------

SYMBOL_ID_MAP = {
    "÷": "divide",
    "/": "divide",
    "×": "multiply",
    "*": "multiply",
    "+": "plus",
    "−": "minus",
    "-": "minus",
    "=": "equals",
    ".": "decimal",
}

DIGIT_ID_MAP = {str(i): f"digit_{i}" for i in range(10)}

ALIAS_ID_MAP = {
    "backspace": "backspace",
    "bksp": "backspace",
    "clear": "backspace",
    "display": "display",
}

BBox = Tuple[int, int, int, int]


def canonicalize_element_id(raw_id: str, raw_text: Optional[str]) -> str:
    """
    Convert unstable/symbol ids into canonical ids.

    Examples:
    - "+"  -> "plus"
    - "7"  -> "digit_7"
    - "÷"  -> "divide"
    """
    rid = (raw_id or "").strip()
    if not rid:
        return rid

    # direct symbol mapping
    if rid in SYMBOL_ID_MAP:
        return SYMBOL_ID_MAP[rid]

    # direct digit mapping
    if rid in DIGIT_ID_MAP:
        return DIGIT_ID_MAP[rid]

    # aliases
    low = rid.lower()
    if low in ALIAS_ID_MAP:
        return ALIAS_ID_MAP[low]

    # fallback to text if id is weak but text is informative
    if raw_text:
        t = raw_text.strip()
        if t in SYMBOL_ID_MAP:
            return SYMBOL_ID_MAP[t]
        if t in DIGIT_ID_MAP:
            return DIGIT_ID_MAP[t]

    return rid


# -------------------------------------------------------------------
# Observation schema
# -------------------------------------------------------------------

class ScreenInfo(BaseModel):
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class UIElement(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["button", "text_field"] = "button"
    text: Optional[str] = None
    supported_actions: List[Literal["click"]] = Field(default_factory=lambda: ["click"])
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @model_validator(mode="after")
    def _canonicalize(self) -> "UIElement":
        self.id = canonicalize_element_id(self.id, self.text)

        # Hard guard: symbol ids must not survive normalization
        if self.id in SYMBOL_ID_MAP:
            raise ValueError(f"Element id must not be a symbol: {self.id}")

        return self

class GroundedUIElement(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["button", "text_field"] = "button"
    text: Optional[str] = None
    bbox: Optional[BBox] = None
    supported_actions: List[Literal["click"]] = Field(default_factory=lambda: ["click"])
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @model_validator(mode="after")
    def _canonicalize(self) -> "GroundedUIElement":
        self.id = canonicalize_element_id(self.id, self.text)

        if self.id in SYMBOL_ID_MAP:
            raise ValueError(f"Element id must not be a symbol: {self.id}")

        if self.bbox is not None:
            x1, y1, x2, y2 = self.bbox
            if x1 < 0 or y1 < 0 or x2 < 0 or y2 < 0:
                raise ValueError(f"Negative bbox coordinates: {self.bbox}")
            if x2 <= x1 or y2 <= y1:
                raise ValueError(f"Invalid bbox: {self.bbox}")

        return self

class ObservationResponse(BaseModel):
    screen: ScreenInfo
    elements: List[UIElement] = Field(default_factory=list)
    notes: Optional[str] = None

class GroundedObservationResponse(BaseModel):
    screen: ScreenInfo
    elements: List[GroundedUIElement] = Field(default_factory=list)
    notes: Optional[str] = None

# -------------------------------------------------------------------
# Action schema
# -------------------------------------------------------------------

class ActionParameters(BaseModel):
    button: Optional[Literal["left", "right", "middle"]] = "left"
    clicks: Optional[int] = Field(default=1, ge=1, le=3)
    text: Optional[str] = None
    key: Optional[str] = None
    keys: Optional[List[str]] = None
    duration: Optional[float] = Field(default=None, ge=0.0)


class ActionProposal(BaseModel):
    action_type: Literal["click", "type", "press", "hotkey", "move_to"] = "click"
    coordinates: Optional[Tuple[int, int]] = None
    target_element_id: Optional[str] = None
    parameters: ActionParameters = Field(default_factory=ActionParameters)
    rationale: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @model_validator(mode="after")
    def _validate_and_canonicalize(self) -> "ActionProposal":
        if self.target_element_id:
            self.target_element_id = canonicalize_element_id(self.target_element_id, None)

        needs_target = {"click", "move_to"}
        if self.action_type in needs_target:
            if self.coordinates is None and self.target_element_id is None:
                raise ValueError(
                    f"action_type='{self.action_type}' requires either coordinates or target_element_id"
                )

        return self


# -------------------------------------------------------------------
# Delta schema
# -------------------------------------------------------------------

class SelfCheck(BaseModel):
    """Model self-assessment returned alongside the delta analysis."""
    action_visually_plausible: bool
    text_change_consistent_with_action: bool
    layout_changed: bool


class DeltaResponse(BaseModel):
    success: bool
    change_type: Literal[
        "no_change",
        "text_updated",
        "result_updated",
        "error_shown",
        "ui_changed",
        "unknown",
    ] = "unknown"
    description: str = Field(..., min_length=1)
    ui_state_changed: bool = False
    content_state_changed: bool = False
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    before_text: Optional[str] = None
    after_text: Optional[str] = None
    self_check: Optional[SelfCheck] = None
