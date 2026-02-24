from app.gamemaster.gamemaster import GameMaster


def test_training_message_detection() -> None:
    gm = GameMaster(None)
    assert gm._is_training_message("Je m'entraine a l'epee.")
    assert gm._is_training_message("On fait du sparring au camp.")
    assert not gm._is_training_message("Je vais acheter une potion.")


def test_limit_narration_to_two_sentences() -> None:
    gm = GameMaster(None)
    text = "La pluie frappe les toits. Le metal chante sous les coups. Les passants se figent en silence."
    out = gm._limit_narration_sentences(text, max_sentences=2, hooks=[])
    assert out == "La pluie frappe les toits. Le metal chante sous les coups."


def test_limit_narration_fallback_when_empty() -> None:
    gm = GameMaster(None)
    out = gm._limit_narration_sentences("", max_sentences=2, hooks=["Le vent tombe."])
    assert out == "Le vent tombe."


def test_dialogue_self_addressing_is_rewritten_to_player_name() -> None:
    gm = GameMaster(None)
    out = gm._sanitize_dialogue_self_addressing(
        "Dis-moi, Mirelle Korr, tu n'as vraiment rien a dire ?",
        player_name="Sephy",
        identity_names=["Mirelle Korr", "Geolier"],
    )
    assert out == "Dis-moi, Sephy, tu n'as vraiment rien a dire ?"


def test_dialogue_keeps_self_introduction_unchanged() -> None:
    gm = GameMaster(None)
    out = gm._sanitize_dialogue_self_addressing(
        "Je suis Mirelle Korr, geolier de ce couloir.",
        player_name="Sephy",
        identity_names=["Mirelle Korr", "Geolier"],
    )
    assert out == "Je suis Mirelle Korr, geolier de ce couloir."


def test_dialogue_variety_rewrites_repeated_reply() -> None:
    gm = GameMaster(None)
    state = {"flags": {"npc_recent_replies": {"mirelle": ["Je te regarde sans bouger."]}}}
    out = gm._ensure_dialogue_variety(
        state=state,
        npc_name="Mirelle",
        dialogue_text="Je te regarde sans bouger.",
    )
    assert out != "Je te regarde sans bouger."
    assert "repeter" in out.casefold() or "contexte" in out.casefold() or "verite" in out.casefold()


def test_dialogue_history_is_capped() -> None:
    gm = GameMaster(None)
    state: dict = {"flags": {}}
    for idx in range(10):
        gm._remember_dialogue_reply(
            state=state,
            npc_name="Mirelle",
            dialogue_text=f"Ligne {idx}",
        )
    history = state["flags"]["npc_recent_replies"]["mirelle"]
    assert len(history) == 6
    assert history[0] == "Ligne 4"
    assert history[-1] == "Ligne 9"
