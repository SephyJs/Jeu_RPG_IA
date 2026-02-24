from app.gamemaster.telegram_ataryxia_core import (
    ensure_user_anchor,
    TELEGRAM_ATARYXIA_MODE_FLAG,
    TELEGRAM_ATARYXIA_NPC_KEY,
    fallback_non_repetitive_reply,
    fallback_non_repetitive_reply_seeded,
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


def test_telegram_ataryxia_mode_detection() -> None:
    state = {
        "selected_npc": "Ataryxia",
        "selected_npc_key": TELEGRAM_ATARYXIA_NPC_KEY,
        "flags": {TELEGRAM_ATARYXIA_MODE_FLAG: True},
    }
    assert is_telegram_ataryxia_mode(state) is True

    state["flags"][TELEGRAM_ATARYXIA_MODE_FLAG] = False
    assert is_telegram_ataryxia_mode(state) is False


def test_recent_replies_memory_and_dedup() -> None:
    state: dict = {"flags": {}}
    remember_reply(state, "Ataryxia: Je t'entends.")
    remember_reply(state, "Ataryxia : Je t'entends.")
    remember_reply(state, "Je veux du concret.")

    recent = get_recent_replies(state, max_items=6)
    assert len(recent) == 2
    assert "Je veux du concret." in recent[-1]


def test_repetition_detection_and_fallback() -> None:
    recent = [
        "Je t'entends. Va plus loin et dis ce que tu veux vraiment.",
        "Ne me donne pas une facade. Donne-moi ton intention brute.",
    ]
    assert is_repetitive_reply("Je t'entends. Va plus loin et dis ce que tu veux vraiment.", recent) is True
    fallback = fallback_non_repetitive_reply("On continue.", recent)
    assert fallback
    assert is_repetitive_reply(fallback, recent) is False


def test_strip_speaker_prefix() -> None:
    text = strip_speaker_prefix("Ataryxia : Ataryxia: Je suis la.")
    assert text == "Je suis la."


def test_meta_or_restrictive_reply_detection() -> None:
    assert is_meta_or_restrictive_reply("Je suis narratrice de jeu, c'est mon metier.") is False
    assert is_meta_or_restrictive_reply("Je suis une IA, je ne peux pas.") is True
    assert is_meta_or_restrictive_reply("Je te regarde droit dans les yeux, continue.") is False


def test_game_framing_reply_detection() -> None:
    assert is_game_framing_reply("Je te propose une quete, aventurier.") is True
    assert is_game_framing_reply("On discute en prive, juste toi et moi.") is False
    assert is_game_framing_reply("Je te propose une quete, aventurier.", allow_work_topic=True) is False


def test_work_topic_message_detection() -> None:
    assert is_work_topic_message("Tu fais quoi comme metier ?") is True
    assert is_work_topic_message("Tu es narratrice de jeu ?") is True
    assert is_work_topic_message("Tu penses a moi ce soir ?") is False


def test_format_sms_reply_limits_block_size() -> None:
    raw = (
        "Je vais te raconter quelque chose de tres long et detaille qui n'a pas besoin de tenir en un seul message. "
        "On peut faire beaucoup plus simple, plus direct, et garder juste l'essentiel pour une conversation sms. "
        "J'ajoute encore une phrase pour verifier la coupe propre."
    )
    out = format_sms_reply(raw, max_lines=3, max_chars=180, max_line_chars=70)
    lines = out.splitlines()
    assert 1 <= len(lines) <= 3
    assert len(out) <= 180
    assert all(len(line) <= 70 for line in lines)
    assert "…" not in out


def test_format_sms_reply_does_not_split_on_comma() -> None:
    raw = 'Pour "travail", ca va. Et toi ?'
    out = format_sms_reply(raw, max_lines=4, max_chars=200, max_line_chars=120)
    assert '"travail",\n' not in out
    assert 'Pour "travail", ca va.' in out


def test_format_sms_reply_merges_short_sentences_on_one_line() -> None:
    out = format_sms_reply("Ca va. Et toi ?", max_lines=4, max_chars=200, max_line_chars=120)
    assert out == "Ca va. Et toi ?"


def test_ensure_user_anchor_keeps_reply_natural() -> None:
    out = ensure_user_anchor("Je t'ecoute.", "On parle de confiance et de limites", turn_seed=2)
    assert out == "Je t'ecoute."


def test_ensure_user_anchor_keeps_small_talk_natural() -> None:
    out = ensure_user_anchor("Ca va. Et toi ?", "Ton travail ca va aussi ?", turn_seed=0)
    assert out == "Ca va. Et toi ?"


def test_question_unanswered_detection() -> None:
    assert is_question_unanswered_reply("Je pense a la lune et au vent.", "Ton travail ca va ?") is True
    assert is_question_unanswered_reply("Oui, le travail va bien. Et toi ?", "Ton travail ca va ?") is False


def test_seeded_fallback_varies_with_turn_seed() -> None:
    recent: list[str] = []
    a = fallback_non_repetitive_reply_seeded("On parle de confiance.", recent, turn_seed=1)
    recent.append(a)
    b = fallback_non_repetitive_reply_seeded("On parle de confiance.", recent, turn_seed=2)
    assert a != b


def test_repetitive_reply_detects_prefixed_copy() -> None:
    recent = ["Ah, vraiment? Que c'est bon d'entendre ca, Sephy."]
    candidate = 'A propos de "travail", Ah, vraiment? Que c\'est bon d\'entendre ca, Sephy.'
    assert is_repetitive_reply(candidate, recent) is True


def test_poetic_nature_reply_detection() -> None:
    assert is_poetic_nature_reply("Le vent, la foret et la lune me suivent.", "Ca va ?") is True
    assert is_poetic_nature_reply("Le vent dans la foret me suit.", "On parle de foret ce soir ?") is False


def test_fallback_question_about_work_is_direct() -> None:
    out = fallback_non_repetitive_reply_seeded("Ton travail ca va ?", recent_replies=[], turn_seed=4)
    lower = out.casefold()
    assert "travail" in lower
    assert "et toi" in lower
