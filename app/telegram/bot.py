from __future__ import annotations
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.telegram.runtime import TelegramGameSession, TelegramSessionManager, TurnOutput

BUTTON_NPCS = "👥 PNJ"
BUTTON_MOVE = "🧭 Deplacer"
BUTTON_STATUS = "📍 Statut"
BUTTON_SAVE = "💾 Sauver"

CALLBACK_TRADE_CONFIRM = "trade:confirm"
CALLBACK_TRADE_CANCEL = "trade:cancel"
CALLBACK_MOVE_NONE = "move:none"


def _main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(BUTTON_NPCS), KeyboardButton(BUTTON_MOVE)],
            [KeyboardButton(BUTTON_STATUS), KeyboardButton(BUTTON_SAVE)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _trade_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirmer", callback_data=CALLBACK_TRADE_CONFIRM),
                InlineKeyboardButton("Annuler", callback_data=CALLBACK_TRADE_CANCEL),
            ]
        ]
    )


def _chunked(values: list[InlineKeyboardButton], size: int = 2) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(values), max(1, size)):
        rows.append(values[i : i + max(1, size)])
    return rows


def _npc_keyboard(session: TelegramGameSession) -> InlineKeyboardMarkup:
    npcs = session.scene_npcs()
    buttons: list[InlineKeyboardButton] = []
    for idx, npc in enumerate(npcs[:20]):
        buttons.append(InlineKeyboardButton(str(npc), callback_data=f"npc:{idx}"))

    if not buttons:
        buttons.append(InlineKeyboardButton("Aucun PNJ ici", callback_data="npc:none"))
    return InlineKeyboardMarkup(_chunked(buttons, size=2))


def _short_scene_title(title: str) -> str:
    text = str(title or "").strip()
    if " - " in text:
        return text.split(" - ", 1)[1].strip()
    return text


def _travel_keyboard(session: TelegramGameSession) -> InlineKeyboardMarkup:
    options = session.travel_options()
    buttons: list[InlineKeyboardButton] = []
    for idx, option in enumerate(options[:20]):
        icon = "🏠" if option.is_building else "➡️"
        locked = "🔒 " if not option.is_open else ""
        label = f"{locked}{icon} {_short_scene_title(option.destination_title)}"
        buttons.append(InlineKeyboardButton(label[:64], callback_data=f"move:{idx}"))

    if not buttons:
        buttons.append(InlineKeyboardButton("Aucun deplacement", callback_data=CALLBACK_MOVE_NONE))
    return InlineKeyboardMarkup(_chunked(buttons, size=1))


def _manager(context: ContextTypes.DEFAULT_TYPE) -> TelegramSessionManager:
    manager = context.application.bot_data.get("telegram_session_manager")
    if isinstance(manager, TelegramSessionManager):
        return manager
    raise RuntimeError("Telegram session manager indisponible.")


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
        raise RuntimeError("Chat Telegram introuvable.")

    display_name = _display_name_from_update(update)

    return await _manager(context).get_session(chat_id=int(chat.id), display_name=display_name)


async def _send_turn_output(
    *,
    update: Update,
    text_target,
    output: TurnOutput,
    session: TelegramGameSession,
) -> None:
    await text_target.reply_text(output.text, reply_markup=_main_keyboard())
    if output.has_pending_trade:
        summary = session.pending_trade_summary() or "Transaction en attente."
        await text_target.reply_text(summary, reply_markup=_trade_keyboard())


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    session = await _session_from_update(update, context)
    async with session.lock:
        session.save()
        status = session.status_text()
        creation = session.creation_status_text() if not (session.state and session.state.player_sheet_ready) else ""

    await message.reply_text(
        "Ataryxia Telegram connecte. Ecris un message pour parler au PNJ actif."
        "\nBoutons: PNJ, Deplacer, Statut, Sauver."
        "\nCommandes utiles: /creation, /profiles, /useprofile, /slot.",
        reply_markup=_main_keyboard(),
    )
    await message.reply_text(status, reply_markup=_main_keyboard())
    if creation:
        await message.reply_text(creation, reply_markup=_main_keyboard())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    session = await _session_from_update(update, context)
    async with session.lock:
        status = session.status_text()
    await message.reply_text(status, reply_markup=_main_keyboard())


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    session = await _session_from_update(update, context)
    async with session.lock:
        session.save()
    await message.reply_text("Sauvegarde effectuee.", reply_markup=_main_keyboard())


async def cmd_npcs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    session = await _session_from_update(update, context)
    await message.reply_text("Choisis le PNJ actif:", reply_markup=_npc_keyboard(session))


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
        await message.reply_text("Aucun profil de sauvegarde detecte.", reply_markup=_main_keyboard())
        return

    lines = ["Profils disponibles:"]
    for row in profiles[:12]:
        key = str(row.get("profile_key") or "").strip()
        name = str(row.get("display_name") or key).strip() or key
        lines.append(f"- {key} ({name})")
    lines.append("Utilise /useprofile <profil_key> pour basculer.")
    await message.reply_text("\n".join(lines), reply_markup=_main_keyboard())


async def cmd_useprofile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    args = list(context.args or [])
    if not args:
        await message.reply_text("Usage: /useprofile <profil_key>", reply_markup=_main_keyboard())
        return

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
    txt = f"Profil actif change: {session.profile_key}\n{status}"
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
        await message.reply_text("Usage: /slot <numero>", reply_markup=_main_keyboard())
        return

    try:
        slot = int(str(args[0]).strip())
    except ValueError:
        await message.reply_text("Slot invalide. Exemple: /slot 1", reply_markup=_main_keyboard())
        return

    manager = _manager(context)
    display_name = _display_name_from_update(update)
    session = await manager.switch_slot(chat_id=int(chat.id), display_name=display_name, slot=slot)
    async with session.lock:
        status = session.status_text()
        creation = session.creation_status_text() if not (session.state and session.state.player_sheet_ready) else ""
    txt = f"Slot actif: {session.slot}\n{status}"
    if creation:
        txt += f"\n\n{creation}"
    await message.reply_text(txt, reply_markup=_main_keyboard())


async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    session = await _session_from_update(update, context)
    async with session.lock:
        options = session.travel_options()
        if session.state is None:
            await message.reply_text("Session non initialisee.", reply_markup=_main_keyboard())
            return
        current_title = session.state.current_scene().title

    if not options:
        await message.reply_text(
            f"Aucun deplacement direct depuis {current_title}.",
            reply_markup=_main_keyboard(),
        )
        return

    lines = [f"Depart: {current_title}", "Choisis une destination:"]
    for option in options[:8]:
        lock = " (ferme)" if not option.is_open else ""
        lines.append(f"- {_short_scene_title(option.destination_title)}{lock}")
    await message.reply_text("\n".join(lines), reply_markup=_travel_keyboard(session))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    text = str(message.text or "").strip()
    if not text:
        return

    if text == BUTTON_NPCS:
        await cmd_npcs(update, context)
        return
    if text == BUTTON_MOVE:
        await cmd_move(update, context)
        return
    if text == BUTTON_STATUS:
        await cmd_status(update, context)
        return
    if text == BUTTON_SAVE:
        await cmd_save(update, context)
        return

    session = await _session_from_update(update, context)
    async with session.lock:
        output = await session.process_user_message(text)

    await _send_turn_output(update=update, text_target=message, output=output, session=session)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    await query.answer()
    if query.message is None:
        return
    data = str(query.data or "")
    session = await _session_from_update(update, context)

    if data == "npc:none":
        await query.message.reply_text("Aucun PNJ disponible ici.", reply_markup=_main_keyboard())
        return

    if data == CALLBACK_MOVE_NONE:
        await query.message.reply_text("Aucun deplacement disponible.", reply_markup=_main_keyboard())
        return

    if data.startswith("npc:"):
        raw_idx = data.split(":", 1)[1]
        try:
            idx = int(raw_idx)
        except ValueError:
            await query.message.reply_text("Selection PNJ invalide.", reply_markup=_main_keyboard())
            return

        npcs = session.scene_npcs()
        if idx < 0 or idx >= len(npcs):
            await query.message.reply_text("PNJ introuvable.", reply_markup=_main_keyboard())
            return

        async with session.lock:
            result = await session.select_npc(npcs[idx])

        await query.message.reply_text(result, reply_markup=_main_keyboard())
        return

    if data.startswith("move:"):
        raw_idx = data.split(":", 1)[1]
        try:
            idx = int(raw_idx)
        except ValueError:
            await query.message.reply_text("Selection de deplacement invalide.", reply_markup=_main_keyboard())
            return

        async with session.lock:
            output = await session.travel_by_index(idx)

        await _send_turn_output(update=update, text_target=query.message, output=output, session=session)
        return

    if data in {CALLBACK_TRADE_CONFIRM, CALLBACK_TRADE_CANCEL}:
        async with session.lock:
            if data == CALLBACK_TRADE_CONFIRM:
                output = await session.confirm_pending_trade()
            else:
                output = await session.cancel_pending_trade()

        await _send_turn_output(update=update, text_target=query.message, output=output, session=session)
        return

    await query.message.reply_text("Action non reconnue.", reply_markup=_main_keyboard())


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
    app.add_handler(CommandHandler("npcs", cmd_npcs))
    app.add_handler(CommandHandler("move", cmd_move))
    app.add_handler(CommandHandler("creation", cmd_creation))
    app.add_handler(CommandHandler("profiles", cmd_profiles))
    app.add_handler(CommandHandler("useprofile", cmd_useprofile))
    app.add_handler(CommandHandler("slot", cmd_slot))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    load_dotenv()
    token = str(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Variable TELEGRAM_BOT_TOKEN manquante dans l'environnement.")

    app = build_application(token)
    app.run_polling(close_loop=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
