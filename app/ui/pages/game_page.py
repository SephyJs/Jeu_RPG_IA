import asyncio
from typing import Callable

from nicegui import ui

from app.ui.state.game_state import Choice, Scene
from app.ui.state.game_state import GameState
from app.ui.components.left_panel import left_panel
from app.ui.components.center_dialogue import center_dialogue
from app.ui.components.right_narrator import right_narrator
from app.core.data.item_manager import ItemsManager
from app.core.save import SaveManager
from app.gamemaster.npc_manager import NPCProfileManager
from app.gamemaster.runtime import get_runtime_services
from app.telegram.bridge_manager import TelegramBridgeManager
from app.ui.components.npc_world import ensure_npc_world_state, spawn_roaming_known_npcs, sync_npc_registry_from_profiles
from app.ui.nsfw import (
    is_nsfw_mode_enabled,
    is_nsfw_scene,
    nsfw_password_is_valid,
    pick_safe_scene_id,
    read_nsfw_password_config,
    set_nsfw_mode_enabled,
    set_profile_nsfw_password,
)
from app.ui.pages.game_page_support import (
    AutosaveController,
    NPCProfileTracker,
    build_initial_state,
    extract_profile_name,
    inject_game_page_css,
    maybe_start_random_media,
    refresh_static_scenes_from_data,
    sync_gm_state,
)


MEDIA_EVERY_X_MESSAGES = 6      # change vidéo après 6 messages
MEDIA_EVERY_Y_SECONDS = 35.0    # ou toutes les 35s si rien ne se passe
MEDIA_DURATION_SECONDS = 8.0     # durée d'une animation avant retour image
SAVE_SLOT_COUNT = 3
AUTOSAVE_DEBOUNCE_SECONDS = 1.2
AUTOSAVE_TICK_SECONDS = 0.4
_runtime_services = get_runtime_services()
_location_seed = _runtime_services.location_manager
_items_manager = ItemsManager(data_dir="data")
_economy_manager = _runtime_services.economy_manager
_telegram_bridge_manager = TelegramBridgeManager(slot_count=SAVE_SLOT_COUNT)
_ai_health_client = _runtime_services.llm


@ui.page('/game')
def game_page() -> None:
    dark = ui.dark_mode()
    dark.enable()
    inject_game_page_css()

    save_manager = SaveManager(slot_count=SAVE_SLOT_COUNT)
    npc_store = NPCProfileManager(None)

    state = build_initial_state(
        location_seed=_location_seed,
        items_manager=_items_manager,
        economy_manager=_economy_manager,
    )
    active_slot = {"value": 1}
    active_profile = {"name": "", "key": ""}
    session = {"ready": False}
    nsfw_switch_holder: dict[str, object] = {"widget": None}
    nsfw_switch_guard = {"active": False}
    nsfw_password_dialog_open = {"active": False}
    nsfw_dialog_host_holder: dict[str, object] = {"widget": None}
    panel_refreshers: list[Callable[[], None]] = []
    right_refreshers: list[Callable[[], None]] = []
    slot_select_holder: dict[str, object] = {"widget": None}
    profile_label_holder: dict[str, object] = {"widget": None}
    save_profile_label_holder: dict[str, object] = {"widget": None}
    save_summary_label_holder: dict[str, object] = {"widget": None}
    save_dialog_holder: dict[str, object] = {"widget": None}
    ai_status_label_holder: dict[str, object] = {"widget": None}
    ai_health_probe_running = {"active": False}
    login_card_holder: dict[str, object] = {"widget": None}
    game_container_holder: dict[str, object] = {"widget": None}
    left_mobile_drawer_holder: dict[str, object] = {"widget": None}
    right_mobile_drawer_holder: dict[str, object] = {"widget": None}
    left_mobile_refresh_holder: dict[str, object] = {"refresh": None}
    right_mobile_refresh_holder: dict[str, object] = {"refresh": None}
    is_mobile_client = False
    autosave = AutosaveController(default_delay_s=AUTOSAVE_DEBOUNCE_SECONDS)
    npc_profile_tracker = NPCProfileTracker(npc_store=npc_store)

    def _normalize_npc_profiles_in_state() -> None:
        if not isinstance(state.npc_profiles, dict):
            state.npc_profiles = {}
        state.npc_profiles = npc_profile_tracker.merge_missing_from_disk(state.npc_profiles)

        ensure_npc_world_state(state)
        sync_npc_registry_from_profiles(state)
        npc_profile_tracker.rebuild_signatures(state.npc_profiles)

    def _refresh_open_mobile_drawers() -> None:
        if not is_mobile_client:
            return
        left_drawer = left_mobile_drawer_holder.get("widget")
        left_refresh = left_mobile_refresh_holder.get("refresh")
        if left_drawer is not None and bool(getattr(left_drawer, "value", False)) and callable(left_refresh):
            left_refresh()

        right_drawer = right_mobile_drawer_holder.get("widget")
        right_refresh = right_mobile_refresh_holder.get("refresh")
        if right_drawer is not None and bool(getattr(right_drawer, "value", False)) and callable(right_refresh):
            right_refresh()

    def _refresh_panels() -> None:
        for refresh in panel_refreshers:
            refresh()
        _refresh_open_mobile_drawers()

    def _refresh_right_panels() -> None:
        for refresh in right_refreshers:
            refresh()
        if not is_mobile_client:
            return
        right_drawer = right_mobile_drawer_holder.get("widget")
        right_refresh = right_mobile_refresh_holder.get("refresh")
        if right_drawer is not None and bool(getattr(right_drawer, "value", False)) and callable(right_refresh):
            right_refresh()

    def _set_nsfw_switch_value(enabled: bool) -> None:
        widget = nsfw_switch_holder.get("widget")
        if widget is None:
            return
        nsfw_switch_guard["active"] = True
        try:
            setter = getattr(widget, "set_value", None)
            if callable(setter):
                setter(bool(enabled))
            else:
                widget.value = bool(enabled)
        except Exception:
            pass
        finally:
            nsfw_switch_guard["active"] = False

    def _kick_out_if_in_nsfw_scene() -> None:
        current = state.scenes.get(state.current_scene_id)
        if not is_nsfw_scene(current):
            return

        fallback_scene_id = pick_safe_scene_id(state, from_scene_id=state.current_scene_id)
        if not fallback_scene_id or fallback_scene_id == state.current_scene_id:
            return

        state.set_scene(fallback_scene_id)
        state.push("Système", "🚪 Mode Adulte désactivé: vous quittez la zone restreinte.", count_for_media=False)

    def _set_nsfw_mode(enabled: bool, *, notify: bool = True, mark_dirty: bool = True) -> None:
        enabled = bool(enabled)
        set_nsfw_mode_enabled(state, enabled)
        _set_nsfw_switch_value(enabled)

        if not enabled:
            _kick_out_if_in_nsfw_scene()

        if mark_dirty:
            _mark_state_dirty()

        if notify:
            if enabled:
                ui.notify("Mode Adulte activé. Accès /bordel autorisé.", type="positive")
            else:
                ui.notify("Mode Adulte désactivé.")

        _refresh_panels()

    async def _prompt_nsfw_password() -> bool:
        plain, digest, source = read_nsfw_password_config(state)
        host = nsfw_dialog_host_holder.get("widget")
        if host is None:
            ui.notify("UI NSFW indisponible: recharge la page.", color="negative")
            return False
        if not plain and not digest and source == "missing":
            with host:
                with ui.dialog() as setup_dialog, ui.card().classes("w-full").style("max-width: 460px;"):
                    ui.label("Configurer le mot de passe Adulte").classes("text-base font-semibold")
                    ui.label(
                        "Aucun mot de passe n'est défini. Crée-en un pour ce profil.",
                    ).classes("text-xs opacity-70")
                    new_password_input = ui.input(
                        "Nouveau mot de passe",
                        password=True,
                        password_toggle_button=True,
                    ).classes("w-full")
                    confirm_password_input = ui.input(
                        "Confirmer le mot de passe",
                        password=True,
                        password_toggle_button=True,
                    ).classes("w-full")
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Annuler", on_click=setup_dialog.close).props("flat")
                        ui.button(
                            "Créer",
                            on_click=lambda: setup_dialog.submit(
                                (
                                    str(new_password_input.value or ""),
                                    str(confirm_password_input.value or ""),
                                )
                            ),
                        )
                    confirm_password_input.on(
                        "keydown.enter",
                        lambda e: setup_dialog.submit(
                            (
                                str(new_password_input.value or ""),
                                str(confirm_password_input.value or ""),
                            )
                        ),
                    )

            setup_dialog.open()
            submitted = await setup_dialog
            if submitted is None:
                return False

            if not isinstance(submitted, (list, tuple)) or len(submitted) != 2:
                ui.notify("Configuration annulée.", color="warning")
                return False

            new_password = str(submitted[0] or "")
            confirm_password = str(submitted[1] or "")
            if len(new_password) < 4:
                ui.notify("Le mot de passe doit contenir au moins 4 caractères.", color="negative")
                return False
            if new_password != confirm_password:
                ui.notify("Les mots de passe ne correspondent pas.", color="negative")
                return False

            set_profile_nsfw_password(state, new_password)
            _mark_state_dirty()
            ui.notify("Mot de passe Adulte configuré pour ce profil.", type="positive")
            return True

        with host:
            with ui.dialog() as pwd_dialog, ui.card().classes("w-full").style("max-width: 420px;"):
                ui.label("Accès restreint : Mode Adulte").classes("text-base font-semibold")
                if source == "env":
                    ui.label("Saisis le mot de passe défini dans l'environnement.").classes("text-xs opacity-70")
                else:
                    ui.label("Saisis le mot de passe de ce profil.").classes("text-xs opacity-70")
                password_input = ui.input("Mot de passe", password=True, password_toggle_button=True).classes("w-full")
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Annuler", on_click=pwd_dialog.close).props("flat")
                    ui.button("Valider", on_click=lambda: pwd_dialog.submit(str(password_input.value or "")))
                password_input.on("keydown.enter", lambda e: pwd_dialog.submit(str(password_input.value or "")))

        pwd_dialog.open()
        submitted = await pwd_dialog
        if submitted is None:
            return False

        if nsfw_password_is_valid(state, str(submitted)):
            return True

        ui.notify("Mot de passe incorrect.", color="negative")
        return False

    async def _ensure_nsfw_enabled() -> bool:
        if is_nsfw_mode_enabled(state):
            _set_nsfw_switch_value(True)
            return True

        if nsfw_password_dialog_open["active"]:
            return False

        nsfw_password_dialog_open["active"] = True
        try:
            allowed = await _prompt_nsfw_password()
        except Exception as e:
            _set_nsfw_mode(False, notify=False, mark_dirty=False)
            ui.notify(f"Activation du Mode Adulte impossible: {e}", color="negative")
            return False
        finally:
            nsfw_password_dialog_open["active"] = False

        if not allowed:
            _set_nsfw_mode(False, notify=False, mark_dirty=False)
            return False

        _set_nsfw_mode(True, notify=True, mark_dirty=True)
        return True

    def _replace_state(new_state: GameState) -> None:
        state.__dict__.clear()
        state.__dict__.update(new_state.__dict__)

    def _apply_memory_profile_to_state() -> None:
        if not isinstance(state.gm_state, dict):
            state.gm_state = {}
        state.gm_state["memory_profile_key"] = str(active_profile.get("key") or "default")
        state.gm_state["memory_profile_name"] = str(active_profile.get("name") or "")

    def _mark_state_dirty(delay_s: float = AUTOSAVE_DEBOUNCE_SECONDS) -> None:
        if not session["ready"] or not active_profile["key"]:
            return
        autosave.mark_dirty(delay_s=float(delay_s))

    def _clear_dirty_state() -> None:
        autosave.clear()

    def _persist_current_slot(
        show_notify: bool = False,
        *,
        refresh_widgets: bool = True,
        force: bool = False,
    ) -> None:
        if not session["ready"] or not active_profile["key"]:
            return
        if not force and not autosave.is_dirty() and not show_notify:
            return
        _apply_memory_profile_to_state()
        sync_gm_state(state, economy_manager=_economy_manager)
        npc_profile_tracker.save_dirty_profiles(state.npc_profiles)
        try:
            save_manager.save_slot(
                active_slot["value"],
                state,
                profile=active_profile["key"],
                display_name=active_profile["name"],
            )
        except Exception as e:
            if show_notify:
                ui.notify(f"Echec sauvegarde slot {active_slot['value']}: {e}", color="negative")
            return
        _clear_dirty_state()
        if refresh_widgets:
            _refresh_save_widgets()
        if show_notify:
            ui.notify(f"Sauvegarde effectuée sur le slot {active_slot['value']}.")

    def _autosave_tick() -> None:
        if not session["ready"] or not active_profile["key"]:
            return
        if not autosave.is_due():
            return
        try:
            _persist_current_slot(show_notify=False, refresh_widgets=False, force=False)
        except Exception:
            pass

    def _load_current_slot(show_notify: bool = True) -> None:
        if not active_profile["key"]:
            if show_notify:
                ui.notify("Profil non defini.")
            return
        fresh = build_initial_state(
            location_seed=_location_seed,
            items_manager=_items_manager,
            economy_manager=_economy_manager,
        )
        if not save_manager.load_slot(active_slot["value"], fresh, profile=active_profile["key"]):
            if show_notify:
                ui.notify(f"Le slot {active_slot['value']} est vide.")
            return

        _replace_state(fresh)
        if active_profile["name"] and str(getattr(state.player, "name", "") or "").strip() in {"", "L'Éveillé"}:
            state.player.name = active_profile["name"]
        _normalize_npc_profiles_in_state()
        refresh_static_scenes_from_data(state)
        _location_seed.seed_static_anchors(state.scenes)
        spawn_roaming_known_npcs(state)
        _apply_memory_profile_to_state()
        sync_gm_state(state, economy_manager=_economy_manager)
        _set_nsfw_mode(is_nsfw_mode_enabled(state), notify=False, mark_dirty=False)
        _clear_dirty_state()
        _refresh_save_widgets()
        _refresh_panels()
        if save_manager.last_warning:
            state.push("Système", save_manager.last_warning, count_for_media=False)
        if show_notify:
            ui.notify(f"Slot {active_slot['value']} chargé.")

    def _new_game_in_slot() -> None:
        if not active_profile["key"]:
            ui.notify("Profil non defini.")
            return
        fresh = build_initial_state(
            location_seed=_location_seed,
            items_manager=_items_manager,
            economy_manager=_economy_manager,
        )
        _replace_state(fresh)
        if active_profile["name"]:
            state.player.name = active_profile["name"]
        _normalize_npc_profiles_in_state()
        refresh_static_scenes_from_data(state)
        _location_seed.seed_static_anchors(state.scenes)
        spawn_roaming_known_npcs(state)
        _apply_memory_profile_to_state()
        sync_gm_state(state, economy_manager=_economy_manager)
        _set_nsfw_mode(is_nsfw_mode_enabled(state), notify=False, mark_dirty=False)
        _refresh_save_widgets()
        _persist_current_slot(show_notify=False, force=True)
        _refresh_panels()
        ui.notify(f"Nouvelle partie créée dans le slot {active_slot['value']}.")

    def _set_active_slot(slot_value: int) -> None:
        if not session["ready"] or not active_profile["key"]:
            return
        if autosave.is_dirty():
            _persist_current_slot(show_notify=False, refresh_widgets=False, force=True)
        try:
            selected = int(slot_value)
        except Exception:
            selected = 1
        active_slot["value"] = max(1, min(SAVE_SLOT_COUNT, selected))
        save_manager.set_last_slot(
            active_slot["value"],
            profile=active_profile["key"],
            display_name=active_profile["name"],
        )
        _refresh_save_widgets()

    def on_change() -> None:
        if not session["ready"]:
            return
        if (
            not state.narrator_media_url.endswith(".mp4")
            and state.narrator_messages_since_last_media >= MEDIA_EVERY_X_MESSAGES
        ):
            maybe_start_random_media(state, duration_seconds=MEDIA_DURATION_SECONDS)

        _mark_state_dirty()
        _refresh_panels()

    def _profile_summary_line(profile_key: str) -> str:
        key = str(profile_key or "").strip()
        if not key:
            return "Aucune sauvegarde."
        last_slot = save_manager.get_last_slot(default=1, profile=key)
        summary = save_manager.slot_summary(last_slot, profile=key)
        if not bool(summary.get("exists", False)):
            return "Aucune sauvegarde."
        location = str(summary.get("location") or "inconnu")
        messages = int(summary.get("messages") or 0)
        return f"Dernier slot: {last_slot} | Lieu: {location} | Messages: {messages}"

    def _refresh_save_widgets() -> None:
        profile_text = f"Profil: {active_profile['name']}" if active_profile["name"] else "Profil: -"
        summary_text = _profile_summary_line(active_profile["key"]) if active_profile["key"] else "Aucune sauvegarde."

        try:
            slot_widget = slot_select_holder.get("widget")
            if slot_widget is not None:
                slot_widget.set_value(active_slot["value"])
        except Exception:
            pass

        try:
            profile_label = profile_label_holder.get("widget")
            if profile_label is not None:
                profile_label.set_text(profile_text)
        except Exception:
            pass

        try:
            save_profile_label = save_profile_label_holder.get("widget")
            if save_profile_label is not None:
                save_profile_label.set_text(profile_text)
        except Exception:
            pass

        try:
            summary_label = save_summary_label_holder.get("widget")
            if summary_label is not None:
                summary_label.set_text(summary_text)
        except Exception:
            pass

    async def _refresh_ai_health_async() -> None:
        if ai_health_probe_running["active"]:
            return
        ai_health_probe_running["active"] = True
        try:
            ok = await _ai_health_client.is_available(cache_ttl_seconds=4.0, probe_timeout_seconds=1.4)
            widget = ai_status_label_holder.get("widget")
            if widget is not None:
                if ok:
                    widget.set_text("IA locale: disponible")
                else:
                    widget.set_text("IA locale: indisponible (fallback actif)")
        except Exception:
            widget = ai_status_label_holder.get("widget")
            if widget is not None:
                widget.set_text("IA locale: indisponible (fallback actif)")
        finally:
            ai_health_probe_running["active"] = False

    def _refresh_ai_health() -> None:
        if not session["ready"]:
            return
        asyncio.create_task(_refresh_ai_health_async())

    def _activate_profile(profile_name: str) -> None:
        pseudo = extract_profile_name(str(profile_name or ""))
        if not pseudo:
            ui.notify("Entre un pseudo.")
            return

        active_profile["name"] = pseudo[:80]
        active_profile["key"] = save_manager.normalize_profile_id(pseudo)
        active_slot["value"] = save_manager.get_last_slot(default=1, profile=active_profile["key"])
        known_profile = save_manager.profile_has_data(active_profile["key"])
        migrated_legacy = 0
        if not known_profile and save_manager.has_legacy_saves():
            migrated_legacy = save_manager.migrate_legacy_saves_to_profile(
                active_profile["key"],
                display_name=active_profile["name"],
            )
            if migrated_legacy > 0:
                known_profile = True
                active_slot["value"] = save_manager.get_last_slot(default=1, profile=active_profile["key"])
        if not known_profile:
            active_slot["value"] = 1

        session["ready"] = True
        if known_profile:
            _load_current_slot(show_notify=False)
            state.push("Ataryxia", f"Bon retour, {active_profile['name']}.", count_for_media=False)
            state.push(
                "Système",
                "Historique personnel charge depuis vos sauvegardes.",
                count_for_media=False,
            )
            if migrated_legacy > 0:
                state.push(
                    "Système",
                    f"Migration automatique: {migrated_legacy} ancien(s) slot(s) rattache(s) a ce pseudo.",
                    count_for_media=False,
                )
        else:
            _new_game_in_slot()
            state.push("Ataryxia", f"Bienvenue, {active_profile['name']}. Ta chronique commence.", count_for_media=False)

        # Le mode adulte repart verrouillé à chaque activation de profil.
        _set_nsfw_mode(False, notify=False, mark_dirty=False)
        _refresh_save_widgets()
        _refresh_ai_health()

        login_card = login_card_holder.get("widget")
        if login_card is not None:
            try:
                login_card.set_visibility(False)
            except Exception:
                pass
        game_container = game_container_holder.get("widget")
        if game_container is not None:
            try:
                game_container.set_visibility(True)
            except Exception:
                pass

        sync_gm_state(state, economy_manager=_economy_manager)
        _apply_memory_profile_to_state()
        _refresh_panels()
        ui.notify(f"Profil actif: {active_profile['name']} (slot {active_slot['value']}).")

    def _open_save_dialog() -> None:
        if not session["ready"] or not active_profile["key"]:
            ui.notify("Connecte un pseudo avant d'ouvrir les sauvegardes.")
            return
        _refresh_save_widgets()
        dlg = save_dialog_holder.get("widget")
        if dlg is not None:
            try:
                dlg.open()
            except Exception:
                pass

    async def _on_nsfw_switch_change(e) -> None:
        if nsfw_switch_guard["active"]:
            return

        desired = bool(getattr(e, "value", False))
        if not desired:
            _set_nsfw_mode(False, notify=True, mark_dirty=True)
            return

        # Validation synchrone dans le flux de l'événement pour éviter les ratés de scheduling.
        await _ensure_nsfw_enabled()

    def _mask_token(token: str) -> str:
        raw = str(token or "").strip()
        if not raw:
            return ""
        if len(raw) <= 10:
            return "*" * len(raw)
        return f"{raw[:4]}...{raw[-4:]}"

    def _telegram_help_text() -> str:
        return (
            "Commande Telegram:\n"
            "- /telegram <TOKEN> : connecter ce profil a un bot Telegram\n"
            "- /telegram status : etat du bot pour ce profil\n"
            "- /telegram start : demarrer le bot avec la config enregistree\n"
            "- /telegram stop : arreter le bot\n"
            "- /telegram slot <n> : choisir le slot utilise par le bot\n"
            "- /telegram clear : supprimer la config Telegram de ce profil"
        )

    def _telegram_status_text(profile_key: str) -> str:
        status = _telegram_bridge_manager.status(profile_key)
        running = "oui" if bool(status.get("running")) else "non"
        has_token = "oui" if bool(status.get("has_token")) else "non"
        token_hint = str(status.get("token_hint") or "")
        pid = status.get("pid")
        pid_text = str(pid) if pid is not None else "-"
        token_part = has_token
        if token_hint:
            token_part = f"{has_token} ({token_hint})"
        return (
            f"Telegram profil={status.get('profile_key')} | token={token_part} | actif={running} | pid={pid_text} | "
            f"slot={status.get('slot')} | log={status.get('log_path')}"
        )

    async def _chat_command_handler(command_text: str) -> tuple[bool, str, str]:
        raw = str(command_text or "").strip()
        if not raw.startswith("/"):
            return False, "", raw

        parts = raw.split()
        if not parts:
            return False, "", raw

        cmd = parts[0].casefold()

        if cmd == "/bordel":
            if not is_nsfw_mode_enabled(state):
                return True, "Accès refusé: active d'abord le Mode Adulte.", "/bordel"

            scene_id = "maison_de_plaisir_01"
            if scene_id not in state.scenes:
                current_scene_id = state.current_scene_id
                back_choice = Choice(id="sortir_plaisir", label="Quitter ce lieu de débauche", next_scene_id=current_scene_id)

                brothel_scene = Scene(
                    id=scene_id,
                    title="Le Baiser de Velours",
                    map_anchor=state.current_scene().map_anchor,
                    narrator_text="Ataryxia : Les tentures de velours rouge et les parfums capiteux vous accueillent. Des silhouettes de toutes les races se prélassent dans les alcôves, attendant de vous offrir un moment d'évasion.",
                    npc_names=["Hôtesse Elfe", "Courtisan Humain", "Amant Drakéide", "Geisha Fée", "Succube Démonide", "Harpie Homme-bête", "Satyre Nain"],
                    choices=[back_choice],
                    generated=True,
                )
                state.scenes[scene_id] = brothel_scene

            target_scene = state.scenes.get(scene_id)
            if not isinstance(target_scene, Scene) or not is_nsfw_scene(target_scene):
                return True, "Lieu indisponible.", "/bordel"

            state.set_scene(scene_id)
            on_change()

            return True, "Vous pénétrez dans le Baiser de Velours...", "/bordel"

        if cmd != "/telegram":
            return False, "", raw

        # --- LOGIQUE TELEGRAM ---
        sub_cmd = parts[1] if len(parts) > 1 else ""
        if sub_cmd and ":" in sub_cmd:
            user_echo = f"/telegram {_mask_token(sub_cmd)}"
        else:
            user_echo = " ".join(parts[:3])

        if not session["ready"] or not active_profile["key"]:
            return True, "Connecte d'abord un profil avant de configurer Telegram.", user_echo

        if len(parts) == 1 or parts[1].casefold() in {"help", "aide", "?"}:
            return True, _telegram_help_text(), "/telegram help"

        sub = sub_cmd.strip()
        sub_lower = sub.casefold()

        if sub_lower in {"status", "etat"}:
            return True, _telegram_status_text(active_profile["key"]), "/telegram status"

        if sub_lower in {"start", "on"}:
            ok, msg = _telegram_bridge_manager.start(profile_key=active_profile["key"])
            prefix = "OK" if ok else "Erreur"
            return True, f"{prefix}: {msg}\n{_telegram_status_text(active_profile['key'])}", "/telegram start"

        if sub_lower in {"stop", "off"}:
            _telegram_bridge_manager.stop(profile_key=active_profile["key"])
            return True, f"Bot stoppe.\n{_telegram_status_text(active_profile['key'])}", "/telegram stop"

        if sub_lower in {"clear", "reset"}:
            _telegram_bridge_manager.stop(profile_key=active_profile["key"])
            _telegram_bridge_manager.clear_config(profile_key=active_profile["key"])
            return True, "Configuration Telegram supprimee pour ce profil.", "/telegram clear"

        if sub_lower == "slot":
            if len(parts) < 3:
                return True, "Usage: /telegram slot <numero>", "/telegram slot"
            try:
                chosen = int(parts[2])
            except ValueError:
                return True, "Slot invalide. Exemple: /telegram slot 1", "/telegram slot"
            chosen = max(1, min(SAVE_SLOT_COUNT, chosen))
            _telegram_bridge_manager.set_slot(profile_key=active_profile["key"], slot=chosen)
            status = _telegram_bridge_manager.status(active_profile["key"])
            if bool(status.get("running")):
                _telegram_bridge_manager.stop(profile_key=active_profile["key"])
                _telegram_bridge_manager.start(profile_key=active_profile["key"])
            return True, f"Slot Telegram regle sur {chosen}.\n{_telegram_status_text(active_profile['key'])}", f"/telegram slot {chosen}"

        token = sub
        if not _telegram_bridge_manager.validate_token(token):
            return True, "Token invalide. Format attendu: /telegram <TOKEN_BOTFATHER>", "/telegram <token>"

        ok, msg = _telegram_bridge_manager.configure_and_start(
            profile_key=active_profile["key"],
            token=token,
            profile_name=active_profile["name"],
            slot=active_slot["value"],
        )
        prefix = "OK" if ok else "Erreur"
        return (
            True,
            f"{prefix}: {msg}\nProfil lie: {active_profile['key']} (slot {active_slot['value']}).\n"
            f"{_telegram_status_text(active_profile['key'])}",
            f"/telegram {_mask_token(token)}",
        )

    def _is_mobile_request() -> bool:
        try:
            request = ui.context.client.request
            user_agent = str(request.headers.get("user-agent") or "").casefold()
        except Exception:
            user_agent = ""
        if not user_agent:
            return False
        mobile_tokens = (
            "android",
            "iphone",
            "ipad",
            "mobile",
            "windows phone",
            "opera mini",
            "blackberry",
        )
        return any(token in user_agent for token in mobile_tokens)

    is_mobile_client = _is_mobile_request()

    if is_mobile_client:
        left_mobile_drawer = ui.left_drawer(value=False, fixed=True).classes('mobile-drawer').props(
            'overlay bordered behavior=mobile width=340'
        )
        left_mobile_drawer_holder["widget"] = left_mobile_drawer
        with left_mobile_drawer:
            with ui.element("div").classes('mobile-panel-card mobile-drawer-content'):
                with ui.row().classes('w-full items-center'):
                    ui.label("Menu").classes("text-sm font-semibold")
                    ui.space()
                    ui.button("Fermer", on_click=left_mobile_drawer.hide).props("flat dense no-caps")
                ui.separator()

                @ui.refreshable
                def render_left_mobile() -> None:
                    left_panel(state, on_change, mobile_menu=True)

                left_mobile_refresh_holder["refresh"] = render_left_mobile.refresh
                render_left_mobile()

        right_mobile_drawer = ui.right_drawer(value=False, fixed=True).classes('mobile-drawer').props(
            'overlay bordered behavior=mobile width=340'
        )
        right_mobile_drawer_holder["widget"] = right_mobile_drawer
        with right_mobile_drawer:
            with ui.element("div").classes('mobile-panel-card mobile-drawer-content'):
                with ui.row().classes('w-full items-center'):
                    ui.label("Narration").classes("text-sm font-semibold")
                    ui.space()
                    ui.button("Fermer", on_click=right_mobile_drawer.hide).props("flat dense no-caps")
                ui.separator()

                @ui.refreshable
                def render_right_mobile() -> None:
                    right_narrator(state)

                right_mobile_refresh_holder["refresh"] = render_right_mobile.refresh
                render_right_mobile()

    known_profiles = save_manager.list_profiles()

    with ui.column().classes('w-full gap-3'):
        with ui.card().classes('w-full rounded-2xl').style('margin-bottom: 10px;') as login_card:
            ui.label("Ataryxia : Qui est la ?").classes("text-lg font-semibold")
            ui.label("Entre ton pseudo pour acceder a tes 3 slots personnels.").classes("text-sm opacity-80")
            if known_profiles:
                ui.separator()
                ui.label("Profils connus").classes("font-semibold text-sm")
                for row in known_profiles[:20]:
                    profile_name = str(row.get("display_name") or row.get("profile_key") or "").strip()
                    profile_key = str(row.get("profile_key") or "").strip()
                    if not profile_name:
                        continue
                    with ui.row().classes("w-full items-center gap-2"):
                        ui.button(
                            profile_name,
                            on_click=lambda name=profile_name: _activate_profile(name),
                        ).props("outline dense no-caps")
                        ui.label(_profile_summary_line(profile_key)).classes("text-xs opacity-70")
            ui.separator()
            pseudo_input = ui.input(placeholder="Pseudo joueur").classes("w-full")

            def _submit_profile() -> None:
                _activate_profile(str(pseudo_input.value or ""))

            pseudo_input.on("keydown.enter", lambda e: _submit_profile())
            ui.button("Entrer", on_click=_submit_profile).props("dense no-caps")
        login_card_holder["widget"] = login_card

        with ui.column().classes('w-full gap-3') as game_container:
            with ui.card().classes('w-full rounded-2xl').style('margin-bottom: 10px;'):
                with ui.row().classes('w-full items-center gap-2'):
                    ui.label('Session').classes('font-semibold')
                    profile_label = ui.label("Profil: -").classes("text-xs opacity-70")
                    profile_label_holder["widget"] = profile_label
                    ui.switch(
                        'Mode nuit',
                        value=True,
                        on_change=lambda e: dark.enable() if e.value else dark.disable(),
                    ).props('dense color=amber')
                    ai_status_label = ui.label("IA locale: verification...").classes("text-xs opacity-75")
                    ai_status_label_holder["widget"] = ai_status_label

                    nsfw_switch = ui.switch(
                        "Mode Adulte",
                        value=is_nsfw_mode_enabled(state),
                        on_change=_on_nsfw_switch_change,
                    ).props("dense color=red")
                    nsfw_switch_holder["widget"] = nsfw_switch
                    with ui.element("div").style("display:none;") as nsfw_dialog_host:
                        pass
                    nsfw_dialog_host_holder["widget"] = nsfw_dialog_host
                    ui.space()
                    ui.button('Sauvegarde', on_click=_open_save_dialog).props('outline dense no-caps')
                    ui.button('Prototype 2D', on_click=lambda: ui.navigate.to('/prototype-2d')).props('outline dense no-caps')

            with ui.dialog() as save_dialog:
                with ui.card().classes('w-full rounded-2xl').style('max-width: 560px; width: min(94vw, 560px);'):
                    ui.label('Gestion des sauvegardes').classes('text-lg font-semibold')
                    save_profile_label = ui.label("Profil: -").classes("text-sm opacity-80")
                    save_profile_label_holder["widget"] = save_profile_label
                    save_summary_label = ui.label("Aucune sauvegarde.").classes("text-xs opacity-70")
                    save_summary_label_holder["widget"] = save_summary_label
                    ui.separator()

                    slot_select = ui.select(
                        options={i: f'Slot {i}' for i in range(1, SAVE_SLOT_COUNT + 1)},
                        value=active_slot["value"],
                        on_change=lambda e: _set_active_slot(e.value),
                    ).props('outlined dense').classes('w-full')
                    slot_select_holder["widget"] = slot_select

                    with ui.row().classes('w-full items-center gap-2'):
                        ui.button('Charger', on_click=lambda: _load_current_slot(show_notify=True)).props('outline no-caps')
                        ui.button('Sauvegarder', on_click=lambda: _persist_current_slot(show_notify=True, force=True)).props('no-caps')
                        ui.button('Nouvelle partie', on_click=_new_game_in_slot).props('outline no-caps')
                    with ui.row().classes('w-full justify-end'):
                        ui.button('Fermer', on_click=save_dialog.close).props('flat no-caps')
            save_dialog_holder["widget"] = save_dialog

            if is_mobile_client:
                with ui.column().classes('w-full mobile-layout'):
                    with ui.card().classes('mobile-panel-card center-mobile'):
                        @ui.refreshable
                        def render_center_mobile() -> None:
                            center_dialogue(state, on_change, chat_command_handler=_chat_command_handler)

                        panel_refreshers.append(render_center_mobile.refresh)
                        render_center_mobile()
            else:
                with ui.row().classes('w-full desktop-layout'):
                    with ui.card().classes('rounded-2xl desktop-panel-left').style('overflow-y:auto; overflow-x:hidden;'):
                        @ui.refreshable
                        def render_left_desktop() -> None:
                            left_panel(state, on_change)

                        panel_refreshers.append(render_left_desktop.refresh)
                        render_left_desktop()

                    with ui.card().classes('rounded-2xl desktop-panel-center'):
                        @ui.refreshable
                        def render_center_desktop() -> None:
                            center_dialogue(state, on_change, chat_command_handler=_chat_command_handler)

                        panel_refreshers.append(render_center_desktop.refresh)
                        render_center_desktop()

                    with ui.card().classes('rounded-2xl desktop-panel-right'):
                        @ui.refreshable
                        def render_right_desktop() -> None:
                            right_narrator(state)

                        panel_refreshers.append(render_right_desktop.refresh)
                        right_refreshers.append(render_right_desktop.refresh)
                        render_right_desktop()
        game_container.set_visibility(False)
        game_container_holder["widget"] = game_container

    ui.timer(
        AUTOSAVE_TICK_SECONDS,
        _autosave_tick,
    )

    ui.timer(
        1.0,
        lambda: (
            _refresh_right_panels()
            if session["ready"] and state.ensure_narrator_image_if_expired()
            else None
        ),
    )

    ui.timer(
        MEDIA_EVERY_Y_SECONDS,
        lambda: (
            (maybe_start_random_media(state, duration_seconds=MEDIA_DURATION_SECONDS) and _refresh_right_panels())
            if session["ready"] and not state.narrator_media_url.endswith(".mp4")
            else None
        ),
    )

    ui.timer(
        8.0,
        _refresh_ai_health,
    )
