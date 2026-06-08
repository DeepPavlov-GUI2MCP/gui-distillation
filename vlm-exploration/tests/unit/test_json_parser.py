# tests/unit/test_json_parser.py
"""Unit tests for RobustJSONParser."""

import pytest

from src.teachers.json_parser import RobustJSONParser


@pytest.fixture()
def parser() -> RobustJSONParser:
    return RobustJSONParser()


class TestDirectMode:
    def test_plain_json_object(self, parser):
        result = parser.parse('{"key": "value"}')
        assert result.ok
        assert result.mode == "direct"
        assert result.data == {"key": "value"}

    def test_plain_json_array(self, parser):
        result = parser.parse('[1, 2, 3]')
        assert result.ok
        assert result.mode == "direct"
        assert result.data == [1, 2, 3]

    def test_trailing_comma_cleaned(self, parser):
        result = parser.parse('{"a": 1,}')
        assert result.ok
        assert result.data == {"a": 1}

    def test_python_true_false_none_normalized(self, parser):
        result = parser.parse('{"x": True, "y": False, "z": None}')
        assert result.ok
        assert result.data == {"x": True, "y": False, "z": None}

    def test_nested_object(self, parser):
        result = parser.parse('{"outer": {"inner": 42}}')
        assert result.ok
        assert result.data["outer"]["inner"] == 42


class TestFencedMode:
    def test_json_fenced_block(self, parser):
        text = 'Here is the result:\n```json\n{"status": "ok"}\n```'
        result = parser.parse(text)
        assert result.ok
        assert result.mode == "fenced"
        assert result.data == {"status": "ok"}

    def test_plain_fenced_block(self, parser):
        text = "```\n[1, 2, 3]\n```"
        result = parser.parse(text)
        assert result.ok
        assert result.mode == "fenced"
        assert result.data == [1, 2, 3]

    def test_fenced_with_trailing_comma(self, parser):
        text = "```json\n{\"a\": 1,}\n```"
        result = parser.parse(text)
        assert result.ok
        assert result.data == {"a": 1}


class TestFirstObjectMode:
    def test_json_embedded_in_prose(self, parser):
        text = 'The answer is {"value": 99} and nothing else.'
        result = parser.parse(text)
        assert result.ok
        assert result.mode == "first_object"
        assert result.data == {"value": 99}

    def test_nested_json_in_prose(self, parser):
        text = 'Output: {"outer": {"inner": [1, 2]}} done.'
        result = parser.parse(text)
        assert result.ok
        assert result.data == {"outer": {"inner": [1, 2]}}

    def test_json_with_quoted_braces_in_string(self, parser):
        # Braces inside a string must not confuse the bracket counter.
        text = 'prefix {"key": "value with } brace"} suffix'
        result = parser.parse(text)
        assert result.ok
        assert result.data == {"key": "value with } brace"}


class TestFailureCases:
    def test_no_json_returns_not_ok(self, parser):
        result = parser.parse("This is plain text with no JSON.")
        assert not result.ok
        assert result.mode == "none"

    def test_empty_string_returns_not_ok(self, parser):
        result = parser.parse("")
        assert not result.ok

    def test_malformed_json_returns_not_ok(self, parser):
        result = parser.parse("{key: value}")
        assert not result.ok
