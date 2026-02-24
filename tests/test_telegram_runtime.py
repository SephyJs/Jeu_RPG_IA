import asyncio

from app.gamemaster.schemas import TurnResult
from app.gamemaster.world_time import day_index
from app.core.data.item_manager import ItemDef
from app.core.save import SaveManager
from app.telegram.runtime import TelegramGameSession
from app.ui.state.game_state import Choice, GameState, Scene
from app.ui.state.inventory import ItemStack


def _build_session(state: GameState) -> TelegramGameSession:
    session = TelegramGameSession(
        chat_id=1,
        profile_key="pytest_telegram",
        profile_name="PyTest",
        slot=1,
        save_manager=SaveManager(slot_count=1),
    )
    session.state = state
    session.save = lambda: None  # type: ignore[method-assign]
    session._apply_world_progression = lambda: None  # type: ignore[attr-defined]
    return session


def _base_state() -> GameState:
    state = GameState()
    state.scenes = {
        "start": Scene(
            id="start",
            title="Lumeria - Place des Cendres",
            narrator_text="Ataryxia : Le vent souffle sur la place.",
            map_anchor="Lumeria",
            npc_names=["Guide"],
            choices=[],
        )
    }
    state.current_scene_id = "start"
    state.selected_npc = "Guide"
    return state


def test_travel_by_index_moves_player_when_destination_is_open() -> None:
    state = GameState()
    state.world_time_minutes = 10 * 60  # 10:00
    state.scenes = {
        "start": Scene(
            id="start",
            title="Lumeria - Ruelle des Lanternes",
            narrator_text="Ataryxia : Une ruelle animee.",
            map_anchor="Lumeria",
            npc_names=["Guide"],
            choices=[Choice(id="go_shop", label="Entrer dans la boutique", next_scene_id="boutique_01")],
        ),
        "boutique_01": Scene(
            id="boutique_01",
            title="Lumeria - Boutique des Brumes",
            narrator_text="Ataryxia : Des etageres chargees de fioles.",
            map_anchor="Lumeria",
            npc_names=["Marchand"],
            choices=[],
        ),
    }
    state.current_scene_id = "start"
    state.selected_npc = "Guide"

    session = _build_session(state)
    options = session.travel_options()

    assert len(options) == 1
    assert options[0].is_open is True
    assert options[0].is_building is True

    output = asyncio.run(session.travel_by_index(0))

    assert state.current_scene_id == "boutique_01"
    assert state.world_time_minutes == (10 * 60) + 8
    assert "Vous arrivez : Lumeria - Boutique des Brumes" in output.text
    assert "PNJ actif: Marchand" in output.text


def test_travel_by_index_refuses_closed_destination() -> None:
    state = GameState()
    state.world_time_minutes = 2 * 60  # 02:00
    state.scenes = {
        "start": Scene(
            id="start",
            title="Lumeria - Ruelle des Lanternes",
            narrator_text="Ataryxia : Une ruelle silencieuse.",
            map_anchor="Lumeria",
            npc_names=["Guide"],
            choices=[Choice(id="go_shop", label="Entrer dans la boutique", next_scene_id="boutique_01")],
        ),
        "boutique_01": Scene(
            id="boutique_01",
            title="Lumeria - Boutique des Brumes",
            narrator_text="Ataryxia : Les volets sont clos.",
            map_anchor="Lumeria",
            npc_names=["Marchand"],
            choices=[],
        ),
    }
    state.current_scene_id = "start"
    state.selected_npc = "Guide"

    session = _build_session(state)
    options = session.travel_options()

    assert len(options) == 1
    assert options[0].is_open is False

    output = asyncio.run(session.travel_by_index(0))

    assert state.current_scene_id == "start"
    assert "ferme" in output.text.lower()


def test_set_telegram_mode_persists_flag() -> None:
    state = _base_state()
    session = _build_session(state)

    mode = session.set_telegram_mode("dungeon")

    assert mode == "dungeon"
    assert session.telegram_mode() == "dungeon"
    assert isinstance(state.gm_state.get("flags"), dict)
    assert state.gm_state["flags"].get("telegram_mode") == "dungeon"


def test_dungeon_enter_or_resume_initializes_run() -> None:
    state = _base_state()
    session = _build_session(state)

    async def _fake_profile(cache, anchor):
        return {
            "name": f"Abysses de {anchor}",
            "theme": "sombre",
            "entry_text": "Ataryxia : Les portes du donjon s'ouvrent.",
            "monster_pool": ["goule"],
            "treasure_pool": ["fragment"],
        }

    session._dungeon_manager.ensure_dungeon_profile = _fake_profile  # type: ignore[attr-defined]
    session._dungeon_manager.start_run = lambda anchor, profile: {  # type: ignore[attr-defined]
        "anchor": anchor,
        "dungeon_name": profile.get("name", "Donjon"),
        "entry_text": profile.get("entry_text", ""),
        "total_floors": 12,
        "current_floor": 0,
        "floors": [],
        "completed": False,
        "run_relic": {},
    }

    output = asyncio.run(session.dungeon_enter_or_resume())

    assert isinstance(state.active_dungeon_run, dict)
    assert state.selected_npc is None
    assert "Mode Donjon actif" in output.text


def test_dungeon_use_consumable_heals_and_consumes_stack() -> None:
    state = _base_state()
    state.player.max_hp = 20
    state.player.hp = 6
    state.item_defs = {
        "potion_soin_01": ItemDef(
            id="potion_soin_01",
            name="Potion de soin",
            stack_max=8,
            type="consumable",
            slot="",
            rarity="common",
            description="",
            stat_bonuses={},
            effects=[{"kind": "heal", "value": 6}],
            value_gold=12,
        )
    }
    state.carried.slots[0] = ItemStack(item_id="potion_soin_01", qty=1)
    session = _build_session(state)

    output = asyncio.run(session.dungeon_use_consumable("potion_soin_01"))

    assert "Consommable utilise" in output.text
    assert state.player.hp > 6
    assert session._inventory_qty("potion_soin_01") == 0


def test_dungeon_skill_heal_works_outside_combat() -> None:
    state = _base_state()
    state.player.max_hp = 24
    state.player.hp = 7
    state.active_dungeon_run = {
        "completed": False,
        "dungeon_name": "Abysses de test",
        "current_floor": 1,
        "total_floors": 5,
    }
    state.player_skills = [
        {
            "skill_id": "soin_base",
            "name": "Soin de base",
            "category": "soin",
            "description": "Restaure des PV.",
            "level": 3,
            "rank": 1,
            "primary_stats": ["sagesse", "magie"],
        }
    ]
    session = _build_session(state)

    output = asyncio.run(session.dungeon_combat_action("heal"))

    assert "Competence hors combat" in output.text
    assert "Soin hors combat" in output.text
    assert state.player.hp > 7


def test_dungeon_skill_buff_works_outside_combat() -> None:
    state = _base_state()
    state.active_dungeon_run = {
        "completed": False,
        "dungeon_name": "Abysses de test",
        "current_floor": 1,
        "total_floors": 5,
    }
    state.player_skills = [
        {
            "skill_id": "aura_garde",
            "name": "Aura de garde",
            "category": "soutien",
            "description": "Renforce la defense et la posture.",
            "level": 5,
            "rank": 2,
            "primary_stats": ["defense", "sagesse"],
        }
    ]
    session = _build_session(state)

    output = asyncio.run(session.dungeon_combat_action("spell"))

    assert "Competence hors combat" in output.text
    assert "Boost hors combat" in output.text
    flags = state.gm_state.get("flags") if isinstance(state.gm_state, dict) else {}
    buffs = flags.get("active_consumable_buffs") if isinstance(flags, dict) else None
    assert isinstance(buffs, list) and buffs


def test_ataryxia_mode_strips_duplicate_speaker_prefix() -> None:
    state = _base_state()
    state.player_sheet_ready = True
    session = _build_session(state)

    async def _fake_play_turn(_state, _text):
        return TurnResult(
            mode="auto",
            speaker="Ataryxia",
            dialogue="Ataryxia : Ataryxia: Je te parle sans detour.",
            narration="Ataryxia : Le vent se leve.",
        )

    session._gm.play_turn = _fake_play_turn  # type: ignore[method-assign]

    output = asyncio.run(session.process_ataryxia_message("Parle-moi."))

    lower = output.text.casefold()
    assert "ataryxia :" not in lower
    assert "ataryxia:" not in lower
    assert "je te parle sans detour" in lower


def test_ataryxia_nearby_world_event_intervention_is_handled_before_ai() -> None:
    state = _base_state()
    state.player_sheet_ready = True
    state.world_time_minutes = 8 * 24 * 60 + 9 * 60
    flags = state.gm_state.get("flags") if isinstance(state.gm_state, dict) else {}
    if not isinstance(flags, dict):
        state.gm_state["flags"] = {}
        flags = state.gm_state["flags"]
    flags["world_event_incident"] = {
        "id": "test_incident",
        "anchor": "Lumeria",
        "label": "Incident proche.",
        "success_text": "Situation stabilisee.",
        "failure_text": "Situation degradee.",
        "resolved": False,
        "dismissed": False,
        "day": day_index(state.world_time_minutes),
    }

    session = _build_session(state)
    called = {"value": False}

    async def _should_not_run(_state, _text):
        called["value"] = True
        return TurnResult(mode="auto", speaker="Ataryxia", dialogue="Ne devrait pas arriver")

    session._gm.play_turn = _should_not_run  # type: ignore[method-assign]

    output = asyncio.run(session.process_ataryxia_message("j'interviens"))

    assert called["value"] is False
    assert "Intervention" in output.text
    incident = flags.get("world_event_incident") if isinstance(flags, dict) else {}
    assert isinstance(incident, dict)
    assert bool(incident.get("resolved", False)) is True
