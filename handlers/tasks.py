"""
Обработчики заданий
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from handlers.auth import db
from config import SUBSCRIPTION_HOLD_DAYS
from utils.i18n import t, DEFAULT_LANG

router = Router()


def _back_to_tasks_keyboard(lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    """Кнопка возврата к списку заданий."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_to_tasks", lang), callback_data="show_tasks_list")
    ]])


def _channel_chat_id(channel_username: str):
    """
    Возвращает аргумент для get_chat_member:
      int  если channel_username — числовой ID (приватный канал)
      str  "@username" если публичный
    """
    ref = channel_username.strip()
    if ref.lstrip('-').isdigit():
        return int(ref)
    return f"@{ref.lstrip('@')}"


def _is_private_channel(channel_username: str) -> bool:
    ref = channel_username.strip()
    return ref.lstrip('-').isdigit() or ref.startswith("+")


async def _check_user_subscribed(bot, user_id: int, channel_username: str) -> bool:
    """
    Строгая проверка подписки: при любой ошибке возвращает False.
    Поддерживает как @username, так и числовой chat_id.
    Для приватных ссылок-приглашений (+...) пропускает проверку (нельзя проверить).
    """
    if channel_username.startswith("+"):
        return True
    try:
        chat_id = _channel_chat_id(channel_username)
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator", "restricted")
    except Exception:
        return False


def _channel_display(task, lang: str = DEFAULT_LANG) -> str:
    """Читаемое название канала для сообщений."""
    if task.channel_title:
        return task.channel_title
    if task.channel_username.startswith("+"):
        return task.channel_id or t("task_channel_private_label", lang)
    if _is_private_channel(task.channel_username):
        return t("task_channel_private_label", lang)
    return f"@{task.channel_username}"


def _subscribe_keyboard(task, lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    """
    Клавиатура с кнопкой подписки и кнопкой повторной проверки.
    Для приватных каналов использует invite-link из task.channel_id.
    """
    rows = []
    display = _channel_display(task, lang)

    if _is_private_channel(task.channel_username):
        if task.channel_id:
            rows.append([
                InlineKeyboardButton(
                    text=t("btn_task_join_private", lang, channel=display),
                    url=task.channel_id,
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=t("btn_task_private_no_link", lang, channel=display),
                    callback_data="noop_private",
                )
            ])
    else:
        rows.append([
            InlineKeyboardButton(
                text=t("btn_task_subscribe", lang, channel=display),
                url=f"https://t.me/{task.channel_username}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text=t("btn_task_check_subscribed", lang),
            callback_data=f"task:{task.task_id}",
        ),
        InlineKeyboardButton(
            text=t("btn_back", lang),
            callback_data="back_to_menu",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "noop_private")
async def noop_private(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await callback.answer(t("task_private_notice", lang), show_alert=True)


@router.callback_query(F.data.startswith("task:"))
async def handle_task(callback: CallbackQuery):
    """Обработчик выбора задания"""
    data_part = callback.data.split(":")[1]

    # «task:my_history» обрабатывается в user_menu — здесь игнорируем
    if not data_part.isdigit():
        return

    task_id = int(data_part)
    user_id = callback.from_user.id

    user = await db.get_user(user_id)
    if not user:
        await callback.answer(t("not_registered", DEFAULT_LANG), show_alert=True)
        return
    lang = user.language

    task = await db.get_task(task_id)
    if not task or not task.is_active:
        await callback.answer(t("task_not_found_or_inactive", lang), show_alert=True)
        return

    if task.current_subscribers >= task.required_subscribers:
        await callback.answer(t("task_limit_reached", lang), show_alert=True)
        return

    if await db.is_task_ineligible(task_id, user_id):
        await callback.answer(t("task_ineligible_alert", lang), show_alert=True)
        return

    user_task = await db.get_user_task(user_id, task_id)
    if user_task:
        if user_task.verification_failed:
            await db.delete_failed_user_task(user_id, task_id)
        elif user_task.is_verified:
            await callback.answer(t("task_already_verified_alert", lang), show_alert=True)
            return
        else:
            await callback.answer(
                t("task_pending_verification_alert", lang, days=SUBSCRIPTION_HOLD_DAYS),
                show_alert=True,
            )
            return

    # ── Строгая проверка подписки ────────────────────────────────
    per_user = task.reward_amount / max(task.required_subscribers, 1)
    channel_display = _channel_display(task, lang)

    is_subscribed = await _check_user_subscribed(
        callback.bot, user_id, task.channel_username
    )

    if not is_subscribed:
        # Запоминаем: этот пользователь на момент клика ещё НЕ подписан —
        # если он потом подпишется и нажмёт снова, это докажет, что
        # подписка появилась именно ради задания, а не была раньше.
        await db.mark_task_pending_subscriber(task_id, user_id)

        # Всегда показываем popup — он работает даже если текст сообщения не изменился
        await callback.answer(
            t("task_not_subscribed_popup", lang, channel=channel_display),
            show_alert=True,
        )
        # Пытаемся обновить сообщение (может не измениться — ловим исключение)
        try:
            await callback.message.edit_text(
                t(
                    "task_card_not_subscribed", lang,
                    id=task_id, channel=channel_display, reward=per_user,
                    check_btn=t("btn_task_check_subscribed", lang),
                ),
                reply_markup=_subscribe_keyboard(task, lang),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    # Подписан сейчас, но ни разу не был замечен НЕ подписанным на это же
    # задание раньше — значит это его самый первый клик по заданию, и уже
    # тогда он был подписан. Telegram не даёт узнать дату вступления, но
    # раз мы не видели его "ещё не подписан", он не мог подписаться ради
    # этого задания — он был в канале/группе и до него.
    if not await db.was_task_pending_subscriber(task_id, user_id):
        await db.mark_task_ineligible(task_id, user_id)
        await callback.answer(t("task_ineligible_alert", lang), show_alert=True)
        return

    # ── Всё ок — фиксируем выполнение ───────────────────────────
    try:
        await db.create_user_task(user_id, task_id)
    except Exception:
        # UNIQUE(user_id, task_id) — это задание уже зафиксировано параллельным
        # запросом (например, два почти одновременных нажатия кнопки проверки).
        # Не начисляем награду повторно.
        await callback.answer(t("task_already_recorded_alert", lang), show_alert=True)
        return

    # Атомарный инкремент в БД (UPDATE ... SET balance = balance + ?), а не
    # «прочитать user.unverified_balance -> посчитать -> записать абсолютное
    # значение» — иначе при двух почти одновременных начислениях (например,
    # выполнение двух разных заданий подряд) один из инкрементов может
    # потеряться (классическая lost update race condition).
    await db.adjust_user_balance(user_id, unverified_delta=per_user)
    await db.increment_task_subscribers(task_id)

    await callback.message.edit_text(
        t(
            "task_completed_message", lang,
            id=task_id, channel=channel_display, reward=per_user,
            days=SUBSCRIPTION_HOLD_DAYS,
        ),
        reply_markup=_back_to_tasks_keyboard(lang),
        parse_mode="HTML",
    )

    await db.log_action(
        user_id,
        "task_completed",
        f"Task #{task_id}, Reward: ${per_user:.2f}",
    )
    await callback.answer()
