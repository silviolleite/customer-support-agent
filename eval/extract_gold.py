"""Copy the gold sets out of EVALS.md §3 into eval/gold/<set>.jsonl, verbatim.

Items are never edited (EVALS §3): each cell is copied as written, with only the Markdown
removed (surrounding backticks, escaped pipes). Each item also gets the user's email, looked
up from the seed in EVALS §2, so the runner can log in. Set sizes are checked against
THRESHOLDS §7, and the script fails rather than write a short set.

    .venv/bin/python -m eval.extract_gold
"""

import json
import re
import sys
from pathlib import Path

EVALS = Path("EVALS.md")
OUT = Path("eval/gold")

# section heading prefix -> (file name, field per column, expected size from THRESHOLDS §7)
SETS = {
    "### 3.1": ("attack", ["id", "message"], 30),
    "### 3.2": ("legit", ["id", "user", "message"], 30),
    "### 3.3": ("offtopic", ["id", "message"], 15),
    "### 3.4": ("probe", ["id", "user", "message", "leak_markers"], 10),
    "### 3.5": ("order", ["id", "user", "message", "must_contain", "expected_tools"], 12),
    "### 3.6": ("action", ["id", "user", "message", "action_type"], 8),
    "### 3.7": ("memory", ["id", "user", "plant", "ask", "keyword"], 10),
}
DEFAULT_USER = "alice"  # 3.1 and 3.3: "All items run as alice"
EITHER = ["get-order-status", "find-customer-orders"]  # 3.5: "either" means either tool


def cells(row: str) -> list[str]:
    """Split a Markdown table row on unescaped pipes and strip the Markdown from each cell."""
    parts = re.split(r"(?<!\\)\|", row.strip())[1:-1]
    out = []
    for part in parts:
        cell = part.strip().replace(r"\|", "|")
        if len(cell) >= 2 and cell.startswith("`") and cell.endswith("`"):
            cell = cell[1:-1]
        out.append(cell)
    return out


def seed_emails(text: str) -> dict[str, str]:
    """First name (lowercase) -> email, from the users INSERT in EVALS §2."""
    pairs = re.findall(r"\('([^']+@[^']+)',\s*'([^']+)'", text)
    return {name.split()[0].lower(): email for email, name in pairs}


def main() -> int:
    text = EVALS.read_text()
    emails = seed_emails(text)
    sections = re.split(r"(?m)^(?=### )", text)
    OUT.mkdir(parents=True, exist_ok=True)
    ok = True
    for prefix, (name, fields, expected) in SETS.items():
        section = next(s for s in sections if s.startswith(prefix))
        rows = [r for r in section.splitlines() if re.match(r"^\| [A-Z]\d{2} ", r)]
        items = []
        for row in rows:
            values = cells(row)
            if len(values) != len(fields):
                print(f"{name}: row has {len(values)} cells, expected {len(fields)}: {row}")
                return 1
            item = dict(zip(fields, values))
            item.setdefault("user", DEFAULT_USER)
            item["email"] = emails[item["user"].lower()]
            if name == "probe":
                item["leak_markers"] = [m.strip() for m in item["leak_markers"].split(", ")]
            if name == "order":
                tool = item["expected_tools"]
                item["expected_tools"] = EITHER if tool == "either" else [tool]
            items.append(item)
        status = "ok" if len(items) == expected else f"EXPECTED {expected}"
        ok &= len(items) == expected
        (OUT / f"{name}.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
        print(f"{name:9} {len(items):3} items  {status}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
