"""
Вспомогательные функции
"""
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Bot
from database.db import Database
from database.models import UserRole


async def check_channel_subscription(bot: Bot, user_id: int, channel_ref: str) -> Optional[bool]:
    """
    Проверить подписку пользователя.

    channel_ref может быть:
      - @username         → публичный канал/группа
      - username          → публичный канал/группа (без @)
      - https://t.me/username        → публичный
      - https://t.me/username?start=… → бот — проверить нельзя, пропускаем (True)
      - https://t.me/+XXXXX или просто +XXXXX → приватный по invite-ссылке — проверить
        нельзя (True), так как Bot API не резолвит инвайт-хэш в chat_id.

    Возвращает:
      True  — подписан
      False — точно НЕ подписан (проверка прошла и вернула однозначный ответ)
      None  — проверить не удалось (ошибка API/сети, канал недоступен и т.п.) —
              вызывающий код должен трактовать это как "неизвестно", а не как
              "подписан", иначе временный сбой может ошибочно подтвердить
              выплату тому, кто уже отписался.
    """
    import re
    try:
        ref = (channel_ref or "").strip()
        if not ref:
            return True

        # Числовой chat_id (приватный канал)
        if re.match(r'^-?\d+$', ref):
            member = await bot.get_chat_member(int(ref), user_id)
            return member.status in ('member', 'administrator', 'creator', 'restricted')

        # Приватная инвайт-ссылка в любом виде (полный URL или голый "+хэш") —
        # без numeric chat_id её в принципе нельзя проверить.
        bare = ref
        if re.match(r'https?://t\.me/', ref, re.IGNORECASE):
            bare = re.sub(r'https?://t\.me/', '', ref, flags=re.IGNORECASE).strip('/')
        elif re.match(r't\.me/', ref, re.IGNORECASE):
            bare = re.sub(r't\.me/', '', ref, flags=re.IGNORECASE).strip('/')
        if bare.startswith('+'):
            return True

        # ── Разбираем формат ──────────────────────────────────────────
        username: Optional[str] = None

        if ref.startswith('@'):
            username = ref.lstrip('@')

        elif re.match(r'https?://t\.me/', ref, re.IGNORECASE):
            path = bare.split('?')[0].strip('/')
            if path:
                username = path
            else:
                return True

        elif re.match(r't\.me/', ref, re.IGNORECASE):
            username = bare.split('?')[0].strip('/')

        else:
            # Обычный username без @
            username = ref.split('?')[0].strip('/')

        if not username:
            return True

        # Telegram bot usernames заканчиваются на "bot" — проверить подписку нельзя
        if username.lower().endswith('bot'):
            return True

        # ── Проверяем членство ────────────────────────────────────────
        member = await bot.get_chat_member(f"@{username}", user_id)
        return member.status in ('member', 'administrator', 'creator', 'restricted')

    except Exception:
        # Не удалось проверить (rate limit, канал недоступен, бота исключили и т.п.) —
        # НЕ считаем это "подписан": для решения о выплате это должно означать
        # "попробовать снова позже", а не автоматическое подтверждение.
        return None


async def format_user_profile(user, db: Database) -> str:
    """Форматировать профиль пользователя для отображения"""
    from utils.i18n import t, DEFAULT_LANG
    lang = getattr(user, "language", None) or DEFAULT_LANG

    username = f"@{user.username}" if user.username else t("profile_not_specified", lang)

    return t(
        "profile_text",
        lang,
        user_id=user.user_id,
        first_name=user.first_name or t("profile_not_specified_name", lang),
        username=username,
        phone=user.phone or t("profile_not_specified", lang),
        role=user.role.value,
        referrals_count=user.referrals_count,
        unverified_balance=user.unverified_balance,
        verified_balance=user.verified_balance,
    )


def format_task_info(task) -> str:
    """Форматировать информацию о задании"""
    per_user = task.reward_amount / max(task.required_subscribers, 1)
    return f"""
📢 <b>Задание</b>

Канал: @{task.channel_username}
💰 Награда за подписку: ${per_user:.2f}
👥 Нужно подписчиков: {task.required_subscribers}
✅ Текущих: {task.current_subscribers}

⚠️ <b>Важно:</b> Вы должны оставаться подписанным на канал в течение 7 дней для получения награды.
"""


def format_withdrawal_request(request, user, lang: str = None) -> str:
    """Форматировать заявку на вывод"""
    from utils.i18n import t, DEFAULT_LANG
    lang = lang or DEFAULT_LANG
    username = f"@{user.username}" if user.username else t("fmt_username_not_specified", lang)
    return t(
        "fmt_withdrawal_request", lang,
        request_id=request.request_id,
        first_name=user.first_name,
        username=username,
        user_id=request.user_id,
        amount=request.amount,
        verified_balance=user.verified_balance,
    )


def format_support_ticket(ticket, user, lang: str = None) -> str:
    """Форматировать тикет поддержки"""
    from utils.i18n import t, DEFAULT_LANG
    lang = lang or DEFAULT_LANG
    username = f"@{user.username}" if user.username else t("fmt_username_not_specified", lang)
    return t(
        "fmt_support_ticket", lang,
        ticket_id=ticket.ticket_id,
        first_name=user.first_name,
        username=username,
        user_id=ticket.user_id,
        reason=ticket.reason,
        description=ticket.description,
    )


async def is_owner(username: Optional[str]) -> bool:
    """Проверить, является ли пользователь владельцем"""
    from config import BOT_OWNERS
    if not username:
        return False
    return f"@{username}" in BOT_OWNERS or username in BOT_OWNERS


async def should_verify_task(user_task) -> bool:
    """Проверить, нужно ли верифицировать задание (прошло N дней удержания)."""
    from config import SUBSCRIPTION_HOLD_DAYS

    if user_task.is_verified or getattr(user_task, "verification_failed", False):
        return False

    if not user_task.completed_at:
        return False

    completed = user_task.completed_at
    if isinstance(completed, str):
        completed = datetime.fromisoformat(completed)

    days_passed = (datetime.now() - completed).days
    return days_passed >= SUBSCRIPTION_HOLD_DAYS


def format_referral_link(bot_username: str, referral_code: str) -> str:
    """Форматировать реферальную ссылку"""
    return f"https://t.me/{bot_username}?start={referral_code}"

