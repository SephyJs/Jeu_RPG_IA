from app.gamemaster.prompts import build_canon_summary, prompt_telegram_ataryxia_dialogue


def test_canon_summary_marks_beginner_profile() -> None:
    state = {
        "location": "Forge",
        "location_id": "forge_01",
        "map_anchor": "Lumeria",
        "flags": {},
        "player_name": "Sephy",
        "player_gold": 10,
        "inventory_summary": "vide",
        "player_level": 1,
        "player_skills": [{"skill_id": "soin_base", "name": "Soin de base", "level": 1}],
        "player_skill_count": 1,
        "equipped_items": {"weapon": ""},
        "world_time_minutes": 0,
        "conversation_short_term": "",
        "conversation_long_term": "",
        "conversation_global_memory": "",
    }

    summary = build_canon_summary(state, "je veux aller au donjon sans arme")

    assert "NiveauJoueur: 1" in summary
    assert "ProfilExperienceJoueur: debutant" in summary
    assert "ArmeEquipee: (aucune)" in summary


def test_canon_summary_marks_advanced_profile() -> None:
    state = {
        "location": "Temple",
        "location_id": "temple_01",
        "map_anchor": "Lumeria",
        "flags": {},
        "player_name": "Sephy",
        "player_gold": 350,
        "inventory_summary": "Epee runique x1",
        "player_level": 8,
        "player_skills": [
            {"skill_id": f"skill_{i}", "name": f"Skill {i}", "level": 3}
            for i in range(8)
        ],
        "player_skill_count": 8,
        "player_weapon_equipped": "epee_runique",
        "world_time_minutes": 0,
        "conversation_short_term": "",
        "conversation_long_term": "",
        "conversation_global_memory": "",
    }

    summary = build_canon_summary(state, "on continue")

    assert "NiveauJoueur: 8" in summary
    assert "ProfilExperienceJoueur: avance" in summary
    assert "ArmeEquipee: epee_runique" in summary


def test_canon_summary_includes_adult_mode_flag() -> None:
    state = {
        "location": "Village",
        "location_id": "village_center_01",
        "map_anchor": "Lumeria",
        "flags": {"nsfw_enabled": True},
        "player_name": "Sephy",
        "player_gold": 0,
        "inventory_summary": "vide",
        "player_level": 1,
        "player_skills": [],
        "player_skill_count": 0,
        "world_time_minutes": 0,
        "conversation_short_term": "",
        "conversation_long_term": "",
        "conversation_global_memory": "",
    }

    summary = build_canon_summary(state, "on continue")

    assert "ModeAdulte: on" in summary


def test_canon_summary_includes_reputation_summary() -> None:
    state = {
        "location": "Village",
        "location_id": "village_center_01",
        "map_anchor": "Lumeria",
        "flags": {},
        "player_name": "Sephy",
        "player_gold": 0,
        "inventory_summary": "vide",
        "player_level": 1,
        "player_skills": [],
        "player_skill_count": 0,
        "faction_reputation_summary": "Marchands:+5 | Peuple:-2",
        "world_time_minutes": 0,
        "conversation_short_term": "",
        "conversation_long_term": "",
        "conversation_global_memory": "",
    }

    summary = build_canon_summary(state, "on continue")

    assert "ReputationFactions: Marchands:+5 | Peuple:-2" in summary


def test_prompt_telegram_enforces_direct_sms_answer() -> None:
    prompt = prompt_telegram_ataryxia_dialogue(
        canon="Canal: Telegram SMS prive",
        user_text="Ton travail ca va ?",
        player_name="Sephy",
        recent_replies=[],
        npc_profile=None,
        freeform_mode=False,
        work_topic_mode=True,
        last_reply="",
    )
    lowered = prompt.casefold()
    assert "reponds d'abord directement" in lowered
    assert "pas de metaphore poetique" in lowered
    assert "max 3 lignes" in lowered
