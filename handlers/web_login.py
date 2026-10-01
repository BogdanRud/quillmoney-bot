"""
Обработчик подтверждения входа в веб-панель через Telegram.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from web_admin import login_requests
from database.models import UserRole
from handlers.auth import db
from utils.i18n import t, DEFAULT_LANG

router = Router()


async def _is_admin(user_id: int) -> bool:
    """Разрешить подтвердить вход в веб-панель — Admin и Manager (Manager
    получит доступ только к CRM, см. web_admin/middleware.py)."""
    from config import OWNER_TELEGRAM_ID
    if user_id == OWNER_TELEGRAM_ID:
        return True
    user = await db.get_user(user_id)
    return bool(user and user.role in (UserRole.ADMIN, UserRole.MANAGER))


async def _user_lang(user_id: int) -> str:
    user = await db.get_user(user_id)
    return user.language if user else DEFAULT_LANG


@router.callback_query(F.data.startswith("web_login_approve:"))
async def handle_approve(callback: CallbackQuery):
    token = callback.data.split(":", 1)[1]
    lang = await _user_lang(callback.from_user.id)

    if not await _is_admin(callback.from_user.id):
        await callback.answer(t("weblogin_no_access", lang), show_alert=True)
        return

    req = login_requests.get_request(token)
    if not req:
        await callback.answer(t("weblogin_request_not_found", lang), show_alert=True)
        await callback.message.edit_text(t("weblogin_request_expired_title", lang))
        return

    # Подтвердить вход может только тот, для кого он был запрошен — иначе
    # один админ мог бы подтверждать сессии другого, если бы токен где-то утёк.
    if callback.from_user.id != req["user_id"]:
        await callback.answer(t("weblogin_not_your_request", lang), show_alert=True)
        return

    if req["status"] != "pending":
        key = "weblogin_already_approved" if req["status"] == "approved" else "weblogin_already_rejected"
        await callback.answer(t(key, lang), show_alert=True)
        return

    login_requests.approve(token)
    await db.log_action(callback.from_user.id, "web_login_approved", f"token={token[:8]}…")
    await callback.answer(t("weblogin_approved_alert", lang))
    await callback.message.edit_text(
        t("weblogin_approved_message", lang, user_id=req["user_id"]),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("web_login_reject:"))
async def handle_reject(callback: CallbackQuery):
    token = callback.data.split(":", 1)[1]
    lang = await _user_lang(callback.from_user.id)

    if not await _is_admin(callback.from_user.id):
        await callback.answer(t("weblogin_no_access", lang), show_alert=True)
        return

    req = login_requests.get_request(token)
    if not req:
        await callback.answer(t("weblogin_request_not_found", lang), show_alert=True)
        await callback.message.edit_text(t("weblogin_request_expired_title", lang))
        return

    if callback.from_user.id != req["user_id"]:
        await callback.answer(t("weblogin_not_your_request", lang), show_alert=True)
        return

    if req["status"] != "pending":
        key = "weblogin_already_approved" if req["status"] == "approved" else "weblogin_already_rejected"
        await callback.answer(t(key, lang), show_alert=True)
        return

    login_requests.reject(token)
    await db.log_action(callback.from_user.id, "web_login_rejected", f"token={token[:8]}…")
    await callback.answer(t("weblogin_rejected_alert", lang))
    await callback.message.edit_text(
        t("weblogin_rejected_message", lang, user_id=req["user_id"]),
        parse_mode="HTML",
    )
