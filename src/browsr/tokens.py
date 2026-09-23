from __future__ import annotations

import math


def estimate(text: str) -> int:
    """Conservative lightweight token estimate, with CJK counted per character."""
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return math.ceil(ascii_chars / 4 + non_ascii_chars)


def fits(text: str, max_tokens: int) -> bool:
    return estimate(text) <= max_tokens
