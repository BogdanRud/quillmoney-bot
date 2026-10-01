"""
Middleware — жёсткая блокировка личного чата, пока пользователь не прошёл
обязательный онбординг: сначала явный выбор языка, затем согласие с
правилами бота (см. handlers/auth.py::onboard_set_language_callback и
handlers/rules.py). Применяется ко ВСЕМ пользователям, включая уже
существующих (языка/правил они раньше явно не подтверждали).
Пропускает сами кнопки выбора языка/согласия — иначе блокировала бы
собственный выход из себя.
"""
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from database.db import Database
from utils.i18n import t, DEFAULT_LANG

db = Database()


class RulesGateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        message = None
        if isinstance(event, Message):
            message = event
        elif isinstance(event, CallbackQuery):
            message = event.message

        if message is None or message.chat.type != "private":
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data in ("rules:accept",):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith("onboard_setlang:"):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return await handler(event, data)

        user = await db.get_user(user_id)
        if user is None:
            return await handler(event, data)

        if not user.language_confirmed:
            from handlers.auth import language_keyboard
            await message.answer(
                t("choose_language", DEFAULT_LANG), reply_markup=language_keyboard("onboard_setlang")
            )
            if isinstance(event, CallbackQuery):
                await event.answer()
            return

        if not user.rules_accepted_at:
            from handlers.rules import rules_text, rules_keyboard
            lang = user.language or DEFAULT_LANG
            await message.answer(
                rules_text(lang), reply_markup=rules_keyboard(lang), parse_mode="HTML"
            )
            if isinstance(event, CallbackQuery):
                await event.answer()
            return

        return await handler(event, data)
