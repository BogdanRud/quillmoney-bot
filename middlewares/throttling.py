"""
Anti-flood middleware: ограничивает частоту сообщений/callback от одного
пользователя, чтобы защитить бота от спам-атак и перегрузки БД.
"""
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

MIN_INTERVAL_SECONDS = 0.5  # минимальный интервал между событиями одного пользователя


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, min_interval: float = MIN_INTERVAL_SECONDS):
        super().__init__()
        self.min_interval = min_interval
        self._last_seen: Dict[int, float] = defaultdict(float)

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            user_id = event.from_user.id

        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last_seen[user_id]
        self._last_seen[user_id] = now

        if now - last < self.min_interval:
            # Тихо игнорируем слишком частые запросы — не отвечаем,
            # чтобы не давать обратную связь, удобную для автоматизированного флуда.
            if isinstance(event, CallbackQuery):
                await event.answer()
            return

        return await handler(event, data)
