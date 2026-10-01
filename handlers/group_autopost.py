"""
Автопост в группах: повторная отправка сообщения с заданным интервалом.
В одной группе может быть несколько одновременно активных автопостов.

Команды:
  /автопост (/autopost)       — настроить автопост
  /автопост stop              — остановить ВСЕ автопосты в текущей группе

Управление конкретными автопостами (добавить/посмотреть/удалить один) —
через инлайн-меню группы (кнопка "🔁 Автопост").
"""
import re
from datetime import datetime, timedelta

from aiogram import Router, Bot, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.db import Database
from filters.chat import clear_group_keyboard
from utils.i18n import t, DEFAULT_LANG

router = Router()
db = Database()

MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 7 * 24 * 3600
MAX_AUTOPOSTS_PER_GROUP = 10

# Служебный аккаунт Telegram для сообщений от анонимных админов (см.
# handlers/mandatory_sub.py::GROUP_ANONYMOUS_ADMIN_ID — та же логика).
GROUP_ANONYMOUS_ADMIN_ID = 1087968824


class GroupAutopostStates(StatesGroup):
    waiting_for_message = State()
    waiting_for_interval = State()


async def _is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id == GROUP_ANONYMOUS_ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


async def _group_lang(chat_id: int) -> str:
    lang = await db.get_group_language(chat_id)
    return lang or DEFAULT_LANG


def _parse_interval(text: str) -> int | None:
    """Разобрать интервал. Возвращает секунды или None."""
    raw = text.strip().lower().replace(",", ".")
    if not raw:
        return None

    patterns = [
        (r"^(\d+(?:\.\d+)?)\s*(?:с|сек|sec|s)$", 1),
        (r"^(\d+(?:\.\d+)?)\s*(?:м|мин|хв|min|m)$", 60),
        (r"^(\d+(?:\.\d+)?)\s*(?:ч|час|г|год|hour|h)$", 3600),
        (r"^(\d+(?:\.\d+)?)\s*(?:д|дн|day|d)$", 86400),
        (r"^(\d+(?:\.\d+)?)$", 60),
    ]
    for pattern, multiplier in patterns:
        match = re.match(pattern, raw)
        if match:
            value = float(match.group(1))
            if value <= 0:
                return None
            return int(value * multiplier)
    return None


def _format_interval(seconds: int, lang: str = DEFAULT_LANG) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} {t('unit_days_short', lang)}"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} {t('unit_hours_short', lang)}"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} {t('unit_minutes_short', lang)}"
    return f"{seconds} {t('unit_seconds_short', lang)}"


async def _handle_autopost_command_in_state(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> bool:
    """Обработать /автопост stop и /cancel во время настройки. True — если обработано."""
    if not message.text:
        return False
    text = message.text.strip().lower()
    if (text.startswith("/автопост") or text.startswith("/autopost")) and "stop" in text:
        await _stop_autopost(message, bot, state)
        return True
    if text.startswith("/cancel"):
        lang = await _group_lang(message.chat.id)
        await state.clear()
        await message.answer(t("autopost_cancelled", lang))
        return True
    return False


async def _stop_autopost(message: Message, bot: Bot, state: FSMContext | None = None) -> None:
    """Остановить ВСЕ автопосты в группе (команда /автопост stop)."""
    if state:
        await state.clear()
    chat_id = message.chat.id
    if not message.from_user:
        return
    lang = await _group_lang(chat_id)

    if not await _is_group_admin(bot, chat_id, message.from_user.id):
        await message.answer(t("autopost_stop_admin_only", lang))
        return

    stopped = await db.deactivate_group_autopost(chat_id)
    if stopped:
        await message.answer(t("autopost_stopped", lang))
        await db.log_action(
            message.from_user.id,
            "group_autopost_stopped",
            f"chat_id={chat_id}",
        )
    else:
        await message.answer(t("autopost_none_active", lang))


async def _begin_autopost_wizard(chat_id: int, state: FSMContext) -> None:
    await state.set_state(GroupAutopostStates.waiting_for_message)
    await state.update_data(target_chat_id=chat_id)


async def _autopost_menu_content(chat_id: int, lang: str):
    """Построить текст и клавиатуру меню автопостов группы."""
    posts = await db.get_active_group_autoposts(chat_id)

    if posts:
        text = t("autopost_menu_title_with_count", lang, count=len(posts))
    else:
        text = t("autopost_menu_title_empty", lang)

    builder = InlineKeyboardBuilder()
    for post in posts:
        interval_label = _format_interval(post["interval_seconds"], lang)
        builder.row(
            InlineKeyboardButton(
                text=t("autopost_item_btn", lang, interval=interval_label),
                callback_data=f"autopost:preview:{post['id']}",
            ),
            InlineKeyboardButton(
                text=t("autopost_btn_delete", lang),
                callback_data=f"autopost:delete:{post['id']}",
            ),
        )
    builder.row(InlineKeyboardButton(text=t("autopost_btn_add", lang), callback_data="autopost:add"))
    builder.row(InlineKeyboardButton(text=t("btn_back_to_group_menu", lang), callback_data="grpmenu:back"))
    return text, builder.as_markup()


@router.callback_query(F.data == "grpmenu:autopost")
async def show_autopost_menu(callback: CallbackQuery):
    """Показать меню автопостов группы (кнопка в меню группы)."""
    if not callback.message:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    lang = await _group_lang(chat_id)

    if not await _is_group_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer(t("autopost_config_admin_only", lang), show_alert=True)
        return

    text, kb = await _autopost_menu_content(chat_id, lang)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "autopost:add")
async def start_autopost_add(callback: CallbackQuery, state: FSMContext):
    """Начать настройку нового автопоста из инлайн-меню группы."""
    if not callback.message:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    lang = await _group_lang(chat_id)

    if not await _is_group_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer(t("autopost_config_admin_only", lang), show_alert=True)
        return

    existing = await db.get_active_group_autoposts(chat_id)
    if len(existing) >= MAX_AUTOPOSTS_PER_GROUP:
        await callback.answer(
            t("autopost_limit_reached", lang, max=MAX_AUTOPOSTS_PER_GROUP),
            show_alert=True,
        )
        return

    await _begin_autopost_wizard(chat_id, state)
    await callback.message.edit_text(t("autopost_setup_prompt", lang), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("autopost:preview:"))
async def preview_autopost(callback: CallbackQuery):
    """Показать сохранённое сообщение автопоста ещё раз (чтобы узнать, какой это)."""
    if not callback.message:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    lang = await _group_lang(chat_id)

    if not await _is_group_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer(t("autopost_config_admin_only", lang), show_alert=True)
        return

    post_id = int(callback.data.split(":")[2])
    post = await db.get_group_autopost_by_id(post_id)
    if not post or post["chat_id"] != chat_id:
        await callback.answer(t("autopost_not_found_alert", lang), show_alert=True)
        return

    try:
        await callback.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=post["from_chat_id"],
            message_id=post["message_id"],
        )
    except Exception:
        pass
    await callback.answer(t("autopost_preview_alert", lang))


@router.callback_query(F.data.startswith("autopost:delete:"))
async def delete_autopost(callback: CallbackQuery):
    """Удалить конкретный автопост из инлайн-меню группы."""
    if not callback.message:
        await callback.answer()
        return
    chat_id = callback.message.chat.id
    lang = await _group_lang(chat_id)

    if not await _is_group_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer(t("autopost_config_admin_only", lang), show_alert=True)
        return

    post_id = int(callback.data.split(":")[2])
    post = await db.get_group_autopost_by_id(post_id)
    if not post or post["chat_id"] != chat_id:
        await callback.answer(t("autopost_not_found_alert", lang), show_alert=True)
        return

    await db.deactivate_group_autopost_by_id(post_id)
    await db.log_action(callback.from_user.id, "group_autopost_deleted", f"chat_id={chat_id}, post_id={post_id}")
    await callback.answer(t("autopost_deleted_alert", lang))

    text, kb = await _autopost_menu_content(chat_id, lang)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@router.message(
    F.chat.type.in_({"group", "supergroup"}),
    Command("автопост", "autopost"),
)
async def cmd_group_autopost(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    bot: Bot,
):
    if not message.from_user:
        return
    lang = await _group_lang(message.chat.id)

    if command.args and command.args.strip().lower() == "stop":
        await _stop_autopost(message, bot, state)
        return

    if not await _is_group_admin(bot, message.chat.id, message.from_user.id):
        await message.answer(t("autopost_config_admin_only", lang))
        return

    existing = await db.get_active_group_autoposts(message.chat.id)
    if len(existing) >= MAX_AUTOPOSTS_PER_GROUP:
        await message.answer(
            t("autopost_limit_reached", lang, max=MAX_AUTOPOSTS_PER_GROUP),
            parse_mode="HTML",
        )
        return

    await _begin_autopost_wizard(message.chat.id, state)
    await message.answer(
        t("autopost_setup_prompt", lang),
        parse_mode="HTML",
    )


@router.message(
    GroupAutopostStates.waiting_for_message,
    F.chat.type.in_({"group", "supergroup"}),
)
async def process_autopost_message(message: Message, state: FSMContext, bot: Bot):
    if not message.from_user:
        return

    data = await state.get_data()
    target_chat_id = data.get("target_chat_id")
    if target_chat_id != message.chat.id:
        return

    if not await _is_group_admin(bot, message.chat.id, message.from_user.id):
        await state.clear()
        return

    if await _handle_autopost_command_in_state(message, state, bot):
        return

    if message.text and message.text.startswith("/"):
        return

    lang = await _group_lang(message.chat.id)
    await state.update_data(
        message_id=message.message_id,
        from_chat_id=message.chat.id,
    )
    await state.set_state(GroupAutopostStates.waiting_for_interval)
    await message.answer(
        t("autopost_interval_prompt", lang, min_interval=_format_interval(MIN_INTERVAL_SECONDS, lang)),
        parse_mode="HTML",
    )


@router.message(
    GroupAutopostStates.waiting_for_interval,
    F.chat.type.in_({"group", "supergroup"}),
)
async def process_autopost_interval(message: Message, state: FSMContext, bot: Bot):
    if not message.from_user:
        return

    data = await state.get_data()
    target_chat_id = data.get("target_chat_id")
    if target_chat_id != message.chat.id:
        return

    if not await _is_group_admin(bot, message.chat.id, message.from_user.id):
        await state.clear()
        return

    if await _handle_autopost_command_in_state(message, state, bot):
        return

    if not message.text or message.text.startswith("/"):
        return

    lang = await _group_lang(message.chat.id)

    interval_seconds = _parse_interval(message.text)
    if interval_seconds is None:
        await message.answer(
            t("autopost_interval_parse_error", lang),
            parse_mode="HTML",
        )
        return

    if interval_seconds < MIN_INTERVAL_SECONDS:
        await message.answer(
            t("autopost_interval_too_small", lang, interval=_format_interval(MIN_INTERVAL_SECONDS, lang)),
        )
        return

    if interval_seconds > MAX_INTERVAL_SECONDS:
        await message.answer(
            t("autopost_interval_too_large", lang, interval=_format_interval(MAX_INTERVAL_SECONDS, lang)),
        )
        return

    await state.update_data(interval_seconds=interval_seconds)

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=t("btn_confirm_autopost", lang),
            callback_data=f"grepeat:confirm:{message.chat.id}",
        ),
        InlineKeyboardButton(
            text=t("btn_cancel", lang),
            callback_data=f"grepeat:cancel:{message.chat.id}",
        ),
    ]])

    interval_label = _format_interval(interval_seconds, lang)
    cmd_name = t("autopost_cmd_name", lang)
    await message.answer(
        t("autopost_confirm_title", lang, interval=interval_label, cmd=cmd_name),
        reply_markup=confirm_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("grepeat:cancel:"))
async def cancel_group_autopost(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    chat_id = int(callback.data.split(":")[2])
    lang = await _group_lang(chat_id)
    if callback.message.chat.id != chat_id:
        await callback.answer(t("autopost_wrong_chat", lang), show_alert=True)
        return

    if not await _is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer(t("autopost_no_access", lang), show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(t("autopost_cancelled", lang), reply_markup=None)
    await callback.answer()


@router.callback_query(F.data.startswith("grepeat:confirm:"))
async def confirm_group_autopost(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    chat_id = int(callback.data.split(":")[2])
    lang = await _group_lang(chat_id)
    cmd_name = t("autopost_cmd_name", lang)
    if callback.message.chat.id != chat_id:
        await callback.answer(t("autopost_wrong_chat", lang), show_alert=True)
        return

    if not await _is_group_admin(bot, chat_id, callback.from_user.id):
        await callback.answer(t("autopost_no_access", lang), show_alert=True)
        return

    data = await state.get_data()
    if data.get("target_chat_id") != chat_id:
        await callback.answer(t("autopost_session_expired", lang, cmd=cmd_name), show_alert=True)
        return

    message_id = data.get("message_id")
    from_chat_id = data.get("from_chat_id")
    interval_seconds = data.get("interval_seconds")

    if not all([message_id, from_chat_id, interval_seconds]):
        await callback.answer(t("autopost_data_not_found", lang), show_alert=True)
        await state.clear()
        return

    await state.clear()

    existing = await db.get_active_group_autoposts(chat_id)
    if len(existing) >= MAX_AUTOPOSTS_PER_GROUP:
        await callback.message.edit_text(
            t("autopost_limit_reached", lang, max=MAX_AUTOPOSTS_PER_GROUP),
            reply_markup=None,
            parse_mode="HTML",
        )
        await callback.answer()
        return

    now = datetime.now()
    next_run = now + timedelta(seconds=interval_seconds)

    post_id = await db.create_group_autopost(
        chat_id=chat_id,
        from_chat_id=from_chat_id,
        message_id=message_id,
        interval_seconds=interval_seconds,
        created_by=callback.from_user.id,
        next_run_at=next_run.isoformat(),
    )

    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )
        await db.update_group_autopost_schedule(
            post_id,
            last_run_at=now.isoformat(),
            next_run_at=next_run.isoformat(),
        )
    except Exception as e:
        await db.deactivate_group_autopost_by_id(post_id)
        await callback.message.edit_text(
            t("autopost_send_failed", lang, error=e),
            reply_markup=None,
        )
        await callback.answer()
        return

    interval_label = _format_interval(interval_seconds, lang)
    await callback.message.edit_text(
        t("autopost_started", lang, interval=interval_label, cmd=cmd_name),
        reply_markup=None,
        parse_mode="HTML",
    )
    await callback.answer(t("autopost_started_alert", lang))

    await db.log_action(
        callback.from_user.id,
        "group_autopost_started",
        f"chat_id={chat_id}, interval={interval_seconds}s",
    )
