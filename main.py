"""
Главный файл бота
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN
from database.db import Database
from handlers import auth, user_menu, tasks, withdrawal, admin, support_chat, web_login, channel_mgmt, group_autopost, mandatory_sub, bot_help, rules, contests, games
from middlewares.block_check import BlockCheckMiddleware
from middlewares.rules_gate import RulesGateMiddleware
from middlewares.throttling import ThrottlingMiddleware
from middlewares.profile_sync import ProfileSyncMiddleware
from services.task_verification import verify_tasks_periodically
from services.group_autopost import run_group_autoposts_periodically
from services.pending_registrations import retry_pending_registrations_periodically, credit_active_referrals_periodically
from filters.chat import private_only
from utils.logging_setup import setup_logging

# Логи пишутся в файлы с ротацией (не зависит от screen/nohup/терминала —
# даже если сессия закроется, история останется на диске).
setup_logging("bot_log.txt", "bot_err.txt")
logger = logging.getLogger(__name__)

# Глобальная переменная для бота (для использования в веб-панели)
bot_instance = None


async def main():
    """Главная функция"""
    global bot_instance
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен! Проверьте файл .env")
        return
    
    # Инициализация бота и диспетчера
    bot_instance = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    # Инициализация базы данных
    db = Database()
    await db.init_db()
    logger.info("База данных инициализирована")
    
    # Anti-flood (outer — самый первый барьер, до всего остального)
    dp.message.outer_middleware(ThrottlingMiddleware())
    dp.callback_query.outer_middleware(ThrottlingMiddleware())

    # Middleware для блокировки (outer — перехватывает до роутеров)
    dp.message.outer_middleware(BlockCheckMiddleware())
    dp.callback_query.outer_middleware(BlockCheckMiddleware())

    # Жёсткая блокировка до согласия с правилами бота (после проверки бана,
    # до всего остального) — см. middlewares/rules_gate.py
    dp.message.outer_middleware(RulesGateMiddleware())
    dp.callback_query.outer_middleware(RulesGateMiddleware())

    # Синхронизация username/first_name — чтобы поиск в админ-панели всегда
    # находил пользователя, даже если он сменил ник после регистрации.
    dp.message.outer_middleware(ProfileSyncMiddleware())
    dp.callback_query.outer_middleware(ProfileSyncMiddleware())

    # Регистрация роутеров (личное меню — только в ЛС)
    private_only(auth.router)
    private_only(user_menu.router)
    private_only(tasks.router)
    private_only(withdrawal.router)
    private_only(admin.router)
    private_only(support_chat.router)
    private_only(web_login.router)
    private_only(bot_help.router)
    private_only(rules.router)
    private_only(contests.router)
    private_only(games.router)

    dp.include_router(auth.router)
    dp.include_router(user_menu.router)
    dp.include_router(tasks.router)
    dp.include_router(withdrawal.router)
    dp.include_router(admin.router)
    dp.include_router(support_chat.router)
    dp.include_router(web_login.router)
    dp.include_router(bot_help.router)
    dp.include_router(rules.router)
    dp.include_router(contests.router)
    dp.include_router(games.router)
    dp.include_router(channel_mgmt.router)
    dp.include_router(group_autopost.router)
    dp.include_router(mandatory_sub.router)
    
    # Запуск фоновой задачи проверки подписок
    from config import SUBSCRIPTION_CHECK_INTERVAL_HOURS
    asyncio.create_task(verify_tasks_periodically(bot_instance))
    logger.info(
        "Фоновая проверка подписок запущена (интервал %s ч)",
        SUBSCRIPTION_CHECK_INTERVAL_HOURS,
    )

    asyncio.create_task(run_group_autoposts_periodically(bot_instance))
    logger.info("Фоновая задача автопостов в группах запущена")

    from config import PENDING_REFERRAL_RETRY_MINUTES, REFERRAL_ACTIVITY_CHECK_MINUTES
    asyncio.create_task(retry_pending_registrations_periodically(bot_instance))
    logger.info(
        "Фоновая задача довершения отложенных регистраций запущена (интервал %s мин)",
        PENDING_REFERRAL_RETRY_MINUTES,
    )

    asyncio.create_task(credit_active_referrals_periodically(bot_instance))
    logger.info(
        "Фоновая задача начисления рефералов за активность запущена (интервал %s мин)",
        REFERRAL_ACTIVITY_CHECK_MINUTES,
    )

    from services.contests import close_expired_contests_periodically
    asyncio.create_task(close_expired_contests_periodically(bot_instance))
    logger.info("Фоновая задача закрытия истёкших конкурсов запущена")

    # Запуск бота
    logger.info("Бот запущен")
    await dp.start_polling(bot_instance, allowed_updates=dp.resolve_used_update_types())


def get_bot_instance():
    """Получить экземпляр бота для использования в веб-панели"""
    return bot_instance


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)

