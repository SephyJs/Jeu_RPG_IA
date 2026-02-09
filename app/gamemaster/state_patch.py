from __future__ import annotations

def apply_patch(state: dict, patch: dict) -> None:
    """
    Patch très simple:
    - state["flags"][k]=v si patch["flags"] existe
    - state["vars"][k]=v si patch["vars"] existe
    - state["location"]=... si patch["location"] existe
    """
    if not patch:
        return

    flags = patch.get("flags")
    if isinstance(flags, dict):
        state.setdefault("flags", {}).update(flags)

    vars_ = patch.get("vars")
    if isinstance(vars_, dict):
        state.setdefault("vars", {}).update(vars_)

    if "location" in patch:
        state["location"] = patch["location"]
