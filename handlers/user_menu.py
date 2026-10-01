"""
Обработчики главного меню пользователя
"""
import time
from urllib.parse import quote
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from database.db import Database
from database.models import UserRole
from keyboards.menus import (
    get_main_menu, get_role_menu, get_tasks_keyboard, get_support_reasons_keyboard,
    get_back_keyboard, get_admin_channel_picker_keyboard
)
from utils.helpers import format_user_profile, format_task_info
from utils.security import tg_escape
from utils.i18n import all_variants, t, DEFAULT_LANG
from handlers.auth import db, check_required_subs, _build_sub_keyboard
from handlers.withdrawal import WithdrawalStates as _WithdrawalStates
from handlers.games import GamesStates

router = Router()


class SupportStates(StatesGroup):
    """Состояния для формы поддержки"""
    waiting_for_reason = State()
    waiting_for_description = State()


class AdStates(StatesGroup):
    """Состояния для формы заявки на рекламу"""
    waiting_for_channel = State()
    waiting_for_budget = State()
    waiting_for_subscribers = State()
    waiting_for_confirm = State()


class TopUpStates(StatesGroup):
    """Состояния для доплаты за доп. участников в своей рекламе"""
    waiting_for_count = State()


# Наборы переводов каждой кнопки (по всем языкам) — используются и для
# определения "это вообще кнопка меню" (_MENU_BUTTON_TEXTS), и для точечного
# определения "это КАКАЯ ИМЕННО кнопка" в диспетчере ниже. "📋 Задания" и
# "💬 Связаться с админом" — не переводим: в текущих клавиатурах такой кнопки
# уже нет, это исторически оставшиеся строки ради обратной совместимости.
_TOP_USERS_TEXTS = all_variants("btn_top_users")
_EARNINGS_TEXTS = all_variants("btn_earnings") | {"📋 Задания"}
_PROFILE_TEXTS = all_variants("btn_profile")
_REFERRALS_TEXTS = all_variants("btn_referrals")
_ADS_SUPPORT_TEXTS = all_variants("btn_ads_support")
_INFO_TEXTS = all_variants("btn_info")
_WITHDRAW_TEXTS = all_variants("btn_withdraw")
_ADMIN_PANEL_TEXTS = all_variants("btn_admin_panel")
_MODER_PANEL_TEXTS = all_variants("btn_moder_panel")
_CRM_PANEL_TEXTS = all_variants("btn_crm_panel")
_BOT_HELP_TEXTS = all_variants("btn_bot_help")
_VIEW_RULES_TEXTS = all_variants("btn_view_rules")
_CONTESTS_TEXTS = all_variants("btn_contests")
_MONEY_GAMES_TEXTS = all_variants("btn_money_games")

# Кнопки меню которые должны прерывать любые активные FSM-состояния
_MENU_BUTTON_TEXTS = (
    _TOP_USERS_TEXTS | _EARNINGS_TEXTS | _PROFILE_TEXTS | _REFERRALS_TEXTS
    | _ADS_SUPPORT_TEXTS | _WITHDRAW_TEXTS | _INFO_TEXTS | _BOT_HELP_TEXTS
    | _VIEW_RULES_TEXTS | _CONTESTS_TEXTS | _MONEY_GAMES_TEXTS
    | _ADMIN_PANEL_TEXTS | _MODER_PANEL_TEXTS | _CRM_PANEL_TEXTS
    | {"💬 Связаться с админом"}
)


@router.message(
    StateFilter(AdStates.waiting_for_channel, AdStates.waiting_for_budget,
                AdStates.waiting_for_subscribers, AdStates.waiting_for_confirm,
                TopUpStates.waiting_for_count,
                SupportStates.waiting_for_reason, SupportStates.waiting_for_description,
                _WithdrawalStates.waiting_for_amount,
                GamesStates.waiting_for_bet),
    F.text.in_(_MENU_BUTTON_TEXTS),
)
async def cancel_fsm_on_menu_button(message: Message, state: FSMContext):
    """Сбрасывает активное FSM-состояние при нажатии кнопки меню и вызывает нужный обработчик.

    Раньше вывод средств (handlers/withdrawal.py::WithdrawalStates) отменялся
    отдельным обработчиком в withdrawal.py, но т.к. этот роутер (user_menu)
    подключается раньше withdrawal.py, а большинство кнопок меню (show_top_users,
    show_earnings и т.д.) зарегистрированы здесь БЕЗ фильтра по состоянию — они
    перехватывали нажатие первыми, и обработчик в withdrawal.py фактически
    никогда не срабатывал, оставляя FSM вывода средств «подвисшим». Поэтому
    состояние вывода теперь тоже обрабатывается прямо здесь."""
    await state.clear()

    # Вызываем нужный обработчик напрямую по тексту кнопки (независимо от
    # того, на каком из поддерживаемых языков эта кнопка сейчас подписана)
    text = message.text

    if text in _WITHDRAW_TEXTS:
        from handlers.withdrawal import begin_withdrawal
        await begin_withdrawal(message.from_user.id, state, message.answer)
    elif text in _TOP_USERS_TEXTS:
        await show_top_users(message)
    elif text in _EARNINGS_TEXTS:
        await show_earnings(message)
    elif text in _PROFILE_TEXTS:
        await show_profile(message)
    elif text in _REFERRALS_TEXTS:
        await show_referral_system(message)
    elif text in _ADS_SUPPORT_TEXTS:
        await show_ad_support(message)
    elif text in _INFO_TEXTS:
        await show_info(message)
    elif text in _BOT_HELP_TEXTS:
        from handlers.bot_help import show_bot_help
        await show_bot_help(message)
    elif text in _VIEW_RULES_TEXTS:
        from handlers.rules import show_rules
        await show_rules(message)
    elif text in _CONTESTS_TEXTS:
        from handlers.contests import show_contests
        await show_contests(message)
    elif text in _MONEY_GAMES_TEXTS:
        from handlers.games import show_games
        await show_games(message)
    else:
        user = await db.get_user(message.from_user.id)
        lang = user.language if user else DEFAULT_LANG
        await message.answer(t("fsm_action_cancelled", lang), reply_markup=get_role_menu(user))


def _get_top_tabs_keyboard(active: str, lang: str = DEFAULT_LANG) -> "InlineKeyboardMarkup":
    from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
    from aiogram.types import InlineKeyboardButton as IKBtn
    builder = IKB()
    builder.row(
        IKBtn(
            text=t("tab_by_balance", lang) + (" ✓" if active == "balance" else ""),
            callback_data="top:balance",
        ),
        IKBtn(
            text=t("tab_by_referrals", lang) + (" ✓" if active == "referrals" else ""),
            callback_data="top:referrals",
        ),
    )
    return builder.as_markup()


async def _build_top_balance_text(lang: str = DEFAULT_LANG) -> str:
    import aiosqlite
    from config import DB_PATH
    rows = []
    async with aiosqlite.connect(DB_PATH) as database:
        async with database.execute("""
            SELECT user_id, first_name, username, verified_balance
            FROM users
            WHERE is_blocked = 0
            ORDER BY verified_balance DESC
            LIMIT 10
        """) as cursor:
            async for row in cursor:
                rows.append(row)
    if not rows:
        return t("top_by_balance_empty", lang)
    text = t("top_by_balance_title", lang)
    for i, row in enumerate(rows, 1):
        name = row[1] or t("no_name", lang)
        uname = f"@{row[2]}" if row[2] else t("no_username", lang)
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} ({uname})\n   💰 ${row[3]:.2f}\n\n"
    return text


async def _build_top_referrals_text(lang: str = DEFAULT_LANG) -> str:
    import aiosqlite
    from config import DB_PATH
    rows = []
    async with aiosqlite.connect(DB_PATH) as database:
        async with database.execute("""
            SELECT user_id, first_name, username, referrals_count
            FROM users
            WHERE is_blocked = 0
            ORDER BY referrals_count DESC
            LIMIT 10
        """) as cursor:
            async for row in cursor:
                rows.append(row)
    if not rows or all(row[3] == 0 for row in rows):
        return t("top_by_referrals_empty", lang)
    text = t("top_by_referrals_title", lang)
    for i, row in enumerate(rows, 1):
        if row[3] == 0:
            break
        name = row[1] or t("no_name", lang)
        uname = f"@{row[2]}" if row[2] else t("no_username", lang)
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {name} ({uname})\n   {t('referrals_count_line', lang, n=row[3])}\n\n"
    return text


@router.message(F.text.in_(_TOP_USERS_TEXTS))
async def show_top_users(message: Message):
    """Показать топ пользователей"""
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer(t("not_registered", DEFAULT_LANG))
        return
    lang = user.language

    blocked, missing = await check_required_subs(message.bot, message.from_user.id)
    if blocked:
        await message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        return

    text = await _build_top_balance_text(lang)
    await message.answer(text, reply_markup=_get_top_tabs_keyboard("balance", lang), parse_mode="HTML")
    await db.log_action(message.from_user.id, "view_top_users")


@router.callback_query(F.data == "top:balance")
async def top_tab_balance(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    text = await _build_top_balance_text(lang)
    kb = _get_top_tabs_keyboard("balance", lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "top:referrals")
async def top_tab_referrals(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    text = await _build_top_referrals_text(lang)
    kb = _get_top_tabs_keyboard("referrals", lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.message(F.text.in_(_EARNINGS_TEXTS))
async def show_earnings(message: Message):
    """Показать задания для заработка"""
    user = await db.get_user(message.from_user.id)

    if not user:
        await message.answer(t("not_registered", DEFAULT_LANG))
        return
    lang = user.language

    blocked, missing = await check_required_subs(message.bot, message.from_user.id)
    if blocked:
        await message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        return

    tasks = await db.get_available_tasks()

    from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
    from aiogram.types import InlineKeyboardButton as IKBtn

    if not tasks:
        kb = IKB()
        kb.row(IKBtn(text=t("btn_my_history", lang), callback_data="task:my_history"))
        kb.row(IKBtn(text=t("btn_my_ads", lang), callback_data="myads:list"))
        kb.row(IKBtn(text=t("btn_back", lang), callback_data="back_to_menu"))
        await message.answer(
            t("no_active_tasks_title", lang) + "\n\n" + t("no_active_tasks_body", lang),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    else:
        ineligible_ids = await db.get_ineligible_task_ids(message.from_user.id)
        kb = get_tasks_keyboard(tasks, ineligible_ids, lang)
        kb.inline_keyboard.insert(len(kb.inline_keyboard) - 1, [IKBtn(text=t("btn_my_ads", lang), callback_data="myads:list")])
        await message.answer(
            t("available_tasks_title", lang),
            reply_markup=kb,
            parse_mode="HTML"
        )

    await db.log_action(message.from_user.id, "view_earnings")


@router.message(F.text.in_(_PROFILE_TEXTS))
async def show_profile(message: Message):
    """Показать профиль пользователя"""
    from keyboards.menus import InlineKeyboardBuilder, InlineKeyboardButton
    
    user = await db.get_user(message.from_user.id)

    if not user:
        await message.answer(t("not_registered", DEFAULT_LANG))
        return

    blocked, missing = await check_required_subs(message.bot, message.from_user.id)
    if blocked:
        await message.answer(
            t("required_subs_header", user.language),
            reply_markup=_build_sub_keyboard(missing, user.language),
            parse_mode="HTML",
        )
        return

    profile_text = await format_user_profile(user, db)

    lang_kb = InlineKeyboardBuilder()
    lang_kb.row(InlineKeyboardButton(text=t("change_language_button", user.language), callback_data="show_language_menu"))
    await message.answer(profile_text, reply_markup=lang_kb.as_markup(), parse_mode="HTML")
    await db.log_action(message.from_user.id, "view_profile")


@router.message(F.text.in_(_REFERRALS_TEXTS))
async def show_referral_system(message: Message):
    """Показать реферальную систему"""
    user = await db.get_user(message.from_user.id)

    if not user:
        await message.answer(t("not_registered", DEFAULT_LANG))
        return
    lang = user.language

    blocked, missing = await check_required_subs(message.bot, message.from_user.id)
    if blocked:
        await message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        return

    from config import REFERRAL_BONUS
    from utils.helpers import format_referral_link

    bot_username = (await message.bot.get_me()).username
    referral_link = format_referral_link(bot_username, user.referral_code)
    bonus_line = (
        t("referral_bonus_line", lang, amount=REFERRAL_BONUS) + "\n\n"
        if REFERRAL_BONUS > 0
        else t("referral_bonus_disabled", lang) + "\n\n"
    )
    share_text = quote(t("referral_share_hint", lang) + " " + referral_link)

    await message.answer(
        f"{t('referral_title', lang)}\n\n"
        f"{t('referral_invited_line', lang, count=user.referrals_count)}\n\n"
        f"{bonus_line}"
        f"{t('referral_link_label', lang)}\n"
        f"<code>{referral_link}</code>\n\n"
        f"{t('referral_share_hint', lang)}\n\n"
        f"<a href=\"https://t.me/share/url?url={referral_link}&text={share_text}\">{t('referral_share_button', lang)}</a>",
        parse_mode="HTML",
    )
    await db.log_action(message.from_user.id, "view_referral_system")


@router.message(F.text.in_(_ADS_SUPPORT_TEXTS))
async def show_ad_support(message: Message):
    """Показать меню рекламы/поддержки"""
    from keyboards.menus import InlineKeyboardBuilder, InlineKeyboardButton

    user = await db.get_user(message.from_user.id)

    if not user:
        await message.answer(t("not_registered", DEFAULT_LANG))
        return
    lang = user.language

    blocked, missing = await check_required_subs(message.bot, message.from_user.id)
    if blocked:
        await message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t("btn_advertise", lang), callback_data="advertise:start"))
    builder.row(InlineKeyboardButton(text=t("btn_contact_support", lang), callback_data="support:create_ticket"))
    builder.row(InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_menu"))

    await message.answer(
        t("ads_support_title", lang),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


async def _notify_active_ticket(callback_or_message, ticket, lang: str = DEFAULT_LANG) -> bool:
    """Если у пользователя уже есть открытый тикет — сообщить и вернуть True."""
    send = (
        callback_or_message.message.answer
        if hasattr(callback_or_message, "message")
        else callback_or_message.answer
    )
    st = t("ticket_status_open", lang) if ticket.status == "open" else t("ticket_status_in_progress", lang)
    await send(
        t("active_ticket_notice", lang, ticket_id=ticket.ticket_id, status=st),
        parse_mode="HTML",
    )
    return True


@router.callback_query(F.data == "support:create_ticket")
async def start_support_ticket(callback: CallbackQuery):
    """Начать создание тикета поддержки"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    active = await db.get_user_active_support_ticket(callback.from_user.id)
    if active:
        await _notify_active_ticket(callback, active, lang)
        await callback.answer()
        return

    await callback.message.answer(
        t("support_choose_reason", lang),
        reply_markup=get_support_reasons_keyboard(lang),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("support_reason:"))
async def handle_support_reason(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора причины обращения"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    active = await db.get_user_active_support_ticket(callback.from_user.id)
    if active:
        await _notify_active_ticket(callback, active, lang)
        await callback.answer()
        return

    reason = callback.data.split(":")[1]
    reason_text = {
        "question": t("support_reason_question", lang),
        "problem": t("support_reason_problem", lang),
        "suggestion": t("support_reason_suggestion", lang),
    }.get(reason, reason)

    await state.update_data(reason=reason, reason_text=reason_text)
    await state.set_state(SupportStates.waiting_for_description)

    await callback.message.edit_text(t("support_reason_selected", lang, reason=reason_text))
    await callback.answer()


@router.message(SupportStates.waiting_for_description)
async def handle_support_description(message: Message, state: FSMContext):
    """Обработчик описания проблемы"""
    data = await state.get_data()
    reason = data.get("reason")
    reason_text = data.get("reason_text")
    description = message.text
    
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    active = await db.get_user_active_support_ticket(message.from_user.id)
    if active:
        await _notify_active_ticket(message, active, lang)
        await state.clear()
        return

    ticket = await db.create_support_ticket(
        user_id=message.from_user.id,
        reason=reason_text,
        description=description,
    )

    from keyboards.menus import get_role_menu
    menu = get_role_menu(user)

    await message.answer(
        t("support_ticket_created", lang, ticket_id=ticket.ticket_id),
        reply_markup=menu,
        parse_mode="HTML",
    )
    
    # Уведомляем админов
    from handlers.admin import notify_admins_about_ticket
    await notify_admins_about_ticket(message.bot, ticket)
    
    await db.save_chat_message(
        chat_request_id=None,
        support_ticket_id=ticket.ticket_id,
        user_id=message.from_user.id,
        admin_id=None,
        message_text=f"[Обращение] {description}",
        is_from_admin=False,
    )

    await db.log_action(message.from_user.id, "support_ticket_created", f"Ticket #{ticket.ticket_id}")
    await state.clear()


@router.callback_query(F.data == "task:my_history")
async def show_task_history(callback: CallbackQuery):
    """История выполненных заданий пользователя."""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t("not_registered", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    blocked, missing = await check_required_subs(callback.bot, callback.from_user.id)
    if blocked:
        await callback.message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    history = await db.get_user_tasks_history(callback.from_user.id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
    from aiogram.types import InlineKeyboardButton as IKBtn
    kb = IKB()
    kb.row(IKBtn(text=t("btn_to_tasks", lang), callback_data="show_tasks_list"))

    if not history:
        await callback.message.edit_text(
            t("task_history_title", lang) + "\n\n" + t("task_history_empty_body", lang),
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    # Статусы
    def status_icon(item):
        if item["verification_failed"]:
            return "❌"
        if item["is_verified"]:
            return "✅"
        return "⏳"

    def status_text(item):
        if item["verification_failed"]:
            return t("task_status_not_counted", lang)
        if item["is_verified"]:
            return t("task_status_paid", lang)
        return t("task_status_pending_verification", lang)

    total_earned = sum(h["reward"] for h in history if h["is_verified"] and not h["verification_failed"])
    pending_count = sum(1 for h in history if not h["is_verified"] and not h["verification_failed"])

    lines = [
        t("task_history_title", lang) + "\n",
        t("task_history_summary", lang, count=len(history), pending=pending_count, earned=total_earned),
        "─" * 28,
    ]

    for item in history[:20]:  # Показываем последние 20
        date = item["completed_at"][:10] if item["completed_at"] else "—"
        lines.append(
            f"\n{status_icon(item)} @{item['channel']}\n"
            f"   💵 ${item['reward']:.2f}  ·  {status_text(item)}  ·  {date}"
        )

    if len(history) > 20:
        lines.append("\n\n<i>" + t("task_history_shown_last", lang, shown=20, total=len(history)) + "</i>")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()
    await db.log_action(callback.from_user.id, "view_task_history")


@router.callback_query(F.data == "profile:referral")
async def profile_referral(callback: CallbackQuery):
    """Реферальная система из профиля"""
    user = await db.get_user(callback.from_user.id)

    if not user:
        await callback.answer(t("not_registered", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    blocked, missing = await check_required_subs(callback.bot, callback.from_user.id)
    if blocked:
        await callback.message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    from config import REFERRAL_BONUS
    from utils.helpers import format_referral_link

    bot_username = (await callback.bot.get_me()).username
    referral_link = format_referral_link(bot_username, user.referral_code)
    bonus_line = (
        t("referral_bonus_line", lang, amount=REFERRAL_BONUS) + "\n\n"
        if REFERRAL_BONUS > 0
        else t("referral_bonus_disabled", lang) + "\n\n"
    )

    await callback.message.edit_text(
        f"{t('referral_title', lang)}\n\n"
        f"{t('referral_invited_line', lang, count=user.referrals_count)}\n\n"
        f"{bonus_line}"
        f"{t('referral_link_label', lang)}\n"
        f"<code>{referral_link}</code>\n\n"
        f"{t('referral_share_hint', lang)}",
        parse_mode="HTML",
    )
    await db.log_action(callback.from_user.id, "view_referral_system")
    await callback.answer()


@router.callback_query(F.data == "profile:withdrawal")
async def profile_withdrawal(callback: CallbackQuery, state: FSMContext):
    """Вывод средств из профиля (inline-кнопка)"""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t("not_registered", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    blocked, missing = await check_required_subs(callback.bot, callback.from_user.id)
    if blocked:
        await callback.message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    from handlers.withdrawal import begin_withdrawal

    async def send_text(text, **kwargs):
        await callback.message.answer(text, **kwargs)

    await begin_withdrawal(callback.from_user.id, state, send_text)
    await callback.answer()


@router.callback_query(F.data == "profile:support")
async def profile_support(callback: CallbackQuery):
    """Поддержка из профиля"""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t("not_registered", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    blocked, missing = await check_required_subs(callback.bot, callback.from_user.id)
    if blocked:
        await callback.message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    active = await db.get_user_active_support_ticket(callback.from_user.id)
    if active:
        await _notify_active_ticket(callback, active, lang)
        await callback.answer()
        return

    await callback.message.answer(
        t("support_choose_reason", lang),
        reply_markup=get_support_reasons_keyboard(lang),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "show_tasks_list")
async def show_tasks_list(callback: CallbackQuery):
    """Вернуться к списку заданий (из истории)"""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t("not_registered", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    blocked, missing = await check_required_subs(callback.bot, callback.from_user.id)
    if blocked:
        await callback.message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    tasks = await db.get_available_tasks()
    from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
    from aiogram.types import InlineKeyboardButton as IKBtn
    from keyboards.menus import get_tasks_keyboard

    if not tasks:
        kb = IKB()
        kb.row(IKBtn(text=t("btn_my_history", lang), callback_data="task:my_history"))
        kb.row(IKBtn(text=t("btn_my_ads", lang), callback_data="myads:list"))
        kb.row(IKBtn(text=t("btn_back", lang), callback_data="back_to_menu"))
        try:
            await callback.message.edit_text(
                t("no_active_tasks_title", lang) + "\n\n" + t("no_active_tasks_body", lang),
                reply_markup=kb.as_markup(),
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        ineligible_ids = await db.get_ineligible_task_ids(callback.from_user.id)
        kb = get_tasks_keyboard(tasks, ineligible_ids, lang)
        kb.inline_keyboard.insert(len(kb.inline_keyboard) - 1, [IKBtn(text=t("btn_my_ads", lang), callback_data="myads:list")])
        try:
            await callback.message.edit_text(
                t("available_tasks_title", lang),
                reply_markup=kb,
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.answer()


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    """Вернуться в главное меню"""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer()
        return
    lang = user.language

    blocked, missing = await check_required_subs(callback.bot, callback.from_user.id)
    if blocked:
        await callback.message.answer(
            t("required_subs_header", lang),
            reply_markup=_build_sub_keyboard(missing, lang),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    try:
        await callback.message.delete()
    except Exception:
        pass

    menu = get_role_menu(user)
    await callback.message.answer(t("menu_label", lang), reply_markup=menu)
    await callback.answer()


@router.message(F.text.in_(_INFO_TEXTS))
async def show_info(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer(t("not_registered", DEFAULT_LANG))
        return
    lang = user.language

    import aiosqlite
    from config import DB_PATH
    from datetime import date

    today = date.today().isoformat()

    async with aiosqlite.connect(DB_PATH) as conn:
        total = (await (await conn.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        today_count = (await (await conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (today,)
        )).fetchone())[0]

    from keyboards.menus import InlineKeyboardBuilder, InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=t("btn_withdrawals_channel", lang), url="https://t.me/+NQ7sLBUCtFE3NGUy"))
    kb.row(InlineKeyboardButton(text=t("btn_our_studio", lang), url="https://t.me/QuillStudio"))
    kb.row(InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_menu"))

    await message.answer(
        f"{t('info_title', lang)}\n\n"
        f"{t('info_users_total', lang, total=total)}\n"
        f"{t('info_users_today', lang, today=today_count)}\n\n"
        f"{t('info_studio_text', lang)}",
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
    await db.log_action(message.from_user.id, "view_info")


# ═══════════════════════════════════════════════════════════════
# ЗАЯВКА НА РЕКЛАМУ
# ═══════════════════════════════════════════════════════════════

COMMISSION_PERCENT = 15


@router.callback_query(F.data == "advertise:start")
async def start_advertise(callback: CallbackQuery, state: FSMContext):
    """Начать оформление заявки на рекламу"""
    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t("not_registered", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    # Проверяем, нет ли уже активной заявки
    pending = await db.get_pending_ad_requests()
    for req in pending:
        if req.user_id == callback.from_user.id:
            await callback.answer(
                t("ads_active_request_notice", lang),
                show_alert=True,
            )
            return

    await callback.message.edit_text(
        t("ad_step1_title", lang),
        parse_mode="HTML",
    )
    await callback.message.answer(
        t("pick_channel_hint", lang),
        reply_markup=get_admin_channel_picker_keyboard(lang, request_id_base=10),
    )
    await state.set_state(AdStates.waiting_for_channel)
    await callback.answer()


def _parse_ad_channel_ref(raw: str):
    """Разобрать ссылку/username канала в проверяемый ref для get_chat_member.
    Возвращает (chat_ref, is_private_unresolvable):
        chat_ref — "@username" (публичный) или int chat_id (числовой ID) или None
        is_private_unresolvable — True, если это ссылка-приглашение (t.me/+...),
        которую Bot API не умеет резолвить напрямую без числового ID."""
    import re
    ref = raw.strip()

    if re.match(r'https?://t\.me/\+', ref, re.IGNORECASE) or re.match(r't\.me/\+', ref, re.IGNORECASE) or ref.startswith('+'):
        return None, True

    if re.match(r'^-?\d+$', ref):
        return int(ref), False

    if re.match(r'https?://t\.me/', ref, re.IGNORECASE):
        path = re.sub(r'https?://t\.me/', '', ref, flags=re.IGNORECASE).strip('/')
        username = path.split('?')[0].strip('/')
        if username:
            return f"@{username}", False
        return None, False

    username = ref.lstrip('@')
    if username:
        return f"@{username}", False
    return None, False


async def _verify_ad_channel_access(bot, chat_ref, user_id: int) -> str | None:
    """Проверить, что бот и заявитель — оба администраторы chat_ref.
    Возвращает None если всё ок, иначе ключ перевода с причиной отказа."""
    try:
        bot_member = await bot.get_chat_member(chat_ref, bot.id)
    except Exception:
        return "ad_bot_not_in_channel"
    if bot_member.status not in ("administrator", "creator"):
        return "ad_bot_not_in_channel"

    try:
        user_member = await bot.get_chat_member(chat_ref, user_id)
    except Exception:
        return "ad_user_not_admin"
    if user_member.status not in ("administrator", "creator"):
        return "ad_user_not_admin"

    return None


@router.message(AdStates.waiting_for_channel, F.chat_shared)
async def process_ad_channel_picked(message: Message, state: FSMContext):
    """Обработать канал/группу, выбранную через нативный пикер Telegram —
    Telegram уже отфильтровал список по правам (бот и пользователь — админы),
    но перепроверяем через API на всякий случай (данные клиента могли устареть)."""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    shared = message.chat_shared
    if shared is None or shared.request_id not in (10, 11):
        return

    fail_reason = await _verify_ad_channel_access(message.bot, shared.chat_id, message.from_user.id)
    if fail_reason:
        await message.answer(t(fail_reason, lang), parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return

    channel_link = f"@{shared.username}" if shared.username else (shared.title or str(shared.chat_id))
    await state.update_data(channel_link=channel_link)
    await state.set_state(AdStates.waiting_for_budget)

    await message.answer(
        t("ad_step2_title", lang),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AdStates.waiting_for_channel)
async def process_ad_channel(message: Message, state: FSMContext):
    """Обработать ссылку на канал"""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    channel_text = (message.text or "").strip()

    if not channel_text:
        await message.answer(t("ad_channel_empty", lang))
        return

    if len(channel_text) > 200:
        await message.answer(t("ad_channel_too_long", lang))
        return

    chat_ref, is_private_unresolvable = _parse_ad_channel_ref(channel_text)

    if is_private_unresolvable:
        await message.answer(t("ad_channel_private_unresolvable", lang), parse_mode="HTML")
        return

    if chat_ref is None:
        await message.answer(t("ad_channel_empty", lang))
        return

    fail_reason = await _verify_ad_channel_access(message.bot, chat_ref, message.from_user.id)
    if fail_reason:
        await message.answer(t(fail_reason, lang), parse_mode="HTML")
        return

    await state.update_data(channel_link=channel_text)
    await state.set_state(AdStates.waiting_for_budget)

    await message.answer(
        t("ad_step2_title", lang),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(AdStates.waiting_for_budget)
async def process_ad_budget(message: Message, state: FSMContext):
    """Обработать бюджет"""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    try:
        budget = float(message.text.replace(",", "."))
        if budget < 1:
            await message.answer(t("ad_budget_too_low", lang), parse_mode="HTML")
            return
        if budget > 100000:
            await message.answer(t("ad_budget_too_high", lang), parse_mode="HTML")
            return
    except ValueError:
        await message.answer(t("ad_budget_invalid", lang), parse_mode="HTML")
        return

    await state.update_data(budget=budget)
    await state.set_state(AdStates.waiting_for_subscribers)

    await message.answer(
        t("ad_step3_title", lang),
        parse_mode="HTML",
    )


@router.message(AdStates.waiting_for_subscribers)
async def process_ad_subscribers(message: Message, state: FSMContext):
    """Обработать количество подписчиков и показать расчёт"""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    try:
        target = int(message.text)
        if target < 1:
            await message.answer(t("ad_subs_too_low", lang), parse_mode="HTML")
            return
        if target > 100000:
            await message.answer(t("ad_subs_too_high", lang), parse_mode="HTML")
            return
    except ValueError:
        await message.answer(t("ad_subs_invalid", lang), parse_mode="HTML")
        return

    data = await state.get_data()
    budget = data["budget"]
    channel_link = data["channel_link"]

    commission = budget * COMMISSION_PERCENT / 100
    net_budget = budget - commission
    reward_per_user = net_budget / target

    await state.update_data(target_subscribers=target, reward_per_user=reward_per_user)

    from keyboards.menus import InlineKeyboardBuilder, InlineKeyboardButton
    from config import STARS_PER_USD

    stars_amount = max(1, round(budget * STARS_PER_USD))

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=t("btn_pay_balance", lang, amount=budget), callback_data="advertise:pay_balance"))
    kb.row(InlineKeyboardButton(text=t("btn_pay_stars", lang, stars=stars_amount), callback_data="advertise:pay_stars"))
    kb.row(InlineKeyboardButton(text=t("btn_pay_via_admin", lang), callback_data="advertise:confirm"))
    kb.row(InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="advertise:cancel"))

    await message.answer(
        t(
            "ad_review_title", lang,
            channel=tg_escape(channel_link), budget=budget, target=target,
            commission=commission, net_budget=net_budget, reward=reward_per_user,
            stars=stars_amount, rate=STARS_PER_USD,
        ),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
    await state.set_state(AdStates.waiting_for_confirm)


@router.callback_query(F.data == "advertise:cancel")
async def cancel_advertise(callback: CallbackQuery, state: FSMContext):
    """Отменить заявку на рекламу"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await state.clear()
    await callback.message.edit_text(t("ad_cancelled", lang))
    await callback.answer()


async def _notify_admins_new_ad(bot, ad_request, user, channel_link, budget,
                                target_subscribers, reward_per_user, payment_note: str):
    """Уведомить всех админов/модеров о новой заявке на рекламу."""
    import aiosqlite
    from config import DB_PATH, WEB_ADMIN_URL

    name = tg_escape(user.first_name or "Пользователь")
    uname = f"@{tg_escape(user.username)}" if user.username else "—"

    notify_text = (
        f"📢 <b>Новая заявка на рекламу #{ad_request.request_id}</b>\n\n"
        f"👤 {name} ({uname}, ID: {user.user_id})\n"
        f"📌 Канал: <code>{tg_escape(channel_link)}</code>\n"
        f"💰 Бюджет: <b>${budget:.2f}</b>\n"
        f"👥 Цель: <b>{target_subscribers} чел.</b>\n"
        f"💵 Награда за подписку: <b>${reward_per_user:.2f}</b>\n"
        f"{payment_note}\n\n"
        f"<a href=\"{WEB_ADMIN_URL}/admin/chat/{user.user_id}\">Открыть чат</a>"
    )

    async with aiosqlite.connect(DB_PATH) as database:
        async with database.execute(
            "SELECT user_id FROM users WHERE role IN ('Admin', 'Moder')"
        ) as cursor:
            async for row in cursor:
                try:
                    await bot.send_message(row[0], notify_text, parse_mode="HTML")
                except Exception:
                    pass


@router.callback_query(F.data == "advertise:confirm")
async def confirm_advertise(callback: CallbackQuery, state: FSMContext):
    """Оплата через администратора (без списания баланса/звёзд) — отправить заявку админам"""
    data = await state.get_data()
    channel_link = data["channel_link"]
    budget = data["budget"]
    target_subscribers = data["target_subscribers"]
    reward_per_user = data["reward_per_user"]

    await state.clear()

    user = await db.get_user(callback.from_user.id)
    if not user:
        await callback.answer(t("ad_generic_error", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    ad_request = await db.create_ad_request(
        user_id=callback.from_user.id,
        channel_link=channel_link,
        budget=budget,
        target_subscribers=target_subscribers,
        reward_per_user=reward_per_user,
        payment_method="manual",
    )

    await _notify_admins_new_ad(
        callback.bot, ad_request, user, channel_link, budget, target_subscribers,
        reward_per_user, "💬 Оплата через администратора",
    )

    await callback.message.edit_text(
        t(
            "ad_submitted_manual", lang,
            id=ad_request.request_id, channel=tg_escape(channel_link),
            budget=budget, target=target_subscribers, reward=reward_per_user,
        ),
        parse_mode="HTML",
    )
    await callback.answer()

    await db.log_action(
        callback.from_user.id,
        "ad_request_created",
        f"AdRequest #{ad_request.request_id}, budget=${budget}, method=manual",
    )


@router.callback_query(F.data == "advertise:pay_balance")
async def pay_balance_advertise(callback: CallbackQuery, state: FSMContext):
    """Оплата рекламы с подтверждённого баланса пользователя"""
    data = await state.get_data()
    channel_link = data.get("channel_link")
    budget = data.get("budget")
    target_subscribers = data.get("target_subscribers")
    reward_per_user = data.get("reward_per_user")

    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    if budget is None:
        await callback.answer(t("ad_data_stale", lang), show_alert=True)
        return

    if not user:
        await callback.answer(t("ad_generic_error", DEFAULT_LANG), show_alert=True)
        return

    if user.verified_balance < budget:
        await callback.answer(
            t("ad_insufficient_balance", lang, balance=user.verified_balance, budget=budget),
            show_alert=True,
        )
        return

    await db.adjust_user_balance(callback.from_user.id, verified_delta=-budget)
    await state.clear()

    ad_request = await db.create_ad_request(
        user_id=callback.from_user.id,
        channel_link=channel_link,
        budget=budget,
        target_subscribers=target_subscribers,
        reward_per_user=reward_per_user,
        payment_method="balance",
    )
    await db.record_ad_payment(ad_request.request_id, "balance", budget, target_subscribers)

    await _notify_admins_new_ad(
        callback.bot, ad_request, user, channel_link, budget, target_subscribers,
        reward_per_user, "💰 Оплачено с баланса пользователя",
    )

    await callback.message.edit_text(
        t(
            "ad_submitted_balance", lang,
            id=ad_request.request_id, budget=budget, channel=tg_escape(channel_link),
            target=target_subscribers, reward=reward_per_user,
        ),
        parse_mode="HTML",
    )
    await callback.answer()

    await db.log_action(
        callback.from_user.id,
        "ad_request_created",
        f"AdRequest #{ad_request.request_id}, budget=${budget}, method=balance",
    )


@router.callback_query(F.data == "advertise:pay_stars")
async def pay_stars_advertise(callback: CallbackQuery, state: FSMContext):
    """Выставить инвойс на оплату рекламы звёздами Telegram (XTR)"""
    data = await state.get_data()
    budget = data.get("budget")

    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    if budget is None:
        await callback.answer(t("ad_data_stale", lang), show_alert=True)
        return

    from config import STARS_PER_USD
    stars_amount = max(1, round(budget * STARS_PER_USD))

    await callback.answer()
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=t("ad_invoice_title", lang),
        description=t("ad_invoice_description", lang, budget=budget),
        payload=f"ad_stars:{callback.from_user.id}:{int(time.time())}",
        currency="XTR",
        prices=[LabeledPrice(label=t("ad_invoice_label", lang), amount=stars_amount)],
        provider_token="",
    )


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """Подтвердить checkout звёздного платежа (обязателен ответ в течение 10 секунд)"""
    if pre_checkout_query.invoice_payload.startswith(("ad_stars:", "ad_topup:")):
        await pre_checkout_query.answer(ok=True)
    else:
        user = await db.get_user(pre_checkout_query.from_user.id)
        lang = user.language if user else DEFAULT_LANG
        await pre_checkout_query.answer(ok=False, error_message=t("pre_checkout_unknown_payment", lang))


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext):
    """Обработать успешную оплату звёздами и создать заявку на рекламу"""
    payment = message.successful_payment

    if payment.invoice_payload.startswith("ad_topup:"):
        await _process_topup_successful_payment(message, state, payment)
        return

    if not payment.invoice_payload.startswith("ad_stars:"):
        return

    data = await state.get_data()
    channel_link = data.get("channel_link")
    budget = data.get("budget")
    target_subscribers = data.get("target_subscribers")
    reward_per_user = data.get("reward_per_user")

    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    if budget is None:
        # Данные заявки потеряны (например, бот перезапускался между инвойсом и оплатой) — возвращаем звёзды
        try:
            await message.bot.refund_star_payment(
                user_id=message.from_user.id,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
            )
        except Exception:
            pass
        await message.answer(t("ad_payment_lost", lang))
        return

    await state.clear()

    ad_request = await db.create_ad_request(
        user_id=message.from_user.id,
        channel_link=channel_link,
        budget=budget,
        target_subscribers=target_subscribers,
        reward_per_user=reward_per_user,
        payment_method="stars",
        stars_charge_id=payment.telegram_payment_charge_id,
    )
    await db.record_ad_payment(
        ad_request.request_id, "stars", budget, target_subscribers,
        stars_charge_id=payment.telegram_payment_charge_id,
    )

    await _notify_admins_new_ad(
        message.bot, ad_request, user, channel_link, budget, target_subscribers,
        reward_per_user, f"⭐ Оплачено {payment.total_amount}⭐ звёздами",
    )

    await message.answer(
        t(
            "ad_submitted_stars", lang,
            id=ad_request.request_id, stars=payment.total_amount,
            channel=tg_escape(channel_link), budget=budget,
            target=target_subscribers, reward=reward_per_user,
        ),
        parse_mode="HTML",
    )

    await db.log_action(
        message.from_user.id,
        "ad_request_created",
        f"AdRequest #{ad_request.request_id}, budget=${budget}, method=stars, stars={payment.total_amount}",
    )


# ═══════════════════════════════════════════════════════════════
# МОЯ РЕКЛАМА — управление своими заказами (удаление с возвратом остатка,
# доплата за доп. участников по уже зафиксированной цене)
# ═══════════════════════════════════════════════════════════════

def _ad_status_label(status: str, lang: str = DEFAULT_LANG) -> str:
    key = {
        "pending": "ad_status_pending",
        "taken": "ad_status_taken",
        "accepted": "ad_status_accepted",
        "rejected": "ad_status_rejected",
        "cancelled": "ad_status_cancelled",
    }.get(status)
    return t(key, lang) if key else status


async def _notify_admins_plain(bot, text: str):
    """Короткое уведомление всем админам/модерам (без деталей новой заявки)."""
    import aiosqlite
    from config import DB_PATH

    async with aiosqlite.connect(DB_PATH) as database:
        async with database.execute(
            "SELECT user_id FROM users WHERE role IN ('Admin', 'Moder')"
        ) as cursor:
            async for row in cursor:
                try:
                    await bot.send_message(row[0], text, parse_mode="HTML")
                except Exception:
                    pass


@router.callback_query(F.data == "myads:list")
async def show_my_ads(callback: CallbackQuery):
    """Список заявок на рекламу, оформленных этим пользователем.
    Отклонённые и отменённые не показываем — это уже закрытая история,
    только загромождает список; остаются только активные и ожидающие решения."""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    requests = await db.get_ad_requests_by_user(
        callback.from_user.id, statuses=("pending", "taken", "accepted"),
    )

    if not requests:
        await callback.message.answer(t("myads_empty", lang))
        await callback.answer()
        return

    await callback.answer()
    for req in requests:
        status_label = _ad_status_label(req.status, lang)
        text = t(
            "myads_item", lang,
            id=req.request_id, status=status_label, channel=tg_escape(req.channel_link),
            budget=req.budget, target=req.target_subscribers, reward=req.reward_per_user,
        )

        kb = None
        if req.status == "accepted" and req.task_id:
            task = await db.get_task(req.task_id)
            if task and task.is_active:
                text += t("myads_progress_line", lang, current=task.current_subscribers, required=task.required_subscribers)
                from keyboards.menus import InlineKeyboardBuilder, InlineKeyboardButton
                kb = InlineKeyboardBuilder()
                kb.row(
                    InlineKeyboardButton(text=t("btn_add_participants", lang), callback_data=f"myads:topup_start:{req.request_id}"),
                )
                kb.row(
                    InlineKeyboardButton(text=t("btn_delete_refund", lang), callback_data=f"myads:delete:{req.request_id}"),
                )
                kb = kb.as_markup()

        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("myads:delete:"))
async def myads_delete_ask_confirm(callback: CallbackQuery):
    """Показать подтверждение удаления с расчётом возврата"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    request_id = int(callback.data.split(":")[2])
    req = await db.get_ad_request(request_id)

    if not req or req.user_id != callback.from_user.id:
        await callback.answer(t("myads_not_found", lang), show_alert=True)
        return
    if req.status != "accepted" or not req.task_id:
        await callback.answer(t("myads_cannot_delete_status", lang), show_alert=True)
        return

    task = await db.get_task(req.task_id)
    if not task or not task.is_active:
        await callback.answer(t("myads_task_stopped", lang), show_alert=True)
        return

    remaining = max(0, task.required_subscribers - task.current_subscribers)
    refund_preview = 0.0
    if req.payment_method in ("balance", "stars") and task.required_subscribers > 0:
        refund_preview = req.budget * remaining / task.required_subscribers

    from keyboards.menus import InlineKeyboardBuilder, InlineKeyboardButton
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=t("btn_confirm_delete", lang), callback_data=f"myads:delete_confirm:{request_id}"))
    kb.row(InlineKeyboardButton(text=t("btn_cancel_delete", lang), callback_data="myads:noop"))

    refund_line = (
        t("myads_refund_preview_line", lang, amount=refund_preview, remaining=remaining)
        if refund_preview > 0 else
        t("myads_no_refund_line", lang)
    )

    await callback.message.answer(
        t(
            "myads_delete_confirm_title", lang,
            task_id=req.task_id, current=task.current_subscribers,
            required=task.required_subscribers, refund_line=refund_line,
        ),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "myads:noop")
async def myads_noop(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await callback.message.edit_text(t("myads_action_cancelled", lang))
    await callback.answer()


@router.callback_query(F.data.startswith("myads:delete_confirm:"))
async def myads_delete_confirm(callback: CallbackQuery):
    """Выполнить удаление заявки/задания с возвратом остатка"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    request_id = int(callback.data.split(":")[2])
    req = await db.get_ad_request(request_id)

    if not req or req.user_id != callback.from_user.id:
        await callback.answer(t("myads_not_found", lang), show_alert=True)
        return
    if req.status != "accepted" or not req.task_id:
        await callback.answer(t("myads_cannot_delete_status", lang), show_alert=True)
        return

    task = await db.get_task(req.task_id)
    if not task or not task.is_active:
        await callback.answer(t("myads_task_stopped", lang), show_alert=True)
        return

    result = await db.delete_task_and_refund(req.task_id, bot=callback.bot)
    refund_amount = result["refund_amount"]
    refund_stars_usd = result["refund_stars_usd"]

    lines = [t("myads_deleted_line", lang, id=req.request_id)]
    if refund_stars_usd > 0:
        lines.append(t("myads_refund_stars_line", lang, amount=refund_stars_usd))
    if refund_amount > 0:
        lines.append(t("myads_refund_balance_line", lang, amount=refund_amount))
    if refund_amount <= 0 and refund_stars_usd <= 0:
        lines.append(t("myads_no_refund_result_line", lang))

    await callback.message.edit_text("\n".join(lines), parse_mode="HTML")
    await callback.answer()

    await _notify_admins_plain(
        callback.bot,
        f"🚫 Пользователь {callback.from_user.id} сам удалил своё рекламное задание #{req.task_id} "
        f"(заявка #{req.request_id}), возврат баланс: ${refund_amount:.2f}, возврат звёздами: ≈${refund_stars_usd:.2f}",
    )
    await db.log_action(
        callback.from_user.id, "ad_task_self_deleted",
        f"AdRequest #{req.request_id}, Task #{req.task_id}, refund_balance=${refund_amount:.2f}, refund_stars_usd=${refund_stars_usd:.2f}",
    )


@router.callback_query(F.data.startswith("myads:topup_start:"))
async def myads_topup_start(callback: CallbackQuery, state: FSMContext):
    """Начать доплату за доп. участников — запросить количество"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    request_id = int(callback.data.split(":")[2])
    req = await db.get_ad_request(request_id)

    if not req or req.user_id != callback.from_user.id:
        await callback.answer(t("myads_not_found", lang), show_alert=True)
        return
    if req.status != "accepted" or not req.task_id:
        await callback.answer(t("myads_topup_unavailable", lang), show_alert=True)
        return

    task = await db.get_task(req.task_id)
    if not task or not task.is_active:
        await callback.answer(t("myads_task_stopped", lang), show_alert=True)
        return

    # Цена за 1 место — как в исходной заявке (с учётом комиссии), фиксированная
    per_user_gross = req.budget / req.target_subscribers

    await state.update_data(
        topup_request_id=request_id,
        topup_task_id=req.task_id,
        topup_per_user_gross=per_user_gross,
    )
    await state.set_state(TopUpStates.waiting_for_count)

    await callback.message.answer(
        t("myads_topup_title", lang, id=request_id, price=per_user_gross),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(TopUpStates.waiting_for_count)
async def myads_topup_process_count(message: Message, state: FSMContext):
    """Обработать введённое количество доп. участников и предложить оплату"""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    try:
        count = int(message.text)
        if count < 1 or count > 100000:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer(t("myads_topup_invalid_count", lang))
        return

    data = await state.get_data()
    request_id = data.get("topup_request_id")
    per_user_gross = data.get("topup_per_user_gross")
    if request_id is None or per_user_gross is None:
        await message.answer(t("myads_topup_data_stale", lang))
        await state.clear()
        return

    # Перепроверяем заявку/задание свежими данными на случай, если что-то
    # изменилось, пока пользователь вводил число
    req = await db.get_ad_request(request_id)
    if not req or req.user_id != message.from_user.id or req.status != "accepted" or not req.task_id:
        await message.answer(t("myads_topup_unavailable_now", lang))
        await state.clear()
        return
    task = await db.get_task(req.task_id)
    if not task or not task.is_active:
        await message.answer(t("myads_task_stopped", lang))
        await state.clear()
        return

    additional_cost = count * per_user_gross
    await state.update_data(topup_count=count, topup_cost=additional_cost)

    from keyboards.menus import InlineKeyboardBuilder, InlineKeyboardButton
    from config import STARS_PER_USD

    stars_amount = max(1, round(additional_cost * STARS_PER_USD))

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=t("btn_pay_balance", lang, amount=additional_cost), callback_data="myads:topup_pay_balance"))
    kb.row(InlineKeyboardButton(text=t("btn_pay_stars", lang, stars=stars_amount), callback_data="myads:topup_pay_stars"))
    kb.row(InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="myads:topup_cancel"))

    await message.answer(
        t("myads_topup_review", lang, count=count, cost=additional_cost),
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "myads:topup_cancel")
async def myads_topup_cancel(callback: CallbackQuery, state: FSMContext):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await state.clear()
    await callback.message.edit_text(t("myads_topup_cancelled", lang))
    await callback.answer()


async def _apply_topup(request_id: int, task_id: int, count: int, additional_cost: float):
    """Применить доплату: увеличить лимит задания и заявки, сохранив ту же
    цену за пользователя, что была зафиксирована изначально."""
    net_addition = additional_cost * (1 - COMMISSION_PERCENT / 100)
    await db.increment_task_capacity(task_id, count, net_addition)
    await db.increment_ad_request_capacity(request_id, additional_cost, count)


@router.callback_query(F.data == "myads:topup_pay_balance")
async def myads_topup_pay_balance(callback: CallbackQuery, state: FSMContext):
    """Оплата доп. участников с баланса"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    data = await state.get_data()
    request_id = data.get("topup_request_id")
    count = data.get("topup_count")
    cost = data.get("topup_cost")

    if request_id is None or not count or cost is None:
        await callback.answer(t("ad_data_stale", lang), show_alert=True)
        return

    req = await db.get_ad_request(request_id)
    if not req or req.user_id != callback.from_user.id or req.status != "accepted" or not req.task_id:
        await callback.answer(t("myads_topup_unavailable_now", lang), show_alert=True)
        return
    task = await db.get_task(req.task_id)
    if not task or not task.is_active:
        await callback.answer(t("myads_task_stopped", lang), show_alert=True)
        return

    if not user or user.verified_balance < cost:
        balance = user.verified_balance if user else 0.0
        await callback.answer(
            t("myads_topup_insufficient_balance", lang, balance=balance, cost=cost),
            show_alert=True,
        )
        return

    await db.adjust_user_balance(callback.from_user.id, verified_delta=-cost)
    await _apply_topup(request_id, req.task_id, count, cost)
    await db.record_ad_payment(request_id, "balance", cost, count)
    await state.clear()

    await callback.message.edit_text(
        t("myads_topup_done_balance", lang, count=count, cost=cost),
        parse_mode="HTML",
    )
    await callback.answer()

    await _notify_admins_plain(
        callback.bot,
        f"➕ Пользователь {callback.from_user.id} доплатил ${cost:.2f} за {count} доп. мест "
        f"в задании #{req.task_id} (заявка #{request_id}), оплата с баланса",
    )
    await db.log_action(
        callback.from_user.id, "ad_task_topup",
        f"AdRequest #{request_id}, Task #{req.task_id}, +{count} slots, +${cost:.2f}, method=balance",
    )


@router.callback_query(F.data == "myads:topup_pay_stars")
async def myads_topup_pay_stars(callback: CallbackQuery, state: FSMContext):
    """Выставить инвойс на оплату доп. участников звёздами"""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    data = await state.get_data()
    request_id = data.get("topup_request_id")
    count = data.get("topup_count")
    cost = data.get("topup_cost")

    if request_id is None or not count or cost is None:
        await callback.answer(t("ad_data_stale", lang), show_alert=True)
        return

    req = await db.get_ad_request(request_id)
    if not req or req.user_id != callback.from_user.id or req.status != "accepted" or not req.task_id:
        await callback.answer(t("myads_topup_unavailable_now", lang), show_alert=True)
        return
    task = await db.get_task(req.task_id)
    if not task or not task.is_active:
        await callback.answer(t("myads_task_stopped", lang), show_alert=True)
        return

    from config import STARS_PER_USD
    stars_amount = max(1, round(cost * STARS_PER_USD))

    await callback.answer()
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=t("myads_topup_invoice_title", lang),
        description=t("myads_topup_invoice_description", lang, count=count, task_id=req.task_id),
        payload=f"ad_topup:{request_id}:{count}:{callback.from_user.id}:{int(time.time())}",
        currency="XTR",
        prices=[LabeledPrice(label=t("myads_topup_invoice_label", lang), amount=stars_amount)],
        provider_token="",
    )


async def _process_topup_successful_payment(message: Message, state: FSMContext, payment):
    """Обработать успешную звёздную оплату доп. участников"""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    try:
        _, request_id_s, count_s, _user_id_s, _ts = payment.invoice_payload.split(":")
        request_id = int(request_id_s)
        count = int(count_s)
    except (ValueError, IndexError):
        return

    req = await db.get_ad_request(request_id)
    if not req or req.user_id != message.from_user.id or req.status != "accepted" or not req.task_id:
        try:
            await message.bot.refund_star_payment(
                user_id=message.from_user.id,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
            )
        except Exception:
            pass
        await message.answer(t("myads_topup_apply_failed_unavailable", lang))
        return

    task = await db.get_task(req.task_id)
    if not task or not task.is_active:
        try:
            await message.bot.refund_star_payment(
                user_id=message.from_user.id,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
            )
        except Exception:
            pass
        await message.answer(t("myads_topup_apply_failed_stopped", lang))
        return

    cost = req.budget / req.target_subscribers * count if req.target_subscribers else 0.0
    await _apply_topup(request_id, req.task_id, count, cost)
    await db.record_ad_payment(
        request_id, "stars", cost, count,
        stars_charge_id=payment.telegram_payment_charge_id,
    )
    await state.clear()

    await message.answer(
        t("myads_topup_done_stars", lang, count=count, stars=payment.total_amount),
        parse_mode="HTML",
    )

    await _notify_admins_plain(
        message.bot,
        f"➕ Пользователь {message.from_user.id} доплатил {payment.total_amount}⭐ за {count} доп. мест "
        f"в задании #{req.task_id} (заявка #{request_id})",
    )
    await db.log_action(
        message.from_user.id, "ad_task_topup",
        f"AdRequest #{request_id}, Task #{req.task_id}, +{count} slots, method=stars, stars={payment.total_amount}",
    )

