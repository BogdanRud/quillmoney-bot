"""
Правила бота — обязательное ознакомление и согласие перед использованием.
Пока пользователь не нажал кнопку согласия, middlewares/rules_gate.py
блокирует любые другие действия в личном чате.
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import SUBSCRIPTION_HOLD_DAYS
from handlers.auth import db
from keyboards.menus import get_role_menu
from utils.i18n import t, all_variants, DEFAULT_LANG

router = Router()

RULES_ACCEPT_CALLBACK = "rules:accept"
_VIEW_RULES_TEXTS = all_variants("btn_view_rules")


def rules_keyboard(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_rules_agree", lang), callback_data=RULES_ACCEPT_CALLBACK)],
    ])


def rules_text(lang: str = DEFAULT_LANG) -> str:
    return t("rules_text", lang, days=SUBSCRIPTION_HOLD_DAYS)


@router.message(F.text.in_(_VIEW_RULES_TEXTS))
async def show_rules(message: Message):
    """Показать правила по кнопке меню — доступно, только когда пользователь
    уже прошёл гейт согласия (см. middlewares/rules_gate.py), поэтому здесь
    просто справочный показ, без кнопки согласия."""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await message.answer(rules_text(lang), parse_mode="HTML")


@router.callback_query(F.data == RULES_ACCEPT_CALLBACK)
async def accept_rules_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    await db.mark_rules_accepted(user_id)

    user = await db.get_user(user_id)
    lang = user.language if user else DEFAULT_LANG

    try:
        await callback.message.delete()
    except Exception:
        pass

    menu = get_role_menu(user) if user else None
    await callback.message.answer(t("rules_accepted_confirm", lang), reply_markup=menu)
    await callback.answer()
