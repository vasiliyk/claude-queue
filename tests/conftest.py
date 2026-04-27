"""Shared pytest configuration — loads claude-queue.py as the claude_queue module."""

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "claude_queue",
    Path(__file__).parent.parent / "claude-queue.py",
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["claude_queue"] = _module
_spec.loader.exec_module(_module)
