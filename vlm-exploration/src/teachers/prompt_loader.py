from __future__ import annotations

from pathlib import Path
from typing import Optional


class PromptLoader:
    """Load prompt templates from a directory by filename."""

    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir

    def load(self, name: str, *, fallback: Optional[str] = None) -> str:
        """
        Load a prompt file by name. If it does not exist and fallback is given,
        try the fallback name. Raises FileNotFoundError if neither exists.
        """
        path = self.prompts_dir / name
        if not path.exists() and fallback is not None:
            path = self.prompts_dir / fallback
        return path.read_text(encoding="utf-8")

    def load_optional(self, name: str) -> Optional[str]:
        """Load a prompt file, returning None if the file does not exist."""
        path = self.prompts_dir / name
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
