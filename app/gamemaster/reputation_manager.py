from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from app.ui.state.game_state import GameState


REPUTATION_MIN = -100
REPUTATION_MAX = 100
REPUTATION_LOG_MAX_ITEMS = 200
_FACTION_NAME_RE = re.compile(r"[^a-zA-Z0-9 _'’-]+")
_DEFAULT_RULES_PATH = Path("data/world/reputation_rules.json")
_REPUTATION_TIERS: list[tuple[int, str]] = [
    (-100, "haine"),
    (-60, "hostile"),
    (-20, "mefiant"),
    (20, "neutre"),
    (50, "respecte"),
    (75, "honore"),
]

_DEFAULT_REPUTATION_RULES = {
    "trade": {
        "merchant_faction": "Marchands",
        "merchant_delta_small": 1,
        "merchant_delta_large": 2,
        "merchant_large_qty_threshold": 2,
        "charity_faction": "Peuple",
        "charity_delta_small": 2,
        "charity_delta_large": 3,
        "charity_large_qty_threshold": 2,
        "generic_give_delta": 1,
    },
    "quest": {
        "default_faction": "Habitants",
        "default_delta": 2,
        "objective_deltas": {
            "clear_dungeon_floors": 3,
            "talk_to_npc": 3,
            "reach_anchor": 3,
            "explore_locations": 3,
            "collect_gold": 2,
            "send_messages": 2,
        },
        "objective_factions": {
            "clear_dungeon_floors": "Aventuriers",
            "talk_to_npc": "Aventuriers",
            "reach_anchor": "Explorateurs",
            "explore_locations": "Explorateurs",
            "collect_gold": "Marchands",
            "send_messages": "Habitants",
        },
    },
    "dungeon": {
        "faction": "Aventuriers",
        "default_delta": 1,
        "high_floor_delta": 2,
        "high_floor_threshold": 10,
        "boss_delta": 3,
        "eligible_event_types": ["monster", "mimic", "boss"],
    },
}

_RULES_CACHE: dict[str, tuple[int | None, dict]] = {}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_reputation(value: int) -> int:
    return max(REPUTATION_MIN, min(REPUTATION_MAX, int(value)))


def _safe_range_int(value: object, default: int, *, lower: int, upper: int) -> int:
    raw = _safe_int(value, default)
    return max(lower, min(upper, int(raw)))


def _default_rules_copy() -> dict:
    return json.loads(json.dumps(_DEFAULT_REPUTATION_RULES, ensure_ascii=False))


def _normalized_rule_faction(value: object, fallback: str) -> str:
    name = normalize_faction_name(value)
    return name or fallback


def normalize_faction_name(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = _FACTION_NAME_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    return text[:64]


def _normalize_reputation_rules(raw_rules: object) -> dict:
    rules = _default_rules_copy()
    if not isinstance(raw_rules, dict):
        return rules

    trade_raw = raw_rules.get("trade")
    if isinstance(trade_raw, dict):
        trade_rules = rules["trade"]
        trade_rules["merchant_faction"] = _normalized_rule_faction(
            trade_raw.get("merchant_faction"),
            str(trade_rules["merchant_faction"]),
        )
        trade_rules["merchant_delta_small"] = _safe_range_int(
            trade_raw.get("merchant_delta_small"),
            int(trade_rules["merchant_delta_small"]),
            lower=-25,
            upper=25,
        )
        trade_rules["merchant_delta_large"] = _safe_range_int(
            trade_raw.get("merchant_delta_large"),
            int(trade_rules["merchant_delta_large"]),
            lower=-25,
            upper=25,
        )
        trade_rules["merchant_large_qty_threshold"] = max(
            1,
            _safe_int(
                trade_raw.get("merchant_large_qty_threshold"),
                int(trade_rules["merchant_large_qty_threshold"]),
            ),
        )
        trade_rules["charity_faction"] = _normalized_rule_faction(
            trade_raw.get("charity_faction"),
            str(trade_rules["charity_faction"]),
        )
        trade_rules["charity_delta_small"] = _safe_range_int(
            trade_raw.get("charity_delta_small"),
            int(trade_rules["charity_delta_small"]),
            lower=-25,
            upper=25,
        )
        trade_rules["charity_delta_large"] = _safe_range_int(
            trade_raw.get("charity_delta_large"),
            int(trade_rules["charity_delta_large"]),
            lower=-25,
            upper=25,
        )
        trade_rules["charity_large_qty_threshold"] = max(
            1,
            _safe_int(
                trade_raw.get("charity_large_qty_threshold"),
                int(trade_rules["charity_large_qty_threshold"]),
            ),
        )
        trade_rules["generic_give_delta"] = _safe_range_int(
            trade_raw.get("generic_give_delta"),
            int(trade_rules["generic_give_delta"]),
            lower=-25,
            upper=25,
        )

    quest_raw = raw_rules.get("quest")
    if isinstance(quest_raw, dict):
        quest_rules = rules["quest"]
        quest_rules["default_faction"] = _normalized_rule_faction(
            quest_raw.get("default_faction"),
            str(quest_rules["default_faction"]),
        )
        quest_rules["default_delta"] = _safe_range_int(
            quest_raw.get("default_delta"),
            int(quest_rules["default_delta"]),
            lower=-25,
            upper=25,
        )

        raw_deltas = quest_raw.get("objective_deltas")
        if isinstance(raw_deltas, dict):
            normalized_deltas: dict[str, int] = dict(quest_rules["objective_deltas"])
            for key, value in raw_deltas.items():
                objective = str(key or "").strip().casefold()
                if not objective:
                    continue
                normalized_deltas[objective] = _safe_range_int(
                    value,
                    _safe_int(normalized_deltas.get(objective), quest_rules["default_delta"]),
                    lower=-25,
                    upper=25,
                )
            quest_rules["objective_deltas"] = normalized_deltas

        raw_factions = quest_raw.get("objective_factions")
        if isinstance(raw_factions, dict):
            normalized_factions: dict[str, str] = dict(quest_rules["objective_factions"])
            for key, value in raw_factions.items():
                objective = str(key or "").strip().casefold()
                if not objective:
                    continue
                fallback = str(normalized_factions.get(objective) or quest_rules["default_faction"])
                normalized_factions[objective] = _normalized_rule_faction(value, fallback)
            quest_rules["objective_factions"] = normalized_factions

    dungeon_raw = raw_rules.get("dungeon")
    if isinstance(dungeon_raw, dict):
        dungeon_rules = rules["dungeon"]
        dungeon_rules["faction"] = _normalized_rule_faction(
            dungeon_raw.get("faction"),
            str(dungeon_rules["faction"]),
        )
        dungeon_rules["default_delta"] = _safe_range_int(
            dungeon_raw.get("default_delta"),
            int(dungeon_rules["default_delta"]),
            lower=-25,
            upper=25,
        )
        dungeon_rules["high_floor_delta"] = _safe_range_int(
            dungeon_raw.get("high_floor_delta"),
            int(dungeon_rules["high_floor_delta"]),
            lower=-25,
            upper=25,
        )
        dungeon_rules["high_floor_threshold"] = max(
            1,
            _safe_int(dungeon_raw.get("high_floor_threshold"), int(dungeon_rules["high_floor_threshold"])),
        )
        dungeon_rules["boss_delta"] = _safe_range_int(
            dungeon_raw.get("boss_delta"),
            int(dungeon_rules["boss_delta"]),
            lower=-25,
            upper=25,
        )

        raw_events = dungeon_raw.get("eligible_event_types")
        if isinstance(raw_events, list):
            events = [str(item or "").strip().casefold() for item in raw_events]
            events = [event for event in events if event]
            if events:
                dungeon_rules["eligible_event_types"] = events[:16]

    return rules


def load_reputation_rules(path: str | Path = _DEFAULT_RULES_PATH) -> dict:
    rules_path = Path(path)
    try:
        payload = json.loads(rules_path.read_text(encoding="utf-8"))
    except Exception:
        return _default_rules_copy()
    return _normalize_reputation_rules(payload)


def _rules_mtime_ns(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return None


def get_reputation_rules(path: str | Path = _DEFAULT_RULES_PATH) -> dict:
    rules_path = Path(path)
    cache_key = str(rules_path)
    mtime_ns = _rules_mtime_ns(rules_path)
    cached = _RULES_CACHE.get(cache_key)
    if cached and cached[0] == mtime_ns:
        return cached[1]

    rules = load_reputation_rules(rules_path)
    _RULES_CACHE[cache_key] = (mtime_ns, rules)
    return rules


def _resolved_rules(rules: dict | None) -> dict:
    if isinstance(rules, dict):
        return _normalize_reputation_rules(rules)
    return get_reputation_rules()


def _format_delta(delta: int) -> str:
    value = _safe_int(delta, 0)
    return f"+{value}" if value >= 0 else str(value)


def ensure_reputation_state(state: GameState) -> None:
    if not isinstance(state.faction_reputation, dict):
        state.faction_reputation = {}
    cleaned: dict[str, int] = {}
    for key, value in state.faction_reputation.items():
        faction = normalize_faction_name(key)
        if not faction:
            continue
        cleaned[faction] = _clamp_reputation(_safe_int(value, 0))
    state.faction_reputation = cleaned

    if not isinstance(state.faction_reputation_log, list):
        state.faction_reputation_log = []
    sanitized_log: list[dict] = []
    for raw in state.faction_reputation_log[-REPUTATION_LOG_MAX_ITEMS:]:
        if not isinstance(raw, dict):
            continue
        faction = normalize_faction_name(raw.get("faction"))
        if not faction:
            continue
        sanitized_log.append(
            {
                "at": str(raw.get("at") or ""),
                "faction": faction,
                "delta": _safe_int(raw.get("delta"), 0),
                "before": _clamp_reputation(_safe_int(raw.get("before"), 0)),
                "after": _clamp_reputation(_safe_int(raw.get("after"), 0)),
                "reason": str(raw.get("reason") or "")[:140],
                "source": str(raw.get("source") or "")[:64],
            }
        )
    state.faction_reputation_log = sanitized_log


def adjust_reputation(
    state: GameState,
    *,
    faction: str,
    delta: int,
    reason: str = "",
    source: str = "",
) -> int:
    ensure_reputation_state(state)
    faction_name = normalize_faction_name(faction)
    if not faction_name:
        return 0

    change = max(-25, min(25, _safe_int(delta, 0)))
    if change == 0:
        return state.faction_reputation.get(faction_name, 0)

    before = _clamp_reputation(state.faction_reputation.get(faction_name, 0))
    after = _clamp_reputation(before + change)
    state.faction_reputation[faction_name] = after

    state.faction_reputation_log.append(
        {
            "at": _utc_now_iso(),
            "faction": faction_name,
            "delta": (after - before),
            "before": before,
            "after": after,
            "reason": str(reason or "")[:140],
            "source": str(source or "")[:64],
        }
    )
    if len(state.faction_reputation_log) > REPUTATION_LOG_MAX_ITEMS:
        del state.faction_reputation_log[:-REPUTATION_LOG_MAX_ITEMS]
    return after


def reputation_tier(score: int) -> str:
    value = _clamp_reputation(_safe_int(score, 0))
    current = "neutre"
    for threshold, label in _REPUTATION_TIERS:
        if value >= threshold:
            current = label
    return current


def merchant_price_multiplier_from_reputation(state: GameState) -> float:
    ensure_reputation_state(state)
    score = _safe_int(state.faction_reputation.get("Marchands"), 0)
    if score <= -60:
        return 1.35
    if score <= -20:
        return 1.15
    if score >= 75:
        return 0.78
    if score >= 50:
        return 0.85
    if score >= 20:
        return 0.93
    return 1.0


def can_access_scene_by_reputation(state: GameState, *, scene_id: str, scene_title: str) -> tuple[bool, str]:
    ensure_reputation_state(state)
    sid = str(scene_id or "").strip().casefold()
    title = str(scene_title or "").strip().casefold()
    merged = f"{sid} {title}"

    autorites = _safe_int(state.faction_reputation.get("Autorites"), 0)
    marchands = _safe_int(state.faction_reputation.get("Marchands"), 0)
    arcanistes = _safe_int(state.faction_reputation.get("Arcanistes"), 0)

    if any(token in merged for token in ("palais", "citadelle", "tribunal", "conseil", "caserne")):
        if autorites < -10:
            return False, "Acces refuse: votre reputation avec les Autorites est trop basse."
    if any(token in merged for token in ("banque", "hotel_monnaies", "hôtel_monnaies", "marche", "marché")):
        if marchands < -20:
            return False, "Les Marchands vous ferment leurs portes."
    if any(token in merged for token in ("academie", "académie", "laboratoire", "observatoire", "scriptoria")):
        if arcanistes < -15:
            return False, "Les Arcanistes refusent de vous recevoir."

    return True, ""


def reputation_summary(state: GameState, *, limit: int = 6) -> str:
    ensure_reputation_state(state)
    rows = sorted(
        state.faction_reputation.items(),
        key=lambda row: (-abs(_safe_int(row[1], 0)), row[0]),
    )
    if not rows:
        return "aucune"
    chunks: list[str] = []
    for faction, score in rows[: max(1, int(limit))]:
        value = _safe_int(score, 0)
        sign = "+" if value >= 0 else ""
        chunks.append(f"{faction}:{sign}{value} ({reputation_tier(value)})")
    return " | ".join(chunks)


def infer_npc_faction(
    *,
    npc_name: str,
    npc_profile: dict | None = None,
    map_anchor: str = "",
) -> str:
    role = str((npc_profile or {}).get("role") or "").strip().casefold()
    label = str((npc_profile or {}).get("label") or npc_name or "").strip().casefold()
    combined = f"{role} {label}"

    if any(token in combined for token in ("marchand", "boutique", "forgeron", "artisan", "banquier")):
        return "Marchands"
    if any(token in combined for token in ("garde", "milice", "officier", "capitaine", "soldat")):
        return "Autorites"
    if any(token in combined for token in ("pretre", "pretresse", "temple", "acolyte", "moine", "sanctuaire")):
        return "Ordres Sacres"
    if any(token in combined for token in ("mendiant", "pauvre", "vagabond", "peuple")):
        return "Peuple"
    if any(token in combined for token in ("mage", "alchim", "sorc", "academie", "arcan")):
        return "Arcanistes"
    anchor = str(map_anchor or "").strip()
    if anchor:
        return f"Habitants de {anchor[:32]}"
    return "Habitants"


def apply_trade_reputation(
    state: GameState,
    *,
    trade_context: dict | None,
    npc_name: str = "",
    npc_profile: dict | None = None,
    map_anchor: str = "",
    rules: dict | None = None,
) -> list[str]:
    config = _resolved_rules(rules)
    trade_rules = config["trade"]
    ctx = trade_context if isinstance(trade_context, dict) else {}
    action = str(ctx.get("action") or "").strip().casefold()
    status = str(ctx.get("status") or "").strip().casefold()
    if status != "ok":
        return []

    lines: list[str] = []
    qty = max(1, _safe_int(ctx.get("qty_done"), 1))
    if action in {"buy", "sell", "exchange"}:
        threshold = max(1, _safe_int(trade_rules.get("merchant_large_qty_threshold"), 2))
        delta = _safe_int(trade_rules.get("merchant_delta_small"), 1)
        if qty > threshold:
            delta = _safe_int(trade_rules.get("merchant_delta_large"), 2)
        faction = str(trade_rules.get("merchant_faction") or "Marchands")
        score = adjust_reputation(
            state,
            faction=faction,
            delta=delta,
            reason=f"transaction:{action}",
            source="trade",
        )
        lines.append(f"{faction} {_format_delta(delta)} ({score})")

    if action == "give":
        if bool(ctx.get("target_is_beggar")):
            threshold = max(1, _safe_int(trade_rules.get("charity_large_qty_threshold"), 2))
            delta = _safe_int(trade_rules.get("charity_delta_small"), 2)
            if qty > threshold:
                delta = _safe_int(trade_rules.get("charity_delta_large"), 3)
            faction = str(trade_rules.get("charity_faction") or "Peuple")
            score = adjust_reputation(
                state,
                faction=faction,
                delta=delta,
                reason="charite",
                source="trade",
            )
            lines.append(f"{faction} {_format_delta(delta)} ({score})")
        else:
            give_delta = _safe_int(trade_rules.get("generic_give_delta"), 1)
            faction = infer_npc_faction(npc_name=npc_name, npc_profile=npc_profile, map_anchor=map_anchor)
            score = adjust_reputation(
                state,
                faction=faction,
                delta=give_delta,
                reason="don",
                source="trade",
            )
            lines.append(f"{faction} {_format_delta(give_delta)} ({score})")

    return lines


def apply_quest_completion_reputation(state: GameState, *, quest: dict, rules: dict | None = None) -> list[str]:
    if not isinstance(quest, dict):
        return []
    if bool(quest.get("reputation_claimed")):
        return []
    if str(quest.get("status") or "") != "completed":
        return []

    config = _resolved_rules(rules)
    quest_rules = config["quest"]
    objective = quest.get("objective", {}) if isinstance(quest.get("objective"), dict) else {}
    objective_type = str(objective.get("type") or "").strip().casefold()
    source_npc = str(quest.get("source_npc_name") or "").strip()
    default_faction = str(quest_rules.get("default_faction") or "Habitants")
    delta = _safe_int(quest_rules.get("default_delta"), 2)

    objective_deltas = quest_rules.get("objective_deltas") if isinstance(quest_rules.get("objective_deltas"), dict) else {}
    objective_factions = (
        quest_rules.get("objective_factions")
        if isinstance(quest_rules.get("objective_factions"), dict)
        else {}
    )
    if objective_type:
        delta = _safe_int(objective_deltas.get(objective_type), delta)
    faction = str(objective_factions.get(objective_type) or default_faction)

    if source_npc:
        faction = infer_npc_faction(npc_name=source_npc, npc_profile=None, map_anchor="")

    score = adjust_reputation(
        state,
        faction=faction,
        delta=delta,
        reason=f"quest:{objective_type or 'generic'}",
        source="quest",
    )
    quest["reputation_claimed"] = True
    return [f"{faction} {_format_delta(delta)} ({score})"]


def apply_dungeon_reputation(state: GameState, *, floor: int, event_type: str, rules: dict | None = None) -> list[str]:
    config = _resolved_rules(rules)
    dungeon_rules = config["dungeon"]
    kind = str(event_type or "").strip().casefold()
    allowed_events = dungeon_rules.get("eligible_event_types")
    if not isinstance(allowed_events, list):
        allowed_events = ["monster", "mimic", "boss"]
    allowed = {str(item or "").strip().casefold() for item in allowed_events if str(item or "").strip()}
    if kind not in allowed:
        return []

    delta = _safe_int(dungeon_rules.get("default_delta"), 1)
    floor_threshold = max(1, _safe_int(dungeon_rules.get("high_floor_threshold"), 10))
    if kind == "boss":
        delta = _safe_int(dungeon_rules.get("boss_delta"), 3)
    elif _safe_int(floor, 0) >= floor_threshold:
        delta = _safe_int(dungeon_rules.get("high_floor_delta"), 2)

    faction = str(dungeon_rules.get("faction") or "Aventuriers")
    score = adjust_reputation(
        state,
        faction=faction,
        delta=delta,
        reason=f"dungeon:{kind}",
        source="dungeon",
    )
    return [f"{faction} {_format_delta(delta)} ({score})"]


def apply_quest_branch_reputation(state: GameState, *, quest: dict) -> list[str]:
    if not isinstance(quest, dict):
        return []
    if str(quest.get("status") or "") != "completed":
        return []
    if bool(quest.get("branch_reputation_claimed")):
        return []

    branching = quest.get("branching")
    if not isinstance(branching, dict):
        quest["branch_reputation_claimed"] = True
        return []

    selected_id = str(branching.get("selected_option_id") or "").strip().casefold()
    options = branching.get("options") if isinstance(branching.get("options"), list) else []
    selected: dict | None = None
    for row in options:
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "").strip().casefold() == selected_id:
            selected = row
            break
    if not isinstance(selected, dict):
        quest["branch_reputation_claimed"] = True
        return []

    rep_map = selected.get("reputation") if isinstance(selected.get("reputation"), dict) else {}
    if not rep_map:
        quest["branch_reputation_claimed"] = True
        return []

    lines: list[str] = []
    for faction, raw_delta in rep_map.items():
        delta = max(-25, min(25, _safe_int(raw_delta, 0)))
        if delta == 0:
            continue
        score = adjust_reputation(
            state,
            faction=str(faction),
            delta=delta,
            reason=f"quest_branch:{quest.get('id')}",
            source="quest_branch",
        )
        lines.append(f"{str(faction)} {_format_delta(delta)} ({score})")

    quest["branch_reputation_claimed"] = True
    return lines
