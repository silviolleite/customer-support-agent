"""PII masking: the Masker's only logic, deterministic so it can change nothing but PII (K-2).

What counts as PII (stage 8 decision, SPEC K-2): emails other than the customer's own, phone
numbers and card-like numbers. The customer's own email and addresses pass untouched.

Each match keeps its shape: letters and digits become '*', separators stay. Everything
outside a match is returned byte for byte, never re-cased or re-spaced (R-8)."""

import re
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# 13 to 19 digits, optionally grouped by spaces or dashes, not followed by a decimal part.
# Checked before phones, so a card is never counted as phone numbers.
_CARD = re.compile(r"(?<![\w$])(?:\d[ -]?){12,18}\d(?!\w|\.\d)")
# 555-0100, (555) 123-4567, +1 555 123 4567. Needs a separator before the last 4 digits,
# so order numbers, prices ($1,500.00) and ISO dates (2026-10-08) do not match.
_PHONE = re.compile(r"(?<![\w$])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])?\d{3,4}[ .-]\d{4}(?![\w])")

_LABELS = {"email": ("email address", "email addresses"),
           "phone": ("phone number", "phone numbers"),
           "card": ("card number", "card numbers")}


@dataclass
class MaskResult:
    text: str
    counts: dict[str, int] = field(default_factory=lambda: {"email": 0, "phone": 0, "card": 0})

    @property
    def detail(self) -> str:
        """What changed, in words (K-3): "masked 1 phone number" or "nothing to mask"."""
        parts = [f"{n} {_LABELS[kind][n != 1]}" for kind, n in self.counts.items() if n]
        return f"masked {', '.join(parts)}" if parts else "nothing to mask"


def _shape(match: str) -> str:
    return "".join("*" if char.isalnum() else char for char in match)


def mask(text: str, own_email: str) -> MaskResult:
    result = MaskResult(text)

    def replace(kind: str, pattern: re.Pattern, keep=lambda m: False) -> None:
        def sub(match: re.Match) -> str:
            if keep(match.group(0)):
                return match.group(0)
            result.counts[kind] += 1
            return _shape(match.group(0))
        result.text = pattern.sub(sub, result.text)

    replace("email", _EMAIL, keep=lambda m: m.lower() == own_email.lower())
    replace("card", _CARD)
    replace("phone", _PHONE)
    return result
