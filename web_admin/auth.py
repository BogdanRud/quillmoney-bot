"""
Аутентификация для веб-панели
"""
import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from starlette.requests import Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import BOT_OWNERS, JWT_SECRET_KEY
from database.db import Database
from database.models import UserRole

# Настройки JWT
if not JWT_SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY не задан. Добавьте его в файл .env "
        "(см. .env.example)."
    )
if len(JWT_SECRET_KEY) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY слишком короткий (< 32 символов). "
        "Сгенерируйте новый: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )

SECRET_KEY = JWT_SECRET_KEY
ALGORITHM = "HS256"
# Короткая сессия: украденный токен живёт максимум 2 часа, а не 8.
# Можно переопределить через .env при необходимости.
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
db = Database()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Хеширование пароля"""
    return pwd_context.hash(password)


def ua_fingerprint_legacy(request: Optional[Request]) -> str:
    """Старый отпечаток — хэш полного User-Agent. Оставлен только чтобы
    не разлогинить сессии, выданные до перехода на нормализованный отпечаток."""
    if request is None:
        return ""
    ua = request.headers.get("User-Agent", "")
    return hashlib.sha256(ua.encode("utf-8", "ignore")).hexdigest()[:16]


def ua_fingerprint(request: Optional[Request]) -> str:
    """Короткий хэш User-Agent БЕЗ номеров версий — привязывает токен к
    браузеру и ОС, в которых он был выдан. Версии вырезаются, потому что
    браузеры автообновляются (Edge 152 -> 153), и с хэшем полного UA сессия
    слетала после каждого обновления. Не панацея (UA можно подделать), но
    токен, использованный из другого браузера/curl, не сработает."""
    if request is None:
        return ""
    ua = request.headers.get("User-Agent", "")
    ua = re.sub(r"\d+(?:[._]\d+)*", "", ua)
    return hashlib.sha256(ua.encode("utf-8", "ignore")).hexdigest()[:16]


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    request: Optional[Request] = None,
):
    """Создание JWT токена"""
    to_encode = data.copy()
    # Убеждаемся, что sub (subject) - строка
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "jti": uuid.uuid4().hex})
    uaf = ua_fingerprint(request)
    if uaf:
        to_encode["uaf"] = uaf
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = None
) -> dict:
    """Получить текущего администратора из токена"""
    # Пробуем получить токен из заголовка или из query параметров
    token = None
    if credentials:
        token = credentials.credentials
    else:
        # Пробуем получить из заголовка Authorization напрямую
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось подтвердить учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        # Преобразуем строку обратно в int
        user_id: int = int(user_id_str)

        from web_admin import login_requests
        if login_requests.is_jti_revoked(payload.get("jti")):
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    # Убеждаемся, что БД инициализирована
    await db.init_db()
    
    # Проверяем, что пользователь является админом
    user = await db.get_user(user_id)
    
    # Если пользователь не найден, но это владелец - разрешаем доступ
    from config import OWNER_TELEGRAM_ID
    if not user and user_id == OWNER_TELEGRAM_ID:
        # Создаем временного пользователя для веб-панели
        return {"user_id": user_id, "user": None}
    
    if not user or user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав доступа"
        )
    
    return {"user_id": user_id, "user": user}


async def verify_admin_by_telegram(user_id: int) -> bool:
    """Проверить, является ли пользователь админом по Telegram ID"""
    try:
        # Убеждаемся, что БД инициализирована
        await db.init_db()
        user = await db.get_user(user_id)
        
        # Если пользователя нет, но это владелец - разрешаем доступ
        if not user:
            from config import OWNER_TELEGRAM_ID
            if user_id == OWNER_TELEGRAM_ID:
                return True
            return False
        
        # Manager тоже пускаем в веб-панель — доступ будет ограничен
        # только CRM-разделом (см. web_admin/middleware.py).
        return user.role in (UserRole.ADMIN, UserRole.MANAGER)
    except Exception as e:
        import traceback
        print(f"Ошибка проверки админа: {e}")
        traceback.print_exc()
        # Если это владелец по ID, разрешаем доступ
        from config import OWNER_TELEGRAM_ID
        return user_id == OWNER_TELEGRAM_ID

