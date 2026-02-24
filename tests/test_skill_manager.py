from app.gamemaster.skill_manager import SkillManager


def test_extract_intent_hints_detects_sword_training(tmp_path) -> None:
    manager = SkillManager(None, data_path=str(tmp_path / "skills_catalog.json"))
    intents = manager.extract_intent_hints("Je veux m'entrainer a l'epee avant le donjon.")
    assert "escrime" in intents


def test_extract_intent_hints_keeps_object_after_a(tmp_path) -> None:
    manager = SkillManager(None, data_path=str(tmp_path / "skills_catalog.json"))
    intents = manager.extract_intent_hints("Je m'entraine au bouclier tous les jours.")
    assert any(intent.startswith("bouclier") for intent in intents)


def test_extract_intent_hints_ignores_training_verb_as_intent(tmp_path) -> None:
    manager = SkillManager(None, data_path=str(tmp_path / "skills_catalog.json"))
    intents = manager.extract_intent_hints("Je m'entrainer encore.")
    assert "entrainer" not in intents
    assert "entraine" not in intents


def test_extract_intent_hints_ignores_enemy_target_words(tmp_path) -> None:
    manager = SkillManager(None, data_path=str(tmp_path / "skills_catalog.json"))
    intents = manager.extract_intent_hints("J'attaque l'ennemi puis je vise la cible.")
    assert "ennemi" not in intents
    assert "cible" not in intents


def test_generated_skill_replaces_generic_name(tmp_path) -> None:
    manager = SkillManager(None, data_path=str(tmp_path / "skills_catalog.json"))
    catalog = manager.load_catalog()
    skill = manager._build_generated_skill(  # noqa: SLF001 - targeted unit test
        raw={
            "name": "Nouveau Sort",
            "category": "magie",
            "description": "Sort d'entrainement.",
            "difficulty": 2,
            "primary_stats": ["defense", "sagesse"],
            "trainer_roles": ["clerc"],
            "effects": ["bonus_resistance"],
        },
        catalog=catalog,
        player_stats={"defense": 8, "sagesse": 7, "magie": 6},
        npc_role="clerc soignant",
        training_context="j'aimerais apprendre un sort de defense et de resistance",
        npc_name="Ronan Ardent",
    )
    assert manager._norm(skill.name) != "nouveau sort"  # noqa: SLF001 - targeted unit test
