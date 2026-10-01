"""
Конкурсы — раздел "🏆 Конкурсы". Пока один вид: топ по приглашениям
(referral_top), в будущем сюда добавятся другие виды.
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from handlers.auth import db
from utils.i18n import t, all_variants, DEFAULT_LANG

router = Router()

_CONTESTS_TEXTS = all_variants("btn_contests")

_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _medal(place: int) -> str:
    return _MEDALS.get(place, "🏅")


def _format_prize_lines(prizes: list, lang: str) -> str:
    return "\n".join(
        t("contest_prize_line", lang, medal=_medal(p["place"]), place=p["place"], amount=p["amount"])
        for p in prizes
    )


async def build_contest_card_text(contest, lang: str) -> str:
    prizes = await db.get_contest_prizes(contest.contest_id)
    end_date = contest.ends_at.strftime("%d.%m.%Y %H:%M") if contest.ends_at else "—"
    key = f"contest_card_{contest.contest_type}"
    return t(
        key, lang,
        title=contest.title,
        prize_lines=_format_prize_lines(prizes, lang),
        end_date=end_date,
    )


def _contests_list_keyboard(contests, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🏆 {c.title}", callback_data=f"contest:view:{c.contest_id}")]
        for c in contests
    ]
    rows.append([InlineKeyboardButton(text=t("btn_back", lang), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _contest_card_keyboard(contest_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_contest_results", lang), callback_data=f"contest:results:{contest_id}")],
        [InlineKeyboardButton(text=t("btn_back_to_contests", lang), callback_data="contest:list")],
    ])


def _contest_results_keyboard(contest_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_back", lang), callback_data=f"contest:view:{contest_id}")],
    ])


async def _send_contests_list(user_id: int, lang: str, send):
    contests = await db.get_active_contests()
    if not contests:
        await send(t("contest_list_empty", lang))
        return
    await send(
        t("contest_list_title", lang),
        reply_markup=_contests_list_keyboard(contests, lang),
        parse_mode="HTML",
    )


@router.message(F.text.in_(_CONTESTS_TEXTS))
async def show_contests(message: Message):
    user = await db.get_user(message.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    await _send_contests_list(message.from_user.id, lang, message.answer)


@router.callback_query(F.data == "contest:list")
async def contest_list_callback(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    contests = await db.get_active_contests()
    if not contests:
        try:
            await callback.message.edit_text(t("contest_list_empty", lang))
        except Exception:
            await callback.message.answer(t("contest_list_empty", lang))
        await callback.answer()
        return
    try:
        await callback.message.edit_text(
            t("contest_list_title", lang),
            reply_markup=_contests_list_keyboard(contests, lang),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            t("contest_list_title", lang),
            reply_markup=_contests_list_keyboard(contests, lang),
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("contest:view:"))
async def contest_view_callback(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    lang = user.language if user else DEFAULT_LANG
    contest_id = int(callback.data.split(":")[2])

    contest = await db.get_contest(contest_id)
    if not contest or contest.status != "active":
        await callback.answer(t("contest_list_empty", lang), show_alert=True)
        return

    card_text = await build_contest_card_text(contest, lang)
    try:
        await callback.message.edit_text(
            card_text,
            reply_markup=_contest_card_keyboard(contest_id, lang),
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.answer(
            card_text,
            reply_markup=_contest_card_keyboard(contest_id, lang),
            parse_mode="HTML",
        )
    await callback.answer()


async def build_leaderboard_text(contest, lang: str, viewer_id: int = None) -> str:
    """Текст топа конкурса. Если передан viewer_id и он не попал в топ —
    дописывает его собственную позицию (используется и в боте для
    пользователя, и в админ-панели при просмотре конкурса)."""
    leaderboard = await db.get_contest_leaderboard(contest.contest_id)
    text = t("contest_results_title", lang, title=contest.title)

    if not leaderboard:
        text += t("contest_results_empty", lang)
    else:
        rows = []
        for i, row in enumerate(leaderboard, 1):
            name = row["first_name"] or (f"@{row['username']}" if row["username"] else str(row["user_id"]))
            rows.append(t("contest_results_row", lang, rank=i, name=name, count=row["count"]))
        text += "\n".join(rows)

    if viewer_id is not None:
        in_top = any(row["user_id"] == viewer_id for row in leaderboard)
        if not in_top:
            stats = await db.get_user_contest_stats(contest.contest_id, viewer_id)
            if stats["count"] > 0:
                text += t("contest_your_rank_known", lang, rank=stats["rank"], count=stats["count"])
            else:
                text += t("contest_your_rank_zero", lang)

    return text


@router.callback_query(F.data.startswith("contest:results:"))
async def contest_results_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await db.get_user(user_id)
    lang = user.language if user else DEFAULT_LANG
    contest_id = int(callback.data.split(":")[2])

    contest = await db.get_contest(contest_id)
    if not contest:
        await callback.answer(t("contest_list_empty", lang), show_alert=True)
        return

    text = await build_leaderboard_text(contest, lang, viewer_id=user_id)

    try:
        await callback.message.edit_text(
            text, reply_markup=_contest_results_keyboard(contest_id, lang), parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text, reply_markup=_contest_results_keyboard(contest_id, lang), parse_mode="HTML"
        )
    await callback.answer()
