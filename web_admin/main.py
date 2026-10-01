"""
Веб-сервер для панели администратора
"""
from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import uvicorn
from typing import Optional, List
from datetime import datetime
import json

from web_admin.auth import (
    create_access_token, get_current_admin, verify_admin_by_telegram,
    ACCESS_TOKEN_EXPIRE_MINUTES
)
from web_admin.middleware import (
    AuthMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware, IPWhitelistMiddleware,
    AccessLogMiddleware,
)
from database.db import Database
from database.models import UserRole
from config import BOT_OWNERS, BOT_TOKEN, COOKIE_SECURE, ALLOWED_ORIGINS, FORCE_HTTPS

app = FastAPI(title="Quill Studio Admin Panel", docs_url=None, redoc_url=None, openapi_url=None)

if FORCE_HTTPS:
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    app.add_middleware(HTTPSRedirectMiddleware)

# CORS — без credentials+wildcard (небезопасная комбинация: с ней куки/токены
# можно было бы читать с любого домена). Список доменов берётся из .env (ALLOWED_ORIGINS).
if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

# Порядок важен: starlette применяет middleware в обратном порядке добавления —
# security-заголовки и rate-limit должны срабатывать раньше auth-проверки.
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(IPWhitelistMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# Добавлен последним -> выполняется первым (самый внешний слой), видит
# абсолютно все запросы, включая отброшенные другими middleware.
app.add_middleware(AccessLogMiddleware)

# Настройка шаблонов
BASE_DIR = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web_admin" / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web_admin" / "static")), name="static")

db = Database()


@app.on_event("startup")
async def startup_event():
    """Инициализация базы данных при запуске"""
    await db.init_db()
    print("✅ База данных инициализирована")


@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница входа"""
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/api/login")
async def login(telegram_id: int = Form(...), request: Request = None):
    """Вход через Telegram ID — отправляет запрос на подтверждение в Telegram."""
    from fastapi.responses import JSONResponse
    import logging
    security_log = logging.getLogger("security")

    try:
        await db.init_db()

        is_admin = await verify_admin_by_telegram(telegram_id)
        from config import OWNER_TELEGRAM_ID
        if not is_admin and telegram_id == OWNER_TELEGRAM_ID:
            is_admin = True

        if not is_admin:
            ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.client.host if request.client else "unknown")
            security_log.warning("Неудачная попытка входа: telegram_id=%s ip=%s", telegram_id, ip)
            return JSONResponse(
                status_code=403,
                content={"detail": "Доступ запрещён. Вы не являетесь администратором."}
            )

        # Создаём pending-запрос и отправляем уведомление в Telegram
        from web_admin import login_requests
        from aiogram import Bot
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        try:
            token = login_requests.create_request(telegram_id)
        except login_requests.TooManyRequests:
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много попыток входа для этого аккаунта. Попробуйте позже."},
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Войти", callback_data=f"web_login_approve:{token}"),
            InlineKeyboardButton(text="🚫 Отклонить", callback_data=f"web_login_reject:{token}"),
        ]])

        try:
            if BOT_TOKEN:
                bot = Bot(token=BOT_TOKEN)
                await bot.send_message(
                    telegram_id,
                    f"🔐 <b>Попытка входа в веб-панель</b>\n\n"
                    f"Telegram ID: <code>{telegram_id}</code>\n\n"
                    f"Разрешить доступ?",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                await bot.session.close()
        except Exception as tg_err:
            print(f"Не удалось отправить уведомление в Telegram: {tg_err}")
            return JSONResponse(
                status_code=500,
                content={"detail": "Не удалось отправить уведомление в Telegram. Проверьте, что вы начали диалог с ботом."}
            )

        return JSONResponse({"pending": True, "token": token})

    except Exception:
        import logging, traceback
        logging.getLogger(__name__).error("Login error:\n%s", traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка. Попробуйте позже."})


@app.get("/api/login/poll/{token}")
async def login_poll(token: str, request: Request):
    """Проверка статуса pending-запроса на вход."""
    from web_admin import login_requests

    req = login_requests.get_request(token)
    if not req:
        return JSONResponse({"status": "expired"})

    status = req["status"]
    if status == "approved":
        # Выдаём JWT и устанавливаем cookie
        telegram_id = req["user_id"]
        access_token = create_access_token(data={"sub": telegram_id}, request=request)
        response = JSONResponse({"status": "approved"})
        response.set_cookie(
            key="admin_token",
            value=access_token,
            httponly=True,
            max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            samesite="lax",
            path="/",
            secure=COOKIE_SECURE,
        )
        return response

    return JSONResponse({"status": status})


@app.post("/api/logout")
async def logout(request: Request):
    """Серверный логаут: отзывает JWT (по jti) и удаляет cookie."""
    from web_admin import login_requests
    from jose import jwt as _jwt, JWTError as _JWTError
    from web_admin.auth import SECRET_KEY, ALGORITHM

    token = request.cookies.get("admin_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if token:
        try:
            payload = _jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
            jti = payload.get("jti")
            if jti:
                login_requests.revoke_jti(jti)
        except _JWTError:
            pass

    response = JSONResponse({"success": True})
    response.delete_cookie("admin_token", path="/")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Главная страница админ-панели"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    
    # Если пользователь не найден в БД, но это владелец - создаем временный объект
    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "admin": admin["user"]
    })


@app.get("/admin/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request):
    """Страница заданий"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    
    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    
    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "admin": admin["user"]
    })


@app.get("/api/tasks")
async def get_tasks(request: Request):
    """API для получения списка АКТИВНЫХ заданий (завершённые — см. /api/tasks/history)"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    tasks = await db.get_active_tasks()
    result = []
    for task in tasks:
        owner = None
        ad_request = await db.get_ad_request_by_task_id(task.task_id)
        if ad_request:
            owner_user = await db.get_user(ad_request.user_id)
            owner = {
                "user_id": ad_request.user_id,
                "name": owner_user.first_name if owner_user else None,
                "username": owner_user.username if owner_user else None,
            }
        result.append({
            "task_id": task.task_id,
            "channel_username": task.channel_username,
            "channel_title": task.channel_title or task.channel_username,
            "reward_amount": task.reward_amount,
            "required_subscribers": task.required_subscribers,
            "current_subscribers": task.current_subscribers,
            "is_active": task.is_active,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "owner": owner,
        })
    return {"tasks": result}


@app.get("/admin/tasks/history", response_class=HTMLResponse)
async def tasks_history_page(request: Request):
    """Страница истории заданий (завершённые/остановленные)"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)

    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])

    return templates.TemplateResponse("task_history.html", {
        "request": request,
        "admin": admin["user"]
    })


@app.get("/api/tasks/history")
async def get_tasks_history(request: Request):
    """API: список завершённых/остановленных заданий"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    tasks = await db.get_task_history()
    result = []
    for task in tasks:
        owner = None
        ad_request = await db.get_ad_request_by_task_id(task.task_id)
        if ad_request:
            owner_user = await db.get_user(ad_request.user_id)
            owner = {
                "user_id": ad_request.user_id,
                "name": owner_user.first_name if owner_user else None,
                "username": owner_user.username if owner_user else None,
            }
        result.append({
            "task_id": task.task_id,
            "channel_username": task.channel_username,
            "channel_title": task.channel_title or task.channel_username,
            "reward_amount": task.reward_amount,
            "required_subscribers": task.required_subscribers,
            "current_subscribers": task.current_subscribers,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "ended_at": task.ended_at.isoformat() if task.ended_at else None,
            "completed_fully": task.current_subscribers >= task.required_subscribers,
            "owner": owner,
        })
    return {"tasks": result}


@app.get("/api/tasks/{task_id}/completions")
async def get_task_completions_api(task_id: int, request: Request):
    """API: кто выполнил конкретное задание (для детального просмотра в истории)"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    completions = await db.get_task_completions(task_id)
    return {
        "task": {
            "task_id": task.task_id,
            "channel_username": task.channel_username,
            "channel_title": task.channel_title or task.channel_username,
            "reward_amount": task.reward_amount,
            "required_subscribers": task.required_subscribers,
            "current_subscribers": task.current_subscribers,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "ended_at": task.ended_at.isoformat() if task.ended_at else None,
        },
        "completions": completions,
    }


async def _parse_channel_ref(raw: str):
    """
    Разбирает любой ввод и возвращает (chat_ref, is_private, error_msg).
    chat_ref — то, что передаётся в get_chat_member:
        str  "@username"   — публичный канал/группа
        int  -1001234...   — приватный канал по числовому ID
    error_msg — строка, если формат не поддерживается напрямую.
    """
    import re
    ref = raw.strip()

    # Пригласительная ссылка t.me/+ — Bot API не умеет резолвить её напрямую,
    # но бот запоминает chat_id по invite-ссылке при каждом добавлении в чат
    # (bot_chat_invites) — сначала пробуем найти чат так, и только если не
    # нашли, просим числовой ID вручную.
    if re.match(r'https?://t\.me/\+', ref, re.IGNORECASE) or re.match(r't\.me/\+', ref, re.IGNORECASE):
        resolved_chat_id = await db.find_chat_by_invite_link(ref)
        if resolved_chat_id is not None:
            return resolved_chat_id, True, None
        return None, True, (
            "Бот не найден в этом приватном канале/группе (по этой ссылке "
            "бот ранее не добавлялся).\n\n"
            "1️⃣ Добавьте бота в канал/группу как администратора\n"
            "2️⃣ Отправьте эту же ссылку сюда ещё раз — бот сам подтянет ID\n\n"
            "Либо отправьте команду /id прямо в чате и введите полученный "
            "числовой ID вручную (например: -1001234567890)."
        )

    # Числовой ID (начинается с -100...)
    if re.match(r'^-?\d+$', ref):
        return int(ref), True, None

    # https://t.me/username (публичный, включая реферальные ?start=... ссылки)
    if re.match(r'https?://t\.me/', ref, re.IGNORECASE):
        path = re.sub(r'https?://t\.me/', '', ref, flags=re.IGNORECASE).strip('/')
        username = path.split('?')[0].split('/')[0].strip()
        if username:
            return f"@{username}", False, None
        return None, False, "Не удалось извлечь username из ссылки."

    # @username или просто username
    username = ref.lstrip('@')
    if username:
        return f"@{username}", False, None

    return None, False, "Укажите username или числовой ID канала."


async def _bot_channel_check(channel_username: str) -> dict:
    """Проверить, является ли бот участником/администратором канала.
    Возвращает {"ok": True, ...} или {"ok": False, "error": "..."}.
    """
    from aiogram import Bot

    chat_ref, is_private, err_msg = await _parse_channel_ref(channel_username)
    if err_msg and chat_ref is None:
        return {"ok": False, "error": err_msg}

    bot = None
    try:
        bot = Bot(token=BOT_TOKEN)
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_ref, me.id)
        if member.status in ("administrator", "creator", "member", "restricted"):
            # Получаем название канала
            try:
                chat_info = await bot.get_chat(chat_ref)
                chat_title = chat_info.title or str(chat_ref)
            except Exception:
                chat_title = str(chat_ref)
            return {
                "ok": True,
                "status": member.status,
                "bot_username": me.username,
                "is_private": is_private,
                "chat_ref": str(chat_ref),
                "chat_title": chat_title,
            }
        return {
            "ok": False,
            "error": (
                f"Бот @{me.username} не является участником канала. "
                "Добавьте бота в канал (желательно как администратора) и попробуйте снова."
            ),
        }
    except Exception as e:
        msg = str(e)
        ref_display = str(chat_ref) if chat_ref else channel_username
        if "chat not found" in msg.lower():
            err = f"Канал {ref_display} не найден. Проверьте ID или username."
        elif "user not found" in msg.lower() or "PARTICIPANT_ID_INVALID" in msg:
            err = f"Бот не состоит в канале {ref_display}."
        elif "not enough rights" in msg.lower():
            err = f"Бот есть в канале, но не имеет прав администратора. Выдайте боту права администратора."
        else:
            err = f"Бот не в канале {ref_display} или канал недоступен. Добавьте бота как администратора."
        return {"ok": False, "error": err}
    finally:
        if bot:
            try:
                await bot.session.close()
            except Exception:
                pass


async def _snapshot_task_ineligibility(task_id: int, channel_username: str):
    """Фоновая задача: помечает пользователей, уже подписанных на канал в момент
    создания задания, как не имеющих права на его награду — она для новых
    подписчиков, а не для тех, кто и так уже состоял в канале/группе.
    Для invite-only ссылок (+...) пропускается — членство нельзя проверить.

    Реальную защиту от "старых" подписчиков теперь обеспечивает click-time
    логика в handlers/tasks.py::handle_task (task_pending_subscribers — если
    юзер ни разу не был замечен НЕ подписанным перед тем как оказаться
    подписанным, значит он был в канале ещё до задания). Эта фоновая джоба —
    дополнительный, не обязательный для корректности прогон (например, для
    статистики в веб-панели), поэтому не должна мешать легитимному сценарию:
    пропускаем и тех, кто уже выполнил задание (user_task), и тех, кто сейчас
    в процессе (task_pending_subscribers — уже кликал, ещё не подписан или
    вот-вот нажмёт повторно) — им нельзя ставить ineligible заранее.

    Задержки между проверками убраны: get_chat_member — лёгкий read-only
    запрос не из группы send_message лимитов, для которой считался
    BROADCAST_DELAY_SECONDS; настоящий бэкофф — по TelegramRetryAfter."""
    if channel_username.startswith("+"):
        await db.mark_eligibility_snapshot_done(task_id)
        return
    from aiogram import Bot
    from aiogram.exceptions import TelegramRetryAfter
    from handlers.tasks import _channel_chat_id
    import asyncio

    bot = Bot(token=BOT_TOKEN)
    chat_id = _channel_chat_id(channel_username)
    marked = 0
    try:
        users = await db.get_all_users()
        for user in users:
            # Уже легитимно выполнил задание — не трогаем, это не "старый подписчик".
            if await db.get_user_task(user.user_id, task_id):
                continue
            # Уже кликал по заданию и был замечен НЕ подписанным — он сейчас
            # либо ждёт своей подписки, либо вот-вот нажмёт "Я подписался"
            # повторно; click-time логика сама всё корректно решит.
            if await db.was_task_pending_subscriber(task_id, user.user_id):
                continue
            try:
                member = await bot.get_chat_member(chat_id, user.user_id)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try:
                    member = await bot.get_chat_member(chat_id, user.user_id)
                except Exception:
                    continue
            except Exception:
                continue
            if member.status in ("member", "administrator", "creator", "restricted"):
                await db.mark_task_ineligible(task_id, user.user_id)
                marked += 1
        await db.mark_eligibility_snapshot_done(task_id)
    finally:
        await bot.session.close()
    print(f"[TaskEligibility] task #{task_id}: исключено уже подписанных — {marked}")


@app.get("/api/tasks/check-channel")
async def check_task_channel_api(request: Request, channel: str = ""):
    """Проверить, что бот является участником канала перед созданием задания."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    result = await _bot_channel_check(channel)
    return result


@app.post("/api/tasks/{task_id}/delete")
async def delete_task_api(task_id: int, request: Request):
    """Удалить задание. Если оно было создано из платной заявки на рекламу
    (баланс/звёзды) и заполнено не полностью — возвращает пользователю
    неизрасходованную часть бюджета (пропорционально незаполненным местам)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    from aiogram import Bot
    refund_bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
    try:
        result = await db.delete_task_and_refund(task_id, bot=refund_bot)
    finally:
        if refund_bot:
            await refund_bot.session.close()

    ad_request = result["ad_request"]
    refund_amount = result["refund_amount"]
    refund_stars_usd = result["refund_stars_usd"]
    remaining = result["remaining"]

    await db.log_action(
        admin["user_id"], "task_deleted_web",
        f"Task #{task_id} @{task.channel_username}, refund_balance=${refund_amount:.2f}, "
        f"refund_stars=${refund_stars_usd:.2f}",
    )

    if refund_amount > 0 or refund_stars_usd > 0:
        try:
            from config import BOT_TOKEN as _TOKEN
            if _TOKEN:
                bot = Bot(token=_TOKEN)
                lines = [
                    f"ℹ️ Ваше рекламное задание #{task_id} (@{task.channel_username}) "
                    f"остановлено администратором.\n",
                    f"Заполнено {task.current_subscribers} из {task.required_subscribers} мест — "
                    f"оставшиеся {remaining} не были использованы.",
                ]
                if refund_stars_usd > 0:
                    lines.append(f"⭐ Часть оплаты (≈${refund_stars_usd:.2f}) возвращена звёздами на ваш счёт Telegram.")
                if refund_amount > 0:
                    lines.append(f"💰 <b>${refund_amount:.2f}</b> возвращено на ваш баланс в боте.")
                await bot.send_message(ad_request.user_id, "\n".join(lines), parse_mode="HTML")
                await bot.session.close()
        except Exception:
            pass

    return {"success": True, "refund_amount": refund_amount, "refund_stars_usd": refund_stars_usd}


@app.post("/api/tasks")
async def create_task(
    request: Request,
    background_tasks: BackgroundTasks,
    channel_username: str = Form(...),
    reward_per_user: Optional[float] = Form(None),
    reward_amount: Optional[float] = Form(None),
    required_subscribers: int = Form(...),
    channel_invite_link: Optional[str] = Form(None),
    channel_title: Optional[str] = Form(None),
):
    """Создать новое задание.
    channel_username: @username (публичный) или -1001234567890 (приватный).
    channel_invite_link: необязательная ссылка-приглашение для приватных каналов.
    """
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if reward_per_user is not None:
        total_reward = reward_per_user * required_subscribers
    elif reward_amount is not None:
        total_reward = reward_amount
    else:
        raise HTTPException(status_code=400, detail="Укажите reward_per_user или reward_amount")

    # Проверяем, что бот состоит в канале
    check = await _bot_channel_check(channel_username)
    if not check["ok"]:
        raise HTTPException(status_code=400, detail=check["error"])

    # Нормализуем: для публичных — username без @; для приватных — числовая строка
    chat_ref = check["chat_ref"]          # "@username" или "-100XXXXXXXX"
    is_private = check.get("is_private", False)

    if is_private:
        stored_username = chat_ref        # "-1001234567890"
    else:
        stored_username = chat_ref.lstrip("@")   # "channelname"

    invite_link = (channel_invite_link or "").strip() or None
    # channel_title: берём из формы (передаётся фронтендом после проверки), или из check
    title = (channel_title or "").strip() or check.get("chat_title") or stored_username

    try:
        task = await db.create_task(
            channel_username=stored_username,
            channel_id=invite_link,       # хранит invite-link для приватных каналов
            reward_amount=total_reward,
            required_subscribers=required_subscribers,
            channel_title=title,
        )
        per_user = total_reward / max(required_subscribers, 1)
        await db.log_action(
            admin["user_id"], "task_created_web",
            f"Task #{task.task_id}, ref={stored_username}, per_user=${per_user:.2f}"
        )
        background_tasks.add_task(_snapshot_task_ineligibility, task.task_id, stored_username)
        return {"success": True, "task_id": task.task_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/admin/users", response_class=HTMLResponse)
async def users_page(request: Request):
    """Страница пользователей"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    
    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    
    return templates.TemplateResponse("users.html", {
        "request": request,
        "admin": admin["user"]
    })


@app.get("/api/users")
async def get_users(request: Request):
    """API для получения списка пользователей"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    users = await db.get_all_users()
    # Build a lookup map for referred_by names
    user_map = {u.user_id: u for u in users}
    users_data = []
    for user in users:
        referred_by = getattr(user, "referred_by", None)
        if referred_by and referred_by in user_map:
            referrer = user_map[referred_by]
            referred_by_name = referrer.first_name or referrer.username or str(referred_by)
            referred_by_username = referrer.username
        elif referred_by:
            referred_by_name = str(referred_by)
            referred_by_username = None
        else:
            referred_by_name = None
            referred_by_username = None
        users_data.append({
            "user_id": user.user_id,
            "username": user.username,
            "first_name": user.first_name,
            "phone": user.phone,
            "role": user.role.value,
            "unverified_balance": user.unverified_balance,
            "verified_balance": user.verified_balance,
            "referrals_count": user.referrals_count,
            "is_blocked": getattr(user, "is_blocked", False),
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "referred_by": referred_by,
            "referred_by_name": referred_by_name,
            "referred_by_username": referred_by_username,
            "language": getattr(user, "language", None),
            "rules_accepted": bool(getattr(user, "rules_accepted_at", None)),
        })
    return {"users": users_data}


@app.post("/api/users/{user_id}/balance")
async def update_user_balance_api(
    user_id: int,
    request: Request,
    verified_balance: Optional[float] = Form(None),
    unverified_balance: Optional[float] = Form(None),
    referrals_count: Optional[int] = Form(None),
):
    """Изменить баланс и/или количество рефералов пользователя (для тестов
    и ручной коррекции — например, аннулирование накрученных рефералов)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    kwargs = {}
    if verified_balance is not None:
        kwargs["verified"] = float(verified_balance)
    if unverified_balance is not None:
        kwargs["unverified"] = float(unverified_balance)
    if kwargs:
        await db.update_user_balance(user_id, **kwargs)

    if referrals_count is not None:
        await db.update_user_referrals_count(user_id, int(referrals_count))

    if not kwargs and referrals_count is None:
        raise HTTPException(status_code=400, detail="Укажите баланс и/или количество рефералов")

    await db.log_action(
        admin["user_id"],
        "balance_updated_web",
        f"User {user_id}: balance={kwargs}, referrals_count={referrals_count}",
    )
    user = await db.get_user(user_id)
    return {
        "success": True,
        "user_id": user_id,
        "verified_balance": user.verified_balance,
        "unverified_balance": user.unverified_balance,
        "referrals_count": user.referrals_count,
    }


@app.post("/api/users/{user_id}/grant_admin")
async def grant_admin_api(user_id: int, request: Request):
    """Выдать роль Admin"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="Нельзя изменить свою роль")
    await db.update_user_role(user_id, UserRole.ADMIN)
    await db.log_action(admin["user_id"], "admin_granted_web", f"To user {user_id}")
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(user_id,
                "🎉 Вам выдана роль <b>Администратор</b>!\n\nТеперь у вас есть доступ к панели администратора.",
                parse_mode="HTML")
            await bot.session.close()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/users/{user_id}/revoke_admin")
async def revoke_admin_api(user_id: int, request: Request):
    """Снять роль Admin"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="Нельзя снять Admin у себя")
    await db.update_user_role(user_id, UserRole.USER)
    await db.log_action(admin["user_id"], "admin_revoked_web", f"From user {user_id}")
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(user_id, "⚠️ Ваша роль <b>Администратор</b> была снята.", parse_mode="HTML")
            await bot.session.close()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/users/{user_id}/grant_moder")
async def grant_moder_api(user_id: int, request: Request):
    """Выдать роль Moderator"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.role == UserRole.ADMIN:
        raise HTTPException(status_code=400, detail="Нельзя понизить администратора до модератора")
    if user.role == UserRole.MODER:
        raise HTTPException(status_code=400, detail="Пользователь уже является модератором")
    await db.update_user_role(user_id, UserRole.MODER)
    await db.log_action(admin["user_id"], "moder_granted_web", f"To user {user_id}")
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(user_id, "🎉 Вам выдана роль <b>Модератор</b>!\n\nТеперь у вас есть доступ к панели модератора.", parse_mode="HTML")
            await bot.session.close()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/users/{user_id}/revoke_moder")
async def revoke_moder_api(user_id: int, request: Request):
    """Снять роль Moderator"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.role != UserRole.MODER:
        raise HTTPException(status_code=400, detail="Пользователь не является модератором")
    await db.update_user_role(user_id, UserRole.USER)
    await db.log_action(admin["user_id"], "moder_revoked_web", f"From user {user_id}")
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(user_id, "⚠️ Ваша роль <b>Модератор</b> была снята.", parse_mode="HTML")
            await bot.session.close()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/users/{user_id}/block")
async def block_user_api(user_id: int, request: Request):
    """Заблокировать пользователя"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="Нельзя заблокировать себя")
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await db.block_user(user_id)
    await db.log_action(admin["user_id"], "user_blocked_web", f"User {user_id}")
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(user_id, "🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку.")
            await bot.session.close()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/users/{user_id}/unblock")
async def unblock_user_api(user_id: int, request: Request):
    """Разблокировать пользователя"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await db.unblock_user(user_id)
    await db.log_action(admin["user_id"], "user_unblocked_web", f"User {user_id}")
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(user_id, "✅ Ваш аккаунт разблокирован. Добро пожаловать обратно!")
            await bot.session.close()
    except Exception:
        pass
    return {"success": True}


@app.post("/api/users/{user_id}/delete")
async def delete_user_api(user_id: int, request: Request):
    """Удалить пользователя и все его данные"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="Нельзя удалить себя")
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    await db.delete_user(user_id)
    await db.log_action(admin["user_id"], "user_deleted_web", f"Deleted user {user_id}")
    return {"success": True}


@app.get("/admin/ad-requests", response_class=HTMLResponse)
async def ad_requests_page(request: Request):
    """Страница заявок на рекламу"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        return RedirectResponse(url="/", status_code=302)
    if not admin.get("user"):
        class TempUser:
            def __init__(self, uid):
                self.user_id = uid
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    return templates.TemplateResponse("ad_requests.html", {
        "request": request,
        "admin": admin["user"],
    })


@app.get("/api/ad-requests")
async def get_ad_requests(request: Request):
    """Список заявок на рекламу"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    requests = await db.get_pending_ad_requests()
    result = []
    for req in requests:
        user = await db.get_user(req.user_id)
        result.append({
            "request_id": req.request_id,
            "user_id": req.user_id,
            "payment_method": req.payment_method,
            "user_name": user.first_name if user else "Неизвестно",
            "username": f"@{user.username}" if user and user.username else "Нет",
            "channel_link": req.channel_link,
            "budget": req.budget,
            "target_subscribers": req.target_subscribers,
            "reward_per_user": req.reward_per_user,
            "status": req.status,
            "task_id": req.task_id,
            "created_at": req.created_at.isoformat() if req.created_at else None,
        })
    return {"requests": result}


@app.get("/api/ad-requests/history")
async def get_ad_requests_history_api(request: Request):
    """История уже обработанных заявок на рекламу (принятые/отклонённые)"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    requests = await db.get_ad_requests_history()
    result = []
    for req in requests:
        user = await db.get_user(req.user_id)
        result.append({
            "request_id": req.request_id,
            "user_id": req.user_id,
            "payment_method": req.payment_method,
            "user_name": user.first_name if user else "Неизвестно",
            "username": f"@{user.username}" if user and user.username else "Нет",
            "channel_link": req.channel_link,
            "budget": req.budget,
            "target_subscribers": req.target_subscribers,
            "reward_per_user": req.reward_per_user,
            "status": req.status,
            "task_id": req.task_id,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "processed_at": req.processed_at.isoformat() if req.processed_at else None,
        })
    return {"requests": result}


@app.post("/api/ad-requests/{request_id}/accept")
async def accept_ad_request_api(request_id: int, request: Request, background_tasks: BackgroundTasks):
    """Принять заявку на рекламу и создать задание"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    req = await db.get_ad_request(request_id)
    if not req or req.status not in ("pending", "taken"):
        raise HTTPException(status_code=400, detail="Заявка уже обработана")

    # Создаём задание
    raw_link = req.channel_link.strip()
    channel_username = raw_link
    channel_id = None
    if "https://t.me/" in raw_link:
        channel_username = raw_link.split("https://t.me/")[-1]
    channel_username = channel_username.lstrip("@")

    # Если это приватная ссылка-приглашение (+...), храним оригинал
    if channel_username.startswith("+"):
        channel_id = raw_link

    # Проверяем, что бот реально состоит в этом канале/группе, ПЕРЕД созданием
    # задания — иначе пользователи не смогут подтвердить подписку, и заявку
    # придётся отклонять постфактум.
    if channel_username.startswith("+"):
        resolved_chat_id = await db.find_chat_by_invite_link(raw_link)
        if resolved_chat_id is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Это приватная ссылка-приглашение — бот не может проверить её напрямую. "
                    "Добавьте бота в этот канал/группу администратором (тогда бот сам "
                    "запомнит ссылку) или отправьте там /id, затем повторите принятие заявки."
                ),
            )
        check = await _bot_channel_check(str(resolved_chat_id))
    else:
        check = await _bot_channel_check(channel_username)
    if not check["ok"]:
        raise HTTPException(status_code=400, detail=check["error"])

    if req.status == "pending":
        await db.take_ad_request(request_id, admin["user_id"])

    commission_percent = 15
    net_budget = req.budget * (1 - commission_percent / 100)
    reward_per_user = net_budget / req.target_subscribers

    task = await db.create_task(
        channel_username=channel_username,
        channel_id=channel_id,
        reward_amount=net_budget,
        required_subscribers=req.target_subscribers,
    )

    await db.accept_ad_request(request_id, admin["user_id"], task.task_id)
    background_tasks.add_task(_snapshot_task_ineligibility, task.task_id, channel_username)

    try:
        from aiogram import Bot
        from config import BOT_TOKEN
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                req.user_id,
                f"✅ <b>Ваша заявка на рекламу #{request_id} принята!</b>\n\n"
                f"Задание для пользователей создано.\n"
                f"Канал: <code>{channel_username}</code>\n"
                f"Награда за подписку: <b>${reward_per_user:.2f}</b>",
                parse_mode="HTML",
            )
            await bot.session.close()
    except Exception:
        pass

    await db.log_action(admin["user_id"], "ad_request_accepted_web",
                        f"AdRequest #{request_id}, Task #{task.task_id}")
    return {"success": True, "task_id": task.task_id}


@app.post("/api/ad-requests/{request_id}/reject")
async def reject_ad_request_api(request_id: int, request: Request):
    """Отклонить заявку на рекламу"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    req = await db.get_ad_request(request_id)
    if not req or req.status not in ("pending", "taken"):
        raise HTTPException(status_code=400, detail="Заявка уже обработана")

    await db.reject_ad_request(request_id, admin["user_id"])

    refund_note = ""
    if req.payment_method == "balance":
        await db.adjust_user_balance(req.user_id, verified_delta=req.budget)
        refund_note = f"\n\n💰 <b>${req.budget:.2f}</b> возвращено на ваш баланс."
    elif req.payment_method == "stars" and req.stars_charge_id:
        try:
            from aiogram import Bot
            from config import BOT_TOKEN
            if BOT_TOKEN:
                refund_bot = Bot(token=BOT_TOKEN)
                await refund_bot.refund_star_payment(
                    user_id=req.user_id,
                    telegram_payment_charge_id=req.stars_charge_id,
                )
                await refund_bot.session.close()
                refund_note = "\n\n⭐ Звёзды возвращены на ваш счёт Telegram."
        except Exception:
            refund_note = "\n\n⚠️ Не удалось автоматически вернуть звёзды, обратитесь в поддержку."

    try:
        from aiogram import Bot
        from config import BOT_TOKEN
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                req.user_id,
                f"❌ Ваша заявка на рекламу #{request_id} отклонена администратором.{refund_note}",
                parse_mode="HTML",
            )
            await bot.session.close()
    except Exception:
        pass

    await db.log_action(admin["user_id"], "ad_request_rejected_web",
                        f"AdRequest #{request_id}, refund_method={req.payment_method}")
    return {"success": True}


@app.get("/admin/tickets", response_class=HTMLResponse)
async def tickets_page(request: Request):
    """Страница тикетов поддержки"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)

    if not admin.get("user"):
        class TempUser:
            def __init__(self, uid):
                self.user_id = uid
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])

    return templates.TemplateResponse("tickets.html", {
        "request": request,
        "admin": admin["user"],
    })


@app.get("/api/tickets")
async def get_tickets(request: Request, status: Optional[str] = None):
    """Список тикетов поддержки"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    tickets = await db.get_support_tickets_list(status=status or None)
    result = []
    for t in tickets:
        user = await db.get_user(t.user_id)
        result.append({
            "ticket_id": t.ticket_id,
            "user_id": t.user_id,
            "user_name": user.first_name if user else "Неизвестно",
            "username": f"@{user.username}" if user and user.username else "Нет",
            "phone": user.phone if user else "Нет",
            "reason": t.reason,
            "description": t.description,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    return {"tickets": result}


@app.post("/api/tickets/{ticket_id}/take")
async def take_ticket(ticket_id: int, request: Request):
    """Взять тикет в работу"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    ticket = await db.get_support_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if ticket.status != "open":
        raise HTTPException(status_code=400, detail="Тикет уже в работе или закрыт")

    await db.take_support_ticket(ticket_id, admin["user_id"])

    try:
        from aiogram import Bot
        from config import BOT_TOKEN
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                ticket.user_id,
                f"✅ Ваше обращение #{ticket_id} принято. Администратор скоро ответит в этом чате.",
            )
            await bot.session.close()
    except Exception:
        pass

    await db.log_action(admin["user_id"], "ticket_taken_web", f"Ticket #{ticket_id}")
    return {"success": True, "chat_url": f"/admin/chat/{ticket.user_id}"}


@app.post("/api/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: int, request: Request):
    """Закрыть тикет"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    ticket = await db.get_support_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Тикет не найден")

    import aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as database:
        await database.execute(
            "UPDATE support_tickets SET status = 'closed' WHERE ticket_id = ?",
            (ticket_id,),
        )
        await database.commit()

    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                ticket.user_id,
                f"✅ Обращение #{ticket_id} закрыто. "
                f"При необходимости вы можете создать новое в разделе поддержки.",
            )
            await bot.session.close()
    except Exception:
        pass

    return {"success": True}


@app.get("/admin/withdrawals", response_class=HTMLResponse)
async def withdrawals_page(request: Request):
    """Страница заявок на вывод"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    
    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    
    return templates.TemplateResponse("withdrawals.html", {
        "request": request,
        "admin": admin["user"]
    })


@app.get("/api/withdrawals")
async def get_withdrawals(request: Request):
    """API для получения заявок на вывод"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    requests = await db.get_pending_withdrawal_requests()
    withdrawals_data = []
    for req in requests:
        user = await db.get_user(req.user_id)
        withdrawals_data.append({
            "request_id": req.request_id,
            "user_id": req.user_id,
            "user_name": user.first_name if user else "Неизвестно",
            "username": f"@{user.username}" if user and user.username else "Нет",
            "phone": user.phone if user else "Нет",
            "amount": req.amount,
            "verified_balance": user.verified_balance if user else 0,
            "status": req.status,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "rules_accepted": bool(user and user.rules_accepted_at),
        })
    return {"withdrawals": withdrawals_data}


@app.get("/api/withdrawals/history")
async def get_withdrawals_history(request: Request):
    """API для получения истории обработанных заявок на вывод (принятые/отклонённые)"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    requests = await db.get_withdrawal_history()
    withdrawals_data = []
    for req in requests:
        user = await db.get_user(req.user_id)
        withdrawals_data.append({
            "request_id": req.request_id,
            "user_id": req.user_id,
            "user_name": user.first_name if user else "Неизвестно",
            "username": f"@{user.username}" if user and user.username else "Нет",
            "phone": user.phone if user else "Нет",
            "amount": req.amount,
            "verified_balance": user.verified_balance if user else 0,
            "status": req.status,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "posted_to_channel_at": req.posted_to_channel_at.isoformat() if req.posted_to_channel_at else None,
            "rules_accepted": bool(user and user.rules_accepted_at),
        })
    return {"withdrawals": withdrawals_data}


@app.post("/api/withdrawals/{request_id}/post_to_channel")
async def post_withdrawal_to_channel(
    request_id: int,
    request: Request,
    text: str = Form(...),
    images: List[UploadFile] = File(default=[]),
):
    """Опубликовать скриншот выплаты в канале "Выводы QuillMoney" — только
    вручную, по нажатию кнопки в веб-панели (никакой автоматики)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    req = await db.get_withdrawal_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Текст поста не может быть пустым")

    from aiogram import Bot
    from aiogram.types import FSInputFile, InputMediaPhoto
    from config import BOT_TOKEN, WITHDRAWALS_CHANNEL_ID
    import tempfile, os

    if not BOT_TOKEN:
        raise HTTPException(status_code=400, detail="BOT_TOKEN не настроен")

    temp_paths = []
    for img in images:
        if not img.filename:
            continue
        content = await img.read()
        if not content:
            continue
        suffix = os.path.splitext(img.filename)[1] or ".jpg"
        temp_path = os.path.join(
            tempfile.gettempdir(),
            f"withdrawal_post_{request_id}_{len(temp_paths)}_{datetime.now().timestamp()}{suffix}",
        )
        with open(temp_path, "wb") as f:
            f.write(content)
        temp_paths.append(temp_path)

    bot = Bot(token=BOT_TOKEN)
    try:
        if not temp_paths:
            await bot.send_message(WITHDRAWALS_CHANNEL_ID, text, parse_mode="HTML")
        elif len(temp_paths) == 1:
            await bot.send_photo(
                WITHDRAWALS_CHANNEL_ID,
                FSInputFile(temp_paths[0]),
                caption=text,
                parse_mode="HTML",
            )
        else:
            media = [
                InputMediaPhoto(media=FSInputFile(p), caption=text if i == 0 else None, parse_mode="HTML" if i == 0 else None)
                for i, p in enumerate(temp_paths)
            ]
            await bot.send_media_group(WITHDRAWALS_CHANNEL_ID, media)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Не удалось опубликовать: {e}")
    finally:
        await bot.session.close()
        for p in temp_paths:
            try:
                os.remove(p)
            except OSError:
                pass

    await db.mark_withdrawal_posted(request_id)
    await db.log_action(
        admin["user_id"], "withdrawal_posted_to_channel",
        f"Request #{request_id}, images={len(temp_paths)}",
    )

    return {"success": True}


@app.post("/api/withdrawals/{request_id}/accept")
async def accept_withdrawal(
    request_id: int,
    request: Request
):
    """Принять заявку на вывод"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    request_obj = await db.get_withdrawal_request(request_id)
    if not request_obj:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    ok, reason = await db.accept_withdrawal_atomic(request_id, admin["user_id"])
    if not ok:
        details = {
            "already_processed": "Заявка уже обработана",
            "insufficient_balance": "Недостаточно средств на балансе пользователя",
        }
        raise HTTPException(status_code=400, detail=details.get(reason, "Ошибка обработки"))
    
    await db.log_action(
        admin["user_id"],
        "withdrawal_accepted_web",
        f"Request #{request_id}"
    )
    
    return {"success": True}


@app.post("/api/withdrawals/{request_id}/reject")
async def reject_withdrawal(request_id: int, request: Request):
    """Отклонить заявку на вывод"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    request_obj = await db.get_withdrawal_request(request_id)
    if not request_obj:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    ok, reason = await db.reject_withdrawal_atomic(request_id, admin["user_id"])
    if not ok:
        raise HTTPException(status_code=400, detail="Заявка уже обработана")

    await db.log_action(
        admin["user_id"],
        "withdrawal_rejected_web",
        f"Request #{request_id}",
    )

    try:
        from aiogram import Bot
        from config import BOT_TOKEN
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                request_obj.user_id,
                f"❌ Ваша заявка на вывод #{request_id} на сумму ${request_obj.amount:.2f} "
                f"отклонена. Средства остались на балансе.",
            )
            await bot.session.close()
    except Exception:
        pass

    return {"success": True}


@app.get("/admin/chat/{user_id}", response_class=HTMLResponse)
async def chat_page(
    user_id: int,
    request: Request
):
    """Страница переписки с пользователем"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    
    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    await db.mark_chat_read(user_id)

    from utils.i18n import lang_name
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "admin": admin["user"],
        "target_user": user,
        "target_user_lang_label": lang_name(user.language),
    })


@app.get("/api/chat/{user_id}/messages")
async def get_chat_messages(
    user_id: int,
    request: Request
):
    """Получить сообщения чата с пользователем"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    messages = await db.get_chat_messages_for_user(user_id)
    await db.mark_chat_read(user_id)
    return {"messages": messages}


@app.get("/api/chat/file/{message_id}")
async def get_chat_file(message_id: int, request: Request):
    """Отдать файл (фото/документ), прикреплённый к сообщению чата — скачивает
    его через Bot API по сохранённому file_id и отдаёт панели."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT file_id, file_type FROM chat_messages WHERE message_id = ?", (message_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Файл не найден")
    file_id, file_type = row

    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN не настроен")

    from aiogram import Bot
    bot = Bot(token=BOT_TOKEN)
    try:
        tg_file = await bot.get_file(file_id)
        buffer = await bot.download_file(tg_file.file_path)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось получить файл из Telegram: {e}")
    finally:
        await bot.session.close()

    media_type = "image/jpeg" if file_type == "photo" else "application/octet-stream"
    return Response(content=buffer.read(), media_type=media_type)


@app.post("/api/chat/{user_id}/send")
async def send_message(
    user_id: int,
    message_text: str = Form(...),
    request: Request = None
):
    """Отправить сообщение пользователю"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    chat_request = await db.ensure_chat_request_for_user(user_id)
    if chat_request.status == "pending":
        await db.take_chat_request(chat_request.request_id, admin["user_id"])

    active_ticket = await db.get_user_active_support_ticket(user_id)
    ticket_id = active_ticket.ticket_id if active_ticket else None

    await db.save_chat_message(
        chat_request_id=chat_request.request_id,
        support_ticket_id=ticket_id,
        user_id=user_id,
        admin_id=admin["user_id"],
        message_text=message_text,
        is_from_admin=True,
    )
    
    # Отправляем сообщение пользователю через бота
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                user_id,
                f"💬 <b>Сообщение от администратора:</b>\n\n{message_text}",
                parse_mode="HTML"
            )
            await bot.session.close()
    except Exception as e:
        # Если бот не доступен, просто логируем
        print(f"Не удалось отправить сообщение через бота: {e}")
    
    await db.log_action(
        admin["user_id"],
        "message_sent_web",
        f"To user {user_id}"
    )

    return {"success": True}


@app.get("/api/notifications/summary")
async def get_notifications_summary(request: Request):
    """Сводка непрочитанного/нового для бэйджей в панели (как в мессенджерах):
    сколько новых заявок в каждом разделе + сколько непрочитанных сообщений
    от каждого пользователя (для бэйджей на кнопках 'Переписка')."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH

    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending'"
        ) as cursor:
            withdrawals_pending = (await cursor.fetchone())[0]

        async with conn.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE status = 'open' AND (reason IS NULL OR reason != 'block_appeal')"
        ) as cursor:
            tickets_open = (await cursor.fetchone())[0]

        async with conn.execute(
            "SELECT COUNT(*) FROM support_tickets WHERE status = 'open' AND reason = 'block_appeal'"
        ) as cursor:
            appeals_pending = (await cursor.fetchone())[0]

        async with conn.execute(
            "SELECT COUNT(*) FROM ad_requests WHERE status = 'pending'"
        ) as cursor:
            ad_requests_pending = (await cursor.fetchone())[0]

    unread_chats = await db.get_unread_chat_counts()

    return {
        "withdrawals_pending": withdrawals_pending,
        "tickets_open": tickets_open,
        "appeals_pending": appeals_pending,
        "ad_requests_pending": ad_requests_pending,
        "unread_chats": unread_chats,
    }


@app.get("/admin/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request):
    """Страница рассылки"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    
    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    
    return templates.TemplateResponse("broadcast.html", {
        "request": request,
        "admin": admin["user"]
    })


async def _run_broadcast(admin_id: int, user_ids: list, message_text: str,
                         photo_to_send, keyboard, temp_file_path: str):
    """Фоновая задача рассылки."""
    from aiogram import Bot
    from aiogram.exceptions import TelegramRetryAfter
    from config import BOT_TOKEN, BROADCAST_DELAY_SECONDS
    import asyncio, os

    bot = Bot(token=BOT_TOKEN)
    success_count = 0
    fail_count = 0

    for user_id in user_ids:
        try:
            if photo_to_send:
                await bot.send_photo(
                    user_id,
                    photo=photo_to_send,
                    caption=message_text or None,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            elif message_text:
                await bot.send_message(
                    user_id,
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            else:
                continue
            success_count += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                if photo_to_send:
                    await bot.send_photo(user_id, photo=photo_to_send,
                                         caption=message_text or None,
                                         reply_markup=keyboard, parse_mode="HTML")
                elif message_text:
                    await bot.send_message(user_id, message_text,
                                           reply_markup=keyboard, parse_mode="HTML")
                success_count += 1
            except Exception:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            print(f"Broadcast error for {user_id}: {e}")

        if BROADCAST_DELAY_SECONDS > 0:
            await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    await bot.session.close()

    if temp_file_path:
        try:
            os.remove(temp_file_path)
        except Exception:
            pass

    await db.log_action(admin_id, "broadcast_sent_web",
                        f"Success: {success_count}, Failed: {fail_count}")
    print(f"[Broadcast] done — sent: {success_count}, failed: {fail_count}")


@app.post("/api/broadcast")
async def send_broadcast(
    background_tasks: BackgroundTasks,
    request: Request,
    message_text: Optional[str] = Form(None),
    photo: Optional[str] = Form(None),
    photo_file: Optional[UploadFile] = File(None),
    inline_buttons: Optional[str] = Form(None),
):
    """Запустить рассылку в фоне и вернуть ответ немедленно."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
    from config import BOT_TOKEN
    import tempfile, os

    if not BOT_TOKEN:
        raise HTTPException(status_code=400, detail="BOT_TOKEN не настроен")

    # Проверяем что хоть что-то передано
    has_photo_file = photo_file and photo_file.filename
    if not message_text and not photo and not has_photo_file:
        raise HTTPException(status_code=400, detail="Необходимо указать текст или фото")

    # Парсим инлайн-кнопки
    keyboard = None
    if inline_buttons and inline_buttons.strip():
        try:
            buttons_data = json.loads(inline_buttons)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            for row in buttons_data:
                kb_row = []
                for btn in row:
                    if btn.get("url"):
                        kb_row.append(InlineKeyboardButton(text=btn["text"], url=btn["url"]))
                    elif btn.get("callback_data"):
                        kb_row.append(InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]))
                if kb_row:
                    keyboard.inline_keyboard.append(kb_row)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка парсинга кнопок: {e}")

    # Обрабатываем фото — читаем до запуска фонового задания
    photo_to_send = None
    temp_file_path = None

    if has_photo_file:
        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(
            temp_dir, f"broadcast_{admin['user_id']}_{datetime.now().timestamp()}.jpg"
        )
        with open(temp_file_path, "wb") as f:
            f.write(await photo_file.read())
        photo_to_send = FSInputFile(temp_file_path)
    elif photo and photo.strip():
        photo_to_send = photo.strip()

    # Собираем список user_id
    users = await db.get_all_users()
    user_ids = [u.user_id for u in users]

    # Запускаем рассылку в фоне — запрос возвращается мгновенно
    background_tasks.add_task(
        _run_broadcast,
        admin["user_id"], user_ids, message_text or "",
        photo_to_send, keyboard, temp_file_path
    )

    await db.log_action(admin["user_id"], "broadcast_started_web", f"Users: {len(user_ids)}")

    return {
        "success": True,
        "sent": len(user_ids),
        "failed": 0,
        "message": f"Рассылка запущена для {len(user_ids)} пользователей"
    }


@app.get("/admin/bot-channels", response_class=HTMLResponse)
async def bot_channels_page(request: Request):
    """Страница событий каналов бота"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        return RedirectResponse(url="/", status_code=302)
    if not admin.get("user"):
        class TempUser:
            def __init__(self, uid):
                self.user_id = uid
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    return templates.TemplateResponse("bot_channels.html", {
        "request": request,
        "admin": admin["user"],
    })


_CRM_NAME_COLUMNS = {"название", "имя", "канал", "группа", "name", "title", "channel", "channel_name"}
# Полноценная ссылка — в приоритете. "Юзернейм"/username — только запасной
# вариант (если самой ссылки в таблице нет), из него достраивается t.me-ссылка.
_CRM_LINK_COLUMNS = {"ссылка", "link", "url", "channel_link"}
_CRM_USERNAME_COLUMNS = {"юзернейм", "username", "тег", "тэг"}
_CRM_SUBS_COLUMNS = {"подписчики", "подписчиков", "участники", "участников", "members",
                     "subscribers", "subs", "количество", "кол-во"}
_CRM_CATEGORY_COLUMNS = {"категория", "тематика", "category", "niche", "тема"}


def _parse_crm_csv(raw: bytes) -> List[dict]:
    """Разобрать CSV из парсера каналов. Колонки определяются по имени
    (регистронезависимо, RU/EN варианты) — что не распознано, попадает
    в extra_data как есть. Пока поддерживается только CSV."""
    import csv
    import io

    text = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Не удалось определить кодировку файла")

    # Автоопределение разделителя (запятая/точка с запятой — частый случай
    # для CSV, сохранённых из Excel с русской локалью)
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("Пустой файл или не удалось прочитать заголовки колонок")

    col_map = {}
    for col in reader.fieldnames:
        key = (col or "").strip().lower()
        if key in _CRM_NAME_COLUMNS:
            col_map[col] = "channel_name"
        elif key in _CRM_LINK_COLUMNS:
            col_map[col] = "channel_link"
        elif key in _CRM_USERNAME_COLUMNS:
            col_map[col] = "channel_username"
        elif key in _CRM_SUBS_COLUMNS:
            col_map[col] = "subscribers"
        elif key in _CRM_CATEGORY_COLUMNS:
            col_map[col] = "category"

    rows = []
    for raw_row in reader:
        row = {"extra": {}}
        for col, value in raw_row.items():
            if col is None:
                continue
            field = col_map.get(col)
            value = (value or "").strip()
            if field == "subscribers":
                digits = "".join(c for c in value if c.isdigit())
                row["subscribers"] = int(digits) if digits else None
            elif field:
                row[field] = value or None
            elif value:
                row["extra"][col] = value
        # Ссылка в приоритете; юзернейм — только запасной вариант, если
        # прямой ссылки в таблице нет (строим t.me-ссылку из него).
        username = row.pop("channel_username", None)
        if not row.get("channel_link") and username:
            row["channel_link"] = f"https://t.me/{username.lstrip('@')}"
        if row.get("channel_name") or row.get("channel_link"):
            rows.append(row)
    return rows


@app.get("/admin/crm", response_class=HTMLResponse)
async def crm_page(request: Request):
    """Страница CRM — лоты (каналы/группы) для менеджеров по рекламе."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        return RedirectResponse(url="/", status_code=302)
    if not admin.get("user"):
        class TempUser:
            def __init__(self, uid):
                self.user_id = uid
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])

    is_admin_role = getattr(admin["user"], "role", None) != UserRole.MANAGER
    return templates.TemplateResponse("crm.html", {
        "request": request,
        "admin": admin["user"],
        "is_admin_role": is_admin_role,
    })


@app.post("/api/crm/import")
async def crm_import(request: Request, file: UploadFile = File(...)):
    """Загрузить таблицу из парсера каналов (CSV) — только для Admin."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(admin["user"], "role", None) == UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Доступно только администратору")

    raw = await file.read()
    try:
        rows = _parse_crm_csv(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows:
        raise HTTPException(status_code=400, detail="В файле не найдено ни одной строки с названием или ссылкой канала")

    result = await db.import_crm_leads(rows, admin["user_id"])
    await db.log_action(
        admin["user_id"], "crm_import",
        f"Imported: {result['imported']}, skipped duplicates: {result['skipped_duplicates']}",
    )
    return result


@app.get("/api/crm/leads")
async def crm_get_leads(
    request: Request, status: Optional[str] = None, assigned_to: Optional[int] = None,
    mine: bool = False, deleted: bool = False,
):
    """Список лотов. Manager без mine=true видит только свободный пул ('new');
    с mine=true — свои (опционально ещё отфильтрованные по статусу, напр.
    mine=true&status=agreed — свои согласившиеся). Admin видит всё, может
    фильтровать по менеджеру. deleted=true — вкладка «Удалённые» (только Admin)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    is_manager = getattr(admin["user"], "role", None) == UserRole.MANAGER
    if is_manager:
        if mine:
            assigned_to = admin["user_id"]
        else:
            assigned_to = None
            status = "new"
        deleted = False

    leads = await db.get_crm_leads(status=status, assigned_to=assigned_to, only_deleted=deleted)

    # Подставляем имя менеджера для отображения (актуально для Admin-вида)
    manager_ids = {l["assigned_to"] for l in leads if l["assigned_to"]}
    managers = {}
    for mid in manager_ids:
        u = await db.get_user(mid)
        if u:
            managers[mid] = f"@{u.username}" if u.username else (u.first_name or str(mid))

    for lead in leads:
        lead["assigned_to_name"] = managers.get(lead["assigned_to"])

    return {"leads": leads}


@app.get("/api/crm/managers")
async def crm_get_managers(request: Request):
    """Список менеджеров CRM с краткой статистикой по их лотам —
    для вкладки «Список менеджеров» (видна только Admin)."""
    admin = _require_crm_admin(request)
    managers = await db.get_crm_managers()
    for m in managers:
        stats = await db.get_crm_stats(assigned_to=m["user_id"])
        m["active"] = (stats.get("in_progress") or 0) + (stats.get("follow_up") or 0)
        m["agreed"] = stats.get("agreed") or 0
        m["total"] = stats.get("total") or 0
    return {"managers": managers}


@app.post("/api/crm/managers/{user_id}/revoke")
async def crm_revoke_manager(user_id: int, request: Request):
    """Снять роль Manager прямо из CRM (тот же эффект, что и revoke-команда в боте)."""
    admin = _require_crm_admin(request)
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.role != UserRole.MANAGER:
        raise HTTPException(status_code=400, detail="Пользователь не является менеджером")
    await db.update_user_role(user_id, UserRole.USER)
    await db.log_action(admin["user_id"], "crm_manager_revoked_web", f"From user {user_id}")
    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(user_id, "⚠️ Ваша роль <b>Менеджер CRM</b> была снята.", parse_mode="HTML")
            await bot.session.close()
    except Exception:
        pass
    return {"success": True}


@app.get("/api/crm/stats")
async def crm_get_stats(request: Request, mine: bool = False):
    """Статистика по лотам. Manager всегда видит только свою (см. is_manager
    ниже); Admin по умолчанию видит общую, а с mine=true — только свою
    (для бейджа на личной вкладке «Мои»)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    is_manager = getattr(admin["user"], "role", None) == UserRole.MANAGER
    scope = admin["user_id"] if (is_manager or mine) else None
    stats = await db.get_crm_stats(assigned_to=scope)
    return {"stats": stats}


@app.get("/api/crm/pool_count")
async def crm_get_pool_count(request: Request):
    """Только количество свободных ('new') лотов — для бейджа вкладки
    «Свободные» у менеджера. Отдельный узкий эндпоинт, а не общая
    статистика, чтобы не раскрывать менеджеру общие цифры по всей команде."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    stats = await db.get_crm_stats(assigned_to=None)
    return {"count": stats.get("new", 0)}


@app.post("/api/crm/leads/{lead_id}/take")
async def crm_take_lead(lead_id: int, request: Request):
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    ok = await db.take_crm_lead(lead_id, admin["user_id"])
    if not ok:
        raise HTTPException(status_code=400, detail="Лот уже взят кем-то другим или не найден")
    return {"success": True}


@app.post("/api/crm/leads/{lead_id}/release")
async def crm_release_lead(lead_id: int, request: Request):
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    is_manager = getattr(admin["user"], "role", None) == UserRole.MANAGER
    ok = await db.release_crm_lead(lead_id, admin["user_id"], is_admin_override=not is_manager)
    if not ok:
        raise HTTPException(status_code=400, detail="Не удалось освободить лот")
    return {"success": True}


@app.post("/api/crm/leads/{lead_id}/status")
async def crm_update_lead_status(lead_id: int, request: Request, status: str = Form(...), note: Optional[str] = Form(None)):
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    is_manager = getattr(admin["user"], "role", None) == UserRole.MANAGER
    ok = await db.update_crm_lead_status(lead_id, admin["user_id"], status, note, is_admin_override=not is_manager)
    if not ok:
        raise HTTPException(status_code=400, detail="Не удалось изменить статус (лот не ваш или статус некорректен)")
    return {"success": True}


@app.get("/api/crm/leads/{lead_id}/history")
async def crm_get_lead_history(lead_id: int, request: Request):
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    history = await db.get_crm_lead_history(lead_id)
    return {"history": history}


def _require_crm_admin(request: Request) -> dict:
    """Удаление/восстановление лотов — только для Admin, не для Manager."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if getattr(admin["user"], "role", None) == UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    return admin


@app.post("/api/crm/leads/{lead_id}/delete")
async def crm_delete_lead(lead_id: int, request: Request):
    admin = _require_crm_admin(request)
    ok = await db.delete_crm_lead(lead_id, admin["user_id"])
    if not ok:
        raise HTTPException(status_code=400, detail="Лот не найден или уже удалён")
    return {"success": True}


@app.post("/api/crm/leads/{lead_id}/restore")
async def crm_restore_lead(lead_id: int, request: Request):
    admin = _require_crm_admin(request)
    ok = await db.restore_crm_lead(lead_id, admin["user_id"])
    if not ok:
        raise HTTPException(status_code=400, detail="Лот не найден или не был удалён")
    return {"success": True}


@app.post("/api/crm/leads/bulk_delete")
async def crm_bulk_delete_leads(request: Request, lead_ids: str = Form(...)):
    """Удалить лоты по явному списку ID (те, что показаны на текущей
    вкладке фронтенда — см. комментарий в db.bulk_delete_crm_leads)."""
    admin = _require_crm_admin(request)
    try:
        ids = [int(x) for x in json.loads(lead_ids)]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Некорректный список ID")
    count = await db.bulk_delete_crm_leads(admin["user_id"], ids)
    await db.log_action(admin["user_id"], "crm_bulk_delete", f"requested={len(ids)}, deleted={count}")
    return {"deleted": count}


@app.post("/api/crm/leads/bulk_restore")
async def crm_bulk_restore_leads(request: Request):
    admin = _require_crm_admin(request)
    count = await db.bulk_restore_crm_leads(admin["user_id"])
    await db.log_action(admin["user_id"], "crm_bulk_restore", f"count={count}")
    return {"restored": count}


@app.post("/api/crm/leads/reorder")
async def crm_reorder_leads(request: Request, lead_ids: str = Form(...)):
    """Личный ручной порядок лотов (drag-and-drop во вкладке «Мои») —
    доступно любому авторизованному пользователю CRM, обновляет позицию
    только среди лотов, назначенных ему самому (см. db.set_crm_lead_manual_order)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        ids = [int(x) for x in json.loads(lead_ids)]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Некорректный список ID")
    count = await db.set_crm_lead_manual_order(admin["user_id"], ids)
    return {"updated": count}


@app.get("/admin/games", response_class=HTMLResponse)
async def games_page(request: Request):
    """Страница истории игр на деньги"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        return RedirectResponse(url="/", status_code=302)
    if not admin.get("user"):
        class TempUser:
            def __init__(self, uid):
                self.user_id = uid
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    return templates.TemplateResponse("games.html", {
        "request": request,
        "admin": admin["user"],
    })


@app.get("/api/games/history")
async def get_games_history(request: Request):
    """История спинов (кто ставил, что выпало, сколько выиграл/проиграл)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    spins = await db.get_game_spins_history(limit=300)
    stats = await db.get_game_stats()
    return {"spins": spins, "stats": stats}


@app.get("/api/bot-channels/events")
async def get_bot_channel_events(request: Request):
    """Список событий каналов бота"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    events = await db.get_bot_channel_events(limit=200)
    return {"events": events}


@app.get("/api/bot-channels/active")
async def get_active_bot_channels(request: Request):
    """Список активных каналов бота"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    channels = await db.get_active_bot_channels()
    return {"channels": channels}


@app.get("/api/bot-channels/{chat_id}/details")
async def get_bot_channel_details(chat_id: int, request: Request):
    """Подробная информация о канале/группе (для окна деталей в админке)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from aiogram import Bot
    from config import BOT_TOKEN

    bot = Bot(token=BOT_TOKEN)
    try:
        chat = await bot.get_chat(chat_id)

        member_count = None
        try:
            member_count = await bot.get_chat_member_count(chat_id)
        except Exception:
            pass

        admins = []
        try:
            admin_list = await bot.get_chat_administrators(chat_id)
            for a in admin_list:
                admins.append({
                    "user_id": a.user.id,
                    "first_name": a.user.first_name,
                    "username": a.user.username,
                    "status": a.status,
                    "is_bot": a.user.is_bot,
                })
        except Exception:
            pass

        return {
            "chat_id": chat.id,
            "title": chat.title,
            "username": chat.username,
            "type": chat.type,
            "description": getattr(chat, "description", None),
            "member_count": member_count,
            "invite_link": getattr(chat, "invite_link", None),
            "admins": admins,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Не удалось получить данные о чате: {e}")
    finally:
        await bot.session.close()


@app.post("/api/bot-channels/leave")
async def leave_bot_channel(request: Request):
    """Бот покидает канал/группу"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from aiogram import Bot
    from config import BOT_TOKEN

    body = await request.json()
    chat_id = body.get("chat_id")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")

    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.leave_chat(chat_id=int(chat_id))
        return {"ok": True, "message": "Бот покинул чат"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка: {e}")


@app.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Страница настроек"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=302)
    
    if not admin.get("user"):
        from config import OWNER_TELEGRAM_ID
        class TempUser:
            def __init__(self, user_id):
                self.user_id = user_id
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    
    required_sub_channels = await db.get_all_required_sub_channels()

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "admin": admin["user"],
        "required_sub_channels": required_sub_channels,
        "now": datetime.now().isoformat(),
    })


@app.post("/api/settings/required-channels")
async def update_required_channels(
    request: Request,
    channels: str = Form(...)
):
    """Обновить обязательные каналы (устаревший эндпоинт, оставлен для совместимости)"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        channels_list = [ch.strip().lstrip("@") for ch in channels.split(",") if ch.strip()]
        await db.set_required_channels(channels_list)
        await db.log_action(admin["user_id"], "settings_updated",
                            f"Required channels (legacy): {channels_list}")
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/settings/required-sub-channels")
async def get_required_sub_channels_api(request: Request):
    """Список всех обязательных подписок"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    channels = await db.get_all_required_sub_channels()
    return {"channels": channels}


@app.post("/api/settings/required-sub-channels")
async def add_required_sub_channel_api(
    request: Request,
    title: str = Form(...),
    channel_ref: str = Form(...),
    duration_value: int = Form(0),
    duration_unit: str = Form("forever"),
    channel_invite_link: Optional[str] = Form(None),
):
    """Добавить обязательную подписку (с проверкой бота в канале)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    title = title.strip()
    channel_ref = channel_ref.strip()
    if not title or not channel_ref:
        raise HTTPException(status_code=400, detail="Заполните все поля")

    # Проверяем, что бот в канале
    check = await _bot_channel_check(channel_ref)
    if not check["ok"]:
        raise HTTPException(status_code=400, detail=check["error"])

    # Вычисляем expires_at
    expires_at: Optional[str] = None
    if duration_value > 0 and duration_unit != "forever":
        from datetime import timedelta
        deltas = {
            "seconds": timedelta(seconds=duration_value),
            "minutes": timedelta(minutes=duration_value),
            "hours":   timedelta(hours=duration_value),
            "days":    timedelta(days=duration_value),
            "weeks":   timedelta(weeks=duration_value),
            "months":  timedelta(days=duration_value * 30),
            "years":   timedelta(days=duration_value * 365),
        }
        delta = deltas.get(duration_unit)
        if delta:
            expires_at = (datetime.now() + delta).isoformat()

    # Нормализуем: для публичных — без @; для приватных — числовой ID
    stored_ref = check["chat_ref"].lstrip("@") if not check.get("is_private") else check["chat_ref"]
    invite = (channel_invite_link or "").strip() or None

    entry = await db.add_required_sub_channel(
        title=title,
        channel_ref=stored_ref,
        expires_at=expires_at,
        channel_title=check.get("chat_title"),
        channel_invite_link=invite,
    )
    await db.log_action(admin["user_id"], "required_sub_added",
                        f"#{entry['id']} {title} → {stored_ref}")
    return {"success": True, "channel": entry}


@app.post("/api/settings/required-sub-channels/{channel_id}/update")
async def update_required_sub_channel_api(
    channel_id: int,
    request: Request,
    title: str = Form(...),
    channel_ref: str = Form(...),
    duration_value: int = Form(0),
    duration_unit: str = Form("forever"),
    channel_invite_link: Optional[str] = Form(None),
):
    """Обновить обязательную подписку (с повторной проверкой бота в канале)."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    title = title.strip()
    channel_ref = channel_ref.strip()
    if not title or not channel_ref:
        raise HTTPException(status_code=400, detail="Заполните все поля")

    # Проверяем, что бот в канале
    check = await _bot_channel_check(channel_ref)
    if not check["ok"]:
        raise HTTPException(status_code=400, detail=check["error"])

    # Вычисляем expires_at
    expires_at: Optional[str] = None
    if duration_value > 0 and duration_unit != "forever":
        from datetime import timedelta
        deltas = {
            "seconds": timedelta(seconds=duration_value),
            "minutes": timedelta(minutes=duration_value),
            "hours":   timedelta(hours=duration_value),
            "days":    timedelta(days=duration_value),
            "weeks":   timedelta(weeks=duration_value),
            "months":  timedelta(days=duration_value * 30),
            "years":   timedelta(days=duration_value * 365),
        }
        delta = deltas.get(duration_unit)
        if delta:
            expires_at = (datetime.now() + delta).isoformat()

    stored_ref = check["chat_ref"].lstrip("@") if not check.get("is_private") else check["chat_ref"]
    invite = (channel_invite_link or "").strip() or None

    entry = await db.update_required_sub_channel(
        channel_id=channel_id,
        title=title,
        channel_ref=stored_ref,
        expires_at=expires_at,
        channel_title=check.get("chat_title"),
        channel_invite_link=invite,
    )
    await db.log_action(admin["user_id"], "required_sub_updated",
                        f"#{channel_id} {title} → {stored_ref}")
    return {"success": True, "channel": entry}


@app.post("/api/settings/required-sub-channels/{channel_id}/delete")
async def delete_required_sub_channel_api(channel_id: int, request: Request):
    """Удалить обязательную подписку"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")
    await db.delete_required_sub_channel(channel_id)
    await db.log_action(admin["user_id"], "required_sub_deleted", f"#{channel_id}")
    return {"success": True}


@app.post("/api/tasks/delete-all")
async def delete_all_tasks_api(request: Request):
    """Остановить все активные задания (перевести в историю), вернув
    рекламодателям неизрасходованную часть бюджета — так же, как при
    удалении одного задания, просто для всех сразу."""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from aiogram import Bot
    active_tasks = await db.get_active_tasks()
    refund_bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
    total_refund = 0.0
    total_refund_stars_usd = 0.0
    try:
        for task in active_tasks:
            result = await db.delete_task_and_refund(task.task_id, bot=refund_bot)
            total_refund += result.get("refund_amount", 0.0)
            total_refund_stars_usd += result.get("refund_stars_usd", 0.0)
    finally:
        if refund_bot:
            await refund_bot.session.close()

    await db.log_action(
        admin["user_id"], "all_tasks_deleted",
        f"Stopped {len(active_tasks)} tasks, refund_balance=${total_refund:.2f}, "
        f"refund_stars=${total_refund_stars_usd:.2f}",
    )
    return {"success": True, "stopped": len(active_tasks)}


@app.get("/admin/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    """Страница статистики"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        return RedirectResponse(url="/", status_code=302)
    if not admin.get("user"):
        class TempUser:
            def __init__(self, uid):
                self.user_id = uid
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "admin": admin["user"],
    })


@app.get("/api/stats")
async def get_stats(request: Request):
    """Сводная статистика для дашборда"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # --- Пользователи ---
        row = await (await conn.execute("SELECT COUNT(*) FROM users")).fetchone()
        total_users = row[0]

        row = await (await conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_blocked = 1"
        )).fetchone()
        blocked_users = row[0]

        row = await (await conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'Admin'"
        )).fetchone()
        admin_users = row[0]

        # Новые за сегодня
        row = await (await conn.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(created_at) = DATE('now')"
        )).fetchone()
        new_today = row[0]

        # Новые за 7 дней
        row = await (await conn.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(created_at) >= DATE('now', '-6 days')"
        )).fetchone()
        new_week = row[0]

        # Новые за 30 дней
        row = await (await conn.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(created_at) >= DATE('now', '-29 days')"
        )).fetchone()
        new_month = row[0]

        # Регистрации по дням (последние 30 дней)
        rows = await (await conn.execute("""
            SELECT DATE(created_at) as day, COUNT(*) as cnt
            FROM users
            WHERE DATE(created_at) >= DATE('now', '-29 days')
            GROUP BY day ORDER BY day
        """)).fetchall()
        reg_by_day = {r["day"]: r["cnt"] for r in rows}

        # --- Задания ---
        row = await (await conn.execute("SELECT COUNT(*) FROM tasks")).fetchone()
        total_tasks = row[0]

        row = await (await conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE is_active = 1"
        )).fetchone()
        active_tasks = row[0]

        row = await (await conn.execute(
            "SELECT COALESCE(SUM(reward_amount), 0) FROM tasks WHERE is_active = 1"
        )).fetchone()
        active_tasks_budget = float(row[0])

        # Выполнено заданий по дням (последние 30 дней)
        rows = await (await conn.execute("""
            SELECT DATE(completed_at) as day, COUNT(*) as cnt
            FROM user_tasks
            WHERE DATE(completed_at) >= DATE('now', '-29 days')
            GROUP BY day ORDER BY day
        """)).fetchall()
        tasks_by_day = {r["day"]: r["cnt"] for r in rows}

        # Всего выполнений
        row = await (await conn.execute("SELECT COUNT(*) FROM user_tasks")).fetchone()
        total_completions = row[0]

        # --- Выводы ---
        row = await (await conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM withdrawal_requests WHERE status='pending'"
        )).fetchone()
        pending_withdrawals_count = row[0]
        pending_withdrawals_amount = float(row[1])

        row = await (await conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM withdrawal_requests WHERE status='accepted'"
        )).fetchone()
        paid_withdrawals_count = row[0]
        paid_withdrawals_amount = float(row[1])

        # --- Балансы ---
        row = await (await conn.execute(
            "SELECT COALESCE(SUM(verified_balance),0), COALESCE(SUM(unverified_balance),0) FROM users"
        )).fetchone()
        total_verified = float(row[0])
        total_unverified = float(row[1])

        # --- Топ каналы по выполнениям ---
        rows = await (await conn.execute("""
            SELECT t.channel_username, COUNT(ut.id) as cnt
            FROM tasks t
            LEFT JOIN user_tasks ut ON t.task_id = ut.task_id
            GROUP BY t.task_id
            ORDER BY cnt DESC LIMIT 5
        """)).fetchall()
        top_channels = [{"channel": r["channel_username"], "completions": r["cnt"]} for r in rows]

    # Активные пользователи по дням (уникальные, выполнившие хотя бы 1 задание)
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (await conn.execute("""
            SELECT DATE(completed_at) as day, COUNT(DISTINCT user_id) as cnt
            FROM user_tasks
            WHERE DATE(completed_at) >= DATE('now', '-29 days')
            GROUP BY day ORDER BY day
        """)).fetchall()
        active_by_day = {r["day"]: r["cnt"] for r in rows}

        # Базовое число пользователей до начала 30-дневного окна
        row = await (await conn.execute("""
            SELECT COUNT(*) FROM users
            WHERE DATE(created_at) < DATE('now', '-29 days')
        """)).fetchone()
        base_users = row[0]

    # Формируем массивы для графиков (последние 30 дней)
    from datetime import timedelta, date
    today = date.today()
    days_labels = [(today - timedelta(days=29 - i)).isoformat() for i in range(30)]
    reg_series = [reg_by_day.get(d, 0) for d in days_labels]
    tasks_series = [tasks_by_day.get(d, 0) for d in days_labels]
    active_series = [active_by_day.get(d, 0) for d in days_labels]

    # Кумулятивный рост: base + накопленная сумма регистраций
    cumulative_series = []
    running = base_users
    for cnt in reg_series:
        running += cnt
        cumulative_series.append(running)

    return {
        "users": {
            "total": total_users,
            "blocked": blocked_users,
            "admins": admin_users,
            "new_today": new_today,
            "new_week": new_week,
            "new_month": new_month,
        },
        "tasks": {
            "total": total_tasks,
            "active": active_tasks,
            "active_budget": active_tasks_budget,
            "total_completions": total_completions,
        },
        "withdrawals": {
            "pending_count": pending_withdrawals_count,
            "pending_amount": pending_withdrawals_amount,
            "paid_count": paid_withdrawals_count,
            "paid_amount": paid_withdrawals_amount,
        },
        "balances": {
            "total_verified": total_verified,
            "total_unverified": total_unverified,
        },
        "top_channels": top_channels,
        "charts": {
            "labels": days_labels,
            "registrations": reg_series,
            "task_completions": tasks_series,
            "active_users": active_series,
            "cumulative_users": cumulative_series,
        },
    }


@app.get("/api/stats/chart")
async def get_chart_data(request: Request, chart_type: str, period: str = "month"):
    """Данные одного графика за выбранный период.
    chart_type: registrations | cumulative | active_users | task_completions
    period:     day | week | month | all
    """
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH
    from datetime import timedelta, date as dt_date

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # ── helpers ──────────────────────────────────────────────
        def fill_days(rows, days: int):
            today = dt_date.today()
            day_list = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
            by = {r["lbl"]: r["cnt"] for r in rows}
            return day_list, [by.get(d, 0) for d in day_list]

        def fill_hours(rows):
            hours = [f"{h:02d}:00" for h in range(24)]
            by = {f"{int(r['lbl']):02d}:00": r["cnt"] for r in rows}
            return hours, [by.get(h, 0) for h in hours]

        def fill_months(rows, n: int = 12):
            """Last n months as YYYY-MM labels with zero-fill."""
            today = dt_date.today()
            month_list = []
            for i in range(n - 1, -1, -1):
                m = today.month - i
                y = today.year
                while m <= 0:
                    m += 12
                    y -= 1
                month_list.append(f"{y}-{m:02d}")
            by = {r["lbl"]: r["cnt"] for r in rows}
            return month_list, [by.get(m, 0) for m in month_list]

        # ── REGISTRATIONS ─────────────────────────────────────────
        if chart_type == "registrations":
            if period == "all":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE created_at IS NOT NULL
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels = [r["lbl"] for r in rows]
                data   = [r["cnt"] for r in rows]
            elif period == "year":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE created_at >= DATE('now', '-12 months')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_months(rows, 12)
            elif period in ("month", "week"):
                days = 30 if period == "month" else 7
                rows = await (await conn.execute(f"""
                    SELECT DATE(created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE DATE(created_at) >= DATE('now', '-{days-1} days')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_days(rows, days)
            else:  # day
                rows = await (await conn.execute("""
                    SELECT strftime('%H', created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE DATE(created_at) = DATE('now')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_hours(rows)
            return {"labels": labels, "data": data}

        # ── CUMULATIVE (Рост аудитории) ────────────────────────────
        if chart_type == "cumulative":
            if period == "all":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE created_at IS NOT NULL
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels   = [r["lbl"] for r in rows]
                new_vals = [r["cnt"] for r in rows]
                base     = 0
            elif period == "year":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE created_at >= DATE('now', '-12 months')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, new_vals = fill_months(rows, 12)
                row = await (await conn.execute("""
                    SELECT COUNT(*) FROM users
                    WHERE DATE(created_at) < DATE('now', '-12 months')
                """)).fetchone()
                base = row[0]
            elif period in ("month", "week"):
                days = 30 if period == "month" else 7
                rows = await (await conn.execute(f"""
                    SELECT DATE(created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE DATE(created_at) >= DATE('now', '-{days-1} days')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, new_vals = fill_days(rows, days)
                row = await (await conn.execute(f"""
                    SELECT COUNT(*) FROM users
                    WHERE DATE(created_at) < DATE('now', '-{days-1} days')
                """)).fetchone()
                base = row[0]
            else:  # day
                rows = await (await conn.execute("""
                    SELECT strftime('%H', created_at) as lbl, COUNT(*) as cnt
                    FROM users WHERE DATE(created_at) = DATE('now')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, new_vals = fill_hours(rows)
                row = await (await conn.execute("""
                    SELECT COUNT(*) FROM users WHERE DATE(created_at) < DATE('now')
                """)).fetchone()
                base = row[0]
            running = base
            cumulative = []
            for v in new_vals:
                running += v
                cumulative.append(running)
            return {"labels": labels, "data": cumulative}

        # ── ACTIVE USERS ──────────────────────────────────────────
        if chart_type == "active_users":
            if period == "all":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', completed_at) as lbl, COUNT(DISTINCT user_id) as cnt
                    FROM user_tasks WHERE completed_at IS NOT NULL
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels = [r["lbl"] for r in rows]
                data   = [r["cnt"] for r in rows]
            elif period == "year":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', completed_at) as lbl, COUNT(DISTINCT user_id) as cnt
                    FROM user_tasks WHERE completed_at >= DATE('now', '-12 months')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_months(rows, 12)
            elif period in ("month", "week"):
                days = 30 if period == "month" else 7
                rows = await (await conn.execute(f"""
                    SELECT DATE(completed_at) as lbl, COUNT(DISTINCT user_id) as cnt
                    FROM user_tasks WHERE DATE(completed_at) >= DATE('now', '-{days-1} days')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_days(rows, days)
            else:  # day
                rows = await (await conn.execute("""
                    SELECT strftime('%H', completed_at) as lbl, COUNT(DISTINCT user_id) as cnt
                    FROM user_tasks WHERE DATE(completed_at) = DATE('now')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_hours(rows)
            return {"labels": labels, "data": data}

        # ── TASK COMPLETIONS ──────────────────────────────────────
        if chart_type == "task_completions":
            if period == "all":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', completed_at) as lbl, COUNT(*) as cnt
                    FROM user_tasks WHERE completed_at IS NOT NULL
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels = [r["lbl"] for r in rows]
                data   = [r["cnt"] for r in rows]
            elif period == "year":
                rows = await (await conn.execute("""
                    SELECT strftime('%Y-%m', completed_at) as lbl, COUNT(*) as cnt
                    FROM user_tasks WHERE completed_at >= DATE('now', '-12 months')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_months(rows, 12)
            elif period in ("month", "week"):
                days = 30 if period == "month" else 7
                rows = await (await conn.execute(f"""
                    SELECT DATE(completed_at) as lbl, COUNT(*) as cnt
                    FROM user_tasks WHERE DATE(completed_at) >= DATE('now', '-{days-1} days')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_days(rows, days)
            else:  # day
                rows = await (await conn.execute("""
                    SELECT strftime('%H', completed_at) as lbl, COUNT(*) as cnt
                    FROM user_tasks WHERE DATE(completed_at) = DATE('now')
                    GROUP BY lbl ORDER BY lbl
                """)).fetchall()
                labels, data = fill_hours(rows)
            return {"labels": labels, "data": data}

    return {"labels": [], "data": []}


@app.get("/admin/appeals", response_class=HTMLResponse)
async def appeals_page(request: Request):
    """Страница апелляций заблокированных пользователей"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        return RedirectResponse(url="/", status_code=302)
    if not admin.get("user"):
        class TempUser:
            def __init__(self, uid):
                self.user_id = uid
                self.first_name = "Администратор"
                self.username = None
        admin["user"] = TempUser(admin["user_id"])
    return templates.TemplateResponse("appeals.html", {
        "request": request,
        "admin": admin["user"],
    })


@app.get("/api/appeals")
async def get_appeals(request: Request):
    """Список апелляций от заблокированных пользователей"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH
    result = []
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (await conn.execute("""
            SELECT t.ticket_id, t.user_id, t.status, t.created_at,
                   u.first_name, u.username, u.phone, u.is_blocked
            FROM support_tickets t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.reason = 'block_appeal'
            ORDER BY t.created_at DESC
        """)).fetchall()
        for r in rows:
            # Последнее сообщение пользователя в этом тикете
            msg_row = await (await conn.execute("""
                SELECT message_text, created_at FROM chat_messages
                WHERE support_ticket_id = ? AND is_from_admin = 0
                ORDER BY created_at DESC LIMIT 1
            """, (r["ticket_id"],))).fetchone()
            result.append({
                "ticket_id": r["ticket_id"],
                "user_id": r["user_id"],
                "first_name": r["first_name"] or "Неизвестно",
                "username": r["username"],
                "phone": r["phone"],
                "is_blocked": bool(r["is_blocked"]),
                "status": r["status"],
                "created_at": r["created_at"],
                "last_message": msg_row["message_text"] if msg_row else None,
            })
    return {"appeals": result}


@app.post("/api/appeals/{ticket_id}/unblock")
async def appeal_unblock(ticket_id: int, request: Request):
    """Одобрить апелляцию — разблокировать пользователя и закрыть тикет"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as conn:
        row = await (await conn.execute(
            "SELECT user_id FROM support_tickets WHERE ticket_id = ? AND reason = 'block_appeal'",
            (ticket_id,)
        )).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Апелляция не найдена")
        user_id = row[0]
        await conn.execute(
            "UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,)
        )
        await conn.execute(
            "UPDATE support_tickets SET status = 'closed' WHERE ticket_id = ?", (ticket_id,)
        )
        await conn.commit()

    await db.log_action(admin["user_id"], "appeal_approved", f"Ticket #{ticket_id}, User {user_id}")

    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                user_id,
                "✅ Ваша апелляция одобрена — аккаунт разблокирован!\n\n"
                "Добро пожаловать обратно. Нажмите /start чтобы продолжить.",
            )
            await bot.session.close()
    except Exception:
        pass

    return {"success": True}


@app.post("/api/appeals/{ticket_id}/reject")
async def appeal_reject(ticket_id: int, request: Request):
    """Отклонить апелляцию — закрыть тикет, пользователь остаётся заблокирован"""
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as conn:
        row = await (await conn.execute(
            "SELECT user_id FROM support_tickets WHERE ticket_id = ? AND reason = 'block_appeal'",
            (ticket_id,)
        )).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Апелляция не найдена")
        user_id = row[0]
        await conn.execute(
            "UPDATE support_tickets SET status = 'closed' WHERE ticket_id = ?", (ticket_id,)
        )
        await conn.commit()

    await db.log_action(admin["user_id"], "appeal_rejected", f"Ticket #{ticket_id}, User {user_id}")

    try:
        from aiogram import Bot
        if BOT_TOKEN:
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                user_id,
                "❌ Ваша апелляция отклонена.\n\n"
                "Аккаунт остаётся заблокированным. "
                "Если считаете это ошибкой, обратитесь повторно.",
            )
            await bot.session.close()
    except Exception:
        pass

    return {"success": True}


@app.post("/api/users/restore-referrals")
async def restore_referrals_api(request: Request):
    """Восстановить потерянные рефералы из логов.
    Находит пользователей, которые пришли по реферальной ссылке (есть запись в логах),
    но у которых referred_by не был проставлен из-за бага.
    """
    admin = getattr(request.state, "admin", None)
    if not admin:
        raise HTTPException(status_code=401, detail="Not authenticated")

    import aiosqlite
    from config import DB_PATH, REFERRAL_BONUS

    restored = []
    skipped = []

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # Шаг 1: находим всех пользователей без referred_by, у кого в логах есть реферальный код.
        # Берём ПЕРВУЮ запись start_command с кодом для каждого user_id.
        rows = await (await conn.execute("""
            SELECT l.user_id,
                   REPLACE(l.details, 'New user, referral: ', '') AS ref_code,
                   MIN(l.created_at) AS first_seen
            FROM logs l
            JOIN users u ON l.user_id = u.user_id
            WHERE l.action = 'start_command'
              AND l.details NOT LIKE '%referral: None%'
              AND l.details LIKE '%referral: %'
              AND u.referred_by IS NULL
            GROUP BY l.user_id
        """)).fetchall()

        for row in rows:
            user_id = row["user_id"]
            ref_code = row["ref_code"].strip() if row["ref_code"] else None
            if not ref_code or ref_code == "None":
                skipped.append({"user_id": user_id, "reason": "empty_code"})
                continue

            # Шаг 2: находим владельца реферального кода
            ref_owner = await (await conn.execute(
                "SELECT user_id, first_name, username FROM users WHERE referral_code = ?",
                (ref_code,)
            )).fetchone()

            if not ref_owner:
                skipped.append({"user_id": user_id, "reason": "referrer_not_found", "code": ref_code})
                continue

            referrer_id = ref_owner["user_id"]
            if referrer_id == user_id:
                skipped.append({"user_id": user_id, "reason": "self_referral"})
                continue

            # Шаг 3: проставляем referred_by и увеличиваем счётчик реферера
            cursor = await conn.execute(
                "UPDATE users SET referred_by = ? WHERE user_id = ? AND referred_by IS NULL",
                (referrer_id, user_id),
            )
            if cursor.rowcount == 0:
                skipped.append({"user_id": user_id, "reason": "already_set"})
                continue

            await conn.execute(
                "UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?",
                (referrer_id,),
            )

            # Начисляем бонус рефереру (если настроен)
            if REFERRAL_BONUS > 0:
                await conn.execute(
                    "UPDATE users SET verified_balance = verified_balance + ? WHERE user_id = ?",
                    (REFERRAL_BONUS, referrer_id),
                )

            await conn.execute(
                "INSERT INTO logs (user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                (user_id, "referral_restored_admin",
                 f"Referred by {referrer_id} (restored from logs by admin {admin['user_id']})",
                 datetime.now().isoformat()),
            )

            await conn.commit()

            restored.append({
                "user_id": user_id,
                "referrer_id": referrer_id,
                "referrer_name": ref_owner["first_name"] or ref_owner["username"] or str(referrer_id),
                "bonus_given": REFERRAL_BONUS,
            })

    await db.log_action(
        admin["user_id"], "referrals_restored",
        f"Restored: {len(restored)}, Skipped: {len(skipped)}"
    )

    return {
        "success": True,
        "restored_count": len(restored),
        "skipped_count": len(skipped),
        "restored": restored,
        "skipped": skipped,
    }


def run_web_server(host: str = "YOUR_SERVER_IP", port: int = 8000):
    """Запустить веб-сервер"""
    uvicorn.run(app, host=host, port=port)

