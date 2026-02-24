from __future__ import annotations

import asyncio
import json

from app.gamemaster.gamemaster import GameMaster


class _DummyLLM:
    async def generate(self, **kwargs) -> str:
        prompt = str(kwargs.get("prompt") or "")
        if "moteur de règles" in prompt:
            return json.dumps(
                {
                    "type": "talk",
                    "target": "Mirelle",
                    "intent": "discussion",
                    "rolls": [],
                    "narration_hooks": ["La scene reste instable."],
                    "state_patch": {},
                    "output_type": "dialogue",
                    "options": [],
                },
                ensure_ascii=False,
            )
        if "Tu joues le rôle" in prompt:
            return "Je te regarde sans bouger."
        return "Un courant d'air traverse la piece."


def _base_state_with_npc(*, tension: int, decision_mode_v2: bool = True) -> dict:
    return {
        "location": "City",
        "location_id": "city",
        "map_anchor": "Lumeria",
        "flags": {"decision_mode_v2": decision_mode_v2},
        "player_name": "Sephy",
        "player_gold": 20,
        "inventory_summary": "vide",
        "world_time_minutes": 120,
        "selected_npc": "Mirelle",
        "selected_npc_key": "city__mirelle",
        "scene_npcs": ["Mirelle"],
        "faction_reputation_summary": "aucune",
        "conversation_short_term": "",
        "conversation_long_term": "",
        "conversation_global_memory": "",
        "npc_profiles": {
            "city__mirelle": {
                "npc_key": "city__mirelle",
                "label": "Mirelle",
                "role": "Geoliere",
                "world_anchor": {"location_id": "city", "location_title": "City"},
                "identity": {
                    "first_name": "Mirelle",
                    "last_name": "Korr",
                    "gender": "femme",
                    "species": "humain",
                    "origin": "City",
                },
                "agenda_secret": "Recuperer un avantage discret sur la garde locale.",
                "besoin": "Garder le controle de sa cellule.",
                "peur": "Perdre son autorite.",
                "rival_id": "caporal_brant",
                "traits": ["pragmatique", "froide", "lucide"],
                "tension_level": tension,
                "truth_state": {
                    "known_secrets": [],
                    "active_lies": [],
                    "mensonge_actif": {},
                    "last_reveal_at": "",
                    "blacklist_until_minutes": 0,
                },
            }
        },
    }


def test_tension_increases_on_threat() -> None:
    gm = GameMaster(_DummyLLM(), seed=2)
    state = _base_state_with_npc(tension=20, decision_mode_v2=False)

    _ = asyncio.run(gm.play_turn(state, "Si tu refuses, je te menace clairement."))

    profile = state["npc_profiles"]["city__mirelle"]
    assert int(profile.get("tension_level") or 0) > 20


def test_tension_decreases_on_helpful_message() -> None:
    gm = GameMaster(_DummyLLM(), seed=2)
    state = _base_state_with_npc(tension=40, decision_mode_v2=False)

    _ = asyncio.run(gm.play_turn(state, "Je t'aide et je te paie, merci pour ton soutien."))

    profile = state["npc_profiles"]["city__mirelle"]
    assert int(profile.get("tension_level") or 0) < 40


def test_agenda_opportunity_produces_choice_required() -> None:
    gm = GameMaster(_DummyLLM(), seed=2)
    state = _base_state_with_npc(tension=30)

    result = asyncio.run(gm.play_turn(state, "J'accepte de faire un service discret, propose-moi un deal."))

    assert result.output_type == "choice_required"
    assert 1 <= len(result.options) <= 3


def test_corruption_and_attraction_evolve_from_ambiguous_social_turn() -> None:
    gm = GameMaster(_DummyLLM(), seed=2)
    state = _base_state_with_npc(tension=35, decision_mode_v2=False)

    _ = asyncio.run(gm.play_turn(state, "Je propose un arrangement discret et je te flatte."))

    assert int(state.get("player_corruption_level") or 0) > 0
    profile = state["npc_profiles"]["city__mirelle"]
    attraction_map = profile.get("attraction_map")
    assert isinstance(attraction_map, dict)
    assert int(attraction_map.get("Sephy") or 0) > 0
