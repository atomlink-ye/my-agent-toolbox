"""Shared Han-run and bigram helpers for Agent Memory's derived CJK index."""

from __future__ import annotations

import re
from collections.abc import Iterable

CJK_INDEX_NAME = "han-bigram"
CJK_INDEX_VERSION = 1

# Unicode Han ideographs, including compatibility and supplementary extensions.
_HAN_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2EBEF),
    (0x2F800, 0x2FA1F),
    (0x30000, 0x323AF),
)
_HAN_RUN_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    "\U00020000-\U0002ebef\U0002f800-\U0002fa1f\U00030000-\U000323af]+"
)


def is_han(character: str) -> bool:
    codepoint = ord(character)
    return any(start <= codepoint <= end for start, end in _HAN_RANGES)


def han_runs(text: str) -> list[str]:
    """Split text into contiguous Han runs without crossing other characters."""
    return _HAN_RUN_RE.findall(text)


def han_bigrams(fields: Iterable[str]) -> list[str]:
    """Return stable, de-duplicated bigrams without crossing field/run boundaries."""
    seen: set[str] = set()
    grams: list[str] = []
    for field in fields:
        for run in han_runs(field):
            for index in range(len(run) - 1):
                gram = run[index : index + 2]
                if gram not in seen:
                    seen.add(gram)
                    grams.append(gram)
    return grams
