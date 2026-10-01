"""
Middleware для проверки блокировки пользователя
"""
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, Update
from database.db import Database
from keyboards.menus import get_blocked_keyboard
from utils.i18n import all_variants, t

# Проверка идёт по всем языковым вариантам кнопки, а не по одной русской
# строке — иначе заблокированный пользователь с английским/украинским языком
# терял бы кнопку апелляции, она просто не совпадала бы с хардкодом.
_SUPPORT_BUTTON_TEXTS = all_variants("btn_support_request")

db = Database()


class BlockCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None

        if user_id is None:
            return await handler(event, data)

        user = await db.get_user(user_id)
        if not (user and user.is_blocked):
            return await handler(event, data)

        # User is blocked — allow the support appeal button through, and also
        # any reply (text/photo/document) while they have an active appeal
        # ticket open — иначе бот молча съедал доказательства, которые
        # пользователь присылал в ответ на вопрос админа (см. handle_support_reply).
        lang = getattr(user, "language", None) or "ru"
        if isinstance(event, Message):
            if event.text in _SUPPORT_BUTTON_TEXTS:
                return await handler(event, data)
            if await db.get_user_active_support_ticket(user_id):
                return await handler(event, data)
            if event.chat.type != "private":
                return
            await event.answer(t("account_blocked", lang), reply_markup=get_blocked_keyboard(lang))
            return

        if isinstance(event, CallbackQuery):
            await event.answer(t("account_blocked", lang), show_alert=True)
            return
