"""
Фоновая задача: закрывать конкурсы по истечении срока и уведомлять
создателя итоговым топом (выплата призов — вручную, через баланс в панели).
"""
import asyncio
from aiogram import Bot
from database.db import Database
from utils.i18n import t, DEFAULT_LANG

db = Database()


async def close_expired_contests_periodically(bot: Bot, interval_minutes: int = 5):
    while True:
        try:
            closed_ids = await db.close_expired_contests()
            for contest_id in closed_ids:
                await _notify_contest_ended(bot, contest_id)
        except Exception as e:
            print(f"Ошибка при закрытии конкурсов: {e}")
        await asyncio.sleep(interval_minutes * 60)


async def _notify_contest_ended(bot: Bot, contest_id: int):
    from handlers.contests import build_leaderboard_text

    contest = await db.get_contest(contest_id)
    if not contest or not contest.created_by:
        return

    creator = await db.get_user(contest.created_by)
    lang = creator.language if creator else DEFAULT_LANG
    results_text = await build_leaderboard_text(contest, lang)
    try:
        await bot.send_message(
            contest.created_by,
            t("contest_ended_notice_admin", lang, title=contest.title, results=results_text),
            parse_mode="HTML",
        )
    except Exception:
        pass
