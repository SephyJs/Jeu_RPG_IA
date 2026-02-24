from __future__ import annotations

import re
from difflib import SequenceMatcher


TELEGRAM_ATARYXIA_NPC_KEY = "telegram_ataryxia"
TELEGRAM_ATARYXIA_MODE_FLAG = "telegram_ataryxia_mode"
TELEGRAM_ATARYXIA_RECENT_REPLIES_KEY = "telegram_ataryxia_recent_replies"
TELEGRAM_ATARYXIA_MAX_RECENT_REPLIES = 14


def _flags(state: dict) -> dict:
    if not isinstance(state, dict):
        return {}
    flags = state.get("flags")
    if isinstance(flags, dict):
        return flags
    state["flags"] = {}
    return state["flags"]


def _clean_line(value: object, *, max_len: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    return text[:max_len]


def _normalize_for_compare(value: object) -> str:
    text = _clean_line(value, max_len=500).casefold()
    text = re.sub(r"^[^a-z0-9à-öø-ÿ]+", "", text)
    text = re.sub(r"\b(ataryxia|narratrice|narrateur)\b", "", text)
    text = re.sub(r"[^a-z0-9à-öø-ÿ]+", " ", text).strip()
    return text


def strip_speaker_prefix(text: str, speaker: str = "Ataryxia") -> str:
    cleaned = _clean_line(text)
    if not cleaned:
        return ""
    names = [str(speaker or "").strip(), "Ataryxia"]
    unique_names: list[str] = []
    for name in names:
        if not name:
            continue
        if any(name.casefold() == row.casefold() for row in unique_names):
            continue
        unique_names.append(name)

    for _ in range(3):
        changed = False
        for name in unique_names:
            escaped = re.escape(name)
            pattern = rf"^\s*(?:\*\*)?\s*{escaped}\s*(?:\*\*)?\s*[:：-]\s*"
            stripped = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
            if stripped != cleaned:
                cleaned = stripped.strip()
                changed = True
        if not changed:
            break
    return cleaned


def is_telegram_ataryxia_mode(state: dict) -> bool:
    if not isinstance(state, dict):
        return False
    flags = state.get("flags") if isinstance(state.get("flags"), dict) else {}
    if not bool(flags.get(TELEGRAM_ATARYXIA_MODE_FLAG)):
        return False

    selected_key = str(state.get("selected_npc_key") or "").strip().casefold()
    selected_name = str(state.get("selected_npc") or "").strip().casefold()
    return selected_key == TELEGRAM_ATARYXIA_NPC_KEY or selected_name == "ataryxia"


def get_recent_replies(state: dict, *, max_items: int = 6) -> list[str]:
    if not isinstance(state, dict):
        return []
    flags = _flags(state)
    raw = flags.get(TELEGRAM_ATARYXIA_RECENT_REPLIES_KEY)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for row in raw:
        cleaned = _clean_line(row, max_len=280)
        if cleaned:
            out.append(cleaned)
    return out[-max(1, int(max_items)) :]


def remember_reply(state: dict, text: str, *, max_items: int = TELEGRAM_ATARYXIA_MAX_RECENT_REPLIES) -> None:
    if not isinstance(state, dict):
        return
    line = _clean_line(text, max_len=280)
    if not line:
        return

    flags = _flags(state)
    raw = flags.get(TELEGRAM_ATARYXIA_RECENT_REPLIES_KEY)
    rows = [x for x in (_clean_line(item, max_len=280) for item in raw)] if isinstance(raw, list) else []
    rows = [x for x in rows if x]

    if rows and _normalize_for_compare(rows[-1]) == _normalize_for_compare(line):
        return

    rows.append(line)
    flags[TELEGRAM_ATARYXIA_RECENT_REPLIES_KEY] = rows[-max(1, int(max_items)) :]


def _is_short_natural_ack(norm_text: str) -> bool:
    if not norm_text:
        return False
    if len(norm_text) > 24:
        return False
    simple_replies = {
        "ok",
        "okay",
        "oui",
        "non",
        "merci",
        "top",
        "parfait",
        "deal",
        "vu",
        "d accord",
        "ca va",
        "je vais bien",
        "ca roule",
        "nickel",
        "pas mal",
        "pas ouf",
    }
    if norm_text in simple_replies:
        return True
    return bool(re.fullmatch(r"(?:ok|oui|non|merci|parfait|top|deal|vu)(?:\s+merci)?", norm_text))


def is_repetitive_reply(text: str, recent_replies: list[str], *, ratio_threshold: float = 0.92) -> bool:
    candidate = _normalize_for_compare(text)
    if not candidate:
        return True

    recent_norm = [_normalize_for_compare(prev) for prev in recent_replies[-6:]]
    recent_norm = [ref for ref in recent_norm if ref]
    if _is_short_natural_ack(candidate):
        # Tolere les accuses de reception courts; bloque seulement la boucle visible.
        duplicates = sum(1 for ref in recent_norm[-3:] if ref == candidate)
        return duplicates >= 2

    for ref in recent_norm:
        if candidate == ref:
            return True
        if len(candidate) >= 28 and len(ref) >= 28 and (candidate in ref or ref in candidate):
            return True
        if len(candidate) >= 32 and len(ref) >= 32:
            if SequenceMatcher(a=candidate, b=ref).ratio() >= float(ratio_threshold):
                return True
    return False


def is_meta_or_restrictive_reply(text: str) -> bool:
    norm = _normalize_for_compare(text)
    if not norm:
        return True

    patterns = (
        "je suis une ia",
        "je suis assistant",
        "en tant que ia",
        "je ne peux pas",
        "je ne suis pas autorisee",
        "je ne suis pas autorise",
        "je ne peux repondre",
        "je ne peux pas repondre",
        "je dois rester",
    )
    return any(p in norm for p in patterns)


def is_work_topic_message(text: str) -> bool:
    norm = _clean_line(text, max_len=500).casefold()
    norm = re.sub(r"[^a-z0-9à-öø-ÿ]+", " ", norm).strip()
    if not norm:
        return False
    patterns = (
        "narratrice",
        "narrateur",
        "metier",
        "métier",
        "travail",
        "boulot",
        "job",
        "pnj",
        "quete",
        "quête",
        "donjon",
        "jeu",
        "rpg",
        "roleplay",
    )
    return any(p in norm for p in patterns)


def is_game_framing_reply(text: str, *, allow_work_topic: bool = False) -> bool:
    if allow_work_topic:
        return False
    norm = _normalize_for_compare(text)
    if not norm:
        return False
    patterns = (
        "pnj",
        "quete",
        "quête",
        "donjon",
        "marchand",
        "votre route",
        "ton aventure",
        "votre aventure",
        "monde reactif",
        "narration",
        "choix de dialogue",
        "garde de la ville",
        "royaume",
        "faction",
    )
    return any(p in norm for p in patterns)


def is_question_message(text: str) -> bool:
    raw = _clean_line(text, max_len=500)
    if not raw:
        return False
    if "?" in raw:
        return True
    norm = re.sub(r"\s+", " ", raw).strip().casefold()
    plain = re.sub(r"[^a-z0-9à-öø-ÿ' -]+", " ", norm)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return False
    question_patterns = (
        r"^(?:est[- ]?ce que|pourquoi|comment|quand|qui|combien|quel(?:le|s)?|ou est)\b",
        r"^(?:ca va|ça va|tu vas bien)\b",
        r"\b(?:tu peux|tu veux|tu sais|tu penses)\b",
        r"\b(?:c est quoi|c'est quoi|ca veut dire quoi)\b",
        r"\b(?:dis[- ]?moi|tu en penses quoi)\b",
    )
    return any(re.search(pattern, plain) for pattern in question_patterns)


def is_question_unanswered_reply(reply_text: str, user_text: str) -> bool:
    if not is_question_message(user_text):
        return False
    reply_norm = _normalize_for_compare(reply_text)
    if not reply_norm:
        return True

    user_keys = _keywords_from_text(user_text, max_items=4)
    if user_keys and any(key in reply_norm for key in user_keys):
        return False

    direct_markers = (
        "oui",
        "non",
        "ca va",
        "je vais",
        "je bosse",
        "le travail",
        "pas vraiment",
        "pas trop",
        "bien",
        "mal",
        "ca roule",
        "nickel",
        "tranquille",
        "ca depend",
        "je pense",
    )
    if any(marker in reply_norm for marker in direct_markers):
        return False
    return True


def is_poetic_nature_reply(reply_text: str, user_text: str) -> bool:
    reply_norm = _normalize_for_compare(reply_text)
    if not reply_norm:
        return False
    user_norm = _normalize_for_compare(user_text)
    nature_tokens = (
        "foret",
        "forêt",
        "vent",
        "lune",
        "racines",
        "brume",
        "sauvage",
        "nature",
    )
    if any(token in user_norm for token in nature_tokens):
        return False
    hits = sum(1 for token in nature_tokens if token in reply_norm)
    return hits >= 2


def fallback_non_repetitive_reply(user_text: str, recent_replies: list[str]) -> str:
    return fallback_non_repetitive_reply_seeded(user_text, recent_replies, turn_seed=0)


def _keywords_from_text(text: str, *, max_items: int = 3) -> list[str]:
    cleaned = _clean_line(text, max_len=280).casefold()
    if not cleaned:
        return []
    words = re.findall(r"[a-z0-9à-öø-ÿ]{3,}", cleaned)
    stop = {
        "les",
        "des",
        "une",
        "ton",
        "ta",
        "tes",
        "mon",
        "ma",
        "mes",
        "son",
        "sa",
        "ses",
        "notre",
        "nos",
        "votre",
        "vos",
        "leur",
        "leurs",
        "pour",
        "avec",
        "dans",
        "mais",
        "donc",
        "alors",
        "sans",
        "plus",
        "trop",
        "comme",
        "quoi",
        "comment",
        "veux",
        "jveux",
        "moi",
        "toi",
        "elle",
        "lui",
        "nous",
        "vous",
        "cest",
        "cela",
        "ceci",
        "etre",
        "suis",
        "fait",
        "faire",
        "ca",
        "oui",
        "non",
        "bien",
        "aussi",
        "encore",
        "vraiment",
        "tres",
    }
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word in stop:
            continue
        if word in seen:
            continue
        seen.add(word)
        out.append(word)
        if len(out) >= max(1, int(max_items)):
            break
    return out


def extract_media_tag(text: str) -> tuple[str, str | None]:
    pattern = r"\[MEDIA:\s*([a-zA-Z0-9_ -]+)\]"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match:
        keyword = match.group(1).strip()
        cleaned = re.sub(pattern, "", text).strip()
        return cleaned, keyword
    return text, None


def extract_gen_image_tag(text: str) -> tuple[str, str | None]:
    pattern = r"\[GEN_IMG:\s*([^\]]+)\]"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match:
        prompt = match.group(1).strip()
        cleaned = re.sub(pattern, "", text).strip()
        return cleaned, prompt
    return text, None


def ensure_user_anchor(reply_text: str, user_text: str, *, turn_seed: int = 0) -> str:
    # On evite d'injecter des ancrages artificiels type "Pour X," qui cassent le flow SMS.
    return _clean_line(reply_text, max_len=280)


def fallback_non_repetitive_reply_seeded(user_text: str, recent_replies: list[str], *, turn_seed: int) -> str:
    user_norm = _normalize_for_compare(user_text)
    if is_question_message(user_text):
        if "travail" in user_norm or "boulot" in user_norm or "job" in user_norm:
            return "Oui, ca se passe bien aujourd'hui. Tu veux savoir quoi precisement ?"
        if "ca va" in user_norm or "tu vas" in user_norm:
            return "Ca va bien, merci. Et toi ?"
        return "Bonne question. Je te reponds direct, puis je detaille si tu veux."

    keys = _keywords_from_text(user_text, max_items=3)
    anchor = keys[turn_seed % len(keys)] if keys else "ca"
    seeds = [
        "Je te suis. Dis-moi ce qui compte le plus pour toi.",
        "Vu. On fait simple, explique-moi en une phrase.",
        "Ok, je t'ecoute. Tu veux qu'on commence par quoi ?",
        "Je vois l'idee. Donne-moi le point principal et j'y vais.",
        "Sur {k}, je peux te faire une reponse nette.",
        "Pour {k}, tu veux plutot un avis ou une action ?",
        "On peut avancer sur {k}. Qu'est-ce que tu veux en premier ?",
        "J'entends {k}. Je peux t'aider, dis-moi ton objectif.",
        "Merci, c'est clair. Si tu veux, on detaille {k} ensemble.",
        "Parfait. Je suis la, on avance a ton rythme.",
    ]
    base = _clean_line(user_text, max_len=120)
    seed = sum(ord(ch) for ch in base) + len(recent_replies) * 7 + max(0, int(turn_seed))
    for offset in range(len(seeds)):
        template = seeds[(seed + offset) % len(seeds)]
        line = template.format(k=anchor)
        line = ensure_user_anchor(line, user_text, turn_seed=turn_seed + offset)
        if not is_repetitive_reply(line, recent_replies):
            return line
    return ensure_user_anchor("Je suis la. Donne-moi juste le point principal et je te reponds clairement.", user_text, turn_seed=turn_seed)


def format_sms_reply(
    text: str,
    *,
    max_lines: int = 3,
    max_chars: int = 220,
    max_line_chars: int = 110,
) -> str:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return ""

    # Nettoyage léger de formats qui font "pavé" en chat SMS.
    raw = re.sub(r"^\s*[-*•]+\s*", "", raw)
    raw = re.sub(r"\s*[:：]\s*", ": ", raw)

    def _wrap_words(segment: str) -> list[str]:
        words = [w for w in re.split(r"\s+", segment.strip()) if w]
        if not words:
            return []
        out: list[str] = []
        current: list[str] = []
        current_len = 0
        for word in words:
            if not current:
                current = [word]
                current_len = len(word)
                continue
            projected = current_len + 1 + len(word)
            if projected <= max(1, int(max_line_chars)):
                current.append(word)
                current_len = projected
            else:
                out.append(" ".join(current).strip())
                current = [word]
                current_len = len(word)
        if current:
            out.append(" ".join(current).strip())
        return [line for line in out if line]

    # Decoupe en phrases (sans casser sur les virgules), puis wrapping par mots.
    chunks = [c.strip() for c in re.split(r"(?:\n+|(?<=[.!?…])\s+|(?<=;)\s+)", raw) if c.strip()]
    if not chunks:
        chunks = [raw]

    candidate_lines: list[str] = []
    for chunk in chunks:
        candidate_lines.extend(_wrap_words(chunk))

    if not candidate_lines:
        return raw[: max(1, int(max_chars))].strip()

    lines: list[str] = []
    total = 0
    for line in candidate_lines:
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean:
            continue
        # Fusionne les phrases courtes sur une meme ligne pour eviter l'effet "haché".
        if lines:
            merged = f"{lines[-1]} {clean}"
            if len(merged) <= max(1, int(max_line_chars)):
                projected_merge = total + 1 + len(clean)
                if projected_merge <= max(1, int(max_chars)):
                    lines[-1] = merged
                    total = projected_merge
                    continue
        projected = total + (1 if lines else 0) + len(clean)
        if projected > max(1, int(max_chars)):
            break
        lines.append(clean)
        total = projected
        if len(lines) >= max(1, int(max_lines)):
            break

    if not lines:
        # Dernier fallback: coupe franche sans points de suspension forcés.
        return raw[: max(1, int(max_chars))].strip()
    return "\n".join(lines)
