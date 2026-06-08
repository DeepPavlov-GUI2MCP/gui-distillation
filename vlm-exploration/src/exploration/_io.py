"""Shared I/O helpers for evaluate scripts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import yaml

JsonDict = Dict[str, Any]


def load_yaml(path: Path) -> JsonDict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_json(path: Path) -> JsonDict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
