from __future__ import annotations

from app.gamemaster.reputation_manager import ensure_reputation_state


STORY_CHAPTERS: list[dict] = [
    {
        "id": "awakening",
        "title": "Chapitre I - Eveil",
        "objective": "Stabiliser votre identite et entrer dans le monde.",
        "reward_gold": 10,
        "reward_skill_points": 0,
    },
    {
        "id": "first_contact",
        "title": "Chapitre II - Premiers liens",
        "objective": "Parler a un habitant influent.",
        "reward_gold": 15,
        "reward_skill_points": 1,
    },
    {
        "id": "first_contract",
        "title": "Chapitre III - Le pacte local",
        "objective": "Obtenir votre premiere quete.",
        "reward_gold": 20,
        "reward_skill_points": 1,
    },
    {
        "id": "first_delve",
        "title": "Chapitre IV - Sous la pierre",
        "objective": "Vaincre un premier etage de donjon.",
        "reward_gold": 25,
        "reward_skill_points": 1,
    },
    {
        "id": "trusted_name",
        "title": "Chapitre V - Nom reconnu",
        "objective": "Atteindre une reputation positive notable.",
        "reward_gold": 30,
        "reward_skill_points": 1,
    },
    {
        "id": "woven_paths",
        "title": "Chapitre VI - Trames croisees",
        "objective": "Terminer plusieurs quetes majeures.",
        "reward_gold": 40,
        "reward_skill_points": 2,
    },
    {
        "id": "ember_gate",
        "title": "Chapitre VII - Porte de cendre",
        "objective": "Prouver votre valeur aux Aventuriers.",
        "reward_gold": 60,
        "reward_skill_points": 2,
    },
]


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _story_store(state) -> dict:
    if not isinstance(state.gm_state, dict):
        state.gm_state = {}
    flags = state.gm_state.get("flags")
    if not isinstance(flags, dict):
        state.gm_state["flags"] = {}
        flags = state.gm_state["flags"]
    story = flags.get("main_story")
    if not isinstance(story, dict):
        story = {}
        flags["main_story"] = story
    return story


def ensure_story_state(state) -> None:
    story = _story_store(state)
    chapter_ids = [str(ch.get("id") or "") for ch in STORY_CHAPTERS]
    completed_raw = story.get("completed_ids")
    completed: list[str] = []
    if isinstance(completed_raw, list):
        for chapter_id in completed_raw:
            key = str(chapter_id or "").strip()
            if key and key in chapter_ids and key not in completed:
                completed.append(key)
    story["completed_ids"] = completed
    story["chapter_index"] = max(0, min(_safe_int(story.get("chapter_index"), 0), len(STORY_CHAPTERS)))
    if "last_completed_at" not in story:
        story["last_completed_at"] = ""


def current_story_chapter(state) -> dict | None:
    ensure_story_state(state)
    story = _story_store(state)
    index = max(0, min(_safe_int(story.get("chapter_index"), 0), len(STORY_CHAPTERS)))
    if index >= len(STORY_CHAPTERS):
        return None
    return STORY_CHAPTERS[index]


def story_status_text(state) -> str:
    ensure_story_state(state)
    story = _story_store(state)
    completed = story.get("completed_ids") if isinstance(story.get("completed_ids"), list) else []
    current = current_story_chapter(state)
    if current is None:
        return "Arc principal termine."
    title = str(current.get("title") or "Chapitre")
    objective = str(current.get("objective") or "")
    return f"{title} | objectif: {objective} | progression: {len(completed)}/{len(STORY_CHAPTERS)}"


def progress_main_story(state, *, safe_int=_safe_int, utc_now_iso=lambda: "") -> list[str]:
    ensure_story_state(state)
    ensure_reputation_state(state)
    story = _story_store(state)
    completed = story.get("completed_ids") if isinstance(story.get("completed_ids"), list) else []
    lines: list[str] = []

    while True:
        chapter = current_story_chapter(state)
        if not isinstance(chapter, dict):
            break
        chapter_id = str(chapter.get("id") or "")
        if chapter_id in completed:
            story["chapter_index"] = max(0, min(_safe_int(story.get("chapter_index"), 0) + 1, len(STORY_CHAPTERS)))
            continue
        if not _chapter_reached(state, chapter_id, safe_int=safe_int):
            break

        completed.append(chapter_id)
        story["completed_ids"] = completed
        story["chapter_index"] = max(0, min(_safe_int(story.get("chapter_index"), 0) + 1, len(STORY_CHAPTERS)))
        story["last_completed_at"] = utc_now_iso()

        title = str(chapter.get("title") or chapter_id)
        reward_gold = max(0, safe_int(chapter.get("reward_gold"), 0))
        reward_skill_points = max(0, safe_int(chapter.get("reward_skill_points"), 0))
        if reward_gold > 0:
            state.player.gold = max(0, safe_int(getattr(state.player, "gold", 0), 0) + reward_gold)
        if reward_skill_points > 0:
            state.skill_points = max(0, safe_int(getattr(state, "skill_points", 0), 0) + reward_skill_points)

        reward_bits: list[str] = []
        if reward_gold > 0:
            reward_bits.append(f"+{reward_gold} or")
        if reward_skill_points > 0:
            reward_bits.append(f"+{reward_skill_points} points competence")
        reward_text = " | ".join(reward_bits) if reward_bits else "aucune"
        lines.append(f"📖 {title} valide. Recompenses: {reward_text}.")

    return lines


def _chapter_reached(state, chapter_id: str, *, safe_int) -> bool:
    chapter = str(chapter_id or "").strip()
    if chapter == "awakening":
        return bool(getattr(state, "player_sheet_ready", False))
    if chapter == "first_contact":
        counts = getattr(state, "npc_dialogue_counts", {})
        if not isinstance(counts, dict):
            return False
        return any(safe_int(value, 0) > 0 for value in counts.values())
    if chapter == "first_contract":
        quests = getattr(state, "quests", [])
        if not isinstance(quests, list):
            return False
        return any(isinstance(row, dict) for row in quests)
    if chapter == "first_delve":
        counters = getattr(state, "quest_counters", {})
        if not isinstance(counters, dict):
            return False
        return safe_int(counters.get("dungeon_floors_cleared"), 0) >= 1
    if chapter == "trusted_name":
        rep = getattr(state, "faction_reputation", {})
        if not isinstance(rep, dict):
            return False
        return max(
            safe_int(rep.get("Marchands"), 0),
            safe_int(rep.get("Habitants"), 0),
            safe_int(rep.get("Habitants de Lumeria"), 0),
        ) >= 20
    if chapter == "woven_paths":
        quests = getattr(state, "quests", [])
        if not isinstance(quests, list):
            return False
        done = 0
        for row in quests:
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "").strip() == "completed":
                done += 1
        return done >= 3
    if chapter == "ember_gate":
        counters = getattr(state, "quest_counters", {})
        rep = getattr(state, "faction_reputation", {})
        floors = safe_int(counters.get("dungeon_floors_cleared"), 0) if isinstance(counters, dict) else 0
        adventures = safe_int(rep.get("Aventuriers"), 0) if isinstance(rep, dict) else 0
        return floors >= 5 and adventures >= 30
    return False
