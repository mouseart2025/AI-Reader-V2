"""Prompt Registry — unified prompt loading with protection support.

Development: loads from plain text files in prompts/ directory.
Desktop build: loads from compiled module (_compiled_prompts.py) with obfuscation.

Usage:
    from src.extraction.prompt_registry import get_prompt
    text = get_prompt("extraction_system")  # loads extraction_system.txt
    examples = get_prompt("extraction_examples")  # loads extraction_examples.json
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Cache for compiled prompts (loaded once)
_compiled: dict[str, str] | None = None


def _load_compiled() -> dict[str, str] | None:
    """Try to load compiled prompts module (used in sidecar builds)."""
    global _compiled
    if _compiled is not None:
        return _compiled
    try:
        from src.extraction._compiled_prompts import PROMPTS
        _compiled = PROMPTS
        logger.info("Loaded compiled prompts (%d entries)", len(PROMPTS))
        return _compiled
    except ImportError:
        _compiled = {}  # empty dict = not available, use file fallback
        return None


@lru_cache(maxsize=32)
def get_prompt(name: str) -> str:
    """Load a prompt by name (without extension).

    Resolution order:
    1. Compiled module (_compiled_prompts.PROMPTS) — for sidecar builds
    2. File on disk (prompts/{name}.txt or .json) — for development

    Args:
        name: Prompt name without extension (e.g., "extraction_system")

    Returns:
        Prompt text content.

    Raises:
        FileNotFoundError: If prompt not found in any source.
    """
    # Try compiled source first
    compiled = _load_compiled()
    if compiled and name in compiled:
        return compiled[name]

    # Fallback: load from file
    for ext in (".txt", ".json"):
        path = _PROMPTS_DIR / f"{name}{ext}"
        if path.exists():
            return path.read_text(encoding="utf-8")

    raise FileNotFoundError(f"Prompt not found: {name}")


def get_prompt_json(name: str) -> list | dict:
    """Load a prompt as parsed JSON."""
    return json.loads(get_prompt(name))


def list_prompts() -> list[str]:
    """List all available prompt names."""
    names = set()

    # From compiled
    compiled = _load_compiled()
    if compiled:
        names.update(compiled.keys())

    # From files
    if _PROMPTS_DIR.exists():
        for f in _PROMPTS_DIR.iterdir():
            if f.suffix in (".txt", ".json"):
                names.add(f.stem)

    return sorted(names)
