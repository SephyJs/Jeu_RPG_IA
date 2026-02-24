from __future__ import annotations

from nicegui import ui

from app.gamemaster.story_manager import story_status_text
from app.gamemaster.world_time import format_fantasy_datetime
from app.ui.state.game_state import GameState


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _quest_reward_summary(rewards: dict) -> str:
    if not isinstance(rewards, dict):
        return "Aucune"

    parts: list[str] = []
    gold = _as_int(rewards.get("gold"), 0)
    if gold > 0:
        parts.append(f"{gold} or")

    items_raw = rewards.get("items")
    if isinstance(items_raw, list):
        for item in items_raw[:4]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or "").strip() or "item"
            qty = max(1, _as_int(item.get("qty"), 1))
            parts.append(f"{item_id} x{qty}")

    discount = _as_int(rewards.get("shop_discount_pct"), 0)
    if discount > 0:
        parts.append(f"Reduction boutique {discount}%")

    temple_bonus = _as_int(rewards.get("temple_heal_bonus"), 0)
    if temple_bonus > 0:
        parts.append(f"Soin temple +{temple_bonus}")

    return ", ".join(parts) if parts else "Aucune"


def _status_text(status: str) -> str:
    if status == "completed":
        return "Terminee"
    if status == "failed":
        return "Echouee"
    return "En cours"


def _branching_summary(quest: dict) -> str:
    branching = quest.get("branching") if isinstance(quest.get("branching"), dict) else {}
    if not isinstance(branching, dict):
        return ""
    selected = str(branching.get("selected_label") or branching.get("selected_option_id") or "").strip()
    if selected:
        return f"Branche: {selected}"
    options = branching.get("options") if isinstance(branching.get("options"), list) else []
    if not options:
        return ""
    rows: list[str] = []
    for row in options[:4]:
        if not isinstance(row, dict):
            continue
        option_id = str(row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        if option_id and label:
            rows.append(f"{option_id}={label}")
    return "Choix: " + ", ".join(rows) if rows else ""


def quest_panel(state: GameState, on_change) -> None:
    _ = on_change
    ui.label("Quetes").classes("text-lg font-semibold")
    ui.label("Arc principal: " + story_status_text(state)).classes("text-xs opacity-70")

    quests = [q for q in state.quests if isinstance(q, dict)]
    if not quests:
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px; margin-top:8px;"):
            ui.label("Aucune quete pour le moment. Discute avec des PNJ pour en obtenir.").classes("opacity-70")
        return

    active = [q for q in quests if str(q.get("status") or "in_progress") == "in_progress"]
    completed = [q for q in quests if str(q.get("status") or "") == "completed"]

    with ui.row().classes("w-full flex-wrap gap-2").style("margin:6px 0 10px 0;"):
        ui.label(f"En cours: {len(active)}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
        ui.label(f"Terminees: {len(completed)}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )

    ordered = active + completed
    for quest in ordered[:30]:
        status = str(quest.get("status") or "in_progress")
        title = str(quest.get("title") or "Quete sans titre")
        description = str(quest.get("description") or "").strip()
        source = str(quest.get("source_npc_name") or "PNJ")

        objective = quest.get("objective", {}) if isinstance(quest.get("objective"), dict) else {}
        objective_label = str(objective.get("label") or objective.get("type") or "Objectif")

        progress = quest.get("progress", {}) if isinstance(quest.get("progress"), dict) else {}
        current = max(0, _as_int(progress.get("current"), 0))
        target = max(1, _as_int(progress.get("target"), 1))
        percent = min(max(current / float(target), 0.0), 1.0)

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px;"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(title).classes("text-sm font-semibold")
                ui.label(_status_text(status)).classes("text-sm opacity-75")

            ui.label(f"Donneur: {source}").classes("text-sm opacity-75").style("margin-top:2px;")
            if description:
                ui.label(description).classes("text-sm leading-6").style("margin-top:2px;")

            ui.label(f"Objectif: {objective_label} ({current}/{target})").classes("text-sm").style("margin-top:4px;")
            ui.linear_progress(value=percent).props("instant-feedback")

            deadline_minutes = max(0, _as_int(quest.get("deadline_world_time_minutes"), 0))
            if status == "in_progress" and deadline_minutes > 0:
                now = max(0, _as_int(getattr(state, "world_time_minutes", 0), 0))
                if now <= deadline_minutes:
                    ui.label(
                        "Echeance: " + format_fantasy_datetime(deadline_minutes),
                    ).classes("text-xs opacity-70").style("margin-top:2px;")
                else:
                    ui.label("Echeance depassee").classes("text-xs text-red-300").style("margin-top:2px;")

            branch_line = _branching_summary(quest)
            if branch_line:
                ui.label(branch_line).classes("text-xs opacity-75").style("margin-top:2px;")

            rewards = quest.get("rewards") if isinstance(quest.get("rewards"), dict) else {}
            ui.label(f"Recompenses: {_quest_reward_summary(rewards)}").classes("text-sm opacity-80").style("margin-top:4px;")
