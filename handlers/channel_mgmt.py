"""
Управление каналами: авто-запись события при добавлении/удалении бота, команда /id
"""
from aiogram import Router, Bot, F
from aiogram.types import ChatMemberUpdated, Message, CallbackQuery, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from database.db import Database
from database.models import UserRole
from filters.chat import clear_group_keyboard
from handlers.support_chat import MENU_BUTTONS
from handlers.mandatory_sub import _is_admin as _is_group_admin, _get_bot_username
from keyboards.menus import get_chat_id_picker_keyboard
from utils.i18n import t, lang_name, SUPPORTED_LANGS, DEFAULT_LANG
from utils.security import tg_escape

router = Router()
db = Database()

_GROUP = F.chat.type.in_({"group", "supergroup"})
_GROUP_CB = F.message.chat.type.in_({"group", "supergroup"})


# ── Бот добавлен / стал администратором в чате ───────────────
@router.my_chat_member(
    F.new_chat_member.status.in_({"administrator", "creator", "member", "restricted"})
)
async def bot_added_to_chat(event: ChatMemberUpdated, bot: Bot):
    """Срабатывает, когда бот добавляется в канал/группу."""
    if event.chat.type not in ("channel", "supergroup", "group"):
        return

    old_status = event.old_chat_member.status if event.old_chat_member else "left"
    new_status = event.new_chat_member.status
    # Пропускаем если статус не изменился или это не значимое изменение
    if old_status == new_status:
        return
    # Пропускаем если это не первое добавление и не повышение до админа
    if old_status not in ("left", "kicked", "restricted") and new_status not in ("administrator", "creator"):
        return

    chat = event.chat

    await db.add_bot_channel_event(
        chat_id=chat.id,
        chat_title=chat.title,
        chat_type=chat.type,
        event_type="added",
        bot_status=new_status,
    )

    # Сохраняем invite-ссылку, использованную при добавлении бота
    if event.invite_link and event.invite_link.invite_link:
        try:
            await db.store_chat_invite_link(chat.id, event.invite_link.invite_link)
        except Exception:
            pass

    # Создаём и сохраняем постоянную ссылку, если бот стал администратором
    if new_status in ("administrator", "creator"):
        try:
            link = await bot.create_chat_invite_link(chat.id, name="bot_mandatory_sub")
            await db.store_chat_invite_link(chat.id, link.invite_link)
        except Exception:
            pass


# ── Бот удалён из чата ────────────────────────────────────────
@router.my_chat_member(F.new_chat_member.status.in_({"left", "kicked"}))
async def bot_removed_from_chat(event: ChatMemberUpdated, bot: Bot):
    if event.chat.type not in ("channel", "supergroup", "group"):
        return

    chat = event.chat
    await db.add_bot_channel_event(
        chat_id=chat.id,
        chat_title=chat.title,
        chat_type=chat.type,
        event_type="removed",
        bot_status=event.new_chat_member.status,
    )


# ── Команда /id в канале или группе ─────────────────────────
@router.channel_post(Command("id"))
async def channel_id_cmd_post(message: Message):
    await _send_chat_id(message)


# ── /id в личке — не показываем бесполезный ID самого личного чата,
# а даём нативный пикер Telegram: выбрать любой канал/группу и узнать его ID
# без необходимости писать /id внутри этого канала/группы (там ведь можно
# быть не админом или вообще не иметь права писать сообщения). ──
@router.message(F.chat.type == "private", Command("id"))
async def cmd_id_private_start(message: Message):
    user = await db.get_user(message.from_user.id) if message.from_user else None
    lang = user.language if user else DEFAULT_LANG
    await message.answer(
        t("id_pick_prompt", lang),
        reply_markup=get_chat_id_picker_keyboard(lang),
    )


@router.message(F.chat.type == "private", F.chat_shared)
async def handle_id_chat_shared(message: Message):
    shared = message.chat_shared
    if shared is None or shared.request_id not in (1, 2):
        return
    user = await db.get_user(message.from_user.id) if message.from_user else None
    lang = user.language if user else DEFAULT_LANG
    title = tg_escape(shared.title) if shared.title else "—"
    await message.answer(
        t("id_picked_result", lang, chat_id=shared.chat_id, title=title),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("id"))
async def channel_id_cmd_message(message: Message):
    await _send_chat_id(message)


async def _send_chat_id(message: Message):
    chat = message.chat
    user = await db.get_user(message.from_user.id) if message.from_user else None
    lang = user.language if user else DEFAULT_LANG

    # Если админ ввёл /id в группе — отправляем ID в личку
    if chat.type in ("group", "supergroup") and message.from_user:
        if user and user.role == UserRole.ADMIN:
            ctype_key = {"group": "chat_type_group", "supergroup": "chat_type_supergroup"}.get(chat.type)
            ctype = t(ctype_key, lang) if ctype_key else chat.type
            await message.bot.send_message(
                message.from_user.id,
                t(
                    "id_card_group_dm", lang,
                    chat_id=chat.id, chat_type=ctype,
                    title=tg_escape(chat.title) or "—",
                ),
                parse_mode="HTML",
            )
            await message.answer(t("id_sent_to_dm_notice", lang))
            return

    ctype_key = {
        "private":    "chat_type_private",
        "group":      "chat_type_group",
        "supergroup": "chat_type_supergroup",
        "channel":    "chat_type_channel",
    }.get(chat.type)
    ctype = t(ctype_key, lang) if ctype_key else chat.type

    await message.answer(
        t(
            "id_card_this_chat", lang,
            chat_id=chat.id, chat_type=ctype, title=chat.title or "—",
        ),
        parse_mode="HTML",
        reply_markup=clear_group_keyboard(message),
    )


@router.message(_GROUP, F.text.in_(MENU_BUTTONS))
async def dismiss_pm_menu_in_group(message: Message):
    """Убрать клавиатуру личного меню, если пользователь нажал её в группе."""
    user = await db.get_user(message.from_user.id) if message.from_user else None
    lang = user.language if user else DEFAULT_LANG
    await message.answer(
        t("pm_menu_only_private_notice", lang),
        reply_markup=ReplyKeyboardRemove(),
    )


def _group_menu_keyboard(lang: str, bot_username: str, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t("group_menu_add_sub", lang), callback_data="sub:add")],
        [InlineKeyboardButton(text=t("group_menu_active_subs", lang), callback_data="sub:back")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text=t("group_menu_autopost", lang), callback_data="grpmenu:autopost")])
        rows.append([InlineKeyboardButton(text=t("group_menu_language", lang), callback_data="grpmenu:language")])
    if bot_username:
        rows.append([InlineKeyboardButton(text=t("group_menu_open_dm", lang), url=f"https://t.me/{bot_username}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_group_menu(message: Message):
    """Меню бота в группе — показывается по /start и /help."""
    lang = await db.get_group_language(message.chat.id)
    bot_username = await _get_bot_username(message.bot)
    is_admin = await _is_group_admin(message.chat.id, message.from_user.id, message.bot)
    await message.answer(
        t("group_menu_title", lang),
        parse_mode="HTML",
        reply_markup=_group_menu_keyboard(lang, bot_username, is_admin),
    )


@router.message(_GROUP, Command("start"))
async def show_start_in_group(message: Message):
    """/start в группе — открывает меню бота.

    handlers/auth.py::cmd_start тоже слушает Command("start"), но main.py
    оборачивает auth.router в private_only() — тот фильтрует все не-личные
    чаты на уровне роутера ДО диспетчеризации по хендлерам. Поэтому групповой
    /start сюда и не доходил бы, будь он там: он должен обрабатываться именно
    здесь, в роутере без private_only."""
    await send_group_menu(message)


@router.message(_GROUP, Command("help"))
async def show_help_in_group(message: Message):
    """/help в группе — то же меню, что и по /start."""
    await send_group_menu(message)


@router.callback_query(_GROUP_CB, F.data == "grpmenu:back")
async def grpmenu_back(callback: CallbackQuery):
    lang = await db.get_group_language(callback.message.chat.id)
    bot_username = await _get_bot_username(callback.bot)
    is_admin = await _is_group_admin(callback.message.chat.id, callback.from_user.id, callback.bot)
    await callback.message.edit_text(
        t("group_menu_title", lang),
        parse_mode="HTML",
        reply_markup=_group_menu_keyboard(lang, bot_username, is_admin),
    )
    await callback.answer()


@router.callback_query(_GROUP_CB, F.data == "grpmenu:language")
async def grpmenu_language(callback: CallbackQuery):
    """Выбор языка группы — доступно только администраторам группы."""
    chat_id = callback.message.chat.id
    lang = await db.get_group_language(chat_id)
    if not await _is_group_admin(chat_id, callback.from_user.id, callback.bot):
        await callback.answer(t("group_menu_admins_only", lang), show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lang_name(code), callback_data=f"setlang_group:{code}")]
        for code in SUPPORTED_LANGS
    ] + [[InlineKeyboardButton(text="🔙", callback_data="grpmenu:back")]])
    await callback.message.edit_text(t("choose_language", lang), reply_markup=kb)
    await callback.answer()


@router.callback_query(_GROUP_CB, F.data.startswith("setlang_group:"))
async def set_group_language_callback(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    current_lang = await db.get_group_language(chat_id)
    if not await _is_group_admin(chat_id, callback.from_user.id, callback.bot):
        await callback.answer(t("group_menu_admins_only", current_lang), show_alert=True)
        return

    new_lang = callback.data.split(":", 1)[1]
    if new_lang not in SUPPORTED_LANGS:
        await callback.answer()
        return

    await db.set_group_language(chat_id, new_lang)
    bot_username = await _get_bot_username(callback.bot)
    is_admin = await _is_group_admin(chat_id, callback.from_user.id, callback.bot)
    await callback.message.edit_text(
        t("language_saved_group", new_lang, lang_name=lang_name(new_lang)),
        parse_mode="HTML",
    )
    await callback.message.answer(
        t("group_menu_title", new_lang),
        parse_mode="HTML",
        reply_markup=_group_menu_keyboard(new_lang, bot_username, is_admin),
    )
    await callback.answer()
