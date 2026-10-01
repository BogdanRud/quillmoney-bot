"""
Синхронизация username/first_name с актуальными данными Telegram.
"""
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from database.db import Database

db = Database()


class ProfileSyncMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            user = event.from_user
            try:
                await db.sync_user_profile(user.id, user.username, user.first_name)
            except Exception:
                pass

        return await handler(event, data)
