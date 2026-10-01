"""Фильтры по типу чата."""
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardRemove


def private_only(router: Router) -> Router:
    """Обрабатывать сообщения и callback только в личных чатах с ботом."""
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    return router


def clear_group_keyboard(message: Message) -> ReplyKeyboardRemove | None:
    """Убрать reply-клавиатуру личного меню, если ответ идёт в группу."""
    if message.chat.type in ("group", "supergroup"):
        return ReplyKeyboardRemove()
    return None
