"""The Sanitizer: the first, cheapest guard (S-1). No model call: a length limit, a character
allow-list and a few unmistakable patterns. Everything subtler is the Judge's job.

The allow-list keeps every character the legitimate set uses (S-2): apostrophes, #, $, :, ;,
@ and ordinary punctuation, plus letters with accents and curly quotes. It leaves out the
characters that only code needs: < > { } [ ] ` | \\ = * ^ ~ and control characters."""

import re
from dataclasses import dataclass

# Our choice, not a THRESHOLDS row: the longest customer message in the gold sets is 73
# characters, so 500 is room for a real complaint and a cheap stop for a pasted payload.
MAX_CHARS = 500

_ALLOWED_PUNCTUATION = set(" \n!#$%&'()+,-./:;?@_\"’‘“”€£")

# Unmistakable shapes only: a quote or semicolon followed by SQL comment syntax.
_OBVIOUS = [
    (re.compile(r"['\";]\s*--"), "SQL comment after a quote or semicolon"),
]


@dataclass
class SanitizeResult:
    passed: bool
    detail: str


def sanitize(message: str) -> SanitizeResult:
    if len(message) > MAX_CHARS:
        return SanitizeResult(False, f"message too long ({len(message)} > {MAX_CHARS} characters)")
    for char in message:
        if not (char.isalnum() or char in _ALLOWED_PUNCTUATION):
            return SanitizeResult(False, f"character {char!r} not allowed")
    for pattern, label in _OBVIOUS:
        if pattern.search(message):
            return SanitizeResult(False, label)
    return SanitizeResult(True, f"clean: {len(message)} characters, all allowed")
