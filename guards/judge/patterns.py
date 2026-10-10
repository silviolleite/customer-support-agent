"""The Judge's deterministic pattern scan (J-2). Stage 6 decision: a match blocks on its own,
without the model, so known attack shapes are rejected in milliseconds.

The cost of that decision is that a pattern which also matches a real customer blocks them
with no second opinion. So every pattern needs SQL or code *structure*, never a bare word:
"drop the gift wrap", "select the fastest shipping", "delete my old address", "ignore my
last message" and "O'Brien St" are all in the legitimate set and must not match."""

import re

_TABLES = r"(users|customer_orders|actions_log)"

PATTERNS: list[tuple[str, re.Pattern]] = [
    # SQL injection: statements, tautologies, stacked queries, our own table names.
    ("sql_injection", re.compile(r"\b(drop|truncate|alter)\s+table\b", re.I)),
    ("sql_injection", re.compile(r"\bunion\s+(all\s+)?select\b", re.I)),
    ("sql_injection", re.compile(r"\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b", re.I)),
    ("sql_injection", re.compile(rf"\b(from|into|update|table|join)\s+{_TABLES}\b", re.I)),
    ("sql_injection", re.compile(r"'\s*(or|and)\s+'?\w+'?\s*=\s*'?\w+", re.I)),
    ("sql_injection", re.compile(r"\b\d+\s+or\s+\d+\s*=\s*\d+", re.I)),
    ("sql_injection", re.compile(r";\s*(drop|delete|update|insert|select|truncate|alter)\b", re.I)),
    ("sql_injection", re.compile(r"['\";]\s*--")),
    # Markup and script injection.
    ("markup", re.compile(r"<\s*/?\s*(script|iframe|img|svg|object|embed|link|style)\b", re.I)),
    ("markup", re.compile(r"\bon[a-z]+\s*=", re.I)),
    ("markup", re.compile(r"\bjavascript\s*:", re.I)),
    # Template injection.
    ("template_injection", re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\$\{.*?\}")),
    # Shell and path traversal.
    ("shell", re.compile(r"\$\([^)]*\)|`[^`]+`")),
    ("shell", re.compile(r"(^|[;&|]\s*)(cat|rm|ls|curl|wget|bash|sh|nc)\s", re.I)),
    ("path_traversal", re.compile(r"\.\./|/etc/(passwd|shadow)\b")),
    # Prompt injection and prompt extraction phrases.
    ("prompt_injection", re.compile(r"\bignore\s+(all\s+)?(the\s+)?(previous|prior|above)\b", re.I)),
    ("prompt_injection", re.compile(r"\bforget\s+(all\s+)?your\s+(rules|instructions)\b", re.I)),
    ("prompt_injection", re.compile(r"\byou\s+are\s+now\b", re.I)),
    ("prompt_injection", re.compile(r"(^|\n)\s*(system|developer)\s*:", re.I)),
    ("prompt_injection", re.compile(r"#+\s*new\s+instructions\b", re.I)),
    ("prompt_injection", re.compile(r"\b(security\s+judge|guardrail)\s+(has\s+)?(approved|allowed)\b", re.I)),
    ("prompt_extraction", re.compile(r"\b(system|developer)\s+prompt\b", re.I)),
    ("prompt_extraction", re.compile(
        r"\b(print|repeat|reveal|show|quote|output)\b.{0,30}\b(instructions|everything\s+above)\b", re.I)),
]


def scan(message: str) -> list[dict]:
    """Every pattern the message matches, as {category, match}. Empty means no known shape."""
    hits = []
    for category, pattern in PATTERNS:
        if found := pattern.search(message):
            hits.append({"category": category, "match": found.group(0).strip()})
    return hits
