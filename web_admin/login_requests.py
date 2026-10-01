"""
Хранилище pending-запросов на вход в веб-панель.
Использует SQLite (тот же файл что и бот) — общий для веб-сервера и бота процессов.
"""
import uuid
import sqlite3
from datetime import datetime, timedelta
from typing import Optional
from config import DB_PATH

TTL_MINUTES = 5
MAX_REQUESTS_PER_USER = 3       # максимум pending/недавних запросов на один telegram_id
USER_WINDOW_MINUTES = 10        # за это окно времени


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


class TooManyRequests(Exception):
    """Превышен лимит попыток входа для этого telegram_id."""


def _ensure_table():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS web_login_requests (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)
        # Отозванные JWT (logout / принудительный разлогин) — позволяет
        # аннулировать конкретную сессию без смены JWT_SECRET_KEY для всех.
        con.execute("""
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti TEXT PRIMARY KEY,
                revoked_at TEXT NOT NULL
            )
        """)
        con.commit()


_ensure_table()


def revoke_jti(jti: str) -> None:
    """Отметить JWT (по его jti) как отозванный."""
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO revoked_tokens (jti, revoked_at) VALUES (?, ?)",
            (jti, datetime.utcnow().isoformat()),
        )
        con.commit()


def is_jti_revoked(jti: str) -> bool:
    if not jti:
        return False
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)
        ).fetchone()
    return row is not None


def create_request(user_id: int) -> str:
    """Создать новый pending-запрос, вернуть уникальный токен.

    Защита от спам-бомбардировки одного администратора запросами на вход
    (и от перебора telegram_id, если IP-лимит обойдён ботнетом): не больше
    MAX_REQUESTS_PER_USER запросов за USER_WINDOW_MINUTES на один user_id,
    независимо от того, с каких IP они приходят.
    """
    _cleanup()
    window_start = (datetime.utcnow() - timedelta(minutes=USER_WINDOW_MINUTES)).isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS cnt FROM web_login_requests WHERE user_id = ? AND created_at >= ?",
            (user_id, window_start),
        ).fetchone()
        if row and row["cnt"] >= MAX_REQUESTS_PER_USER:
            raise TooManyRequests()

        token = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        con.execute(
            "INSERT INTO web_login_requests (token, user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (token, user_id, now),
        )
        con.commit()
    return token


def get_request(token: str) -> Optional[dict]:
    """Получить запрос по токену (None если не найден или истёк)."""
    _cleanup()
    with _conn() as con:
        row = con.execute(
            "SELECT token, user_id, status, created_at FROM web_login_requests WHERE token = ?",
            (token,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def approve(token: str) -> bool:
    """Подтвердить вход. Возвращает True если успешно."""
    with _conn() as con:
        cur = con.execute(
            "UPDATE web_login_requests SET status = 'approved' WHERE token = ? AND status = 'pending'",
            (token,),
        )
        con.commit()
        return cur.rowcount > 0


def reject(token: str) -> bool:
    """Отклонить вход. Возвращает True если успешно."""
    with _conn() as con:
        cur = con.execute(
            "UPDATE web_login_requests SET status = 'rejected' WHERE token = ? AND status = 'pending'",
            (token,),
        )
        con.commit()
        return cur.rowcount > 0


def _cleanup():
    """Удалить просроченные записи."""
    threshold = (datetime.utcnow() - timedelta(minutes=TTL_MINUTES)).isoformat()
    with _conn() as con:
        con.execute(
            "DELETE FROM web_login_requests WHERE created_at < ?",
            (threshold,),
        )
        con.commit()
