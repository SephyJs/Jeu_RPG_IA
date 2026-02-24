from __future__ import annotations

import re
import unicodedata

from app.ui.state.game_state import GameState, Scene


QUEST_MIN_TARGET = 1
QUEST_MAX_TARGET = 999


def ensure_quest_state(state: GameState) -> None:
    if not isinstance(state.quests, list):
        state.quests = []
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        status = str(quest.get("status") or "in_progress")
        if status != "in_progress":
            continue
        quest["accepted_by_player"] = bool(quest.get("accepted_by_player", True))
        if not str(quest.get("accepted_at") or "").strip():
            quest["accepted_at"] = str(quest.get("created_at") or quest.get("updated_at") or "")
    if not isinstance(state.npc_dialogue_counts, dict):
        state.npc_dialogue_counts = {}
    if not isinstance(state.npc_quests_given, dict):
        state.npc_quests_given = {}
    if not isinstance(state.quest_generation_in_progress, set):
        state.quest_generation_in_progress = set()
    if not isinstance(state.quest_counters, dict):
        state.quest_counters = {}
    state.quest_counters.setdefault("player_messages_sent", 0)
    state.quest_counters.setdefault("dungeon_floors_cleared", 0)
    if not isinstance(state.quest_seq, int) or state.quest_seq < 0:
        state.quest_seq = len(state.quests)


def active_quest_for_npc(state: GameState, npc_key: str) -> dict | None:
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        if str(quest.get("source_npc_key") or "") != npc_key:
            continue
        if str(quest.get("status") or "in_progress") == "in_progress":
            return quest
    return None


def active_quests(state: GameState) -> list[dict]:
    rows: list[dict] = []
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        if str(quest.get("status") or "in_progress") != "in_progress":
            continue
        rows.append(quest)
    return rows


def quest_branch_options_summary(quest: dict) -> str:
    if not isinstance(quest, dict):
        return ""
    branching = quest.get("branching")
    if not isinstance(branching, dict):
        return ""
    options = branching.get("options") if isinstance(branching.get("options"), list) else []
    if not options:
        return ""
    rows: list[str] = []
    for row in options:
        if not isinstance(row, dict):
            continue
        option_id = str(row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        if not option_id or not label:
            continue
        rows.append(f"{option_id}: {label}")
    return " | ".join(rows)


def quest_offer_threshold(
    state: GameState,
    npc_key: str,
    *,
    min_messages_before_offer: int,
    messages_per_next_offer: int,
    safe_int,
) -> tuple[int, int]:
    talked = max(0, safe_int(state.npc_dialogue_counts.get(npc_key), 0))
    given = max(0, safe_int(state.npc_quests_given.get(npc_key), 0))
    threshold = max(0, int(min_messages_before_offer)) + (given * max(0, int(messages_per_next_offer)))
    return talked, threshold


def can_request_quest(
    state: GameState,
    npc_key: str,
    *,
    min_messages_before_offer: int,
    messages_per_next_offer: int,
    safe_int,
) -> tuple[bool, str]:
    ensure_quest_state(state)
    if npc_key in state.quest_generation_in_progress:
        return False, "Generation de quete en cours..."
    if active_quest_for_npc(state, npc_key):
        return False, "Ce PNJ vous a deja confie une quete en cours."

    talked, threshold = quest_offer_threshold(
        state,
        npc_key,
        min_messages_before_offer=min_messages_before_offer,
        messages_per_next_offer=messages_per_next_offer,
        safe_int=safe_int,
    )
    if talked < threshold:
        return False, f"Parlez encore un peu au PNJ pour debloquer une quete ({talked}/{threshold})."
    return True, "Quete disponible."


def objective_label(objective_type: str, target_count: int, target_npc: str, target_anchor: str) -> str:
    if objective_type == "talk_to_npc":
        who = target_npc or "ce PNJ"
        return f"Parler a {who} ({target_count}/{target_count})"
    if objective_type == "send_messages":
        return f"Echanger {target_count} messages"
    if objective_type == "explore_locations":
        return f"Explorer {target_count} nouveau(x) lieu(x)"
    if objective_type == "reach_anchor":
        return f"Atteindre {target_anchor or 'la zone cible'}"
    if objective_type == "collect_gold":
        return f"Gagner {target_count} or"
    if objective_type == "clear_dungeon_floors":
        return f"Nettoyer {target_count} etage(s) de donjon"
    return f"Objectif ({target_count})"


def build_runtime_quest(
    state: GameState,
    *,
    quest_payload: dict,
    npc_name: str,
    npc_key: str,
    scene: Scene,
    safe_int,
    utc_now_iso,
    npc_profile_key,
) -> dict:
    ensure_quest_state(state)
    state.quest_seq += 1

    objective_type = str(quest_payload.get("objective_type") or "send_messages")
    target_count = max(1, safe_int(quest_payload.get("target_count"), 1))
    target_npc = str(quest_payload.get("target_npc") or npc_name)
    target_anchor = str(quest_payload.get("target_anchor") or (scene.map_anchor or "Lumeria"))
    target_npc_key = npc_profile_key(target_npc, scene.id)
    if objective_type == "talk_to_npc":
        target_npc = npc_name
        target_npc_key = npc_key

    deadline_hours = max(0, safe_int(quest_payload.get("deadline_hours"), 0))
    deadline_hours = min(deadline_hours, 96)
    deadline_world_time_minutes = 0
    if deadline_hours > 0:
        deadline_world_time_minutes = max(0, safe_int(state.world_time_minutes, 0) + (deadline_hours * 60))

    branching = _normalize_branching_payload(quest_payload.get("branching"))

    objective = {
        "type": objective_type,
        "target": target_count,
        "base_target": target_count,
        "target_npc": target_npc,
        "target_npc_key": target_npc_key,
        "target_anchor": target_anchor,
        "label": objective_label(objective_type, target_count, target_npc, target_anchor),
        "start_player_messages": max(0, safe_int(state.quest_counters.get("player_messages_sent"), 0)),
        "start_npc_messages": max(0, safe_int(state.npc_dialogue_counts.get(target_npc_key), 0)),
        "start_discovered_locations": len(state.discovered_scene_ids),
        "start_gold": max(0, safe_int(state.player.gold, 0)),
        "start_dungeon_floors": max(0, safe_int(state.quest_counters.get("dungeon_floors_cleared"), 0)),
    }
    rewards = quest_payload.get("rewards") if isinstance(quest_payload.get("rewards"), dict) else {}

    quest = {
        "id": f"quest_{state.quest_seq:04d}",
        "title": str(quest_payload.get("title") or f"Mission de {npc_name}"),
        "description": str(quest_payload.get("description") or ""),
        "source_npc_name": npc_name,
        "source_npc_key": npc_key,
        "location_id": scene.id,
        "location_title": scene.title,
        "objective": objective,
        "progress": {"current": 0, "target": target_count, "percent": 0.0},
        "rewards": {
            "gold": max(0, safe_int(rewards.get("gold"), 0)),
            "items": rewards.get("items") if isinstance(rewards.get("items"), list) else [],
            "shop_discount_pct": max(0, safe_int(rewards.get("shop_discount_pct"), 0)),
            "temple_heal_bonus": max(0, safe_int(rewards.get("temple_heal_bonus"), 0)),
        },
        "progress_hint": str(quest_payload.get("progress_hint") or ""),
        "status": "in_progress",
        "reward_claimed": False,
        "accepted_by_player": True,
        "accepted_at": utc_now_iso(),
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "completed_at": "",
        "failed_at": "",
        "failure_consequence": str(quest_payload.get("failure_consequence") or "").strip()[:220],
        "deadline_hours": deadline_hours,
        "deadline_world_time_minutes": deadline_world_time_minutes,
        "branching": branching,
    }
    return quest


def _normalize_branching_payload(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"prompt": "", "options": [], "selected_option_id": "", "selected_label": "", "applied": False}

    prompt = str(raw.get("prompt") or "").strip()[:220]
    options_raw = raw.get("options")
    options: list[dict] = []
    if isinstance(options_raw, list):
        for idx, row in enumerate(options_raw[:4]):
            if not isinstance(row, dict):
                continue
            option_id = str(row.get("id") or f"option_{idx + 1}").strip().casefold()[:40]
            label = str(row.get("label") or "").strip()[:80]
            if not label:
                continue
            description = str(row.get("description") or "").strip()[:180]
            objective_delta = max(-4, min(4, _safe_int_local(row.get("objective_delta"), 0)))
            rewards_bonus = _normalize_branch_rewards(row.get("rewards_bonus"))

            rep_raw = row.get("reputation")
            reputation: dict[str, int] = {}
            if isinstance(rep_raw, dict):
                for key, value in rep_raw.items():
                    faction = str(key or "").strip()[:64]
                    if not faction:
                        continue
                    delta = max(-20, min(20, _safe_int_local(value, 0)))
                    if delta == 0:
                        continue
                    reputation[faction] = delta

            options.append(
                {
                    "id": option_id,
                    "label": label,
                    "description": description,
                    "objective_delta": objective_delta,
                    "rewards_bonus": rewards_bonus,
                    "reputation": reputation,
                }
            )

    return {
        "prompt": prompt,
        "options": options,
        "selected_option_id": "",
        "selected_label": "",
        "applied": False,
        "choice_at": "",
    }


def _normalize_branch_rewards(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    items_raw = source.get("items")
    items: list[dict] = []
    if isinstance(items_raw, list):
        for row in items_raw[:6]:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("item_id") or "").strip()
            if not item_id:
                continue
            qty = max(1, min(_safe_int_local(row.get("qty"), 1), 15))
            items.append({"item_id": item_id, "qty": qty})
    return {
        "gold": max(0, min(_safe_int_local(source.get("gold"), 0), 500)),
        "items": items,
        "shop_discount_pct": max(0, min(_safe_int_local(source.get("shop_discount_pct"), 0), 35)),
        "temple_heal_bonus": max(0, min(_safe_int_local(source.get("temple_heal_bonus"), 0), 10)),
    }


def _safe_int_local(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_lookup_token(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "", folded)


def _quest_id_matches(quest_id: str, user_key: str) -> bool:
    qid = str(quest_id or "").strip()
    key = str(user_key or "").strip()
    if not qid or not key:
        return False

    if qid.casefold() == key.casefold():
        return True

    qid_norm = _normalize_lookup_token(qid)
    key_norm = _normalize_lookup_token(key)
    if qid_norm and key_norm and qid_norm == key_norm:
        return True

    if key_norm.isdigit():
        qdigits = "".join(ch for ch in qid if ch.isdigit())
        if qdigits:
            return qdigits.lstrip("0") == key_norm.lstrip("0")
    return False


def _option_matches(option: dict, user_key: str) -> bool:
    if not isinstance(option, dict):
        return False
    key = str(user_key or "").strip()
    if not key:
        return False
    key_norm = _normalize_lookup_token(key)
    if not key_norm:
        return False

    option_id = str(option.get("id") or "").strip()
    label = str(option.get("label") or "").strip()
    option_id_norm = _normalize_lookup_token(option_id)
    label_norm = _normalize_lookup_token(label)

    if key_norm in {option_id_norm, label_norm}:
        return True
    if label_norm and key_norm and key_norm in label_norm:
        return True
    return False


def choose_quest_branch(
    state: GameState,
    *,
    quest_id: str,
    option_id: str,
    safe_int,
    utc_now_iso,
) -> tuple[bool, str]:
    quest_key = str(quest_id or "").strip()
    option_key = str(option_id or "").strip()
    if not quest_key or not option_key:
        return False, "Usage: /quest choose <quest_id> <option_id>"

    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        if not _quest_id_matches(str(quest.get("id") or "").strip(), quest_key):
            continue
        if str(quest.get("status") or "in_progress") != "in_progress":
            return False, "Cette quete n'est plus active."

        branching = quest.get("branching")
        if not isinstance(branching, dict):
            return False, "Cette quete ne propose pas de branche."

        selected_existing = str(branching.get("selected_option_id") or "").strip()
        if selected_existing:
            if _normalize_lookup_token(selected_existing) == _normalize_lookup_token(option_key):
                label = str(branching.get("selected_label") or selected_existing).strip()
                return True, f"Branche deja active: {label}."
            return False, "Branche deja verrouillee pour cette quete."

        options = branching.get("options") if isinstance(branching.get("options"), list) else []
        chosen: dict | None = None
        for row in options:
            if not isinstance(row, dict):
                continue
            if _option_matches(row, option_key):
                chosen = row
                break
        if not isinstance(chosen, dict):
            available = ", ".join(
                f"{str(row.get('id') or '')} ({str(row.get('label') or '').strip()})".strip()
                for row in options
                if isinstance(row, dict)
            )
            if not available:
                return False, "Aucune option disponible pour cette quete."
            return False, f"Option inconnue. Disponibles: {available}"

        objective = quest.get("objective")
        if not isinstance(objective, dict):
            objective = {}
            quest["objective"] = objective

        current_target = max(
            QUEST_MIN_TARGET,
            min(QUEST_MAX_TARGET, safe_int(objective.get("target"), safe_int(objective.get("base_target"), 1))),
        )
        objective_delta = max(-4, min(4, safe_int(chosen.get("objective_delta"), 0)))
        adjusted_target = max(QUEST_MIN_TARGET, min(QUEST_MAX_TARGET, current_target + objective_delta))
        objective["target"] = adjusted_target

        progress = quest.get("progress")
        if not isinstance(progress, dict):
            progress = {}
            quest["progress"] = progress
        current_progress = max(0, safe_int(progress.get("current"), 0))
        progress["current"] = min(current_progress, adjusted_target)
        progress["target"] = adjusted_target
        progress["percent"] = (progress["current"] / float(adjusted_target)) if adjusted_target > 0 else 0.0

        _merge_branch_reward_bonus(quest, chosen, safe_int=safe_int)

        branching["selected_option_id"] = str(chosen.get("id") or "")
        branching["selected_label"] = str(chosen.get("label") or "")
        branching["choice_at"] = utc_now_iso()
        branching["applied"] = True
        if not str(quest.get("accepted_at") or "").strip():
            quest["accepted_at"] = str(quest.get("created_at") or utc_now_iso())
        quest["accepted_by_player"] = True
        quest["updated_at"] = utc_now_iso()

        option_label = str(chosen.get("label") or branching["selected_option_id"]).strip()
        resolved_quest_id = str(quest.get("id") or quest_key).strip()
        return True, f"Branche choisie pour {resolved_quest_id}: {option_label}."

    return False, "Quete introuvable."


def _merge_branch_reward_bonus(quest: dict, chosen_option: dict, *, safe_int) -> None:
    rewards = quest.get("rewards")
    if not isinstance(rewards, dict):
        rewards = {}
        quest["rewards"] = rewards
    bonus = chosen_option.get("rewards_bonus") if isinstance(chosen_option.get("rewards_bonus"), dict) else {}

    rewards["gold"] = max(0, safe_int(rewards.get("gold"), 0) + max(0, safe_int(bonus.get("gold"), 0)))
    rewards["shop_discount_pct"] = max(
        0,
        min(35, safe_int(rewards.get("shop_discount_pct"), 0) + max(0, safe_int(bonus.get("shop_discount_pct"), 0))),
    )
    rewards["temple_heal_bonus"] = max(
        0,
        min(10, safe_int(rewards.get("temple_heal_bonus"), 0) + max(0, safe_int(bonus.get("temple_heal_bonus"), 0))),
    )

    existing_items = rewards.get("items")
    if not isinstance(existing_items, list):
        existing_items = []
    bonus_items = bonus.get("items") if isinstance(bonus.get("items"), list) else []
    normalized_bonus: list[dict] = []
    for row in bonus_items[:6]:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id") or "").strip()
        if not item_id:
            continue
        qty = max(1, min(15, safe_int(row.get("qty"), 1)))
        normalized_bonus.append({"item_id": item_id, "qty": qty})
    rewards["items"] = [*existing_items, *normalized_bonus][:16]


def apply_quest_timeouts(
    state: GameState,
    *,
    safe_int,
    utc_now_iso,
) -> list[str]:
    now_minutes = max(0, safe_int(getattr(state, "world_time_minutes", 0), 0))
    lines: list[str] = []

    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        if str(quest.get("status") or "in_progress") != "in_progress":
            continue
        deadline = max(0, safe_int(quest.get("deadline_world_time_minutes"), 0))
        if deadline <= 0:
            continue
        if now_minutes <= deadline:
            continue

        quest["status"] = "failed"
        quest["failed_at"] = utc_now_iso()
        quest["updated_at"] = utc_now_iso()
        consequence = str(quest.get("failure_consequence") or "").strip()
        title = str(quest.get("title") or "Quete")
        if consequence:
            lines.append(f"❌ Quete echouee: {title}. {consequence}")
        else:
            lines.append(f"❌ Quete echouee: {title}.")
    return lines


def has_secret_charity_quest(state: GameState, npc_key: str) -> bool:
    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        meta = quest.get("meta", {}) if isinstance(quest.get("meta"), dict) else {}
        if str(meta.get("secret_kind") or "") != "charity_beggar":
            continue
        if str(quest.get("source_npc_key") or "") != str(npc_key or ""):
            continue
        return True
    return False


def maybe_unlock_secret_charity_quest(
    state: GameState,
    *,
    npc_name: str,
    npc_key: str,
    scene: Scene,
    trade_context: dict,
    safe_int,
    random_fn,
    build_runtime_quest_fn,
) -> None:
    if not bool(trade_context.get("target_is_beggar")):
        return
    if has_secret_charity_quest(state, npc_key):
        return

    flags = state.gm_state.setdefault("flags", {})
    if bool(flags.get("secret_quest_beggar_unlocked", False)):
        return

    qty_done = max(0, safe_int(trade_context.get("qty_done"), 0))
    if qty_done <= 0:
        return
    beggar_total = max(0, safe_int(trade_context.get("charity_to_beggar_total"), 0))
    roll = float(random_fn())
    chance = min(0.6, 0.15 + (beggar_total * 0.08))
    if beggar_total < 2 and roll > chance:
        return

    item_name = str(trade_context.get("item_name") or "provision").strip()
    payload = {
        "title": "Les Mains dans l'Ombre",
        "description": f"Le mendiant semble en savoir plus qu'il n'y parait apres ton don ({item_name}).",
        "objective_type": "talk_to_npc",
        "target_count": 1,
        "target_npc": npc_name,
        "target_anchor": scene.map_anchor or "Lumeria",
        "rewards": {
            "gold": 12,
            "items": [{"item_id": "pain_01", "qty": 1}],
            "shop_discount_pct": 3,
            "temple_heal_bonus": 0,
        },
        "progress_hint": "Reparle au mendiant en confidence.",
        "quest_intro": "Le mendiant baisse la voix: 'On me paie pour oublier des choses... et j'ai vu quelque chose pour toi.'",
    }
    quest = build_runtime_quest_fn(
        state,
        quest_payload=payload,
        npc_name=npc_name,
        npc_key=npc_key,
        scene=scene,
    )
    quest["is_secret"] = True
    quest["meta"] = {"secret_kind": "charity_beggar", "trigger_item": str(trade_context.get("item_id") or "")}
    state.quests.append(quest)
    flags["secret_quest_beggar_unlocked"] = True
    state.push("Système", f"🕯️ Quete secrete debloquee: {quest.get('title', 'Mission cachee')}", count_for_media=False)


def compute_quest_progress(state: GameState, quest: dict, *, safe_int) -> tuple[int, int]:
    objective = quest.get("objective", {}) if isinstance(quest.get("objective"), dict) else {}
    objective_type = str(objective.get("type") or "send_messages")
    target = max(1, safe_int(objective.get("target"), 1))

    if objective_type == "talk_to_npc":
        npc_key = str(objective.get("target_npc_key") or "")
        base = max(0, safe_int(objective.get("start_npc_messages"), 0))
        current_total = max(0, safe_int(state.npc_dialogue_counts.get(npc_key), 0))
        return max(0, current_total - base), target

    if objective_type == "send_messages":
        base = max(0, safe_int(objective.get("start_player_messages"), 0))
        current_total = max(0, safe_int(state.quest_counters.get("player_messages_sent"), 0))
        return max(0, current_total - base), target

    if objective_type == "explore_locations":
        base = max(0, safe_int(objective.get("start_discovered_locations"), 0))
        return max(0, len(state.discovered_scene_ids) - base), target

    if objective_type == "reach_anchor":
        target_anchor = str(objective.get("target_anchor") or "").strip().casefold()
        current_anchor = str(state.current_scene().map_anchor or "").strip().casefold()
        return (1 if target_anchor and target_anchor == current_anchor else 0), 1

    if objective_type == "collect_gold":
        base = max(0, safe_int(objective.get("start_gold"), 0))
        return max(0, safe_int(state.player.gold, 0) - base), target

    if objective_type == "clear_dungeon_floors":
        base = max(0, safe_int(objective.get("start_dungeon_floors"), 0))
        current_total = max(0, safe_int(state.quest_counters.get("dungeon_floors_cleared"), 0))
        return max(0, current_total - base), target

    return 0, target


def update_quests_and_notify(
    state: GameState,
    *,
    safe_int,
    utc_now_iso,
    compute_quest_progress_fn,
    apply_quest_rewards_fn,
    apply_quest_reputation_fn,
    apply_quest_branch_reputation_fn=None,
) -> None:
    ensure_quest_state(state)
    timeout_lines = apply_quest_timeouts(
        state,
        safe_int=safe_int,
        utc_now_iso=utc_now_iso,
    )
    for line in timeout_lines:
        state.push("Système", line, count_for_media=False)

    for quest in state.quests:
        if not isinstance(quest, dict):
            continue
        if str(quest.get("status") or "in_progress") != "in_progress":
            continue

        current, target = compute_quest_progress_fn(state, quest, safe_int=safe_int)
        current = min(current, target)
        percent = current / float(target) if target > 0 else 0.0
        quest["progress"] = {
            "current": current,
            "target": target,
            "percent": percent,
        }
        quest["updated_at"] = utc_now_iso()

        if current < target:
            continue

        quest["status"] = "completed"
        quest["completed_at"] = utc_now_iso()
        state.push("Système", f"✅ Quete terminee: {quest.get('title', 'Quete')}", count_for_media=False)

        if not bool(quest.get("reward_claimed", False)):
            reward_lines = apply_quest_rewards_fn(state, quest)
            if reward_lines:
                state.push("Système", "Recompenses: " + " | ".join(reward_lines), count_for_media=False)
        rep_lines = apply_quest_reputation_fn(state, quest=quest)
        if rep_lines:
            state.push("Système", "Réputation: " + " | ".join(rep_lines), count_for_media=False)
        if callable(apply_quest_branch_reputation_fn):
            branch_lines = apply_quest_branch_reputation_fn(state, quest=quest)
            if isinstance(branch_lines, list):
                normalized = [str(line).strip() for line in branch_lines if str(line).strip()]
                if normalized:
                    state.push("Système", "Conséquences: " + " | ".join(normalized), count_for_media=False)


async def request_quest_from_selected_npc(
    state: GameState,
    on_change,
    *,
    safe_int,
    npc_profile_key,
    npc_manager,
    profile_display_name,
    quest_manager,
    can_request_quest_fn,
    build_runtime_quest_fn,
    update_quests_and_notify_fn,
    refresh_chat_messages_view,
) -> None:
    ensure_quest_state(state)
    npc = getattr(state, "selected_npc", None)
    if not npc:
        return

    scene = state.current_scene()
    npc_key = npc_profile_key(npc, scene.id)
    can_request, reason = can_request_quest_fn(state, npc_key)
    if not can_request:
        if reason:
            state.push("Système", reason, count_for_media=False)
        on_change()
        refresh_chat_messages_view()
        return

    state.quest_generation_in_progress.add(npc_key)
    state.push("Système", f"Vous demandez une mission a {npc}...", count_for_media=False)
    on_change()
    refresh_chat_messages_view()

    try:
        profile = state.npc_profiles.get(npc_key)
        if not isinstance(profile, dict):
            try:
                profile = await npc_manager.ensure_profile(
                    state.npc_profiles,
                    npc,
                    location_id=scene.id,
                    location_title=scene.title,
                )
            except Exception:
                profile = None

        npc_name = profile_display_name(profile, npc) if isinstance(profile, dict) else npc
        existing_titles = [
            str(q.get("title") or "")
            for q in state.quests
            if isinstance(q, dict)
        ]
        quest_payload = await quest_manager.generate_quest(
            player_name=getattr(state.player, "name", "l'Eveille"),
            npc_name=npc_name,
            location_id=scene.id,
            location_title=scene.title,
            map_anchor=scene.map_anchor or scene.title,
            npc_profile=profile if isinstance(profile, dict) else None,
            existing_titles=existing_titles,
        )

        quest = build_runtime_quest_fn(
            state,
            quest_payload=quest_payload,
            npc_name=npc_name,
            npc_key=npc_key,
            scene=scene,
        )
        state.quests.append(quest)
        state.npc_quests_given[npc_key] = max(0, safe_int(state.npc_quests_given.get(npc_key), 0) + 1)

        intro = str(quest_payload.get("quest_intro") or "").strip()
        if intro:
            state.push(npc_name, intro, count_for_media=False)
        state.push("Système", f"Nouvelle quete: {quest.get('title', 'Mission')}", count_for_media=False)
        state.push("Système", f"Quete acceptee: {quest.get('id', 'quest')}", count_for_media=False)
        branch_line = quest_branch_options_summary(quest)
        if branch_line:
            state.push(
                "Système",
                f"Choix de quete disponible: {branch_line}. Utilise /quest choose {quest.get('id')} <option_id>.",
                count_for_media=False,
            )

        update_quests_and_notify_fn(state)
    except Exception as e:
        state.push("Système", f"Echec generation quete: {e}", count_for_media=False)
    finally:
        state.quest_generation_in_progress.discard(npc_key)

    on_change()
    refresh_chat_messages_view()
