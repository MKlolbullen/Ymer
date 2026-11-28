"""Core logic: pure functions that compute statistics from a text string."""

import string
from collections import Counter
from dataclasses import dataclass, field

# ASCII punctuation plus common typographic quotes and dashes.
PUNCTUATION = string.punctuation + "\u2018\u2019\u201c\u201d\u2013\u2014\u2026\u00ab\u00bb"


@dataclass
class TextStats:
    words: int = 0
    lines: int = 0
    characters: int = 0
    top_words: list[tuple[str, int]] = field(default_factory=list)


def normalize_words(text: str) -> list[str]:
    """Split text on whitespace, lowercase, strip surrounding punctuation
    and drop tokens that end up empty."""
    tokens = (token.strip(PUNCTUATION).lower() for token in text.split())
    return [token for token in tokens if token]


def top_words(text: str, top: int) -> list[tuple[str, int]]:
    """Return the top most common words, case-insensitive.

    Sorted by count descending, then word ascending. If there are fewer
    than top distinct words, all of them are returned. A top of zero or
    less returns an empty list.
    """
    if top <= 0:
        return []
    counts = Counter(normalize_words(text))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:top]


def analyze(text: str, top: int = 5) -> TextStats:
    """Compute word, line and character counts and the most common words.

    Words are whitespace-separated tokens, lines are newline characters
    (like Unix wc) and characters is the length of the decoded text.
    """
    return TextStats(
        words=len(text.split()),
        lines=text.count("\n"),
        characters=len(text),
        top_words=top_words(text, top),
    )
