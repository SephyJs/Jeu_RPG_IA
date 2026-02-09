from __future__ import annotations
import re

_at_re = re.compile(r"^\s*@([A-Za-zÀ-ÖØ-öø-ÿ0-9_\-']+)\b")

def detect_target(user_text: str) -> str | None:
    m = _at_re.match(user_text or "")
    return m.group(1) if m else None
