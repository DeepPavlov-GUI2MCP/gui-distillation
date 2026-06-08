# tests/conftest.py
import sys
from pathlib import Path

import pytest

# Add repo root to sys.path so imports like `from src...` work.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def sample_screenshot():
    """
    Minimal dummy object standing in for a PIL.Image.

    Runtime schemas use arbitrary_types_allowed=True, so any object works.
    Intentionally avoids a PIL dependency in unit tests.
    """
    return object()
