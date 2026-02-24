from __future__ import annotations

import random
import re

from app.gamemaster.location_manager import canonical_anchor, official_shortest_path
from app.gamemaster.world_time import day_index, hour_minute, time_period_label

_URBAN_EVENT_ANCHORS = [
    "Lumeria",
    "Valedor",
    "Brumefeu",
    "Sylvaën",
    "Dun'Khar",
]

_INTERVENE_RE = re.compile(
    r"\b(intervenir|j[' ]?interviens|m[' ]?en occupe|m[' ]?en charge|je gere|je m[' ]?en charge|j[' ]?aide)\b",
    flags=re.IGNORECASE,
)
_IGNORE_RE = re.compile(
    r"\b(ignorer|j[' ]?ignore|laisser|pas maintenant|plus tard)\b",
    flags=re.IGNORECASE,
)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _flags(state) -> dict:
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    flags = state.gm_state.get("flags")
    if not isinstance(flags, dict):
        state.gm_state["flags"] = {}
        flags = state.gm_state["flags"]
    return flags


def _world_state(state) -> dict:
    if not isinstance(getattr(state, "world_state", None), dict):
        state.world_state = {}
    return state.world_state


def _safe_current_anchor(state, default: str = "Lumeria") -> str:
    scene = None
    getter = getattr(state, "current_scene", None)
    if callable(getter):
        try:
            scene = getter()
        except Exception:
            scene = None
    anchor = str(getattr(scene, "map_anchor", "") or "").strip()
    if not anchor and isinstance(getattr(state, "gm_state", None), dict):
        gm_state = state.gm_state
        anchor = str(gm_state.get("map_anchor") or gm_state.get("location") or "").strip()
    return canonical_anchor(anchor or default)


def _safe_anchor_distance(from_anchor: str, to_anchor: str) -> int:
    start = canonical_anchor(from_anchor)
    goal = canonical_anchor(to_anchor)
    if start == goal:
        return 0
    path = official_shortest_path(start, goal)
    return max(0, len(path) - 1)


def _is_dungeon_active(state) -> bool:
    run = getattr(state, "active_dungeon_run", None)
    return isinstance(run, dict) and not bool(run.get("completed", False))


def _event_incident_for_day(
    day: int,
    *,
    instability: int,
    global_tension: int,
    previous_anchor: str = "",
) -> dict:
    anchors = list(_URBAN_EVENT_ANCHORS)
    clean_previous = canonical_anchor(previous_anchor) if previous_anchor else ""
    if clean_previous in anchors and len(anchors) > 1:
        anchors.remove(clean_previous)

    seed = (abs(int(day)) * 1543) + (max(0, int(instability)) * 17) + (max(0, int(global_tension)) * 13)
    rng = random.Random(seed)
    anchor = str(rng.choice(anchors))

    table = [
        {
            "id": "rixe_marche",
            "label": "Une rixe eclate au marche et menace de tourner a l'emeute.",
            "weight": 7,
            "success": "Tu disperses les fauteurs de trouble et les etals reouvrent.",
            "failure": "La foule panique, plusieurs echoppes sont pillees.",
        },
        {
            "id": "incendie_entrepot",
            "label": "Un incendie prend dans les entrepots de ravitaillement.",
            "weight": 6,
            "success": "Le feu est contenu avant de devorer tout le quartier.",
            "failure": "Les flammes gagnent les reserves et les prix explosent.",
        },
        {
            "id": "razzia_nocturne",
            "label": "Une bande armee frappe les rues a la tombee du jour.",
            "weight": 5,
            "success": "La bande est repoussee avant d'installer la terreur.",
            "failure": "La bande se replie avec un lourd butin.",
        },
        {
            "id": "enlevement",
            "label": "Un notable local est enleve en pleine rue.",
            "weight": 4,
            "success": "La victime est retrouvee vivante apres une poursuite eclair.",
            "failure": "Les ravisseurs disparaissent et la ville se verrouille.",
        },
    ]

    pressure = max(0, min(100, int(instability))) + max(0, min(100, int(global_tension)))
    weights: list[int] = []
    for row in table:
        base = max(1, _safe_int(row.get("weight"), 1))
        if pressure >= 120 and str(row.get("id") or "") in {"razzia_nocturne", "enlevement"}:
            base += 3
        elif pressure <= 55 and str(row.get("id") or "") in {"rixe_marche", "incendie_entrepot"}:
            base += 2
        weights.append(base)

    chosen = rng.choices(population=table, weights=weights, k=1)[0]
    return {
        "id": str(chosen.get("id") or "incident"),
        "anchor": anchor,
        "label": str(chosen.get("label") or "Un incident trouble la ville."),
        "success_text": str(chosen.get("success") or "La situation est stabilisee."),
        "failure_text": str(chosen.get("failure") or "La situation empire brutalement."),
        "resolved": False,
        "dismissed": False,
        "day": max(0, int(day)),
    }


def apply_world_time_events(
    state,
    *,
    safe_int=_safe_int,
    utc_now_iso=lambda: "",
    current_anchor: str | None = None,
    in_dungeon: bool | None = None,
) -> list[str]:
    flags = _flags(state)
    world_state = _world_state(state)
    now_minutes = max(0, safe_int(getattr(state, "world_time_minutes", 0), 0))
    today = day_index(now_minutes)
    hour, _ = hour_minute(now_minutes)
    period = time_period_label(now_minutes)
    instability = max(0, min(100, safe_int(world_state.get("instability_level"), 0)))
    global_tension = max(0, min(100, safe_int(world_state.get("global_tension"), 0)))
    active_anchor = canonical_anchor(current_anchor) if isinstance(current_anchor, str) and current_anchor.strip() else None
    dungeon_mode = bool(in_dungeon) if in_dungeon is not None else _is_dungeon_active(state)
    legacy_show_all = active_anchor is None
    if active_anchor is None:
        active_anchor = _safe_current_anchor(state)
    show_city_ambience = (not dungeon_mode) and (active_anchor in _URBAN_EVENT_ANCHORS)
    lines: list[str] = []

    last_period = str(flags.get("world_period") or "")
    if period != last_period:
        flags["world_period"] = period
        if show_city_ambience and period in {"Nuit", "Nuit profonde"}:
            lines.append("🌙 Les rues se vident, les patrouilles prennent le relais.")
        elif show_city_ambience and period == "Aube":
            lines.append("🌅 La ville s'eveille, les premieres echoppes ouvrent.")
        elif show_city_ambience and period == "Jour":
            lines.append("☀️ L'activite bat son plein dans les quartiers.")
        elif show_city_ambience and period == "Crepuscule":
            lines.append("🌆 Les lumieres se rallument, la foule change de visage.")

    last_day = _safe_int(flags.get("world_event_day"), -1)
    if today != last_day:
        previous_event_id = str(flags.get("world_event_name") or "")
        flags["world_event_day"] = today
        event = _event_for_day(
            today,
            instability=instability,
            global_tension=global_tension,
            previous_event_id=previous_event_id,
        )
        flags["world_event_name"] = event["id"]
        flags["world_event_trade_mod_pct"] = int(event["trade_mod_pct"])
        flags["world_event_travel_bias"] = dict(event.get("travel_bias") or {})
        flags["merchant_restock_bonus_pct"] = int(event.get("merchant_restock_bonus_pct") or 0)
        flags["merchant_restock_day"] = today
        flags["world_event_updated_at"] = utc_now_iso()
        previous_incident = flags.get("world_event_incident") if isinstance(flags.get("world_event_incident"), dict) else {}
        previous_anchor = str(previous_incident.get("anchor") or "")
        incident = _event_incident_for_day(
            today,
            instability=instability,
            global_tension=global_tension,
            previous_anchor=previous_anchor,
        )
        flags["world_event_incident"] = incident
        history = flags.get("world_event_history")
        if not isinstance(history, list):
            history = []
        history.append({"day": today, "id": str(event["id"])})
        flags["world_event_history"] = history[-14:]
        world_state["market_price_mod_pct"] = int(event["trade_mod_pct"])
        world_state["travel_event_bias"] = dict(event.get("travel_bias") or {})
        world_state["merchant_restock_bonus_pct"] = int(event.get("merchant_restock_bonus_pct") or 0)
        world_state["last_event_anchor"] = str(incident.get("anchor") or "")

        incident_anchor = canonical_anchor(str(incident.get("anchor") or active_anchor))
        distance = _safe_anchor_distance(active_anchor, incident_anchor)
        is_near = (distance <= 1) and (not dungeon_mode)
        if legacy_show_all or is_near:
            lines.append(f"📰 Evenement du jour ({incident_anchor}): {event['label']}")
            lines.append(f"⚠️ Incident local: {str(incident.get('label') or '')}")
            lines.append("🛡️ Tu peux intervenir: ecris \"j'interviens\".")

    # Petite variation intrajournee: a midi et a 20h, les prix se stabilisent.
    if hour in {12, 20} and show_city_ambience:
        intraday_key = f"world_intraday_mark_{today}_{hour}"
        if not bool(flags.get(intraday_key)):
            flags[intraday_key] = True
            lines.append("⚖️ Les prix du marche se recalibrent.")

    return lines


def try_resolve_nearby_world_event(state, user_text: str, *, safe_int=_safe_int, utc_now_iso=lambda: "") -> list[str]:
    text = str(user_text or "").strip()
    if not text:
        return []
    if not _INTERVENE_RE.search(text) and not _IGNORE_RE.search(text):
        return []

    flags = _flags(state)
    world_state = _world_state(state)
    incident = flags.get("world_event_incident") if isinstance(flags.get("world_event_incident"), dict) else None
    if not isinstance(incident, dict):
        return []
    if bool(incident.get("resolved", False)):
        return []

    now_minutes = max(0, safe_int(getattr(state, "world_time_minutes", 0), 0))
    today = day_index(now_minutes)
    incident_day = max(-1, safe_int(incident.get("day"), -1))
    if incident_day >= 0 and today != incident_day:
        return []

    current_anchor = _safe_current_anchor(state)
    incident_anchor = canonical_anchor(str(incident.get("anchor") or current_anchor))
    distance = _safe_anchor_distance(current_anchor, incident_anchor)

    if _is_dungeon_active(state):
        return ["Impossible d'intervenir depuis le donjon."]
    if distance > 1:
        return [f"Tu es trop loin de {incident_anchor} pour intervenir maintenant."]

    if _IGNORE_RE.search(text):
        incident["dismissed"] = True
        flags["world_event_incident"] = incident
        return [f"Tu laisses l'incident de {incident_anchor} aux autorites locales, pour le moment."]

    level = 1
    sheet = getattr(state, "player_sheet", None)
    if isinstance(sheet, dict):
        effective = sheet.get("effective_stats") if isinstance(sheet.get("effective_stats"), dict) else {}
        stats = sheet.get("stats") if isinstance(sheet.get("stats"), dict) else {}
        level = max(1, safe_int(effective.get("niveau"), safe_int(stats.get("niveau"), 1)))

    instability = max(0, min(100, safe_int(world_state.get("instability_level"), 0)))
    global_tension = max(0, min(100, safe_int(world_state.get("global_tension"), 0)))

    chance = 0.58 + min(0.18, level * 0.012)
    chance += 0.12 if distance == 0 else -0.06
    pressure = max(0, (instability + global_tension) - 120)
    chance -= pressure * 0.002
    chance = max(0.22, min(0.9, chance))

    seed = (today * 4099) + (now_minutes * 13) + len(text)
    success = random.Random(seed).random() <= chance
    percent = int(round(chance * 100))
    lines: list[str] = []

    if success:
        tension_drop = 3 + (1 if distance == 0 else 0) + (1 if level >= 10 else 0)
        instability_drop = 2 + (1 if level >= 14 else 0)
        world_state["global_tension"] = max(0, min(100, global_tension - tension_drop))
        world_state["instability_level"] = max(0, min(100, instability - instability_drop))
        reward = max(6, 8 + level + (2 if distance == 0 else 0))
        if hasattr(state, "player") and hasattr(state.player, "gold"):
            state.player.gold = max(0, safe_int(getattr(state.player, "gold", 0), 0) + reward)
        lines.append(f"🛡️ Intervention a {incident_anchor}: {str(incident.get('success_text') or '').strip()}")
        lines.append(f"✅ Tu reprends le controle de la situation ({percent}% de chance).")
        lines.append(f"💰 Recompense: +{reward} or.")
    else:
        world_state["global_tension"] = max(0, min(100, global_tension + 3))
        world_state["instability_level"] = max(0, min(100, instability + 2))
        lines.append(f"⚠️ Intervention a {incident_anchor}: {str(incident.get('failure_text') or '').strip()}")
        lines.append(f"❌ Tu arrives trop tard, la situation degenere ({percent}% de chance).")

    incident["resolved"] = True
    incident["resolved_at"] = utc_now_iso()
    incident["result"] = "success" if success else "failure"
    flags["world_event_incident"] = incident
    flags["world_event_incident_last_result"] = {
        "day": today,
        "id": str(incident.get("id") or ""),
        "anchor": incident_anchor,
        "result": str(incident.get("result") or ""),
    }
    return lines


def _event_for_day(
    day: int,
    *,
    instability: int = 0,
    global_tension: int = 0,
    previous_event_id: str = "",
) -> dict:
    table = [
        {
            "id": "fete_du_commerce",
            "label": "Fete du commerce: ristournes et etals bien fournis.",
            "trade_mod_pct": -10,
            "travel_bias": {"discovery": 18, "ambush": -12, "hazard": -8},
            "merchant_restock_bonus_pct": 30,
            "weight": 7,
        },
        {
            "id": "controle_fiscal",
            "label": "Controle fiscal: les marchands augmentent leurs marges.",
            "trade_mod_pct": 12,
            "travel_bias": {"hazard": 8, "encounter": 6, "discovery": -10},
            "merchant_restock_bonus_pct": -10,
            "weight": 6,
        },
        {
            "id": "caravanes",
            "label": "Caravanes en ville: afflux de produits courants.",
            "trade_mod_pct": -4,
            "travel_bias": {"encounter": 12, "ambush": -10},
            "merchant_restock_bonus_pct": 18,
            "weight": 8,
        },
        {
            "id": "insomnie_urbaine",
            "label": "Nuit trouble: les prix de securite montent.",
            "trade_mod_pct": 6,
            "travel_bias": {"ambush": 15, "camp": 8, "discovery": -8},
            "merchant_restock_bonus_pct": 0,
            "weight": 5,
        },
        {
            "id": "etat_d_alerte",
            "label": "Etat d'alerte: patrouilles, barrages et rumeurs de pillage.",
            "trade_mod_pct": 9,
            "travel_bias": {"hazard": 14, "ambush": 14, "camp": 6, "discovery": -12},
            "merchant_restock_bonus_pct": -18,
            "weight": 4,
        },
        {
            "id": "routine_stable",
            "label": "Journee stable: marche sans variation majeure.",
            "trade_mod_pct": 0,
            "travel_bias": {"discovery": 4},
            "merchant_restock_bonus_pct": 4,
            "weight": 10,
        },
    ]

    idx_hint = abs(int(day)) % len(table)
    seed = (abs(int(day)) * 9973) + (idx_hint * 131)
    rng = random.Random(seed)
    weights: list[int] = []
    for row in table:
        base = max(1, _safe_int(row.get("weight"), 1))
        if str(row.get("id") or "") == str(previous_event_id or ""):
            base = max(1, base // 3)
        if str(row.get("id") or "") == "etat_d_alerte":
            pressure = max(0, min(100, _safe_int(instability, 0))) + max(0, min(100, _safe_int(global_tension, 0)))
            if pressure >= 130:
                base += 8
            elif pressure >= 95:
                base += 4
        if str(row.get("id") or "") == "fete_du_commerce":
            if max(0, _safe_int(instability, 0)) <= 35:
                base += 3
        weights.append(max(1, base))

    chosen = rng.choices(population=table, weights=weights, k=1)[0]
    chosen_id = str(chosen.get("id") or "")
    if previous_event_id and chosen_id == str(previous_event_id):
        alternatives = [row for row in table if str(row.get("id") or "") != str(previous_event_id)]
        if alternatives:
            chosen = alternatives[0 if idx_hint >= len(alternatives) else idx_hint]

    out = dict(chosen)
    out["travel_bias"] = dict(chosen.get("travel_bias") or {})
    out["merchant_restock_bonus_pct"] = int(chosen.get("merchant_restock_bonus_pct") or 0)
    out["trade_mod_pct"] = int(chosen.get("trade_mod_pct") or 0)
    out["id"] = str(chosen.get("id") or "routine_stable")
    out["label"] = str(chosen.get("label") or "Journee stable.")
    return out
