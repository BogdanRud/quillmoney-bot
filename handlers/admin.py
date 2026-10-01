"""
Обработчики панели администратора
"""
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from database.db import Database
from database.models import UserRole
from keyboards.menus import (
    get_admin_menu, get_admin_panel, get_moder_panel, get_role_menu,
    get_withdrawal_action_keyboard, get_withdrawal_notification_keyboard,
    get_ticket_accept_keyboard
)
from utils.helpers import (
    format_withdrawal_request, format_support_ticket,
    is_owner
)
from utils.security import tg_escape
from utils.i18n import all_variants, t, DEFAULT_LANG, SUPPORTED_LANGS, lang_name
from handlers.auth import db

COMMISSION_PERCENT = 15
_ADMIN_PANEL_TEXTS = all_variants("btn_admin_panel")
_MODER_PANEL_TEXTS = all_variants("btn_moder_panel")
_CRM_PANEL_TEXTS = all_variants("btn_crm_panel")

router = Router()


class AdminStates(StatesGroup):
    """Состояния для админ-панели"""
    waiting_for_channel = State()
    waiting_for_reward = State()
    waiting_for_subscribers = State()
    waiting_for_broadcast_langs = State()
    waiting_for_broadcast_content = State()
    waiting_for_broadcast_confirm = State()
    waiting_for_admin_username = State()
    waiting_for_revoke_admin_username = State()
    waiting_for_moder_username = State()
    waiting_for_revoke_moder_username = State()
    waiting_for_manager_username = State()
    waiting_for_revoke_manager_username = State()
    waiting_for_block_username = State()
    waiting_for_unblock_username = State()
    waiting_for_delete_username = State()
    waiting_for_contest_places = State()
    waiting_for_contest_prize = State()
    waiting_for_contest_duration = State()


async def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь админом"""
    user = await db.get_user(user_id)
    return user and user.role == UserRole.ADMIN


async def is_moder_or_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь модератором или админом"""
    user = await db.get_user(user_id)
    return user and user.role in (UserRole.ADMIN, UserRole.MODER)


async def _lang(user_id: int) -> str:
    """Язык пользователя (для собственных сообщений/экранов вызывающего)."""
    user = await db.get_user(user_id)
    return user.language if user else DEFAULT_LANG


def get_staff_panel(user) -> "InlineKeyboardMarkup":
    """Вернуть нужную inline-панель по роли, на языке пользователя"""
    lang = user.language if user else DEFAULT_LANG
    if user and user.role == UserRole.ADMIN:
        return get_admin_panel(lang)
    return get_moder_panel(lang)


@router.message(F.text.in_(_ADMIN_PANEL_TEXTS))
async def show_admin_panel(message: Message):
    """Показать панель администратора"""
    lang = await _lang(message.from_user.id)
    if not await is_admin(message.from_user.id):
        await message.answer(t("admin_no_access_feature", lang))
        return

    from config import WEB_ADMIN_URL

    await message.answer(
        t("admin_panel_text", lang, url=WEB_ADMIN_URL),
        reply_markup=get_admin_panel(lang),
        parse_mode="HTML",
    )
    await db.log_action(message.from_user.id, "admin_panel_opened")


@router.message(F.text.in_(_CRM_PANEL_TEXTS))
async def show_crm_panel(message: Message):
    """Показать ссылку для входа в CRM (менеджерам, а также админу/владельцу)."""
    lang = await _lang(message.from_user.id)
    user = await db.get_user(message.from_user.id)
    is_allowed = (
        (user and user.role in (UserRole.MANAGER, UserRole.ADMIN))
        or await is_admin(message.from_user.id)
    )
    if not is_allowed:
        await message.answer(t("admin_no_access_feature", lang))
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from config import WEB_ADMIN_URL

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("admin_btn_web_panel", lang), url=WEB_ADMIN_URL)],
    ])
    await message.answer(
        t("crm_panel_text", lang, url=WEB_ADMIN_URL),
        reply_markup=kb,
        parse_mode="HTML",
    )


@router.message(F.text.in_(_MODER_PANEL_TEXTS))
async def show_moder_panel(message: Message):
    """Показать панель модератора"""
    lang = await _lang(message.from_user.id)
    if not await is_moder_or_admin(message.from_user.id):
        await message.answer(t("admin_no_access_feature", lang))
        return

    await message.answer(
        t("moder_panel_text", lang),
        reply_markup=get_moder_panel(lang),
        parse_mode="HTML",
    )
    await db.log_action(message.from_user.id, "moder_panel_opened")


@router.callback_query(F.data == "admin:create_ad")
async def start_create_ad(callback: CallbackQuery, state: FSMContext):
    """Начать создание рекламы"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_create_ad_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_channel)
    await callback.answer()


@router.message(AdminStates.waiting_for_channel)
async def process_channel(message: Message, state: FSMContext):
    """Обработать ссылку на канал"""
    lang = await _lang(message.from_user.id)
    channel_text = message.text.strip()

    # Извлекаем username канала
    channel_username = None
    if channel_text.startswith("https://t.me/"):
        channel_username = channel_text.split("/")[-1].lstrip("@")
    elif channel_text.startswith("@"):
        channel_username = channel_text.lstrip("@")
    else:
        channel_username = channel_text.lstrip("@")

    # Проверяем, добавлен ли бот в канал
    try:
        bot_member = await message.bot.get_chat_member(f"@{channel_username}", message.bot.id)
        if bot_member.status not in ['administrator', 'creator']:
            await message.answer(t("admin_bot_not_admin_in_channel", lang))
            return
    except Exception as e:
        await message.answer(t("admin_channel_check_error", lang, error=str(e)))
        return

    await state.update_data(channel_username=channel_username)
    await state.set_state(AdminStates.waiting_for_reward)

    await message.answer(
        t("admin_channel_set_reward_prompt", lang, channel=channel_username),
        parse_mode="HTML",
    )


@router.message(AdminStates.waiting_for_reward)
async def process_reward(message: Message, state: FSMContext):
    """Обработать сумму вознаграждения"""
    lang = await _lang(message.from_user.id)
    try:
        reward = float(message.text.replace(",", "."))
        if reward <= 0:
            raise ValueError
    except ValueError:
        await message.answer(t("admin_invalid_amount", lang))
        return

    await state.update_data(reward_per_user=reward)
    await state.set_state(AdminStates.waiting_for_subscribers)

    await message.answer(t("admin_reward_set_subscribers_prompt", lang, reward=reward))


@router.message(AdminStates.waiting_for_subscribers)
async def process_subscribers(message: Message, state: FSMContext):
    """Обработать количество подписчиков"""
    lang = await _lang(message.from_user.id)
    try:
        subscribers = int(message.text)
        if subscribers <= 0:
            raise ValueError
    except ValueError:
        await message.answer(t("admin_invalid_integer", lang))
        return

    data = await state.get_data()
    channel_username = data.get("channel_username")
    reward_per_user = data.get("reward_per_user")
    total_reward = reward_per_user * subscribers

    # Создаем задание (reward_amount хранит общую сумму = per_user * subscribers)
    task = await db.create_task(
        channel_username=channel_username,
        channel_id=None,
        reward_amount=total_reward,
        required_subscribers=subscribers
    )

    caller = await db.get_user(message.from_user.id)
    await message.answer(
        t(
            "admin_ad_created", lang,
            channel=channel_username, reward=reward_per_user,
            subs=subscribers, total=total_reward, task_id=task.task_id,
        ),
        reply_markup=get_role_menu(caller),
        parse_mode="HTML"
    )

    await db.log_action(
        message.from_user.id,
        "ad_created",
        f"Task #{task.task_id}, Channel: @{channel_username}"
    )
    await state.clear()


_CONTEST_DURATION_UNIT_MINUTES = {"h": 60, "d": 1440, "w": 10080, "m": 43200, "y": 525600}
_CONTEST_DURATION_RE = re.compile(r"^(\d+)\s*([hdwmy])$")


def _parse_contest_duration_minutes(text: str):
    """Голое число — минуты; либо короткая запись 3d/12h/1w/1m/1y."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    match = _CONTEST_DURATION_RE.match(text)
    if not match:
        return None
    amount, unit = match.groups()
    return int(amount) * _CONTEST_DURATION_UNIT_MINUTES[unit]


def _contests_admin_keyboard(contests, lang: str):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    builder = InlineKeyboardBuilder()
    for c in contests:
        builder.row(InlineKeyboardButton(text=f"🏆 {c.title}", callback_data=f"admin:contest_view:{c.contest_id}"))
    builder.row(InlineKeyboardButton(text=t("btn_create_contest", lang), callback_data="admin:create_contest"))
    builder.row(InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:panel_back"))
    return builder.as_markup()


@router.callback_query(F.data == "admin:contests")
async def show_contests_admin(callback: CallbackQuery):
    """Список активных конкурсов с возможностью создать новый или открыть существующий."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    contests = await db.get_active_contests()
    text = t("admin_contests_list_title", lang) if contests else t("admin_contests_list_empty", lang)
    await callback.message.edit_text(
        text, reply_markup=_contests_admin_keyboard(contests, lang), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:contest_view:"))
async def admin_contest_view(callback: CallbackQuery):
    """Полная карточка конкурса + текущий топ + кнопка удаления."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    from handlers.contests import build_contest_card_text, build_leaderboard_text

    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    contest_id = int(callback.data.split(":")[2])
    contest = await db.get_contest(contest_id)
    if not contest:
        await callback.answer(t("admin_contest_not_found", lang), show_alert=True)
        return

    card_text = await build_contest_card_text(contest, lang)
    leaderboard_text = await build_leaderboard_text(contest, lang)
    text = f"{card_text}\n\n{leaderboard_text}"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=t("admin_contest_delete_btn", lang), callback_data=f"admin:contest_delete:{contest_id}"
    ))
    builder.row(InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:contests"))

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:contest_delete:"))
async def admin_contest_delete(callback: CallbackQuery):
    """Удалить (остановить) конкурс и вернуться к обновлённому списку."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    contest_id = int(callback.data.split(":")[2])
    contest = await db.get_contest(contest_id)
    if not contest:
        await callback.answer(t("admin_contest_not_found", lang), show_alert=True)
        return

    await db.cancel_contest(contest_id)
    await db.log_action(callback.from_user.id, "contest_deleted", f"Contest #{contest_id}")
    await callback.answer(t("admin_contest_deleted_alert", lang, title=contest.title), show_alert=False)

    contests = await db.get_active_contests()
    text = t("admin_contests_list_title", lang) if contests else t("admin_contests_list_empty", lang)
    await callback.message.edit_text(
        text, reply_markup=_contests_admin_keyboard(contests, lang), parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:create_contest")
async def start_create_contest(callback: CallbackQuery, state: FSMContext):
    """Начать создание конкурса «Топ по приглашениям»."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    await callback.message.edit_text(t("contest_admin_intro", lang), parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_contest_places)
    await callback.answer()


@router.message(AdminStates.waiting_for_contest_places)
async def process_contest_places(message: Message, state: FSMContext):
    lang = await _lang(message.from_user.id)
    try:
        places = int(message.text)
        if places <= 0:
            raise ValueError
    except ValueError:
        await message.answer(t("admin_invalid_integer", lang))
        return

    await state.update_data(contest_places=places, contest_prizes=[], contest_current_place=1)
    await state.set_state(AdminStates.waiting_for_contest_prize)
    await message.answer(t("contest_admin_ask_prize_amount", lang, place=1))


@router.message(AdminStates.waiting_for_contest_prize)
async def process_contest_prize(message: Message, state: FSMContext):
    lang = await _lang(message.from_user.id)
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer(t("admin_invalid_amount", lang))
        return

    data = await state.get_data()
    places = data["contest_places"]
    current_place = data["contest_current_place"]
    prizes = data["contest_prizes"] + [(current_place, amount)]

    if current_place < places:
        await state.update_data(contest_prizes=prizes, contest_current_place=current_place + 1)
        await message.answer(t("contest_admin_ask_prize_amount", lang, place=current_place + 1))
        return

    await state.update_data(contest_prizes=prizes)
    await state.set_state(AdminStates.waiting_for_contest_duration)
    await message.answer(t("contest_admin_ask_duration", lang), parse_mode="HTML")


@router.message(AdminStates.waiting_for_contest_duration)
async def process_contest_duration(message: Message, state: FSMContext):
    from datetime import datetime, timedelta
    from handlers.contests import build_contest_card_text

    lang = await _lang(message.from_user.id)
    minutes = _parse_contest_duration_minutes(message.text)
    if not minutes or minutes <= 0:
        await message.answer(t("contest_admin_invalid_duration", lang))
        return

    data = await state.get_data()
    ends_at = (datetime.now() + timedelta(minutes=minutes)).isoformat()

    contest = await db.create_contest(
        ends_at=ends_at,
        prizes=data["contest_prizes"],
        contest_type="referral_top",
        created_by=message.from_user.id,
    )

    card_text = await build_contest_card_text(contest, lang)
    caller = await db.get_user(message.from_user.id)
    await message.answer(
        t("contest_admin_created", lang, card=card_text),
        reply_markup=get_role_menu(caller),
        parse_mode="HTML",
    )
    await db.log_action(message.from_user.id, "contest_created", f"Contest #{contest.contest_id}")
    await state.clear()


def _broadcast_lang_kb(selected: list, lang: str):
    """Клавиатура выбора языковой аудитории — чекбоксы + быстрый выбор всех 3."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for code in SUPPORTED_LANGS:
        mark = "✅" if code in selected else "⬜"
        rows.append([InlineKeyboardButton(
            text=f"{mark} {lang_name(code)}", callback_data=f"bcastlang:toggle:{code}",
        )])
    rows.append([InlineKeyboardButton(text=t("admin_broadcast_lang_all_btn", lang), callback_data="bcastlang:all")])
    rows.append([
        InlineKeyboardButton(text=t("admin_broadcast_lang_next_btn", lang), callback_data="bcastlang:next"),
        InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="broadcast:cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "admin:broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Начать рассылку — сперва выбор языковой аудитории (все 3 или выборочно)."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    await state.update_data(broadcast_langs_selected=[])
    await state.set_state(AdminStates.waiting_for_broadcast_langs)
    await callback.message.edit_text(
        t("admin_broadcast_lang_select_title", lang),
        reply_markup=_broadcast_lang_kb([], lang),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(AdminStates.waiting_for_broadcast_langs, F.data.startswith("bcastlang:toggle:"))
async def toggle_broadcast_lang(callback: CallbackQuery, state: FSMContext):
    """Включить/выключить язык в выборке аудитории."""
    lang = await _lang(callback.from_user.id)
    code = callback.data.split(":")[-1]
    data = await state.get_data()
    selected = list(data.get("broadcast_langs_selected", []))
    if code in selected:
        selected.remove(code)
    else:
        selected.append(code)
    await state.update_data(broadcast_langs_selected=selected)
    await callback.message.edit_reply_markup(reply_markup=_broadcast_lang_kb(selected, lang))
    await callback.answer()


@router.callback_query(AdminStates.waiting_for_broadcast_langs, F.data == "bcastlang:all")
async def select_all_broadcast_langs(callback: CallbackQuery, state: FSMContext):
    """Быстрый выбор — сразу все 3 языка."""
    lang = await _lang(callback.from_user.id)
    selected = list(SUPPORTED_LANGS)
    await state.update_data(broadcast_langs_selected=selected)
    await callback.message.edit_reply_markup(reply_markup=_broadcast_lang_kb(selected, lang))
    await callback.answer()


@router.callback_query(AdminStates.waiting_for_broadcast_langs, F.data == "bcastlang:next")
async def proceed_broadcast_langs(callback: CallbackQuery, state: FSMContext):
    """Языки выбраны — переходим к сбору сообщения для каждого по очереди."""
    lang = await _lang(callback.from_user.id)
    data = await state.get_data()
    selected = list(data.get("broadcast_langs_selected", []))
    if not selected:
        await callback.answer(t("admin_broadcast_lang_none_selected", lang), show_alert=True)
        return

    await state.update_data(
        broadcast_langs_queue=list(selected),
        broadcast_langs_selected=selected,
        broadcast_msgs={},
    )
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await callback.message.edit_text(
        t("admin_broadcast_content_prompt", lang, lang_label=lang_name(selected[0])),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast_content)
async def process_broadcast_content(message: Message, state: FSMContext):
    """Собрать сообщение для текущего языка в очереди; когда все собраны — попросить подтверждение."""
    if not await is_moder_or_admin(message.from_user.id):
        await state.clear()
        return
    lang = await _lang(message.from_user.id)

    data = await state.get_data()
    queue = list(data.get("broadcast_langs_queue", []))
    if not queue:
        await state.clear()
        return
    current_lang = queue.pop(0)

    msgs = dict(data.get("broadcast_msgs", {}))
    msgs[current_lang] = {"message_id": message.message_id, "chat_id": message.chat.id}
    await state.update_data(broadcast_langs_queue=queue, broadcast_msgs=msgs)

    if queue:
        await message.answer(
            t("admin_broadcast_content_prompt", lang, lang_label=lang_name(queue[0])),
            parse_mode="HTML",
        )
        return

    # Сообщения для всех выбранных языков собраны — считаем аудиторию и просим подтверждение
    selected = list(data.get("broadcast_langs_selected", []))
    users = await db.get_all_users()
    counts = {code: 0 for code in selected}
    for u in users:
        if u.language in counts:
            counts[u.language] += 1
    total = sum(counts.values())
    breakdown = "\n".join(f"• {lang_name(code)}: {counts[code]}" for code in selected)

    await state.set_state(AdminStates.waiting_for_broadcast_confirm)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("admin_broadcast_send_btn", lang, count=total), callback_data="broadcast:confirm"),
        InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="broadcast:cancel"),
    ]])
    await message.answer(
        t("admin_broadcast_multi_confirm_prompt", lang, breakdown=breakdown, count=total),
        reply_markup=confirm_kb,
        parse_mode="HTML",
    )


@router.callback_query(F.data == "broadcast:cancel")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Отменить рассылку на любом шаге"""
    lang = await _lang(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text(t("admin_broadcast_cancelled", lang), reply_markup=None)
    await callback.answer()


@router.callback_query(F.data == "broadcast:confirm")
async def confirm_broadcast(callback: CallbackQuery, state: FSMContext):
    """Выполнить рассылку после подтверждения — каждому пользователю на его языке."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    data = await state.get_data()
    await state.clear()

    msgs = data.get("broadcast_msgs", {})
    if not msgs:
        await callback.message.edit_text(t("admin_broadcast_not_found", lang), reply_markup=None)
        await callback.answer()
        return

    users = await db.get_all_users()
    targeted = [u for u in users if u.language in msgs]
    if not targeted:
        await callback.message.edit_text(
            t("admin_broadcast_no_users", lang), reply_markup=None
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        t("admin_broadcast_starting", lang, count=len(targeted)),
        reply_markup=None,
    )
    await callback.answer()

    # Пересылаем каждому пользователю сообщение на его языке
    from config import BROADCAST_DELAY_SECONDS
    import asyncio
    success_count = 0
    fail_count = 0

    for user in targeted:
        src = msgs[user.language]
        try:
            await callback.bot.copy_message(
                chat_id=user.user_id,
                from_chat_id=src["chat_id"],
                message_id=src["message_id"],
            )
            success_count += 1
        except Exception:
            fail_count += 1
        if BROADCAST_DELAY_SECONDS > 0:
            await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    caller = await db.get_user(callback.from_user.id)
    await callback.message.answer(
        t("admin_broadcast_done", lang, success=success_count, fail=fail_count),
        reply_markup=get_role_menu(caller),
    )

    await db.log_action(
        callback.from_user.id,
        "broadcast_sent",
        f"Sent to {success_count} users, {fail_count} failed, langs={','.join(sorted(msgs.keys()))}",
    )


@router.callback_query(F.data == "admin:withdrawals")
async def show_withdrawals(callback: CallbackQuery):
    """Показать заявки на вывод"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    requests = await db.get_pending_withdrawal_requests()

    if not requests:
        await callback.message.edit_text(
            t("admin_no_pending_withdrawals", lang),
            reply_markup=get_admin_panel(lang)
        )
    else:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton

        text = t("admin_withdrawals_title", lang)
        builder = InlineKeyboardBuilder()
        for req in requests:
            user = await db.get_user(req.user_id)
            name = tg_escape(user.first_name) if user else t("admin_unknown_user", lang)
            text += t("admin_withdrawal_line", lang, id=req.request_id, amount=req.amount, name=name)
            builder.row(
                InlineKeyboardButton(
                    text=f"✅ #{req.request_id}",
                    callback_data=f"withdrawal:accept:{req.request_id}",
                ),
                InlineKeyboardButton(
                    text=f"❌ #{req.request_id}",
                    callback_data=f"withdrawal:reject:{req.request_id}",
                ),
            )

        builder.row(
            InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:panel_back")
        )
        await callback.message.edit_text(
            text, parse_mode="HTML", reply_markup=builder.as_markup()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("withdrawal:accept:"))
async def accept_withdrawal(callback: CallbackQuery):
    """Принять заявку на вывод"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    request_id = int(callback.data.split(":")[2])
    request = await db.get_withdrawal_request(request_id)

    if not request or request.status != "pending":
        await callback.answer(t("admin_withdrawal_not_found", lang), show_alert=True)
        return

    ok, reason = await db.accept_withdrawal_atomic(request_id, callback.from_user.id)
    if not ok:
        messages = {
            "already_processed": t("admin_withdrawal_already_processed", lang),
            "insufficient_balance": t("admin_withdrawal_insufficient_balance", lang),
        }
        await callback.answer(messages.get(reason, t("admin_generic_processing_error", lang)), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_withdrawal_accepted", lang, id=request_id),
        reply_markup=get_admin_panel(lang)
    )

    # Уведомляем пользователя (на его языке)
    recipient_lang = await _lang(request.user_id)
    await callback.bot.send_message(
        request.user_id,
        t("dm_withdrawal_accepted", recipient_lang, id=request_id, amount=request.amount),
    )

    await db.log_action(
        callback.from_user.id,
        "withdrawal_accepted",
        f"Request #{request_id}, User: {request.user_id}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("withdrawal:reject:"))
async def reject_withdrawal(callback: CallbackQuery):
    """Отклонить заявку на вывод"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    request_id = int(callback.data.split(":")[2])
    request = await db.get_withdrawal_request(request_id)

    if not request or request.status != "pending":
        await callback.answer(t("admin_withdrawal_not_found", lang), show_alert=True)
        return

    ok, reason = await db.reject_withdrawal_atomic(request_id, callback.from_user.id)
    if not ok:
        await callback.answer(t("admin_withdrawal_already_processed", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_withdrawal_rejected", lang, id=request_id),
        reply_markup=get_admin_panel(lang),
    )

    try:
        recipient_lang = await _lang(request.user_id)
        await callback.bot.send_message(
            request.user_id,
            t("dm_withdrawal_rejected", recipient_lang, id=request_id, amount=request.amount),
        )
    except Exception:
        pass

    await db.log_action(
        callback.from_user.id,
        "withdrawal_rejected",
        f"Request #{request_id}, User: {request.user_id}",
    )
    await callback.answer()


@router.callback_query(F.data == "admin:panel_back")
async def admin_panel_back(callback: CallbackQuery):
    """Вернуться в панель администратора/модератора"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return
    caller = await db.get_user(callback.from_user.id)
    panel = get_staff_panel(caller)
    if caller and caller.role == UserRole.ADMIN:
        from config import WEB_ADMIN_URL
        title = t("admin_panel_text", lang, url=WEB_ADMIN_URL)
    else:
        title = t("moder_panel_text", lang)
    await callback.message.edit_text(title, reply_markup=panel, parse_mode="HTML")
    await callback.answer()


# ───────────── TICKETS IN-BOT ─────────────────────────────────────

def _ticket_reason_label(reason: str, lang: str = DEFAULT_LANG) -> str:
    return {
        "question": t("ticket_reason_question", lang),
        "problem": t("ticket_reason_problem", lang),
        "suggestion": t("ticket_reason_suggestion", lang),
        "block_appeal": t("ticket_reason_block_appeal", lang),
    }.get(reason, reason)


def _ticket_status_label(status: str, lang: str = DEFAULT_LANG) -> str:
    return {
        "open": t("ticket_status_open", lang),
        "taken": t("ticket_status_taken", lang),
        "closed": t("ticket_status_closed", lang),
    }.get(status, status)


@router.callback_query(F.data.in_({"admin:support_tickets", "admin:tickets"}))
async def show_support_tickets(callback: CallbackQuery):
    """Список тикетов поддержки прямо в боте."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    tickets = await db.get_open_tickets_bot()

    if not tickets:
        await callback.answer(t("admin_tickets_none_open", lang), show_alert=False)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    text = t("admin_tickets_title", lang, count=len(tickets))
    builder = InlineKeyboardBuilder()

    for tk in tickets:
        user = await db.get_user(tk.user_id)
        uname = f"@{user.username}" if user and user.username else f"ID {tk.user_id}"
        short_desc = (tk.description[:60] + "…") if len(tk.description) > 60 else tk.description
        text += t(
            "admin_ticket_line", lang,
            id=tk.ticket_id, reason=_ticket_reason_label(tk.reason, lang),
            status=_ticket_status_label(tk.status, lang), uname=uname, desc=short_desc,
        )
        builder.row(
            InlineKeyboardButton(text=t("admin_ticket_view_btn", lang, id=tk.ticket_id), callback_data=f"ticket:view:{tk.ticket_id}"),
            InlineKeyboardButton(text=t("admin_ticket_close_btn", lang, id=tk.ticket_id), callback_data=f"ticket:close:{tk.ticket_id}"),
        )

    builder.row(InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:panel_back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("ticket:view:"))
async def view_ticket(callback: CallbackQuery):
    """Подробный просмотр тикета."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    ticket_id = int(callback.data.split(":")[2])
    ticket = await db.get_support_ticket(ticket_id)
    if not ticket:
        await callback.answer(t("admin_ticket_not_found", lang), show_alert=True)
        return

    user = await db.get_user(ticket.user_id)
    uname = f"@{tg_escape(user.username)}" if user and user.username else f"ID {ticket.user_id}"
    fname = tg_escape(user.first_name) if user else "—"
    date_str = ticket.created_at.strftime("%d.%m.%Y %H:%M") if ticket.created_at else "—"

    text = t(
        "admin_ticket_detail", lang,
        id=ticket.ticket_id, fname=fname, uname=uname,
        reason=_ticket_reason_label(ticket.reason, lang), date=date_str,
        status=_ticket_status_label(ticket.status, lang), desc=tg_escape(ticket.description),
    )

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    if ticket.status != "closed":
        if ticket.status == "open":
            builder.row(InlineKeyboardButton(
                text=t("admin_ticket_take_btn", lang),
                callback_data=f"ticket:take:{ticket.ticket_id}"
            ))
        builder.row(InlineKeyboardButton(
            text=t("admin_ticket_close_full_btn", lang),
            callback_data=f"ticket:close:{ticket.ticket_id}"
        ))
    builder.row(InlineKeyboardButton(text=t("admin_ticket_back_btn", lang), callback_data="admin:tickets"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("ticket:take:"))
async def bot_take_ticket(callback: CallbackQuery):
    """Взять тикет в работу (из бота)."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    ticket_id = int(callback.data.split(":")[2])
    ticket = await db.get_support_ticket(ticket_id)
    if not ticket or ticket.status != "open":
        await callback.answer(t("admin_ticket_already_taken_or_closed", lang), show_alert=True)
        return

    taken = await db.take_support_ticket(ticket_id, callback.from_user.id)
    if not taken:
        await callback.answer(t("admin_ticket_taken_by_other", lang), show_alert=True)
        return
    await callback.answer(t("admin_ticket_taken_alert", lang))

    try:
        recipient_lang = await _lang(ticket.user_id)
        await callback.bot.send_message(
            ticket.user_id,
            t("dm_ticket_taken", recipient_lang, id=ticket_id),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await db.log_action(callback.from_user.id, "ticket_taken_bot", f"Ticket #{ticket_id}")
    # Обновляем вид тикета
    ticket = await db.get_support_ticket(ticket_id)
    user = await db.get_user(ticket.user_id)
    uname = f"@{user.username}" if user and user.username else f"ID {ticket.user_id}"
    fname = user.first_name if user else "—"
    date_str = ticket.created_at.strftime("%d.%m.%Y %H:%M") if ticket.created_at else "—"
    text = t(
        "admin_ticket_detail", lang,
        id=ticket.ticket_id, fname=fname, uname=uname,
        reason=_ticket_reason_label(ticket.reason, lang), date=date_str,
        status=_ticket_status_label(ticket.status, lang), desc=ticket.description,
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t("admin_ticket_close_full_btn", lang), callback_data=f"ticket:close:{ticket_id}"))
    builder.row(InlineKeyboardButton(text=t("admin_ticket_back_btn", lang), callback_data="admin:tickets"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("ticket:close:"))
async def bot_close_ticket(callback: CallbackQuery):
    """Закрыть тикет из бота."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    ticket_id = int(callback.data.split(":")[2])
    ticket = await db.get_support_ticket(ticket_id)
    if not ticket or ticket.status == "closed":
        await callback.answer(t("admin_ticket_already_closed", lang), show_alert=True)
        return

    await db.close_support_ticket(ticket_id, callback.from_user.id)

    try:
        recipient_lang = await _lang(ticket.user_id)
        await callback.bot.send_message(
            ticket.user_id,
            t("dm_ticket_closed", recipient_lang, id=ticket_id),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.answer(t("admin_ticket_closed_alert", lang))
    await db.log_action(callback.from_user.id, "ticket_closed_bot", f"Ticket #{ticket_id}")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t("admin_ticket_back_btn", lang), callback_data="admin:tickets"))
    await callback.message.edit_text(
        t("admin_ticket_closed_title", lang, id=ticket_id),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# ───────────── APPEALS IN-BOT ─────────────────────────────────────

@router.callback_query(F.data == "admin:appeals")
async def show_appeals(callback: CallbackQuery):
    """Список апелляций прямо в боте."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    appeals = await db.get_open_appeals_bot()

    if not appeals:
        await callback.answer(t("admin_appeals_none_open", lang), show_alert=False)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    text = t("admin_appeals_title", lang, count=len(appeals))
    builder = InlineKeyboardBuilder()

    for a in appeals:
        user = await db.get_user(a.user_id)
        uname = f"@{tg_escape(user.username)}" if user and user.username else f"ID {a.user_id}"
        fname = tg_escape(user.first_name) if user else "—"
        short_desc = tg_escape((a.description[:80] + "…") if len(a.description) > 80 else a.description)
        date_str = a.created_at.strftime("%d.%m") if a.created_at else "—"
        text += t("admin_appeal_line", lang, id=a.ticket_id, fname=fname, uname=uname, date=date_str, desc=short_desc)
        builder.row(
            InlineKeyboardButton(
                text=t("admin_appeal_unblock_btn", lang, id=a.ticket_id),
                callback_data=f"bot_appeal:unblock:{a.ticket_id}:{a.user_id}"
            ),
            InlineKeyboardButton(
                text=t("admin_appeal_reject_btn", lang, id=a.ticket_id),
                callback_data=f"bot_appeal:reject:{a.ticket_id}"
            ),
        )

    builder.row(InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:panel_back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("bot_appeal:unblock:"))
async def bot_appeal_unblock(callback: CallbackQuery):
    """Одобрить апелляцию — разблокировать пользователя."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    parts = callback.data.split(":")
    ticket_id = int(parts[2])
    user_id = int(parts[3])

    ticket = await db.get_support_ticket(ticket_id)
    if not ticket or ticket.status == "closed":
        await callback.answer(t("admin_appeal_already_processed", lang), show_alert=True)
        return

    await db.unblock_user(user_id)
    await db.close_support_ticket(ticket_id, callback.from_user.id)

    try:
        recipient_lang = await _lang(user_id)
        await callback.bot.send_message(user_id, t("dm_appeal_approved", recipient_lang))
    except Exception:
        pass

    await callback.answer(t("admin_appeal_unblocked_alert", lang))
    await db.log_action(callback.from_user.id, "appeal_approved_bot", f"Ticket #{ticket_id}, User {user_id}")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t("admin_appeals_back_btn", lang), callback_data="admin:appeals"))
    await callback.message.edit_text(
        t("admin_appeal_unblock_title", lang, id=ticket_id, user_id=user_id),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("bot_appeal:reject:"))
async def bot_appeal_reject(callback: CallbackQuery):
    """Отклонить апелляцию — пользователь остаётся заблокированным."""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    parts = callback.data.split(":")
    ticket_id = int(parts[2])

    ticket = await db.get_support_ticket(ticket_id)
    if not ticket or ticket.status == "closed":
        await callback.answer(t("admin_appeal_already_processed", lang), show_alert=True)
        return

    await db.close_support_ticket(ticket_id, callback.from_user.id)

    try:
        recipient_lang = await _lang(ticket.user_id)
        await callback.bot.send_message(ticket.user_id, t("dm_appeal_rejected", recipient_lang))
    except Exception:
        pass

    await callback.answer(t("admin_appeal_rejected_alert", lang))
    await db.log_action(callback.from_user.id, "appeal_rejected_bot", f"Ticket #{ticket_id}")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=t("admin_appeals_back_btn", lang), callback_data="admin:appeals"))
    await callback.message.edit_text(
        t("admin_appeal_reject_title", lang, id=ticket_id),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("ticket:accept:"))
async def accept_ticket(callback: CallbackQuery):
    """Принять тикет"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    ticket_id = int(callback.data.split(":")[2])
    ticket = await db.get_support_ticket(ticket_id)

    if not ticket or ticket.status != "open":
        await callback.answer(t("admin_ticket_not_found_or_processed", lang), show_alert=True)
        return

    if ticket.admin_id:
        admin = await db.get_user(ticket.admin_id)
        name = admin.first_name if admin and admin.first_name else t("admin_default_admin_word", lang)
        await callback.answer(
            t("admin_ticket_taken_by_name", lang, name=name),
            show_alert=True
        )
        return

    taken = await db.take_support_ticket(ticket_id, callback.from_user.id)
    if not taken:
        await callback.answer(t("admin_ticket_taken_by_other", lang), show_alert=True)
        return

    user = await db.get_user(ticket.user_id)
    ticket_text = format_support_ticket(ticket, user, lang)

    await callback.message.edit_text(
        f"{ticket_text}\n\n{t('admin_ticket_accepted_suffix', lang)}",
        reply_markup=get_admin_panel(lang),
        parse_mode="HTML"
    )

    recipient_lang = await _lang(ticket.user_id)
    await callback.bot.send_message(
        ticket.user_id,
        t("dm_ticket_accepted", recipient_lang, id=ticket_id),
    )

    await db.log_action(
        callback.from_user.id,
        "ticket_accepted",
        f"Ticket #{ticket_id}"
    )
    await callback.answer()


@router.callback_query(F.data == "admin:grant_admin")
async def start_grant_admin(callback: CallbackQuery, state: FSMContext):
    """Начать процесс выдачи админки"""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    if not user.username or not await is_owner(user.username):
        await callback.answer(t("admin_only_owner_grant", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_grant_admin_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_admin_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_admin_username)
async def process_grant_admin(message: Message, state: FSMContext):
    """Обработать выдачу админки"""
    if not await is_admin(message.from_user.id):
        await state.clear()
        return
    lang = await _lang(message.from_user.id)

    user = await db.get_user(message.from_user.id)
    if not user.username or not await is_owner(user.username):
        await message.answer(t("admin_only_owner_grant", lang))
        await state.clear()
        return

    username = message.text.strip().lstrip("@")

    # Ищем пользователя по username
    target_user = await db.get_user_by_username(username)

    if not target_user:
        await message.answer(
            t("admin_user_not_found_hint", lang, username=username),
            reply_markup=get_admin_menu(lang)
        )
        await state.clear()
        return

    if target_user.role == UserRole.ADMIN:
        await message.answer(
            t("admin_already_admin", lang, username=username),
            reply_markup=get_admin_menu(lang)
        )
        await state.clear()
        return

    # Выдаем админку
    await db.update_user_role(target_user.user_id, UserRole.ADMIN)

    await message.answer(
        t("admin_admin_granted", lang, username=username, user_id=target_user.user_id),
        reply_markup=get_admin_menu(lang)
    )

    # Уведомляем пользователя
    try:
        recipient_lang = target_user.language
        await message.bot.send_message(
            target_user.user_id,
            t("dm_admin_granted", recipient_lang),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await db.log_action(
        message.from_user.id,
        "admin_granted",
        f"Granted to @{username} (ID: {target_user.user_id})"
    )
    await state.clear()


@router.callback_query(F.data == "admin:revoke_admin")
async def start_revoke_admin(callback: CallbackQuery, state: FSMContext):
    """Начать процесс снятия роли Admin"""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    if not user.username or not await is_owner(user.username):
        await callback.answer(t("admin_only_owner_revoke", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_revoke_admin_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_revoke_admin_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_revoke_admin_username)
async def process_revoke_admin(message: Message, state: FSMContext):
    """Обработать снятие роли Admin"""
    if not await is_admin(message.from_user.id):
        await state.clear()
        return
    lang = await _lang(message.from_user.id)

    user = await db.get_user(message.from_user.id)
    if not user.username or not await is_owner(user.username):
        await message.answer(t("admin_only_owner_revoke", lang))
        await state.clear()
        return

    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(
            t("admin_user_not_found_generic", lang),
            reply_markup=get_admin_menu(lang)
        )
        await state.clear()
        return

    if target_user.user_id == message.from_user.id:
        await message.answer(t("admin_cant_revoke_self", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    if target_user.role != UserRole.ADMIN:
        await message.answer(
            t("admin_not_admin_role", lang, user_id=target_user.user_id),
            reply_markup=get_admin_menu(lang)
        )
        await state.clear()
        return

    await db.update_user_role(target_user.user_id, UserRole.USER)

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await message.answer(
        t("admin_admin_revoked", lang, name=name),
        reply_markup=get_admin_menu(lang)
    )

    try:
        await message.bot.send_message(
            target_user.user_id,
            t("dm_admin_revoked", target_user.language),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await db.log_action(
        message.from_user.id, "admin_revoked",
        f"Revoked from {name} (ID: {target_user.user_id})"
    )
    await state.clear()


@router.callback_query(F.data == "admin:grant_moder")
async def start_grant_moder(callback: CallbackQuery, state: FSMContext):
    """Начать процесс выдачи роли Moderator"""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_only_admin_grant_moder", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_grant_moder_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_moder_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_moder_username)
async def process_grant_moder(message: Message, state: FSMContext):
    """Выдать роль Moderator"""
    if not await is_admin(message.from_user.id):
        await state.clear()
        return
    lang = await _lang(message.from_user.id)

    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(t("admin_user_not_found_short", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    if target_user.role == UserRole.MODER:
        await message.answer(
            t("admin_already_moder", lang),
            reply_markup=get_admin_menu(lang)
        )
        await state.clear()
        return

    if target_user.role == UserRole.ADMIN:
        await message.answer(t("admin_cant_demote_admin", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    await db.update_user_role(target_user.user_id, UserRole.MODER)

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await message.answer(t("admin_moder_granted", lang, name=name), reply_markup=get_admin_menu(lang))

    try:
        await message.bot.send_message(
            target_user.user_id,
            t("dm_moder_granted", target_user.language),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await db.log_action(message.from_user.id, "moder_granted", f"Granted to {name} (ID: {target_user.user_id})")
    await state.clear()


@router.callback_query(F.data == "admin:revoke_moder")
async def start_revoke_moder(callback: CallbackQuery, state: FSMContext):
    """Начать процесс снятия роли Moderator"""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_only_admin_revoke_moder", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_revoke_moder_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_revoke_moder_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_revoke_moder_username)
async def process_revoke_moder(message: Message, state: FSMContext):
    """Снять роль Moderator"""
    if not await is_admin(message.from_user.id):
        await state.clear()
        return
    lang = await _lang(message.from_user.id)

    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(t("admin_user_not_found_short", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    if target_user.role != UserRole.MODER:
        await message.answer(t("admin_not_moder_role", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    await db.update_user_role(target_user.user_id, UserRole.USER)

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await message.answer(t("admin_moder_revoked", lang, name=name), reply_markup=get_admin_menu(lang))

    try:
        await message.bot.send_message(
            target_user.user_id,
            t("dm_moder_revoked", target_user.language),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await db.log_action(message.from_user.id, "moder_revoked", f"Revoked from {name} (ID: {target_user.user_id})")
    await state.clear()


@router.callback_query(F.data == "admin:grant_manager")
async def start_grant_manager(callback: CallbackQuery, state: FSMContext):
    """Начать процесс выдачи роли Manager (доступ только к CRM в веб-панели)"""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_only_admin_grant_manager", lang), show_alert=True)
        return

    await callback.message.edit_text(t("admin_grant_manager_prompt", lang), parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_manager_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_manager_username)
async def process_grant_manager(message: Message, state: FSMContext):
    """Выдать роль Manager"""
    if not await is_admin(message.from_user.id):
        await state.clear()
        return
    lang = await _lang(message.from_user.id)

    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(t("admin_user_not_found_short", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    if target_user.role == UserRole.MANAGER:
        await message.answer(t("admin_already_manager", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    if target_user.role in (UserRole.ADMIN, UserRole.MODER):
        await message.answer(t("admin_cant_demote_admin", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    await db.update_user_role(target_user.user_id, UserRole.MANAGER)

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await message.answer(t("admin_manager_granted", lang, name=name), reply_markup=get_admin_menu(lang))

    try:
        await message.bot.send_message(
            target_user.user_id,
            t("dm_manager_granted", target_user.language),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await db.log_action(message.from_user.id, "manager_granted", f"Granted to {name} (ID: {target_user.user_id})")
    await state.clear()


@router.callback_query(F.data == "admin:revoke_manager")
async def start_revoke_manager(callback: CallbackQuery, state: FSMContext):
    """Начать процесс снятия роли Manager"""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_only_admin_revoke_manager", lang), show_alert=True)
        return

    await callback.message.edit_text(t("admin_revoke_manager_prompt", lang), parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_revoke_manager_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_revoke_manager_username)
async def process_revoke_manager(message: Message, state: FSMContext):
    """Снять роль Manager"""
    if not await is_admin(message.from_user.id):
        await state.clear()
        return
    lang = await _lang(message.from_user.id)

    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(t("admin_user_not_found_short", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    if target_user.role != UserRole.MANAGER:
        await message.answer(t("admin_not_manager_role", lang), reply_markup=get_admin_menu(lang))
        await state.clear()
        return

    await db.update_user_role(target_user.user_id, UserRole.USER)

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await message.answer(t("admin_manager_revoked", lang, name=name), reply_markup=get_admin_menu(lang))

    try:
        await message.bot.send_message(
            target_user.user_id,
            t("dm_manager_revoked", target_user.language),
            parse_mode="HTML"
        )
    except Exception:
        pass

    await db.log_action(message.from_user.id, "manager_revoked", f"Revoked from {name} (ID: {target_user.user_id})")
    await state.clear()


@router.callback_query(F.data == "admin:block_user")
async def start_block_user(callback: CallbackQuery, state: FSMContext):
    """Начать блокировку пользователя"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_block_user_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_block_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_block_username)
async def process_block_user(message: Message, state: FSMContext):
    """Заблокировать пользователя"""
    if not await is_moder_or_admin(message.from_user.id):
        await state.clear()
        return

    caller = await db.get_user(message.from_user.id)
    lang = caller.language if caller else DEFAULT_LANG
    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(t("admin_user_not_found_short", lang), reply_markup=get_role_menu(caller))
        await state.clear()
        return

    if target_user.user_id == message.from_user.id:
        await message.answer(t("admin_cant_block_self", lang), reply_markup=get_role_menu(caller))
        await state.clear()
        return

    # Модератор не может заблокировать Администратора
    if target_user.role == UserRole.ADMIN and caller.role != UserRole.ADMIN:
        await message.answer(t("admin_cant_block_admin", lang), reply_markup=get_role_menu(caller))
        await state.clear()
        return

    await db.block_user(target_user.user_id)

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await message.answer(t("admin_user_blocked", lang, name=name), reply_markup=get_role_menu(caller))

    try:
        await message.bot.send_message(
            target_user.user_id,
            t("dm_user_blocked", target_user.language),
        )
    except Exception:
        pass

    await db.log_action(message.from_user.id, "user_blocked", f"Blocked {name} (ID: {target_user.user_id})")
    await state.clear()


@router.callback_query(F.data == "admin:unblock_user")
async def start_unblock_user(callback: CallbackQuery, state: FSMContext):
    """Начать разблокировку пользователя"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_unblock_user_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_unblock_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_unblock_username)
async def process_unblock_user(message: Message, state: FSMContext):
    """Разблокировать пользователя"""
    if not await is_moder_or_admin(message.from_user.id):
        await state.clear()
        return

    caller = await db.get_user(message.from_user.id)
    lang = caller.language if caller else DEFAULT_LANG
    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(t("admin_user_not_found_short", lang), reply_markup=get_role_menu(caller))
        await state.clear()
        return

    await db.unblock_user(target_user.user_id)

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await message.answer(t("admin_user_unblocked", lang, name=name), reply_markup=get_role_menu(caller))

    try:
        await message.bot.send_message(target_user.user_id, t("dm_user_unblocked", target_user.language))
    except Exception:
        pass

    await db.log_action(message.from_user.id, "user_unblocked", f"Unblocked {name} (ID: {target_user.user_id})")
    await state.clear()


@router.callback_query(F.data == "admin:delete_user")
async def start_delete_user(callback: CallbackQuery, state: FSMContext):
    """Начать удаление пользователя"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    await callback.message.edit_text(
        t("admin_delete_user_prompt", lang),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.waiting_for_delete_username)
    await callback.answer()


@router.message(AdminStates.waiting_for_delete_username)
async def process_delete_user(message: Message, state: FSMContext):
    """Удалить пользователя"""
    if not await is_moder_or_admin(message.from_user.id):
        await state.clear()
        return

    caller = await db.get_user(message.from_user.id)
    lang = caller.language if caller else DEFAULT_LANG
    target_user = await db.get_user_by_id_or_username(message.text.strip())

    if not target_user:
        await message.answer(t("admin_user_not_found_short", lang), reply_markup=get_role_menu(caller))
        await state.clear()
        return

    if target_user.user_id == message.from_user.id:
        await message.answer(t("admin_cant_delete_self", lang), reply_markup=get_role_menu(caller))
        await state.clear()
        return

    # Модератор не может удалить Администратора
    if target_user.role == UserRole.ADMIN and caller.role != UserRole.ADMIN:
        await message.answer(t("admin_cant_delete_admin", lang), reply_markup=get_role_menu(caller))
        await state.clear()
        return

    name = f"@{target_user.username}" if target_user.username else f"ID {target_user.user_id}"
    await db.delete_user(target_user.user_id)

    await message.answer(t("admin_user_deleted", lang, name=name), reply_markup=get_role_menu(caller))

    await db.log_action(message.from_user.id, "user_deleted", f"Deleted {name} (ID: {target_user.user_id})")
    await state.clear()


# ───────────── DELETE TASK IN-BOT (Admin only) ─────────────────────

@router.callback_query(F.data == "admin:delete_task")
async def show_delete_task_list(callback: CallbackQuery):
    """Показать список заданий для удаления (только Admins)."""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_only_admin_delete_task", lang), show_alert=True)
        return

    tasks = await db.get_active_tasks()

    if not tasks:
        await callback.answer(t("admin_no_active_tasks", lang), show_alert=False)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    text = t("admin_choose_task_delete", lang)
    builder = InlineKeyboardBuilder()

    for tk in tasks:
        per_user = tk.reward_amount / max(tk.required_subscribers, 1)
        text += t(
            "admin_task_line", lang,
            channel=tk.channel_username, per_user=per_user,
            current=tk.current_subscribers, required=tk.required_subscribers,
        )
        builder.row(InlineKeyboardButton(
            text=t("admin_task_delete_btn", lang, channel=tk.channel_username),
            callback_data=f"task:delete:{tk.task_id}"
        ))

    builder.row(InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:panel_back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("task:delete:"))
async def confirm_delete_task(callback: CallbackQuery):
    """Удалить задание (только Admin)."""
    lang = await _lang(callback.from_user.id)
    if not await is_admin(callback.from_user.id):
        await callback.answer(t("admin_only_admin_delete_task", lang), show_alert=True)
        return

    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)

    if not task:
        await callback.answer(t("admin_task_not_found", lang), show_alert=True)
        return

    await db.delete_task(task_id)
    await db.log_action(callback.from_user.id, "task_deleted_bot", f"Task #{task_id} @{task.channel_username}")

    await callback.answer(t("admin_task_deleted_alert", lang, channel=task.channel_username), show_alert=False)

    # Обновляем список заданий
    tasks = await db.get_active_tasks()
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton

    if not tasks:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:panel_back"))
        await callback.message.edit_text(
            t("admin_no_tasks_title", lang),
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return

    text = t("admin_choose_task_delete", lang)
    builder = InlineKeyboardBuilder()
    for tk in tasks:
        per_user = tk.reward_amount / max(tk.required_subscribers, 1)
        text += t(
            "admin_task_line", lang,
            channel=tk.channel_username, per_user=per_user,
            current=tk.current_subscribers, required=tk.required_subscribers,
        )
        builder.row(InlineKeyboardButton(
            text=t("admin_task_delete_btn", lang, channel=tk.channel_username),
            callback_data=f"task:delete:{tk.task_id}"
        ))
    builder.row(InlineKeyboardButton(text=t("btn_back_to_panel", lang), callback_data="admin:panel_back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# Функции для уведомления стафа
async def get_all_admins() -> list:
    """Получить всех админов и модераторов (для уведомлений)"""
    import aiosqlite
    from config import DB_PATH
    admins = []
    async with aiosqlite.connect(DB_PATH) as database:
        async with database.execute(
            "SELECT user_id FROM users WHERE role IN ('Admin', 'Moder')"
        ) as cursor:
            async for row in cursor:
                admins.append(row[0])
    return admins


async def notify_admins_about_withdrawal(bot, request: "WithdrawalRequest"):
    """Уведомить админов о новой заявке на вывод"""
    admins = await get_all_admins()
    user = await db.get_user(request.user_id)

    for admin_id in admins:
        try:
            admin_lang = await _lang(admin_id)
            request_text = format_withdrawal_request(request, user, admin_lang)
            await bot.send_message(
                admin_id,
                t("admin_dm_new_withdrawal_title", admin_lang, text=request_text),
                reply_markup=get_withdrawal_notification_keyboard(request.request_id, request.user_id, admin_lang),
                parse_mode="HTML"
            )
        except Exception:
            pass


async def notify_admins_about_ticket(bot, ticket: "SupportTicket"):
    """Уведомить админов о новом тикете (обработка в веб-панели)."""
    from config import WEB_ADMIN_URL

    admins = await get_all_admins()
    user = await db.get_user(ticket.user_id)

    for admin_id in admins:
        try:
            admin_lang = await _lang(admin_id)
            ticket_text = format_support_ticket(ticket, user, admin_lang)
            await bot.send_message(
                admin_id,
                t(
                    "admin_dm_new_ticket_title", admin_lang,
                    id=ticket.ticket_id, text=ticket_text,
                    url=WEB_ADMIN_URL, user_id=ticket.user_id,
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            pass


# ───────────── ЗАЯВКИ НА РЕКЛАМУ (в боте) ──────────────────────────

@router.callback_query(F.data == "admin:ad_requests")
async def show_ad_requests(callback: CallbackQuery):
    """Управление заявками на рекламу — через веб-панель"""
    lang = await _lang(callback.from_user.id)
    if not await is_moder_or_admin(callback.from_user.id):
        await callback.answer(t("admin_no_access", lang), show_alert=True)
        return

    from config import WEB_ADMIN_URL

    await callback.message.edit_text(
        t("admin_ad_requests_text", lang, url=WEB_ADMIN_URL),
        parse_mode="HTML",
    )
    await callback.answer()
