"""
Рассылка сообщений с ограничением скорости (защита от FloodWait).
"""
import asyncio
from typing import Optional

from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramRetryAfter

from config import BROADCAST_DELAY_SECONDS


async def send_broadcast_message(
    bot: Bot,
    user_id: int,
    source: Message,
) -> bool:
    """Отправить копию сообщения одному пользователю."""
    try:
        if source.text:
            await bot.send_message(user_id, source.text, parse_mode="HTML")
        elif source.photo:
            await bot.send_photo(
                user_id,
                source.photo[-1].file_id,
                caption=source.caption,
                parse_mode="HTML" if source.caption else None,
            )
        elif source.video:
            await bot.send_video(
                user_id,
                source.video.file_id,
                caption=source.caption,
                parse_mode="HTML" if source.caption else None,
            )
        elif source.document:
            await bot.send_document(
                user_id,
                source.document.file_id,
                caption=source.caption,
                parse_mode="HTML" if source.caption else None,
            )
        else:
            return False
        return True
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await send_broadcast_message(bot, user_id, source)
    except Exception:
        return False


async def run_broadcast(
    bot: Bot,
    users: list,
    source: Message,
    *,
    delay: Optional[float] = None,
) -> tuple[int, int]:
    """Рассылка списку пользователей. Возвращает (успех, ошибки)."""
    pause = delay if delay is not None else BROADCAST_DELAY_SECONDS
    success = 0
    failed = 0

    for user in users:
        uid = user.user_id if hasattr(user, "user_id") else user
        if await send_broadcast_message(bot, uid, source):
            success += 1
        else:
            failed += 1
        if pause > 0:
            await asyncio.sleep(pause)

    return success, failed
