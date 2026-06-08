# tests/unit/test_teacher_schemas.py
"""Unit tests for teacher-facing Pydantic schemas (src/skills/teacher_schemas.py)."""

import pytest
from pydantic import ValidationError

from src.skills.teacher_schemas import (
    ActionParameters,
    ActionProposal,
    DeltaResponse,
    GroundedObservationResponse,
    GroundedUIElement,
    ObservationResponse,
    ScreenInfo,
    SelfCheck,
    UIElement,
    canonicalize_element_id,
)


# -------------------------------------------------------------------
# canonicalize_element_id
# -------------------------------------------------------------------
class TestCanonicalizeElementId:
    def test_symbol_plus(self):
        assert canonicalize_element_id("+", None) == "plus"

    def test_symbol_divide_unicode(self):
        assert canonicalize_element_id("÷", None) == "divide"

    def test_symbol_divide_slash(self):
        assert canonicalize_element_id("/", None) == "divide"

    def test_digit(self):
        assert canonicalize_element_id("7", None) == "digit_7"

    def test_alias_bksp(self):
        assert canonicalize_element_id("bksp", None) == "backspace"

    def test_alias_case_insensitive(self):
        assert canonicalize_element_id("CLEAR", None) == "backspace"

    def test_fallback_to_text_symbol(self):
        # id is unrecognised, text carries the symbol — text fallback kicks in
        assert canonicalize_element_id("btn_plus", "+") == "plus"
        assert canonicalize_element_id("unknown_btn", "+") == "plus"

    def test_passthrough_unknown_id(self):
        assert canonicalize_element_id("my_button", None) == "my_button"

    def test_empty_id_passthrough(self):
        assert canonicalize_element_id("", None) == ""


# -------------------------------------------------------------------
# UIElement
# -------------------------------------------------------------------
class TestUIElement:
    def test_digit_canonicalized(self):
        el = UIElement(id="5", text="5")
        assert el.id == "digit_5"

    def test_symbol_canonicalized(self):
        el = UIElement(id="+", text="+")
        assert el.id == "plus"

    def test_known_stable_id_passthrough(self):
        el = UIElement(id="plus", text="+")
        assert el.id == "plus"

    def test_symbol_id_normalized_to_canonical(self):
        # "=" maps to "equals" via canonicalization, so it passes validation
        el = UIElement(id="=", text=None)
        assert el.id == "equals"

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            UIElement(id="", text=None)


# -------------------------------------------------------------------
# GroundedUIElement
# -------------------------------------------------------------------
class TestGroundedUIElement:
    def test_valid_with_bbox(self):
        el = GroundedUIElement(id="plus", bbox=(10, 20, 50, 60))
        assert el.id == "plus"
        assert el.bbox == (10, 20, 50, 60)

    def test_valid_without_bbox(self):
        el = GroundedUIElement(id="digit_3")
        assert el.bbox is None

    def test_negative_bbox_raises(self):
        with pytest.raises(ValidationError):
            GroundedUIElement(id="plus", bbox=(-1, 20, 50, 60))

    def test_inverted_bbox_raises(self):
        with pytest.raises(ValidationError):
            GroundedUIElement(id="plus", bbox=(50, 20, 10, 60))

    def test_digit_id_canonicalized(self):
        el = GroundedUIElement(id="3")
        assert el.id == "digit_3"


# -------------------------------------------------------------------
# SelfCheck
# -------------------------------------------------------------------
class TestSelfCheck:
    def test_all_fields(self):
        sc = SelfCheck(
            action_visually_plausible=True,
            text_change_consistent_with_action=False,
            layout_changed=True,
        )
        assert sc.action_visually_plausible is True
        assert sc.layout_changed is True

    def test_missing_field_raises(self):
        with pytest.raises(ValidationError):
            SelfCheck(action_visually_plausible=True, text_change_consistent_with_action=True)


# -------------------------------------------------------------------
# DeltaResponse
# -------------------------------------------------------------------
class TestDeltaResponse:
    def test_minimal_valid(self):
        delta = DeltaResponse(
            success=True,
            change_type="text_updated",
            description="Display changed from 0 to 5",
        )
        assert delta.success is True
        assert delta.self_check is None

    def test_with_self_check(self):
        sc = SelfCheck(
            action_visually_plausible=True,
            text_change_consistent_with_action=True,
            layout_changed=False,
        )
        delta = DeltaResponse(
            success=True,
            change_type="text_updated",
            description="Display changed",
            self_check=sc,
        )
        assert delta.self_check.layout_changed is False

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError):
            DeltaResponse(success=True, change_type="unknown", description="")

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            DeltaResponse(success=True, change_type="unknown", description="ok", confidence=1.5)


# -------------------------------------------------------------------
# ActionProposal
# -------------------------------------------------------------------
class TestActionProposal:
    def test_click_with_coordinates(self):
        ap = ActionProposal(action_type="click", coordinates=(100, 200))
        assert ap.coordinates == (100, 200)

    def test_click_with_element_id(self):
        ap = ActionProposal(action_type="click", target_element_id="plus")
        assert ap.target_element_id == "plus"

    def test_click_without_target_raises(self):
        with pytest.raises(ValidationError):
            ActionProposal(action_type="click")

    def test_move_to_without_target_raises(self):
        with pytest.raises(ValidationError):
            ActionProposal(action_type="move_to")

    def test_type_action_no_target_required(self):
        # type does not require coordinates or target_element_id
        ap = ActionProposal(action_type="type", parameters=ActionParameters(text="42"))
        assert ap.action_type == "type"

    def test_target_element_id_canonicalized(self):
        ap = ActionProposal(action_type="click", target_element_id="5")
        assert ap.target_element_id == "digit_5"
