from nicegui import ui
from app.ui.state.game_state import GameState
from app.ui.components.inventory_grid import inventory_panel
from app.ui.components.quest_panel import quest_panel
from app.ui.components.player_sheet_panel import player_sheet_panel
from app.ui.components.skills_panel import skills_panel
from app.ui.components.npc_panel import npc_panel
from app.ui.components.reputation_panel import reputation_panel
from app.ui.components.world_map import world_map_panel


_LEFT_PANEL_OPTIONS = [
    ("carte", "Carte"),
    ("pnj", "PNJ"),
    ("inventaire", "Inventaire"),
    ("quetes", "Quetes"),
    ("competences", "Competences"),
    ("reputation", "Reputation"),
    ("fiche", "Fiche"),
]


def _render_left_panel_content(state: GameState, on_change) -> None:
    tab = str(getattr(state, "left_panel_tab", "") or "carte")
    if tab == "carte":
        world_map_panel(state, on_change)
        return
    if tab == "pnj":
        npc_panel(state, on_change)
        return
    if tab == "inventaire":
        inventory_panel(state, on_change)
        return
    if tab == "quetes":
        quest_panel(state, on_change)
        return
    if tab == "competences":
        skills_panel(state, on_change)
        return
    if tab == "reputation":
        reputation_panel(state, on_change)
        return
    if tab == "fiche":
        player_sheet_panel(state, on_change)
        return
    state.left_panel_tab = "carte"
    world_map_panel(state, on_change)


def left_panel(state: GameState, on_change, *, mobile_menu: bool = False) -> None:
    allowed_tabs = {key for key, _ in _LEFT_PANEL_OPTIONS}
    if str(getattr(state, "left_panel_tab", "") or "") not in allowed_tabs:
        state.left_panel_tab = "carte"
    refresh_holder: dict[str, object] = {"refresh": None}
    container_overflow = "overflow:visible;" if mobile_menu else "overflow-x:hidden; overflow-y:visible;"

    def _set_left_tab(value: object) -> None:
        tab = str(value or "").strip()
        if tab not in allowed_tabs:
            tab = "carte"
        if tab == state.left_panel_tab:
            return
        state.left_panel_tab = tab
        refresh = refresh_holder.get("refresh")
        if callable(refresh):
            refresh()

    with ui.element("div").classes("w-full").style(f"max-width:100%; min-width:0; {container_overflow}"):
        with ui.tabs(
            value=state.left_panel_tab,
            on_change=lambda e: _set_left_tab(e.value),
        ).props("dense mobile-arrows outside-arrows indicator-color=primary").classes("w-full"):
            for key, label in _LEFT_PANEL_OPTIONS:
                ui.tab(key, label).classes("text-xs px-2")

        @ui.refreshable
        def render_active_tab() -> None:
            with ui.element("div").classes("w-full").style(f"max-width:100%; min-width:0; {container_overflow}"):
                _render_left_panel_content(state, on_change)

        refresh_holder["refresh"] = render_active_tab.refresh
        render_active_tab()
