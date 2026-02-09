from nicegui import ui
from app.ui.state.game_state import GameState
from app.ui.components.inventory_grid import inventory_panel
from app.ui.components.quest_panel import quest_panel
from app.ui.components.player_sheet_panel import player_sheet_panel
from app.ui.components.skills_panel import skills_panel
from app.ui.components.npc_panel import npc_panel
from app.ui.components.world_map import world_map_panel


def left_panel(state: GameState, on_change) -> None:
    with ui.element("div").classes("w-full").style("max-width:100%; min-width:0; overflow:hidden;"):
        with ui.tabs(
            value=state.left_panel_tab,
            on_change=lambda e: setattr(state, "left_panel_tab", e.value),
        ).props("dense").classes("w-full") as tabs:
            ui.tab("carte", "Carte")
            ui.tab("pnj", "PNJ")
            ui.tab("inventaire", "Inventaire")
            ui.tab("quetes", "Quetes")
            ui.tab("competences", "Competences")
            ui.tab("fiche", "Fiche")

        with ui.tab_panels(tabs, value=state.left_panel_tab).props("animated").classes("w-full").style(
            "max-width:100%; min-width:0; overflow:hidden;"
        ):
            with ui.tab_panel("carte").classes("w-full").style("max-width:100%; min-width:0; overflow:hidden;"):
                world_map_panel(state, on_change)

            with ui.tab_panel("pnj").classes("w-full").style("max-width:100%; min-width:0; overflow:hidden;"):
                npc_panel(state, on_change)

            with ui.tab_panel("inventaire").classes("w-full").style("max-width:100%; min-width:0; overflow:hidden;"):
                inventory_panel(state, on_change)

            with ui.tab_panel("quetes").classes("w-full").style("max-width:100%; min-width:0; overflow:hidden;"):
                quest_panel(state, on_change)

            with ui.tab_panel("competences").classes("w-full").style("max-width:100%; min-width:0; overflow:hidden;"):
                skills_panel(state, on_change)

            with ui.tab_panel("fiche").classes("w-full").style("max-width:100%; min-width:0; overflow:hidden;"):
                player_sheet_panel(state, on_change)
