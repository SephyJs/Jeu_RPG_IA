from __future__ import annotations

import json
import random
import re
from pydantic import ValidationError

from .ollama_client import OllamaClient
from .models import MODEL_NAME, model_for
from .debug_commands import parse_debug_command
from .router import detect_target
from .schemas import Plan, RollResult, TurnResult
from .dice import roll as roll_dice
from .state_patch import apply_patch
from .prompts import (
    build_canon_summary,
    prompt_rules_json,
    prompt_dialogue,
    prompt_narration,
)


class GameMaster:
    def __init__(self, llm: OllamaClient, *, seed: int | None = None):
        self.llm = llm
        self.debug_enabled = False
        self.debug_forced: str | None = None  # "mistral"|"qwen"|"dolphin"|None
        self.rng = random.Random(seed)

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
        canon = build_canon_summary(state, user_text)
        selected_npc = str(state.get("selected_npc") or "").strip()

        # 1) RULES: plan JSON (mistral)
        plan = await self._get_plan(canon)

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
        target = plan.target or detect_target(user_text)
        if not target and selected_npc:
            target = selected_npc
        dialogue_text = None
        speaker = None

        if target:
            speaker = target
            npc_profile = self._find_npc_profile(state.get("npc_profiles"), target, state.get("location_id"))
            if not npc_profile and selected_npc and self._norm_token(target) == self._norm_token(selected_npc):
                selected_profile = state.get("selected_npc_profile")
                if isinstance(selected_profile, dict):
                    npc_profile = selected_profile
            dialogue_prompt = prompt_dialogue(target, canon, user_text, roll_summary, npc_profile=npc_profile)
            dialogue_text = await self.llm.generate(
                model=model_for("dialogue"),
                prompt=dialogue_prompt,
                temperature=0.8,
                num_ctx=2048,
                num_predict=250,
            )

        # 5) Narration: interprète l'échange joueur <-> PNJ du tour
        turn_exchange_lines = [f"Joueur: {user_text}"]
        if speaker and dialogue_text:
            turn_exchange_lines.append(f"{speaker}: {dialogue_text}")
        turn_exchange = "\n".join(turn_exchange_lines)

        narration_prompt = prompt_narration(
            canon,
            user_text,
            plan.narration_hooks,
            roll_summary,
            turn_exchange=turn_exchange,
        )
        narration_text = await self.llm.generate(
            model=model_for("narration"),
            prompt=narration_prompt,
            temperature=0.7,
            num_ctx=4096,
            num_predict=320,
        )
        narration_text = self._sanitize_narration_text(narration_text, plan.narration_hooks)

        return TurnResult(
            mode="auto",
            narration=narration_text,
            speaker=speaker,
            dialogue=dialogue_text,
            plan=plan,
            rolls=roll_results,
        )

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

        raw = await self.llm.generate(
            model=model_for("rules"),
            prompt=prompt,
            temperature=0.2,
            num_ctx=2048,
            num_predict=260,
            stop=None,
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
