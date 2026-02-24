from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import os
import random
import re
import time

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.gamemaster import BananaClient
from app.infra import text_library as _text_library
from app.telegram.runtime import (
    TELEGRAM_MODE_ATARYXIA,
    TELEGRAM_MODE_DUNGEON,
    DungeonConsumableOption,
    TelegramGameSession,
    TelegramSessionManager,
    TurnOutput,
)


def _text(key: str, **vars: object) -> str:
    return _text_library.pick(key, **vars)


BUTTON_MODE_DUNGEON = _text("ui.button.mode_dungeon")
BUTTON_MODE_ATARYXIA = _text("ui.button.mode_ataryxia")
BUTTON_STATUS = _text("ui.button.status")
BUTTON_SAVE = _text("ui.button.save")

CALLBACK_NOOP = "noop"
CALLBACK_DUNGEON_ENTER = "dg:enter"
CALLBACK_DUNGEON_ADVANCE = "dg:advance"
CALLBACK_DUNGEON_ATTACK = "dg:attack"
CALLBACK_DUNGEON_SKILL_MENU = "dg:skill_menu"
CALLBACK_DUNGEON_SKILL_HEAL = "dg:skill:heal"
CALLBACK_DUNGEON_SKILL_SPELL = "dg:skill:spell"
CALLBACK_DUNGEON_SKILL_CORE = "dg:skill:core"
CALLBACK_DUNGEON_FLEE = "dg:flee"
CALLBACK_DUNGEON_INVENTORY = "dg:inventory"
CALLBACK_DUNGEON_ITEM_PREFIX = "dg:item:"
CALLBACK_DUNGEON_BACK = "dg:back"

_IDLE_NUDGE_TASK_KEY = "telegram_idle_nudge_task"
_IDLE_LAST_USER_ACTIVITY_KEY = "telegram_idle_last_user_activity"
_IDLE_LAST_NUDGE_KEY = "telegram_idle_last_nudge"
_IDLE_WAITING_REPLY_KEY = "telegram_idle_waiting_reply"
_IDLE_DAILY_PLAN_KEY = "telegram_idle_daily_plan"


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


IDLE_NUDGE_AFTER_SECONDS = max(120, _env_int("TELEGRAM_IDLE_NUDGE_AFTER_SECONDS", 1200))
IDLE_NUDGE_CHECK_SECONDS = max(300, _env_int("TELEGRAM_IDLE_NUDGE_CHECK_SECONDS", 600))
IDLE_NUDGE_MAX_PER_DAY = max(1, min(3, _env_int("TELEGRAM_IDLE_NUDGE_MAX_PER_DAY", 3)))
IDLE_NUDGE_WINDOW_START_HOUR = max(0, min(23, _env_int("TELEGRAM_IDLE_NUDGE_WINDOW_START_HOUR", 8)))
IDLE_NUDGE_WINDOW_END_HOUR = max(IDLE_NUDGE_WINDOW_START_HOUR + 1, min(24, _env_int("TELEGRAM_IDLE_NUDGE_WINDOW_END_HOUR", 20)))
IDLE_NUDGE_MIN_GAP_CHOICES_SECONDS = (3600, 7200)

# Client global pour l'API image (stateless)
banana_client = BananaClient()


def _main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(BUTTON_MODE_DUNGEON), KeyboardButton(BUTTON_MODE_ATARYXIA)],
            [KeyboardButton(BUTTON_STATUS), KeyboardButton(BUTTON_SAVE)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _chunked(values: list[InlineKeyboardButton], size: int = 2) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(values), max(1, size)):
        rows.append(values[i : i + max(1, size)])
    return rows


def _dungeon_keyboard(session: TelegramGameSession) -> InlineKeyboardMarkup:
    in_dungeon = session.in_dungeon()
    in_combat = session.dungeon_has_active_combat()

    if not in_dungeon:
        top = InlineKeyboardButton(_text("ui.dungeon.action.enter"), callback_data=CALLBACK_DUNGEON_ENTER)
    elif in_combat:
        top = InlineKeyboardButton(_text("ui.dungeon.action.combat_active"), callback_data=CALLBACK_NOOP)
    else:
        top = InlineKeyboardButton(_text("ui.dungeon.action.advance"), callback_data=CALLBACK_DUNGEON_ADVANCE)

    rows: list[list[InlineKeyboardButton]] = [
        [top],
        [
            InlineKeyboardButton(_text("ui.dungeon.action.attack"), callback_data=CALLBACK_DUNGEON_ATTACK),
            InlineKeyboardButton(_text("ui.dungeon.action.skill"), callback_data=CALLBACK_DUNGEON_SKILL_MENU),
            InlineKeyboardButton(_text("ui.dungeon.action.flee"), callback_data=CALLBACK_DUNGEON_FLEE),
        ],
        [InlineKeyboardButton(_text("ui.dungeon.action.inventory"), callback_data=CALLBACK_DUNGEON_INVENTORY)],
    ]
    return InlineKeyboardMarkup(rows)


def _dungeon_skill_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(_text("ui.dungeon.action.heal"), callback_data=CALLBACK_DUNGEON_SKILL_HEAL),
                InlineKeyboardButton(_text("ui.dungeon.action.spell"), callback_data=CALLBACK_DUNGEON_SKILL_SPELL),
            ],
            [InlineKeyboardButton(_text("ui.dungeon.action.skill"), callback_data=CALLBACK_DUNGEON_SKILL_CORE)],
            [InlineKeyboardButton(_text("ui.dungeon.action.back"), callback_data=CALLBACK_DUNGEON_BACK)],
        ]
    )


def _dungeon_inventory_keyboard(rows: list[DungeonConsumableOption]) -> InlineKeyboardMarkup:
    buttons: list[InlineKeyboardButton] = []
    for row in rows[:24]:
        item_id = str(row.item_id or "").strip().casefold()
        item_name = str(row.item_name or item_id).strip()[:40]
        qty = max(1, int(row.qty))
        if not item_id:
            continue
        buttons.append(
            InlineKeyboardButton(
                _text("ui.dungeon.inventory_item_label", item=item_name, qty=qty),
                callback_data=f"{CALLBACK_DUNGEON_ITEM_PREFIX}{item_id}",
            )
        )

    output_rows: list[list[InlineKeyboardButton]] = []
    if buttons:
        output_rows.extend(_chunked(buttons, size=1))
    else:
        output_rows.append([InlineKeyboardButton(_text("ui.dungeon.inventory_empty"), callback_data=CALLBACK_NOOP)])
    output_rows.append([InlineKeyboardButton(_text("ui.dungeon.action.back"), callback_data=CALLBACK_DUNGEON_BACK)])
    return InlineKeyboardMarkup(output_rows)


def _manager(context: ContextTypes.DEFAULT_TYPE) -> TelegramSessionManager:
    manager = context.application.bot_data.get("telegram_session_manager")
    if isinstance(manager, TelegramSessionManager):
        return manager
    raise RuntimeError(_text("error.bot.session_manager_unavailable"))


def _dict_map(application: Application, key: str) -> dict:
    raw = application.bot_data.get(key)
    if isinstance(raw, dict):
        return raw
    application.bot_data[key] = {}
    return application.bot_data[key]


def _ts_map(application: Application, key: str) -> dict[int, float]:
    return _dict_map(application, key)  # type: ignore[return-value]


def _local_day_key(ts_value: float) -> str:
    return datetime.fromtimestamp(float(ts_value)).date().isoformat()


def _is_within_nudge_window(ts_value: float) -> bool:
    local_dt = datetime.fromtimestamp(float(ts_value))
    hour = int(local_dt.hour)
    return IDLE_NUDGE_WINDOW_START_HOUR <= hour < IDLE_NUDGE_WINDOW_END_HOUR


def _build_random_daily_schedule(*, now_ts: float, max_items: int) -> list[float]:
    local_now = datetime.fromtimestamp(float(now_ts))
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = day_start + timedelta(hours=IDLE_NUDGE_WINDOW_START_HOUR)
    end_dt = day_start + timedelta(hours=IDLE_NUDGE_WINDOW_END_HOUR)
    if end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    start_ts = start_dt.timestamp()
    end_ts = end_dt.timestamp()
    hard_min_gap = min(IDLE_NUDGE_MIN_GAP_CHOICES_SECONDS)
    capacity = max(1, int((end_ts - start_ts) // hard_min_gap) + 1)
    target = max(1, min(int(max_items), capacity))

    for _ in range(48):
        points: list[float] = []
        earliest = start_ts
        ok = True
        for idx in range(target):
            remaining = target - idx - 1
            latest = end_ts - (remaining * hard_min_gap)
            if latest < earliest:
                ok = False
                break
            slot = random.uniform(earliest, latest)
            slot = round(slot / 60.0) * 60.0
            points.append(slot)
            if remaining > 0:
                min_gap = random.choice(IDLE_NUDGE_MIN_GAP_CHOICES_SECONDS)
                earliest = slot + float(min_gap)
        if ok and len(points) == target:
            points = sorted(points)
            return points

    # Fallback deterministe si la generation aleatoire echoue.
    if target <= 1:
        return [round((start_ts + end_ts) / 2.0 / 60.0) * 60.0]
    step = (end_ts - start_ts) / float(target - 1)
    points = [round((start_ts + (step * i)) / 60.0) * 60.0 for i in range(target)]
    return sorted(points)


def _daily_plan_state(application: Application, chat_id: int, *, now_ts: float) -> dict:
    plans = _dict_map(application, _IDLE_DAILY_PLAN_KEY)
    day = _local_day_key(now_ts)
    row = plans.get(int(chat_id))
    if not isinstance(row, dict) or str(row.get("day") or "") != day:
        schedule = _build_random_daily_schedule(now_ts=now_ts, max_items=IDLE_NUDGE_MAX_PER_DAY)
        sent_count = sum(1 for slot in schedule if float(slot) <= float(now_ts))
        row = {"day": day, "schedule": schedule, "sent_count": sent_count}
        plans[int(chat_id)] = row
        return row

    schedule_raw = row.get("schedule")
    schedule = [float(slot) for slot in schedule_raw] if isinstance(schedule_raw, list) else []
    if not schedule:
        schedule = _build_random_daily_schedule(now_ts=now_ts, max_items=IDLE_NUDGE_MAX_PER_DAY)
    try:
        sent_count = int(row.get("sent_count") or 0)
    except (TypeError, ValueError):
        sent_count = 0
    sent_count = max(0, min(sent_count, len(schedule)))
    row["day"] = day
    row["schedule"] = schedule
    row["sent_count"] = sent_count
    plans[int(chat_id)] = row
    return row


def _next_daily_nudge_ts(application: Application, chat_id: int, *, now_ts: float) -> float | None:
    row = _daily_plan_state(application, chat_id, now_ts=now_ts)
    schedule_raw = row.get("schedule")
    schedule = [float(slot) for slot in schedule_raw] if isinstance(schedule_raw, list) else []
    try:
        sent_count = int(row.get("sent_count") or 0)
    except (TypeError, ValueError):
        sent_count = 0
    sent_count = max(0, sent_count)
    if sent_count >= len(schedule):
        return None
    return float(schedule[sent_count])


def _advance_daily_nudge(application: Application, chat_id: int, *, now_ts: float) -> None:
    row = _daily_plan_state(application, chat_id, now_ts=now_ts)
    schedule_raw = row.get("schedule")
    schedule = [float(slot) for slot in schedule_raw] if isinstance(schedule_raw, list) else []
    try:
        sent_count = int(row.get("sent_count") or 0)
    except (TypeError, ValueError):
        sent_count = 0
    if sent_count < len(schedule):
        row["sent_count"] = sent_count + 1
    else:
        row["sent_count"] = len(schedule)
    _dict_map(application, _IDLE_DAILY_PLAN_KEY)[int(chat_id)] = row


def _set_waiting_for_reply(application: Application, chat_id: int, waiting: bool) -> None:
    _dict_map(application, _IDLE_WAITING_REPLY_KEY)[int(chat_id)] = bool(waiting)


def _is_waiting_for_reply(application: Application, chat_id: int) -> bool:
    return bool(_dict_map(application, _IDLE_WAITING_REPLY_KEY).get(int(chat_id), False))


def _mark_user_activity(application: Application, chat_id: int) -> None:
    _ts_map(application, _IDLE_LAST_USER_ACTIVITY_KEY)[int(chat_id)] = time.time()
    _set_waiting_for_reply(application, int(chat_id), False)


def _mark_nudge_sent(application: Application, chat_id: int) -> None:
    _ts_map(application, _IDLE_LAST_NUDGE_KEY)[int(chat_id)] = time.time()
    _set_waiting_for_reply(application, int(chat_id), True)
    _advance_daily_nudge(application, int(chat_id), now_ts=time.time())


def _ensure_idle_nudge_loop(application: Application) -> None:
    task = application.bot_data.get(_IDLE_NUDGE_TASK_KEY)
    if isinstance(task, asyncio.Task) and not task.done():
        return
    application.bot_data[_IDLE_NUDGE_TASK_KEY] = asyncio.create_task(_idle_nudge_loop(application))


def _display_name_from_update(update: Update) -> str:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None:
        return ""
    display_name = ""
    if user is not None:
        parts = [str(user.first_name or "").strip(), str(user.last_name or "").strip()]
        display_name = " ".join(p for p in parts if p).strip() or str(user.username or "").strip()
    if not display_name:
        display_name = f"Telegram-{chat.id}"
    return display_name


async def _session_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> TelegramGameSession:
    chat = update.effective_chat
    if chat is None:
        raise RuntimeError(_text("error.bot.chat_not_found"))
    display_name = _display_name_from_update(update)
    session = await _manager(context).get_session(chat_id=int(chat.id), display_name=display_name)
    _ensure_idle_nudge_loop(context.application)
    _mark_user_activity(context.application, int(chat.id))
    return session


def _register_user_activity_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    _ensure_idle_nudge_loop(context.application)
    _mark_user_activity(context.application, int(chat.id))


def _split_ataryxia_bubbles(text: str, *, max_bubbles: int = 2, max_chars: int = 170) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []

    parts = [re.sub(r"\s+", " ", chunk).strip() for chunk in re.split(r"\n+", raw) if str(chunk).strip()]
    merged = " ".join(part for part in parts if part).strip()
    if not merged:
        return []

    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", merged) if s.strip()]
    if not sentences:
        sentences = [merged]

    bubbles: list[str] = []
    current = ""
    limit = max(1, int(max_bubbles))
    for sentence in sentences:
        candidate = sentence if not current else f"{current} {sentence}"
        if not current or len(candidate) <= max(1, int(max_chars)):
            current = candidate
            continue
        bubbles.append(current)
        current = sentence
        if len(bubbles) >= limit - 1:
            break
    if current:
        bubbles.append(current)

    bubbles = [row for row in bubbles[:limit] if row]
    if len(bubbles) == 1 and len(bubbles[0]) > max(1, int(max_chars)):
        words = [w for w in bubbles[0].split(" ") if w]
        head = ""
        tail = ""
        for word in words:
            probe = f"{head} {word}".strip()
            if not head or len(probe) <= max(1, int(max_chars)):
                head = probe
            else:
                tail = f"{tail} {word}".strip()
        bubbles = [head]
        if tail:
            bubbles.append(tail[: max(1, int(max_chars))].strip())
    return [row for row in bubbles[:limit] if row]


async def _send_typing_hint(text_target, text: str) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    try:
        await text_target.get_bot().send_chat_action(chat_id=text_target.chat_id, action="typing")
    except Exception:
        return
    wait_s = min(1.6, max(0.35, len(clean) * 0.012))
    await asyncio.sleep(wait_s)


async def _send_typing_hint_chat_id(application: Application, chat_id: int, text: str) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    try:
        await application.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        return
    wait_s = min(1.6, max(0.35, len(clean) * 0.012))
    await asyncio.sleep(wait_s)


async def _idle_nudge_loop(application: Application) -> None:
    try:
        while True:
            await asyncio.sleep(IDLE_NUDGE_CHECK_SECONDS)
            manager = application.bot_data.get("telegram_session_manager")
            if not isinstance(manager, TelegramSessionManager):
                continue

            now = time.time()
            user_activity = _ts_map(application, _IDLE_LAST_USER_ACTIVITY_KEY)
            last_nudges = _ts_map(application, _IDLE_LAST_NUDGE_KEY)

            for session in manager.active_sessions():
                chat_id = int(getattr(session, "chat_id", 0) or 0)
                if chat_id <= 0:
                    continue
                if not _is_within_nudge_window(now):
                    continue

                last_user = float(user_activity.get(chat_id, 0.0) or 0.0)
                if last_user <= 0.0:
                    # Evite une relance immediate au redemarrage: initialise d'abord.
                    user_activity[chat_id] = now
                    continue

                next_nudge_ts = _next_daily_nudge_ts(application, chat_id, now_ts=now)
                if next_nudge_ts is None:
                    continue
                if now < float(next_nudge_ts):
                    continue

                last_nudge = float(last_nudges.get(chat_id, 0.0) or 0.0)
                waiting_reply = _is_waiting_for_reply(application, chat_id)
                if waiting_reply and last_nudge <= 0.0:
                    _set_waiting_for_reply(application, chat_id, False)
                    waiting_reply = False

                if waiting_reply:
                    # Relances 2/3: uniquement si aucune reponse utilisateur depuis la relance precedente.
                    if last_user > last_nudge:
                        _set_waiting_for_reply(application, chat_id, False)
                        continue
                else:
                    # Premiere relance de la serie: seulement apres inactivite.
                    if (now - last_user) < float(IDLE_NUDGE_AFTER_SECONDS):
                        continue

                try:
                    async with session.lock:
                        if session.telegram_mode() != TELEGRAM_MODE_ATARYXIA:
                            continue
                        if not (session.state and session.state.player_sheet_ready):
                            continue
                        if session.in_dungeon() or session.dungeon_has_active_combat():
                            continue
                        nudge_text = session.build_idle_nudge_text()
                        if not nudge_text:
                            continue
                        session.save()

                    bubbles = _split_ataryxia_bubbles(nudge_text, max_bubbles=2, max_chars=170)
                    if not bubbles:
                        bubbles = [str(nudge_text).strip() or _text("system.message.placeholder")]
                    for idx, bubble in enumerate(bubbles):
                        await _send_typing_hint_chat_id(application, chat_id, bubble)
                        markup = _main_keyboard() if idx == len(bubbles) - 1 else None
                        await application.bot.send_message(chat_id=chat_id, text=bubble, reply_markup=markup)
                    _mark_nudge_sent(application, chat_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
    except asyncio.CancelledError:
        return


async def _send_turn_output(*, text_target, output: TurnOutput, session: TelegramGameSession) -> None:
    mode = session.telegram_mode()
    if mode == TELEGRAM_MODE_ATARYXIA:
        bubbles = _split_ataryxia_bubbles(output.text, max_bubbles=3, max_chars=220)
        if not bubbles:
            bubbles = [str(output.text or "").strip() or _text("system.message.placeholder")]
        for idx, bubble in enumerate(bubbles):
            await _send_typing_hint(text_target, bubble)
            markup = _main_keyboard() if idx == len(bubbles) - 1 else None
            await text_target.reply_text(bubble, reply_markup=markup)
    else:
        await text_target.reply_text(output.text, reply_markup=_main_keyboard())

    if output.generated_image_prompt:
        # Petit feedback visuel "uploading photo" pendant que ça génère
        try:
            await text_target.get_bot().send_chat_action(chat_id=text_target.chat_id, action="upload_photo")
        except Exception:
            pass

        image_bytes = await banana_client.generate_image(output.generated_image_prompt)
        if image_bytes:
            await text_target.reply_photo(photo=image_bytes)

    if mode == TELEGRAM_MODE_DUNGEON:
        await text_target.reply_text(_text("ui.dungeon.actions_title"), reply_markup=_dungeon_keyboard(session))


async def _switch_mode(session: TelegramGameSession, mode: str) -> TurnOutput:
    if mode == TELEGRAM_MODE_DUNGEON:
        session.set_telegram_mode(TELEGRAM_MODE_DUNGEON)
        return await session.dungeon_enter_or_resume()
    session.set_telegram_mode(TELEGRAM_MODE_ATARYXIA)
    session.save()
    return TurnOutput(
        text=_text("system.mode.ataryxia_active"),
        has_pending_trade=False,
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    session = await _session_from_update(update, context)
    async with session.lock:
        status = session.status_text()
        mode = session.telegram_mode()
        creation = session.creation_status_text() if not (session.state and session.state.player_sheet_ready) else ""
    mode_name = _text("system.bot.mode_name_dungeon") if mode == TELEGRAM_MODE_DUNGEON else _text("system.bot.mode_name_ataryxia")
    lines = [
        _text("system.start.title"),
        _text("system.start.current_mode", mode=mode_name),
        _text("system.start.quick_choice"),
    ]
    await message.reply_text("\n".join(lines), reply_markup=_main_keyboard())
    await message.reply_text(status, reply_markup=_main_keyboard())
    if creation:
        await message.reply_text(creation, reply_markup=_main_keyboard())
    if mode == TELEGRAM_MODE_DUNGEON:
        await message.reply_text(_text("ui.dungeon.actions_title"), reply_markup=_dungeon_keyboard(session))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    session = await _session_from_update(update, context)
    async with session.lock:
        mode = session.telegram_mode()
        status = session.status_text()
        if mode == TELEGRAM_MODE_DUNGEON:
            status = f"{status}\n\n{session.dungeon_status_text()}"
    await message.reply_text(status, reply_markup=_main_keyboard())
    if mode == TELEGRAM_MODE_DUNGEON:
        await message.reply_text(_text("ui.dungeon.actions_title"), reply_markup=_dungeon_keyboard(session))


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    session = await _session_from_update(update, context)
    async with session.lock:
        session.save()
    await message.reply_text(_text("system.save.done"), reply_markup=_main_keyboard())


async def cmd_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    session = await _session_from_update(update, context)
    async with session.lock:
        text = session.creation_status_text()
    await message.reply_text(text, reply_markup=_main_keyboard())


async def cmd_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    manager = _manager(context)
    profiles = manager.list_profiles()
    if not profiles:
        await message.reply_text(_text("system.bot.no_profiles"), reply_markup=_main_keyboard())
        return

    lines = [_text("system.bot.profiles_header")]
    for row in profiles[:12]:
        key = str(row.get("profile_key") or "").strip()
        name = str(row.get("display_name") or key).strip() or key
        lines.append(_text("system.bot.profile_line", key=key, name=name))
    lines.append(_text("system.bot.useprofile_hint"))
    await message.reply_text("\n".join(lines), reply_markup=_main_keyboard())


async def cmd_useprofile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    args = list(context.args or [])
    if not args:
        await message.reply_text(_text("error.bot.profile_usage"), reply_markup=_main_keyboard())
        return

    _register_user_activity_from_update(update, context)
    manager = _manager(context)
    display_name = _display_name_from_update(update)
    session = await manager.switch_profile(
        chat_id=int(chat.id),
        display_name=display_name,
        profile_key=str(args[0]),
    )
    async with session.lock:
        status = session.status_text()
        creation = session.creation_status_text() if not (session.state and session.state.player_sheet_ready) else ""
    txt = _text("system.bot.profile_switched", profile_key=session.profile_key) + "\n" + status
    if creation:
        txt += f"\n\n{creation}"
    await message.reply_text(txt, reply_markup=_main_keyboard())


async def cmd_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    args = list(context.args or [])
    if not args:
        await message.reply_text(_text("error.bot.slot_usage"), reply_markup=_main_keyboard())
        return

    try:
        slot = int(str(args[0]).strip())
    except ValueError:
        await message.reply_text(_text("error.bot.slot_invalid"), reply_markup=_main_keyboard())
        return

    _register_user_activity_from_update(update, context)
    manager = _manager(context)
    display_name = _display_name_from_update(update)
    session = await manager.switch_slot(chat_id=int(chat.id), display_name=display_name, slot=slot)
    async with session.lock:
        status = session.status_text()
        creation = session.creation_status_text() if not (session.state and session.state.player_sheet_ready) else ""
    txt = _text("system.bot.slot_switched", slot=session.slot) + "\n" + status
    if creation:
        txt += f"\n\n{creation}"
    await message.reply_text(txt, reply_markup=_main_keyboard())


async def cmd_dungeon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    session = await _session_from_update(update, context)
    async with session.lock:
        output = await _switch_mode(session, TELEGRAM_MODE_DUNGEON)
    await _send_turn_output(text_target=message, output=output, session=session)


async def cmd_ataryxia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    session = await _session_from_update(update, context)
    async with session.lock:
        output = await _switch_mode(session, TELEGRAM_MODE_ATARYXIA)
    await _send_turn_output(text_target=message, output=output, session=session)


async def cmd_npcs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        _text("system.bot.unsupported_npcs"),
        reply_markup=_main_keyboard(),
    )


async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        _text("system.bot.unsupported_move"),
        reply_markup=_main_keyboard(),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    text = str(message.text or "").strip()
    if not text:
        return

    session = await _session_from_update(update, context)

    if text == BUTTON_MODE_DUNGEON:
        async with session.lock:
            output = await _switch_mode(session, TELEGRAM_MODE_DUNGEON)
        await _send_turn_output(text_target=message, output=output, session=session)
        return
    if text == BUTTON_MODE_ATARYXIA:
        async with session.lock:
            output = await _switch_mode(session, TELEGRAM_MODE_ATARYXIA)
        await _send_turn_output(text_target=message, output=output, session=session)
        return
    if text == BUTTON_STATUS:
        await cmd_status(update, context)
        return
    if text == BUTTON_SAVE:
        await cmd_save(update, context)
        return

    async with session.lock:
        mode = session.telegram_mode()
        if mode == TELEGRAM_MODE_DUNGEON:
            output = TurnOutput(
                text=_text("ui.hint.dungeon_use_buttons"),
                has_pending_trade=False,
            )
        else:
            output = await session.process_ataryxia_message(text)
    await _send_turn_output(text_target=message, output=output, session=session)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()
    if query.message is None:
        return

    data = str(query.data or "")
    session = await _session_from_update(update, context)

    if data == CALLBACK_NOOP:
        return

    if data == CALLBACK_DUNGEON_SKILL_MENU:
        await query.message.reply_text(_text("ui.dungeon.skill_menu_title"), reply_markup=_dungeon_skill_keyboard())
        return

    if data == CALLBACK_DUNGEON_INVENTORY:
        async with session.lock:
            rows = session.dungeon_consumables()
        await query.message.reply_text(_text("ui.dungeon.inventory_title"), reply_markup=_dungeon_inventory_keyboard(rows))
        return

    if data == CALLBACK_DUNGEON_BACK:
        await query.message.reply_text(_text("ui.dungeon.actions_title"), reply_markup=_dungeon_keyboard(session))
        return

    output: TurnOutput | None = None
    async with session.lock:
        session.set_telegram_mode(TELEGRAM_MODE_DUNGEON)
        if data == CALLBACK_DUNGEON_ENTER:
            output = await session.dungeon_enter_or_resume()
        elif data == CALLBACK_DUNGEON_ADVANCE:
            output = await session.dungeon_advance_floor()
        elif data == CALLBACK_DUNGEON_ATTACK:
            output = await session.dungeon_combat_action("attack")
        elif data == CALLBACK_DUNGEON_SKILL_HEAL:
            output = await session.dungeon_combat_action("heal")
        elif data == CALLBACK_DUNGEON_SKILL_SPELL:
            output = await session.dungeon_combat_action("spell")
        elif data == CALLBACK_DUNGEON_SKILL_CORE:
            output = await session.dungeon_combat_action("skill")
        elif data == CALLBACK_DUNGEON_FLEE:
            output = await session.dungeon_combat_action("flee")
        elif data.startswith(CALLBACK_DUNGEON_ITEM_PREFIX):
            item_id = data[len(CALLBACK_DUNGEON_ITEM_PREFIX) :].strip().casefold()
            output = await session.dungeon_use_consumable(item_id)

    if output is None:
        await query.message.reply_text(_text("system.bot.action_unknown"), reply_markup=_main_keyboard())
        return
    await _send_turn_output(text_target=query.message, output=output, session=session)


def build_application(token: str) -> Application:
    slot_count = int(os.getenv("TELEGRAM_SLOT_COUNT", "3") or "3")
    default_slot = int(os.getenv("TELEGRAM_DEFAULT_SLOT", "1") or "1")
    shared_profile_key = str(os.getenv("TELEGRAM_PROFILE_KEY") or "").strip()
    shared_profile_name = str(os.getenv("TELEGRAM_PROFILE_NAME") or "").strip()
    manager = TelegramSessionManager(
        slot_count=slot_count,
        default_slot=default_slot,
        shared_profile_key=shared_profile_key or None,
        shared_profile_name=shared_profile_name or None,
    )

    app = Application.builder().token(token).build()
    app.bot_data["telegram_session_manager"] = manager

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("creation", cmd_creation))
    app.add_handler(CommandHandler("profiles", cmd_profiles))
    app.add_handler(CommandHandler("useprofile", cmd_useprofile))
    app.add_handler(CommandHandler("slot", cmd_slot))
    app.add_handler(CommandHandler("dungeon", cmd_dungeon))
    app.add_handler(CommandHandler("ataryxia", cmd_ataryxia))
    app.add_handler(CommandHandler("npcs", cmd_npcs))
    app.add_handler(CommandHandler("move", cmd_move))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    load_dotenv()
    token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(_text("system.bot.token_missing"))

    app = build_application(token)
    app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
