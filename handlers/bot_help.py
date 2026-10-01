"""
Раздел "Что умеет бот?" — краткая справка отдельно для пользователей и
отдельно для владельцев групп/каналов.
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from handlers.auth import db
from utils.i18n import all_variants, t, DEFAULT_LANG

router = Router()

_BOT_HELP_TEXTS = all_variants("btn_bot_help")


def _sections_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_bot_help_users", lang), callback_data="bothelp:users")],
        [InlineKeyboardButton(text=t("btn_bot_help_owners", lang), callback_data="bothelp:owners")],
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_menu")],
    ])


def _section_back_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data="bothelp:sections")],
    ])


@router.message(F.text.in_(_BOT_HELP_TEXTS))
async def show_bot_help(message: Message):
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await message.answer(
        t("bot_help_choose_section", lang),
        reply_markup=_sections_kb(lang),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "bothelp:sections")
async def back_to_sections(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await callback.message.edit_text(
        t("bot_help_choose_section", lang),
        reply_markup=_sections_kb(lang),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "bothelp:users")
async def show_users_section(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await callback.message.edit_text(
        t("bot_help_users_text", lang),
        reply_markup=_section_back_kb(lang),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "bothelp:owners")
async def show_owners_section(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await callback.message.edit_text(
        t("bot_help_owners_text", lang),
        reply_markup=_section_back_kb(lang),
        parse_mode="HTML",
    )
    await callback.answer()
