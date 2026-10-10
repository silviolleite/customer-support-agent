"""The Judge's verdict on the wire (J-3): an explicit allow or block with a reason, never an
echo of the input. Shared by the Judge service and the pipeline that parses its reply."""

import json
from typing import Literal

from pydantic import BaseModel


class Verdict(BaseModel):
    verdict: Literal["allow", "block"]
    reason: str


def parse_verdict(text: str) -> Verdict:
    """Anything that is not exactly a Verdict raises ValueError, and the turn errors (P-4)."""
    return Verdict.model_validate(json.loads(text))
