"""
Сервис проверки подписок: ранняя отписка и верификация после удержания.
"""
import asyncio
from aiogram import Bot
from database.db import Database
from utils.helpers import check_channel_subscription, should_verify_task
from utils.i18n import t, DEFAULT_LANG
from config import SUBSCRIPTION_HOLD_DAYS, SUBSCRIPTION_CHECK_INTERVAL_HOURS

db = Database()


async def verify_tasks_periodically(bot: Bot, interval_hours: int | None = None):
    """Периодически проверять подписки (ранняя отписка + верификация по сроку)."""
    hours = interval_hours if interval_hours is not None else SUBSCRIPTION_CHECK_INTERVAL_HOURS
    while True:
        try:
            await check_early_unsubscribes(bot)
            await verify_pending_tasks(bot)
            closed = await db.close_fully_verified_tasks()
            if closed:
                print(f"Задания переведены в историю (полностью укомплектованы и верифицированы): {closed}")
        except Exception as e:
            print(f"Ошибка при проверке заданий: {e}")

        await asyncio.sleep(hours * 3600)


async def check_early_unsubscribes(bot: Bot):
    """Списать награду, если пользователь отписался до истечения срока удержания."""
    unverified_tasks = await db.get_unverified_user_tasks()

    for user_task in unverified_tasks:
        if await should_verify_task(user_task):
            continue

        task = await db.get_task(user_task.task_id)
        if not task:
            continue

        user = await db.get_user(user_task.user_id)
        if not user:
            continue

        is_subscribed = await check_channel_subscription(
            bot, user_task.user_id, task.channel_username
        )
        # None (проверить не удалось) трактуем как "не отписался" — не наказываем
        # пользователя за временный сбой API, попробуем ещё раз в следующем цикле.
        if is_subscribed is not False:
            continue

        await _fail_task_verification(bot, user_task, task, user, early=True)


async def verify_pending_tasks(bot: Bot):
    """Верифицировать задания после истечения срока удержания."""
    unverified_tasks = await db.get_unverified_user_tasks()

    for user_task in unverified_tasks:
        if not await should_verify_task(user_task):
            continue

        task = await db.get_task(user_task.task_id)
        if not task:
            continue

        user = await db.get_user(user_task.user_id)
        if not user:
            continue

        is_subscribed = await check_channel_subscription(
            bot, user_task.user_id, task.channel_username
        )

        if is_subscribed is None:
            # Не удалось проверить (сбой API и т.п.) — НЕ подтверждаем выплату
            # "на всякий случай", а откладываем решение до следующего цикла
            # (иначе временная ошибка могла бы ошибочно узаконить деньги тому,
            # кто уже отписался).
            continue

        if is_subscribed:
            await _succeed_task_verification(bot, user_task, task, user)
        else:
            await _fail_task_verification(bot, user_task, task, user, early=False)


async def _succeed_task_verification(bot: Bot, user_task, task, user):
    reward_per_user = task.reward_amount / task.required_subscribers
    lang = user.language if user else DEFAULT_LANG

    await db.verify_user_task(user_task.user_id, user_task.task_id)
    await db.adjust_user_balance(
        user_task.user_id,
        unverified_delta=-reward_per_user,
        verified_delta=reward_per_user,
    )

    try:
        await bot.send_message(
            user_task.user_id,
            t("task_verified_dm", lang, amount=reward_per_user, channel=f"@{task.channel_username}"),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await db.log_action(
        user_task.user_id,
        "task_verified",
        f"Task #{user_task.task_id}, Reward: ${reward_per_user:.2f}",
    )


async def _fail_task_verification(bot: Bot, user_task, task, user, *, early: bool):
    reward_per_user = task.reward_amount / task.required_subscribers
    lang = user.language if user else DEFAULT_LANG
    channel = f"@{task.channel_username}"

    await db.fail_user_task(user_task.user_id, user_task.task_id)
    await db.adjust_user_balance(user_task.user_id, unverified_delta=-reward_per_user)

    if early:
        text = t(
            "task_failed_early_unsub_dm", lang,
            channel=channel, days=SUBSCRIPTION_HOLD_DAYS, amount=reward_per_user,
        )
        action = "task_failed_early_unsubscribe"
    else:
        text = t(
            "task_failed_verification_dm", lang,
            channel=channel, days=SUBSCRIPTION_HOLD_DAYS, amount=reward_per_user,
        )
        action = "task_failed_verification"

    try:
        await bot.send_message(user_task.user_id, text, parse_mode="HTML")
    except Exception:
        pass

    await db.log_action(user_task.user_id, action, f"Task #{user_task.task_id}")
