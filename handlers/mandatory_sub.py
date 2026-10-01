"""
Обязательная подписка в группах.
"""
import re
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from handlers.auth import db
from keyboards.menus import get_admin_channel_picker_keyboard
from utils.security import tg_escape
from utils.i18n import t, DEFAULT_LANG

router = Router()

# Служебный аккаунт Telegram, от имени которого приходят сообщения админов
# с включённой настройкой «Оставаться анонимным» (Remain Anonymous). В этом
# случае message.from_user.id — НЕ реальный ID админа, а этот фиксированный
# ID (одинаковый во всех чатах и для всех ботов). Telegram не позволяет
# никому, кроме настоящего админа/владельца чата, писать от этого имени —
# поэтому такой ID можно доверять как признак админа напрямую.
GROUP_ANONYMOUS_ADMIN_ID = 1087968824

# ─── Состояния ────────────────────────────────────────────────────────────────

class MSubStates(StatesGroup):
    ref = State()
    invite = State()
    duration = State()
    subbot_ref = State()
    forward_ref = State()
    pick_chat = State()

# ─── Фильтры для групп ────────────────────────────────────────────────────────

_MSG = F.chat.type.in_({"group", "supergroup"})
_CB = F.message.chat.type.in_({"group", "supergroup"})


def _cancel_kb(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    """Кнопка отмены для любого шага мастера добавления — чтобы не было тупиков
    без возможности вернуться в меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="sub:cancel")],
    ])


_DURATION_UNIT_MINUTES = {
    "h": 60,
    "d": 1440,
    "w": 10080,
    "m": 43200,   # месяц ~ 30 дней
    "y": 525600,  # год ~ 365 дней
}
_DURATION_RE = re.compile(r"^(\d+)\s*([hdwmy])$")


def _parse_duration_shorthand(text: str) -> Optional[int]:
    """Разобрать срок подписки: голое число — минуты; либо короткая запись
    вида 1h/1d/1w/1m/1y (часы/дни/недели/месяцы/годы). Возвращает минуты
    или None, если формат не распознан."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    match = _DURATION_RE.match(text)
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * _DURATION_UNIT_MINUTES[unit]


@router.callback_query(_CB, F.data == "sub:cancel")
async def sub_cancel(callback: CallbackQuery, state: FSMContext):
    """Отменить мастер добавления на любом шаге и вернуться в меню группы."""
    await state.clear()
    from handlers.channel_mgmt import _group_menu_keyboard
    from utils.i18n import t
    chat_id = callback.message.chat.id
    lang = await db.get_group_language(chat_id)
    bot_username = await _get_bot_username(callback.bot)
    is_admin = await _is_admin(chat_id, callback.from_user.id, callback.bot)
    await callback.message.edit_text(
        t("group_menu_title", lang),
        parse_mode="HTML",
        reply_markup=_group_menu_keyboard(lang, bot_username, is_admin),
    )
    await callback.answer()


async def _is_admin(chat_id: int, user_id: int, bot) -> bool:
    if user_id == GROUP_ANONYMOUS_ADMIN_ID:
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def _get_bot_username(bot) -> str:
    """Получить username бота."""
    me = await bot.get_me()
    return me.username or ""


def _parse_link(text: str) -> dict:
    """Извлекает channel_ref, title, invite_link из ссылки/тега."""
    text = text.strip()
    res = {"ref": text, "title": text, "invite": None}
    if "t.me/+" in text:
        ref = text.split("t.me/+")[-1].split("?")[0].split()[0]
        res["ref"] = f"+{ref}"
        res["invite"] = text
    elif "t.me/" in text:
        # .split("?")[0] обязателен — иначе реферальные ссылки вида
        # t.me/username?start=xyz ломают извлечение username (?start=xyz
        # попадал бы в ref) и канал ошибочно считался "ненайденным".
        ref = text.split("t.me/")[-1].split("?")[0].split("/")[0].split()[0].lstrip("@")
        res["ref"] = ref
        res["title"] = f"@{ref}"
    elif text.startswith("@"):
        res["ref"] = text.lstrip("@")
        res["title"] = text
    else:
        res["ref"] = text.lstrip("@")
        res["title"] = f"@{text.lstrip('@')}"
    return res


async def _init_invite_links(bot):
    """Populate bot_chat_invites for all chats where bot is admin."""
    try:
        events = await db.get_active_bot_channels()
        for ev in events:
            cid = ev["chat_id"]
            try:
                link = await bot.create_chat_invite_link(cid, name="bot_mandatory_sub")
                await db.store_chat_invite_link(cid, link.invite_link)
            except Exception:
                pass
    except Exception:
        pass


async def _resolve_invite_link(invite_url: str, bot=None) -> Optional[str]:
    """Найти ID канала по сохранённой пригласительной ссылке."""
    try:
        chat_id = await db.find_chat_by_invite_link(invite_url)
        if chat_id:
            return str(chat_id)
    except Exception:
        pass
    return None


async def _check_sub(bot, user_id: int, sub) -> bool:
    ref = sub.channel_ref
    if ref.startswith("+"):
        # Пробуем найти числовой chat_id через сохранённую invite-ссылку
        try:
            invite_url = sub.invite_link or f"https://t.me/{ref}"
            chat_id = await db.find_chat_by_invite_link(invite_url)
            if chat_id:
                m = await bot.get_chat_member(int(chat_id), user_id)
                return m.status in ("member", "administrator", "creator", "restricted")
        except Exception:
            pass
        return True  # Не удалось определить чат — пропускаем
    if ref == "bot:start":
        user = await db.get_user(user_id)
        return user is not None
    try:
        cid = int(ref) if ref.lstrip("-").isdigit() else f"@{ref}"
        m = await bot.get_chat_member(cid, user_id)
        return m.status in ("member", "administrator", "creator", "restricted")
    except Exception:
        # Если не удалось проверить (бот не может читать участников) — не блокируем
        if ref.lstrip("-").isdigit():
            return True
        return False


async def _bot_is_admin(bot, ref: str) -> bool:
    try:
        cid = int(ref) if ref.lstrip("-").isdigit() else f"@{ref}"
        m = await bot.get_chat_member(cid, bot.id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


async def _show_pick_chat(target, state: FSMContext, bot, current_group_id: int):
    """Показать список групп/каналов где бот является администратором."""
    lang = await db.get_group_language(current_group_id)
    channels = await db.get_active_bot_channels()
    # Дедуплицируем по chat_id, проверяем реальный статус через API
    seen = set()
    admin_chats = []
    for c in channels:
        if c["chat_id"] == current_group_id:
            continue
        if c["chat_id"] in seen:
            continue
        seen.add(c["chat_id"])
        if await _bot_is_admin(bot, str(c["chat_id"])):
            admin_chats.append(c)

    if not admin_chats:
        text = t("msub_no_available_channels", lang)
        if isinstance(target, Message):
            await target.answer(text, parse_mode="HTML")
        else:
            await target.message.edit_text(text, parse_mode="HTML")
        await state.set_state(MSubStates.forward_ref)
        return

    text = t("msub_pick_chat_prompt", lang)
    buttons = []
    for c in admin_chats[:20]:  # максимум 20 кнопок
        title = c.get("chat_title") or str(c["chat_id"])
        ctype = "📢" if c.get("chat_type") == "channel" else "👥"
        buttons.append([InlineKeyboardButton(
            text=f"{ctype} {title}",
            callback_data=f"sub:pick:{c['chat_id']}",
        )])
    buttons.append([InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="sub:pick:cancel")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await state.set_state(MSubStates.pick_chat)


async def _show_menu(target, subs):
    """Показать меню управления подписками (работает и от Message, и от CallbackQuery)."""
    chat_id = target.chat.id if isinstance(target, Message) else target.message.chat.id
    lang = await db.get_group_language(chat_id)

    text = t("msub_menu_title", lang) + "\n\n"
    if subs:
        text += t("msub_total_count", lang, count=len(subs)) + "\n"
        for s in subs:
            exp = t("msub_expires_at", lang, date=s.expires_at[:10]) if s.expires_at else t("msub_forever_suffix", lang)
            warn = ""
            if s.channel_ref.startswith("+"):
                warn = t("msub_check_impossible_warn", lang)
            elif s.channel_ref == "bot:start":
                warn = t("msub_bot_warn", lang)
            text += f"  • {s.title}{exp}{warn}\n"
    else:
        text += t("msub_nothing_added_yet", lang)
    text += t("msub_members_notice", lang)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("msub_btn_add", lang), callback_data="sub:add")],
        [InlineKeyboardButton(text=t("msub_btn_remove", lang), callback_data="sub:rm")],
        [InlineKeyboardButton(text=t("btn_back_to_group_menu", lang), callback_data="grpmenu:back")],
    ])
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


async def _get_sub_link(bot, sub) -> Optional[str]:
    """Получить ссылку для подписки (invite_link или сгенерировать)."""
    if sub.invite_link:
        return sub.invite_link
    ref = sub.channel_ref
    if ref.startswith("+") or ref == "bot:start":
        return None
    try:
        cid = int(ref) if ref.lstrip("-").isdigit() else f"@{ref}"
        inv = await bot.create_chat_invite_link(cid, member_limit=1)
        return inv.invite_link
    except Exception:
        return f"https://t.me/{ref.lstrip('@')}"


# ═══════════════════════════════════════════════════════════════════════════════
# УПРАВЛЕНИЕ
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(_MSG, Command("sub", "подписки"))
async def cmd_sub(message: Message):
    """Меню управления подписками."""
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return
    subs = await db.get_group_mandatory_subs(message.chat.id)
    await _show_menu(message, subs)


@router.message(_MSG, Command("subbot", "подпискабот"))
async def cmd_subbot(message: Message, state: FSMContext):
    """Добавить бота как обязательную подписку."""
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return
    lang = await db.get_group_language(message.chat.id)
    existing = await db.get_group_mandatory_subs(message.chat.id)
    if any(s.channel_ref == "bot:start" for s in existing):
        return await message.answer(t("msub_bot_already_added", lang))
    username = await _get_bot_username(message.bot)
    await message.answer(
        t("msub_add_bot_prompt", lang, username=username),
        parse_mode="HTML",
        reply_markup=_cancel_kb(lang),
    )
    await state.set_state(MSubStates.subbot_ref)


@router.message(_MSG, MSubStates.subbot_ref)
async def subbot_ref(message: Message, state: FSMContext):
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return await state.clear()
    lang = await db.get_group_language(message.chat.id)
    text = message.text.strip().lower()
    invite = None
    if text not in ("0", "пропустить", "пропустити", "skip", "-"):
        invite = message.text.strip()
    username = await _get_bot_username(message.bot)
    # Дальше — тот же шаг длительности, что и для обычных каналов (MSubStates.duration
    # /add_duration), чтобы бота как обязательную подписку тоже можно было добавить
    # на ограниченное время, а не только навсегда.
    await state.update_data(ref="bot:start", title=t("msub_our_bot_title", lang, username=username), invite=invite)
    await message.answer(
        t("msub_enter_duration_prompt", lang),
        parse_mode="HTML",
        reply_markup=_cancel_kb(lang),
    )
    await state.set_state(MSubStates.duration)


@router.callback_query(_CB, F.data == "sub:add")
async def add_start(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.message.chat.id, callback.from_user.id, callback.bot):
        lang = await db.get_group_language(callback.message.chat.id)
        return await callback.answer(t("msub_no_access", lang), show_alert=True)
    lang = await db.get_group_language(callback.message.chat.id)
    await callback.message.edit_text(
        t("msub_add_start_prompt", lang),
        parse_mode="HTML",
        reply_markup=_cancel_kb(lang),
    )
    await callback.message.answer(
        t("pick_channel_hint", lang),
        reply_markup=get_admin_channel_picker_keyboard(lang, request_id_base=20),
    )
    await state.set_state(MSubStates.ref)
    await callback.answer()


@router.message(_MSG, MSubStates.ref, F.chat_shared)
async def add_ref_picked(message: Message, state: FSMContext):
    """Обработать канал/группу, выбранную через нативный пикер Telegram —
    закрывает всю проблему с приватными ссылками: пикер сам показывает chat_id,
    без ручного поиска ID и без ограничений на приватность канала/группы."""
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return await state.clear()
    lang = await db.get_group_language(message.chat.id)
    shared = message.chat_shared
    if shared is None or shared.request_id not in (20, 21):
        return

    # Пикер уже отфильтровал по правам (бот и пользователь — админы), но
    # перепроверяем через API на случай устаревших данных клиента.
    ok = await _bot_is_admin(message.bot, str(shared.chat_id))
    if not ok:
        return await message.answer(
            t("msub_bot_not_admin_named", lang, title=shared.title or str(shared.chat_id)),
            parse_mode="HTML",
            reply_markup=_cancel_kb(lang),
        )

    ref = str(shared.chat_id)
    title = f"@{shared.username}" if shared.username else (shared.title or ref)

    existing = await db.get_group_mandatory_subs(message.chat.id)
    if any(s.channel_ref == ref for s in existing):
        return await message.answer(t("msub_already_added", lang, title=title), parse_mode="HTML")

    await state.update_data(ref=ref, title=title)
    await message.answer(
        t("msub_ask_invite_link", lang, title=title),
        parse_mode="HTML",
        reply_markup=_cancel_kb(lang),
    )
    await state.set_state(MSubStates.invite)


@router.message(_MSG, MSubStates.ref)
async def add_ref(message: Message, state: FSMContext):
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return await state.clear()
    lang = await db.get_group_language(message.chat.id)
    p = _parse_link(message.text)

    if p["ref"].startswith("+"):
        # Приватная ссылка — Telegram Bot API не позволяет получить chat_id из t.me/+ ссылки.
        # Просим числовой ID.
        await state.update_data(invite=p["invite"], title=p["title"])
        await message.answer(
            t("msub_private_link_prompt", lang),
            parse_mode="HTML",
            reply_markup=_cancel_kb(lang),
        )
        await state.set_state(MSubStates.forward_ref)
        return

    ok = await _bot_is_admin(message.bot, p["ref"])
    if not ok:
        return await message.answer(
            t("msub_bot_not_admin_in", lang, title=p["title"]),
            parse_mode="HTML",
            reply_markup=_cancel_kb(lang),
        )

    # Для числовых ID получаем реальное название из Telegram
    ref = p["ref"]
    title = p["title"]
    if ref.lstrip("-").isdigit():
        try:
            chat = await message.bot.get_chat(int(ref))
            title = f"@{chat.username}" if chat.username else (chat.title or title)
        except Exception:
            pass

    existing = await db.get_group_mandatory_subs(message.chat.id)
    if any(s.channel_ref == ref for s in existing):
        return await message.answer(t("msub_already_added", lang, title=title), parse_mode="HTML")
    await state.update_data(ref=ref, title=title)
    await message.answer(
        t("msub_ask_invite_link", lang, title=title),
        parse_mode="HTML",
        reply_markup=_cancel_kb(lang),
    )
    await state.set_state(MSubStates.invite)


@router.message(_MSG, MSubStates.forward_ref)
async def add_forward(message: Message, state: FSMContext):
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return await state.clear()
    lang = await db.get_group_language(message.chat.id)

    text = message.text.strip() if message.text else ""

    # Ожидаем числовой ID — без него нельзя проверить, что бот состоит в группе,
    # поэтому больше нет варианта "добавить без проверки".
    if not text.lstrip("-").isdigit():
        return await message.answer(
            t("msub_need_numeric_id", lang),
            parse_mode="HTML",
            reply_markup=_cancel_kb(lang),
        )

    try:
        chat = await message.bot.get_chat(int(text))
    except Exception:
        return await message.answer(
            t("msub_chat_not_found", lang),
            parse_mode="HTML",
            reply_markup=_cancel_kb(lang),
        )

    ok = await _bot_is_admin(message.bot, str(chat.id))
    if not ok:
        return await message.answer(
            t("msub_bot_not_admin_named", lang, title=chat.title),
            parse_mode="HTML",
            reply_markup=_cancel_kb(lang),
        )

    title = f"@{chat.username}" if chat.username else (chat.title or str(chat.id))
    data = await state.get_data()
    invite_url = data.get("invite", "")
    # Сохраняем маппинг invite_link → chat_id для будущих проверок
    if invite_url:
        try:
            await db.store_chat_invite_link(chat.id, invite_url)
        except Exception:
            pass

    await state.update_data(ref=str(chat.id), title=title)
    await message.answer(
        t("msub_confirm_ask_duration", lang, title=title),
        parse_mode="HTML",
        reply_markup=_cancel_kb(lang),
    )
    await state.set_state(MSubStates.duration)


@router.message(_MSG, MSubStates.invite)
async def add_invite(message: Message, state: FSMContext):
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return await state.clear()
    lang = await db.get_group_language(message.chat.id)
    text = message.text.strip().lower()
    invite = None
    if text not in ("0", "пропустить", "пропустити", "skip", "-"):
        invite = message.text.strip()
    await state.update_data(invite=invite)
    await message.answer(
        t("msub_enter_duration_prompt", lang),
        parse_mode="HTML",
        reply_markup=_cancel_kb(lang),
    )
    await state.set_state(MSubStates.duration)


async def _finish_add_sub(chat_id: int, added_by: int, state: FSMContext, lang: str, minutes: int, answer_fn):
    """Завершить мастер добавления обязательной подписки — создать запись в БД
    и отчитаться, независимо от того, пришёл срок кнопкой-пресетом или текстом."""
    data = await state.get_data()
    sub = await db.add_group_mandatory_sub(
        group_id=chat_id,
        channel_ref=data["ref"],
        title=data["title"],
        duration_minutes=minutes,
        invite_link=data.get("invite"),
        added_by=added_by,
    )
    # Если ref — числовой chat_id, сохраняем invite link для будущего поиска
    ref = data["ref"]
    if isinstance(ref, str) and ref.lstrip("-").isdigit():
        inv = data.get("invite")
        if inv:
            try:
                await db.store_chat_invite_link(int(ref), inv)
            except Exception:
                pass
    exp = t("msub_forever", lang) if minutes == 0 else t("msub_minutes_suffix", lang, minutes=minutes)
    await answer_fn(
        t("msub_added_success", lang, title=sub.title.replace('@', ''), exp=exp),
        parse_mode="HTML",
    )
    await state.clear()


@router.message(_MSG, MSubStates.duration)
async def add_duration(message: Message, state: FSMContext):
    if not await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return await state.clear()
    lang = await db.get_group_language(message.chat.id)
    minutes = _parse_duration_shorthand(message.text or "")
    if minutes is None or minutes < 0:
        return await message.answer(
            t("msub_invalid_number", lang),
            parse_mode="HTML",
            reply_markup=_cancel_kb(lang),
        )
    await _finish_add_sub(message.chat.id, message.from_user.id, state, lang, minutes, message.answer)


@router.callback_query(_CB, F.data == "sub:rm")
async def rm_list(callback: CallbackQuery):
    lang = await db.get_group_language(callback.message.chat.id)
    if not await _is_admin(callback.message.chat.id, callback.from_user.id, callback.bot):
        return await callback.answer(t("msub_no_access", lang), show_alert=True)
    subs = await db.get_group_mandatory_subs(callback.message.chat.id)
    if not subs:
        return await callback.message.edit_text(
            t("msub_no_subs_to_remove", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("btn_back", lang), callback_data="sub:back")],
            ]),
        )
    kb = [[InlineKeyboardButton(
        text=f"❌ {s.title}{' ⚠️' if s.channel_ref.startswith('+') else ''}",
        callback_data=f"sub:rm:{s.id}",
    )] for s in subs]
    kb.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="sub:back")])
    await callback.message.edit_text(t("msub_choose_to_remove", lang),
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
                                     parse_mode="HTML")
    await callback.answer()


@router.callback_query(_CB, F.data.startswith("sub:rm:"))
async def rm_confirm(callback: CallbackQuery):
    lang = await db.get_group_language(callback.message.chat.id)
    if not await _is_admin(callback.message.chat.id, callback.from_user.id, callback.bot):
        return await callback.answer(t("msub_no_access", lang), show_alert=True)
    sub_id = int(callback.data.split(":")[-1])
    sub = await db.get_group_mandatory_sub(sub_id)
    if not sub or sub.group_id != callback.message.chat.id:
        return await callback.answer(t("msub_not_found", lang), show_alert=True)
    await db.remove_group_mandatory_sub(sub_id, callback.message.chat.id)
    await callback.answer(t("msub_removed", lang, title=sub.title), show_alert=True)
    subs = await db.get_group_mandatory_subs(callback.message.chat.id)
    if not subs:
        await callback.message.edit_text(
            t("msub_no_active_subs", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("msub_btn_add", lang), callback_data="sub:add")],
                [InlineKeyboardButton(text=t("btn_back_to_group_menu", lang), callback_data="grpmenu:back")],
            ]),
        )
    else:
        await _show_menu(callback, subs)


@router.callback_query(_CB, F.data == "sub:back")
async def sub_back(callback: CallbackQuery):
    subs = await db.get_group_mandatory_subs(callback.message.chat.id)
    await _show_menu(callback, subs)
    await callback.answer()


@router.callback_query(_CB, F.data.startswith("sub:pick:"))
async def pick_chat_cb(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора чата из списка."""
    lang = await db.get_group_language(callback.message.chat.id)
    if not await _is_admin(callback.message.chat.id, callback.from_user.id, callback.bot):
        return await callback.answer(t("msub_no_access", lang), show_alert=True)

    value = callback.data[len("sub:pick:"):]
    if value == "cancel":
        await sub_cancel(callback, state)
        return

    try:
        target_chat_id = int(value)
    except ValueError:
        return await callback.answer(t("msub_invalid_format", lang), show_alert=True)

    ok = await _bot_is_admin(callback.bot, str(target_chat_id))
    if not ok:
        return await callback.answer(t("msub_bot_not_admin_in_chat", lang), show_alert=True)

    try:
        chat = await callback.bot.get_chat(target_chat_id)
        title = f"@{chat.username}" if chat.username else (chat.title or str(target_chat_id))
    except Exception:
        title = str(target_chat_id)

    # Сохраняем invite link для будущих проверок подписки
    data = await state.get_data()
    invite = data.get("invite")
    if invite:
        try:
            await db.store_chat_invite_link(target_chat_id, invite)
        except Exception:
            pass

    existing = await db.get_group_mandatory_subs(callback.message.chat.id)
    if any(s.channel_ref == str(target_chat_id) for s in existing):
        await state.clear()
        await callback.message.edit_text(t("msub_already_added", lang, title=title), parse_mode="HTML")
        return await callback.answer()

    await state.update_data(ref=str(target_chat_id), title=title)
    await callback.message.edit_text(
        t("msub_confirm_ask_duration", lang, title=title),
        parse_mode="HTML",
    )
    await state.set_state(MSubStates.duration)
    await callback.answer()


# ═══════════════════════════════════════════════════════════════════════════════
# ФИЛЬТРАЦИЯ СООБЩЕНИЙ
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(_MSG)
async def filter_msg(message: Message):
    """Удаляет сообщения от неподписанных участников."""
    if not message.from_user or message.from_user.is_bot:
        return
    if message.content_type in ("new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo", "delete_chat_photo", "group_chat_created", "supergroup_chat_created", "pinned_message"):
        return
    if message.text and message.text.startswith("/"):
        return

    subs = await db.get_group_mandatory_subs(message.chat.id)
    if not subs:
        return
    if await _is_admin(message.chat.id, message.from_user.id, message.bot):
        return

    bad = []
    for s in subs:
        if not await _check_sub(message.bot, message.from_user.id, s):
            bad.append(s)
    if not bad:
        return

    lang = await db.get_group_language(message.chat.id)

    try:
        await message.delete()
    except Exception:
        pass

    mention = f"<a href=\"tg://user?id={message.from_user.id}\">{tg_escape(message.from_user.full_name)}</a>"
    lines = [
        t("filter_must_subscribe_header", lang, mention=mention),
    ]
    kb_buttons = []
    idx = 1
    for s in bad:
        ref = s.channel_ref
        name = s.title.replace("@", "")
        link = None
        if ref == "bot:start":
            if s.invite_link:
                link = s.invite_link
            lines.append(f"{idx}. <b>{name}</b>")
            if link:
                kb_buttons.append([InlineKeyboardButton(text=t("filter_subscribe_btn", lang, idx=idx), url=link)])
            else:
                lines[-1] += t("filter_bot_launch_hint", lang, bot_username=message.bot.username)
            idx += 1
            continue
        if s.invite_link:
            link = s.invite_link
        elif ref.startswith("+"):
            link = s.invite_link
        else:
            link = await _get_sub_link(message.bot, s)
        if link:
            lines.append(f"{idx}. <b>{name}</b>")
            kb_buttons.append([InlineKeyboardButton(text=t("filter_subscribe_btn", lang, idx=idx), url=link)])
        else:
            lines.append(t("filter_ask_admin_hint", lang, name=f"{idx}. {name}"))
        idx += 1
    lines.append(t("filter_after_subscribe_notice", lang))
    await message.bot.send_message(
        message.chat.id,
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons) if kb_buttons else None,
    )
