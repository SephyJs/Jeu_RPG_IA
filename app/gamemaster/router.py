from __future__ import annotations
import re
from typing import Iterable

_at_re = re.compile(r"^\s*@([A-Za-zÀ-ÖØ-öø-ÿ0-9_\-']+)\b")


def _clean_spaces(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _is_mention_boundary(ch: str) -> bool:
    return (not ch) or ch.isspace() or ch in ",;:.!?)]}"


def detect_target(user_text: str, *, preferred_names: Iterable[str] | None = None) -> str | None:
    text = str(user_text or "").lstrip()
    if not text.startswith("@"):
        return None

    payload = text[1:].lstrip()
    if not payload:
        return None

    if preferred_names:
        seen: set[str] = set()
        candidates: list[str] = []
        for raw in preferred_names:
            name = _clean_spaces(raw)
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(name)
        candidates.sort(key=len, reverse=True)

        payload_fold = payload.casefold()
        for name in candidates:
            name_fold = name.casefold()
            if not payload_fold.startswith(name_fold):
                continue
            if len(payload) == len(name) or _is_mention_boundary(payload[len(name)]):
                return name

    m = _at_re.match(text)
    return _clean_spaces(m.group(1)) if m else None
