"""
Обработчики авторизации
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from database.db import Database
from database.models import UserRole
from keyboards.menus import get_main_menu, get_admin_menu, get_role_menu
from utils.helpers import is_owner, check_channel_subscription
from utils.i18n import t, lang_name, SUPPORTED_LANGS, DEFAULT_LANG
from config import BOT_OWNERS

router = Router()
db = Database()


async def _complete_registration(bot, user_id: int, from_user, state: FSMContext, answer):
    """Создать пользователя (если его ещё нет) и показать меню. Вызывается
    сразу после подтверждения обязательных подписок — раньше здесь была
    капча, её убрали (много жалоб от пользователей, что только мешает).
    Повторная проверка подписок не нужна: она уже пройдена вызывающим кодом
    прямо перед этим вызовом, паузы (на которую раньше уходила капча) больше нет.
    answer — callable вроде message.answer/callback.message.answer."""
    user = await db.get_user(user_id)

    data = await state.get_data()
    referral_code = data.get("referral_code") or await db.get_pending_referral(user_id)

    if not user:
        user = await db.create_user(
            user_id=user_id,
            username=from_user.username,
            first_name=from_user.first_name,
        )
        await db.log_action(user_id, "user_created")

        if referral_code and referral_code != user.referral_code:
            referrer = await db.get_user_by_referral_code(referral_code)
            if referrer and await db.assign_referrer(user_id, referrer.user_id):
                # Бонус рефереру начисляется НЕ здесь, а фоновой задачей
                # (services/pending_registrations.py::credit_active_referrals_periodically)
                # — только после того, как этот пользователь проявит хоть
                # какую-то активность в боте. Так не платим за фейковые/ботовские
                # регистрации, которые никогда не возвращаются.
                await db.log_action(
                    user_id,
                    "referral_registered",
                    f"Referred by {referrer.user_id}",
                )

        await db.clear_pending_referral(user_id)

    from config import OWNER_TELEGRAM_ID
    if user_id == OWNER_TELEGRAM_ID or (from_user.username and await is_owner(from_user.username)):
        await db.update_user_role(user_id, UserRole.ADMIN)
        await db.log_action(user_id, "role_granted", "Admin (owner)")

    user = await db.get_user(user_id)
    lang = user.language if user else DEFAULT_LANG

    if user and not user.language_confirmed:
        # Язык ещё не подтверждён явно — обязательный первый шаг онбординга,
        # до правил и до меню. Показываем на английском (пока язык не выбран).
        # Дальше middlewares/rules_gate.py блокирует остальные действия, пока
        # пользователь не нажмёт одну из кнопок (см. onboard_setlang_callback).
        await answer(t("choose_language", DEFAULT_LANG), reply_markup=language_keyboard("onboard_setlang"))
        await state.clear()
        return

    if user and not user.rules_accepted_at:
        # Правила ещё не приняты — показываем их вместо меню. Дальше
        # middlewares/rules_gate.py блокирует остальные действия, пока
        # пользователь не нажмёт кнопку согласия (см. handlers/rules.py).
        from handlers.rules import rules_text, rules_keyboard
        await answer(rules_text(lang), reply_markup=rules_keyboard(lang), parse_mode="HTML")
        await state.clear()
        return

    menu = get_role_menu(user)
    username = (
        f"@{from_user.username}" if from_user.username
        else from_user.first_name or "friend"
    )
    welcome_text = t("welcome_full", lang, username=username)
    await answer(welcome_text, reply_markup=menu)

    await state.clear()


def _build_sub_keyboard(missing: list, lang: str = DEFAULT_LANG) -> InlineKeyboardMarkup:
    """Inline-кнопки для каждого недостающего канала + кнопка «Я подписался»."""
    rows = []
    for i, sub in enumerate(missing, 1):
        label = t("sub_sponsor_label", lang, n=i)
        invite = sub.get("channel_invite_link")
        ref = sub["channel_ref"].strip()

        if invite:
            url = invite
        elif ref.startswith("http"):
            url = ref
        elif ref.lstrip("-").isdigit():
            url = None  # приватный канал без invite-ссылки
        else:
            url = f"https://t.me/{ref.lstrip('@')}"

        if url:
            rows.append([InlineKeyboardButton(text=f"📢 {label}", url=url)])
        else:
            rows.append([InlineKeyboardButton(
                text=t("sub_private_locked", lang, label=label), callback_data="sub_no_link"
            )])

    rows.append([
        InlineKeyboardButton(
            text=t("sub_check_button", lang),
            callback_data="check_subscriptions",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def check_required_subs(bot, user_id: int):
    """Проверить обязательные подписки. Возвращает (blocked, missing_list)."""
    required_subs = await db.get_active_required_sub_channels()
    if not required_subs:
        return False, []
    missing = []
    for sub in required_subs:
        if not await check_channel_subscription(bot, user_id, sub["channel_ref"]):
            missing.append(sub)
    return bool(missing), missing


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отменить текущее действие / выйти из любого состояния"""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG

    current = await state.get_state()
    if current is None:
        await message.answer(t("cmd_cancel_no_active", lang))
        return
    await state.clear()
    menu = get_role_menu(user)
    await message.answer(t("fsm_action_cancelled", lang), reply_markup=menu)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    # Групповые /start сюда никогда не попадают — main.py оборачивает этот
    # роутер в private_only(), который фильтрует ВСЕ сообщения не из личных
    # чатов ещё до диспетчеризации по хендлерам. За группы отвечает отдельный
    # обработчик Command("start") в handlers/channel_mgmt.py (router там без
    # private_only). Раньше здесь была ветка на message.chat.type — она была
    # мёртвым кодом и /start в группах молча не отвечал.

    user_id = message.from_user.id
    user = await db.get_user(user_id)
    # До регистрации язык ещё не выбран — новым пользователям по умолчанию
    # показываем английский (см. create_user); уже известным — их язык.
    lang = user.language if user else DEFAULT_LANG

    if user and user.is_blocked:
        await message.answer(t("account_blocked", lang))
        return

    # Проверяем реферальный код. Сохраняем и в FSM-state (быстрый путь), и в БД
    # (надёжный путь) — между /start и подтверждением спонсорских подписок
    # пользователь может тянуть произвольно долго, а MemoryStorage не переживает
    # перезапуск бота. Раньше из-за этого реферальный код иногда тихо терялся.
    referral_code = None
    if message.text and len(message.text.split()) > 1:
        referral_code = message.text.split()[1]
        await state.update_data(referral_code=referral_code)
        if not user:
            await db.save_pending_referral(user_id, referral_code)

    # Проверяем обязательные подписки
    required_subs = await db.get_active_required_sub_channels()
    if required_subs:
        missing = []
        for sub in required_subs:
            if not await check_channel_subscription(message.bot, user_id, sub["channel_ref"]):
                missing.append(sub)

        if missing:
            await message.answer(
                t("required_subs_header", lang),
                reply_markup=_build_sub_keyboard(missing, lang),
                parse_mode="HTML",
            )
            await db.log_action(user_id, "start_blocked",
                                 f"Missing: {[s['title'] for s in missing]}")
            return

    if not user:
        # Новый пользователь — раньше здесь была капча (много жалоб, что
        # только мешает), теперь регистрация завершается сразу.
        await _complete_registration(message.bot, user_id, message.from_user, state, message.answer)
    else:
        # Пользователь уже в БД — меню
        menu = get_role_menu(user)
        await message.answer(
            t("authorized_menu", lang),
            reply_markup=menu
        )
        await state.clear()


@router.callback_query(F.data == "sub_no_link")
async def sub_no_link(callback: CallbackQuery):
    """Приватный канал без invite-ссылки — информируем."""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await callback.answer(t("sub_no_link_notice", lang), show_alert=True)


@router.callback_query(F.data == "check_subscriptions")
async def check_subscriptions_callback(callback: CallbackQuery, state: FSMContext):
    """Повторная проверка подписок после нажатия кнопки."""
    user_id = callback.from_user.id
    user = await db.get_user(user_id)
    lang = user.language if user else DEFAULT_LANG

    required_subs = await db.get_active_required_sub_channels()
    missing = []
    for sub in required_subs:
        if not await check_channel_subscription(callback.bot, user_id, sub["channel_ref"]):
            missing.append(sub)

    if missing:
        await callback.answer(t("subs_still_missing", lang), show_alert=True)
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_build_sub_keyboard(missing, lang)
            )
        except Exception:
            pass
        return

    # Все подписки пройдены — продолжаем авторизацию
    await callback.answer(t("subs_all_confirmed", lang))

    if user:
        menu = get_role_menu(user)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(t("welcome_back", lang), reply_markup=menu)
    else:
        # Новый пользователь — раньше здесь была капча, убрали (реферальный
        # код сохранён в state, регистрация завершается сразу).
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _complete_registration(
            callback.bot, user_id, callback.from_user, state, callback.message.answer
        )


def language_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора языка. prefix различает контекст callback_data:
    'setlang_user' — личный язык пользователя, 'setlang_group' — язык группы
    (см. handlers/channel_mgmt.py), 'onboard_setlang' — обязательный выбор
    при первом входе (см. middlewares/rules_gate.py и onboard_setlang_callback)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lang_name(code), callback_data=f"{prefix}:{code}")]
        for code in SUPPORTED_LANGS
    ])


@router.message(F.chat.type == "private", Command("language", "язык", "мова"))
async def cmd_language(message: Message):
    """Выбор личного языка интерфейса (только в личных сообщениях — язык
    группы задаётся отдельно, через меню /start в группе, и только админом)."""
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await message.answer(t("choose_language", lang), reply_markup=language_keyboard("setlang_user"))


@router.callback_query(F.data == "show_language_menu")
async def show_language_menu(callback: CallbackQuery):
    """Тот же выбор языка, но по кнопке (например, из профиля), а не по команде."""
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await callback.message.answer(t("choose_language", lang), reply_markup=language_keyboard("setlang_user"))
    await callback.answer()


@router.callback_query(F.data.startswith("setlang_user:"))
async def set_user_language_callback(callback: CallbackQuery):
    new_lang = callback.data.split(":", 1)[1]
    if new_lang not in SUPPORTED_LANGS:
        return await callback.answer()
    await db.set_user_language(callback.from_user.id, new_lang)
    try:
        await callback.message.edit_text(t("language_saved", new_lang, lang_name=lang_name(new_lang)))
    except Exception:
        await callback.message.answer(t("language_saved", new_lang, lang_name=lang_name(new_lang)))

    # Телеграм не меняет постоянную (reply) клавиатуру снизу сам по себе —
    # она остаётся такой, какой была отправлена последний раз, пока боту не
    # пришлют новую. Явно пересылаем её здесь на новом языке, иначе кнопки
    # меню молча остаются на старом языке до случайного триггера где-то ещё.
    user = await db.get_user(callback.from_user.id)
    if user:
        await callback.message.answer(t("menu_label", new_lang), reply_markup=get_role_menu(user))


@router.callback_query(F.data.startswith("onboard_setlang:"))
async def onboard_set_language_callback(callback: CallbackQuery):
    """Обязательный выбор языка при первом входе (см. language_keyboard в
    _complete_registration и middlewares/rules_gate.py). После выбора —
    правила (если ещё не приняты) либо сразу меню, если уже приняты
    раньше (актуально для существующих пользователей, которых тоже
    пересадили на этот флоу)."""
    new_lang = callback.data.split(":", 1)[1]
    if new_lang not in SUPPORTED_LANGS:
        return await callback.answer()

    user_id = callback.from_user.id
    await db.set_user_language_confirmed(user_id, new_lang)
    user = await db.get_user(user_id)

    try:
        await callback.message.delete()
    except Exception:
        pass

    if user and not user.rules_accepted_at:
        from handlers.rules import rules_text, rules_keyboard
        await callback.message.answer(
            rules_text(new_lang), reply_markup=rules_keyboard(new_lang), parse_mode="HTML"
        )
    else:
        menu = get_role_menu(user) if user else None
        await callback.message.answer(t("welcome_back", new_lang), reply_markup=menu)

    await callback.answer()

    await callback.answer()
