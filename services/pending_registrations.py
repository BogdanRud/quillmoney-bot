"""
Отложенная обработка рефералов: 1) довершение "зависших" регистраций,
2) начисление бонуса рефереру только после активности приглашённого.

Сценарий 1: пользователь переходит по реферальной ссылке, видит требование
подписаться на обязательные каналы, подписывается — но не возвращается в бота
нажать «✅ Я подписался». Без этого шага регистрация не завершается и реферал
никогда не засчитывается, хотя условие уже выполнено. Фоновая задача
retry_pending_registrations_periodically периодически проверяет таких
пользователей и, если ВСЕ обязательные подписки подтверждены, довершает
регистрацию сама — так же, как если бы он нажал кнопку.

Сценарий 2: реферальный бонус рефереру начисляется не сразу при регистрации
приглашённого, а только когда тот проявит хоть какую-то активность в боте
(не обязательно выполнение задания — достаточно любого осознанного действия).
Это защита от накрутки: фейковые/ботовские аккаунты регистрируются и больше
никогда не заходят, поэтому бонус за них просто никогда не начислится.
Фоновая задача credit_active_referrals_periodically следит за этим.
"""
import asyncio
from aiogram import Bot
from database.db import Database
from keyboards.menus import get_role_menu
from utils.helpers import check_channel_subscription
from utils.i18n import t, DEFAULT_LANG
from config import REFERRAL_BONUS, PENDING_REFERRAL_RETRY_MINUTES, REFERRAL_ACTIVITY_CHECK_MINUTES

db = Database()


async def retry_pending_registrations_periodically(bot: Bot, interval_minutes: int | None = None):
    minutes = interval_minutes if interval_minutes is not None else PENDING_REFERRAL_RETRY_MINUTES
    while True:
        try:
            await retry_pending_registrations(bot)
        except Exception as e:
            print(f"Ошибка при довершении отложенных регистраций: {e}")

        await asyncio.sleep(minutes * 60)


async def retry_pending_registrations(bot: Bot):
    required_subs = await db.get_active_required_sub_channels()
    pending = await db.get_all_pending_referrals()

    for entry in pending:
        user_id = entry["user_id"]
        referral_code = entry["referral_code"]

        # Уже зарегистрирован каким-то другим путём — просто убираем хвост.
        if await db.get_user(user_id):
            await db.clear_pending_referral(user_id)
            continue

        if not await _all_subs_satisfied(bot, user_id, required_subs):
            continue  # ещё не выполнил все условия — проверим в следующий раз

        try:
            chat = await bot.get_chat(user_id)
        except Exception:
            continue  # не удалось получить данные (заблокировал бота и т.п.) — попробуем позже

        await _finish_registration(bot, user_id, chat.username, chat.first_name, referral_code)


async def _all_subs_satisfied(bot: Bot, user_id: int, required_subs: list) -> bool:
    for sub in required_subs:
        # is True — намеренно: None (проверить не удалось) НЕ считаем выполненным
        # условием, иначе временный сбой API мог бы ошибочно засчитать реферала.
        if await check_channel_subscription(bot, user_id, sub["channel_ref"]) is not True:
            return False
    return True


async def _finish_registration(bot: Bot, user_id: int, username: str, first_name: str, referral_code: str):
    user = await db.create_user(user_id=user_id, username=username, first_name=first_name)
    await db.log_action(user_id, "user_created", "Delayed via pending-referral retry")

    if referral_code and referral_code != user.referral_code:
        referrer = await db.get_user_by_referral_code(referral_code)
        if referrer and await db.assign_referrer(user_id, referrer.user_id):
            # Бонус НЕ начисляется здесь — см. credit_active_referrals_periodically
            # ниже: платим только после того, как этот пользователь проявит
            # хоть какую-то активность в боте.
            await db.log_action(
                user_id, "referral_registered",
                f"Referred by {referrer.user_id} (delayed)",
            )

    await db.clear_pending_referral(user_id)

    lang = user.language if user else DEFAULT_LANG
    label = f"@{username}" if username else (first_name or "friend")
    try:
        await bot.send_message(
            user_id,
            t("welcome_full", lang, username=label),
            parse_mode="HTML",
            reply_markup=get_role_menu(user),
        )
    except Exception:
        pass


async def credit_active_referrals_periodically(bot: Bot, interval_minutes: int | None = None):
    minutes = interval_minutes if interval_minutes is not None else REFERRAL_ACTIVITY_CHECK_MINUTES
    while True:
        try:
            await credit_active_referrals(bot)
        except Exception as e:
            print(f"Ошибка при начислении рефералов за активность: {e}")

        await asyncio.sleep(minutes * 60)


async def credit_active_referrals(bot: Bot):
    """Проверить всех "ожидающих начисления" рефералов и заплатить рефереру
    за тех, кто уже проявил активность в боте."""
    pending = await db.get_users_pending_referral_credit()

    for entry in pending:
        result = await db.check_and_credit_referral(entry["user_id"])
        if not result:
            continue
        referrer_id, amount = result
        if amount <= 0:
            continue
        try:
            referrer = await db.get_user(referrer_id)
            lang = referrer.language if referrer else DEFAULT_LANG
            await bot.send_message(
                referrer_id,
                t("referral_bonus_notification", lang, amount=amount),
                parse_mode="HTML",
            )
        except Exception:
            pass
