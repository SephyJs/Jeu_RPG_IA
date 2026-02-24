from __future__ import annotations

import difflib
import hashlib
import json
import os
import random
import re
import unicodedata
from typing import Any, Callable
from pydantic import ValidationError

from app.core.events import (
    EventBus,
    OnLocationEntered,
    OnNpcTensionChanged,
    OnQuestUpdated,
    OnTradeCompleted,
    get_global_event_bus,
)
from .ollama_client import OllamaClient
from .models import MODEL_NAME, model_for
from .debug_commands import parse_debug_command
from .router import detect_target
from .schemas import ChoiceOption, Plan, RollResult, TurnResult
from .dice import roll as roll_dice
from .state_patch import apply_patch
from .npc_manager import (
    apply_attraction_delta,
    apply_tension_delta,
    is_npc_blacklisted,
    normalize_profile_extensions_in_place,
    profile_aggressiveness_level,
    profile_attraction_for_player,
    profile_corruption_level,
    profile_morale_level,
    profile_tension_level,
    set_npc_blacklist,
    tension_tier_label,
)
from .prompts import (
    build_canon_summary,
    prompt_telegram_ataryxia_dialogue,
    prompt_rules_json,
    prompt_dialogue,
    prompt_narration,
)
from .telegram_ataryxia_core import (
    ensure_user_anchor,
    fallback_non_repetitive_reply_seeded,
    extract_media_tag,
    extract_gen_image_tag,
    format_sms_reply,
    get_recent_replies,
    is_game_framing_reply,
    is_meta_or_restrictive_reply,
    is_poetic_nature_reply,
    is_question_unanswered_reply,
    is_repetitive_reply,
    is_telegram_ataryxia_mode,
    is_work_topic_message,
    remember_reply,
    strip_speaker_prefix,
)

NARRATION_MAX_SENTENCES_DEFAULT = 2
NARRATION_MAX_SENTENCES_TRAINING = 1
NARRATION_MAX_CHARS = 220


class GameMaster:
    def __init__(
        self,
        llm: OllamaClient,
        *,
        seed: int | None = None,
        decision_mode_v2: bool | None = None,
        event_bus: EventBus | None = None,
    ):
        self.llm = llm
        self.debug_enabled = False
        self.debug_forced: str | None = None  # "mistral"|"qwen"|"dolphin"|None
        self.rng = random.Random(seed)
        if decision_mode_v2 is None:
            env_value = str(os.getenv("GM_DECISION_MODE_V2", "1")).strip().casefold()
            self.decision_mode_v2_default = env_value not in {"0", "false", "off", "no"}
        else:
            self.decision_mode_v2_default = bool(decision_mode_v2)
        self.event_bus = event_bus if isinstance(event_bus, EventBus) else get_global_event_bus()
        self._pending_events: list[Any] = []
        self._event_unsubscribers: list[Callable[[], None]] = []
        self._event_unsubscribers.append(self.event_bus.subscribe(OnTradeCompleted, self._on_trade_completed))
        self._event_unsubscribers.append(self.event_bus.subscribe(OnQuestUpdated, self._on_quest_updated))
        self._event_unsubscribers.append(self.event_bus.subscribe(OnLocationEntered, self._on_location_entered))

    def close(self) -> None:
        for unsubscribe in self._event_unsubscribers:
            try:
                unsubscribe()
            except Exception:
                continue
        self._event_unsubscribers.clear()

    async def play_turn(self, state: dict, user_text: str) -> TurnResult:
        # ---- DEBUG layer (temporary) ----
        choice, handled = parse_debug_command(user_text, self.debug_enabled)
        self.debug_enabled = choice.enabled

        if handled:
            return TurnResult(mode="debug", system=f"Debug {'ON' if self.debug_enabled else 'OFF'}")

        if self.debug_enabled:
            self.debug_forced = choice.forced_model

        text = choice.cleaned_text if choice.cleaned_text != "" else user_text

        # Forced model: raw prompt (debug)
        if self.debug_enabled and self.debug_forced:
            model = MODEL_NAME[self.debug_forced]
            out = await self.llm.generate(
                model=model,
                prompt=text,
                temperature=0.7,
                num_ctx=2048,
                num_predict=300,
            )
            return TurnResult(mode="debug", model_used=self.debug_forced, narration=out)

        # ---- AUTO pipeline ----
        return await self._play_turn_auto(state, text)

    async def _play_turn_auto(self, state: dict, user_text: str) -> TurnResult:
        selected_npc = str(state.get("selected_npc") or "").strip()
        prompt_user_text = self._sanitize_user_text_for_dialogue(user_text, selected_npc)
        if is_telegram_ataryxia_mode(state):
            return await self._play_turn_telegram_ataryxia(
                state=state,
                user_text=prompt_user_text,
            )
        canon = build_canon_summary(state, prompt_user_text)
        decision_mode_v2 = self._decision_mode_v2_enabled(state)
        verbose_mode = self._verbose_mode_enabled(state)
        system_lines: list[str] = []

        # 1) RULES: plan JSON (mistral)
        plan = self._normalize_plan_for_engine(await self._get_plan(canon))
        if not decision_mode_v2:
            plan.output_type = "dialogue"
            plan.options = []
            plan.event_text = ""
            plan.event_state_patch = {}

        # 2) Dice (code) — robust
        roll_results: list[RollResult] = []
        for rr in plan.rolls:
            raw_expr = (rr.expr or "").strip()
            expr = self._sanitize_dice_expr(raw_expr)

            try:
                total, detail = roll_dice(expr, rng=self.rng)
                # On garde expr “propre” pour l'affichage
                roll_results.append(RollResult(expr=expr, total=total, detail=detail))
            except Exception:
                # fallback safe
                total, detail = roll_dice("d20", rng=self.rng)
                roll_results.append(
                    RollResult(
                        expr="d20",
                        total=total,
                        detail=f"fallback (bad expr was: {raw_expr}) | {detail}",
                    )
                )

        roll_summary = "\n".join(f"- {r.expr}: {r.total} ({r.detail})" for r in roll_results) or "(aucun)"

        # 3) Apply patch (canon state)
        apply_patch(state, plan.state_patch)

        # 4) Dialogue if target or type talk
        target = self._resolve_dialogue_target(
            selected_npc=selected_npc,
            plan_target=plan.target,
            user_text=user_text,
            state=state,
        )
        dialogue_text = None
        speaker = None
        output_type = "dialogue"
        options: list[ChoiceOption] = []
        event_text: str | None = None
        npc_profile = None

        if target:
            speaker = target
            player_name = re.sub(r"\s+", " ", str(state.get("player_name") or "").strip())
            npc_profile = self._find_npc_profile(state.get("npc_profiles"), target, state.get("location_id"))
            if not npc_profile and selected_npc and self._norm_token(target) == self._norm_token(selected_npc):
                selected_profile = state.get("selected_npc_profile")
                if isinstance(selected_profile, dict):
                    npc_profile = selected_profile
            if isinstance(npc_profile, dict):
                normalize_profile_extensions_in_place(npc_profile, fallback_label=target)
            event_reactions = self._consume_pending_events(
                state=state,
                npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
                npc_name=target,
            )
            if event_reactions:
                system_lines.extend(event_reactions)

            plan = self._ensure_agenda_choice_if_needed(
                plan,
                npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
                user_text=prompt_user_text,
            )
            if not decision_mode_v2:
                plan.output_type = "dialogue"
                plan.options = []

            old_tension, new_tension, tension_reason = self._apply_turn_tension(
                state=state,
                npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
                npc_name=target,
                user_text=prompt_user_text,
                plan=plan,
                roll_results=roll_results,
            )
            if old_tension != new_tension:
                self.event_bus.publish(
                    OnNpcTensionChanged(
                        npc_key=str(self._selected_npc_key(state) or ""),
                        npc_name=target,
                        old_value=old_tension,
                        new_value=new_tension,
                        reason=tension_reason,
                    )
                )
            social_lines = self._apply_social_dynamics(
                state=state,
                npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
                npc_name=target,
                player_name=player_name,
                user_text=prompt_user_text,
                plan=plan,
                roll_results=roll_results,
            )
            if social_lines:
                system_lines.extend(social_lines)

            world_time_minutes = self._safe_int(state.get("world_time_minutes"), 0)
            rupture_active = (
                isinstance(npc_profile, dict)
                and (
                    profile_tension_level(npc_profile) >= 90
                    or is_npc_blacklisted(npc_profile, world_time_minutes=world_time_minutes)
                )
            )
            if rupture_active and isinstance(npc_profile, dict):
                set_npc_blacklist(
                    npc_profile,
                    until_world_time_minutes=world_time_minutes + 180,
                )
                output_type = "dialogue"
                dialogue_text = self._rupture_line(target, npc_profile)
                if not plan.narration_hooks:
                    plan.narration_hooks = ["L'echange se brise net, sous le regard froid du PNJ."]
                system_lines.append("Rupture: le PNJ refuse de cooperer pour un moment.")
            elif decision_mode_v2 and plan.output_type == "choice_required" and plan.options:
                output_type = "choice_required"
                options = self._normalize_choice_options(plan.options)
                dialogue_text = self._choice_intro_line(
                    target,
                    npc_profile if isinstance(npc_profile, dict) else None,
                    player_name=player_name,
                )
                if not plan.narration_hooks:
                    plan.narration_hooks = ["Le PNJ pose plusieurs options concretes et attend votre decision."]
            else:
                dialogue_prompt = prompt_dialogue(
                    target,
                    canon,
                    prompt_user_text,
                    roll_summary,
                    npc_profile=npc_profile,
                    player_name=player_name,
                    verbose_mode=verbose_mode,
                )
                dialogue_model = model_for("dialogue")
                dialogue_text = await self.llm.generate(
                    model=dialogue_model,
                    prompt=dialogue_prompt,
                    temperature=0.8,
                    num_ctx=2048,
                    num_predict=250,
                    fallback_models=self._fallback_models(dialogue_model),
                )
                dialogue_text = self._sanitize_dialogue_self_addressing(
                    dialogue_text,
                    player_name=player_name,
                    identity_names=self._dialogue_identity_candidates(target, npc_profile),
                )
                dialogue_text = self._limit_dialogue_by_tension(
                    dialogue_text,
                    npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
                    verbose_mode=verbose_mode,
                )
                lie_line = self._update_truth_state_after_reply(
                    state=state,
                    npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
                    user_text=prompt_user_text,
                    npc_reply=dialogue_text,
                )
                if lie_line:
                    system_lines.append(lie_line)

            if dialogue_text and isinstance(dialogue_text, str):
                dialogue_text = self._ensure_dialogue_variety(
                    state=state,
                    npc_name=target,
                    dialogue_text=dialogue_text,
                )
                self._remember_dialogue_reply(
                    state=state,
                    npc_name=target,
                    dialogue_text=dialogue_text,
                )

        # 5) Narration: interprète l'échange joueur <-> PNJ du tour
        micro_event = self._maybe_generate_micro_event(
            npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
            user_text=prompt_user_text,
            plan=plan,
        )
        if decision_mode_v2 and plan.output_type == "event" and plan.event_text:
            event_text = self._clean_single_line(plan.event_text, fallback="Un incident coupe brièvement la conversation.")
            self._apply_extra_state_patch(
                state,
                plan.event_state_patch if isinstance(plan.event_state_patch, dict) else {},
                npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
            )
            output_type = "event"
        elif isinstance(micro_event, dict):
            event_text = str(micro_event.get("text") or "").strip()
            if event_text:
                self._apply_extra_state_patch(
                    state,
                    micro_event.get("state_patch") if isinstance(micro_event.get("state_patch"), dict) else {},
                    npc_profile=npc_profile if isinstance(npc_profile, dict) else None,
                )
                output_type = "event" if bool(micro_event.get("interrupt")) else output_type

        if output_type == "choice_required":
            narration_text = plan.narration_hooks[0] if plan.narration_hooks else "Le PNJ attend votre choix."
            narration_text = self._limit_narration_sentences(
                narration_text,
                max_sentences=1,
                hooks=plan.narration_hooks,
                max_chars=NARRATION_MAX_CHARS,
            )
        else:
            turn_exchange_lines = [f"Joueur: {prompt_user_text}"]
            if speaker and dialogue_text:
                turn_exchange_lines.append(f"{speaker}: {dialogue_text}")
            turn_exchange = "\n".join(turn_exchange_lines)

            narration_prompt = prompt_narration(
                canon,
                prompt_user_text,
                plan.narration_hooks,
                roll_summary,
                turn_exchange=turn_exchange,
            )
            narration_model = model_for("narration")
            narration_text = await self.llm.generate(
                model=narration_model,
                prompt=narration_prompt,
                temperature=0.7,
                num_ctx=4096,
                num_predict=180,
                fallback_models=self._fallback_models(narration_model),
            )
            narration_text = self._sanitize_narration_text(narration_text, plan.narration_hooks)
            max_sentences = NARRATION_MAX_SENTENCES_DEFAULT
            if self._is_training_message(prompt_user_text):
                max_sentences = NARRATION_MAX_SENTENCES_TRAINING
            narration_text = self._limit_narration_sentences(
                narration_text,
                max_sentences=max_sentences,
                hooks=plan.narration_hooks,
                max_chars=NARRATION_MAX_CHARS,
            )

        return TurnResult(
            mode="auto",
            narration=narration_text,
            speaker=speaker,
            dialogue=dialogue_text,
            plan=plan,
            rolls=roll_results,
            output_type=output_type,
            options=options,
            event_text=event_text,
            system="\n".join(system_lines).strip() or None,
        )

    async def _play_turn_telegram_ataryxia(self, *, state: dict, user_text: str) -> TurnResult:
        npc_name = "Ataryxia"
        player_name = re.sub(r"\s+", " ", str(state.get("player_name") or "").strip())
        selected_profile = state.get("selected_npc_profile") if isinstance(state.get("selected_npc_profile"), dict) else None
        if not isinstance(selected_profile, dict):
            selected_profile = self._find_npc_profile(
                state.get("npc_profiles"),
                npc_name,
                state.get("location_id"),
            )
        npc_profile = selected_profile if isinstance(selected_profile, dict) else {}
        recent_replies = get_recent_replies(state, max_items=8)
        flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
        freeform_mode = bool(flags.get("telegram_ataryxia_freeform"))
        work_topic_mode = is_work_topic_message(user_text)
        turn_seed = max(1, self._safe_int(flags.get("telegram_ataryxia_turn_id"), 0) + 1)
        flags["telegram_ataryxia_turn_id"] = turn_seed
        sms_canon = self._build_telegram_sms_canon(state=state, user_text=user_text)
        dialogue_model = self._telegram_dialogue_model()
        telegram_fallback_models = self._telegram_fallback_models(dialogue_model)

        prompt = prompt_telegram_ataryxia_dialogue(
            canon=sms_canon,
            user_text=user_text,
            player_name=player_name,
            recent_replies=recent_replies,
            npc_profile=npc_profile,
            freeform_mode=freeform_mode,
            work_topic_mode=work_topic_mode,
            last_reply=recent_replies[-1] if recent_replies else "",
        )
        dialogue_text = await self.llm.generate(
            model=dialogue_model,
            prompt=prompt,
            temperature=self._telegram_temperature(default=0.45),
            num_ctx=4096,
            num_predict=self._telegram_num_predict(default=220),
            fallback_models=telegram_fallback_models,
        )
        dialogue_text, media_keyword = extract_media_tag(dialogue_text)
        dialogue_text, gen_image_prompt = extract_gen_image_tag(dialogue_text)
        dialogue_text = strip_speaker_prefix(dialogue_text, speaker=npc_name)
        dialogue_text = self._sanitize_dialogue_self_addressing(
            dialogue_text,
            player_name=player_name,
            identity_names=self._dialogue_identity_candidates(npc_name, npc_profile),
        )
        dialogue_text = re.sub(r"\s+", " ", str(dialogue_text or "").strip())
        dialogue_text = ensure_user_anchor(dialogue_text, user_text, turn_seed=turn_seed)
        dialogue_text = format_sms_reply(dialogue_text, max_lines=3, max_chars=220, max_line_chars=110)

        def _invalid_sms_reply(text: str) -> bool:
            return (
                not str(text or "").strip()
                or is_meta_or_restrictive_reply(text)
                or is_game_framing_reply(text, allow_work_topic=work_topic_mode)
                or is_repetitive_reply(text, recent_replies)
                or is_question_unanswered_reply(text, user_text)
                or is_poetic_nature_reply(text, user_text)
            )

        if _invalid_sms_reply(dialogue_text):
            retry_prompt = (
                f"{prompt}\n\n"
                "CORRECTION OBLIGATOIRE:\n"
                "- Reponds en SMS direct, 1 a 2 phrases, maximum 220 caracteres.\n"
                "- Reponds D'ABORD a la question du joueur si son message est interrogatif.\n"
                "- Pas de metaphore poetique ni d'imagerie foret/vent/lune sauf demande explicite.\n"
                "- Pas de blabla, pas de relance automatique, pas de role PNJ.\n"
                "- Pas d'emoji."
            )
            retry = await self.llm.generate(
                model=dialogue_model,
                prompt=retry_prompt,
                temperature=self._telegram_temperature(default=0.35, env_key="ATARYXIA_TELEGRAM_RETRY_TEMP"),
                num_ctx=4096,
                num_predict=self._telegram_num_predict(default=220, env_key="ATARYXIA_TELEGRAM_RETRY_NUM_PREDICT"),
                fallback_models=telegram_fallback_models,
            )
            retry, retry_media = extract_media_tag(retry)
            if retry_media:
                media_keyword = retry_media
            retry, retry_gen = extract_gen_image_tag(retry)
            if retry_gen:
                gen_image_prompt = retry_gen
            retry = strip_speaker_prefix(retry, speaker=npc_name)
            retry = self._sanitize_dialogue_self_addressing(
                retry,
                player_name=player_name,
                identity_names=self._dialogue_identity_candidates(npc_name, npc_profile),
            )
            retry = re.sub(r"\s+", " ", str(retry or "").strip())
            retry = ensure_user_anchor(retry, user_text, turn_seed=turn_seed + 1)
            retry = format_sms_reply(retry, max_lines=3, max_chars=220, max_line_chars=110)
            if not _invalid_sms_reply(retry):
                dialogue_text = retry

        if _invalid_sms_reply(dialogue_text):
            dialogue_text = fallback_non_repetitive_reply_seeded(user_text, recent_replies, turn_seed=turn_seed)
            dialogue_text = format_sms_reply(dialogue_text, max_lines=3, max_chars=220, max_line_chars=110)

        remember_reply(state, dialogue_text)
        return TurnResult(
            mode="auto",
            speaker=npc_name,
            dialogue=dialogue_text,
            narration="",
            output_type="dialogue",
            media_keyword=media_keyword,
            generated_image_prompt=gen_image_prompt,
        )

    def _telegram_dialogue_model(self) -> str:
        explicit = str(os.getenv("ATARYXIA_TELEGRAM_MODEL") or "").strip()
        if explicit:
            return explicit
        key = str(os.getenv("ATARYXIA_TELEGRAM_MODEL_KEY") or "").strip().casefold()
        if key in MODEL_NAME:
            return MODEL_NAME[key]
        # Telegram chat mode is typically more stable with qwen as default.
        return MODEL_NAME.get("qwen", model_for("dialogue"))

    def _telegram_temperature(self, *, default: float, env_key: str = "ATARYXIA_TELEGRAM_TEMP") -> float:
        raw = str(os.getenv(env_key, str(default)) or str(default)).strip()
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return float(default)
        return max(0.0, min(1.2, value))

    def _telegram_num_predict(self, *, default: int, env_key: str = "ATARYXIA_TELEGRAM_NUM_PREDICT") -> int:
        raw = str(os.getenv(env_key, str(default)) or str(default)).strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return int(default)
        return max(80, min(480, value))

    def _telegram_fallback_models(self, primary_model: str) -> list[str]:
        disable_raw = str(os.getenv("ATARYXIA_TELEGRAM_DISABLE_FALLBACK", "0") or "").strip().casefold()
        if disable_raw in {"1", "true", "on", "yes"}:
            return []
        configured = str(os.getenv("ATARYXIA_TELEGRAM_FALLBACK_MODELS", "") or "").strip()
        if configured:
            out: list[str] = []
            for raw in configured.split(","):
                candidate = str(raw or "").strip()
                if not candidate:
                    continue
                normalized = MODEL_NAME.get(candidate.casefold(), candidate)
                if normalized == primary_model or normalized in out:
                    continue
                out.append(normalized)
            return out
        return self._fallback_models(primary_model)

    def _build_telegram_sms_canon(self, *, state: dict, user_text: str) -> str:
        player_name = re.sub(r"\s+", " ", str(state.get("player_name") or "").strip()) or "Joueur"
        work_topic_mode = is_work_topic_message(user_text)
        short_term = str(state.get("conversation_short_term") or "").strip()
        long_term = str(state.get("conversation_long_term") or "").strip()
        retrieved = str(state.get("conversation_retrieved_memory") or "").strip()
        lines: list[str] = []
        if short_term:
            for raw in short_term.splitlines():
                row = re.sub(r"\s+", " ", str(raw or "").strip())
                if not row:
                    continue
                row = row.removeprefix("- ").strip()
                lower = row.casefold()
                if lower.startswith("joueur:") or lower.startswith("ataryxia:"):
                    lines.append(row[:180])
        recent_text = "\n".join(lines[-6:]) if lines else "(aucun)"
        memory_blocks: list[str] = []
        if long_term:
            memory_blocks.append(long_term)
        if retrieved:
            memory_blocks.append(retrieved)
        memory_text = "\n".join(memory_blocks).strip() or "(aucun)"
        work_line = "Sujet actuel: hors travail (discussion perso)." if not work_topic_mode else "Sujet actuel: travail de narratrice accepte."
        return (
            "Canal: Telegram SMS prive\n"
            f"Interlocuteurs: {player_name} et Ataryxia\n"
            "Regle de canal: conversation personnelle, style message prive, hors narration de jeu.\n"
            "Ne bascule pas en PNJ de quete/donjon sauf demande explicite du joueur.\n"
            f"{work_line}\n"
            f"Message joueur actuel: {user_text}\n"
            f"Extraits recents:\n{recent_text}\n"
            f"Souvenirs pertinents:\n{memory_text}\n"
        )

    def _safe_int(self, value: object, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _decision_mode_v2_enabled(self, state: dict) -> bool:
        flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
        if "decision_mode_v2" in flags:
            return bool(flags.get("decision_mode_v2"))
        return bool(self.decision_mode_v2_default)

    def _verbose_mode_enabled(self, state: dict) -> bool:
        flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
        return bool(flags.get("verbose_dialogue"))

    def _selected_npc_key(self, state: dict) -> str:
        return str(state.get("selected_npc_key") or "").strip()

    def _clean_single_line(self, value: object, *, fallback: str = "") -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text:
            return fallback
        return text[:220]

    def _normalize_plan_for_engine(self, plan: Plan) -> Plan:
        # Compat: accepte `choices` ou `options` et synchronise output_type avec decision_type.
        if not plan.options and plan.choices:
            plan.options = list(plan.choices)
        if not plan.choices and plan.options:
            plan.choices = list(plan.options)

        decision_type = str(getattr(plan, "decision_type", "dialogue") or "dialogue").strip().casefold()
        if decision_type == "choice" and plan.output_type != "choice_required":
            plan.output_type = "choice_required"
        elif decision_type == "event" and plan.output_type == "dialogue":
            plan.output_type = "event"
        elif decision_type in {"dialogue", "combat"} and plan.output_type not in {"dialogue", "choice_required", "event"}:
            plan.output_type = "dialogue"

        plan.tension_delta = max(-35, min(35, self._safe_int(plan.tension_delta, 0)))
        plan.morale_delta = max(-35, min(35, self._safe_int(plan.morale_delta, 0)))
        plan.corruption_delta = max(-35, min(35, self._safe_int(plan.corruption_delta, 0)))
        plan.attraction_delta = max(-35, min(35, self._safe_int(plan.attraction_delta, 0)))
        return plan

    def _normalize_choice_options(self, options: list[ChoiceOption]) -> list[ChoiceOption]:
        out: list[ChoiceOption] = []
        seen: set[str] = set()
        for idx, option in enumerate(options[:3]):
            option_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(option.id or "").strip()).strip("_").casefold()
            if not option_id:
                option_id = f"option_{idx + 1}"
            if option_id in seen:
                continue
            seen.add(option_id)
            state_patch = option.state_patch if isinstance(option.state_patch, dict) else {}
            if not state_patch:
                state_patch = {"flags": {f"choice_{option_id[:24]}": True}}
            out.append(
                ChoiceOption(
                    id=option_id[:40],
                    text=self._clean_single_line(option.text, fallback=f"Option {idx + 1}")[:120],
                    risk_tag=self._clean_single_line(option.risk_tag, fallback="moyen")[:32],
                    effects_hint=self._clean_single_line(option.effects_hint, fallback="Impact mesuré.")[:140],
                    state_patch=state_patch,
                )
            )
        return out

    def _choice_intro_line(self, npc_name: str, npc_profile: dict | None, *, player_name: str = "") -> str:
        if isinstance(npc_profile, dict):
            tension = profile_tension_level(npc_profile)
            if tension >= 70:
                return "Je n'ai pas de temps a perdre. Choisis."
            corruption = profile_corruption_level(npc_profile)
            if corruption >= 70:
                return "J'ai une proposition utile... et sale. Choisis."
            attraction = profile_attraction_for_player(npc_profile, player_name)
            if attraction >= 70:
                return "Reste proche. Je te laisse trois options."
            agenda = str(npc_profile.get("agenda_secret") or "").strip()
            if agenda:
                return "Voici tes options. Choisis vite, chacune a un prix."
        return f"{npc_name} vous propose un choix concret."

    def _limit_dialogue_by_tension(self, text: str, *, npc_profile: dict | None, verbose_mode: bool) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if not cleaned:
            return cleaned
        if verbose_mode:
            max_sentences = 7
        else:
            max_sentences = 5
            if isinstance(npc_profile, dict) and profile_tension_level(npc_profile) > 70:
                max_sentences = 2
        chunks = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
        if len(chunks) <= max_sentences:
            return cleaned
        return " ".join(chunks[:max_sentences]).strip()

    def _rupture_line(self, npc_name: str, npc_profile: dict | None) -> str:
        if isinstance(npc_profile, dict):
            truth = npc_profile.get("truth_state") if isinstance(npc_profile.get("truth_state"), dict) else {}
            active_lie = truth.get("mensonge_actif") if isinstance(truth.get("mensonge_actif"), dict) else {}
            if active_lie:
                return "Cette conversation est terminee. Pars avant que j'appelle la garde."
        return f"{npc_name}: Cela suffit. Conversation terminee."

    def _apply_extra_state_patch(self, state: dict, patch: dict, *, npc_profile: dict | None) -> None:
        if not isinstance(patch, dict):
            return

        # Reuse secure patch for generic sections.
        apply_patch(state, patch)

        rep_patch = patch.get("reputation")
        if isinstance(rep_patch, dict):
            current = state.get("faction_reputation")
            if not isinstance(current, dict):
                current = {}
                state["faction_reputation"] = current
            for key, value in rep_patch.items():
                faction = str(key or "").strip()[:80]
                if not faction:
                    continue
                delta = max(-30, min(30, self._safe_int(value, 0)))
                current[faction] = max(-100, min(100, self._safe_int(current.get(faction), 0) + delta))

        npc_patch = patch.get("npc")
        if isinstance(npc_patch, dict) and isinstance(npc_profile, dict):
            if "tension_set" in npc_patch:
                npc_profile["tension_level"] = max(0, min(100, self._safe_int(npc_patch.get("tension_set"), profile_tension_level(npc_profile))))
            if "tension_delta" in npc_patch:
                apply_tension_delta(npc_profile, delta=max(-35, min(35, self._safe_int(npc_patch.get("tension_delta"), 0))), reason="state_patch:npc")
            if "morale_delta" in npc_patch:
                npc_profile["morale"] = max(0, min(100, profile_morale_level(npc_profile) + self._safe_int(npc_patch.get("morale_delta"), 0)))
            if "aggressiveness_delta" in npc_patch:
                npc_profile["aggressiveness"] = max(
                    0,
                    min(100, profile_aggressiveness_level(npc_profile) + self._safe_int(npc_patch.get("aggressiveness_delta"), 0)),
                )
            if "corruption_delta" in npc_patch:
                npc_profile["corruption_level"] = max(
                    0,
                    min(100, profile_corruption_level(npc_profile) + self._safe_int(npc_patch.get("corruption_delta"), 0)),
                )
            if "attraction_delta" in npc_patch:
                player_id = str(npc_patch.get("attraction_player_id") or state.get("player_name") or "").strip()
                apply_attraction_delta(
                    npc_profile,
                    player_id=player_id,
                    delta=self._safe_int(npc_patch.get("attraction_delta"), 0),
                    reason="state_patch:npc",
                )
            if profile_aggressiveness_level(npc_profile) >= 75:
                npc_profile["dominance_style"] = "aggressive"
            elif profile_corruption_level(npc_profile) >= 70:
                npc_profile["dominance_style"] = "manipulative"
            elif profile_tension_level(npc_profile) >= 65:
                npc_profile["dominance_style"] = "cold"
            else:
                npc_profile["dominance_style"] = "soft"
            truth_patch = npc_patch.get("truth_state")
            if isinstance(truth_patch, dict):
                truth = npc_profile.get("truth_state") if isinstance(npc_profile.get("truth_state"), dict) else {}
                truth.update(truth_patch)
                npc_profile["truth_state"] = truth

        player_patch = patch.get("player")
        if isinstance(player_patch, dict) and "corruption_delta" in player_patch:
            base = max(0, min(100, self._safe_int(state.get("player_corruption_level"), 0)))
            state["player_corruption_level"] = max(0, min(100, base + self._safe_int(player_patch.get("corruption_delta"), 0)))

    def _heuristic_tension_delta(self, user_text: str, plan: Plan, roll_results: list[RollResult]) -> tuple[int, str]:
        text = self._norm_token(user_text)
        delta = 0
        reason = "interaction"

        if any(token in text for token in ("non", "refuse", "pas question", "j insiste", "insiste", "mentir", "mensonge", "menace", "sinon")):
            delta += 5
            reason = "refus/pression/menace"
        if any(token in text for token in ("aide", "j aide", "merci", "payer", "paie", "je paie", "je paye", "flatte", "compliment", "s il vous plait", "s'il vous plait")):
            delta -= 4
            reason = "aide/politesse"
        if any(token in self._norm_token(plan.intent) for token in ("menace", "forcer", "intimider")):
            delta += 6
            reason = "intent menaçant"

        for roll in roll_results:
            roll_reason = self._norm_token(getattr(roll, "detail", ""))
            if not any(token in roll_reason for token in ("persuasion", "social", "charisme", "negociation", "intimidation")):
                continue
            if roll.total >= 15:
                delta -= 5
                reason = "jet social reussi"
            elif roll.total <= 8:
                delta += 4
                reason = "jet social rate"

        return max(-20, min(20, delta)), reason

    def _apply_turn_tension(
        self,
        *,
        state: dict,
        npc_profile: dict | None,
        npc_name: str,
        user_text: str,
        plan: Plan,
        roll_results: list[RollResult],
    ) -> tuple[int, int, str]:
        if not isinstance(npc_profile, dict):
            return 0, 0, ""

        delta, reason = self._heuristic_tension_delta(user_text, plan, roll_results)
        explicit_plan_delta = self._safe_int(plan.tension_delta, 0)
        if explicit_plan_delta:
            delta += max(-35, min(35, explicit_plan_delta))
            reason = f"{reason} + decision_delta"
        patch = plan.state_patch if isinstance(plan.state_patch, dict) else {}
        npc_patch = patch.get("npc") if isinstance(patch.get("npc"), dict) else {}
        explicit_delta = self._safe_int(npc_patch.get("tension_delta"), 0)
        if explicit_delta:
            delta += max(-35, min(35, explicit_delta))
            reason = f"{reason} + state_patch"

        old, new = apply_tension_delta(npc_profile, delta=max(-35, min(35, delta)), reason=reason)
        if new >= 90:
            world_time = self._safe_int(state.get("world_time_minutes"), 0)
            set_npc_blacklist(npc_profile, until_world_time_minutes=world_time + 180)
        return old, new, reason

    def _heuristic_social_deltas(
        self,
        *,
        user_text: str,
        plan: Plan,
        roll_results: list[RollResult],
    ) -> tuple[int, int, int, int]:
        text = self._norm_token(user_text)
        morale_delta = 0
        aggression_delta = 0
        corruption_delta = 0
        attraction_delta = 0

        if any(token in text for token in ("merci", "respect", "pardon", "j aide", "je t aide", "je paie", "je paye")):
            morale_delta += 2
            aggression_delta -= 2
        if any(token in text for token in ("menace", "sinon", "oblige", "force", "ta gueule")):
            morale_delta -= 4
            aggression_delta += 5
        if any(token in text for token in ("deal", "arrangement", "pas vu pas pris", "pots de vin", "pot de vin", "chantage")):
            corruption_delta += 4
        if any(token in text for token in ("flatte", "charme", "seduire", "séduire", "beau", "belle", "desir", "désir")):
            attraction_delta += 4
        if any(token in text for token in ("aide", "proteger", "protéger", "soutien")):
            attraction_delta += 2

        for roll in roll_results:
            roll_detail = self._norm_token(getattr(roll, "detail", ""))
            if not any(token in roll_detail for token in ("social", "persuasion", "charisme", "seduction", "séduction", "negociation")):
                continue
            if roll.total >= 15:
                morale_delta += 2
                attraction_delta += 2
                aggression_delta -= 2
            elif roll.total <= 8:
                morale_delta -= 2
                attraction_delta -= 2
                aggression_delta += 2

        morale_delta += max(-20, min(20, self._safe_int(plan.morale_delta, 0)))
        corruption_delta += max(-20, min(20, self._safe_int(plan.corruption_delta, 0)))
        attraction_delta += max(-20, min(20, self._safe_int(plan.attraction_delta, 0)))
        return (
            max(-25, min(25, morale_delta)),
            max(-25, min(25, aggression_delta)),
            max(-25, min(25, corruption_delta)),
            max(-25, min(25, attraction_delta)),
        )

    def _apply_social_dynamics(
        self,
        *,
        state: dict,
        npc_profile: dict | None,
        npc_name: str,
        player_name: str,
        user_text: str,
        plan: Plan,
        roll_results: list[RollResult],
    ) -> list[str]:
        if not isinstance(npc_profile, dict):
            return []

        lines: list[str] = []
        morale_delta, aggression_delta, corruption_delta, attraction_delta = self._heuristic_social_deltas(
            user_text=user_text,
            plan=plan,
            roll_results=roll_results,
        )
        npc_patch = plan.state_patch.get("npc") if isinstance(plan.state_patch, dict) and isinstance(plan.state_patch.get("npc"), dict) else {}
        morale_delta += max(-20, min(20, self._safe_int(npc_patch.get("morale_delta"), 0)))
        aggression_delta += max(-20, min(20, self._safe_int(npc_patch.get("aggressiveness_delta"), 0)))
        corruption_delta += max(-20, min(20, self._safe_int(npc_patch.get("corruption_delta"), 0)))
        attraction_delta += max(-20, min(20, self._safe_int(npc_patch.get("attraction_delta"), 0)))

        old_morale = profile_morale_level(npc_profile)
        old_aggression = profile_aggressiveness_level(npc_profile)
        old_npc_corruption = profile_corruption_level(npc_profile)
        old_attraction = profile_attraction_for_player(npc_profile, player_name)

        npc_profile["morale"] = max(0, min(100, old_morale + morale_delta))
        npc_profile["aggressiveness"] = max(0, min(100, old_aggression + aggression_delta))
        npc_profile["corruption_level"] = max(0, min(100, old_npc_corruption + max(-12, min(12, corruption_delta // 2))))
        attraction_player_id = str(npc_patch.get("attraction_player_id") or player_name).strip()
        _, new_attraction = apply_attraction_delta(
            npc_profile,
            player_id=attraction_player_id,
            delta=attraction_delta,
            reason="social_interaction",
        )
        if profile_aggressiveness_level(npc_profile) >= 75:
            npc_profile["dominance_style"] = "aggressive"
        elif profile_corruption_level(npc_profile) >= 70:
            npc_profile["dominance_style"] = "manipulative"
        elif profile_tension_level(npc_profile) >= 65:
            npc_profile["dominance_style"] = "cold"
        else:
            npc_profile["dominance_style"] = "soft"

        current_player_corruption = max(0, min(100, self._safe_int(state.get("player_corruption_level"), 0)))
        player_patch = plan.state_patch.get("player") if isinstance(plan.state_patch, dict) and isinstance(plan.state_patch.get("player"), dict) else {}
        explicit_player_corr_delta = max(-25, min(25, self._safe_int(player_patch.get("corruption_delta"), 0)))
        if explicit_player_corr_delta == 0:
            explicit_player_corr_delta = max(-15, min(15, corruption_delta))
        new_player_corruption = max(0, min(100, current_player_corruption + explicit_player_corr_delta))
        state["player_corruption_level"] = new_player_corruption

        if old_morale != npc_profile["morale"]:
            lines.append(f"Morale PNJ: {old_morale} -> {npc_profile['morale']}")
        if old_aggression != npc_profile["aggressiveness"]:
            lines.append(f"Agressivite PNJ: {old_aggression} -> {npc_profile['aggressiveness']}")
        if old_npc_corruption != npc_profile["corruption_level"]:
            lines.append(f"Corruption PNJ: {old_npc_corruption} -> {npc_profile['corruption_level']}")
        if old_attraction != new_attraction:
            lines.append(f"Attraction {npc_name}: {old_attraction} -> {new_attraction}")
        if current_player_corruption != new_player_corruption:
            lines.append(f"Corruption joueur: {current_player_corruption} -> {new_player_corruption}")
        if profile_attraction_for_player(npc_profile, player_name) >= 70 and profile_tension_level(npc_profile) < 70:
            lines.append("Le ton devient plus intime, sans perdre la retenue.")
        if profile_corruption_level(npc_profile) >= 70:
            lines.append("Le PNJ glisse vers des propositions moralement ambigues.")
        return lines

    def _update_truth_state_after_reply(
        self,
        *,
        state: dict,
        npc_profile: dict | None,
        user_text: str,
        npc_reply: str,
    ) -> str:
        if not isinstance(npc_profile, dict):
            return ""

        normalize_profile_extensions_in_place(npc_profile, fallback_label=str(npc_profile.get("label") or "PNJ"))
        truth = npc_profile.get("truth_state") if isinstance(npc_profile.get("truth_state"), dict) else {}
        agenda = str(npc_profile.get("agenda_secret") or "").strip()
        rival_id = str(npc_profile.get("rival_id") or "").strip()
        text = self._norm_token(user_text)

        ask_probe = any(token in text for token in ("enquete", "enquête", "secret", "preuve", "rival", "mensonge"))
        has_active_lie = bool(truth.get("mensonge_actif")) and isinstance(truth.get("mensonge_actif"), dict)

        if agenda and ask_probe and not has_active_lie:
            expose_condition = "Enqueter plus loin ou parler au rival."
            if rival_id:
                expose_condition = f"Parler a {rival_id} ou enqueter dans la zone."
            lie = {
                "id": f"lie_{self.rng.randint(1000, 9999)}",
                "statement": self._clean_single_line(npc_reply, fallback="Declaration evasive."),
                "expose_condition": expose_condition,
                "created_at": str(self._safe_int(state.get("world_time_minutes"), 0)),
            }
            truth["mensonge_actif"] = dict(lie)
            active = truth.get("active_lies") if isinstance(truth.get("active_lies"), list) else []
            active.append(dict(lie))
            truth["active_lies"] = active[-8:]
            npc_profile["truth_state"] = truth
            return "Memoire secrète: un mensonge actif est enregistre."

        if has_active_lie and ask_probe and any(token in text for token in ("rival", "revenir", "plus tard", "preuve", "temoin", "témoin")):
            active_lie = truth.get("mensonge_actif") if isinstance(truth.get("mensonge_actif"), dict) else {}
            known = truth.get("known_secrets") if isinstance(truth.get("known_secrets"), list) else []
            expose_line = str(active_lie.get("statement") or "").strip()
            if expose_line:
                known.append(expose_line[:160])
            truth["known_secrets"] = known[-16:]
            truth["mensonge_actif"] = {}
            truth["last_reveal_at"] = str(self._safe_int(state.get("world_time_minutes"), 0))
            npc_profile["truth_state"] = truth
            return "Un mensonge potentiel vacille; une revelation devient possible."

        return ""

    def _ensure_agenda_choice_if_needed(self, plan: Plan, npc_profile: dict | None, user_text: str) -> Plan:
        if not isinstance(npc_profile, dict):
            return plan
        if plan.output_type == "choice_required" and plan.options:
            return plan

        agenda = str(npc_profile.get("agenda_secret") or "").strip()
        if not agenda:
            return plan

        text = self._norm_token(user_text)
        opportunity = any(token in text for token in ("aide", "service", "secret", "deal", "paye", "payer", "marché", "marche", "preuve", "rumeur"))
        if not opportunity:
            return plan

        tension = profile_tension_level(npc_profile)
        corruption = profile_corruption_level(npc_profile)
        dominance = str(npc_profile.get("dominance_style") or "soft").strip().casefold()
        base_delta = 4 if tension < 70 else 8
        plan.output_type = "choice_required"
        plan.decision_type = "choice"
        plan.options = [
            ChoiceOption(
                id="coop",
                text="Aider discrètement le PNJ",
                risk_tag="moyen",
                effects_hint="Confiance locale, mais dette morale.",
                state_patch={
                    "flags": {"npc_agenda_helped": True},
                    "npc": {"tension_delta": -6, "morale_delta": 2, "attraction_delta": 3},
                    "player": {"corruption_delta": 2 if corruption >= 60 else 0},
                    "reputation": {"Habitants": 1},
                },
            ),
            ChoiceOption(
                id="marchandage",
                text="Négocier une compensation",
                risk_tag="moyen",
                effects_hint="Gain materiel possible, tension moderee.",
                state_patch={
                    "flags": {"npc_agenda_bargain": True},
                    "npc": {"tension_delta": base_delta, "corruption_delta": 3, "attraction_delta": 1},
                    "player": {"corruption_delta": 4},
                    "reputation": {"Marchands": 1},
                },
            ),
            ChoiceOption(
                id="refus",
                text="Refuser et passer votre chemin",
                risk_tag="eleve",
                effects_hint="Relation degradee, portes fermees temporairement.",
                state_patch={
                    "flags": {"npc_agenda_refused": True},
                    "npc": {
                        "tension_delta": 12 if dominance != "aggressive" else 16,
                        "aggressiveness_delta": 3 if dominance in {"aggressive", "cold"} else 1,
                        "attraction_delta": -5,
                    },
                    "reputation": {"Habitants": -1},
                },
            ),
        ]
        if corruption >= 70:
            plan.options[1] = ChoiceOption(
                id="pacte_sombre",
                text="Accepter un pacte ambigu",
                risk_tag="eleve",
                effects_hint="Gain rapide, cout moral a moyen terme.",
                state_patch={
                    "flags": {"npc_dark_pact": True},
                    "npc": {"tension_delta": -4, "corruption_delta": 6, "attraction_delta": 2},
                    "player": {"corruption_delta": 9},
                    "reputation": {"Habitants": -1, "Marchands": 1},
                },
            )
        plan.choices = list(plan.options)
        if not plan.narration_hooks:
            plan.narration_hooks = ["Une proposition voilee transforme la discussion en choix stratégique."]
        return plan

    def _maybe_generate_micro_event(self, *, npc_profile: dict | None, user_text: str, plan: Plan) -> dict | None:
        tension = profile_tension_level(npc_profile) if isinstance(npc_profile, dict) else 0
        important_choice = bool(plan.output_type == "choice_required" and plan.options)
        trigger = self.rng.random() < 0.08 or tension > 70 or important_choice
        if not trigger:
            return None

        templates = [
            {
                "text": "Narration système: Un messager essoufflé interrompt brièvement l'échange.",
                "patch": {"flags": {"event_messenger_arrived": True}, "npc": {"tension_delta": 3}},
                "interrupt": False,
            },
            {
                "text": "Narration système: Un bruit sec éclate dehors, les regards se tournent vers la porte.",
                "patch": {"flags": {"event_noise_outside": True}, "npc": {"tension_delta": 2}},
                "interrupt": False,
            },
            {
                "text": "Narration système: Un témoin discret écoute la conversation à distance.",
                "patch": {"flags": {"event_witness_listening": True}, "npc": {"tension_delta": 5}},
                "interrupt": False,
            },
            {
                "text": "Narration système: Un garde passe et ralentit en observant la scène.",
                "patch": {"flags": {"event_guard_patrol": True}, "npc": {"tension_delta": 7}},
                "interrupt": tension > 85,
            },
        ]

        selected = templates[self.rng.randrange(len(templates))]
        return {
            "text": selected["text"],
            "state_patch": selected["patch"],
            "interrupt": bool(selected["interrupt"]),
        }

    def _consume_pending_events(self, *, state: dict, npc_profile: dict | None, npc_name: str) -> list[str]:
        if not self._pending_events:
            return []
        if not isinstance(npc_profile, dict):
            self._pending_events.clear()
            return []

        lines: list[str] = []
        selected_key = self._selected_npc_key(state)
        events = list(self._pending_events)
        self._pending_events.clear()

        for event in events:
            if isinstance(event, OnTradeCompleted):
                if selected_key and event.npc_key and selected_key != event.npc_key:
                    continue
                delta = -5 if event.action in {"buy", "sell"} else -2
                old, new = apply_tension_delta(npc_profile, delta=delta, reason="trade_completed")
                if old != new:
                    lines.append(f"Tension {tension_tier_label(old)} -> {tension_tier_label(new)} apres transaction.")
            elif isinstance(event, OnQuestUpdated):
                if selected_key and event.source_npc_key and selected_key != event.source_npc_key:
                    continue
                if event.status == "completed":
                    old, new = apply_tension_delta(npc_profile, delta=-6, reason="quest_completed")
                    if old != new:
                        lines.append("Le succes de quete detend l'atmosphere.")
                elif event.status == "failed":
                    old, new = apply_tension_delta(npc_profile, delta=8, reason="quest_failed")
                    if old != new:
                        lines.append("L'echec de quete crispe visiblement le PNJ.")
            elif isinstance(event, OnLocationEntered):
                ctx = event.context if isinstance(event.context, dict) else {}
                scene_npc = str(ctx.get("selected_npc") or "").strip()
                if scene_npc and self._norm_token(scene_npc) != self._norm_token(npc_name):
                    continue
                old, new = apply_tension_delta(npc_profile, delta=-2, reason="location_entered")
                if old != new:
                    lines.append("Le changement de lieu apaise legerement la tension.")
        return lines

    def _on_trade_completed(self, event: OnTradeCompleted) -> None:
        self._pending_events.append(event)

    def _on_quest_updated(self, event: OnQuestUpdated) -> None:
        self._pending_events.append(event)

    def _on_location_entered(self, event: OnLocationEntered) -> None:
        self._pending_events.append(event)

    def _fallback_models(self, primary_model: str) -> list[str]:
        ordered = [
            MODEL_NAME["mistral"],
            MODEL_NAME["qwen"],
            MODEL_NAME["dolphin"],
        ]
        out: list[str] = []
        for candidate in ordered:
            if candidate == primary_model or candidate in out:
                continue
            out.append(candidate)
        return out

    def _resolve_dialogue_target(
        self,
        *,
        selected_npc: str,
        plan_target: str | None,
        user_text: str,
        state: dict,
    ) -> str | None:
        # En discussion PNJ, la sélection UI est la source de vérité:
        # on évite qu'un target halluciné (ex: "Joueur") inverse les rôles.
        if selected_npc:
            return selected_npc

        mention_target = detect_target(
            user_text,
            preferred_names=self._target_mention_candidates(state),
        )
        candidates = [
            str(mention_target or "").strip(),
            str(plan_target or "").strip(),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            if self._is_forbidden_dialogue_target(candidate, state):
                continue
            return candidate
        return None

    def _target_mention_candidates(self, state: dict) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        def _add(value: object) -> None:
            name = re.sub(r"\s+", " ", str(value or "").strip())
            if not name:
                return
            key = name.casefold()
            if key in seen:
                return
            seen.add(key)
            out.append(name)

        scene_npcs = state.get("scene_npcs")
        if isinstance(scene_npcs, list):
            for raw in scene_npcs:
                _add(raw)

        selected_npc = str(state.get("selected_npc") or "").strip()
        if selected_npc:
            _add(selected_npc)

        selected_profile = state.get("selected_npc_profile")
        if isinstance(selected_profile, dict):
            _add(selected_profile.get("label"))
            _add(selected_profile.get("role"))
            identity = selected_profile.get("identity", {})
            if isinstance(identity, dict):
                first = str(identity.get("first_name") or "").strip()
                last = str(identity.get("last_name") or "").strip()
                full_name = " ".join(part for part in (first, last) if part).strip()
                _add(full_name)
                _add(identity.get("alias"))

        profiles = state.get("npc_profiles")
        location_norm = self._norm_token(str(state.get("location_id") or ""))
        if isinstance(profiles, dict):
            for profile in profiles.values():
                if not isinstance(profile, dict):
                    continue
                if location_norm:
                    world_anchor = profile.get("world_anchor", {})
                    if isinstance(world_anchor, dict):
                        anchor_loc = self._norm_token(str(world_anchor.get("location_id") or ""))
                        if anchor_loc and anchor_loc != location_norm:
                            continue
                _add(profile.get("label"))
                _add(profile.get("role"))
                identity = profile.get("identity", {})
                if isinstance(identity, dict):
                    first = str(identity.get("first_name") or "").strip()
                    last = str(identity.get("last_name") or "").strip()
                    full_name = " ".join(part for part in (first, last) if part).strip()
                    _add(full_name)
                    _add(identity.get("alias"))

        out.sort(key=len, reverse=True)
        return out[:64]

    def _sanitize_user_text_for_dialogue(self, user_text: str, selected_npc: str) -> str:
        text = str(user_text or "").strip()
        if not text:
            return ""
        if not selected_npc or not text.startswith("@"):
            return text

        escaped = re.escape(selected_npc.strip())
        # Retire le préfixe éventuel "@PNJ " ajouté par l'UI pour ne pas polluer le prompt.
        m = re.match(rf"^\s*@\s*{escaped}(?:\s+|$)(.*)$", text, flags=re.IGNORECASE)
        if m:
            cleaned = (m.group(1) or "").strip()
            return cleaned or text

        # Fallback: enlève seulement le premier token @xxx.
        m = re.match(r"^\s*@[\wÀ-ÖØ-öø-ÿ'_-]+\s*(.*)$", text)
        if m:
            cleaned = (m.group(1) or "").strip()
            return cleaned or text
        return text

    def _is_forbidden_dialogue_target(self, target: str, state: dict) -> bool:
        norm = self._norm_token(target)
        if not norm:
            return True

        blocked = {
            "joueur",
            "player",
            "systeme",
            "système",
            "system",
            "narrateur",
            "narration",
            "assistant",
            "ia",
            "toi",
            "moi",
            "je",
            "tu",
            "vous",
        }
        if norm in blocked:
            return True

        player_name = self._norm_token(str(state.get("player_name") or ""))
        if player_name and norm == player_name:
            return True
        return False

    def _dialogue_identity_candidates(self, target: str, npc_profile: dict | None) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def _add(value: object) -> None:
            label = re.sub(r"\s+", " ", str(value or "").strip())
            if not label:
                return
            key = self._norm_token(label)
            if not key or key in seen:
                return
            seen.add(key)
            names.append(label)

        _add(target)
        if isinstance(npc_profile, dict):
            _add(npc_profile.get("label"))
            _add(npc_profile.get("role"))
            identity = npc_profile.get("identity", {})
            if isinstance(identity, dict):
                first = str(identity.get("first_name") or "").strip()
                last = str(identity.get("last_name") or "").strip()
                full_name = " ".join(part for part in (first, last) if part).strip()
                _add(full_name)
                _add(identity.get("alias"))

        names.sort(key=len, reverse=True)
        return names

    def _sanitize_dialogue_self_addressing(
        self,
        dialogue_text: str,
        *,
        player_name: str,
        identity_names: list[str],
    ) -> str:
        text = str(dialogue_text or "").strip()
        player = re.sub(r"\s+", " ", str(player_name or "").strip())
        if not text or not player:
            return text

        player_norm = self._norm_token(player)
        if not player_norm:
            return text

        fixed = text
        for raw_name in identity_names:
            name = re.sub(r"\s+", " ", str(raw_name or "").strip())
            if not name:
                continue
            name_norm = self._norm_token(name)
            if not name_norm or name_norm == player_norm:
                continue
            pattern = re.compile(
                rf"(?<![\wÀ-ÖØ-öø-ÿ]){re.escape(name)}(?![\wÀ-ÖØ-öø-ÿ])(?P<sep>\s*,?\s*)(?=(tu|vous|t['’]|te|toi|ton|ta|tes)\b)",
                flags=re.IGNORECASE,
            )
            fixed = pattern.sub(lambda m: f"{player}{m.group('sep')}", fixed)

        return fixed

    def _npc_reply_history(self, state: dict, npc_name: str, *, ensure: bool) -> list[str]:
        if not isinstance(state, dict):
            return []

        flags = state.get("flags")
        if not isinstance(flags, dict):
            if not ensure:
                return []
            state["flags"] = {}
            flags = state["flags"]

        storage = flags.get("npc_recent_replies")
        if not isinstance(storage, dict):
            if not ensure:
                return []
            storage = {}
            flags["npc_recent_replies"] = storage

        key = self._norm_token(npc_name) or "npc"
        rows = storage.get(key)
        if not isinstance(rows, list):
            if ensure:
                storage[key] = []
            return []

        out: list[str] = []
        for row in rows[-8:]:
            text = re.sub(r"\s+", " ", str(row or "").strip())
            if text:
                out.append(text[:260])
        if ensure:
            storage[key] = out
        return out

    def _dialogue_similarity(self, a: str, b: str) -> float:
        left = self._norm_token(a)
        right = self._norm_token(b)
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        return difflib.SequenceMatcher(a=left, b=right).ratio()

    def _ensure_dialogue_variety(self, *, state: dict, npc_name: str, dialogue_text: str) -> str:
        text = re.sub(r"\s+", " ", str(dialogue_text or "").strip())
        if not text:
            return text

        recent = self._npc_reply_history(state, npc_name, ensure=False)
        if not recent:
            return text

        last = recent[-1]
        if self._dialogue_similarity(text, last) < 0.90:
            return text

        suffixes = [
            "Retiens ce point: le contexte a bouge.",
            "Je le redis autrement: le contexte n'est plus stable.",
            "Ne me force pas a me repeter encore.",
            "C'est la meme verite, mais la pression monte.",
        ]
        selector = "|".join((self._norm_token(npc_name), self._norm_token(text), str(len(recent))))
        digest = hashlib.blake2s(selector.encode("utf-8"), digest_size=4).digest()
        idx = int.from_bytes(digest, "big") % len(suffixes)
        base = text.rstrip(" .!?")
        candidate = f"{base}. {suffixes[idx]}"
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if self._dialogue_similarity(candidate, last) >= 0.92:
            candidate = f"{suffixes[idx]}"
        return candidate[:260]

    def _remember_dialogue_reply(self, *, state: dict, npc_name: str, dialogue_text: str) -> None:
        text = re.sub(r"\s+", " ", str(dialogue_text or "").strip())
        if not text:
            return
        history = self._npc_reply_history(state, npc_name, ensure=True)
        if history and self._dialogue_similarity(history[-1], text) >= 0.995:
            return
        history.append(text[:260])
        trimmed = history[-6:]

        flags = state.get("flags")
        if not isinstance(flags, dict):
            state["flags"] = {}
            flags = state["flags"]
        storage = flags.get("npc_recent_replies")
        if not isinstance(storage, dict):
            storage = {}
            flags["npc_recent_replies"] = storage
        storage[self._norm_token(npc_name) or "npc"] = trimmed

    def _sanitize_narration_text(self, text: str, hooks: list[str]) -> str:
        """
        Garde-fou: retire les formats de dialogue direct qui ne doivent pas
        apparaître dans la narration du panneau de droite.
        """
        t = (text or "").strip()
        if not t:
            return hooks[0] if hooks else "Le silence retombe sur la scène."

        # Supprime les segments entre guillemets typiques de répliques directes.
        t = re.sub(r"«[^»\n]{0,300}»", "", t)
        t = re.sub(r"“[^”\n]{0,300}”", "", t)
        t = re.sub(r'"[^"\n]{0,300}"', "", t)

        kept_lines: list[str] = []
        for raw_line in t.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Tiret de dialogue.
            if re.match(r"^[-—–]\s+", line):
                continue
            # Préfixe de type "Nom: ..."
            if re.match(r"^[A-ZÀ-ÖØ-Ý][\wÀ-ÖØ-öø-ÿ' -]{0,30}\s*:\s+", line):
                continue

            kept_lines.append(line)

        cleaned = " ".join(kept_lines)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        if not cleaned:
            return hooks[0] if hooks else "Un souffle froid traverse la scène."
        return cleaned

    def _is_training_message(self, text: str) -> bool:
        raw = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
        plain = re.sub(r"\s+", " ", raw).strip()
        if not plain:
            return False
        return bool(
            re.search(
                r"\b(entraine|entrainer|entrainement|pratique|pratiquer|exerce|exercer|drill|sparring|combo)\b",
                plain,
            )
        )

    def _limit_narration_sentences(
        self,
        text: str,
        *,
        max_sentences: int,
        hooks: list[str],
        max_chars: int | None = None,
    ) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if not cleaned:
            return hooks[0] if hooks else "Le silence retombe sur la scène."

        chunks = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
        if not chunks:
            return hooks[0] if hooks else "Le silence retombe sur la scène."

        limited = " ".join(chunks[: max(1, int(max_sentences))]).strip()
        if max_chars is not None and max_chars > 0 and len(limited) > int(max_chars):
            truncated = limited[: int(max_chars)].rstrip()
            last_space = truncated.rfind(" ")
            if last_space >= 80:
                truncated = truncated[:last_space].rstrip()
            limited = truncated.rstrip(" ,;:") + "."
        return limited or (hooks[0] if hooks else "Le silence retombe sur la scène.")

    def _find_npc_profile(self, profiles: object, target: str, location_id: object = None) -> dict | None:
        if not isinstance(profiles, dict) or not target:
            return None

        target_norm = self._norm_token(target)
        loc_norm = self._norm_token(str(location_id or ""))
        candidates: list[dict] = []

        for key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            if loc_norm:
                world_anchor = profile.get("world_anchor", {})
                if isinstance(world_anchor, dict):
                    anchor_loc = self._norm_token(str(world_anchor.get("location_id") or ""))
                    if anchor_loc and anchor_loc != loc_norm:
                        continue

            matched = False
            if self._norm_token(str(key)) == target_norm:
                matched = True

            identity = profile.get("identity", {})
            if isinstance(identity, dict):
                first = str(identity.get("first_name") or "").strip()
                last = str(identity.get("last_name") or "").strip()
                full_name = " ".join(part for part in (first, last) if part)
                if self._norm_token(full_name) == target_norm:
                    matched = True
                if self._norm_token(str(identity.get("alias") or "")) == target_norm:
                    matched = True
            label = str(profile.get("label") or "").strip()
            if self._norm_token(label) == target_norm:
                matched = True
            role = str(profile.get("role") or "").strip()
            if self._norm_token(role) == target_norm:
                matched = True

            if matched:
                candidates.append(profile)

        return candidates[0] if candidates else None

    def _norm_token(self, value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip()).casefold()

    def _sanitize_dice_expr(self, expr: str) -> str:
        """
        Autorise uniquement:
          - d20
          - 2d6
          - d20+2 / 2d6-1
        Si l'IA renvoie "d20+persuasion" ou "d20 + STR", on retire le texte et on garde d20 (ou NdM).
        """
        e = (expr or "").strip().replace(" ", "")
        if not e:
            return "d20"

        # match strict NdM(+/-X)
        m = re.match(r"^(\d*d\d+)([+-]\d+)?$", e, flags=re.IGNORECASE)
        if m:
            return e.lower()

        # essaye de récupérer juste "NdM" au début
        m2 = re.match(r"^(\d*d\d+)", e, flags=re.IGNORECASE)
        if m2:
            base = m2.group(1).lower()
            # cherche un mod numérique explicite après
            m3 = re.search(r"([+-])(\d+)", e)
            if m3:
                return f"{base}{m3.group(1)}{m3.group(2)}"
            return base

        return "d20"

    async def _get_plan(self, canon: str) -> Plan:
        prompt = prompt_rules_json(canon)
        rules_model = model_for("rules")
        raw = await self.llm.generate(
            model=rules_model,
            prompt=prompt,
            temperature=0.2,
            num_ctx=2048,
            num_predict=260,
            stop=None,
            fallback_models=self._fallback_models(rules_model),
        )

        # Mistral peut parfois renvoyer du texte autour: on tente d'extraire le JSON brut
        json_str = self._extract_json(raw)
        try:
            data = json.loads(json_str)
            return Plan.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            # fallback safe: idle plan
            return Plan(type="idle", intent="fallback", narration_hooks=["Le destin hésite, mais le monde demeure cohérent."])

    def _extract_json(self, s: str) -> str:
        s = (s or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start:end + 1]
        return "{}"
