"""
Периодическая отправка сообщений в группах (автопост).
"""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from database.db import Database

logger = logging.getLogger(__name__)
db = Database()

POLL_INTERVAL_SECONDS = 30


async def run_group_autoposts_periodically(bot: Bot):
    """Проверять и отправлять запланированные автопосты в группах."""
    while True:
        try:
            await _process_due_autoposts(bot)
        except Exception as e:
            logger.exception("Ошибка при обработке автопостов: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _process_due_autoposts(bot: Bot):
    posts = await db.get_due_group_autoposts()
    now = datetime.now()

    for post in posts:
        try:
            await bot.copy_message(
                chat_id=post["chat_id"],
                from_chat_id=post["from_chat_id"],
                message_id=post["message_id"],
            )
            next_run = now + timedelta(seconds=post["interval_seconds"])
            await db.update_group_autopost_schedule(
                post["id"],
                last_run_at=now.isoformat(),
                next_run_at=next_run.isoformat(),
            )
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning(
                "Автопост #%s остановлен (чат %s): %s",
                post["id"],
                post["chat_id"],
                e,
            )
            await db.deactivate_group_autopost_by_id(post["id"])
        except Exception as e:
            logger.exception(
                "Ошибка отправки автопоста #%s в чат %s: %s",
                post["id"],
                post["chat_id"],
                e,
            )
