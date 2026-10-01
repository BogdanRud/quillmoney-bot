"""
Клавиатуры меню
"""
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButtonRequestChat, ChatAdministratorRights,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from utils.i18n import t

# lang по умолчанию — 'ru', НЕ DEFAULT_LANG ('en'): это сохраняет прежнее
# поведение для всех мест кода, которые вызывают эти функции без явного
# языка (в первую очередь handlers/admin.py — там ещё не переведено, и без
# этого дефолта админка неожиданно перескочила бы на английский).
_FALLBACK_LANG = "ru"

# Права, которые Telegram требует и от пользователя, и (отдельно) от бота,
# чтобы нативный пикер чатов вообще показал канал/группу в списке выбора —
# используется везде, где нужно и подтвердить, что бот там админ, и что
# выбирающий пользователь тоже админ, БЕЗ единого запроса к get_chat_member.
_ADMIN_PICKER_RIGHTS = ChatAdministratorRights(
    is_anonymous=False,
    can_manage_chat=True,
    can_delete_messages=False,
    can_manage_video_chats=False,
    can_restrict_members=False,
    can_promote_members=False,
    can_change_info=False,
    can_invite_users=False,
    can_post_stories=False,
    can_edit_stories=False,
    can_delete_stories=False,
)


def get_chat_id_picker_keyboard(lang: str = _FALLBACK_LANG) -> ReplyKeyboardMarkup:
    """Клавиатура для /id в личке: нативный пикер Telegram — показать ID
    любого канала/группы без ограничений по правам (просто справочный инструмент)."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(
            text=t("btn_pick_channel", lang),
            request_chat=KeyboardButtonRequestChat(
                request_id=1, chat_is_channel=True, request_title=True,
            ),
        ),
        KeyboardButton(
            text=t("btn_pick_group", lang),
            request_chat=KeyboardButtonRequestChat(
                request_id=2, chat_is_channel=False, request_title=True,
            ),
        ),
    )
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True, selective=True)


def get_admin_channel_picker_keyboard(lang: str = _FALLBACK_LANG, request_id_base: int = 10) -> ReplyKeyboardMarkup:
    """Клавиатура выбора канала/группы с фильтром прав: пикер покажет только
    те чаты, где ОБА — и бот, и выбирающий пользователь — админы. Используется
    везде, где раньше требовалось вручную искать и вводить числовой ID
    (заявка на рекламу, обязательная подписка в группе и т.д.)."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(
            text=t("btn_pick_channel", lang),
            request_chat=KeyboardButtonRequestChat(
                request_id=request_id_base, chat_is_channel=True,
                request_title=True, request_username=True,
                user_administrator_rights=_ADMIN_PICKER_RIGHTS,
                bot_administrator_rights=_ADMIN_PICKER_RIGHTS,
            ),
        ),
        KeyboardButton(
            text=t("btn_pick_group", lang),
            request_chat=KeyboardButtonRequestChat(
                request_id=request_id_base + 1, chat_is_channel=False,
                request_title=True, request_username=True,
                user_administrator_rights=_ADMIN_PICKER_RIGHTS,
                bot_administrator_rights=_ADMIN_PICKER_RIGHTS,
            ),
        ),
    )
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True, selective=True)


def get_phone_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для запроса номера телефона"""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="📱 Отправить номер телефона", request_contact=True))
    return builder.as_markup(resize_keyboard=True)


def get_main_menu(lang: str = _FALLBACK_LANG) -> ReplyKeyboardMarkup:
    """Главное меню"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=t("btn_earnings", lang)),
        KeyboardButton(text=t("btn_profile", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_referrals", lang)),
        KeyboardButton(text=t("btn_ads_support", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_top_users", lang)),
        KeyboardButton(text=t("btn_withdraw", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_info", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_bot_help", lang)),
        KeyboardButton(text=t("btn_view_rules", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_contests", lang)),
        KeyboardButton(text=t("btn_money_games", lang)),
    )
    return builder.as_markup(resize_keyboard=True)


def get_admin_menu(lang: str = _FALLBACK_LANG) -> ReplyKeyboardMarkup:
    """Меню администратора"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=t("btn_admin_panel", lang)),
        KeyboardButton(text=t("btn_earnings", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_profile", lang)),
        KeyboardButton(text=t("btn_referrals", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_ads_support", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_top_users", lang)),
        KeyboardButton(text=t("btn_withdraw", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_info", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_bot_help", lang)),
        KeyboardButton(text=t("btn_view_rules", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_contests", lang)),
        KeyboardButton(text=t("btn_money_games", lang)),
    )
    return builder.as_markup(resize_keyboard=True)

def get_moder_menu(lang: str = _FALLBACK_LANG) -> ReplyKeyboardMarkup:
    """Меню модератора"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=t("btn_moder_panel", lang)),
        KeyboardButton(text=t("btn_earnings", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_profile", lang)),
        KeyboardButton(text=t("btn_ads_support", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_top_users", lang)),
        KeyboardButton(text=t("btn_withdraw", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_info", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_bot_help", lang)),
        KeyboardButton(text=t("btn_view_rules", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_contests", lang)),
        KeyboardButton(text=t("btn_money_games", lang)),
    )
    return builder.as_markup(resize_keyboard=True)


def get_manager_menu(lang: str = _FALLBACK_LANG) -> ReplyKeyboardMarkup:
    """Меню менеджера CRM — обычное пользовательское меню плюс кнопка входа в CRM."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=t("btn_crm_panel", lang)),
        KeyboardButton(text=t("btn_earnings", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_profile", lang)),
        KeyboardButton(text=t("btn_referrals", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_ads_support", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_top_users", lang)),
        KeyboardButton(text=t("btn_withdraw", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_info", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_bot_help", lang)),
        KeyboardButton(text=t("btn_view_rules", lang)),
    )
    builder.row(
        KeyboardButton(text=t("btn_contests", lang)),
        KeyboardButton(text=t("btn_money_games", lang)),
    )
    return builder.as_markup(resize_keyboard=True)


def get_role_menu(user) -> ReplyKeyboardMarkup:
    """Вернуть нужное меню по роли пользователя, на его языке."""
    from database.models import UserRole
    lang = getattr(user, "language", None) or _FALLBACK_LANG
    if user and user.role == UserRole.ADMIN:
        return get_admin_menu(lang)
    if user and user.role == UserRole.MODER:
        return get_moder_menu(lang)
    if user and user.role == UserRole.MANAGER:
        return get_manager_menu(lang)
    return get_main_menu(lang)


def get_admin_panel(lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Панель администратора (крупные кнопки, по 1–2 в ряд)."""
    from config import WEB_ADMIN_URL

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_new_ad", lang), callback_data="admin:create_ad"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_broadcast", lang), callback_data="admin:broadcast"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_withdrawals", lang), callback_data="admin:withdrawals"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_ad_requests", lang), callback_data="admin:ad_requests"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_tickets", lang), callback_data="admin:tickets"),
        InlineKeyboardButton(text=t("admin_btn_appeals", lang), callback_data="admin:appeals"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_grant_admin", lang), callback_data="admin:grant_admin"),
        InlineKeyboardButton(text=t("admin_btn_revoke_admin", lang), callback_data="admin:revoke_admin"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_grant_moder", lang), callback_data="admin:grant_moder"),
        InlineKeyboardButton(text=t("admin_btn_revoke_moder", lang), callback_data="admin:revoke_moder"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_grant_manager", lang), callback_data="admin:grant_manager"),
        InlineKeyboardButton(text=t("admin_btn_revoke_manager", lang), callback_data="admin:revoke_manager"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_block", lang), callback_data="admin:block_user"),
        InlineKeyboardButton(text=t("admin_btn_unblock", lang), callback_data="admin:unblock_user"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_delete_user", lang), callback_data="admin:delete_user"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_delete_task", lang), callback_data="admin:delete_task"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_contests", lang), callback_data="admin:contests"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_web_panel", lang), url=WEB_ADMIN_URL),
    )
    return builder.as_markup()


def get_moder_panel(lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Панель модератора (без Admin-управления и веб-панели)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_new_ad", lang), callback_data="admin:create_ad"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_broadcast", lang), callback_data="admin:broadcast"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_withdrawals", lang), callback_data="admin:withdrawals"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_ad_requests", lang), callback_data="admin:ad_requests"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_tickets", lang), callback_data="admin:tickets"),
        InlineKeyboardButton(text=t("admin_btn_appeals", lang), callback_data="admin:appeals"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_block", lang), callback_data="admin:block_user"),
        InlineKeyboardButton(text=t("admin_btn_unblock", lang), callback_data="admin:unblock_user"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_delete_user", lang), callback_data="admin:delete_user"),
    )
    builder.row(
        InlineKeyboardButton(text=t("admin_btn_contests", lang), callback_data="admin:contests"),
    )
    return builder.as_markup()


def get_tasks_keyboard(tasks, ineligible_task_ids=frozenset(), lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Клавиатура с заданиями.
    ineligible_task_ids — задания, для которых пользователь уже был подписан
    на канал до их создания: показываем их, но как недоступные."""
    builder = InlineKeyboardBuilder()
    for task in tasks:
        if task.task_id in ineligible_task_ids:
            text = t("task_ineligible_label", lang, id=task.task_id)
        else:
            per_user = task.reward_amount / max(task.required_subscribers, 1)
            text = t("task_available_label", lang, id=task.task_id, amount=f"{per_user:.2f}")
        builder.row(InlineKeyboardButton(
            text=text,
            callback_data=f"task:{task.task_id}"
        ))
    builder.row(InlineKeyboardButton(text=t("btn_my_history", lang), callback_data="task:my_history"))
    builder.row(InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_menu"))
    return builder.as_markup()


def get_support_reasons_keyboard(lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Клавиатура причин обращения в поддержку"""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text=t("support_reason_question", lang), callback_data="support_reason:question"))
    builder.add(InlineKeyboardButton(text=t("support_reason_problem", lang), callback_data="support_reason:problem"))
    builder.add(InlineKeyboardButton(text=t("support_reason_suggestion", lang), callback_data="support_reason:suggestion"))
    builder.add(InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_menu"))
    return builder.as_markup()


def get_withdrawal_action_keyboard(request_id: int, lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Клавиатура принять / отклонить заявку на вывод"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("btn_accept", lang),
            callback_data=f"withdrawal:accept:{request_id}",
        ),
        InlineKeyboardButton(
            text=t("btn_reject", lang),
            callback_data=f"withdrawal:reject:{request_id}",
        ),
    )
    return builder.as_markup()


def get_withdrawal_notification_keyboard(request_id: int, user_id: int, lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Клавиатура уведомления о выводе (с кнопкой чата, как в тикетах)"""
    from config import WEB_ADMIN_URL
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=t("btn_accept", lang),
            callback_data=f"withdrawal:accept:{request_id}",
        ),
        InlineKeyboardButton(
            text=t("btn_reject", lang),
            callback_data=f"withdrawal:reject:{request_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=t("admin_btn_message_user", lang),
            url=f"{WEB_ADMIN_URL}/admin/chat/{user_id}",
        ),
    )
    return builder.as_markup()


def get_withdrawal_accept_keyboard(request_id: int, lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Алиас для обратной совместимости."""
    return get_withdrawal_action_keyboard(request_id, lang)


def get_ticket_accept_keyboard(ticket_id: int, lang: str = _FALLBACK_LANG) -> InlineKeyboardMarkup:
    """Клавиатура для принятия тикета"""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text=t("btn_accept", lang),
        callback_data=f"ticket:accept:{ticket_id}"
    ))
    return builder.as_markup()


def get_blocked_keyboard(lang: str = _FALLBACK_LANG) -> ReplyKeyboardMarkup:
    """Клавиатура для заблокированного пользователя"""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=t("btn_support_request", lang)))
    return builder.as_markup(resize_keyboard=True)


def get_back_keyboard() -> InlineKeyboardMarkup:
    """Кнопка назад"""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"))
    return builder.as_markup()

