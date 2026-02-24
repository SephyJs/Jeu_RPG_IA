from __future__ import annotations

import re


_KEY_RE = re.compile(r"^[a-zA-Z0-9_:-]{1,80}$")
_MAX_TEXT_LEN = 180
_MAX_MAP_ITEMS = 48
_ALLOWED_LOCATION_FIELDS = ("location", "location_id", "map_anchor")


def _sanitize_scalar(value: object) -> object | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(-10_000, min(10_000, value))
    if isinstance(value, float):
        if value != value:  # NaN guard
            return None
        return max(-10_000.0, min(10_000.0, value))
    if isinstance(value, str):
        text = " ".join(value.split()).strip()
        if not text:
            return ""
        return text[:_MAX_TEXT_LEN]
    return None


def _sanitize_patch_map(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, object] = {}
    for key, value in raw.items():
        k = str(key or "").strip()
        if not _KEY_RE.fullmatch(k):
            continue
        sanitized = _sanitize_scalar(value)
        if sanitized is None:
            continue
        out[k] = sanitized
        if len(out) >= _MAX_MAP_ITEMS:
            break
    return out


def sanitize_state_patch(patch: object) -> dict:
    if not isinstance(patch, dict):
        return {}

    out: dict[str, object] = {}
    flags = _sanitize_patch_map(patch.get("flags"))
    vars_ = _sanitize_patch_map(patch.get("vars"))
    if flags:
        out["flags"] = flags
    if vars_:
        out["vars"] = vars_

    for field in _ALLOWED_LOCATION_FIELDS:
        sanitized = _sanitize_scalar(patch.get(field))
        if isinstance(sanitized, str) and sanitized:
            out[field] = sanitized

    return out


def apply_patch(state: dict, patch: dict) -> None:
    """
    Patch sécurisé:
    - state["flags"][k]=v si patch["flags"] existe
    - state["vars"][k]=v si patch["vars"] existe
    - state["location"/"location_id"/"map_anchor"]=... si valeurs valides
    """
    normalized = sanitize_state_patch(patch)
    if not normalized:
        return

    flags = normalized.get("flags")
    if isinstance(flags, dict):
        state.setdefault("flags", {}).update(flags)

    vars_ = normalized.get("vars")
    if isinstance(vars_, dict):
        state.setdefault("vars", {}).update(vars_)

    for field in _ALLOWED_LOCATION_FIELDS:
        if field in normalized:
            state[field] = normalized[field]
