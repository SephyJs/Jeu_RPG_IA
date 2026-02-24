from __future__ import annotations

from app.ui.state.game_state import GameState


_CONSUMABLE_BUFFS_FLAG = "active_consumable_buffs"
_STAT_KEYS = {
    "force",
    "intelligence",
    "magie",
    "defense",
    "sagesse",
    "agilite",
    "dexterite",
    "chance",
    "charisme",
}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _gm_flags(state: GameState) -> dict:
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    flags = state.gm_state.get("flags")
    if isinstance(flags, dict):
        return flags
    state.gm_state["flags"] = {}
    return state.gm_state["flags"]


def get_active_consumable_buffs(state: GameState) -> list[dict]:
    flags = _gm_flags(state)
    buffs_raw = flags.get(_CONSUMABLE_BUFFS_FLAG)
    if not isinstance(buffs_raw, list):
        flags[_CONSUMABLE_BUFFS_FLAG] = []
        return flags[_CONSUMABLE_BUFFS_FLAG]
    out: list[dict] = []
    for row in buffs_raw:
        if not isinstance(row, dict):
            continue
        stat = str(row.get("stat") or "").strip().casefold()
        value = _safe_int(row.get("value"), 0)
        turns = max(0, _safe_int(row.get("turns_remaining"), 0))
        if stat not in _STAT_KEYS or value == 0 or turns <= 0:
            continue
        out.append(
            {
                "stat": stat,
                "value": value,
                "turns_remaining": turns,
                "item_id": str(row.get("item_id") or "").strip().casefold(),
                "item_name": str(row.get("item_name") or "").strip()[:80],
            }
        )
    flags[_CONSUMABLE_BUFFS_FLAG] = out
    return out


def add_consumable_stat_buff(
    state: GameState,
    *,
    stat: str,
    value: int,
    duration_turns: int,
    item_id: str = "",
    item_name: str = "",
) -> dict | None:
    stat_key = str(stat or "").strip().casefold()
    bonus = _safe_int(value, 0)
    turns = max(1, _safe_int(duration_turns, 1))
    if stat_key not in _STAT_KEYS or bonus == 0:
        return None

    buffs = get_active_consumable_buffs(state)
    for buff in buffs:
        if (
            str(buff.get("stat") or "") == stat_key
            and _safe_int(buff.get("value"), 0) == bonus
            and str(buff.get("item_id") or "").strip().casefold() == str(item_id or "").strip().casefold()
        ):
            buff["turns_remaining"] = max(_safe_int(buff.get("turns_remaining"), 0), turns)
            return buff

    new_buff = {
        "stat": stat_key,
        "value": bonus,
        "turns_remaining": turns,
        "item_id": str(item_id or "").strip().casefold(),
        "item_name": str(item_name or "").strip()[:80],
    }
    buffs.append(new_buff)
    return new_buff


def get_consumable_stat_bonus_totals(state: GameState) -> dict[str, int]:
    totals: dict[str, int] = {}
    for buff in get_active_consumable_buffs(state):
        stat = str(buff.get("stat") or "").strip().casefold()
        value = _safe_int(buff.get("value"), 0)
        if stat not in _STAT_KEYS or value == 0:
            continue
        totals[stat] = totals.get(stat, 0) + value
    return totals


def tick_consumable_buffs(state: GameState) -> list[dict]:
    buffs = get_active_consumable_buffs(state)
    still_active: list[dict] = []
    expired: list[dict] = []
    for buff in buffs:
        updated = dict(buff)
        updated["turns_remaining"] = max(0, _safe_int(buff.get("turns_remaining"), 0) - 1)
        if _safe_int(updated.get("turns_remaining"), 0) <= 0:
            expired.append(updated)
        else:
            still_active.append(updated)
    flags = _gm_flags(state)
    flags[_CONSUMABLE_BUFFS_FLAG] = still_active
    return expired
