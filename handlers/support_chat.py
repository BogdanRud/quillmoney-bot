"""
Ответы пользователей в переписке по тикету поддержки или заявке на вывод
"""
from aiogram import Router, F
from aiogram.types import Message, TelegramObject
from aiogram.filters import StateFilter, BaseFilter

from handlers.auth import db
from config import WEB_ADMIN_URL
from utils.security import tg_escape
from utils.i18n import all_variants, t, DEFAULT_LANG

router = Router()

_SUPPORT_BUTTON_TEXTS = all_variants("btn_support_request")


@router.message(F.text.in_(_SUPPORT_BUTTON_TEXTS))
async def handle_blocked_user_support(message: Message):
    """Обращение в поддержку для заблокированных пользователей (и не только)."""
    user_id = message.from_user.id
    user = await db.get_user(user_id)
    lang = user.language if user else DEFAULT_LANG

    # Проверяем существующий открытый тикет
    existing = await db.get_user_active_support_ticket(user_id)
    if existing:
        await message.answer(t("support_appeal_already_pending", lang))
        return

    ticket = await db.create_support_ticket(
        user_id=user_id,
        reason="block_appeal",
        description="Обращение заблокированного пользователя.",
    )
    await db.log_action(user_id, "block_appeal", "Support ticket created")

    await message.answer(t("support_appeal_sent", lang))

    # Уведомляем всех администраторов
    import aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as database:
        async with database.execute(
            "SELECT user_id FROM users WHERE role = 'Admin'"
        ) as cursor:
            async for row in cursor:
                try:
                    await message.bot.send_message(
                        row[0],
                        f"🚫 <b>Апелляция заблокированного пользователя</b>\n"
                        f"От: {tg_escape(message.from_user.full_name)} "
                        f"(@{tg_escape(message.from_user.username) or '—'}, ID {user_id})\n\n"
                        f"<a href=\"{WEB_ADMIN_URL}/admin/chat/{user_id}\">Открыть чат</a>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass


class HasActiveTicketOrWithdrawalFilter(BaseFilter):
    """Пользователь имеет активный тикет / заявку на вывод / заявку на рекламу."""
    async def __call__(self, event: TelegramObject) -> bool:
        if not isinstance(event, Message) or not event.from_user:
            return False
        ticket = await db.get_user_active_support_ticket(event.from_user.id)
        if ticket:
            return True
        withdrawal = await db.get_pending_withdrawal_for_user(event.from_user.id)
        if withdrawal:
            return True
        ad_request = await db.get_user_active_ad_request(event.from_user.id)
        return ad_request is not None


# Для обратной совместимости
HasActiveTicketFilter = HasActiveTicketOrWithdrawalFilter

MENU_BUTTONS = (
    all_variants("btn_top_users") | {"📋 Задания"} | all_variants("btn_earnings")
    | all_variants("btn_profile") | all_variants("btn_referrals")
    | all_variants("btn_ads_support") | all_variants("btn_withdraw")
    | all_variants("btn_info") | all_variants("btn_bot_help") | all_variants("btn_view_rules")
    | all_variants("btn_contests") | all_variants("btn_money_games")
    | all_variants("btn_admin_panel") | all_variants("btn_moder_panel") | all_variants("btn_crm_panel")
    | {"💬 Связаться с админом", "📱 Отправить номер телефона"}
    | _SUPPORT_BUTTON_TEXTS
)


class ReplyContentFilter(BaseFilter):
    """Текст (не кнопка меню и не команда), либо фото/документ — то, чем пользователь
    мог ответить на сообщение админа (например, прислать скриншот в доказательство)."""
    async def __call__(self, message: Message) -> bool:
        if message.photo or message.document:
            return True
        if message.text and message.text not in MENU_BUTTONS and not message.text.startswith("/"):
            return True
        return False


@router.message(
    ReplyContentFilter(),
    StateFilter(None),
    HasActiveTicketOrWithdrawalFilter(),
)
async def handle_support_reply(message: Message):
    """Сохранить ответ пользователя (текст, фото или файл) по активному тикету,
    заявке на вывод или рекламу."""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    ticket = await db.get_user_active_support_ticket(message.from_user.id)
    withdrawal = await db.get_pending_withdrawal_for_user(message.from_user.id)
    ad_request = await db.get_user_active_ad_request(message.from_user.id)

    if not ticket and not withdrawal and not ad_request:
        return

    file_id = None
    file_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        message_text = message.caption or ""
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        message_text = message.caption or message.document.file_name or ""
    else:
        message_text = message.text

    chat_request = await db.ensure_chat_request_for_user(message.from_user.id)
    assigned_admin_id = None

    if ticket:
        if chat_request.status == "pending" and ticket.admin_id:
            await db.take_chat_request(chat_request.request_id, ticket.admin_id)
        assigned_admin_id = ticket.admin_id

        await db.save_chat_message(
            chat_request_id=chat_request.request_id,
            support_ticket_id=ticket.ticket_id,
            user_id=message.from_user.id,
            admin_id=None,
            message_text=message_text,
            is_from_admin=False,
            file_id=file_id,
            file_type=file_type,
        )

        context_label = f"тикету #{ticket.ticket_id}"
    else:
        entity = withdrawal or ad_request
        await db.save_chat_message(
            chat_request_id=chat_request.request_id,
            support_ticket_id=None,
            user_id=message.from_user.id,
            admin_id=None,
            message_text=message_text,
            is_from_admin=False,
            file_id=file_id,
            file_type=file_type,
        )
        if withdrawal:
            context_label = f"заявке на вывод #{withdrawal.request_id}"
        else:
            context_label = f"заявке на рекламу #{ad_request.request_id}"

    await message.answer(t("support_message_sent_to_admin", lang))

    await db.log_action(
        message.from_user.id,
        "user_chat_reply",
        context_label,
    )
