from __future__ import annotations

from nicegui import ui

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


def quest_panel(state: GameState, on_change) -> None:
    _ = on_change
    ui.label("Quetes").classes("text-lg font-semibold")
    ui.separator()

    quests = [q for q in state.quests if isinstance(q, dict)]
    if not quests:
        ui.label("Aucune quete pour le moment. Discute avec des PNJ pour en obtenir.").classes("opacity-70")
        return

    active = [q for q in quests if str(q.get("status") or "in_progress") == "in_progress"]
    completed = [q for q in quests if str(q.get("status") or "") == "completed"]

    ui.label(f"En cours: {len(active)} | Terminees: {len(completed)}").classes("text-sm opacity-80")

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

        with ui.card().classes("w-full rounded-xl shadow-sm"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(title).classes("font-semibold")
                ui.label(_status_text(status)).classes("text-xs opacity-80")

            ui.label(f"Donneur: {source}").classes("text-xs opacity-70")
            if description:
                ui.label(description).classes("text-sm")

            ui.label(f"Objectif: {objective_label} ({current}/{target})").classes("text-sm")
            ui.linear_progress(value=percent).props("instant-feedback")

            rewards = quest.get("rewards") if isinstance(quest.get("rewards"), dict) else {}
            ui.label(f"Recompenses: {_quest_reward_summary(rewards)}").classes("text-xs opacity-80")
