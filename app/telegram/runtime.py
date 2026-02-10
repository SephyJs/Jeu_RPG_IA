from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.core.data.data_manager import DataError, DataManager
from app.core.data.item_manager import ItemsManager
from app.core.save import SaveManager
from app.gamemaster.conversation_memory import (
    build_global_memory_context,
    build_long_term_context,
    build_short_term_context,
    ensure_conversation_memory_state,
    remember_dialogue_turn,
    remember_system_event,
)
from app.gamemaster.economy_manager import EconomyManager
from app.gamemaster.gamemaster import GameMaster
from app.gamemaster.location_manager import LocationManager, is_building_scene_title, scene_open_status
from app.gamemaster.npc_manager import (
    NPCProfileManager,
    npc_profile_key,
    profile_display_name,
    profile_summary_line,
)
from app.gamemaster.ollama_client import OllamaClient
from app.gamemaster.player_sheet_manager import PlayerSheetManager
from app.gamemaster.world_time import format_fantasy_datetime
from app.ui.state.game_state import GameState


@dataclass
class TurnOutput:
    text: str
    has_pending_trade: bool = False


@dataclass(frozen=True)
class TravelOption:
    label: str
    next_scene_id: str
    destination_title: str
    destination_anchor: str
    is_building: bool
    is_open: bool
    status_hint: str


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
        self._npc_store = NPCProfileManager(self._llm)
        self._player_sheet_manager = PlayerSheetManager(self._llm)

    async def load_or_create(self) -> None:
        state = self._build_initial_state()
        loaded = self.save_manager.load_slot(self.slot, state, profile=self.profile_key)
        self.state = state

        if loaded:
            self._refresh_static_scenes_from_data()
            self._ensure_player_sheet_ready()
            self._inject_creation_intro_once()
        else:
            self._ensure_player_sheet_ready()
            self._inject_creation_intro_once()
            self._sync_gm_state()
            self.save()

        self._ensure_selected_npc()
        self._sync_gm_state()

    def short_status_line(self) -> str:
        if self.state is None:
            return "Session non initialisee."
        return f"Profil={self.profile_key} | Slot={self.slot} | Lieu={self.state.current_scene().title}"

    def save(self) -> None:
        if self.state is None:
            return
        self.save_manager.save_slot(
            self.slot,
            self.state,
            profile=self.profile_key,
            display_name=self.profile_name,
        )

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

            label = str(choice.label or "").strip() or f"Aller vers {target_scene.title}"
            is_open, status_hint = scene_open_status(target_scene, self.state.world_time_minutes)
            options.append(
                TravelOption(
                    label=label,
                    next_scene_id=target_id,
                    destination_title=str(target_scene.title),
                    destination_anchor=str(target_scene.map_anchor or ""),
                    is_building=is_building_scene_title(str(target_scene.title or "")),
                    is_open=bool(is_open),
                    status_hint=str(status_hint or "").strip(),
                )
            )
        return options

    async def travel_by_index(self, option_index: int) -> TurnOutput:
        options = self.travel_options()
        if option_index < 0 or option_index >= len(options):
            return TurnOutput(text="Deplacement invalide ou obsolete.", has_pending_trade=bool(self.pending_trade()))
        return self._apply_travel(options[option_index])

    async def travel_to_scene(self, scene_id: str) -> TurnOutput:
        target_id = str(scene_id or "").strip()
        if not target_id:
            return TurnOutput(text="Deplacement invalide.", has_pending_trade=bool(self.pending_trade()))

        for option in self.travel_options():
            if option.next_scene_id == target_id:
                return self._apply_travel(option)

        return TurnOutput(text="Ce deplacement n'est pas disponible ici.", has_pending_trade=bool(self.pending_trade()))

    def _apply_travel(self, option: TravelOption) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text="Session non initialisee.", has_pending_trade=False)
        if self.state.current_scene_id == option.next_scene_id:
            return TurnOutput(text=f"Vous etes deja a {option.destination_title}.", has_pending_trade=bool(self.pending_trade()))

        if not option.is_open:
            closed_line = f"🚪 {option.status_hint}" if option.status_hint else "Ce lieu est ferme pour le moment."
            self.state.push("Systeme", closed_line, count_for_media=False)
            self.save()
            return TurnOutput(text=closed_line, has_pending_trade=bool(self.pending_trade()))

        origin = self.state.current_scene()
        self.state.push("Joueur", option.label)
        self.state.set_scene(option.next_scene_id)
        travel_minutes = 8 if option.is_building else 14
        self.state.advance_world_time(travel_minutes)
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
            remember_system_event(
                self.state,
                fact_text=f"Deplacement: {origin.title} -> {destination.title} (+{travel_minutes} min)",
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

        lines = [f"➡️ Vous arrivez : {self.state.current_scene().title}"]
        if selected_npc:
            lines.append(f"PNJ actif: {selected_npc}")
        else:
            lines.append("Aucun PNJ actif dans ce lieu.")
        lines.append(f"Temps du monde: {format_fantasy_datetime(self.state.world_time_minutes)}")
        narration = str(self.state.current_scene().narrator_text or "").strip()
        if narration:
            lines.append(f"Narration: {narration}")

        return TurnOutput(text="\n".join(lines), has_pending_trade=bool(self.pending_trade()))

    def status_text(self) -> str:
        if self.state is None:
            return "Session non initialisee."

        scene = self.state.current_scene()
        equipped_items = self.state.equipped_items if isinstance(self.state.equipped_items, dict) else {}
        weapon = str(equipped_items.get("weapon") or "").strip() or "aucune"
        level = self._player_level(self.state)
        skill_count = len([x for x in self.state.player_skills if isinstance(x, dict)])
        npc = str(self.state.selected_npc or "").strip() or "(aucun)"

        return "\n".join(
            [
                f"Profil: {self.profile_name}",
                f"Profil ID: {self.profile_key}",
                f"Slot: {self.slot}",
                f"Lieu: {scene.title}",
                f"PNJ actif: {npc}",
                f"Or: {max(0, int(self.state.player.gold))}",
                f"Niveau: {level} | Competences: {skill_count}",
                f"Arme equipee: {weapon}",
                f"Temps du monde: {format_fantasy_datetime(self.state.world_time_minutes)}",
                f"Creation joueur: {'OK' if self.state.player_sheet_ready else 'incomplete'}",
            ]
        )

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
                return f"Achat en attente: {item} x{qty} pour {total} or ({unit_price}/u)."
            return f"Achat en attente: {item} x{qty}."
        if action == "sell":
            if unit_price > 0:
                return f"Vente en attente: {item} x{qty} pour {total} or ({unit_price}/u)."
            return f"Vente en attente: {item} x{qty}."
        if action == "exchange":
            return f"Echange en attente: {item} x{qty}."
        if action == "give":
            return f"Don en attente: {item} x{qty}."
        return f"Transaction en attente: {item} x{qty}."

    async def select_npc(self, npc_name: str) -> str:
        if self.state is None:
            return "Session non initialisee."

        npcs = self.scene_npcs()
        if not npcs:
            return "Aucun PNJ disponible dans ce lieu."

        target = str(npc_name or "").strip()
        if target not in npcs:
            return "PNJ introuvable dans ce lieu."

        self.state.selected_npc = target
        npc_key, profile = await self._ensure_selected_npc_profile()
        first_contact_line = self._consume_first_contact_line(target, profile)
        self._sync_gm_state(selected_npc=target, selected_npc_key=npc_key, selected_profile=profile)
        self.save()

        if isinstance(profile, dict):
            summary = f"PNJ actif: {profile_summary_line(profile, target)}"
            if first_contact_line:
                speaker = profile_display_name(profile, target)
                self.state.push(speaker, first_contact_line, count_for_media=False)
                self.save()
                return f"{summary}\n{speaker}: {first_contact_line}"
            return summary
        return f"PNJ actif: {target}"

    async def confirm_pending_trade(self) -> TurnOutput:
        pending = self.pending_trade()
        if not pending:
            return TurnOutput(text="Aucune transaction en attente.", has_pending_trade=False)
        cmd = self._confirm_text_for_action(str(pending.get("action") or ""))
        return await self.process_user_message(cmd)

    async def cancel_pending_trade(self) -> TurnOutput:
        pending = self.pending_trade()
        if not pending:
            return TurnOutput(text="Aucune transaction en attente.", has_pending_trade=False)
        return await self.process_user_message("annuler")

    def creation_status_text(self) -> str:
        if self.state is None:
            return "Session non initialisee."
        missing = self.state.player_sheet_missing if isinstance(self.state.player_sheet_missing, list) else []
        if self.state.player_sheet_ready:
            return "Fiche joueur complete."
        next_q = self._player_sheet_manager.next_creation_question(missing)
        labels = self._player_sheet_manager.creation_missing_labels()
        missing_labels = ", ".join(labels.get(str(k), str(k)) for k in missing[:5]) if missing else "inconnu"
        return (
            "Creation personnage incomplete.\n"
            f"Champs manquants: {missing_labels}\n"
            f"Question: {next_q}"
        )

    async def process_creation_message(self, text: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text="Session non initialisee.", has_pending_trade=False)

        user_text = str(text or "").strip()
        if not user_text:
            return TurnOutput(text=self.creation_status_text(), has_pending_trade=False)
        if self.state.player_sheet_generation_in_progress:
            return TurnOutput(text="Creation en cours, reessaie dans un instant.", has_pending_trade=False)

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
                lines.append(f"Systeme: {ack}")

            if self.state.player_sheet_ready:
                done_lines = [
                    "Fiche joueur creee. Les dialogues complets sont maintenant actifs.",
                    "Tu debutes sans sort avance: entraine-toi avec les PNJ pour progresser.",
                    "Parfait. Maintenant, avance.",
                ]
                for line in done_lines:
                    self.state.push("Systeme", line, count_for_media=False)
                    lines.append(f"Systeme: {line}")
            else:
                next_q = str(result.get("next_question") or "").strip()
                if next_q:
                    self.state.push("Systeme", next_q, count_for_media=False)
                    lines.append(f"Systeme: {next_q}")
        except Exception as e:
            err = f"Impossible de mettre a jour la fiche joueur: {e}"
            self.state.push("Systeme", err, count_for_media=False)
            lines.append(f"Systeme: {err}")
        finally:
            self.state.player_sheet_generation_in_progress = False

        self._sync_gm_state()
        self.save()
        return TurnOutput(text="\n".join(lines) if lines else self.creation_status_text(), has_pending_trade=False)

    async def process_user_message(self, text: str) -> TurnOutput:
        if self.state is None:
            return TurnOutput(text="Session non initialisee.", has_pending_trade=False)

        user_text = str(text or "").strip()
        if not user_text:
            return TurnOutput(text="Message vide.", has_pending_trade=bool(self.pending_trade()))

        if not self.state.player_sheet_ready:
            return await self.process_creation_message(user_text)

        self._ensure_selected_npc()
        npc = str(self.state.selected_npc or "").strip()
        if not npc:
            return TurnOutput(text="Aucun PNJ selectionne. Utilise le bouton PNJ.", has_pending_trade=False)

        scene = self.state.current_scene()
        npc_key, npc_profile = await self._ensure_selected_npc_profile()
        first_contact_line = self._consume_first_contact_line(npc, npc_profile)
        self._sync_gm_state(selected_npc=npc, selected_npc_key=npc_key, selected_profile=npc_profile)

        self.state.push("Joueur", user_text)

        trade_outcome = self._apply_trade(user_text=user_text, selected_npc=npc, npc_key=npc_key, profile=npc_profile)
        trade_lines = self._trade_lines(trade_outcome)

        self._sync_gm_state(selected_npc=npc, selected_npc_key=npc_key, selected_profile=npc_profile)

        try:
            res = await self._gm.play_turn(self.state.gm_state, user_text)
        except Exception as e:
            self.state.push("Systeme", f"Erreur IA: {e}", count_for_media=False)
            self.save()
            return TurnOutput(text=f"Erreur IA: {e}", has_pending_trade=bool(self.pending_trade()))

        lines: list[str] = []

        if first_contact_line and isinstance(npc_profile, dict):
            speaker = profile_display_name(npc_profile, npc)
            self.state.push(speaker, first_contact_line, count_for_media=False)
            lines.append(f"{speaker}: {first_contact_line}")

        for line in trade_lines:
            self.state.push("Systeme", line, count_for_media=False)
            self._append_unique_line(lines, f"Systeme: {line}")

        if res.system:
            self.state.push("Systeme", str(res.system), count_for_media=False)
            self._append_unique_line(lines, f"Systeme: {res.system}")

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
        self._sync_gm_state(selected_npc=npc, selected_npc_key=npc_key, selected_profile=npc_profile)
        self.save()

        if not lines:
            lines.append("Aucune reponse exploitable pour ce tour.")

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
            raise RuntimeError(f"Impossible de charger les donnees de jeu: {e}") from e

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
            "Avant de commencer: presente ton personnage (pseudo, genre, apparence, atouts).",
            count_for_media=False,
        )
        self.state.push(
            "Ataryxia",
            "Je dois savoir qui tu es avant d'ouvrir les routes et les rencontres.",
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

        state.gm_state["player_name"] = state.player.name
        state.gm_state["location"] = scene.title
        state.gm_state["location_id"] = scene.id
        state.gm_state["map_anchor"] = scene.map_anchor
        state.gm_state["scene_npcs"] = list(getattr(scene, "npc_names", []) or [])
        state.gm_state["world_time_minutes"] = max(0, self._safe_int(state.world_time_minutes, 0))
        state.gm_state["world_datetime"] = format_fantasy_datetime(state.world_time_minutes)
        state.gm_state.setdefault("flags", {})

        state.gm_state["npc_profiles"] = state.npc_profiles
        state.gm_state["player_sheet"] = state.player_sheet if isinstance(state.player_sheet, dict) else {}
        state.gm_state["player_sheet_ready"] = bool(state.player_sheet_ready)
        state.gm_state["player_gold"] = max(0, self._safe_int(state.player.gold, 0))
        state.gm_state["inventory_summary"] = self._economy_manager.inventory_summary(state, state.item_defs)
        state.gm_state["skill_points"] = max(0, self._safe_int(state.skill_points, 0))

        known_skills = state.player_skills if isinstance(state.player_skills, list) else []
        state.gm_state["player_skills"] = [
            {
                "skill_id": str(s.get("skill_id") or ""),
                "name": str(s.get("name") or ""),
                "category": str(s.get("category") or ""),
                "rank": max(1, self._safe_int(s.get("rank"), 1)),
                "level": max(1, self._safe_int(s.get("level"), 1)),
                "uses": max(0, self._safe_int(s.get("uses"), 0)),
            }
            for s in known_skills
            if isinstance(s, dict)
        ]

        state.gm_state["equipped_items"] = dict(state.equipped_items)

        effective_stats: dict[str, Any] = {}
        if isinstance(state.player_sheet, dict):
            raw_effective = state.player_sheet.get("effective_stats")
            if isinstance(raw_effective, dict):
                effective_stats = dict(raw_effective)
            else:
                raw_stats = state.player_sheet.get("stats")
                if isinstance(raw_stats, dict):
                    effective_stats = dict(raw_stats)
        state.gm_state["effective_stats"] = effective_stats

        player_level = self._player_level(state)
        skill_count = len(state.gm_state["player_skills"])
        state.gm_state["player_level"] = player_level
        state.gm_state["player_skill_count"] = max(0, skill_count)
        state.gm_state["player_weapon_equipped"] = str(state.gm_state["equipped_items"].get("weapon") or "").strip()
        state.gm_state["player_experience_tier"] = self._experience_tier(player_level, skill_count)

        npc = str(selected_npc or state.selected_npc or "").strip()
        if npc:
            state.gm_state["selected_npc"] = npc
            if selected_npc_key:
                state.gm_state["selected_npc_key"] = selected_npc_key
            elif state.gm_state.get("selected_npc_key"):
                state.gm_state["selected_npc_key"] = str(state.gm_state.get("selected_npc_key") or "")
            else:
                state.gm_state["selected_npc_key"] = npc_profile_key(npc, scene.id)

            if isinstance(selected_profile, dict):
                state.gm_state["selected_npc_profile"] = selected_profile
            else:
                maybe = state.npc_profiles.get(state.gm_state.get("selected_npc_key", ""))
                if isinstance(maybe, dict):
                    state.gm_state["selected_npc_profile"] = maybe
                else:
                    state.gm_state.pop("selected_npc_profile", None)
        else:
            state.gm_state.pop("selected_npc", None)
            state.gm_state.pop("selected_npc_key", None)
            state.gm_state.pop("selected_npc_profile", None)

        ensure_conversation_memory_state(state)
        npc_key_context = str(state.gm_state.get("selected_npc_key") or "").strip() or None
        state.gm_state["conversation_short_term"] = build_short_term_context(state, npc_key_context, max_lines=12)
        state.gm_state["conversation_long_term"] = build_long_term_context(state, npc_key_context, max_items=10)
        state.gm_state["conversation_global_memory"] = build_global_memory_context(state, max_items=8)

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
            summary = f"Economie ({action}): {status}"
            if detail:
                if qty_done > 0:
                    summary += f" | {detail} x{qty_done}"
                else:
                    summary += f" | {detail}"
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
            return "j'achete"
        if key == "sell":
            return "je vends"
        if key == "give":
            return "je donne"
        if key == "exchange":
            return "j'echange"
        return "oui"

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
