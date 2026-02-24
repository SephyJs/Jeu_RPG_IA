from __future__ import annotations

from datetime import datetime, timezone
import inspect
import re

from nicegui import ui

from app.ui.state.game_state import GameState


_CHAT_SCROLL_JS = """
(() => {
  const cards = Array.from(document.querySelectorAll('.dialogue-chat-card'));
  if (!cards.length) return;
  cards.forEach((card) => {
    const distanceToBottom = Math.max(0, card.scrollHeight - card.clientHeight - card.scrollTop);
    const force = card.dataset.forceScroll === '1';
    if (force || distanceToBottom <= 96) {
      card.scrollTop = card.scrollHeight;
    }
    card.dataset.forceScroll = '0';
  });
})();
"""

_CHAT_FOCUS_JS = """
(() => {
  const active = document.activeElement;
  const typingInAnotherField = !!active && active.tagName === 'INPUT' && active.id !== 'main_chat_input';
  if (typingInAnotherField) return;
  const input = document.getElementById('main_chat_input');
  if (!input) return;
  const isDisabled = input.hasAttribute('disabled') || input.getAttribute('aria-disabled') === 'true';
  if (isDisabled) return;
  if (document.activeElement !== input) {
    input.focus({preventScroll: true});
  }
})();
"""

_TRADE_REASON_RE = re.compile(
    r"\b(achat|acheter|achete|acheté|achetee|vente|vendre|vendu|vendue|echange|échange|echanger|don|donner|offrir|offert|troque)\b",
    flags=re.IGNORECASE,
)


@ui.refreshable
def render_chat_messages(state: GameState) -> None:
    if not state.chat:
        ui.label("Aucun message pour l'instant.").classes("opacity-70")
    else:
        for msg in state.chat[-200:]:
            speaker = str(msg.speaker or "").strip()
            text = str(msg.text or "").strip()
            if speaker.casefold() in {"narration système", "narration systeme"}:
                ui.markdown(f"> **Narration système**: *{text}*")
            else:
                ui.markdown(f"**{speaker}** : {text}")


def schedule_chat_autoscroll(*, force: bool = True) -> None:
    def _run_scroll() -> None:
        try:
            if force:
                ui.run_javascript(
                    """
                    (() => {
                      const cards = Array.from(document.querySelectorAll('.dialogue-chat-card'));
                      cards.forEach((card) => { card.dataset.forceScroll = '1'; });
                    })();
                    """
                )
            ui.run_javascript(_CHAT_SCROLL_JS)
        except Exception:
            pass

    try:
        ui.timer(0.05, _run_scroll, once=True)
    except Exception:
        _run_scroll()


def refresh_chat_messages_view(*, force_scroll: bool = True) -> None:
    render_chat_messages.refresh()
    schedule_chat_autoscroll(force=force_scroll)


def schedule_chat_input_focus() -> None:
    def _run_focus() -> None:
        try:
            ui.run_javascript(_CHAT_FOCUS_JS)
        except Exception:
            pass

    try:
        ui.timer(0.05, _run_focus, once=True)
    except Exception:
        _run_focus()


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def experience_tier(level: int, skill_count: int) -> str:
    if level <= 2 and skill_count <= 2:
        return "debutant"
    if level <= 5 or skill_count <= 6:
        return "intermediaire"
    return "avance"


class TransientInput:
    def __init__(self, value: str = "") -> None:
        self.value = value


def sanitize_progression_for_trade(progression: dict, trade_outcome: dict) -> dict:
    if not isinstance(progression, dict):
        return progression
    if not isinstance(trade_outcome, dict):
        return progression

    trade_context = (
        trade_outcome.get("trade_context")
        if isinstance(trade_outcome.get("trade_context"), dict)
        else {}
    )
    trade_ok = bool(trade_outcome.get("applied")) and str(trade_context.get("status") or "").strip() == "ok"
    if trade_ok:
        return progression

    reason = str(progression.get("reason") or "").strip()
    if not reason:
        return progression
    if not _TRADE_REASON_RE.search(reason):
        return progression

    cleaned = dict(progression)
    cleaned["xp_gain"] = 0
    if "restore_hp_to_full" in cleaned:
        cleaned["restore_hp_to_full"] = False
    if "restore_hp" in cleaned:
        cleaned["restore_hp"] = 0
    deltas = cleaned.get("stat_deltas")
    if isinstance(deltas, dict):
        cleaned["stat_deltas"] = {str(k): 0 for k in deltas.keys()}
    cleaned["reason"] = ""
    return cleaned


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_command_echo(command_text: str) -> str:
    raw = str(command_text or "").strip()
    if not raw:
        return raw
    if raw.casefold().startswith("/telegram "):
        parts = raw.split(maxsplit=2)
        if len(parts) >= 2 and ":" in parts[1]:
            token = parts[1]
            if len(token) <= 10:
                masked = "*" * len(token)
            else:
                masked = f"{token[:4]}...{token[-4:]}"
            if len(parts) == 3:
                return f"/telegram {masked} {parts[2]}"
            return f"/telegram {masked}"
    return raw


async def run_chat_command_handler(command_text: str, chat_command_handler) -> tuple[bool, str, str]:
    if chat_command_handler is None:
        return False, "", sanitize_command_echo(command_text)
    try:
        result = chat_command_handler(command_text)
        if inspect.isawaitable(result):
            result = await result
    except Exception as e:
        return True, f"Commande echouee: {e}", sanitize_command_echo(command_text)

    if isinstance(result, tuple):
        handled = bool(result[0]) if len(result) >= 1 else False
        response = str(result[1] or "").strip() if len(result) >= 2 else ""
        user_echo = str(result[2] or "").strip() if len(result) >= 3 else sanitize_command_echo(command_text)
        return handled, response, user_echo

    if isinstance(result, dict):
        handled = bool(result.get("handled"))
        response = str(result.get("response") or "").strip()
        user_echo = str(result.get("user_echo") or sanitize_command_echo(command_text)).strip()
        return handled, response, user_echo

    if isinstance(result, bool):
        return bool(result), "", sanitize_command_echo(command_text)
    if isinstance(result, str):
        return True, result.strip(), sanitize_command_echo(command_text)
    return False, "", sanitize_command_echo(command_text)
