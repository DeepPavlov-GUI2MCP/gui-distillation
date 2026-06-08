"""Unit tests for src/exploration/metrics.py."""

import json
from pathlib import Path

import pytest

from src.exploration.metrics import compute_metrics, deduplicate_graph, _count_unique_elements


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _graph(
    screens: dict | None = None,
    total_steps: int = 10,
    total_screens: int | None = None,
    usage: dict | None = None,
    model: str | None = None,
) -> dict:
    screens = screens or {}
    return {
        "total_steps": total_steps,
        "total_screens": total_screens if total_screens is not None else len(screens),
        "screens": screens,
        "usage": usage,
        "model": model,
    }


def _screen_with_elements(elements: list[dict]) -> dict:
    return {"elements": elements, "buttons": [], "description": ""}


def _screen_with_buttons(buttons: list[str]) -> dict:
    return {"buttons": buttons, "description": ""}


# -------------------------------------------------------------------
# _count_unique_elements
# -------------------------------------------------------------------

class TestCountUniqueElements:
    def test_new_style_elements(self):
        g = _graph(screens={
            "basic": _screen_with_elements([
                {"name": "1", "type": "button"},
                {"name": "2", "type": "button"},
                {"name": "Display", "type": "label"},
            ]),
            "advanced": _screen_with_elements([
                {"name": "1", "type": "button"},  # duplicate from basic
                {"name": "sin", "type": "button"},
                {"name": "Degrees", "type": "dropdown"},
            ]),
        })
        # unique: (1,button), (2,button), (Display,label), (sin,button), (Degrees,dropdown)
        assert _count_unique_elements(g) == 5

    def test_old_style_buttons_only(self):
        g = _graph(screens={
            "basic": _screen_with_buttons(["1", "2", "+"]),
            "adv": _screen_with_buttons(["1", "sin"]),
        })
        # unique: 1, 2, +, sin = 4
        assert _count_unique_elements(g) == 4

    def test_empty_graph(self):
        assert _count_unique_elements(_graph(screens={})) == 0

    def test_same_name_different_type(self):
        g = _graph(screens={
            "s": _screen_with_elements([
                {"name": "Angle units", "type": "dropdown"},
                {"name": "Angle units", "type": "label"},
            ]),
        })
        assert _count_unique_elements(g) == 2


# -------------------------------------------------------------------
# compute_metrics
# -------------------------------------------------------------------

class TestComputeMetrics:
    def test_basic(self):
        g = _graph(
            screens={
                "s1": _screen_with_elements([
                    {"name": "1", "type": "button"},
                    {"name": "2", "type": "button"},
                ]),
                "s2": _screen_with_elements([
                    {"name": "sin", "type": "button"},
                ]),
            },
            total_steps=10,
            usage={"tokens_input": 5000, "tokens_output": 1000, "tokens_total": 6000},
        )
        m = compute_metrics(g)

        assert m["total_screens"] == 2
        assert m["total_steps"] == 10
        assert m["unique_elements"] == 3
        assert m["discovery_rate"] == 0.2  # 2/10
        assert m["elements_per_step"] == 0.3  # 3/10
        assert m["elements_per_1k_tokens"] == 0.5  # 3/6

    def test_no_tokens(self):
        g = _graph(
            screens={"s": _screen_with_buttons(["a", "b"])},
            total_steps=5,
        )
        m = compute_metrics(g)

        assert m["elements_per_1k_tokens"] is None
        assert m["cost_usd"] is None
        assert m["elements_per_dollar"] is None

    def test_zero_steps(self):
        g = _graph(screens={}, total_steps=0)
        m = compute_metrics(g)

        assert m["discovery_rate"] == 0.0
        assert m["elements_per_step"] == 0.0

    def test_cost_with_known_model(self):
        g = _graph(
            screens={"s": _screen_with_buttons(["a"] * 100)},
            total_steps=10,
            total_screens=1,
            usage={"tokens_input": 100_000, "tokens_output": 10_000, "tokens_total": 110_000},
            model="gpt-5.4-mini",
        )
        m = compute_metrics(g)

        # cost = (100000 * 1.10 + 10000 * 4.40) / 1M = (110000 + 44000) / 1M = 0.154
        assert m["cost_usd"] == pytest.approx(0.154, abs=0.001)
        assert m["elements_per_dollar"] is not None
        assert m["elements_per_dollar"] > 0

    def test_unknown_model_no_cost(self):
        g = _graph(
            screens={"s": _screen_with_buttons(["a"])},
            total_steps=1,
            usage={"tokens_input": 1000, "tokens_output": 500, "tokens_total": 1500},
            model="some-unknown-model",
        )
        m = compute_metrics(g)

        assert m["cost_usd"] is None
        assert m["elements_per_dollar"] is None
        assert m["elements_per_1k_tokens"] is not None


# -------------------------------------------------------------------
# deduplicate_graph
# -------------------------------------------------------------------

def _write_test_png(path: Path, pattern: str = "black") -> None:
    """Write a 64x64 test PNG. pattern: 'black', 'white', 'checker'."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img = Image.new("RGB", (64, 64), (0, 0, 0))
    if pattern == "white":
        img = Image.new("RGB", (64, 64), (255, 255, 255))
    elif pattern == "checker":
        pixels = img.load()
        for x in range(64):
            for y in range(64):
                if (x // 8 + y // 8) % 2:
                    pixels[x, y] = (255, 255, 255)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


class TestDeduplicateGraph:
    def test_identical_screenshots_merged(self, tmp_path):
        ss_dir = tmp_path / "screenshots"
        _write_test_png(ss_dir / "a.png", "black")
        _write_test_png(ss_dir / "b.png", "black")  # identical

        g = {
            "screens": {
                "screen_a": {
                    "elements": [{"name": "1", "type": "button"}],
                    "buttons": ["1"],
                    "screenshot": "screenshots/a.png",
                    "description": "A",
                },
                "screen_b": {
                    "elements": [{"name": "2", "type": "button"}],
                    "buttons": ["2"],
                    "screenshot": "screenshots/b.png",
                    "description": "B",
                },
            },
            "transitions": [
                {"from": "screen_a", "action": "click", "to": "screen_b"},
            ],
            "total_screens": 2,
            "total_transitions": 1,
        }

        result = deduplicate_graph(g, ss_dir, threshold=5)

        assert result["total_screens"] == 1
        # Elements merged
        merged = list(result["screens"].values())[0]
        names = {e["name"] for e in merged["elements"]}
        assert names == {"1", "2"}
        # Transition remapped
        assert result["transitions"][0]["to"] == result["transitions"][0]["from"]

    def test_different_screenshots_not_merged(self, tmp_path):
        ss_dir = tmp_path / "screenshots"
        _write_test_png(ss_dir / "a.png", "black")
        _write_test_png(ss_dir / "b.png", "checker")  # very different

        g = {
            "screens": {
                "s1": {
                    "elements": [{"name": "x", "type": "button"}],
                    "buttons": ["x"],
                    "screenshot": "screenshots/a.png",
                    "description": "",
                },
                "s2": {
                    "elements": [{"name": "y", "type": "button"}],
                    "buttons": ["y"],
                    "screenshot": "screenshots/b.png",
                    "description": "",
                },
            },
            "transitions": [],
            "total_screens": 2,
            "total_transitions": 0,
        }

        result = deduplicate_graph(g, ss_dir, threshold=5)

        assert result["total_screens"] == 2

    def test_single_screen_unchanged(self, tmp_path):
        ss_dir = tmp_path / "screenshots"
        _write_test_png(ss_dir / "a.png", "black")

        g = {
            "screens": {
                "only": {
                    "elements": [{"name": "x", "type": "button"}],
                    "buttons": ["x"],
                    "screenshot": "screenshots/a.png",
                    "description": "",
                },
            },
            "transitions": [],
            "total_screens": 1,
            "total_transitions": 0,
        }

        result = deduplicate_graph(g, ss_dir, threshold=5)
        assert result["total_screens"] == 1

    def test_no_screenshots_returns_unchanged(self, tmp_path):
        ss_dir = tmp_path / "empty"
        g = {
            "screens": {"a": {"elements": [], "screenshot": "nope.png", "description": ""}},
            "transitions": [],
            "total_screens": 1,
        }
        result = deduplicate_graph(g, ss_dir, threshold=5)
        assert result["total_screens"] == 1
