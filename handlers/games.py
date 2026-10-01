"""
Игры на деньги — раздел "🎰 Игры на деньги". Пока одна игра: "Колесо удачи"
(случайный множитель ставки), ставки принимаются только с
верифицированного баланса. В будущем сюда добавятся другие игры.
"""
import asyncio

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import WHEEL_MIN_BET, WHEEL_SEGMENTS
from handlers.auth import db
from utils.i18n import t, all_variants, DEFAULT_LANG

# Анимация "бегущего огонька" по всем 8 секторам колеса — с замедлением
# к концу (как у настоящего рулеточного колеса), останавливается ровно на
# реальном (уже определённом и записанном в БД) секторе.
_WHEEL_SPIN_FRAMES = 14
_WHEEL_SPIN_DELAYS = [0.07, 0.07, 0.08, 0.09, 0.10, 0.12, 0.14, 0.17, 0.20, 0.25, 0.30, 0.38, 0.5]

router = Router()

_MONEY_GAMES_TEXTS = all_variants("btn_money_games")


class GamesStates(StatesGroup):
    waiting_for_bet = State()


def _games_list_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_wheel_game", lang), callback_data="games:wheel")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_menu")],
    ])


def _wheel_result_keyboard(bet_amount: float, lang: str) -> InlineKeyboardMarkup:
    bet_cents = round(bet_amount * 100)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=t("btn_spin_again", lang, bet=bet_amount), callback_data=f"wheel:again:{bet_cents}"
        )],
        [InlineKeyboardButton(text=t("btn_change_bet", lang), callback_data="games:wheel")],
        [InlineKeyboardButton(text=t("btn_back_to_games", lang), callback_data="games:list")],
    ])


async def _send_games_list(user_id: int, lang: str, send):
    text = t("games_list_title", lang) + t("gambling_warning_full", lang)
    await send(text, reply_markup=_games_list_keyboard(lang), parse_mode="HTML")


@router.message(F.text.in_(_MONEY_GAMES_TEXTS))
async def show_games(message: Message):
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await _send_games_list(message.from_user.id, lang, message.answer)


@router.callback_query(F.data == "games:list")
async def games_list_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    text = t("games_list_title", lang) + t("gambling_warning_full", lang)
    try:
        await callback.message.edit_text(text, reply_markup=_games_list_keyboard(lang), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=_games_list_keyboard(lang), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "games:wheel")
async def wheel_intro_callback(callback: CallbackQuery, state: FSMContext):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    balance = user.verified_balance if user else 0.0

    segments_display = "  ".join(f"x{seg}" for seg in WHEEL_SEGMENTS)
    text = (
        t("wheel_game_intro", lang, min_bet=WHEEL_MIN_BET, balance=balance, segments_display=segments_display)
        + t("gambling_warning_full", lang)
    )
    try:
        await callback.message.edit_text(text, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, parse_mode="HTML")
    await state.set_state(GamesStates.waiting_for_bet)
    await callback.answer()


def _render_wheel_frame(lang: str, active_index: int) -> str:
    lines = []
    for i, seg in enumerate(WHEEL_SEGMENTS):
        marker = "👉" if i == active_index else "▫️"
        lines.append(f"{marker} x{seg}")
    return t("wheel_spin_header", lang) + "\n\n" + "\n".join(lines)


async def _animate_wheel(spin_msg, lang: str, target_index: int):
    n = _WHEEL_SPIN_FRAMES
    start = (target_index - (n - 1)) % len(WHEEL_SEGMENTS)
    positions = [(start + i) % len(WHEEL_SEGMENTS) for i in range(n)]

    for i, pos in enumerate(positions):
        if i > 0:
            await asyncio.sleep(_WHEEL_SPIN_DELAYS[i - 1])
        try:
            await spin_msg.edit_text(_render_wheel_frame(lang, pos), parse_mode="HTML")
        except Exception:
            pass


async def _do_spin(user_id: int, bet_amount: float, lang: str, answer):
    result = await db.play_wheel_spin(user_id, bet_amount)
    if not result["ok"]:
        await answer(t("wheel_insufficient_balance", lang))
        return

    multiplier = result["multiplier"]
    segment_index = result["segment_index"]
    payout = result["payout"]
    balance = result["new_balance"]
    diff = abs(round(payout - bet_amount, 2))

    # Анимация — реальный исход уже определён и записан в БД (это чисто
    # визуальный эффект), последний кадр всегда совпадает с настоящим
    # результатом.
    spin_msg = await answer(_render_wheel_frame(lang, 0), parse_mode="HTML")
    await _animate_wheel(spin_msg, lang, segment_index)

    if payout > bet_amount:
        text = t("wheel_result_win", lang, multiplier=multiplier, bet=bet_amount, payout=payout, diff=diff, balance=balance)
    elif payout < bet_amount:
        text = t("wheel_result_lose", lang, multiplier=multiplier, bet=bet_amount, payout=payout, diff=diff, balance=balance)
    else:
        text = t("wheel_result_even", lang, multiplier=multiplier, payout=payout, balance=balance)
    text += t("gambling_warning_short", lang)

    try:
        await spin_msg.edit_text(text, reply_markup=_wheel_result_keyboard(bet_amount, lang), parse_mode="HTML")
    except Exception:
        await answer(text, reply_markup=_wheel_result_keyboard(bet_amount, lang), parse_mode="HTML")

    await db.log_action(user_id, "wheel_spin", f"Bet: ${bet_amount:.2f}, x{multiplier}, Payout: ${payout:.2f}")


@router.message(GamesStates.waiting_for_bet)
async def process_bet_amount(message: Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    try:
        bet_amount = float(message.text.replace(",", "."))
    except (ValueError, AttributeError):
        await message.answer(t("wheel_invalid_bet", lang, min_bet=WHEEL_MIN_BET))
        return

    if bet_amount < WHEEL_MIN_BET:
        await message.answer(t("wheel_invalid_bet", lang, min_bet=WHEEL_MIN_BET))
        return

    if not user or user.verified_balance < bet_amount - 0.001:
        await message.answer(t("wheel_insufficient_balance", lang))
        return

    await state.clear()
    await _do_spin(message.from_user.id, bet_amount, lang, message.answer)


@router.callback_query(F.data.startswith("wheel:again:"))
async def wheel_spin_again_callback(callback: CallbackQuery, state: FSMContext):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    bet_amount = int(callback.data.split(":")[2]) / 100

    if not user or user.verified_balance < bet_amount - 0.001:
        await callback.answer(t("wheel_insufficient_balance", lang), show_alert=True)
        return

    await callback.answer()
    await _do_spin(callback.from_user.id, bet_amount, lang, callback.message.answer)
