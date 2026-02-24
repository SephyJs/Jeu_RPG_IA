from __future__ import annotations

from datetime import datetime

from nicegui import ui

from app.gamemaster.reputation_manager import ensure_reputation_state
from app.gamemaster.reputation_manager import reputation_tier
from app.ui.state.game_state import GameState


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _score_label(score: int) -> str:
    return reputation_tier(_safe_int(score, 0)).capitalize()


def _progress_from_score(score: int) -> float:
    value = max(-100, min(100, _safe_int(score, 0)))
    return max(0.0, min(1.0, (value + 100.0) / 200.0))


def _format_delta(delta: int) -> str:
    value = _safe_int(delta, 0)
    return f"+{value}" if value >= 0 else str(value)


def _format_log_time(raw_value: object) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return "--"
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return raw[:16]
    return parsed.strftime("%Y-%m-%d %H:%M")


def reputation_panel(state: GameState, on_change) -> None:
    _ = on_change
    ensure_reputation_state(state)
    ui.label("Reputation factions").classes("text-lg font-semibold")

    rows = sorted(
        state.faction_reputation.items(),
        key=lambda row: (-abs(_safe_int(row[1], 0)), str(row[0]).casefold()),
    )

    positive_count = len([row for row in rows if _safe_int(row[1], 0) > 0])
    negative_count = len([row for row in rows if _safe_int(row[1], 0) < 0])
    neutral_count = len(rows) - positive_count - negative_count

    with ui.row().classes("w-full flex-wrap gap-2").style("margin:6px 0 10px 0;"):
        ui.label(f"Factions: {len(rows)}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
        ui.label(f"Positif: {positive_count}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
        ui.label(f"Negatif: {negative_count}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )
        ui.label(f"Neutre: {neutral_count}").classes("text-sm rounded-md").style(
            "padding:4px 10px; border:1px solid rgba(255,255,255,0.16); background:rgba(255,255,255,0.04);"
        )

    if not rows:
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:12px;"):
            ui.label("Aucune reputation enregistree pour le moment.").classes("opacity-70")
    else:
        for faction, raw_score in rows[:30]:
            score = max(-100, min(100, _safe_int(raw_score, 0)))
            sign = "+" if score >= 0 else ""
            with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px;"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(str(faction)).classes("text-sm font-semibold")
                    ui.label(f"{sign}{score}").classes("text-sm")
                ui.linear_progress(value=_progress_from_score(score)).props("instant-feedback")
                ui.label(f"Etat: {_score_label(score)}").classes("text-xs opacity-75").style("margin-top:2px;")

    ui.separator()
    ui.label("Historique recent").classes("text-sm font-semibold")

    log_rows = state.faction_reputation_log if isinstance(state.faction_reputation_log, list) else []
    if not log_rows:
        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:10px 12px;"):
            ui.label("Aucun evenement de reputation.").classes("opacity-70")
        return

    for raw_entry in list(reversed(log_rows[-40:])):
        if not isinstance(raw_entry, dict):
            continue
        faction = str(raw_entry.get("faction") or "Faction")
        delta = _safe_int(raw_entry.get("delta"), 0)
        after = _safe_int(raw_entry.get("after"), 0)
        reason = str(raw_entry.get("reason") or "").strip()
        source = str(raw_entry.get("source") or "").strip()
        timestamp = _format_log_time(raw_entry.get("at"))

        with ui.card().classes("w-full rounded-xl shadow-sm").style("padding:8px 10px;"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(faction).classes("text-sm font-semibold")
                ui.label(f"{_format_delta(delta)} -> {after}").classes("text-xs")
            with ui.row().classes("w-full items-center justify-between"):
                if reason:
                    ui.label(f"Motif: {reason}").classes("text-xs opacity-75")
                else:
                    ui.label("Motif: n/a").classes("text-xs opacity-70")
                suffix = f" | {source}" if source else ""
                ui.label(f"{timestamp}{suffix}").classes("text-xs opacity-60")
