"""
Middleware для обработки токенов из cookies
"""
import logging
import time
from collections import defaultdict, deque

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, JSONResponse
from jose import JWTError, jwt
from web_admin.auth import SECRET_KEY, ALGORITHM
from database.db import Database
from database.models import UserRole
from config import OWNER_TELEGRAM_ID, ADMIN_IP_WHITELIST

db = Database()
access_log = logging.getLogger("web_admin.access")


def _client_ip(request: Request) -> str:
    """IP клиента. За реверс-прокси нужно доверять X-Forwarded-For только если
    он проставляется самим прокси (настройте Nginx с proxy_set_header)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Простой in-memory rate limiter по IP (sliding window).

    Для /api/login лимит жёстче — это самая чувствительная ручка
    (брутфорс telegram_id, спам уведомлениями в боте).
    """

    GLOBAL_LIMIT = 60        # запросов
    GLOBAL_WINDOW = 60       # секунд
    LOGIN_LIMIT = 5
    LOGIN_WINDOW = 60

    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, deque] = defaultdict(deque)

    def _check(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    async def dispatch(self, request: Request, call_next):
        ip = _client_ip(request)

        if request.url.path == "/api/login":
            if not self._check(f"login:{ip}", self.LOGIN_LIMIT, self.LOGIN_WINDOW):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Слишком много попыток. Попробуйте позже."},
                )

        if not self._check(f"global:{ip}", self.GLOBAL_LIMIT, self.GLOBAL_WINDOW):
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много запросов. Попробуйте позже."},
            )

        return await call_next(request)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Свой лог запросов к панели: IP, метод, путь, статус, кто авторизован.

    Это то, чего не хватило при расследовании прошлого инцидента — без
    Nginx access.log и без этого мидлвара не осталось следов того, какие
    запросы реально приходили на /api/login и другие чувствительные ручки.
    """

    async def dispatch(self, request: Request, call_next):
        ip = _client_ip(request)
        try:
            response = await call_next(request)
        except Exception:
            access_log.exception(
                "EXCEPTION %s %s ip=%s", request.method, request.url.path, ip
            )
            raise

        admin = getattr(request.state, "admin", None)
        admin_id = admin["user_id"] if admin else "-"
        access_log.info(
            "%s %s status=%s ip=%s admin=%s ua=%r",
            request.method,
            request.url.path,
            response.status_code,
            ip,
            admin_id,
            request.headers.get("User-Agent", ""),
        )
        return response


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """Опциональный белый список IP для всей панели (выключен, если
    ADMIN_IP_WHITELIST пуст). Не пускает дальше, даже до страницы логина —
    злоумышленник снаружи списка не должен даже видеть, что панель существует."""

    async def dispatch(self, request: Request, call_next):
        if not ADMIN_IP_WHITELIST:
            return await call_next(request)

        if request.url.path.startswith("/static"):
            return await call_next(request)

        ip = _client_ip(request)
        if ip not in ADMIN_IP_WHITELIST:
            return JSONResponse(status_code=403, content={"detail": "Access denied"})

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Базовые security-заголовки для всех ответов."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # HSTS — браузер запомнит и сам будет требовать HTTPS для этого домена
        # на год вперёд. Безвредно по HTTP (заголовок просто игнорируется),
        # но включайте реальный эффект только когда HTTPS настроен (COOKIE_SECURE=true).
        from config import COOKIE_SECURE
        if COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Шаблоны панели используют инлайн <script>, поэтому script-src вынужденно
        # содержит 'unsafe-inline' — без этого вся админка перестанет работать.
        # Если в будущем перенести JS во внешние файлы (как static/script.js),
        # можно убрать 'unsafe-inline' и сделать CSP заметно строже.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return response


async def get_admin_from_token(token: str, request: Request = None) -> dict:
    """Получить админа из токена"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            return None
        # Преобразуем строку обратно в int
        user_id: int = int(user_id_str)

        from web_admin import login_requests
        if login_requests.is_jti_revoked(payload.get("jti")):
            return None

        # Привязка к User-Agent: если токен был выдан с зафиксированным
        # отпечатком UA, текущий запрос обязан совпадать.
        token_uaf = payload.get("uaf")
        if token_uaf and request is not None:
            from web_admin.auth import ua_fingerprint, ua_fingerprint_legacy
            if token_uaf not in (ua_fingerprint(request), ua_fingerprint_legacy(request)):
                return None

        await db.init_db()
        user = await db.get_user(user_id)
        
        # Если пользователь не найден, но это владелец - разрешаем доступ
        if not user and user_id == OWNER_TELEGRAM_ID:
            class TempUser:
                def __init__(self, user_id):
                    self.user_id = user_id
                    self.first_name = "Администратор"
                    self.username = None
            return {"user_id": user_id, "user": TempUser(user_id)}
        
        # Если пользователь найден, проверяем роль. Manager тоже допускается —
        # доступ ему ограничивается ниже, в AuthMiddleware (только /admin/crm
        # и /api/crm), а не здесь.
        if user:
            if user.role in (UserRole.ADMIN, UserRole.MANAGER):
                return {"user_id": user_id, "user": user}
            # Если не админ, но владелец - все равно разрешаем
            if user_id == OWNER_TELEGRAM_ID:
                return {"user_id": user_id, "user": user}

        return None
    except JWTError as e:
        print(f"Ошибка декодирования токена: {e}")
        return None
    except Exception as e:
        print(f"Ошибка получения админа: {e}")
        import traceback
        traceback.print_exc()
        return None


_MANAGER_ALLOWED_HTML = ("/admin/crm",)
_MANAGER_ALLOWED_API = ("/api/crm", "/api/logout", "/api/notifications/summary")


def _is_manager(admin: dict) -> bool:
    """Manager видит только CRM — проверяем роль реального User (TempUser
    владельца-фолбэка атрибута role не имеет, getattr вернёт None → False)."""
    user = admin.get("user") if admin else None
    return getattr(user, "role", None) == UserRole.MANAGER


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware для проверки авторизации"""

    async def dispatch(self, request: Request, call_next):
        # Пропускаем публичные страницы
        if request.url.path in ["/", "/api/login"] or request.url.path.startswith("/api/login/poll/"):
            return await call_next(request)
        
        # Пропускаем статические файлы
        if request.url.path.startswith("/static"):
            return await call_next(request)
        
        # Пропускаем API endpoints - они проверяют авторизацию сами
        if request.url.path.startswith("/api/"):
            # Для API получаем токен из заголовка или cookie
            token = None
            auth_header = request.headers.get("Authorization")
            used_cookie_auth = False
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
            else:
                token = request.cookies.get("admin_token")
                used_cookie_auth = token is not None

            # CSRF-защита: изменяющие запросы, авторизованные по cookie,
            # обязаны нести кастомный заголовок X-Requested-With. Сторонний
            # сайт не может выставить произвольный заголовок в простой форме,
            # а cross-origin fetch с заголовком упрётся в нашу CORS-политику.
            # Bearer-авторизация (явный заголовок) CSRF не подвержена — пропускаем.
            if (
                used_cookie_auth
                and request.method in ("POST", "PUT", "PATCH", "DELETE")
                and request.headers.get("X-Requested-With") != "XMLHttpRequest"
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF check failed"},
                )

            if token:
                admin = await get_admin_from_token(token, request)
                if admin:
                    if _is_manager(admin) and not request.url.path.startswith(_MANAGER_ALLOWED_API):
                        return JSONResponse(status_code=403, content={"detail": "Доступ ограничен разделом CRM"})
                    request.state.admin = admin

            return await call_next(request)

        # Для HTML страниц проверяем cookie
        if request.url.path.startswith("/admin"):
            token = request.cookies.get("admin_token")

            if not token:
                # Нет токена - редирект на логин
                return RedirectResponse(url="/", status_code=302)

            # Проверяем токен
            admin = await get_admin_from_token(token, request)
            if admin:
                if _is_manager(admin) and not request.url.path.startswith(_MANAGER_ALLOWED_HTML):
                    return RedirectResponse(url="/admin/crm", status_code=302)
                request.state.admin = admin
            else:
                # Токен невалидный - редирект на логин
                response = RedirectResponse(url="/", status_code=302)
                response.delete_cookie("admin_token", path="/")
                return response
        
        return await call_next(request)

