"""
Обработчики вывода средств
"""
from typing import Callable, Awaitable, Any

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import MIN_WITHDRAWAL
from keyboards.menus import get_role_menu
from database.models import UserRole
from utils.i18n import all_variants, t, DEFAULT_LANG
from handlers.auth import db

_WITHDRAW_TEXTS = all_variants("btn_withdraw")

router = Router()


class WithdrawalStates(StatesGroup):
    """Состояния для заявки на вывод"""
    waiting_for_amount = State()


async def _reply_menu(user_id: int):
    user = await db.get_user(user_id)
    return get_role_menu(user)


async def begin_withdrawal(
    user_id: int,
    state: FSMContext,
    send_text: Callable[..., Awaitable[Any]],
):
    """Начать вывод (из сообщения или callback)."""
    user = await db.get_user(user_id)

    if not user:
        await send_text(t("not_registered", DEFAULT_LANG))
        return
    lang = user.language

    if user.verified_balance < MIN_WITHDRAWAL:
        await send_text(
            t("wd_insufficient_funds", lang, min_amount=MIN_WITHDRAWAL, balance=user.verified_balance),
            parse_mode="HTML",
            reply_markup=await _reply_menu(user_id),
        )
        return

    await send_text(
        t("wd_prompt", lang, balance=user.verified_balance, min_amount=MIN_WITHDRAWAL),
        parse_mode="HTML",
    )
    await state.set_state(WithdrawalStates.waiting_for_amount)
    await db.log_action(user_id, "withdrawal_started")


@router.message(F.text.in_(_WITHDRAW_TEXTS))
async def start_withdrawal(message: Message, state: FSMContext):
    await begin_withdrawal(
        message.from_user.id,
        state,
        message.answer,
    )


# Отмена вывода при нажатии кнопки меню обрабатывается в handlers/user_menu.py
# (cancel_fsm_on_menu_button) — этот роутер подключается раньше, и большинство
# кнопок меню там зарегистрированы без фильтра по состоянию, так что отдельный
# обработчик здесь всё равно никогда бы не сработал (см. комментарий в
# user_menu.py).


@router.message(WithdrawalStates.waiting_for_amount)
async def process_withdrawal_amount(message: Message, state: FSMContext):
    """Обработать сумму вывода"""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    try:
        amount = float(message.text.replace(",", "."))
    except (ValueError, AttributeError):
        await message.answer(t("wd_invalid_format", lang))
        return

    if amount < MIN_WITHDRAWAL:
        await message.answer(t("wd_min_amount", lang, min_amount=MIN_WITHDRAWAL))
        return

    pending_sum = await db.get_pending_withdrawal_sum(message.from_user.id)
    available = user.verified_balance - pending_sum
    if amount > available:
        await message.answer(
            t("wd_insufficient_available", lang, balance=user.verified_balance,
              pending=pending_sum, available=available)
        )
        return

    request = await db.create_withdrawal_request(
        user_id=message.from_user.id,
        amount=amount,
    )
    if not request:
        await message.answer(t("wd_failed_create", lang))
        return

    await message.answer(
        t("wd_created", lang, request_id=request.request_id, amount=amount),
        reply_markup=await _reply_menu(message.from_user.id),
    )

    from handlers.admin import notify_admins_about_withdrawal
    await notify_admins_about_withdrawal(message.bot, request)

    await db.log_action(
        message.from_user.id,
        "withdrawal_request_created",
        f"Request #{request.request_id}, Amount: ${amount:.2f}",
    )
    await state.clear()
