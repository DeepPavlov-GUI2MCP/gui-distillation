from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional, Union

JsonValue = Union[dict, list, str, int, float, bool, None]


@dataclass
class ParseResult:
    ok: bool
    data: Optional[JsonValue]
    mode: str  # direct | fenced | first_object | none
    error: Optional[str] = None


class RobustJSONParser:
    """
    direct -> fenced -> first_object
    + небольшой cleanup (trailing commas)
    """

    FENCED_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

    @staticmethod
    def _cleanup(s: str) -> str:
        s = s.strip().lstrip("\ufeff")
        s = re.sub(r",\s*([}\]])", r"\1", s)   # trailing commas
        s = re.sub(r"\bTrue\b", "true", s)      # Python bool → JSON
        s = re.sub(r"\bFalse\b", "false", s)
        s = re.sub(r"\bNone\b", "null", s)      # Python None → JSON null
        return s

    @staticmethod
    def _extract_first_json(text: str) -> Optional[str]:
        """
        Find the first complete JSON object or array using bracket counting.
        Handles nested structures and quoted strings correctly.
        Returns the raw substring, or None if no complete structure is found.
        """
        pairs = {"{": "}", "[": "]"}
        first_idx = -1
        opener = ""
        for ch in ("{", "["):
            idx = text.find(ch)
            if idx != -1 and (first_idx == -1 or idx < first_idx):
                first_idx = idx
                opener = ch
        if not opener:
            return None

        closer = pairs[opener]
        depth = 0
        in_string = False
        escape_next = False
        for i in range(first_idx, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[first_idx : i + 1]
        return None

    def parse(self, text: str) -> ParseResult:
        raw = text.strip()

        # 1) direct
        try:
            return ParseResult(ok=True, data=json.loads(self._cleanup(raw)), mode="direct")
        except json.JSONDecodeError:
            pass

        # 2) fenced
        m = self.FENCED_RE.search(raw)
        if m:
            candidate = self._cleanup(m.group(1))
            try:
                return ParseResult(ok=True, data=json.loads(candidate), mode="fenced")
            except json.JSONDecodeError as e:
                fenced_err = str(e)
        else:
            fenced_err = None

        # 3) first complete object/array (bracket counting)
        extracted = self._extract_first_json(raw)
        if extracted is not None:
            candidate = self._cleanup(extracted)
            try:
                return ParseResult(ok=True, data=json.loads(candidate), mode="first_object")
            except json.JSONDecodeError as e:
                return ParseResult(ok=False, data=None, mode="first_object", error=str(e))

        return ParseResult(ok=False, data=None, mode="none", error=fenced_err or "No JSON found")
