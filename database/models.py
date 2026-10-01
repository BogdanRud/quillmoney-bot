"""
Модели базы данных
"""
from enum import Enum
from datetime import datetime
from typing import Optional


class UserRole(str, Enum):
    """Роли пользователей"""
    USER = "User"
    ADMIN = "Admin"
    MODER = "Moder"  # На будущее
    MANAGER = "Manager"  # Доступ только к CRM в веб-панели (см. web_admin/middleware.py)


class User:
    """Модель пользователя"""
    def __init__(
        self,
        user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        phone: Optional[str],
        role: UserRole = UserRole.USER,
        unverified_balance: float = 0.0,
        verified_balance: float = 0.0,
        referrals_count: int = 0,
        referral_code: Optional[str] = None,
        created_at: Optional[datetime] = None,
        referred_by: Optional[int] = None,
        is_blocked: bool = False,
        language: str = "ru",
        rules_accepted_at: Optional[datetime] = None,
        language_confirmed: bool = False,
    ):
        self.user_id = user_id
        self.username = username
        self.first_name = first_name
        self.phone = phone
        self.role = role
        self.unverified_balance = unverified_balance
        self.verified_balance = verified_balance
        self.referrals_count = referrals_count
        self.referral_code = referral_code
        self.created_at = created_at or datetime.now()
        self.referred_by = referred_by
        self.is_blocked = is_blocked
        self.language = language  # 'ru' | 'en' | 'uk'
        self.rules_accepted_at = rules_accepted_at
        self.language_confirmed = language_confirmed


class Task:
    """Модель задания (подписка на канал)"""
    def __init__(
        self,
        task_id: int,
        channel_username: str,
        channel_id: Optional[str],
        reward_amount: float,
        required_subscribers: int,
        current_subscribers: int = 0,
        is_active: bool = True,
        created_at: Optional[datetime] = None,
        channel_title: Optional[str] = None,
        eligibility_snapshot_done: bool = False,
        ended_at: Optional[datetime] = None,
    ):
        self.task_id = task_id
        self.channel_username = channel_username
        self.channel_id = channel_id           # invite-link для приватных
        self.reward_amount = reward_amount
        self.required_subscribers = required_subscribers
        self.current_subscribers = current_subscribers
        self.is_active = is_active
        self.created_at = created_at or datetime.now()
        self.channel_title = channel_title     # читаемое название канала
        # True, когда фоновая проверка уже подписанных пользователей завершена —
        # до этого момента (гонка) is_task_ineligible() может ещё не знать о
        # части пользователей, поэтому при False дополнительно делаем live-проверку.
        self.eligibility_snapshot_done = eligibility_snapshot_done
        self.ended_at = ended_at               # когда задание остановлено/заполнено (для истории)


class UserTask:
    """Связь пользователя с заданием"""
    def __init__(
        self,
        user_id: int,
        task_id: int,
        completed_at: Optional[datetime] = None,
        verified_at: Optional[datetime] = None,
        is_verified: bool = False,
        verification_failed: bool = False,
    ):
        self.user_id = user_id
        self.task_id = task_id
        self.completed_at = completed_at or datetime.now()
        self.verified_at = verified_at
        self.is_verified = is_verified
        self.verification_failed = verification_failed


class WithdrawalRequest:
    """Заявка на вывод средств"""
    def __init__(
        self,
        request_id: int,
        user_id: int,
        amount: float,
        status: str = "pending",  # pending, accepted, rejected
        admin_id: Optional[int] = None,
        created_at: Optional[datetime] = None,
        posted_to_channel_at: Optional[datetime] = None,
        processed_at: Optional[datetime] = None,
    ):
        self.request_id = request_id
        self.user_id = user_id
        self.amount = amount
        self.status = status
        self.admin_id = admin_id
        self.created_at = created_at or datetime.now()
        self.posted_to_channel_at = posted_to_channel_at
        self.processed_at = processed_at


class SupportTicket:
    """Тикет поддержки"""
    def __init__(
        self,
        ticket_id: int,
        user_id: int,
        reason: str,
        description: str,
        status: str = "open",  # open, taken, closed
        admin_id: Optional[int] = None,
        created_at: Optional[datetime] = None,
        processed_at: Optional[datetime] = None,
    ):
        self.ticket_id = ticket_id
        self.user_id = user_id
        self.reason = reason
        self.description = description
        self.status = status
        self.admin_id = admin_id
        self.created_at = created_at or datetime.now()
        self.processed_at = processed_at


class ChatRequest:
    """Запрос на связь с админом"""
    def __init__(
        self,
        request_id: int,
        user_id: int,
        status: str = "pending",  # pending, taken, closed
        admin_id: Optional[int] = None,
        created_at: Optional[datetime] = None
    ):
        self.request_id = request_id
        self.user_id = user_id
        self.status = status
        self.admin_id = admin_id
        self.created_at = created_at or datetime.now()


class AdRequest:
    """Заявка на рекламу от пользователя"""
    def __init__(
        self,
        request_id: int,
        user_id: int,
        channel_link: str,
        budget: float,
        target_subscribers: int,
        reward_per_user: float,
        status: str = "pending",
        admin_id: Optional[int] = None,
        task_id: Optional[int] = None,
        created_at: Optional[datetime] = None,
        payment_method: str = "manual",  # "manual", "balance", "stars"
        stars_charge_id: Optional[str] = None,
        processed_at: Optional[datetime] = None,
    ):
        self.request_id = request_id
        self.user_id = user_id
        self.channel_link = channel_link
        self.budget = budget
        self.target_subscribers = target_subscribers
        self.reward_per_user = reward_per_user
        self.status = status
        self.admin_id = admin_id
        self.task_id = task_id
        self.created_at = created_at or datetime.now()
        self.payment_method = payment_method
        self.stars_charge_id = stars_charge_id
        self.processed_at = processed_at


class Contest:
    """Конкурс (например «топ по приглашениям»)."""
    def __init__(
        self,
        contest_id: int,
        contest_type: str,
        title: str,
        starts_at: Optional[datetime],
        ends_at: Optional[datetime],
        status: str = "active",  # active, ended
        created_by: Optional[int] = None,
        created_at: Optional[datetime] = None,
    ):
        self.contest_id = contest_id
        self.contest_type = contest_type
        self.title = title
        self.starts_at = starts_at
        self.ends_at = ends_at
        self.status = status
        self.created_by = created_by
        self.created_at = created_at or datetime.now()


class GroupMandatorySub:
    """Обязательная подписка в группе"""
    def __init__(
        self,
        id: int,
        group_id: int,
        channel_ref: str,
        title: str,
        invite_link: Optional[str] = None,
        duration_minutes: int = 0,
        expires_at: Optional[str] = None,
        is_bot_owner: bool = False,
        added_by: Optional[int] = None,
        created_at: Optional[str] = None,
    ):
        self.id = id
        self.group_id = group_id
        self.channel_ref = channel_ref
        self.title = title
        self.invite_link = invite_link
        self.duration_minutes = duration_minutes
        self.expires_at = expires_at
        self.is_bot_owner = is_bot_owner
        self.added_by = added_by
        self.created_at = created_at or datetime.now().isoformat()


class LogEntry:
    """Запись лога"""
    def __init__(
        self,
        log_id: int,
        user_id: Optional[int],
        action: str,
        details: Optional[str] = None,
        created_at: Optional[datetime] = None
    ):
        self.log_id = log_id
        self.user_id = user_id
        self.action = action
        self.details = details
        self.created_at = created_at or datetime.now()


