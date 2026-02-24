from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field

from app.core.events import OnLocationEntered, get_global_event_bus
from app.core.data.data_manager import DataError, DataManager
from app.core.data.item_manager import ItemsManager
from app.core.save import SaveManager
from app.gamemaster.conversation_memory import (
    build_retrieved_context,
    build_global_memory_context,
    build_long_term_context,
    build_short_term_context,
    ensure_conversation_memory_state,
    remember_dialogue_turn,
    remember_system_event,
)
from app.gamemaster.dungeon_combat import build_combat_state, is_combat_event, resolve_combat_turn
from app.gamemaster.dungeon_manager import DungeonManager
from app.gamemaster.economy_manager import EconomyManager
from app.gamemaster.gamemaster import GameMaster
from app.gamemaster.gm_state_builder import apply_base_gm_state
from app.gamemaster.location_manager import LocationManager, is_building_scene_title, scene_open_status
from app.gamemaster.loot_manager import LootManager
from app.gamemaster.monster_manager import MonsterManager
from app.gamemaster.npc_manager import (
    NPCProfileManager,
    npc_profile_key,
    profile_display_name,
    profile_summary_line,
)
from app.gamemaster.ollama_client import OllamaClient
from app.gamemaster.player_sheet_manager import PlayerSheetManager
from app.gamemaster.reputation_manager import apply_dungeon_reputation, can_access_scene_by_reputation
from app.gamemaster.story_manager import progress_main_story
from app.gamemaster.world_events import apply_world_time_events, try_resolve_nearby_world_event
from app.gamemaster.world_time import format_fantasy_datetime
from app.infra import text_library as _text_library
from app.ui.components.consumables import add_consumable_stat_buff, get_consumable_stat_bonus_totals, tick_consumable_buffs
from app.ui.state.game_state import GameState


@dataclass
class TurnOutput:
    text: str
    has_pending_trade: bool = False
    generated_image_prompt: str | None = None


@dataclass(frozen=True)
class TravelOption:
    label: str
    next_scene_id: str
    destination_title: str
    destination_anchor: str
    is_building: bool
    is_open: bool
    status_hint: str


@dataclass(frozen=True)
class DungeonConsumableOption:
    item_id: str
    item_name: str
    qty: int


TELEGRAM_MODE_DUNGEON = "dungeon"
TELEGRAM_MODE_ATARYXIA = "ataryxia"
_TELEGRAM_MODE_SET = {TELEGRAM_MODE_DUNGEON, TELEGRAM_MODE_ATARYXIA}


def _text(key: str, **vars: object) -> str:
    return _text_library.pick(key, **vars)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().casefold()
    if raw in {"1", "true", "on", "yes", "y"}:
        return True
    if raw in {"0", "false", "off", "no", "n"}:
        return False
    return bool(default)


_ATARYXIA_FREEFORM_DEFAULT = _env_bool("TELEGRAM_ATARYXIA_FREEFORM_DEFAULT", False)


@dataclass
class TelegramGameSession:
    chat_id: int
    profile_key: str
    profile_name: str
    slot: int
    save_manager: SaveManager
    data_dir: str = "data"
    state: GameState | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._llm = OllamaClient()
        self._gm = GameMaster(self._llm, seed=123)
        self._location_seed = LocationManager(None)
        self._items_manager = ItemsManager(data_dir=self.data_dir)
        self._economy_manager = EconomyManager(data_dir=self.data_dir)
        self._dungeon_manager = DungeonManager(self._llm)
        self._monster_manager = MonsterManager(data_dir="data/monsters")
        self._loot_manager = LootManager(self._llm, data_dir=self.data_dir)
        self._npc_store = NPCProfileManager(self._llm)
        self._player_sheet_manager = PlayerSheetManager(self._llm)
        self._event_bus = get_global_event_bus()

    async def load_or_create(self) -> None:
        state = self._build_initial_state()
        loaded = self.save_manager.load_slot(self.slot, state, profile=self.profile_key)
        self.state = state

        if loaded:
            self._refresh_static_scenes_from_data()
            self._ensure_player_sheet_ready()
            self._inject_creation_intro_once()
            if self.save_manager.last_warning:
                state.push("Systeme", self.save_manager.last_warning, count_for_media=False)
        else:
            self._ensure_player_sheet_ready()
            self._inject_creation_intro_once()
            self._sync_gm_state()
            self.save()

        self._ensure_selected_npc()
        self._sync_gm_state()
        self.set_telegram_mode(self.telegram_mode())

    def short_status_line(self) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")
        return _text(
            "system.status.short",
            profile_key=self.profile_key,
            slot=self.slot,
            location=self.state.current_scene().title,
        )

    def save(self) -> None:
        if self.state is None:
            return
        try:
            self.save_manager.save_slot(
                self.slot,
                self.state,
                profile=self.profile_key,
                display_name=self.profile_name,
            )
        except Exception as e:
            self.state.push("Systeme", _text("error.save.failed", error=e), count_for_media=False)

    def _apply_world_progression(self) -> None:
        if self.state is None:
            return
        in_dungeon = self._active_dungeon_run() is not None or self.telegram_mode() == TELEGRAM_MODE_DUNGEON
        current_anchor = str(self.state.current_scene().map_anchor or self.state.current_scene().title or "Lumeria")
        for line in apply_world_time_events(
            self.state,
            current_anchor=current_anchor,
            in_dungeon=in_dungeon,
        ):
            if isinstance(line, str) and line.strip():
                self.state.push("Systeme", line.strip(), count_for_media=False)
        for line in progress_main_story(self.state):
            if isinstance(line, str) and line.strip():
                self.state.push("Systeme", line.strip(), count_for_media=False)

    def scene_npcs(self) -> list[str]:
        if self.state is None:
            return []
        return list(getattr(self.state.current_scene(), "npc_names", []) or [])

    def travel_options(self) -> list[TravelOption]:
        if self.state is None:
            return []

        scene = self.state.current_scene()
        options: list[TravelOption] = []
        seen_targets: set[str] = set()

        for choice in scene.choices:
            target_id = str(choice.next_scene_id or "").strip()
            if not target_id or target_id in seen_targets:
                continue

            target_scene = self.state.scenes.get(target_id)
            if target_scene is None:
                continue
            seen_targets.add(target_id)

            label = str(choice.label or "").strip() or _text("system.travel.option.default", destination=target_scene.title)
            is_open, status_hint = scene_open_status(target_scene, self.state.world_time_minutes)
            can_access, rep_hint = can_access_scene_by_reputation(
                self.state,
                scene_id=target_scene.id,
                scene_title=target_scene.title,
            )
            open_state = bool(is_open and can_access)
            merged_hint = status_hint
            if not can_access and rep_hint:
                if merged_hint:
                    merged_hint = f"{merged_hint} | {rep_hint}"
                else:
                    merged_hint = rep_hint
            options.append(
                TravelOption(
                    label=label,
                    next_scene_id=target_id,
                    destination_title=str(target_scene.title),
                    destination_anchor=str(target_scene.map_anchor or ""),
                    is_building=is_building_scene_title(str(target_scene.title or "")),
                    is_open=open_state,
                    status_hint=str(merged_hint or "").strip(),
                )
            )
        return options

    async def travel_by_index(self, option_index: int) -> TurnOutput:
        options = self.travel_options()
        if option_index < 0 or option_index >= len(options):
            return TurnOutput(text=_text("error.invalid_move_obsolete"), has_pending_trade=bool(self.pending_trade()))
        return self._apply_travel(options[option_index])

    async def travel_to_scene(self, scene_id: str) -> TurnOutput:
        target_id = str(scene_id or "").strip()
        if not target_id:
            return TurnOutput(text=_text("error.invalid_move"), has_pending_trade=bool(self.pending_trade()))

        for option in self.travel_options():
            if option.next_scene_id == target_id:
                return self._apply_travel(option)

        return TurnOutput(text=_text("error.invalid_move_obsolete"), has_pending_trade=bool(self.pending_trade()))

    def _apply_travel(self, option: TravelOption) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)
        if self.state.current_scene_id == option.next_scene_id:
            return TurnOutput(
                text=_text("system.travel.already_there", destination=option.destination_title),
                has_pending_trade=bool(self.pending_trade()),
            )

        if not option.is_open:
            closed_line = _text("narration.travel.closed", reason=option.status_hint) if option.status_hint else _text("narration.travel.default_closed")
            self.state.push("Systeme", closed_line, count_for_media=False)
            self.save()
            return TurnOutput(text=closed_line, has_pending_trade=bool(self.pending_trade()))

        origin = self.state.current_scene()
        self.state.push("Joueur", option.label)
        self.state.set_scene(option.next_scene_id)
        travel_minutes = 8 if option.is_building else 14
        self.state.advance_world_time(travel_minutes)
        self._apply_world_progression()
        self._ensure_selected_npc()

        selected_npc = str(self.state.selected_npc or "").strip() or None
        selected_npc_key = npc_profile_key(selected_npc, self.state.current_scene().id) if selected_npc else None
        selected_profile = self.state.npc_profiles.get(selected_npc_key) if selected_npc_key else None
        self._sync_gm_state(
            selected_npc=selected_npc,
            selected_npc_key=selected_npc_key,
            selected_profile=selected_profile if isinstance(selected_profile, dict) else None,
        )
        self.save()

        try:
            destination = self.state.current_scene()
            self._event_bus.publish(
                OnLocationEntered(
                    scene_id=destination.id,
                    scene_title=destination.title,
                    map_anchor=str(destination.map_anchor or ""),
                    context={
                        "from_scene_id": origin.id,
                        "from_scene_title": origin.title,
                        "selected_npc": str(selected_npc or ""),
                    },
                )
            )
            remember_system_event(
                self.state,
                fact_text=_text(
                    "system.memory.travel_fact",
                    from_location=origin.title,
                    to_location=destination.title,
                    minutes=travel_minutes,
                ),
                npc_key=selected_npc_key,
                npc_name=str(selected_npc or ""),
                scene_id=destination.id,
                scene_title=destination.title,
                world_time_minutes=self.state.world_time_minutes,
                kind="travel",
                importance=3,
            )
        except Exception:
            pass

        lines = [_text("system.travel.arrival", destination=self.state.current_scene().title)]
        if selected_npc:
            lines.append(_text("system.travel.npc_active", npc=selected_npc))
        else:
            lines.append(_text("system.travel.no_npc"))
        lines.append(_text("system.travel.world_time", world_time=format_fantasy_datetime(self.state.world_time_minutes)))
        narration = str(self.state.current_scene().narrator_text or "").strip()
        if narration:
            lines.append(_text("system.travel.narration", narration=narration))

        return TurnOutput(text="\n".join(lines), has_pending_trade=bool(self.pending_trade()))

    def status_text(self) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")

        scene = self.state.current_scene()
        equipped_items = self.state.equipped_items if isinstance(self.state.equipped_items, dict) else {}
        weapon = str(equipped_items.get("weapon") or "").strip() or _text("system.status.weapon_none")
        level = self._player_level(self.state)
        skill_count = len([x for x in self.state.player_skills if isinstance(x, dict)])
        npc = str(self.state.selected_npc or "").strip() or _text("system.status.npc_none")
        creation_state = _text("system.status.creation.ok") if self.state.player_sheet_ready else _text("system.status.creation.incomplete")

        return "\n".join(
            [
                _text("system.status.line.profile", profile=self.profile_name),
                _text("system.status.line.profile_id", profile_id=self.profile_key),
                _text("system.status.line.slot", slot=self.slot),
                _text("system.status.line.location", location=scene.title),
                _text("system.status.line.npc", npc=npc),
                _text("system.status.line.gold", gold=max(0, int(self.state.player.gold))),
                _text("system.status.line.level_skills", level=level, skills=skill_count),
                _text("system.status.line.weapon", weapon=weapon),
                _text("system.status.line.world_time", world_time=format_fantasy_datetime(self.state.world_time_minutes)),
                _text("system.status.line.creation", creation=creation_state),
            ]
        )

    def _gm_flags(self) -> dict:
        if self.state is None:
            return {}
        if not isinstance(self.state.gm_state, dict):
            self.state.gm_state = {}
        flags = self.state.gm_state.get("flags")
        if isinstance(flags, dict):
            return flags
        self.state.gm_state["flags"] = {}
        return self.state.gm_state["flags"]

    def telegram_mode(self) -> str:
        flags = self._gm_flags()
        raw = str(flags.get("telegram_mode") or TELEGRAM_MODE_ATARYXIA).strip().casefold()
        return raw if raw in _TELEGRAM_MODE_SET else TELEGRAM_MODE_ATARYXIA

    def set_telegram_mode(self, mode: str) -> str:
        normalized = str(mode or "").strip().casefold()
        if normalized not in _TELEGRAM_MODE_SET:
            normalized = TELEGRAM_MODE_ATARYXIA
        flags = self._gm_flags()
        flags["telegram_mode"] = normalized
        return normalized

    def in_dungeon(self) -> bool:
        return self._active_dungeon_run() is not None

    def dungeon_has_active_combat(self) -> bool:
        run = self._active_dungeon_run()
        return self._active_dungeon_combat(run) is not None

    def pending_trade(self) -> dict | None:
        if self.state is None:
            return None
        gm_state = self.state.gm_state if isinstance(self.state.gm_state, dict) else {}
        pending = gm_state.get("pending_trade") if isinstance(gm_state.get("pending_trade"), dict) else None
        if not isinstance(pending, dict):
            return None

        selected_npc = str(self.state.selected_npc or "").strip().casefold()
        pending_npc = str(pending.get("npc_name") or "").strip().casefold()
        if selected_npc and pending_npc and pending_npc != selected_npc:
            return None
        return dict(pending)

    def pending_trade_summary(self) -> str:
        pending = self.pending_trade()
        if not pending:
            return ""

        action = str(pending.get("action") or "trade").strip().casefold()
        item = str(pending.get("item_name") or pending.get("item_id") or "objet").strip()
        qty = max(1, self._safe_int(pending.get("qty"), 1))
        unit_price = max(0, self._safe_int(pending.get("unit_price"), 0))
        total = qty * unit_price

        if action == "buy":
            if unit_price > 0:
                return _text("system.trade.pending.buy_full", item=item, qty=qty, total=total, unit_price=unit_price)
            return _text("system.trade.pending.buy", item=item, qty=qty)
        if action == "sell":
            if unit_price > 0:
                return _text("system.trade.pending.sell_full", item=item, qty=qty, total=total, unit_price=unit_price)
            return _text("system.trade.pending.sell", item=item, qty=qty)
        if action == "exchange":
            return _text("system.trade.pending.exchange", item=item, qty=qty)
        if action == "give":
            return _text("system.trade.pending.give", item=item, qty=qty)
        return _text("system.trade.pending.generic", item=item, qty=qty)

    async def select_npc(self, npc_name: str) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")

        npcs = self.scene_npcs()
        if not npcs:
            return _text("error.no_npc_in_scene")

        target = str(npc_name or "").strip()
        if target not in npcs:
            return _text("error.npc_not_found")

        self.state.selected_npc = target
        npc_key, profile = await self._ensure_selected_npc_profile()
        first_contact_line = self._consume_first_contact_line(target, profile)
        self._sync_gm_state(selected_npc=target, selected_npc_key=npc_key, selected_profile=profile)
        self.save()

        if isinstance(profile, dict):
            summary = _text("system.npc.active.summary", summary=profile_summary_line(profile, target))
            if first_contact_line:
                speaker = profile_display_name(profile, target)
                self.state.push(speaker, first_contact_line, count_for_media=False)
                self.save()
                return _text("system.npc.active.with_first_message", summary=summary, speaker=speaker, line=first_contact_line)
            return summary
        return _text("system.npc.active.simple", npc=target)

    async def confirm_pending_trade(self) -> TurnOutput:
        pending = self.pending_trade()
        if not pending:
            return TurnOutput(text=_text("system.trade.none_pending"), has_pending_trade=False)
        cmd = self._confirm_text_for_action(str(pending.get("action") or ""))
        return await self.process_user_message(cmd)

    async def cancel_pending_trade(self) -> TurnOutput:
        pending = self.pending_trade()
        if not pending:
            return TurnOutput(text=_text("system.trade.none_pending"), has_pending_trade=False)
        return await self.process_user_message(_text("system.trade.cancel_command"))

    def dungeon_status_text(self) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")
        run = self._active_dungeon_run()
        if not run:
            anchor = str(self.state.current_scene().map_anchor or self.state.current_scene().title or "Lumeria")
            return _text("system.dungeon.status.none", anchor=anchor)

        floor = max(0, self._safe_int(run.get("current_floor"), 0))
        total = max(0, self._safe_int(run.get("total_floors"), 0))
        lines = [
            _text("system.dungeon.status.name", dungeon_name=str(run.get("dungeon_name") or "Donjon")),
            _text("system.dungeon.status.floor", floor=floor, total=total),
            _text(
                "system.dungeon.status.hp",
                hp=max(0, self._safe_int(self.state.player.hp, 0)),
                max_hp=max(1, self._safe_int(self.state.player.max_hp, 1)),
            ),
            _text("system.dungeon.status.gold", gold=max(0, self._safe_int(self.state.player.gold, 0))),
        ]
        combat = self._active_dungeon_combat(run)
        if combat:
            enemy = str(combat.get("enemy_name") or "Adversaire")
            enemy_hp = max(0, self._safe_int(combat.get("enemy_hp"), 0))
            enemy_max_hp = max(1, self._safe_int(combat.get("enemy_max_hp"), enemy_hp))
            lines.append(_text("system.dungeon.status.combat", enemy=enemy, enemy_hp=enemy_hp, enemy_max_hp=enemy_max_hp))
        else:
            lines.append(_text("system.dungeon.status.combat_none"))
        return "\n".join(lines)

    async def dungeon_enter_or_resume(self) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        run = self._active_dungeon_run()
        if run:
            self.set_telegram_mode(TELEGRAM_MODE_DUNGEON)
            return TurnOutput(text=self.dungeon_status_text(), has_pending_trade=False)

        scene = self.state.current_scene()
        anchor = str(scene.map_anchor or scene.title or "Lumeria")
        try:
            profile = await self._dungeon_manager.ensure_dungeon_profile(self.state.dungeon_profiles, anchor)
            run = self._dungeon_manager.start_run(anchor, profile)
        except Exception as e:
            return TurnOutput(text=_text("error.dungeon.open_failed", error=e), has_pending_trade=False)

        self.state.active_dungeon_run = run
        self.state.selected_npc = None
        self.state.advance_world_time(18)
        self._apply_world_progression()
        self.set_telegram_mode(TELEGRAM_MODE_DUNGEON)
        self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
        self.save()

        lines = [
            str(run.get("entry_text") or _text("system.dungeon.entered", anchor=anchor)),
            _text(
                "system.dungeon.profile_line",
                dungeon_name=run.get("dungeon_name", "Donjon"),
                total_floors=run.get("total_floors", 0),
            ),
            _text("system.mode.dungeon_active"),
        ]
        return TurnOutput(text="\n".join(lines), has_pending_trade=False)

    async def dungeon_advance_floor(self) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        run = self._active_dungeon_run()
        if not run:
            return await self.dungeon_enter_or_resume()

        combat = self._active_dungeon_combat(run)
        if combat:
            return TurnOutput(text=_text("system.dungeon.combat_prompt"), has_pending_trade=False)

        event = self._dungeon_manager.advance_floor(run)
        if not isinstance(event, dict):
            self.state.active_dungeon_run = None
            self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
            self.save()
            return TurnOutput(text=_text("system.dungeon.finished"), has_pending_trade=False)

        self.state.advance_world_time(35)
        floor = max(1, self._safe_int(event.get("floor"), 1))
        total = max(1, self._safe_int(run.get("total_floors"), 1))
        lines = [_text("system.dungeon.floor_header", floor=floor, total=total), str(event.get("text") or _text("system.dungeon.floor_explored"))]

        if self._start_dungeon_combat(run, event):
            combat_now = self._active_dungeon_combat(run)
            enemy = str((combat_now or {}).get("enemy_name") or event.get("name") or "Adversaire")
            enemy_hp = max(1, self._safe_int((combat_now or {}).get("enemy_hp"), 1))
            lines.append(_text("system.dungeon.combat_engaged", enemy=enemy, hp=enemy_hp))
            self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
            self.save()
            return TurnOutput(text="\n".join(lines), has_pending_trade=False)

        clear_lines = await self._finalize_dungeon_floor_clear(run=run, event=event)
        lines.extend(clear_lines)
        self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
        self.save()
        return TurnOutput(text="\n".join(lines), has_pending_trade=False)

    async def dungeon_combat_action(self, action: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        action_key = str(action or "").strip().casefold()
        run = self._active_dungeon_run()
        combat = self._active_dungeon_combat(run)
        if action_key in {"heal", "spell", "skill"} and run and not combat:
            return await self._dungeon_non_combat_skill_action(action_key)
        if not run or not combat:
            return TurnOutput(text=_text("system.dungeon.none_combat"), has_pending_trade=False)

        if action_key == "flee":
            output = self._attempt_dungeon_flee(run, combat)
            self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
            self.save()
            return output

        action_text = self._combat_action_text(action_key)
        runtime_stat_bonuses = get_consumable_stat_bonus_totals(self.state)
        result = resolve_combat_turn(
            combat_state=combat,
            action_text=action_text,
            player_hp=self._safe_int(getattr(self.state.player, "hp", 0), 0),
            player_max_hp=self._safe_int(getattr(self.state.player, "max_hp", 1), 1),
            player_sheet=self.state.player_sheet if isinstance(self.state.player_sheet, dict) else {},
            known_skills=self.state.player_skills if isinstance(self.state.player_skills, list) else [],
            runtime_stat_bonuses=runtime_stat_bonuses,
            skill_manager=None,
            rng=self._loot_manager.rng,
            run_relic=run.get("run_relic") if isinstance(run.get("run_relic"), dict) else None,
        )

        run["combat"] = result.get("combat") if isinstance(result.get("combat"), dict) else combat
        self._set_player_hp_from_combat(self._safe_int(result.get("player_hp"), self.state.player.hp))
        lines = [str(line).strip() for line in (result.get("lines") if isinstance(result.get("lines"), list) else []) if str(line).strip()]

        expired_buffs = tick_consumable_buffs(self.state)
        for buff in expired_buffs:
            if not isinstance(buff, dict):
                continue
            label = str(buff.get("item_name") or buff.get("stat") or "bonus").strip()
            if label:
                lines.append(_text("system.dungeon.buff_expired", label=label))

        self.state.advance_world_time(6)
        self._apply_world_progression()

        if bool(result.get("defeat", False)):
            recovery = max(1, self._safe_int(self.state.player.max_hp, 1) // 2)
            self._set_player_hp_from_combat(recovery)
            self.state.active_dungeon_run = None
            lines.append(_text("system.skill.out_of_combat.rescue"))
            lines.append(_text("system.skill.out_of_combat.recovery", hp=recovery, max_hp=self.state.player.max_hp))
            self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
            self.save()
            return TurnOutput(text="\n".join(lines), has_pending_trade=False)

        if bool(result.get("victory", False)):
            event = run.get("current_event") if isinstance(run.get("current_event"), dict) else {}
            if not event:
                event = {
                    "type": str(combat.get("event_type") or "monster"),
                    "floor": self._safe_int(combat.get("floor"), self._safe_int(run.get("current_floor"), 1)),
                    "name": str(combat.get("enemy_name") or "Adversaire"),
                }
            clear_lines = await self._finalize_dungeon_floor_clear(run=run, event=event)
            lines.extend(clear_lines)
            self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
            self.save()
            return TurnOutput(text="\n".join(lines), has_pending_trade=False)

        combat_now = self._active_dungeon_combat(run)
        if combat_now:
            enemy = str(combat_now.get("enemy_name") or "Adversaire")
            enemy_hp = max(0, self._safe_int(combat_now.get("enemy_hp"), 0))
            enemy_max_hp = max(1, self._safe_int(combat_now.get("enemy_max_hp"), enemy_hp))
            lines.append(
                _text(
                    "system.dungeon.state_line",
                    enemy=enemy,
                    enemy_hp=enemy_hp,
                    enemy_max_hp=enemy_max_hp,
                    player_hp=self.state.player.hp,
                    player_max_hp=self.state.player.max_hp,
                )
            )

        self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
        self.save()
        return TurnOutput(text="\n".join(lines or [_text("system.dungeon.turn_resolved")]), has_pending_trade=False)

    async def _dungeon_non_combat_skill_action(self, action_key: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        run = self._active_dungeon_run()
        if not run:
            return TurnOutput(text=_text("system.dungeon.none_active"), has_pending_trade=False)
        if self._active_dungeon_combat(run):
            return TurnOutput(text=_text("error.combat_active_buttons"), has_pending_trade=False)

        action = str(action_key or "").strip().casefold()
        rows = [row for row in self.state.player_skills if isinstance(row, dict)] if isinstance(self.state.player_skills, list) else []
        if not rows:
            return TurnOutput(text=_text("error.skill.none_known"), has_pending_trade=False)

        selected = self._pick_non_combat_skill_for_action(rows, action)
        if not isinstance(selected, dict):
            if action == "heal":
                return TurnOutput(text=_text("error.skill.no_heal_outside"), has_pending_trade=False)
            if action == "spell":
                return TurnOutput(text=_text("error.skill.no_support_outside"), has_pending_trade=False)
            return TurnOutput(text=_text("error.skill.no_usable_outside"), has_pending_trade=False)

        skill_name = str(selected.get("name") or selected.get("skill_id") or "Competence").strip() or "Competence"
        support_kind = self._non_combat_support_kind(selected)
        lines = [_text("system.skill.out_of_combat.header", skill=skill_name)]
        if support_kind == "heal":
            lines.append(self._apply_non_combat_heal_skill(selected))
        elif support_kind == "buff":
            lines.append(self._apply_non_combat_buff_skill(selected))
        else:
            lines.append(_text("system.skill.out_of_combat.offensive_only"))
            return TurnOutput(text="\n".join(lines), has_pending_trade=False)

        self.state.advance_world_time(3)
        self._apply_world_progression()
        self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
        self.save()
        return TurnOutput(text="\n".join(lines), has_pending_trade=False)

    def dungeon_consumables(self) -> list[DungeonConsumableOption]:
        if self.state is None:
            return []
        totals = self._economy_manager.inventory_totals(self.state)
        out: list[DungeonConsumableOption] = []
        for item_id, qty in totals.items():
            clean_id = str(item_id or "").strip().casefold()
            if qty <= 0 or not clean_id:
                continue
            item = self.state.item_defs.get(clean_id) if isinstance(self.state.item_defs, dict) else None
            item_type = str(getattr(item, "type", "") or "").strip().casefold()
            if item_type != "consumable":
                continue
            effects_raw = getattr(item, "effects", None)
            effects = effects_raw if isinstance(effects_raw, list) else []
            if not any(isinstance(row, dict) and str(row.get("kind") or "").strip().casefold() in {"heal", "mana", "stat_buff"} for row in effects):
                continue
            name = str(getattr(item, "name", clean_id) or clean_id).strip() or clean_id
            out.append(DungeonConsumableOption(item_id=clean_id, item_name=name, qty=max(1, int(qty))))
        out.sort(key=lambda row: row.item_name)
        return out[:24]

    async def dungeon_use_consumable(self, item_id: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)
        clean_id = str(item_id or "").strip().casefold()
        if not clean_id:
            return TurnOutput(text=_text("error.consumable.invalid"), has_pending_trade=False)
        if self._inventory_qty(clean_id) <= 0:
            return TurnOutput(text=_text("error.consumable.unavailable"), has_pending_trade=False)

        item = self.state.item_defs.get(clean_id) if isinstance(self.state.item_defs, dict) else None
        item_name = str(getattr(item, "name", clean_id) or clean_id).strip() or clean_id
        item_type = str(getattr(item, "type", "") or "").strip().casefold()
        if item_type != "consumable":
            return TurnOutput(text=_text("error.consumable.not_consumable"), has_pending_trade=False)

        effects_raw = getattr(item, "effects", None)
        effects = [row for row in effects_raw if isinstance(row, dict)] if isinstance(effects_raw, list) else []
        if not effects:
            return TurnOutput(text=_text("error.consumable.no_effect"), has_pending_trade=False)

        applied_any = False
        lines = [_text("system.dungeon.item_used", item_name=item_name)]
        for effect in effects[:6]:
            line, applied = self._apply_consumable_effect(clean_id, item_name, effect)
            if line:
                lines.append(line)
            if applied:
                applied_any = True
        if not applied_any:
            return TurnOutput(text=_text("error.consumable.none_applied"), has_pending_trade=False)

        removed = self._remove_item_from_inventory(clean_id, 1)
        if removed <= 0:
            return TurnOutput(text=_text("error.consumable.inventory_fail"), has_pending_trade=False)

        self.state.advance_world_time(2)
        self._apply_world_progression()
        self._sync_gm_state(selected_npc=None, selected_npc_key=None, selected_profile=None)
        self.save()
        return TurnOutput(text="\n".join(lines), has_pending_trade=False)

    async def process_ataryxia_message(self, text: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        user_text = str(text or "").strip()
        if not user_text:
            return TurnOutput(text=_text("system.message.empty"), has_pending_trade=False)

        if not self.state.player_sheet_ready:
            return await self.process_creation_message(user_text)

        self.set_telegram_mode(TELEGRAM_MODE_ATARYXIA)
        self.state.push("Joueur", user_text)
        self.state.gm_state["conversation_last_player_line"] = user_text
        world_intervention_lines = try_resolve_nearby_world_event(self.state, user_text)
        if world_intervention_lines:
            for line in world_intervention_lines:
                clean = str(line or "").strip()
                if clean:
                    self.state.push("Systeme", clean, count_for_media=False)
            self.state.advance_world_time(4)
            self._apply_world_progression()
            self.save()
            return TurnOutput(text="\n".join(world_intervention_lines), has_pending_trade=False)
        scene = self.state.current_scene()

        ataryxia_key = "telegram_ataryxia"
        ataryxia_profile = self.state.npc_profiles.get(ataryxia_key) if isinstance(self.state.npc_profiles.get(ataryxia_key), dict) else {}
        if not isinstance(ataryxia_profile, dict):
            ataryxia_profile = {}
        if not ataryxia_profile:
            ataryxia_profile = self._default_telegram_ataryxia_profile()
        else:
            defaults = self._default_telegram_ataryxia_profile()
            for key, value in defaults.items():
                if key not in ataryxia_profile:
                    ataryxia_profile[key] = value
        ataryxia_profile = self._apply_telegram_ataryxia_persona(ataryxia_profile)
        self.state.npc_profiles[ataryxia_key] = ataryxia_profile
        self._sync_gm_state(
            selected_npc="Ataryxia",
            selected_npc_key=ataryxia_key,
            selected_profile=ataryxia_profile,
        )

        flags = self._gm_flags()
        flags["telegram_ataryxia_mode"] = True
        flags["telegram_dungeon_mode"] = False
        flags["telegram_ataryxia_freeform"] = bool(_ATARYXIA_FREEFORM_DEFAULT)
        flags["telegram_ataryxia_sms_mode"] = True

        try:
            res = await self._gm.play_turn(self.state.gm_state, user_text)
        except Exception as e:
            return TurnOutput(text=_text("error.ai.failure", error=e), has_pending_trade=False)

        speaker = str(res.speaker or "Ataryxia").strip() or "Ataryxia"
        dialogue_text = self._strip_dialogue_speaker_prefix(str(res.dialogue or ""), speaker=speaker)
        narration_text = self._strip_dialogue_speaker_prefix(str(res.narration or ""), speaker="Ataryxia")

        lines: list[str] = []
        if res.system:
            lines.append(_text("system.prefix.system", text=str(res.system).strip()))
        if dialogue_text:
            lines.append(dialogue_text)
        elif narration_text:
            lines.append(narration_text)

        if not lines:
            lines.append(_text("system.ataryxia.fallback_continue"))

        self.state.current_scene().narrator_text = str(narration_text or self.state.current_scene().narrator_text or "")
        try:
            remember_dialogue_turn(
                self.state,
                npc_key=ataryxia_key,
                npc_name="Ataryxia",
                player_text=user_text,
                npc_reply=str(dialogue_text or narration_text or ""),
                scene_id=scene.id,
                scene_title=scene.title,
                world_time_minutes=self.state.world_time_minutes,
            )
        except Exception:
            pass

        self.state.advance_world_time(4)
        self._apply_world_progression()
        self._sync_gm_state(
            selected_npc="Ataryxia",
            selected_npc_key=ataryxia_key,
            selected_profile=ataryxia_profile,
        )
        self.save()
        return TurnOutput(
            text="\n".join(lines),
            has_pending_trade=False,
            generated_image_prompt=res.generated_image_prompt,
        )

    def creation_status_text(self) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")
        missing = self.state.player_sheet_missing if isinstance(self.state.player_sheet_missing, list) else []
        if self.state.player_sheet_ready:
            return _text("system.creation.ready")
        next_q = self._player_sheet_manager.next_creation_question(missing)
        labels = self._player_sheet_manager.creation_missing_labels()
        missing_labels = ", ".join(labels.get(str(k), str(k)) for k in missing[:5]) if missing else _text("system.creation.missing_unknown")
        return _text("system.creation.status", missing=missing_labels, question=next_q)

    async def process_creation_message(self, text: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        user_text = str(text or "").strip()
        if not user_text:
            return TurnOutput(text=self.creation_status_text(), has_pending_trade=False)
        if self.state.player_sheet_generation_in_progress:
            return TurnOutput(text=_text("error.creation.busy"), has_pending_trade=False)

        self.state.player_sheet_generation_in_progress = True
        self.state.push("Joueur", user_text)
        lines: list[str] = []
        try:
            recent = [f"{msg.speaker}: {msg.text}" for msg in self.state.chat[-20:]]
            result = await self._player_sheet_manager.ingest_creation_message(
                sheet=self.state.player_sheet if isinstance(self.state.player_sheet, dict) else {},
                user_message=user_text,
                recent_chat_lines=recent,
            )
            new_sheet = result.get("sheet")
            self.state.player_sheet = new_sheet if isinstance(new_sheet, dict) else self.state.player_sheet
            self._ensure_player_sheet_ready()

            ack = str(result.get("ack_text") or "").strip()
            if ack:
                self.state.push("Systeme", ack, count_for_media=False)
                lines.append(_text("system.prefix.system", text=ack))

            if self.state.player_sheet_ready:
                done_lines = [
                    _text("system.creation.done.1"),
                    _text("system.creation.done.2"),
                    _text("system.creation.done.3"),
                ]
                for line in done_lines:
                    self.state.push("Systeme", line, count_for_media=False)
                    lines.append(_text("system.prefix.system", text=line))
            else:
                next_q = str(result.get("next_question") or "").strip()
                if next_q:
                    self.state.push("Systeme", next_q, count_for_media=False)
                    lines.append(_text("system.prefix.system", text=next_q))
        except Exception as e:
            err = _text("error.creation.update_failed", error=e)
            self.state.push("Systeme", err, count_for_media=False)
            lines.append(_text("system.prefix.system", text=err))
        finally:
            self.state.player_sheet_generation_in_progress = False

        self._sync_gm_state()
        self.save()
        return TurnOutput(text="\n".join(lines) if lines else self.creation_status_text(), has_pending_trade=False)

    async def process_user_message(self, text: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        user_text = str(text or "").strip()
        if not user_text:
            return TurnOutput(text=_text("system.message.empty"), has_pending_trade=bool(self.pending_trade()))

        if not self.state.player_sheet_ready:
            return await self.process_creation_message(user_text)

        self._ensure_selected_npc()
        npc = str(self.state.selected_npc or "").strip()
        if not npc:
            return TurnOutput(text=_text("error.npc.none_selected"), has_pending_trade=False)

        scene = self.state.current_scene()
        npc_key, npc_profile = await self._ensure_selected_npc_profile()
        first_contact_line = self._consume_first_contact_line(npc, npc_profile)
        self._sync_gm_state(selected_npc=npc, selected_npc_key=npc_key, selected_profile=npc_profile)

        self.state.push("Joueur", user_text)
        world_intervention_lines = try_resolve_nearby_world_event(self.state, user_text)
        if world_intervention_lines:
            for line in world_intervention_lines:
                clean = str(line or "").strip()
                if clean:
                    self.state.push("Systeme", clean, count_for_media=False)
            self.state.advance_world_time(4)
            self._apply_world_progression()
            self._sync_gm_state(selected_npc=npc, selected_npc_key=npc_key, selected_profile=npc_profile)
            self.save()
            return TurnOutput(text="\n".join(world_intervention_lines), has_pending_trade=bool(self.pending_trade()))

        trade_outcome = self._apply_trade(user_text=user_text, selected_npc=npc, npc_key=npc_key, profile=npc_profile)
        trade_lines = self._trade_lines(trade_outcome)

        self.state.gm_state["conversation_last_player_line"] = user_text
        self._sync_gm_state(selected_npc=npc, selected_npc_key=npc_key, selected_profile=npc_profile)

        try:
            res = await self._gm.play_turn(self.state.gm_state, user_text)
        except Exception as e:
            self.state.push("Systeme", _text("error.ai.failure", error=e), count_for_media=False)
            self.save()
            return TurnOutput(text=_text("error.ai.failure", error=e), has_pending_trade=bool(self.pending_trade()))

        lines: list[str] = []

        if first_contact_line and isinstance(npc_profile, dict):
            speaker = profile_display_name(npc_profile, npc)
            self.state.push(speaker, first_contact_line, count_for_media=False)
            lines.append(f"{speaker}: {first_contact_line}")

        for line in trade_lines:
            self.state.push("Systeme", line, count_for_media=False)
            self._append_unique_line(lines, _text("system.prefix.system", text=line))

        if res.system:
            self.state.push("Systeme", str(res.system), count_for_media=False)
            self._append_unique_line(lines, _text("system.prefix.system", text=res.system))

        if res.dialogue and res.speaker:
            self.state.push(str(res.speaker), str(res.dialogue))
            self._append_unique_line(lines, f"{res.speaker}: {res.dialogue}")

        if res.narration:
            self.state.current_scene().narrator_text = str(res.narration)
            if not (res.dialogue and res.speaker):
                self._append_unique_line(lines, f"Narration: {res.narration}")

        try:
            remember_dialogue_turn(
                self.state,
                npc_key=npc_key,
                npc_name=str(res.speaker or npc or "PNJ"),
                player_text=user_text,
                npc_reply=str(res.dialogue or ""),
                scene_id=scene.id,
                scene_title=scene.title,
                world_time_minutes=self.state.world_time_minutes,
            )
        except Exception:
            pass

        self.state.advance_world_time(6)
        self._apply_world_progression()
        self._sync_gm_state(selected_npc=npc, selected_npc_key=npc_key, selected_profile=npc_profile)
        self.save()

        if not lines:
            lines.append(_text("system.turn.no_response"))

        pending = bool(self.pending_trade())
        if pending:
            summary = self.pending_trade_summary()
            if summary:
                lines.append(summary)

        return TurnOutput(text="\n".join(lines), has_pending_trade=pending)

    def _build_initial_state(self) -> GameState:
        state = GameState()
        dm = DataManager(data_dir=self.data_dir)

        try:
            state.scenes = dm.load_all_location_scenes()
        except DataError as e:
            raise RuntimeError(_text("error.data.load_failed", error=e)) from e

        start_id = dm.choose_start_location_id()
        self._location_seed.seed_static_anchors(state.scenes)
        state.set_scene(start_id)

        try:
            state.item_defs = self._items_manager.load_all()
        except Exception:
            state.item_defs = {}

        if self.profile_name:
            state.player.name = self.profile_name

        self._ensure_player_sheet_ready(state=state)
        ensure_conversation_memory_state(state)
        return state

    def _refresh_static_scenes_from_data(self) -> None:
        if self.state is None:
            return
        dm = DataManager(data_dir=self.data_dir)
        try:
            static_scenes = dm.load_all_location_scenes()
        except DataError:
            return

        for scene_id, scene in static_scenes.items():
            self.state.scenes[scene_id] = scene

        if self.state.current_scene_id not in self.state.scenes:
            self.state.current_scene_id = dm.choose_start_location_id()

        self._location_seed.seed_static_anchors(self.state.scenes)

        try:
            self.state.item_defs = self._items_manager.load_all()
        except Exception:
            self.state.item_defs = {}

        self._ensure_player_sheet_ready(state=self.state)
        self._ensure_selected_npc()

    def _ensure_player_sheet_ready(self, state: GameState | None = None) -> None:
        target = state if isinstance(state, GameState) else self.state
        if target is None:
            return

        profile_name = str(self.profile_name or target.player.name or "Joueur").strip() or "Joueur"

        if not isinstance(target.player_sheet, dict) or not target.player_sheet:
            target.player_sheet = self._player_sheet_manager.create_initial_sheet(fallback_name=profile_name)
        else:
            target.player_sheet = self._player_sheet_manager.ensure_sheet(target.player_sheet, fallback_name=profile_name)

        self._player_sheet_manager.sync_player_basics(target.player_sheet, target.player)
        target.player_sheet_missing = self._player_sheet_manager.missing_creation_fields(target.player_sheet)
        target.player_sheet_ready = not bool(target.player_sheet_missing)

    def _inject_creation_intro_once(self) -> None:
        if self.state is None or self.state.player_sheet_ready:
            return
        if len(self.state.chat) > 0:
            return
        self.state.push(
            "Systeme",
            _text("system.creation.intro.system"),
            count_for_media=False,
        )
        self.state.push(
            "Ataryxia",
            _text("system.creation.intro.ataryxia"),
            count_for_media=False,
        )

    def _ensure_selected_npc(self) -> None:
        if self.state is None:
            return
        scene = self.state.current_scene()
        npcs = list(getattr(scene, "npc_names", []) or [])

        current = str(self.state.selected_npc or "").strip()
        if current and current in npcs:
            return

        self.state.selected_npc = npcs[0] if npcs else None

    async def _ensure_selected_npc_profile(self) -> tuple[str | None, dict | None]:
        if self.state is None:
            return None, None

        npc = str(self.state.selected_npc or "").strip()
        if not npc:
            return None, None

        scene = self.state.current_scene()
        key = npc_profile_key(npc, scene.id)
        profile = self.state.npc_profiles.get(key)
        if isinstance(profile, dict):
            return key, profile

        reused = self._find_existing_profile_for_scene(npc, scene.id)
        if isinstance(reused, dict):
            self.state.npc_profiles[key] = reused
            return key, reused

        try:
            profile = await self._npc_store.ensure_profile(
                self.state.npc_profiles,
                npc,
                location_id=scene.id,
                location_title=scene.title,
            )
            if isinstance(profile, dict):
                self.state.npc_profiles[key] = profile
                return key, profile
        except Exception:
            return key, None
        return key, None

    def _find_existing_profile_for_scene(self, npc_name: str, scene_id: str) -> dict | None:
        if self.state is None:
            return None
        target_npc = self._norm_text(npc_name)
        target_scene = self._norm_text(scene_id)
        if not target_npc:
            return None

        for maybe_profile in self.state.npc_profiles.values():
            if not isinstance(maybe_profile, dict):
                continue
            label = self._norm_text(maybe_profile.get("label"))
            if label and label != target_npc:
                continue
            world_anchor = maybe_profile.get("world_anchor") if isinstance(maybe_profile.get("world_anchor"), dict) else {}
            location_id = self._norm_text(world_anchor.get("location_id"))
            if target_scene and location_id and location_id != target_scene:
                continue
            return maybe_profile
        return None

    def _consume_first_contact_line(self, npc_name: str, profile: dict | None) -> str:
        if self.state is None or not isinstance(profile, dict):
            return ""
        flags = profile.get("dynamic_flags")
        if not isinstance(flags, dict):
            flags = {}
            profile["dynamic_flags"] = flags
        if bool(flags.get("is_met", False)):
            return ""

        first_message = str(profile.get("first_message") or "").strip()
        flags["is_met"] = True
        try:
            self._npc_store.save_profile(npc_name, profile, location_id=self.state.current_scene().id)
        except Exception:
            pass
        return first_message

    def _sync_gm_state(
        self,
        *,
        selected_npc: str | None = None,
        selected_npc_key: str | None = None,
        selected_profile: dict | None = None,
    ) -> None:
        if self.state is None:
            return

        state = self.state
        scene = state.current_scene()
        in_dungeon = self._active_dungeon_run() is not None
        npc = str(selected_npc or state.selected_npc or "").strip()
        npc_key = str(selected_npc_key or "").strip()
        if in_dungeon and selected_npc is None and selected_npc_key is None:
            npc = ""
            npc_key = ""
        if npc and not npc_key:
            persisted = str(state.gm_state.get("selected_npc_key") or "").strip()
            npc_key = persisted or npc_profile_key(npc, scene.id)

        resolved_profile = selected_profile if isinstance(selected_profile, dict) else None
        if resolved_profile is None and npc_key:
            maybe_profile = state.npc_profiles.get(npc_key)
            if isinstance(maybe_profile, dict):
                resolved_profile = maybe_profile

        apply_base_gm_state(
            state,
            economy_manager=self._economy_manager,
            location=scene.title,
            location_id=scene.id,
            map_anchor=scene.map_anchor,
            scene_npcs=list(getattr(scene, "npc_names", []) or []),
            in_dungeon=in_dungeon,
            selected_npc=npc or None,
            selected_npc_key=npc_key or None,
            selected_profile=resolved_profile,
        )
        state.gm_state["memory_profile_key"] = str(self.profile_key or "default")
        state.gm_state["memory_profile_name"] = str(self.profile_name or "")

        ensure_conversation_memory_state(state)
        npc_key_context = str(state.gm_state.get("selected_npc_key") or "").strip() or None
        state.gm_state["conversation_short_term"] = build_short_term_context(state, npc_key_context, max_lines=8)
        state.gm_state["conversation_long_term"] = build_long_term_context(state, npc_key_context, max_items=12)
        state.gm_state["conversation_global_memory"] = build_global_memory_context(state, max_items=12)
        state.gm_state["conversation_retrieved_memory"] = build_retrieved_context(state, npc_key_context, max_items=10)

    def _apply_trade(self, *, user_text: str, selected_npc: str, npc_key: str | None, profile: dict | None) -> dict:
        if self.state is None:
            return {"attempted": False}

        outcome = self._economy_manager.process_trade_message(
            state=self.state,
            user_text=user_text,
            selected_npc_name=str(selected_npc),
            selected_npc_profile=profile if isinstance(profile, dict) else None,
            item_defs=self.state.item_defs,
        )

        if not bool(outcome.get("attempted")):
            return outcome

        trade_context = outcome.get("trade_context") if isinstance(outcome.get("trade_context"), dict) else {}
        if trade_context:
            merged = dict(trade_context)
            merged["npc_name"] = str(selected_npc)
            if npc_key:
                merged["npc_key"] = str(npc_key)
            merged["gold_after"] = max(0, self._safe_int(self.state.player.gold, 0))
            merged["inventory_after"] = self._economy_manager.inventory_summary(self.state, self.state.item_defs)
            self.state.gm_state["last_trade"] = merged

            action = str(merged.get("action") or "trade")
            status = str(merged.get("status") or "inconnu")
            detail = str(merged.get("item_id") or merged.get("query") or "").strip()
            qty_done = max(0, self._safe_int(merged.get("qty_done"), 0))
            summary = _text("system.memory.trade_fact", action=action, status=status)
            if detail:
                if qty_done > 0:
                    summary += _text("system.memory.trade_fact.item_qty", item=detail, qty=qty_done)
                else:
                    summary += _text("system.memory.trade_fact.item", item=detail)
            remember_system_event(
                self.state,
                fact_text=summary,
                npc_key=npc_key,
                npc_name=str(selected_npc),
                scene_id=self.state.current_scene().id,
                scene_title=self.state.current_scene().title,
                world_time_minutes=self.state.world_time_minutes,
                kind="trade",
                importance=4,
            )

        return outcome

    def _trade_lines(self, outcome: dict) -> list[str]:
        if not isinstance(outcome, dict):
            return []
        lines_raw = outcome.get("system_lines")
        if not isinstance(lines_raw, list):
            return []
        lines: list[str] = []
        for line in lines_raw:
            text = str(line or "").strip()
            if text:
                lines.append(text)
        return lines

    def _confirm_text_for_action(self, action: str) -> str:
        key = str(action or "").strip().casefold()
        if key == "buy":
            return _text("system.trade.confirm_cmd.buy")
        if key == "sell":
            return _text("system.trade.confirm_cmd.sell")
        if key == "give":
            return _text("system.trade.confirm_cmd.give")
        if key == "exchange":
            return _text("system.trade.confirm_cmd.exchange")
        return _text("system.trade.confirm_cmd.default")

    def _active_dungeon_run(self) -> dict | None:
        if self.state is None:
            return None
        run = self.state.active_dungeon_run if isinstance(self.state.active_dungeon_run, dict) else None
        if not isinstance(run, dict):
            return None
        if bool(run.get("completed", False)):
            return None
        return run

    def _active_dungeon_combat(self, run: dict | None) -> dict | None:
        if not isinstance(run, dict):
            return None
        combat = run.get("combat") if isinstance(run.get("combat"), dict) else None
        if not isinstance(combat, dict):
            return None
        if not bool(combat.get("active", True)):
            return None
        return combat

    def _start_dungeon_combat(self, run: dict, event: dict) -> bool:
        if not is_combat_event(event):
            return False
        if self._active_dungeon_combat(run):
            return True

        run["completion_pending"] = bool(run.get("completed", False))
        run["completed"] = False
        event_with_context = dict(event)
        run_relic = run.get("run_relic") if isinstance(run.get("run_relic"), dict) else None
        if isinstance(run_relic, dict):
            event_with_context["run_relic"] = run_relic
        run["current_event"] = event_with_context
        run["combat"] = build_combat_state(
            event_with_context,
            rng=self._loot_manager.rng,
            monster_manager=self._monster_manager,
        )
        return True

    async def _finalize_dungeon_floor_clear(self, *, run: dict, event: dict) -> list[str]:
        if self.state is None:
            return []
        event_type = str(event.get("type") or "").strip().casefold()
        floor = max(1, self._safe_int(event.get("floor"), self._safe_int(run.get("current_floor"), 1)))
        lines: list[str] = []

        if event_type == "boss":
            lines.append(_text("system.dungeon.victory_boss"))

        self.state.quest_counters["dungeon_floors_cleared"] = max(
            0,
            self._safe_int(self.state.quest_counters.get("dungeon_floors_cleared"), 0) + 1,
        )

        loot_lines = await self._award_loot_from_dungeon_event(event)
        lines.extend(loot_lines)

        rep_lines = apply_dungeon_reputation(self.state, floor=floor, event_type=event_type)
        if rep_lines:
            lines.append(_text("system.dungeon.reputation_prefix") + " " + " | ".join(rep_lines))

        run.pop("combat", None)
        run.pop("current_event", None)
        if bool(run.pop("completion_pending", False)):
            run["completed"] = True

        if bool(run.get("completed", False)):
            lines.append(_text("system.dungeon.end_surface"))
            self.state.active_dungeon_run = None
        return lines

    def _attempt_dungeon_flee(self, run: dict, combat: dict) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text=_text("system.session.not_initialized"), has_pending_trade=False)

        stats = self.state.player_sheet.get("effective_stats") if isinstance(self.state.player_sheet, dict) and isinstance(self.state.player_sheet.get("effective_stats"), dict) else (
            self.state.player_sheet.get("stats") if isinstance(self.state.player_sheet, dict) and isinstance(self.state.player_sheet.get("stats"), dict) else {}
        )
        agilite = max(0, self._safe_int(stats.get("agilite"), 0))
        chance = max(0, self._safe_int(stats.get("chance"), 0))
        flee_chance = min(0.88, 0.32 + (agilite * 0.015) + (chance * 0.01))
        if self._loot_manager.rng.random() <= flee_chance:
            recovery = max(1, self._safe_int(self.state.player.max_hp, 1) // 2)
            if self._safe_int(self.state.player.hp, 0) < recovery:
                self._set_player_hp_from_combat(recovery)
            self.state.active_dungeon_run = None
            self.state.advance_world_time(10)
            self._apply_world_progression()
            return TurnOutput(
                text=_text("system.dungeon.flee.success", chance=int(round(flee_chance * 100))),
                has_pending_trade=False,
            )

        # Echec de fuite: l'ennemi gagne un tour via une posture defensive forcee.
        result = resolve_combat_turn(
            combat_state=combat,
            action_text=_text("system.dungeon.flee.defense_action"),
            player_hp=self._safe_int(getattr(self.state.player, "hp", 0), 0),
            player_max_hp=self._safe_int(getattr(self.state.player, "max_hp", 1), 1),
            player_sheet=self.state.player_sheet if isinstance(self.state.player_sheet, dict) else {},
            known_skills=self.state.player_skills if isinstance(self.state.player_skills, list) else [],
            runtime_stat_bonuses=get_consumable_stat_bonus_totals(self.state),
            skill_manager=None,
            rng=self._loot_manager.rng,
            run_relic=run.get("run_relic") if isinstance(run.get("run_relic"), dict) else None,
        )
        run["combat"] = result.get("combat") if isinstance(result.get("combat"), dict) else combat
        self._set_player_hp_from_combat(self._safe_int(result.get("player_hp"), self.state.player.hp))
        lines = [_text("system.dungeon.flee.fail")]
        lines.extend([str(line).strip() for line in (result.get("lines") if isinstance(result.get("lines"), list) else []) if str(line).strip()])
        return TurnOutput(text="\n".join(lines), has_pending_trade=False)

    def _skill_text_blob(self, row: dict) -> str:
        chunks: list[str] = []
        for key in ("skill_id", "name", "category", "description"):
            chunks.append(str(row.get(key) or ""))
        effects = row.get("effects")
        if isinstance(effects, list):
            for entry in effects[:8]:
                if isinstance(entry, str):
                    chunks.append(entry)
                elif isinstance(entry, dict):
                    chunks.append(str(entry.get("kind") or ""))
                    chunks.append(str(entry.get("name") or ""))
                    chunks.append(str(entry.get("effect") or ""))
        return " ".join(chunks).casefold()

    def _non_combat_support_kind(self, row: dict) -> str:
        blob = self._skill_text_blob(row)
        if any(token in blob for token in ("soin", "heal", "gueri", "regen", "restaur", "recuper")):
            return "heal"
        if any(token in blob for token in ("buff", "boost", "aura", "bened", "soutien", "barriere", "protec", "defense", "bouclier", "renfor")):
            return "buff"
        primary = row.get("primary_stats")
        stats = [str(x or "").strip().casefold() for x in primary] if isinstance(primary, list) else []
        if any(stat in {"defense", "sagesse", "agilite", "chance"} for stat in stats):
            return "buff"
        return "other"

    def _pick_non_combat_skill_for_action(self, rows: list[dict], action: str) -> dict | None:
        if not rows:
            return None
        normalized = str(action or "").strip().casefold()
        heal_skills = [row for row in rows if self._non_combat_support_kind(row) == "heal"]
        buff_skills = [row for row in rows if self._non_combat_support_kind(row) == "buff"]
        if normalized == "heal":
            return heal_skills[0] if heal_skills else None
        if normalized == "spell":
            if buff_skills:
                return buff_skills[0]
            if heal_skills:
                return heal_skills[0]
            return None
        if buff_skills:
            return buff_skills[0]
        if heal_skills:
            return heal_skills[0]
        return rows[0]

    def _resolve_buff_stat_for_skill(self, skill_entry: dict) -> str:
        stats = skill_entry.get("primary_stats")
        if isinstance(stats, list):
            for key in stats:
                clean = str(key or "").strip().casefold()
                if clean in {"force", "intelligence", "magie", "defense", "sagesse", "agilite", "dexterite", "chance", "charisme"}:
                    return clean
        blob = self._skill_text_blob(skill_entry)
        keyword_map = (
            ("force", "force"),
            ("dexterite", "dexterite"),
            ("agilite", "agilite"),
            ("defense", "defense"),
            ("bouclier", "defense"),
            ("sagesse", "sagesse"),
            ("chance", "chance"),
            ("charisme", "charisme"),
            ("magie", "magie"),
            ("intelligence", "intelligence"),
        )
        for token, stat in keyword_map:
            if token in blob:
                return stat
        return "defense"

    def _apply_non_combat_heal_skill(self, skill_entry: dict) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")
        level = max(1, self._safe_int(skill_entry.get("level"), 1))
        rank = max(1, self._safe_int(skill_entry.get("rank"), 1))
        base = 2 + (level // 2) + rank
        variance = self._loot_manager.rng.randint(0, 2)
        amount = max(1, min(40, base + variance))
        max_hp = max(1, self._safe_int(getattr(self.state.player, "max_hp", 1), 1))
        current = max(0, self._safe_int(getattr(self.state.player, "hp", 0), 0))
        target = min(max_hp, current + amount)
        gained = max(0, target - current)
        self._set_player_hp_from_combat(target)
        if gained <= 0:
            return _text("system.skill.out_of_combat.heal_full", hp=target, max_hp=max_hp)
        return _text("system.skill.out_of_combat.heal", gain=gained, hp=target, max_hp=max_hp)

    def _apply_non_combat_buff_skill(self, skill_entry: dict) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")
        level = max(1, self._safe_int(skill_entry.get("level"), 1))
        rank = max(1, self._safe_int(skill_entry.get("rank"), 1))
        stat = self._resolve_buff_stat_for_skill(skill_entry)
        bonus = max(1, min(4, 1 + (level // 18) + (1 if rank >= 3 else 0)))
        duration = max(2, min(8, 3 + (level // 14)))
        skill_id = str(skill_entry.get("skill_id") or "").strip().casefold()
        skill_name = str(skill_entry.get("name") or skill_id or "competence").strip() or "competence"
        buff = add_consumable_stat_buff(
            self.state,
            stat=stat,
            value=bonus,
            duration_turns=duration,
            item_id=f"skill:{skill_id}",
            item_name=f"Competence {skill_name}",
        )
        if not isinstance(buff, dict):
            return _text("error.consumable.none_applied")
        turns = max(1, self._safe_int(buff.get("turns_remaining"), duration))
        return _text("system.skill.out_of_combat.buff", stat=stat, bonus=bonus, turns=turns)

    def _combat_action_text(self, action: str) -> str:
        key = str(action or "").strip().casefold()
        if key == "attack":
            return _text("system.combat.action.attack")
        if key == "spell":
            return _text("system.combat.action.spell")
        if key == "heal":
            return _text("system.combat.action.heal")
        if key == "skill":
            rows = self.state.player_skills if self.state is not None and isinstance(self.state.player_skills, list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or row.get("skill_id") or "").strip()
                if name:
                    return _text("system.combat.action.skill_use", skill=name)
            return _text("system.combat.action.skill_fallback")
        return _text("system.combat.action.attack")

    def _set_player_hp_from_combat(self, hp_value: int) -> None:
        if self.state is None:
            return
        max_hp = max(1, self._safe_int(getattr(self.state.player, "max_hp", 1), 1))
        hp = min(max(0, self._safe_int(hp_value, max_hp)), max_hp)
        self.state.player.hp = hp

        if isinstance(self.state.player_sheet, dict):
            stats = self.state.player_sheet.get("stats")
            if isinstance(stats, dict):
                stats["pv"] = hp
            effective = self.state.player_sheet.get("effective_stats")
            if isinstance(effective, dict):
                effective["pv"] = hp

    async def _award_loot_from_dungeon_event(self, event: dict) -> list[str]:
        if self.state is None or not isinstance(event, dict):
            return []

        event_type = str(event.get("type") or "").strip().casefold()
        chances = {"monster": 0.35, "mimic": 0.75, "treasure": 1.0, "boss": 1.0}
        chance = float(chances.get(event_type, 0.0))
        if chance <= 0.0 or self._loot_manager.rng.random() > chance:
            return []

        lines: list[str] = []
        floor = max(1, self._safe_int(event.get("floor"), 1))
        gold_gain = self._dungeon_gold_gain(event_type, floor)
        if gold_gain > 0:
            self.state.player.gold = max(0, self._safe_int(getattr(self.state.player, "gold", 0), 0) + gold_gain)
            lines.append(_text("system.dungeon.loot_gold", gold=gold_gain))

        run = self._active_dungeon_run()
        anchor = str((run or {}).get("anchor") or self.state.current_scene().map_anchor or self.state.current_scene().title or "Lumeria")
        hint_text = ""
        if event_type == "monster":
            hint_text = str(
                event.get("monster_base_name")
                or event.get("name")
                or event.get("monster_id")
                or ""
            ).strip()
        elif event_type == "treasure":
            hint_text = str(event.get("loot") or "").strip()
        elif event_type == "mimic":
            hint_text = str(event.get("loot_lure") or event.get("loot") or "").strip()

        loot = await self._loot_manager.generate_loot(
            source_type=event_type or "treasure",
            floor=floor,
            anchor=anchor,
            known_items=self.state.item_defs,
            hint_text=hint_text,
        )
        got_line = self._grant_generated_loot(loot, prefix=_text("system.dungeon.loot_prefix.obtained"))
        if got_line:
            lines.append(got_line)

        if event_type == "boss":
            bonus = await self._loot_manager.generate_loot(
                source_type="boss",
                floor=floor + 2,
                anchor=anchor,
                known_items=self.state.item_defs,
                hint_text=str(event.get("loot") or "").strip(),
            )
            bonus_line = self._grant_generated_loot(bonus, prefix=_text("system.dungeon.loot_prefix.boss"))
            if bonus_line:
                lines.append(bonus_line)

        if self._loot_manager.rng.random() <= self._potion_bonus_drop_chance(event_type):
            potion = await self._loot_manager.generate_loot(
                source_type=event_type or "treasure",
                floor=floor,
                anchor=anchor,
                known_items=self.state.item_defs,
                hint_text=self._potion_hint_for_drop(floor),
            )
            potion_line = self._grant_generated_loot(potion, prefix=_text("system.dungeon.loot_prefix.potion"))
            if potion_line:
                lines.append(potion_line)

        return lines

    def _grant_generated_loot(self, loot: dict, *, prefix: str) -> str:
        if self.state is None:
            return ""
        item_id, updated_defs, created_new = self._loot_manager.ensure_item_exists(loot, self.state.item_defs)
        self.state.item_defs = updated_defs

        qty = max(1, self._safe_int(loot.get("qty"), 1))
        granted = self._add_item_to_inventory(item_id=item_id, qty=qty)
        label = str(getattr(self.state.item_defs.get(item_id), "name", item_id) or item_id).strip() or item_id
        rarity = str(loot.get("rarity") or "").strip().casefold()
        if granted <= 0:
            return _text("system.dungeon.loot.lost", prefix=prefix, label=label, qty=qty)

        suffix = f" [{rarity}]" if rarity else ""
        created = _text("system.dungeon.loot.new_item_suffix") if created_new else ""
        capped = _text("system.dungeon.loot.capped_suffix") if granted < qty else ""
        return f"{prefix}: {label} x{granted}{suffix}{created}{capped}"

    def _dungeon_gold_gain(self, event_type: str, floor: int) -> int:
        kind = str(event_type or "").strip().casefold()
        f = max(1, self._safe_int(floor, 1))
        if kind == "monster":
            base = self._loot_manager.rng.randint(4, 10)
        elif kind == "mimic":
            base = self._loot_manager.rng.randint(10, 22)
        elif kind == "treasure":
            base = self._loot_manager.rng.randint(8, 18)
        elif kind == "boss":
            base = self._loot_manager.rng.randint(24, 42)
        else:
            return 0
        return max(0, base + max(0, f))

    def _potion_hint_for_drop(self, floor: int) -> str:
        hints = [
            _text("system.dungeon.potion_hint.heal"),
            _text("system.dungeon.potion_hint.mana"),
            _text("system.dungeon.potion_hint.strength"),
            _text("system.dungeon.potion_hint.dexterity"),
            _text("system.dungeon.potion_hint.agility"),
            _text("system.dungeon.potion_hint.defense"),
        ]
        if floor >= 10:
            hints.append(_text("system.dungeon.potion_hint.wisdom"))
        if floor >= 16:
            hints.append(_text("system.dungeon.potion_hint.magic"))
        return str(self._loot_manager.rng.choice(hints))

    def _potion_bonus_drop_chance(self, event_type: str) -> float:
        return {
            "monster": 0.25,
            "mimic": 0.42,
            "treasure": 0.28,
            "boss": 0.65,
        }.get(str(event_type or "").strip().casefold(), 0.22)

    def _inventory_qty(self, item_id: str) -> int:
        if self.state is None:
            return 0
        target = str(item_id or "").strip().casefold()
        if not target:
            return 0
        totals = self._economy_manager.inventory_totals(self.state)
        return max(0, self._safe_int(totals.get(target), 0))

    def _remove_item_from_inventory(self, item_id: str, qty: int) -> int:
        if self.state is None:
            return 0
        target_id = str(item_id or "").strip().casefold()
        wanted = max(0, self._safe_int(qty, 0))
        if not target_id or wanted <= 0:
            return 0
        removed = 0
        for grid in (self.state.carried, self.state.storage):
            for idx, stack in enumerate(grid.slots):
                if removed >= wanted:
                    break
                if stack is None:
                    continue
                sid = str(getattr(stack, "item_id", "") or "").strip().casefold()
                if sid != target_id:
                    continue
                sqty = max(0, self._safe_int(getattr(stack, "qty", 0), 0))
                if sqty <= 0:
                    grid.set(idx, None)
                    continue
                take = min(sqty, wanted - removed)
                left = sqty - take
                removed += take
                if left > 0:
                    stack.qty = left
                else:
                    grid.set(idx, None)
            if removed >= wanted:
                break
        return removed

    def _add_item_to_inventory(self, *, item_id: str, qty: int) -> int:
        if self.state is None:
            return 0
        target_id = str(item_id or "").strip().casefold()
        wanted = max(0, self._safe_int(qty, 0))
        if not target_id or wanted <= 0:
            return 0
        item_def = self.state.item_defs.get(target_id) if isinstance(self.state.item_defs, dict) else None
        stack_max = max(1, self._safe_int(getattr(item_def, "stack_max", 1), 1))
        added = 0

        for grid in (self.state.carried, self.state.storage):
            for stack in grid.slots:
                if added >= wanted:
                    break
                if stack is None:
                    continue
                sid = str(getattr(stack, "item_id", "") or "").strip().casefold()
                if sid != target_id:
                    continue
                current_qty = max(0, self._safe_int(getattr(stack, "qty", 0), 0))
                room = max(0, stack_max - current_qty)
                if room <= 0:
                    continue
                take = min(room, wanted - added)
                stack.qty = current_qty + take
                added += take
            if added >= wanted:
                return added

        from app.ui.state.inventory import ItemStack

        for grid in (self.state.carried, self.state.storage):
            for idx, stack in enumerate(grid.slots):
                if added >= wanted:
                    break
                if stack is not None:
                    continue
                take = min(stack_max, wanted - added)
                if take <= 0:
                    continue
                grid.set(idx, ItemStack(item_id=target_id, qty=take))
                added += take
            if added >= wanted:
                break
        return added

    def _sheet_stats_refs(self) -> tuple[dict | None, dict | None]:
        if self.state is None or not isinstance(self.state.player_sheet, dict):
            return None, None
        stats = self.state.player_sheet.get("stats")
        if not isinstance(stats, dict):
            stats = {}
            self.state.player_sheet["stats"] = stats
        effective = self.state.player_sheet.get("effective_stats")
        if isinstance(effective, dict):
            return stats, effective
        return stats, None

    def _heal_player_from_consumable(self, value: object) -> str:
        if self.state is None:
            return _text("system.session.not_initialized")
        amount = max(1, self._safe_int(value, 1))
        max_hp = max(1, self._safe_int(getattr(self.state.player, "max_hp", 1), 1))
        current = max(0, self._safe_int(getattr(self.state.player, "hp", 0), 0))
        target = min(max_hp, current + amount)
        gained = max(0, target - current)
        self._set_player_hp_from_combat(target)
        if gained <= 0:
            return _text("system.skill.out_of_combat.heal_full", hp=target, max_hp=max_hp)
        return _text("system.skill.out_of_combat.heal", gain=gained, hp=target, max_hp=max_hp)

    def _restore_mana_from_consumable(self, value: object) -> str:
        amount = max(1, self._safe_int(value, 1))
        stats, effective = self._sheet_stats_refs()
        if not isinstance(stats, dict):
            return _text("error.sheet.missing")

        shown = effective if isinstance(effective, dict) else stats
        mana_max = max(0, self._safe_int(shown.get("mana_max"), self._safe_int(stats.get("mana_max"), 0)))
        if mana_max <= 0:
            return _text("error.consumable.no_mana_pool")

        current = max(0, self._safe_int(stats.get("mana"), self._safe_int(shown.get("mana"), 0)))
        target = min(mana_max, current + amount)
        gained = max(0, target - current)
        stats["mana"] = target
        if isinstance(effective, dict):
            effective["mana"] = target
        if gained <= 0:
            return _text("system.consumable.mana_full", mana=target, mana_max=mana_max)
        return _text("system.consumable.mana_gain", gain=gained, mana=target, mana_max=mana_max)

    def _apply_consumable_effect(self, item_id: str, item_name: str, effect: dict) -> tuple[str | None, bool]:
        kind = str(effect.get("kind") or "").strip().casefold()
        if kind == "heal":
            return self._heal_player_from_consumable(effect.get("value")), True
        if kind == "mana":
            return self._restore_mana_from_consumable(effect.get("value")), True
        if kind == "stat_buff":
            stat = str(effect.get("stat") or "").strip().casefold()
            value = self._safe_int(effect.get("value"), 0)
            duration = max(1, self._safe_int(effect.get("duration_turns"), 3))
            buff = add_consumable_stat_buff(
                self.state,
                stat=stat,
                value=value,
                duration_turns=duration,
                item_id=item_id,
                item_name=item_name,
            )
            if not isinstance(buff, dict):
                return _text("error.consumable.none_applied"), False
            turns = max(1, self._safe_int(buff.get("turns_remaining"), duration))
            return _text("system.skill.out_of_combat.buff", stat=stat, bonus=value, turns=turns), True
        return None, False

    def _player_level(self, state: GameState) -> int:
        sheet = state.player_sheet if isinstance(state.player_sheet, dict) else {}
        effective = sheet.get("effective_stats") if isinstance(sheet.get("effective_stats"), dict) else None
        stats = effective if isinstance(effective, dict) else (sheet.get("stats") if isinstance(sheet.get("stats"), dict) else {})
        return max(1, self._safe_int(stats.get("niveau"), 1))

    def _experience_tier(self, level: int, skill_count: int) -> str:
        if level <= 2 and skill_count <= 2:
            return "debutant"
        if level <= 5 or skill_count <= 6:
            return "intermediaire"
        return "avance"

    def _append_unique_line(self, lines: list[str], line: str) -> None:
        text = str(line or "").strip()
        if not text:
            return
        if lines and lines[-1] == text:
            return
        lines.append(text)

    def _latest_player_message_text(self, *, max_scan: int = 30) -> str:
        if self.state is None:
            return ""
        chat_rows = self.state.chat if isinstance(getattr(self.state, "chat", None), list) else []
        for row in reversed(chat_rows[-max(1, int(max_scan)) :]):
            speaker = str(getattr(row, "speaker", "") or "").strip().casefold()
            text = str(getattr(row, "text", "") or "").strip()
            if speaker == "joueur" and text:
                return text
        return ""

    def _idle_topic_from_text(self, text: str) -> str:
        raw = re.sub(r"\s+", " ", str(text or "").strip()).casefold()
        if not raw:
            return ""
        words = re.findall(r"[a-z0-9à-öø-ÿ']{2,}", raw)
        if not words:
            return ""
        stop = {
            "les",
            "des",
            "une",
            "pour",
            "avec",
            "dans",
            "mais",
            "donc",
            "alors",
            "sans",
            "plus",
            "trop",
            "comme",
            "quoi",
            "comment",
            "moi",
            "toi",
            "nous",
            "vous",
            "cest",
            "cela",
            "ceci",
            "etre",
            "suis",
            "fait",
            "faire",
            "ca",
            "oui",
            "non",
            "bien",
            "aussi",
            "encore",
            "vraiment",
            "tres",
            "salut",
            "hello",
            "bonjour",
            "bonsoir",
            "petit",
            "depuis",
            "moment",
            "juste",
            "alors",
            "veux",
            "peux",
            "etre",
            "avais",
            "avons",
            "avez",
            "parle",
            "parler",
            "reprendre",
            "reprend",
            "repris",
        }
        picked: list[str] = []
        seen: set[str] = set()
        for word in words:
            clean = word.strip("'")
            if len(clean) < 3:
                continue
            if clean in stop:
                continue
            if clean in seen:
                continue
            seen.add(clean)
            picked.append(clean)
            if len(picked) >= 4:
                break
        if not picked:
            return ""
        phrase = " ".join(picked).strip()
        return phrase[:52].strip()

    def build_idle_nudge_text(self) -> str:
        flags = self._gm_flags()
        step = max(1, self._safe_int(flags.get("telegram_ataryxia_idle_nudge_turn"), 0) + 1)
        flags["telegram_ataryxia_idle_nudge_turn"] = step

        last_player_text = self._latest_player_message_text(max_scan=30)
        topic = self._idle_topic_from_text(last_player_text)
        last_sent = str(flags.get("telegram_ataryxia_last_idle_nudge") or "").strip().casefold()

        topic_or_subject = topic or _text("system.ataryxia.idle.topic_fallback")
        context_templates = _text_library.get_phrases("narration.ataryxia.idle.context")
        checkin_templates = _text_library.get_phrases("narration.ataryxia.idle.checkin")
        generic_templates = _text_library.get_phrases("narration.ataryxia.idle.generic")
        pool: list[str] = []
        if topic:
            pool.extend(context_templates)
            pool.extend(_text_library.get_phrases("narration.ataryxia.idle.context_checkin"))
        if step % 3 == 0:
            pool.extend(checkin_templates)
        pool.extend(generic_templates)
        if not pool:
            pool.extend(_text_library.get_phrases("narration.ataryxia.idle.fallback"))

        seed = step * 17 + sum(ord(ch) for ch in topic)
        for offset in range(len(pool)):
            template = pool[(seed + offset) % len(pool)]
            line = re.sub(r"\s+", " ", _text_library.format_vars(template, topic=topic_or_subject)).strip()
            if not line:
                continue
            if line.casefold() == last_sent:
                continue
            flags["telegram_ataryxia_last_idle_nudge"] = line
            return line[:200]

        fallback = _text("narration.ataryxia.idle.fallback")
        flags["telegram_ataryxia_last_idle_nudge"] = fallback
        return fallback

    def _default_telegram_ataryxia_profile(self) -> dict:
        return {
            "label": "Ataryxia",
            "role": _text("system.ataryxia.profile.role"),
            "agenda_secret": _text("system.ataryxia.profile.agenda"),
            "besoin": _text("system.ataryxia.profile.need"),
            "peur": _text("system.ataryxia.profile.fear"),
            "traits": ["franche", "chaleureuse", "lucide", "curieuse", "nuancee"],
            "tension_level": 8,
            "morale": 72,
            "aggressiveness": 22,
            "corruption_level": 34,
            "dominance_style": "soft",
            "attraction_map": {},
            "persona_directives": _text("system.ataryxia.profile.persona_directives"),
            "truth_state": {
                "known_secrets": [],
                "active_lies": [],
                "mensonge_actif": {},
                "last_reveal_at": "",
                "blacklist_until_minutes": 0,
            },
        }

    def _apply_telegram_ataryxia_persona(self, profile: dict) -> dict:
        if not isinstance(profile, dict):
            return self._default_telegram_ataryxia_profile()

        defaults = self._default_telegram_ataryxia_profile()
        # Persona Telegram: stable et distincte de la narratrice in-game.
        for key in (
            "label",
            "role",
            "agenda_secret",
            "besoin",
            "peur",
            "traits",
            "dominance_style",
            "persona_directives",
        ):
            profile[key] = defaults.get(key)

        profile["tension_level"] = max(0, min(100, self._safe_int(profile.get("tension_level"), self._safe_int(defaults.get("tension_level"), 8))))
        profile["morale"] = max(0, min(100, self._safe_int(profile.get("morale"), self._safe_int(defaults.get("morale"), 72))))
        profile["aggressiveness"] = max(
            0,
            min(100, self._safe_int(profile.get("aggressiveness"), self._safe_int(defaults.get("aggressiveness"), 22))),
        )
        profile["corruption_level"] = max(
            0,
            min(100, self._safe_int(profile.get("corruption_level"), self._safe_int(defaults.get("corruption_level"), 34))),
        )

        if not isinstance(profile.get("attraction_map"), dict):
            profile["attraction_map"] = {}
        if not isinstance(profile.get("truth_state"), dict):
            profile["truth_state"] = dict(defaults.get("truth_state") or {})
        return profile

    def _strip_dialogue_speaker_prefix(self, text: str, *, speaker: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""

        names: list[str] = []
        for raw_name in (speaker, "Ataryxia"):
            name = str(raw_name or "").strip()
            if not name:
                continue
            if any(name.casefold() == existing.casefold() for existing in names):
                continue
            names.append(name)

        for _ in range(3):
            changed = False
            for name in names:
                escaped = re.escape(name)
                pattern = rf"^\s*(?:\*\*)?\s*{escaped}\s*(?:\*\*)?\s*[:：-]\s*"
                stripped = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
                if stripped != cleaned:
                    cleaned = stripped.strip()
                    changed = True
            if not changed:
                break
        return cleaned

    def _norm_text(self, value: object) -> str:
        text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").strip())
        while "__" in text:
            text = text.replace("__", "_")
        return text.strip("_")

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


class TelegramSessionManager:
    def __init__(
        self,
        *,
        data_dir: str = "data",
        slot_count: int = 3,
        default_slot: int = 1,
        shared_profile_key: str | None = None,
        shared_profile_name: str | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.default_slot = max(1, int(default_slot))
        self.save_manager = SaveManager(slot_count=max(1, int(slot_count)))
        self.shared_profile_key = (
            self.save_manager.normalize_profile_id(shared_profile_key)
            if str(shared_profile_key or "").strip()
            else None
        )
        self.shared_profile_name = str(shared_profile_name or "").strip()[:80]
        self._sessions: dict[int, TelegramGameSession] = {}

    async def get_session(self, *, chat_id: int, display_name: str = "") -> TelegramGameSession:
        existing = self._sessions.get(int(chat_id))
        if existing is not None:
            return existing

        profile_key, profile_name = self._resolve_profile(chat_id=chat_id, display_name=display_name)

        session = TelegramGameSession(
            chat_id=int(chat_id),
            profile_key=profile_key,
            profile_name=profile_name,
            slot=self.default_slot,
            save_manager=self.save_manager,
            data_dir=self.data_dir,
        )
        await session.load_or_create()
        self._sessions[int(chat_id)] = session
        return session

    async def switch_profile(self, *, chat_id: int, display_name: str, profile_key: str) -> TelegramGameSession:
        chosen_key = self.save_manager.normalize_profile_id(profile_key)
        chosen_name = self._display_name_for_profile(chosen_key) or display_name or chosen_key
        previous = self._sessions.get(int(chat_id))
        chosen_slot = previous.slot if isinstance(previous, TelegramGameSession) else self.default_slot

        session = TelegramGameSession(
            chat_id=int(chat_id),
            profile_key=chosen_key,
            profile_name=str(chosen_name).strip()[:80] or chosen_key,
            slot=self._clamp_slot(chosen_slot),
            save_manager=self.save_manager,
            data_dir=self.data_dir,
        )
        await session.load_or_create()
        self._sessions[int(chat_id)] = session
        return session

    async def switch_slot(self, *, chat_id: int, display_name: str, slot: int) -> TelegramGameSession:
        session = await self.get_session(chat_id=chat_id, display_name=display_name)
        session.slot = self._clamp_slot(slot)
        await session.load_or_create()
        self._sessions[int(chat_id)] = session
        return session

    def list_profiles(self) -> list[dict]:
        return self.save_manager.list_profiles()

    def active_sessions(self) -> list[TelegramGameSession]:
        return list(self._sessions.values())

    def _resolve_profile(self, *, chat_id: int, display_name: str) -> tuple[str, str]:
        fallback_name = str(display_name or f"Telegram-{chat_id}").strip()[:80] or f"Telegram-{chat_id}"

        if self.shared_profile_key:
            profile_name = self.shared_profile_name or self._display_name_for_profile(self.shared_profile_key) or fallback_name
            return self.shared_profile_key, profile_name

        return self.save_manager.normalize_profile_id(f"telegram_{chat_id}"), fallback_name

    def _display_name_for_profile(self, profile_key: str) -> str:
        target = self.save_manager.normalize_profile_id(profile_key)
        for row in self.save_manager.list_profiles():
            key = self.save_manager.normalize_profile_id(str(row.get("profile_key") or ""))
            if key != target:
                continue
            return str(row.get("display_name") or "").strip()[:80]
        return ""

    def _clamp_slot(self, slot: int) -> int:
        raw = int(slot)
        if raw < 1:
            return 1
        max_slot = max(1, int(self.save_manager.slot_count))
        if raw > max_slot:
            return max_slot
        return raw
