"""
Работа с базой данных SQLite
"""
import aiosqlite
import json
from datetime import datetime
from typing import Optional, List
from config import DB_PATH
from database.models import (
    User, UserRole, Task, UserTask, WithdrawalRequest,
    SupportTicket, ChatRequest, AdRequest, LogEntry, Contest
)


class Database:
    """Класс для работы с базой данных"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
    
    async def init_db(self):
        """Инициализация базы данных"""
        async with aiosqlite.connect(self.db_path) as db:
            # WAL вместо дефолтного rollback-journal — иначе при большом числе
            # параллельных короткоживущих подключений (каждый метод открывает
            # своё) и нескольких фоновых задачах читатели блокируют писателей
            # и наоборот, что при заметной нагрузке валится в "database is
            # locked" (так и было на проде — уже исправлено там вручную через
            # PRAGMA, здесь — чтобы настройка сохранялась и после
            # переустановки / на чистой БД). journal_mode хранится в самом
            # файле БД, так что достаточно применить один раз здесь — новые
            # подключения из других методов унаследуют его автоматически.
            await db.execute("PRAGMA journal_mode=WAL")
            await db.commit()

            # Таблица пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    phone TEXT,
                    role TEXT DEFAULT 'User',
                    unverified_balance REAL DEFAULT 0.0,
                    verified_balance REAL DEFAULT 0.0,
                    referrals_count INTEGER DEFAULT 0,
                    referral_code TEXT UNIQUE,
                    created_at TEXT
                )
            """)
            
            # Таблица заданий
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_username TEXT NOT NULL,
                    channel_id TEXT,
                    reward_amount REAL NOT NULL,
                    required_subscribers INTEGER NOT NULL,
                    current_subscribers INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT
                )
            """)
            
            # Таблица связей пользователь-задание
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    task_id INTEGER NOT NULL,
                    completed_at TEXT,
                    verified_at TEXT,
                    is_verified INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
                    UNIQUE(user_id, task_id)
                )
            """)
            
            # Реферальный код, увиденный в /start до завершения регистрации
            # (пока не пройдены спонсорские подписки). Храним в БД, а не только
            # в FSM-state (MemoryStorage), потому что state живёт только в
            # памяти процесса — перезапуск/деплой бота между /start и
            # подтверждением подписок стирает его, и реферал тихо не
            # засчитывался. БД переживает перезапуск.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending_referrals (
                    user_id INTEGER PRIMARY KEY,
                    referral_code TEXT NOT NULL,
                    created_at TEXT
                )
            """)

            # Пользователи, уже подписанные на канал в момент создания задания —
            # награда предназначена для новых подписчиков, этим она не положена.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_ineligible_users (
                    task_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (task_id, user_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Пользователи, которых мы застали НЕ подписанными при первом клике
            # по заданию — доказательство, что дальнейшая подписка произошла
            # именно ради этого задания, а не была у них до его создания.
            # Telegram Bot API не отдаёт дату вступления в чат, поэтому вместо
            # попытки угадать "когда он подписался" мы сравниваем со СВОЕЙ же
            # историей взаимодействия с заданием — это не подвержено гонке
            # с фоновым сканированием.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS task_pending_subscribers (
                    task_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (task_id, user_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Таблица заявок на вывод
            await db.execute("""
                CREATE TABLE IF NOT EXISTS withdrawal_requests (
                    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT DEFAULT 'pending',
                    admin_id INTEGER,
                    created_at TEXT,
                    posted_to_channel_at TEXT,
                    processed_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Таблица тикетов поддержки
            await db.execute("""
                CREATE TABLE IF NOT EXISTS support_tickets (
                    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    admin_id INTEGER,
                    created_at TEXT,
                    processed_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            # Таблица запросов на связь
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chat_requests (
                    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    admin_id INTEGER,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            
            # Таблица сообщений чата (для логирования переписки)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_request_id INTEGER,
                    support_ticket_id INTEGER,
                    user_id INTEGER,
                    admin_id INTEGER,
                    message_text TEXT,
                    is_from_admin INTEGER DEFAULT 0,
                    is_read_by_admin INTEGER DEFAULT 0,
                    file_id TEXT,
                    file_type TEXT,
                    created_at TEXT,
                    FOREIGN KEY (chat_request_id) REFERENCES chat_requests(request_id),
                    FOREIGN KEY (support_ticket_id) REFERENCES support_tickets(ticket_id)
                )
            """)
            
            # Таблица логов
            await db.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT
                )
            """)
            
            # Таблица настроек бота
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT,
                    updated_at TEXT
                )
            """)

            # Таблица событий каналов бота (добавлен/удалён)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_channel_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    chat_title TEXT,
                    chat_type TEXT,
                    event_type TEXT NOT NULL,
                    bot_status TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Таблица обязательных подписок
            await db.execute("""
                CREATE TABLE IF NOT EXISTS required_sub_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    channel_ref TEXT NOT NULL,
                    expires_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # Заявки на рекламу от пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ad_requests (
                    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_link TEXT NOT NULL,
                    budget REAL NOT NULL,
                    target_subscribers INTEGER NOT NULL,
                    reward_per_user REAL NOT NULL,
                    status TEXT DEFAULT 'pending',
                    admin_id INTEGER,
                    task_id INTEGER,
                    created_at TEXT,
                    payment_method TEXT DEFAULT 'manual',
                    stars_charge_id TEXT,
                    processed_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Отдельные оплаты в рамках одной заявки на рекламу (исходная + каждый
            # топ-ап доп. участников) — нужно, чтобы при удалении с возвратом
            # можно было вернуть именно звёзды через конкретный charge_id, а не
            # только конвертировать всё в баланс.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ad_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    slots INTEGER NOT NULL,
                    stars_charge_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (request_id) REFERENCES ad_requests(request_id)
                )
            """)

            # Автопост в группах (повторная отправка сообщения). Несколько
            # автопостов на одну группу разрешены — поэтому chat_id БЕЗ UNIQUE.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS group_autoposts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    from_chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    created_by INTEGER NOT NULL,
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL
                )
            """)

            # CRM для менеджеров по поиску рекламодателей: лоты (каналы/группы
            # из парсера), которые менеджер берёт в работу и закрывает с
            # результатом. extra_data — JSON со «лишними» колонками из
            # загруженной таблицы, которые не распознаны как стандартные поля.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS crm_leads (
                    lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_name TEXT,
                    channel_link TEXT,
                    subscribers INTEGER,
                    category TEXT,
                    extra_data TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    assigned_to INTEGER,
                    notes TEXT,
                    taken_at TEXT,
                    closed_at TEXT,
                    imported_by INTEGER,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    manual_position INTEGER
                )
            """)

            # Полная история действий по каждому лоту (взял/освободил/сменил статус)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS crm_lead_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (lead_id) REFERENCES crm_leads(lead_id)
                )
            """)

            # История игр на деньги (спины колеса и т.д. — задел на будущие игры)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS game_spins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game_type TEXT NOT NULL,
                    bet_amount REAL NOT NULL,
                    multiplier REAL NOT NULL,
                    payout REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Конкурсы (например «топ по приглашениям»). contest_type пока
            # только 'referral_top', заложен на будущее расширение видами.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS contests (
                    contest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contest_type TEXT NOT NULL DEFAULT 'referral_top',
                    title TEXT NOT NULL,
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_by INTEGER,
                    created_at TEXT NOT NULL
                )
            """)

            # Призовые места конкурса (место -> сумма)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS contest_prizes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contest_id INTEGER NOT NULL,
                    place INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    FOREIGN KEY (contest_id) REFERENCES contests(contest_id)
                )
            """)

            # Обязательные подписки в группах
            await db.execute("""
                CREATE TABLE IF NOT EXISTS group_mandatory_subs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER NOT NULL,
                    channel_ref TEXT NOT NULL,
                    title TEXT NOT NULL,
                    invite_link TEXT,
                    duration_minutes INTEGER DEFAULT 0,
                    expires_at TEXT,
                    is_bot_owner INTEGER DEFAULT 0,
                    added_by INTEGER,
                    created_at TEXT NOT NULL
                )
            """)

            # Сохранённые пригласительные ссылки каналов (бот — админ)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_chat_invites (
                    chat_id INTEGER PRIMARY KEY,
                    invite_link TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            await db.commit()
            await self._migrate_schema(db)

    async def _migrate_schema(self, db: aiosqlite.Connection):
        """Добавление колонок в существующую БД."""
        migrations = [
            "ALTER TABLE users ADD COLUMN referred_by INTEGER",
            "ALTER TABLE user_tasks ADD COLUMN verification_failed INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0",
            # Создаём таблицу, если БД старая и init_db её не создал
            """CREATE TABLE IF NOT EXISTS required_sub_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                channel_ref TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL
            )""",
            "ALTER TABLE tasks ADD COLUMN channel_title TEXT",
            "ALTER TABLE required_sub_channels ADD COLUMN channel_title TEXT",
            "ALTER TABLE required_sub_channels ADD COLUMN channel_invite_link TEXT",
            """CREATE TABLE IF NOT EXISTS bot_channel_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                chat_title TEXT,
                chat_type TEXT,
                event_type TEXT NOT NULL,
                bot_status TEXT,
                created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS group_autoposts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                from_chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                interval_seconds INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                next_run_at TEXT NOT NULL,
                last_run_at TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )""",
            "ALTER TABLE ad_requests ADD COLUMN payment_method TEXT DEFAULT 'manual'",
            "ALTER TABLE ad_requests ADD COLUMN stars_charge_id TEXT",
            "ALTER TABLE tasks ADD COLUMN eligibility_snapshot_done INTEGER DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN ended_at TEXT",
            # DEFAULT 'ru' — так уже существующие пользователи не переключаются
            # на английский сами по себе; 'en' по умолчанию задаётся явно только
            # для НОВЫХ регистраций в create_user().
            "ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'",
            """CREATE TABLE IF NOT EXISTS group_settings (
                chat_id INTEGER PRIMARY KEY,
                language TEXT DEFAULT 'ru',
                updated_at TEXT
            )""",
            "ALTER TABLE withdrawal_requests ADD COLUMN posted_to_channel_at TEXT",
            "ALTER TABLE chat_messages ADD COLUMN is_read_by_admin INTEGER DEFAULT 0",
            "ALTER TABLE chat_messages ADD COLUMN file_id TEXT",
            "ALTER TABLE chat_messages ADD COLUMN file_type TEXT",
            "ALTER TABLE users ADD COLUMN referral_credited INTEGER DEFAULT 0",
            "ALTER TABLE withdrawal_requests ADD COLUMN processed_at TEXT",
            "ALTER TABLE support_tickets ADD COLUMN processed_at TEXT",
            "ALTER TABLE ad_requests ADD COLUMN processed_at TEXT",
            "ALTER TABLE users ADD COLUMN rules_accepted_at TEXT",
            "ALTER TABLE users ADD COLUMN language_confirmed INTEGER DEFAULT 0",
            "ALTER TABLE crm_leads ADD COLUMN deleted_at TEXT",
            "ALTER TABLE crm_leads ADD COLUMN manual_position INTEGER",
        ]
        for sql in migrations:
            try:
                await db.execute(sql)
                await db.commit()
            except Exception:
                pass

        # Убираем UNIQUE(chat_id) из group_autoposts на старых БД — теперь
        # в одной группе разрешено несколько одновременных автопостов.
        # SQLite не даёт снять UNIQUE через ALTER, поэтому пересоздаём таблицу.
        try:
            async with db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='group_autoposts'"
            ) as cursor:
                row = await cursor.fetchone()
            if row and row[0] and "UNIQUE" in row[0]:
                await db.execute("ALTER TABLE group_autoposts RENAME TO group_autoposts_old")
                await db.execute("""
                    CREATE TABLE group_autoposts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        from_chat_id INTEGER NOT NULL,
                        message_id INTEGER NOT NULL,
                        interval_seconds INTEGER NOT NULL,
                        created_by INTEGER NOT NULL,
                        next_run_at TEXT NOT NULL,
                        last_run_at TEXT,
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL
                    )
                """)
                await db.execute("""
                    INSERT INTO group_autoposts (
                        id, chat_id, from_chat_id, message_id, interval_seconds,
                        created_by, next_run_at, last_run_at, is_active, created_at
                    )
                    SELECT id, chat_id, from_chat_id, message_id, interval_seconds,
                           created_by, next_run_at, last_run_at, is_active, created_at
                    FROM group_autoposts_old
                """)
                await db.execute("DROP TABLE group_autoposts_old")
                await db.commit()
        except Exception:
            pass

    # ========== Работа с пользователями ==========
    
    async def get_user(self, user_id: int) -> Optional[User]:
        """Получить пользователя по ID"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return User(
                        user_id=row[0],
                        username=row[1],
                        first_name=row[2],
                        phone=row[3],
                        role=UserRole(row[4]),
                        unverified_balance=row[5],
                        verified_balance=row[6],
                        referrals_count=row[7],
                        referral_code=row[8],
                        created_at=datetime.fromisoformat(row[9]) if row[9] else None,
                        referred_by=row[10] if len(row) > 10 else None,
                        is_blocked=bool(row[11]) if len(row) > 11 else False,
                        language=row[12] if len(row) > 12 and row[12] else "ru",
                        rules_accepted_at=datetime.fromisoformat(row[14]) if len(row) > 14 and row[14] else None,
                        language_confirmed=bool(row[15]) if len(row) > 15 and row[15] else False,
                    )
                return None

    async def mark_rules_accepted(self, user_id: int) -> None:
        """Отметить, что пользователь ознакомился с правилами и согласился."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET rules_accepted_at = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id),
            )
            await db.commit()

    async def set_user_language_confirmed(self, user_id: int, language: str) -> None:
        """Явный (обязательный) выбор языка при онбординге — в отличие от
        set_user_language (смена языка позже, например через /language),
        помечает язык как осознанно подтверждённый пользователем."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET language = ?, language_confirmed = 1 WHERE user_id = ?",
                (language, user_id),
            )
            await db.commit()

    async def create_user(self, user_id: int, username: Optional[str],
                         first_name: Optional[str], phone: Optional[str] = None,
                         language: str = "en") -> User:
        """Создать нового пользователя. Новые регистрации по умолчанию на
        английском (language='en') — уже существующих пользователей это не
        затрагивает, у них язык остаётся 'ru' (значение по умолчанию колонки)."""
        import secrets
        referral_code = secrets.token_urlsafe(16)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, first_name, phone, referral_code, created_at, language)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, username, first_name, phone, referral_code, datetime.now().isoformat(), language))
            await db.commit()

        return await self.get_user(user_id)

    async def set_user_language(self, user_id: int, language: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET language = ? WHERE user_id = ?", (language, user_id)
            )
            await db.commit()

    async def get_group_language(self, chat_id: int) -> str:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT language FROM group_settings WHERE chat_id = ?", (chat_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row and row[0] else "ru"

    async def set_group_language(self, chat_id: int, language: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO group_settings (chat_id, language, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET language = excluded.language, updated_at = excluded.updated_at",
                (chat_id, language, datetime.now().isoformat()),
            )
            await db.commit()
    
    async def update_user_phone(self, user_id: int, phone: str):
        """Обновить номер телефона пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET phone = ? WHERE user_id = ?",
                (phone, user_id)
            )
            await db.commit()
    
    async def update_user_role(self, user_id: int, role: UserRole):
        """Обновить роль пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET role = ? WHERE user_id = ?",
                (role.value, user_id)
            )
            await db.commit()

    async def block_user(self, user_id: int):
        """Заблокировать пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    async def unblock_user(self, user_id: int):
        """Разблокировать пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,)
            )
            await db.commit()

    async def delete_user(self, user_id: int):
        """Удалить пользователя и все его данные"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM support_tickets WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM chat_requests WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM withdrawal_requests WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM user_tasks WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM logs WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await db.commit()

    async def get_user_by_id_or_username(self, value: str) -> Optional[User]:
        """Найти пользователя по ID (если число) или username"""
        value = value.strip().lstrip("@")
        if value.isdigit():
            return await self.get_user(int(value))
        return await self.get_user_by_username(value)
    
    async def sync_user_profile(self, user_id: int, username: Optional[str], first_name: Optional[str]):
        """Обновить username/first_name, если пользователь сменил их в Telegram.

        Без этого данные в БД замораживаются на момент регистрации — поиск в
        админ-панели по текущему username не находит пользователей, которые
        сменили ник после регистрации (хотя сам user_id у них не меняется).
        Вызывается на каждое сообщение/callback, поэтому пишет в БД только
        если что-то реально изменилось.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT username, first_name FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return
            current_username, current_first_name = row
            if current_username == username and current_first_name == first_name:
                return
            await db.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (username, first_name, user_id),
            )
            await db.commit()

    async def update_user_balance(self, user_id: int, unverified: Optional[float] = None,
                                 verified: Optional[float] = None):
        """Обновить баланс пользователя"""
        updates = []
        params = []
        
        if unverified is not None:
            updates.append("unverified_balance = ?")
            params.append(unverified)
        
        if verified is not None:
            updates.append("verified_balance = ?")
            params.append(verified)
        
        if updates:
            params.append(user_id)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?",
                    params
                )
                await db.commit()

    async def update_user_referrals_count(self, user_id: int, referrals_count: int) -> None:
        """Ручная коррекция количества рефералов (например, при подтверждённой
        накрутке — вручную аннулировать засчитанных фейковых рефералов)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET referrals_count = ? WHERE user_id = ?",
                (referrals_count, user_id),
            )
            await db.commit()

    async def adjust_user_balance(self, user_id: int, unverified_delta: float = 0.0,
                                  verified_delta: float = 0.0):
        """Атомарно изменить баланс пользователя на дельту (не перезаписывая)."""
        updates = []
        params = []
        if unverified_delta != 0:
            updates.append("unverified_balance = MAX(0, unverified_balance + ?)")
            params.append(unverified_delta)
        if verified_delta != 0:
            updates.append("verified_balance = MAX(0, verified_balance + ?)")
            params.append(verified_delta)
        if updates:
            params.append(user_id)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?",
                    params,
                )
                await db.commit()

    async def increment_referrals(self, user_id: int):
        """Увеличить счетчик рефералов"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()

    async def assign_referrer(self, new_user_id: int, referrer_id: int) -> bool:
        """Привязать реферера один раз. Бонус НЕ начисляется здесь — только
        после того, как приглашённый проявит хоть какую-то активность в боте
        (см. check_and_credit_referral) — защита от накрутки фейковыми/ботовскими
        аккаунтами, которые регистрируются и больше никогда не заходят."""
        if new_user_id == referrer_id:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE users SET referred_by = ? WHERE user_id = ? AND referred_by IS NULL",
                (referrer_id, new_user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    # Действия, которые НЕ считаются "активностью" для целей начисления
    # реферального бонуса — это автоматические события самой регистрации,
    # а не осознанное использование бота приглашённым человеком.
    _REFERRAL_CREDIT_EXCLUDED_ACTIONS = (
        "user_created", "referral_registered", "start_blocked", "role_granted",
    )

    async def get_users_pending_referral_credit(self) -> List[dict]:
        """Пользователи, привязанные к рефереру, чей бонус ещё не начислен —
        ждём от них хоть какой-то активности (см. check_and_credit_referral)."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                "SELECT user_id, referred_by FROM users "
                "WHERE referred_by IS NOT NULL AND (referral_credited IS NULL OR referral_credited = 0)"
            ) as cursor:
                async for user_id, referred_by in cursor:
                    rows.append({"user_id": user_id, "referred_by": referred_by})
            return rows

    async def check_and_credit_referral(self, user_id: int) -> Optional[tuple[int, float]]:
        """Если приглашённый пользователь проявил хоть какую-то активность
        (не обязательно выполнение задания) — атомарно начислить бонус его
        рефереру. Возвращает (referrer_id, amount) при начислении, иначе None."""
        from config import REFERRAL_BONUS

        placeholders = ",".join("?" for _ in self._REFERRAL_CREDIT_EXCLUDED_ACTIONS)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT referred_by, referral_credited FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if not row or not row[0] or row[1]:
                return None
            referrer_id = row[0]

            async with db.execute(
                f"SELECT COUNT(*) FROM logs WHERE user_id = ? AND action NOT IN ({placeholders})",
                (user_id, *self._REFERRAL_CREDIT_EXCLUDED_ACTIONS),
            ) as cursor:
                has_activity = (await cursor.fetchone())[0] > 0
            if not has_activity:
                return None

            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "UPDATE users SET referral_credited = 1 WHERE user_id = ? "
                "AND (referral_credited IS NULL OR referral_credited = 0)",
                (user_id,),
            )
            if cursor.rowcount == 0:
                await db.execute("ROLLBACK")
                return None
            await db.execute(
                "UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?",
                (referrer_id,),
            )
            if REFERRAL_BONUS > 0:
                await db.execute(
                    "UPDATE users SET verified_balance = verified_balance + ? WHERE user_id = ?",
                    (REFERRAL_BONUS, referrer_id),
                )
            await db.commit()
        return (referrer_id, REFERRAL_BONUS)

    async def save_pending_referral(self, user_id: int, referral_code: str) -> None:
        """Сохранить реферальный код до завершения регистрации (переживает
        перезапуск бота, в отличие от FSM-state в памяти)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO pending_referrals (user_id, referral_code, created_at) "
                "VALUES (?, ?, ?)",
                (user_id, referral_code, datetime.now().isoformat()),
            )
            await db.commit()

    async def get_pending_referral(self, user_id: int) -> Optional[str]:
        """Получить сохранённый реферальный код (если был)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT referral_code FROM pending_referrals WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def clear_pending_referral(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM pending_referrals WHERE user_id = ?", (user_id,))
            await db.commit()

    async def get_all_pending_referrals(self) -> List[dict]:
        """Все ещё не завершённые регистрации с реферальным кодом (для фоновой
        задачи, которая довершает их, когда пользователь выполнит все условия)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT user_id, referral_code, created_at FROM pending_referrals ORDER BY created_at"
            ) as cursor:
                return [
                    {"user_id": row[0], "referral_code": row[1], "created_at": row[2]}
                    async for row in cursor
                ]

    async def get_user_by_referral_code(self, referral_code: str) -> Optional[User]:
        """Найти пользователя по реферальному коду"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE referral_code = ?", (referral_code,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return await self.get_user(row[0])
                return None
    
    async def get_user_by_username(self, username: str) -> Optional[User]:
        """Найти пользователя по username"""
        username_clean = username.lstrip("@")
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE username = ?", (username_clean,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return await self.get_user(row[0])
                return None
    
    async def get_all_users(self) -> List[User]:
        """Получить всех пользователей"""
        async with aiosqlite.connect(self.db_path) as db:
            users = []
            async with db.execute("SELECT user_id FROM users") as cursor:
                async for row in cursor:
                    user = await self.get_user(row[0])
                    if user:
                        users.append(user)
            return users
    
    # ========== Работа с заданиями ==========
    
    async def create_task(self, channel_username: str, channel_id: Optional[str],
                         reward_amount: float, required_subscribers: int,
                         channel_title: Optional[str] = None) -> Task:
        """Создать новое задание"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO tasks (channel_username, channel_id, reward_amount,
                                 required_subscribers, created_at, channel_title)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (channel_username, channel_id, reward_amount, required_subscribers,
                  datetime.now().isoformat(), channel_title))
            task_id = cursor.lastrowid
            await db.commit()

        return await self.get_task(task_id)
    
    async def increment_task_capacity(self, task_id: int, additional_subscribers: int,
                                      additional_reward_amount: float):
        """Увеличить лимит и призовой фонд задания при доплате за доп. участников
        (топ-ап по уже установленной цене за 1 место)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tasks SET required_subscribers = required_subscribers + ?, "
                "reward_amount = reward_amount + ? WHERE task_id = ?",
                (additional_subscribers, additional_reward_amount, task_id),
            )
            await db.commit()

    @staticmethod
    def _row_to_task(row) -> Task:
        """Преобразовать строку БД в объект Task."""
        return Task(
            task_id=row[0],
            channel_username=row[1],
            channel_id=row[2],
            reward_amount=row[3],
            required_subscribers=row[4],
            current_subscribers=row[5],
            is_active=bool(row[6]),
            created_at=datetime.fromisoformat(row[7]) if row[7] else None,
            channel_title=row[8] if len(row) > 8 else None,
            eligibility_snapshot_done=bool(row[9]) if len(row) > 9 else False,
            ended_at=datetime.fromisoformat(row[10]) if len(row) > 10 and row[10] else None,
        )

    async def get_task(self, task_id: int) -> Optional[Task]:
        """Получить задание по ID"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_task(row) if row else None

    async def get_active_tasks(self) -> List[Task]:
        """Получить все активные задания"""
        async with aiosqlite.connect(self.db_path) as db:
            tasks = []
            async with db.execute(
                "SELECT * FROM tasks WHERE is_active = 1"
            ) as cursor:
                async for row in cursor:
                    tasks.append(self._row_to_task(row))
            return tasks

    async def get_all_tasks(self) -> List[Task]:
        """Получить все задания (для веб-панели)"""
        async with aiosqlite.connect(self.db_path) as db:
            tasks = []
            async with db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            ) as cursor:
                async for row in cursor:
                    tasks.append(self._row_to_task(row))
            return tasks
    
    async def delete_task(self, task_id: int):
        """Остановить задание и перевести его в историю. Данные о том, кто его
        выполнил (user_tasks), НЕ удаляются — иначе историю заданий было бы
        нечем наполнять."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tasks SET is_active = 0, ended_at = ? WHERE task_id = ?",
                (datetime.now().isoformat(), task_id),
            )
            await db.commit()

    async def get_task_history(self, limit: int = 100) -> List[Task]:
        """Завершённые/остановленные задания — для «Истории заданий» в админке."""
        async with aiosqlite.connect(self.db_path) as db:
            tasks = []
            async with db.execute(
                "SELECT * FROM tasks WHERE is_active = 0 "
                "ORDER BY COALESCE(ended_at, created_at) DESC LIMIT ?",
                (limit,),
            ) as cursor:
                async for row in cursor:
                    tasks.append(self._row_to_task(row))
            return tasks

    async def get_task_completions(self, task_id: int) -> List[dict]:
        """Кто выполнил задание (для детального просмотра в истории)."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute("""
                SELECT ut.user_id, u.username, u.first_name, ut.completed_at,
                       ut.verified_at, ut.is_verified, ut.verification_failed
                FROM user_tasks ut
                LEFT JOIN users u ON u.user_id = ut.user_id
                WHERE ut.task_id = ?
                ORDER BY ut.completed_at ASC
            """, (task_id,)) as cursor:
                async for row in cursor:
                    rows.append({
                        "user_id": row[0],
                        "username": row[1],
                        "first_name": row[2],
                        "completed_at": row[3],
                        "verified_at": row[4],
                        "is_verified": bool(row[5]),
                        "verification_failed": bool(row[6]),
                    })
            return rows

    async def increment_task_subscribers(self, task_id: int):
        """Увеличить счётчик подписчиков. Задание НЕ деактивируется по
        достижению лимита — доступность новым пользователям регулируется
        отдельной проверкой current_subscribers < required_subscribers
        (см. handlers/tasks.py, get_available_tasks), а сама деактивация
        (перевод в историю) происходит только когда ВСЕ выполнения этого
        задания пройдут верификацию (см. close_fully_verified_tasks) —
        иначе слот, освободившийся из-за ранней отписки/провала проверки
        (см. fail_user_task), навсегда терялся бы для уже "заполненного",
        но фактически недоукомплектованного задания."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tasks SET current_subscribers = current_subscribers + 1 WHERE task_id = ?",
                (task_id,),
            )
            await db.commit()

    async def get_available_tasks(self) -> List[Task]:
        """Активные задания, у которых ещё реально есть свободные места —
        для показа пользователю в списке заданий (в отличие от
        get_active_tasks, который используется в админке и включает и уже
        заполненные, но ожидающие верификации задания)."""
        async with aiosqlite.connect(self.db_path) as db:
            tasks = []
            async with db.execute(
                "SELECT * FROM tasks WHERE is_active = 1 AND current_subscribers < required_subscribers"
            ) as cursor:
                async for row in cursor:
                    tasks.append(self._row_to_task(row))
            return tasks

    async def close_fully_verified_tasks(self) -> List[int]:
        """Перевести в историю задания, которые заполнены (current_subscribers
        >= required_subscribers) И у которых ВСЕ выполнения уже прошли
        верификацию (успешно или нет) — то есть больше нет висящих
        (is_verified=0 И verification_failed=0) записей. Возвращает список
        task_id закрытых заданий."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT t.task_id FROM tasks t
                WHERE t.is_active = 1
                AND t.current_subscribers >= t.required_subscribers
                AND EXISTS (SELECT 1 FROM user_tasks ut WHERE ut.task_id = t.task_id)
                AND NOT EXISTS (
                    SELECT 1 FROM user_tasks ut
                    WHERE ut.task_id = t.task_id
                    AND ut.is_verified = 0
                    AND COALESCE(ut.verification_failed, 0) = 0
                )
            """) as cursor:
                task_ids = [row[0] for row in await cursor.fetchall()]

            if task_ids:
                now = datetime.now().isoformat()
                await db.executemany(
                    "UPDATE tasks SET is_active = 0, ended_at = ? WHERE task_id = ?",
                    [(now, tid) for tid in task_ids],
                )
                await db.commit()
            return task_ids
    
    # ========== Право на задание (исключение уже подписанных) ==========

    async def mark_task_ineligible(self, task_id: int, user_id: int) -> None:
        """Отметить пользователя как не имеющего права на награду за это
        задание — он был подписан на канал ещё до создания задания."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO task_ineligible_users (task_id, user_id) VALUES (?, ?)",
                (task_id, user_id),
            )
            await db.commit()

    async def is_task_ineligible(self, task_id: int, user_id: int) -> bool:
        """Проверить, не имеет ли пользователь права на это задание."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM task_ineligible_users WHERE task_id = ? AND user_id = ?",
                (task_id, user_id),
            ) as cursor:
                return (await cursor.fetchone()) is not None

    async def mark_task_pending_subscriber(self, task_id: int, user_id: int) -> None:
        """Запомнить, что этот пользователь был замечен НЕ подписанным при
        клике по заданию — доказательство, что он ещё не был подписан до
        того, как узнал о задании."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO task_pending_subscribers (task_id, user_id) VALUES (?, ?)",
                (task_id, user_id),
            )
            await db.commit()

    async def was_task_pending_subscriber(self, task_id: int, user_id: int) -> bool:
        """Был ли этот пользователь ранее замечен НЕ подписанным на это задание."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM task_pending_subscribers WHERE task_id = ? AND user_id = ?",
                (task_id, user_id),
            ) as cursor:
                return (await cursor.fetchone()) is not None

    async def mark_eligibility_snapshot_done(self, task_id: int) -> None:
        """Отметить, что фоновая проверка уже подписанных пользователей для
        этого задания завершена — до этого момента возможна гонка, когда
        пользователь успевает выполнить задание раньше, чем его успели
        проверить и, если нужно, занести в task_ineligible_users."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE tasks SET eligibility_snapshot_done = 1 WHERE task_id = ?",
                (task_id,),
            )
            await db.commit()

    async def get_ineligible_task_ids(self, user_id: int) -> set:
        """ID заданий, недоступных этому пользователю (был подписан до их создания)."""
        async with aiosqlite.connect(self.db_path) as db:
            ids = set()
            async with db.execute(
                "SELECT task_id FROM task_ineligible_users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                async for row in cursor:
                    ids.add(row[0])
            return ids

    # ========== Работа с выполненными заданиями ==========

    async def create_user_task(self, user_id: int, task_id: int) -> UserTask:
        """Создать связь пользователя с заданием"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO user_tasks (user_id, task_id, completed_at)
                VALUES (?, ?, ?)
            """, (user_id, task_id, datetime.now().isoformat()))
            await db.commit()
        
        return UserTask(user_id, task_id)
    
    async def get_user_task(self, user_id: int, task_id: int) -> Optional[UserTask]:
        """Получить связь пользователя с заданием"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM user_tasks WHERE user_id = ? AND task_id = ?",
                (user_id, task_id)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return UserTask(
                        user_id=row[1],
                        task_id=row[2],
                        completed_at=datetime.fromisoformat(row[3]) if row[3] else None,
                        verified_at=datetime.fromisoformat(row[4]) if row[4] else None,
                        is_verified=bool(row[5]),
                        verification_failed=bool(row[6]) if len(row) > 6 else False,
                    )
                return None
    
    async def get_user_tasks_history(self, user_id: int) -> list:
        """История заданий пользователя с деталями канала."""
        async with aiosqlite.connect(self.db_path) as db:
            results = []
            async with db.execute("""
                SELECT ut.task_id, ut.completed_at, ut.is_verified, ut.verification_failed,
                       t.channel_username, t.reward_amount, t.required_subscribers
                FROM user_tasks ut
                JOIN tasks t ON ut.task_id = t.task_id
                WHERE ut.user_id = ?
                ORDER BY ut.completed_at DESC
            """, (user_id,)) as cursor:
                async for row in cursor:
                    per_user = row[5] / max(row[6], 1)
                    results.append({
                        "task_id": row[0],
                        "completed_at": row[1],
                        "is_verified": bool(row[2]),
                        "verification_failed": bool(row[3]),
                        "channel": row[4],
                        "reward": per_user,
                    })
            return results

    async def delete_failed_user_task(self, user_id: int, task_id: int) -> bool:
        """Удалить запись о проваленном задании (для повторной попытки)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """DELETE FROM user_tasks
                   WHERE user_id = ? AND task_id = ? AND verification_failed = 1""",
                (user_id, task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def verify_user_task(self, user_id: int, task_id: int):
        """Верифицировать выполнение задания (после 7 дней)"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE user_tasks 
                SET is_verified = 1, verified_at = ?
                WHERE user_id = ? AND task_id = ?
            """, (datetime.now().isoformat(), user_id, task_id))
            await db.commit()

    async def fail_user_task(self, user_id: int, task_id: int):
        """Отметить задание как проваленное (ранняя отписка либо провал
        финальной проверки) и освободить занятое им место в current_subscribers.
        Без этого проваливший проверку навсегда занимал бы слот в лимите
        задания, а при повторной попытке (см. delete_failed_user_task —
        удаляет старую запись, чтобы можно было попробовать снова) один и
        тот же человек засчитывался бы в current_subscribers дважды."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE user_tasks
                SET is_verified = 1, verification_failed = 1, verified_at = ?
                WHERE user_id = ? AND task_id = ? AND is_verified = 0
            """, (datetime.now().isoformat(), user_id, task_id))
            if cursor.rowcount > 0:
                await db.execute(
                    "UPDATE tasks SET current_subscribers = MAX(current_subscribers - 1, 0) WHERE task_id = ?",
                    (task_id,),
                )
            await db.commit()
    
    async def get_unverified_user_tasks(self) -> List[UserTask]:
        """Получить задания, ожидающие верификации (7 дней)."""
        async with aiosqlite.connect(self.db_path) as db:
            tasks = []
            async with db.execute(
                """SELECT * FROM user_tasks
                   WHERE is_verified = 0
                   AND COALESCE(verification_failed, 0) = 0"""
            ) as cursor:
                async for row in cursor:
                    tasks.append(UserTask(
                        user_id=row[1],
                        task_id=row[2],
                        completed_at=datetime.fromisoformat(row[3]) if row[3] else None,
                        verified_at=datetime.fromisoformat(row[4]) if row[4] else None,
                        is_verified=bool(row[5]),
                        verification_failed=bool(row[6]) if len(row) > 6 else False,
                    ))
            return tasks
    
    # ========== Работа с заявками на вывод ==========
    
    async def get_pending_withdrawal_sum(self, user_id: int) -> float:
        """Сумма всех pending-заявок пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """SELECT COALESCE(SUM(amount), 0) FROM withdrawal_requests
                   WHERE user_id = ? AND status = 'pending'""",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return float(row[0]) if row else 0.0

    async def create_withdrawal_request(self, user_id: int, amount: float) -> Optional[WithdrawalRequest]:
        """Создать заявку на вывод (с проверкой доступного баланса)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT verified_balance FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    await db.execute("ROLLBACK")
                    return None
                balance = float(row[0])

            async with db.execute(
                """SELECT COALESCE(SUM(amount), 0) FROM withdrawal_requests
                   WHERE user_id = ? AND status = 'pending'""",
                (user_id,),
            ) as cursor:
                pending_row = await cursor.fetchone()
                pending_sum = float(pending_row[0]) if pending_row else 0.0

            if amount + pending_sum > balance + 0.001:
                await db.execute("ROLLBACK")
                return None

            cursor = await db.execute("""
                INSERT INTO withdrawal_requests (user_id, amount, created_at)
                VALUES (?, ?, ?)
            """, (user_id, amount, datetime.now().isoformat()))
            request_id = cursor.lastrowid
            await db.commit()

        return await self.get_withdrawal_request(request_id)
    
    async def get_withdrawal_request(self, request_id: int) -> Optional[WithdrawalRequest]:
        """Получить заявку на вывод"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM withdrawal_requests WHERE request_id = ?", (request_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return WithdrawalRequest(
                        request_id=row[0],
                        user_id=row[1],
                        amount=row[2],
                        status=row[3],
                        admin_id=row[4],
                        created_at=datetime.fromisoformat(row[5]) if row[5] else None,
                        posted_to_channel_at=datetime.fromisoformat(row[6]) if len(row) > 6 and row[6] else None,
                        processed_at=datetime.fromisoformat(row[7]) if len(row) > 7 and row[7] else None,
                    )
                return None

    async def get_pending_withdrawal_for_user(self, user_id: int) -> Optional[WithdrawalRequest]:
        """Активная (pending) заявка на вывод конкретного пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM withdrawal_requests WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return WithdrawalRequest(
                        request_id=row[0], user_id=row[1], amount=row[2],
                        status=row[3], admin_id=row[4],
                        created_at=datetime.fromisoformat(row[5]) if row[5] else None,
                        posted_to_channel_at=datetime.fromisoformat(row[6]) if len(row) > 6 and row[6] else None,
                        processed_at=datetime.fromisoformat(row[7]) if len(row) > 7 and row[7] else None,
                    )
                return None

    async def get_withdrawal_history(self, limit: int = 200) -> List[WithdrawalRequest]:
        """История уже обработанных заявок на вывод (accepted/rejected).
        Сверху — недавно ОБРАБОТАННЫЕ (processed_at), а не недавно созданные."""
        async with aiosqlite.connect(self.db_path) as db:
            requests = []
            async with db.execute(
                """SELECT * FROM withdrawal_requests WHERE status != 'pending'
                   ORDER BY COALESCE(processed_at, created_at) DESC LIMIT ?""",
                (limit,),
            ) as cursor:
                async for row in cursor:
                    requests.append(WithdrawalRequest(
                        request_id=row[0],
                        user_id=row[1],
                        amount=row[2],
                        status=row[3],
                        admin_id=row[4],
                        created_at=datetime.fromisoformat(row[5]) if row[5] else None,
                        posted_to_channel_at=datetime.fromisoformat(row[6]) if len(row) > 6 and row[6] else None,
                        processed_at=datetime.fromisoformat(row[7]) if len(row) > 7 and row[7] else None,
                    ))
            return requests

    async def get_pending_withdrawal_requests(self) -> List[WithdrawalRequest]:
        """Получить все ожидающие заявки на вывод"""
        async with aiosqlite.connect(self.db_path) as db:
            requests = []
            async with db.execute(
                "SELECT * FROM withdrawal_requests WHERE status = 'pending'"
            ) as cursor:
                async for row in cursor:
                    requests.append(WithdrawalRequest(
                        request_id=row[0],
                        user_id=row[1],
                        amount=row[2],
                        status=row[3],
                        admin_id=row[4],
                        created_at=datetime.fromisoformat(row[5]) if row[5] else None,
                        posted_to_channel_at=datetime.fromisoformat(row[6]) if len(row) > 6 and row[6] else None,
                        processed_at=datetime.fromisoformat(row[7]) if len(row) > 7 and row[7] else None,
                    ))
            return requests

    async def mark_withdrawal_posted(self, request_id: int) -> None:
        """Отметить, что скриншот выплаты опубликован в канале."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE withdrawal_requests SET posted_to_channel_at = ? WHERE request_id = ?",
                (datetime.now().isoformat(), request_id),
            )
            await db.commit()
    
    async def accept_withdrawal_request(self, request_id: int, admin_id: int):
        """Принять заявку на вывод (устаревший метод — используйте accept_withdrawal_atomic)."""
        await self.accept_withdrawal_atomic(request_id, admin_id)

    async def accept_withdrawal_atomic(self, request_id: int, admin_id: int) -> tuple[bool, str]:
        """Атомарно принять заявку и списать баланс."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT user_id, amount, status FROM withdrawal_requests WHERE request_id = ?",
                (request_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row or row[2] != "pending":
                    await db.execute("ROLLBACK")
                    return False, "already_processed"

                user_id, amount, _ = row[0], float(row[1]), row[2]

            async with db.execute(
                "SELECT verified_balance FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                user_row = await cursor.fetchone()
                balance = float(user_row[0]) if user_row else 0.0

            if balance < amount - 0.001:
                await db.execute("ROLLBACK")
                return False, "insufficient_balance"

            cursor = await db.execute("""
                UPDATE withdrawal_requests
                SET status = 'accepted', admin_id = ?, processed_at = ?
                WHERE request_id = ? AND status = 'pending'
            """, (admin_id, datetime.now().isoformat(), request_id))
            if cursor.rowcount != 1:
                await db.execute("ROLLBACK")
                return False, "already_processed"

            await db.execute(
                "UPDATE users SET verified_balance = verified_balance - ? WHERE user_id = ?",
                (amount, user_id),
            )
            await db.commit()
        return True, "ok"

    async def reject_withdrawal_atomic(self, request_id: int, admin_id: int) -> tuple[bool, str]:
        """Отклонить заявку на вывод (баланс не меняется)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("""
                UPDATE withdrawal_requests
                SET status = 'rejected', admin_id = ?, processed_at = ?
                WHERE request_id = ? AND status = 'pending'
            """, (admin_id, datetime.now().isoformat(), request_id))
            if cursor.rowcount != 1:
                await db.execute("ROLLBACK")
                return False, "already_processed"
            await db.commit()
        return True, "ok"
    
    # ========== Работа с тикетами поддержки ==========
    
    async def get_user_active_support_ticket(self, user_id: int) -> Optional[SupportTicket]:
        """Активный тикет пользователя (open или taken)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM support_tickets
                WHERE user_id = ? AND status IN ('open', 'taken')
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return SupportTicket(
                        ticket_id=row[0],
                        user_id=row[1],
                        reason=row[2],
                        description=row[3],
                        status=row[4],
                        admin_id=row[5],
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    )
                return None

    async def create_support_ticket(self, user_id: int, reason: str,
                                    description: str) -> SupportTicket:
        """Создать тикет поддержки"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO support_tickets (user_id, reason, description, created_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, reason, description, datetime.now().isoformat()))
            ticket_id = cursor.lastrowid
            await db.commit()
        
        return await self.get_support_ticket(ticket_id)
    
    async def get_support_ticket(self, ticket_id: int) -> Optional[SupportTicket]:
        """Получить тикет поддержки"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM support_tickets WHERE ticket_id = ?", (ticket_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return SupportTicket(
                        ticket_id=row[0],
                        user_id=row[1],
                        reason=row[2],
                        description=row[3],
                        status=row[4],
                        admin_id=row[5],
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                        processed_at=datetime.fromisoformat(row[7]) if len(row) > 7 and row[7] else None,
                    )
                return None

    async def get_open_support_tickets(self) -> List[SupportTicket]:
        """Получить все открытые тикеты"""
        return await self.get_support_tickets_list(status="open")

    async def get_support_tickets_list(self, status: Optional[str] = None) -> List[SupportTicket]:
        """Список тикетов (для веб-панели). status=None — все.
        Сверху — недавно ОБРАБОТАННЫЕ (взятые/закрытые), а не недавно созданные."""
        async with aiosqlite.connect(self.db_path) as db:
            tickets = []
            if status:
                query = ("SELECT * FROM support_tickets WHERE status = ? "
                         "ORDER BY COALESCE(processed_at, created_at) DESC")
                params = (status,)
            else:
                query = "SELECT * FROM support_tickets ORDER BY COALESCE(processed_at, created_at) DESC"
                params = ()
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    tickets.append(SupportTicket(
                        ticket_id=row[0],
                        user_id=row[1],
                        reason=row[2],
                        description=row[3],
                        status=row[4],
                        admin_id=row[5],
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                        processed_at=datetime.fromisoformat(row[7]) if len(row) > 7 and row[7] else None,
                    ))
            return tickets

    async def take_support_ticket(self, ticket_id: int, admin_id: int) -> bool:
        """Взять тикет в работу. Возвращает True если тикет успешно взят, False если уже занят."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                UPDATE support_tickets
                SET status = 'taken', admin_id = ?, processed_at = ?
                WHERE ticket_id = ? AND status = 'open'
            """, (admin_id, datetime.now().isoformat(), ticket_id))
            await db.commit()
            return cursor.rowcount > 0

    async def close_support_ticket(self, ticket_id: int, admin_id: int = None):
        """Закрыть тикет"""
        async with aiosqlite.connect(self.db_path) as db:
            if admin_id:
                await db.execute(
                    "UPDATE support_tickets SET status = 'closed', admin_id = ?, processed_at = ? WHERE ticket_id = ?",
                    (admin_id, datetime.now().isoformat(), ticket_id)
                )
            else:
                await db.execute(
                    "UPDATE support_tickets SET status = 'closed', processed_at = ? WHERE ticket_id = ?",
                    (datetime.now().isoformat(), ticket_id)
                )
            await db.commit()

    async def get_open_tickets_bot(self, exclude_reason: str = "block_appeal", limit: int = 15) -> List[SupportTicket]:
        """Открытые тикеты для отображения в боте (без апелляций)."""
        async with aiosqlite.connect(self.db_path) as db:
            tickets = []
            async with db.execute(
                """SELECT * FROM support_tickets
                   WHERE status IN ('open', 'taken') AND reason != ?
                   ORDER BY created_at DESC LIMIT ?""",
                (exclude_reason, limit)
            ) as cursor:
                async for row in cursor:
                    tickets.append(SupportTicket(
                        ticket_id=row[0], user_id=row[1], reason=row[2],
                        description=row[3], status=row[4], admin_id=row[5],
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    ))
            return tickets

    async def get_open_appeals_bot(self, limit: int = 15) -> List[SupportTicket]:
        """Открытые апелляции для отображения в боте."""
        async with aiosqlite.connect(self.db_path) as db:
            tickets = []
            async with db.execute(
                """SELECT * FROM support_tickets
                   WHERE status IN ('open', 'taken') AND reason = 'block_appeal'
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,)
            ) as cursor:
                async for row in cursor:
                    tickets.append(SupportTicket(
                        ticket_id=row[0], user_id=row[1], reason=row[2],
                        description=row[3], status=row[4], admin_id=row[5],
                        created_at=datetime.fromisoformat(row[6]) if row[6] else None,
                    ))
            return tickets
    
    # ========== Работа с запросами на связь ==========
    
    async def create_chat_request(self, user_id: int) -> ChatRequest:
        """Создать запрос на связь с админом"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO chat_requests (user_id, created_at)
                VALUES (?, ?)
            """, (user_id, datetime.now().isoformat()))
            request_id = cursor.lastrowid
            await db.commit()
        
        return await self.get_chat_request(request_id)
    
    async def get_chat_request(self, request_id: int) -> Optional[ChatRequest]:
        """Получить запрос на связь"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM chat_requests WHERE request_id = ?", (request_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return ChatRequest(
                        request_id=row[0],
                        user_id=row[1],
                        status=row[2],
                        admin_id=row[3],
                        created_at=datetime.fromisoformat(row[4]) if row[4] else None
                    )
                return None
    
    async def get_active_chat_request_for_user(self, user_id: int) -> Optional[ChatRequest]:
        """Активный запрос на связь (pending или taken)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT * FROM chat_requests
                WHERE user_id = ? AND status IN ('pending', 'taken')
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return ChatRequest(
                        request_id=row[0],
                        user_id=row[1],
                        status=row[2],
                        admin_id=row[3],
                        created_at=datetime.fromisoformat(row[4]) if row[4] else None,
                    )
                return None

    async def ensure_chat_request_for_user(self, user_id: int) -> ChatRequest:
        """Найти или создать активный запрос на связь для переписки."""
        existing = await self.get_active_chat_request_for_user(user_id)
        if existing:
            return existing
        return await self.create_chat_request(user_id)

    async def get_chat_messages_for_user(self, user_id: int) -> List[dict]:
        """Сообщения переписки с пользователем (веб-панель)."""
        async with aiosqlite.connect(self.db_path) as db:
            messages = []
            async with db.execute(
                """
                SELECT m.message_id, m.user_id, m.admin_id, m.message_text,
                       m.is_from_admin, m.created_at, m.file_id, m.file_type
                FROM chat_messages m
                LEFT JOIN chat_requests cr ON m.chat_request_id = cr.request_id
                WHERE m.user_id = ? OR cr.user_id = ?
                ORDER BY m.created_at ASC
                """,
                (user_id, user_id),
            ) as cursor:
                async for row in cursor:
                    messages.append({
                        "message_id": row[0],
                        "user_id": row[1],
                        "admin_id": row[2],
                        "text": row[3],
                        "is_from_admin": bool(row[4]),
                        "created_at": row[5],
                        "file_id": row[6] if len(row) > 6 else None,
                        "file_type": row[7] if len(row) > 7 else None,
                    })
            return messages
    
    async def take_chat_request(self, request_id: int, admin_id: int):
        """Взять запрос на связь в работу"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE chat_requests 
                SET status = 'taken', admin_id = ?
                WHERE request_id = ? AND status = 'pending'
            """, (admin_id, request_id))
            await db.commit()
    
    async def save_chat_message(self, chat_request_id: Optional[int],
                               support_ticket_id: Optional[int],
                               user_id: Optional[int], admin_id: Optional[int],
                               message_text: str, is_from_admin: bool = False,
                               file_id: Optional[str] = None, file_type: Optional[str] = None):
        """Сохранить сообщение чата (текст и/или прикреплённый файл — фото/документ)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO chat_messages
                (chat_request_id, support_ticket_id, user_id, admin_id, message_text, is_from_admin, is_read_by_admin, file_id, file_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (chat_request_id, support_ticket_id, user_id, admin_id,
                  message_text, 1 if is_from_admin else 0, 1 if is_from_admin else 0,
                  file_id, file_type,
                  datetime.now().isoformat()))
            await db.commit()

    async def get_unread_chat_counts(self) -> dict:
        """Кол-во непрочитанных сообщений от пользователей, сгруппированное по user_id —
        для бэйджей 'непрочитано' в веб-панели (как в мессенджерах)."""
        async with aiosqlite.connect(self.db_path) as db:
            counts = {}
            async with db.execute("""
                SELECT user_id, COUNT(*) FROM chat_messages
                WHERE is_from_admin = 0 AND is_read_by_admin = 0 AND user_id IS NOT NULL
                GROUP BY user_id
            """) as cursor:
                async for user_id, count in cursor:
                    counts[user_id] = count
            return counts

    async def mark_chat_read(self, user_id: int) -> None:
        """Отметить всю переписку с пользователем прочитанной (при открытии чата в панели)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE chat_messages SET is_read_by_admin = 1 WHERE user_id = ? AND is_from_admin = 0",
                (user_id,),
            )
            await db.commit()
    
    # ========== Логирование ==========
    
    async def log_action(self, user_id: Optional[int], action: str, details: Optional[str] = None):
        """Записать действие в лог"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO logs (user_id, action, details, created_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, action, details, datetime.now().isoformat()))
            await db.commit()
    
    # ========== Работа с настройками ==========
    
    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Получить настройку"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]
                return default
    
    async def set_setting(self, key: str, value: str):
        """Установить настройку"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))
            await db.commit()
    
    async def get_required_channels(self) -> List[str]:
        """Получить список обязательных каналов для старта"""
        channels_str = await self.get_setting("required_channels", "[]")
        try:
            return json.loads(channels_str)
        except:
            return []
    
    async def set_required_channels(self, channels: List[str]):
        """Установить обязательные каналы"""
        await self.set_setting("required_channels", json.dumps(channels))

    # ========== Обязательные подписки (новая система) ==========

    @staticmethod
    def _row_to_sub(row) -> dict:
        return {
            "id": row[0],
            "title": row[1],
            "channel_ref": row[2],
            "expires_at": row[3],
            "created_at": row[4],
            "channel_title": row[5] if len(row) > 5 else None,
            "channel_invite_link": row[6] if len(row) > 6 else None,
        }

    async def get_active_required_sub_channels(self) -> List[dict]:
        """Активные (не истёкшие) обязательные подписки."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                """SELECT id, title, channel_ref, expires_at, created_at,
                          channel_title, channel_invite_link
                   FROM required_sub_channels
                   WHERE expires_at IS NULL OR expires_at > ?
                   ORDER BY created_at DESC""",
                (now,),
            ) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_sub(row))
            return rows

    async def get_all_required_sub_channels(self) -> List[dict]:
        """Все записи (включая истёкшие) — для веб-панели."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                """SELECT id, title, channel_ref, expires_at, created_at,
                          channel_title, channel_invite_link
                   FROM required_sub_channels
                   ORDER BY created_at DESC"""
            ) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_sub(row))
            return rows

    async def add_required_sub_channel(
        self,
        title: str,
        channel_ref: str,
        expires_at: Optional[str],
        channel_title: Optional[str] = None,
        channel_invite_link: Optional[str] = None,
    ) -> dict:
        """Добавить запись обязательной подписки."""
        created = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO required_sub_channels
                   (title, channel_ref, expires_at, created_at, channel_title, channel_invite_link)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (title, channel_ref, expires_at, created, channel_title, channel_invite_link),
            )
            row_id = cursor.lastrowid
            await db.commit()
        return {
            "id": row_id, "title": title, "channel_ref": channel_ref,
            "expires_at": expires_at, "created_at": created,
            "channel_title": channel_title, "channel_invite_link": channel_invite_link,
        }

    async def update_required_sub_channel(
        self,
        channel_id: int,
        title: str,
        channel_ref: str,
        expires_at: Optional[str],
        channel_title: Optional[str] = None,
        channel_invite_link: Optional[str] = None,
    ) -> dict:
        """Обновить запись обязательной подписки."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE required_sub_channels
                   SET title = ?, channel_ref = ?, expires_at = ?,
                       channel_title = ?, channel_invite_link = ?
                   WHERE id = ?""",
                (title, channel_ref, expires_at, channel_title, channel_invite_link, channel_id),
            )
            await db.commit()
        return {
            "id": channel_id, "title": title, "channel_ref": channel_ref,
            "expires_at": expires_at, "channel_title": channel_title,
            "channel_invite_link": channel_invite_link,
        }

    async def delete_required_sub_channel(self, channel_id: int):
        """Удалить запись обязательной подписки."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM required_sub_channels WHERE id = ?", (channel_id,)
            )
            await db.commit()

    # ========== Обязательные подписки в группах ==========

    @staticmethod
    def _row_to_group_mandatory_sub(row) -> Optional["GroupMandatorySub"]:
        from database.models import GroupMandatorySub
        if not row:
            return None
        return GroupMandatorySub(
            id=row[0],
            group_id=row[1],
            channel_ref=row[2],
            title=row[3],
            invite_link=row[4],
            duration_minutes=row[5] or 0,
            expires_at=row[6],
            is_bot_owner=bool(row[7]),
            added_by=row[8],
            created_at=row[9],
        )

    async def add_group_mandatory_sub(
        self,
        group_id: int,
        channel_ref: str,
        title: str,
        duration_minutes: int = 0,
        invite_link: Optional[str] = None,
        added_by: Optional[int] = None,
    ) -> Optional["GroupMandatorySub"]:
        """Добавить обязательную подписку в группу."""
        from database.models import GroupMandatorySub
        now = datetime.now().isoformat()
        expires_at = None
        if duration_minutes and duration_minutes > 0:
            from datetime import timedelta
            expires_at = (datetime.now() + timedelta(minutes=duration_minutes)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO group_mandatory_subs
                   (group_id, channel_ref, title, invite_link,
                    duration_minutes, expires_at, added_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (group_id, channel_ref, title, invite_link,
                 duration_minutes, expires_at, added_by, now),
            )
            row_id = cursor.lastrowid
            await db.commit()
        return GroupMandatorySub(
            id=row_id, group_id=group_id, channel_ref=channel_ref, title=title,
            invite_link=invite_link, duration_minutes=duration_minutes,
            expires_at=expires_at, added_by=added_by,
            created_at=now,
        )

    async def get_group_mandatory_subs(self, group_id: int) -> list["GroupMandatorySub"]:
        """Активные (не истёкшие) обязательные подписки в группе."""
        from database.models import GroupMandatorySub
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                """SELECT * FROM group_mandatory_subs
                   WHERE group_id = ?
                     AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at ASC""",
                (group_id, now),
            ) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_group_mandatory_sub(row))
            return rows

    async def get_group_mandatory_sub(self, sub_id: int) -> Optional["GroupMandatorySub"]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM group_mandatory_subs WHERE id = ?", (sub_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_group_mandatory_sub(row)

    async def remove_group_mandatory_sub(self, sub_id: int, group_id: int):
        """Удалить обязательную подписку из группы."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM group_mandatory_subs WHERE id = ? AND group_id = ?",
                (sub_id, group_id),
            )
            await db.commit()

    async def get_all_groups_with_mandatory_subs(self) -> list[int]:
        """Все ID групп, в которых есть активные обязательные подписки."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                """SELECT DISTINCT group_id FROM group_mandatory_subs
                   WHERE expires_at IS NULL OR expires_at > ?""",
                (now,),
            ) as cursor:
                async for row in cursor:
                    rows.append(row[0])
            return rows

    # ========== События каналов бота ==========

    async def add_bot_channel_event(
        self,
        chat_id: int,
        chat_title: Optional[str],
        chat_type: str,
        event_type: str,       # "added" | "removed"
        bot_status: Optional[str],
    ):
        """Записать событие (бот добавлен/удалён из канала)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO bot_channel_events
                   (chat_id, chat_title, chat_type, event_type, bot_status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chat_id, chat_title, chat_type, event_type, bot_status,
                 datetime.now().isoformat()),
            )
            await db.commit()

    async def get_bot_channel_events(self, limit: int = 100) -> List[dict]:
        """Получить последние события каналов."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                """SELECT id, chat_id, chat_title, chat_type, event_type, bot_status, created_at
                   FROM bot_channel_events
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ) as cursor:
                async for row in cursor:
                    rows.append({
                        "id": row[0],
                        "chat_id": row[1],
                        "chat_title": row[2] or str(row[1]),
                        "chat_type": row[3],
                        "event_type": row[4],
                        "bot_status": row[5],
                        "created_at": row[6],
                    })
            return rows

    async def get_active_bot_channels(self) -> List[dict]:
        """Получить каналы, где бот сейчас активен (последнее событие — added)."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute("""
                SELECT e.chat_id, e.chat_title, e.chat_type, e.bot_status, e.created_at
                FROM bot_channel_events e
                INNER JOIN (
                    SELECT chat_id, MAX(created_at) as max_ts
                    FROM bot_channel_events
                    GROUP BY chat_id
                ) latest ON e.chat_id = latest.chat_id AND e.created_at = latest.max_ts
                WHERE e.event_type = 'added'
                ORDER BY e.chat_title
            """) as cursor:
                async for row in cursor:
                    rows.append({
                        "chat_id": row[0],
                        "chat_title": row[1] or str(row[0]),
                        "chat_type": row[2],
                        "bot_status": row[3],
                        "last_event": row[4],
                    })
            return rows

    # ========== Пригласительные ссылки каналов ==========

    async def store_chat_invite_link(self, chat_id: int, invite_link: str):
        """Сохранить/обновить пригласительную ссылку канала."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO bot_chat_invites (chat_id, invite_link, updated_at) VALUES (?, ?, ?)",
                (chat_id, invite_link, datetime.now().isoformat()),
            )
            await db.commit()

    async def find_chat_by_invite_link(self, invite_link: str) -> Optional[int]:
        """Найти ID канала по пригласительной ссылке."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT chat_id FROM bot_chat_invites WHERE invite_link = ?",
                (invite_link,),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def get_all_invite_links(self) -> list[dict]:
        """Все сохранённые пригласительные ссылки."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                "SELECT chat_id, invite_link, updated_at FROM bot_chat_invites ORDER BY updated_at DESC"
            ) as cursor:
                async for row in cursor:
                    rows.append({"chat_id": row[0], "invite_link": row[1], "updated_at": row[2]})
            return rows

    # ========== Автопост в группах ==========

    async def create_group_autopost(
        self,
        chat_id: int,
        from_chat_id: int,
        message_id: int,
        interval_seconds: int,
        created_by: int,
        next_run_at: str,
    ) -> int:
        """Создать новый автопост для группы (группа может иметь несколько
        одновременно активных). Возвращает id созданной записи."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO group_autoposts (
                    chat_id, from_chat_id, message_id, interval_seconds,
                    created_by, next_run_at, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    chat_id,
                    from_chat_id,
                    message_id,
                    interval_seconds,
                    created_by,
                    next_run_at,
                    now,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_active_group_autoposts(self, chat_id: int) -> List[dict]:
        """Получить все активные автопосты группы."""
        async with aiosqlite.connect(self.db_path) as db:
            posts = []
            async with db.execute(
                """
                SELECT id, chat_id, from_chat_id, message_id, interval_seconds,
                       created_by, next_run_at, last_run_at, created_at
                FROM group_autoposts
                WHERE chat_id = ? AND is_active = 1
                ORDER BY id ASC
                """,
                (chat_id,),
            ) as cursor:
                async for row in cursor:
                    posts.append({
                        "id": row[0],
                        "chat_id": row[1],
                        "from_chat_id": row[2],
                        "message_id": row[3],
                        "interval_seconds": row[4],
                        "created_by": row[5],
                        "next_run_at": row[6],
                        "last_run_at": row[7],
                        "created_at": row[8],
                    })
            return posts

    async def get_group_autopost_by_id(self, post_id: int) -> Optional[dict]:
        """Получить автопост по id (для превью/удаления конкретной записи)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, chat_id, from_chat_id, message_id, interval_seconds,
                       created_by, next_run_at, last_run_at, created_at
                FROM group_autoposts
                WHERE id = ?
                """,
                (post_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "chat_id": row[1],
                    "from_chat_id": row[2],
                    "message_id": row[3],
                    "interval_seconds": row[4],
                    "created_by": row[5],
                    "next_run_at": row[6],
                    "last_run_at": row[7],
                    "created_at": row[8],
                }

    async def get_due_group_autoposts(self) -> List[dict]:
        """Получить автопосты, которые пора отправить."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                """
                SELECT id, chat_id, from_chat_id, message_id, interval_seconds
                FROM group_autoposts
                WHERE is_active = 1 AND next_run_at <= ?
                """,
                (now,),
            ) as cursor:
                async for row in cursor:
                    rows.append({
                        "id": row[0],
                        "chat_id": row[1],
                        "from_chat_id": row[2],
                        "message_id": row[3],
                        "interval_seconds": row[4],
                    })
            return rows

    async def update_group_autopost_schedule(
        self,
        post_id: int | None = None,
        chat_id: int | None = None,
        last_run_at: str | None = None,
        next_run_at: str | None = None,
    ) -> None:
        """Обновить расписание автопоста."""
        if post_id is None and chat_id is None:
            return
        fields = []
        values = []
        if last_run_at is not None:
            fields.append("last_run_at = ?")
            values.append(last_run_at)
        if next_run_at is not None:
            fields.append("next_run_at = ?")
            values.append(next_run_at)
        if not fields:
            return
        where = "id = ?" if post_id is not None else "chat_id = ?"
        values.append(post_id if post_id is not None else chat_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE group_autoposts SET {', '.join(fields)} WHERE {where}",
                values,
            )
            await db.commit()

    async def deactivate_group_autopost(self, chat_id: int) -> bool:
        """Остановить ВСЕ автопосты в группе (команда /автопост stop).
        Возвращает True, если хотя бы один был активен."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE group_autoposts SET is_active = 0 WHERE chat_id = ? AND is_active = 1",
                (chat_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def deactivate_group_autopost_by_id(self, post_id: int) -> bool:
        """Остановить один конкретный автопост по id (кнопка удаления в меню)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE group_autoposts SET is_active = 0 WHERE id = ? AND is_active = 1",
                (post_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    # ========== Заявки на рекламу (от пользователей) ==========

    @staticmethod
    def _row_to_ad_request(row) -> AdRequest:
        return AdRequest(
            request_id=row[0],
            user_id=row[1],
            channel_link=row[2],
            budget=row[3],
            target_subscribers=row[4],
            reward_per_user=row[5],
            status=row[6],
            admin_id=row[7],
            task_id=row[8],
            created_at=datetime.fromisoformat(row[9]) if row[9] else None,
            payment_method=row[10] if len(row) > 10 and row[10] else "manual",
            stars_charge_id=row[11] if len(row) > 11 else None,
            processed_at=datetime.fromisoformat(row[12]) if len(row) > 12 and row[12] else None,
        )

    async def create_ad_request(
        self,
        user_id: int,
        channel_link: str,
        budget: float,
        target_subscribers: int,
        reward_per_user: float,
        payment_method: str = "manual",
        stars_charge_id: Optional[str] = None,
    ) -> AdRequest:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO ad_requests
                    (user_id, channel_link, budget, target_subscribers, reward_per_user,
                     created_at, payment_method, stars_charge_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, channel_link, budget, target_subscribers, reward_per_user,
                  datetime.now().isoformat(), payment_method, stars_charge_id))
            request_id = cursor.lastrowid
            await db.commit()
        return await self.get_ad_request(request_id)

    async def get_ad_request(self, request_id: int) -> Optional[AdRequest]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM ad_requests WHERE request_id = ?", (request_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_ad_request(row) if row else None

    async def get_pending_ad_requests(self) -> List[AdRequest]:
        """Активные заявки на рекламу (ещё не приняты/отклонены).
        'taken' включена — иначе взятая в работу заявка пропадала бы из списка."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                "SELECT * FROM ad_requests WHERE status IN ('pending', 'taken') "
                "ORDER BY COALESCE(processed_at, created_at) DESC"
            ) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_ad_request(row))
            return rows

    async def get_ad_requests_history(self, limit: int = 200) -> List[AdRequest]:
        """История уже обработанных заявок на рекламу (accepted/rejected),
        сверху — недавно обработанные."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                "SELECT * FROM ad_requests WHERE status IN ('accepted', 'rejected') "
                "ORDER BY COALESCE(processed_at, created_at) DESC LIMIT ?",
                (limit,),
            ) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_ad_request(row))
            return rows

    async def record_ad_payment(self, request_id: int, method: str, amount_usd: float,
                                slots: int, stars_charge_id: Optional[str] = None):
        """Записать одну оплату в рамках заявки на рекламу (исходная или топ-ап) —
        нужно для точного возврата (в т.ч. настоящими звёздами) при удалении."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO ad_payments (request_id, method, amount_usd, slots, stars_charge_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, method, amount_usd, slots, stars_charge_id, datetime.now().isoformat()),
            )
            await db.commit()

    async def get_ad_payments(self, request_id: int) -> List[dict]:
        """Все оплаты заявки в хронологическом порядке (для FIFO-расчёта возврата)."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                "SELECT method, amount_usd, slots, stars_charge_id FROM ad_payments "
                "WHERE request_id = ? ORDER BY created_at ASC, id ASC",
                (request_id,),
            ) as cursor:
                async for row in cursor:
                    rows.append({
                        "method": row[0],
                        "amount_usd": row[1],
                        "slots": row[2],
                        "stars_charge_id": row[3],
                    })
            return rows

    async def get_ad_request_by_task_id(self, task_id: int) -> Optional[AdRequest]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM ad_requests WHERE task_id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_ad_request(row) if row else None

    async def get_user_active_ad_request(self, user_id: int) -> Optional[AdRequest]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM ad_requests WHERE user_id = ? AND status IN ('pending', 'taken') ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_ad_request(row) if row else None

    async def take_ad_request(self, request_id: int, admin_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ad_requests SET status = 'taken', admin_id = ?, processed_at = ? "
                "WHERE request_id = ? AND status = 'pending'",
                (admin_id, datetime.now().isoformat(), request_id),
            )
            await db.commit()

    async def accept_ad_request(self, request_id: int, admin_id: int, task_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ad_requests SET status = 'accepted', admin_id = ?, task_id = ?, processed_at = ? "
                "WHERE request_id = ?",
                (admin_id, task_id, datetime.now().isoformat(), request_id),
            )
            await db.commit()

    async def reject_ad_request(self, request_id: int, admin_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ad_requests SET status = 'rejected', admin_id = ?, processed_at = ? WHERE request_id = ?",
                (admin_id, datetime.now().isoformat(), request_id),
            )
            await db.commit()

    async def set_ad_request_status(self, request_id: int, status: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ad_requests SET status = ? WHERE request_id = ?",
                (status, request_id),
            )
            await db.commit()

    async def get_ad_requests_by_user(self, user_id: int, limit: int = 20,
                                      statuses: Optional[tuple] = None) -> List[AdRequest]:
        """Заявки на рекламу, оформленные конкретным пользователем (для «Моя реклама»).
        statuses — если задано, отфильтровать по статусам прямо в SQL (например,
        только незакрытые: 'pending', 'taken', 'accepted' — без отклонённых/отменённых)."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            if statuses:
                placeholders = ",".join("?" * len(statuses))
                query = (
                    f"SELECT * FROM ad_requests WHERE user_id = ? AND status IN ({placeholders}) "
                    f"ORDER BY created_at DESC LIMIT ?"
                )
                params = (user_id, *statuses, limit)
            else:
                query = "SELECT * FROM ad_requests WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"
                params = (user_id, limit)
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_ad_request(row))
            return rows

    async def increment_ad_request_capacity(self, request_id: int, additional_budget: float,
                                            additional_subscribers: int):
        """Увеличить бюджет/цель заявки при доплате за доп. участников (топ-ап)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ad_requests SET budget = budget + ?, target_subscribers = target_subscribers + ? "
                "WHERE request_id = ?",
                (additional_budget, additional_subscribers, request_id),
            )
            await db.commit()

    async def delete_task_and_refund(self, task_id: int, bot=None) -> dict:
        """Удалить задание. Если оно создано из платной заявки на рекламу (баланс/
        звёзды/оплата через администратора) и заполнено не полностью —
        возвращает пользователю неизрасходованную часть.

        Места считаются занятыми в порядке поступления оплат (FIFO): исходная
        оплата + каждый топ-ап по очереди. Если КОНКРЕТНАЯ оплата звёздами
        целиком осталась неизрасходованной — она возвращается настоящими
        звёздами через refund_star_payment (нужен bot). Если частично
        израсходована, оплачена балансом, либо bot не передан/возврат не
        удался — остаток зачисляется на баланс. Для старых заявок без записей
        в ad_payments (созданных до этой доработки) и для оплаты через
        администратора (payment_method="manual" — за неё нет ad_payments,
        деньги получил админ напрямую, а не бот) — старый способ: весь
        остаток зачисляется на баланс пользователя, чтобы он мог вывести или
        потратить его в боте. Пользователям, уже выполнившим задание, ничего
        не пересчитывается."""
        task = await self.get_task(task_id)
        if not task:
            return {"success": False, "error": "not_found"}

        ad_request = await self.get_ad_request_by_task_id(task_id)
        refund_balance = 0.0
        refund_stars_usd = 0.0
        remaining = 0
        refunded_charge_ids: List[str] = []

        if ad_request and ad_request.payment_method in ("balance", "stars", "manual") and task.required_subscribers > 0:
            filled = task.current_subscribers
            remaining = max(0, task.required_subscribers - filled)

            if remaining > 0:
                payments = await self.get_ad_payments(ad_request.request_id)
                if not payments:
                    # Заявка без учёта отдельных оплат (создана до этой доработки)
                    refund_balance = ad_request.budget * remaining / task.required_subscribers
                else:
                    consumed = 0
                    for p in payments:
                        p_start, p_end = consumed, consumed + p["slots"]
                        consumed = p_end
                        unfilled = max(0, min(p_end, task.required_subscribers) - max(p_start, filled))
                        if unfilled <= 0:
                            continue
                        per_slot = p["amount_usd"] / p["slots"] if p["slots"] else 0
                        if (unfilled == p["slots"] and p["method"] == "stars"
                                and p["stars_charge_id"] and bot is not None):
                            try:
                                await bot.refund_star_payment(
                                    user_id=ad_request.user_id,
                                    telegram_payment_charge_id=p["stars_charge_id"],
                                )
                                refunded_charge_ids.append(p["stars_charge_id"])
                                refund_stars_usd += per_slot * unfilled
                                continue
                            except Exception:
                                pass  # не удалось вернуть звёздами — вернём балансом ниже
                        refund_balance += per_slot * unfilled

                if refund_balance > 0:
                    await self.adjust_user_balance(ad_request.user_id, verified_delta=refund_balance)

        if ad_request:
            await self.set_ad_request_status(ad_request.request_id, "cancelled")

        await self.delete_task(task_id)

        return {
            "success": True,
            "task": task,
            "ad_request": ad_request,
            "refund_amount": refund_balance,       # начислено на баланс
            "refund_stars_usd": refund_stars_usd,  # возвращено настоящими звёздами (в $-эквиваленте, для сообщения)
            "refunded_charge_ids": refunded_charge_ids,
            "remaining": remaining,
        }

    # ========== Конкурсы ==========

    @staticmethod
    def _row_to_contest(row) -> Contest:
        return Contest(
            contest_id=row[0],
            contest_type=row[1],
            title=row[2],
            starts_at=datetime.fromisoformat(row[3]) if row[3] else None,
            ends_at=datetime.fromisoformat(row[4]) if row[4] else None,
            status=row[5],
            created_by=row[6],
            created_at=datetime.fromisoformat(row[7]) if row[7] else None,
        )

    async def create_contest(
        self,
        ends_at: str,
        prizes: List[tuple],
        contest_type: str = "referral_top",
        created_by: Optional[int] = None,
        title: Optional[str] = None,
    ) -> Contest:
        """Создать конкурс. prizes — список (место, сумма), например
        [(1, 10.0), (2, 5.0), (3, 2.0)]. Если title не передан — генерируется
        автоматически с порядковым номером по типу конкурса."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            if not title:
                async with db.execute(
                    "SELECT COUNT(*) FROM contests WHERE contest_type = ?", (contest_type,)
                ) as cursor:
                    number = (await cursor.fetchone())[0] + 1
                title_map = {"referral_top": f"Конкурс по приглашениям #{number}"}
                title = title_map.get(contest_type, f"Конкурс #{number}")

            cursor = await db.execute("""
                INSERT INTO contests (contest_type, title, starts_at, ends_at, status, created_by, created_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
            """, (contest_type, title, now, ends_at, created_by, now))
            contest_id = cursor.lastrowid

            await db.executemany(
                "INSERT INTO contest_prizes (contest_id, place, amount) VALUES (?, ?, ?)",
                [(contest_id, place, amount) for place, amount in prizes],
            )
            await db.commit()

        return await self.get_contest(contest_id)

    async def get_contest(self, contest_id: int) -> Optional[Contest]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM contests WHERE contest_id = ?", (contest_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_contest(row) if row else None

    async def get_active_contests(self) -> List[Contest]:
        async with aiosqlite.connect(self.db_path) as db:
            contests = []
            async with db.execute(
                "SELECT * FROM contests WHERE status = 'active' ORDER BY created_at DESC"
            ) as cursor:
                async for row in cursor:
                    contests.append(self._row_to_contest(row))
            return contests

    async def get_contest_prizes(self, contest_id: int) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                "SELECT place, amount FROM contest_prizes WHERE contest_id = ? ORDER BY place ASC",
                (contest_id,),
            ) as cursor:
                async for row in cursor:
                    rows.append({"place": row[0], "amount": row[1]})
            return rows

    async def get_contest_leaderboard(self, contest_id: int, limit: int = 20) -> List[dict]:
        """Топ по числу приглашённых (зарегистрировавшихся по реф-ссылке)
        строго в промежутке [starts_at, ends_at] конкурса — считаются сырые
        регистрации, не только уже начисленные (см. обсуждение с владельцем:
        для конкурса важна скорость и наглядность топа в реальном времени)."""
        contest = await self.get_contest(contest_id)
        if not contest:
            return []
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute("""
                SELECT u.referred_by, COUNT(*) as cnt, r.username, r.first_name
                FROM users u
                JOIN users r ON r.user_id = u.referred_by
                WHERE u.referred_by IS NOT NULL
                AND u.created_at >= ? AND u.created_at <= ?
                GROUP BY u.referred_by
                ORDER BY cnt DESC
                LIMIT ?
            """, (contest.starts_at.isoformat(), contest.ends_at.isoformat(), limit)) as cursor:
                async for row in cursor:
                    rows.append({
                        "user_id": row[0],
                        "count": row[1],
                        "username": row[2],
                        "first_name": row[3],
                    })
            return rows

    async def get_user_contest_stats(self, contest_id: int, user_id: int) -> dict:
        """Место и число приглашённых конкретного пользователя в конкурсе
        (для показа «твоя позиция», даже если он вне топа)."""
        contest = await self.get_contest(contest_id)
        if not contest:
            return {"count": 0, "rank": None}
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT COUNT(*) FROM users
                WHERE referred_by = ? AND created_at >= ? AND created_at <= ?
            """, (user_id, contest.starts_at.isoformat(), contest.ends_at.isoformat())) as cursor:
                count = (await cursor.fetchone())[0]

            if count == 0:
                return {"count": 0, "rank": None}

            async with db.execute("""
                SELECT COUNT(*) FROM (
                    SELECT referred_by, COUNT(*) as cnt
                    FROM users
                    WHERE referred_by IS NOT NULL AND created_at >= ? AND created_at <= ?
                    GROUP BY referred_by
                    HAVING cnt > ?
                )
            """, (contest.starts_at.isoformat(), contest.ends_at.isoformat(), count)) as cursor:
                better_count = (await cursor.fetchone())[0]

        return {"count": count, "rank": better_count + 1}

    async def cancel_contest(self, contest_id: int) -> None:
        """Остановить конкурс досрочно (админом) — не удаляем данные, просто
        переводим в статус 'ended', чтобы он пропал из активных."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE contests SET status = 'ended' WHERE contest_id = ?", (contest_id,)
            )
            await db.commit()

    async def close_expired_contests(self) -> List[int]:
        """Перевести в статус 'ended' конкурсы, у которых истёк срок.
        Возвращает список закрытых contest_id."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT contest_id FROM contests WHERE status = 'active' AND ends_at <= ?",
                (now,),
            ) as cursor:
                contest_ids = [row[0] for row in await cursor.fetchall()]

            if contest_ids:
                await db.executemany(
                    "UPDATE contests SET status = 'ended' WHERE contest_id = ?",
                    [(cid,) for cid in contest_ids],
                )
                await db.commit()
            return contest_ids

    # ========== Игры на деньги ==========

    async def play_wheel_spin(self, user_id: int, bet_amount: float) -> dict:
        """Атомарный спин «Колеса удачи»: списывает ставку с верифицированного
        баланса, определяет случайный множитель и сразу начисляет выигрыш.
        Финальная проверка баланса — здесь, внутри транзакции (защита от
        гонки при двойном нажатии/параллельном выводе средств)."""
        import random
        from config import WHEEL_SEGMENTS, WHEEL_WEIGHTS

        segment_index = random.SystemRandom().choices(
            range(len(WHEEL_SEGMENTS)), weights=WHEEL_WEIGHTS, k=1
        )[0]
        multiplier = WHEEL_SEGMENTS[segment_index]
        payout = round(bet_amount * multiplier, 2)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT verified_balance FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    await db.execute("ROLLBACK")
                    return {"ok": False, "reason": "user_not_found"}
                balance = float(row[0])

            if balance < bet_amount - 0.001:
                await db.execute("ROLLBACK")
                return {"ok": False, "reason": "insufficient_balance"}

            new_balance = round(balance - bet_amount + payout, 2)
            await db.execute(
                "UPDATE users SET verified_balance = ? WHERE user_id = ?",
                (new_balance, user_id),
            )
            await db.execute(
                "INSERT INTO game_spins (user_id, game_type, bet_amount, multiplier, payout, created_at) "
                "VALUES (?, 'wheel', ?, ?, ?, ?)",
                (user_id, bet_amount, multiplier, payout, datetime.now().isoformat()),
            )
            await db.commit()

        return {
            "ok": True, "multiplier": multiplier, "segment_index": segment_index,
            "payout": payout, "new_balance": new_balance,
        }

    async def get_game_spins_history(self, limit: int = 300) -> List[dict]:
        """История спинов для веб-панели — кто ставил, что выпало, сколько
        выиграл/проиграл, и текущий (актуальный) баланс игрока."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute("""
                SELECT gs.id, gs.user_id, u.username, u.first_name, gs.game_type,
                       gs.bet_amount, gs.multiplier, gs.payout, gs.created_at,
                       u.verified_balance
                FROM game_spins gs
                LEFT JOIN users u ON u.user_id = gs.user_id
                ORDER BY gs.created_at DESC
                LIMIT ?
            """, (limit,)) as cursor:
                async for row in cursor:
                    rows.append({
                        "id": row[0],
                        "user_id": row[1],
                        "username": row[2],
                        "first_name": row[3],
                        "game_type": row[4],
                        "bet_amount": row[5],
                        "multiplier": row[6],
                        "payout": row[7],
                        "profit": round(row[7] - row[5], 2),
                        "created_at": row[8],
                        "current_balance": row[9],
                    })
            return rows

    async def get_game_stats(self) -> dict:
        """Агрегированная статистика по всем спинам (для сводки в веб-панели)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT COUNT(*), COALESCE(SUM(bet_amount), 0), COALESCE(SUM(payout), 0),
                       COUNT(DISTINCT user_id)
                FROM game_spins
            """) as cursor:
                row = await cursor.fetchone()
        total_spins, total_bet, total_payout, unique_players = row
        return {
            "total_spins": total_spins,
            "total_bet": round(total_bet, 2),
            "total_payout": round(total_payout, 2),
            "house_profit": round(total_bet - total_payout, 2),
            "unique_players": unique_players,
        }

    # ========== CRM (менеджеры по поиску рекламодателей) ==========

    CRM_STATUSES = (
        "new", "in_progress", "follow_up",
        "agreed", "rejected", "no_response", "invalid", "duplicate",
    )
    CRM_TERMINAL_STATUSES = ("agreed", "rejected", "no_response", "invalid", "duplicate")

    @staticmethod
    def _row_to_crm_lead(row) -> dict:
        import json
        extra = {}
        if row[5]:
            try:
                extra = json.loads(row[5])
            except (ValueError, TypeError):
                extra = {}
        return {
            "lead_id": row[0],
            "channel_name": row[1],
            "channel_link": row[2],
            "subscribers": row[3],
            "category": row[4],
            "extra_data": extra,
            "status": row[6],
            "assigned_to": row[7],
            "notes": row[8],
            "taken_at": row[9],
            "closed_at": row[10],
            "imported_by": row[11],
            "created_at": row[12],
            "deleted_at": row[13] if len(row) > 13 else None,
            "manual_position": row[14] if len(row) > 14 else None,
        }

    @staticmethod
    def _normalize_crm_link(link: str) -> str:
        """Нормализовать ссылку на канал для сравнения при дедупликации:
        без разницы http/https, www, слэша в конце, query-параметров,
        префикса t.me/ и @ в начале юзернейма."""
        value = (link or "").strip().lower()
        if not value:
            return ""
        value = value.split("?", 1)[0]
        value = value.rstrip("/")
        for prefix in ("https://", "http://"):
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        if value.startswith("www."):
            value = value[len("www."):]
        for prefix in ("t.me/", "telegram.me/"):
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        value = value.lstrip("@")
        return value

    async def import_crm_leads(self, rows: List[dict], imported_by: int) -> dict:
        """Массовый импорт лотов из загруженной таблицы. rows — список словарей
        с ключами channel_name/channel_link/subscribers/category/extra (dict).
        Дедупликация по нормализованной channel_link (без учёта http/https,
        www, слэша в конце, @ и т.д.) — уже существующие каналы (в любом
        статусе) не добавляются повторно; дубликаты внутри одного файла
        тоже отсеиваются. Для строк без ссылки (приватные каналы/группы без
        юзернейма) используется запасной ключ дедупликации — нормализованное
        название канала, иначе такие лоты дублировались бы при каждой
        повторной загрузке той же таблицы."""
        import json

        now = datetime.now().isoformat()
        imported = 0
        skipped = 0

        async with aiosqlite.connect(self.db_path) as db:
            existing_links = set()
            existing_names_no_link = set()
            async with db.execute(
                "SELECT channel_link, channel_name FROM crm_leads"
            ) as cursor:
                async for existing_link, existing_name in cursor:
                    normalized = self._normalize_crm_link(existing_link or "")
                    if normalized:
                        existing_links.add(normalized)
                    else:
                        name_key = (existing_name or "").strip().lower()
                        if name_key:
                            existing_names_no_link.add(name_key)

            for row in rows:
                link = (row.get("channel_link") or "").strip()
                normalized_link = self._normalize_crm_link(link)
                if normalized_link:
                    if normalized_link in existing_links:
                        skipped += 1
                        continue
                    existing_links.add(normalized_link)
                else:
                    name_key = (row.get("channel_name") or "").strip().lower()
                    if name_key:
                        if name_key in existing_names_no_link:
                            skipped += 1
                            continue
                        existing_names_no_link.add(name_key)

                cursor = await db.execute("""
                    INSERT INTO crm_leads
                        (channel_name, channel_link, subscribers, category, extra_data,
                         status, imported_by, created_at)
                    VALUES (?, ?, ?, ?, ?, 'new', ?, ?)
                """, (
                    row.get("channel_name"), link or None, row.get("subscribers"),
                    row.get("category"), json.dumps(row.get("extra") or {}, ensure_ascii=False),
                    imported_by, now,
                ))
                lead_id = cursor.lastrowid
                await db.execute(
                    "INSERT INTO crm_lead_history (lead_id, user_id, action, to_status, created_at) "
                    "VALUES (?, ?, 'imported', 'new', ?)",
                    (lead_id, imported_by, now),
                )
                imported += 1

            await db.commit()

        return {"imported": imported, "skipped_duplicates": skipped}

    async def get_crm_leads(
        self, status: Optional[str] = None, assigned_to: Optional[int] = None,
        only_deleted: bool = False, limit: int = 500,
    ) -> List[dict]:
        """Список лотов с фильтрами. status=None — все статусы,
        assigned_to=None — не фильтровать по менеджеру. По умолчанию
        удалённые (deleted_at IS NOT NULL) скрыты; only_deleted=True —
        наоборот, показать только их (вкладка «Удалённые»)."""
        query = "SELECT * FROM crm_leads WHERE deleted_at IS "
        query += "NOT NULL" if only_deleted else "NULL"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if assigned_to is not None:
            query += " AND assigned_to = ?"
            params.append(assigned_to)
        query += " ORDER BY COALESCE(deleted_at, closed_at, taken_at, created_at) DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    rows.append(self._row_to_crm_lead(row))
            return rows

    async def get_crm_lead(self, lead_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM crm_leads WHERE lead_id = ?", (lead_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_crm_lead(row) if row else None

    async def take_crm_lead(self, lead_id: int, manager_id: int) -> bool:
        """Взять лот в работу — только если он ещё свободен ('new')."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("""
                UPDATE crm_leads SET status = 'in_progress', assigned_to = ?, taken_at = ?, manual_position = NULL
                WHERE lead_id = ? AND status = 'new'
            """, (manager_id, now, lead_id))
            if cursor.rowcount == 0:
                await db.execute("ROLLBACK")
                return False
            await db.execute(
                "INSERT INTO crm_lead_history (lead_id, user_id, action, from_status, to_status, created_at) "
                "VALUES (?, ?, 'taken', 'new', 'in_progress', ?)",
                (lead_id, manager_id, now),
            )
            await db.commit()
        return True

    async def release_crm_lead(self, lead_id: int, manager_id: int, is_admin_override: bool = False) -> bool:
        """Вернуть лот в общий пул (например, взял по ошибке). Admin может
        освободить любой лот, менеджер — только свой."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            lead = await self.get_crm_lead(lead_id)
            if not lead or lead["status"] not in ("in_progress", "follow_up"):
                return False
            if not is_admin_override and lead["assigned_to"] != manager_id:
                return False
            await db.execute(
                "UPDATE crm_leads SET status = 'new', assigned_to = NULL, taken_at = NULL, manual_position = NULL "
                "WHERE lead_id = ?",
                (lead_id,),
            )
            await db.execute(
                "INSERT INTO crm_lead_history (lead_id, user_id, action, from_status, to_status, created_at) "
                "VALUES (?, ?, 'released', ?, 'new', ?)",
                (lead_id, manager_id, lead["status"], now),
            )
            await db.commit()
        return True

    async def set_crm_lead_manual_order(self, user_id: int, ordered_lead_ids: List[int]) -> int:
        """Сохранить личный ручной порядок лотов менеджера (drag-and-drop во
        вкладке «Мои») — обновляет позицию только среди лотов, назначенных
        именно этому пользователю, чтобы менеджер не мог повлиять на чужие
        лоты. Возвращает количество реально обновлённых записей."""
        updated = 0
        async with aiosqlite.connect(self.db_path) as db:
            for position, lead_id in enumerate(ordered_lead_ids):
                cursor = await db.execute(
                    "UPDATE crm_leads SET manual_position = ? "
                    "WHERE lead_id = ? AND assigned_to = ? AND deleted_at IS NULL",
                    (position, lead_id, user_id),
                )
                updated += cursor.rowcount
            await db.commit()
        return updated

    async def update_crm_lead_status(
        self, lead_id: int, manager_id: int, new_status: str, note: Optional[str] = None,
        is_admin_override: bool = False,
    ) -> bool:
        """Сменить статус лота (follow_up/agreed/rejected/no_response/invalid/
        duplicate) — менеджер остаётся закреплён за лотом, лот НЕ возвращается
        в общий пул (кроме как через release_crm_lead). Admin может менять
        статус любого лота, менеджер — только своего."""
        if new_status not in self.CRM_STATUSES or new_status in ("new",):
            return False

        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            lead = await self.get_crm_lead(lead_id)
            if not lead:
                return False
            if not is_admin_override and lead["assigned_to"] != manager_id:
                return False

            closed_at = now if new_status in self.CRM_TERMINAL_STATUSES else None
            params = [new_status]
            set_clause = "status = ?"
            if closed_at:
                set_clause += ", closed_at = ?"
                params.append(closed_at)
            if note is not None:
                set_clause += ", notes = ?"
                params.append(note)
            params.append(lead_id)

            await db.execute(f"UPDATE crm_leads SET {set_clause} WHERE lead_id = ?", params)
            await db.execute(
                "INSERT INTO crm_lead_history (lead_id, user_id, action, from_status, to_status, note, created_at) "
                "VALUES (?, ?, 'status_changed', ?, ?, ?, ?)",
                (lead_id, manager_id, lead["status"], new_status, note, now),
            )
            await db.commit()
        return True

    async def delete_crm_lead(self, lead_id: int, admin_id: int) -> bool:
        """Мягкое удаление — данные и история не теряются, лот пропадает из
        обычных списков и попадает во вкладку «Удалённые»."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE crm_leads SET deleted_at = ? WHERE lead_id = ? AND deleted_at IS NULL",
                (now, lead_id),
            )
            if cursor.rowcount == 0:
                return False
            await db.execute(
                "INSERT INTO crm_lead_history (lead_id, user_id, action, created_at) "
                "VALUES (?, ?, 'deleted', ?)",
                (lead_id, admin_id, now),
            )
            await db.commit()
        return True

    async def restore_crm_lead(self, lead_id: int, admin_id: int) -> bool:
        """Восстановить удалённый лот — статус/менеджер/история остаются как были."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE crm_leads SET deleted_at = NULL WHERE lead_id = ? AND deleted_at IS NOT NULL",
                (lead_id,),
            )
            if cursor.rowcount == 0:
                return False
            await db.execute(
                "INSERT INTO crm_lead_history (lead_id, user_id, action, created_at) "
                "VALUES (?, ?, 'restored', ?)",
                (lead_id, admin_id, now),
            )
            await db.commit()
        return True

    async def bulk_delete_crm_leads(self, admin_id: int, lead_ids: List[int]) -> int:
        """Удалить лоты по явному списку ID — ID приходят с фронтенда, это
        ровно те лоты, что показаны на текущей вкладке (некоторые вкладки,
        например «Остальные закрытые», фильтруются на клиенте сразу по
        нескольким статусам, поэтому переигрывать фильтр на сервере
        отдельным запросом было бы неверно — список мог бы не совпасть)."""
        if not lead_ids:
            return 0
        now = datetime.now().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ",".join("?" for _ in lead_ids)
            async with db.execute(
                f"SELECT lead_id FROM crm_leads WHERE deleted_at IS NULL AND lead_id IN ({placeholders})",
                lead_ids,
            ) as cursor:
                lead_ids = [row[0] for row in await cursor.fetchall()]
            if not lead_ids:
                return 0
            await db.executemany(
                "UPDATE crm_leads SET deleted_at = ? WHERE lead_id = ?",
                [(now, lid) for lid in lead_ids],
            )
            await db.executemany(
                "INSERT INTO crm_lead_history (lead_id, user_id, action, created_at) VALUES (?, ?, 'deleted', ?)",
                [(lid, admin_id, now) for lid in lead_ids],
            )
            await db.commit()
        return len(lead_ids)

    async def bulk_restore_crm_leads(self, admin_id: int) -> int:
        """Восстановить все удалённые лоты (вкладка «Удалённые» → «Восстановить все»)."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT lead_id FROM crm_leads WHERE deleted_at IS NOT NULL"
            ) as cursor:
                lead_ids = [row[0] for row in await cursor.fetchall()]
            if not lead_ids:
                return 0
            await db.execute("UPDATE crm_leads SET deleted_at = NULL WHERE deleted_at IS NOT NULL")
            await db.executemany(
                "INSERT INTO crm_lead_history (lead_id, user_id, action, created_at) VALUES (?, ?, 'restored', ?)",
                [(lid, admin_id, now) for lid in lead_ids],
            )
            await db.commit()
        return len(lead_ids)

    async def get_crm_lead_history(self, lead_id: int) -> List[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute("""
                SELECT h.id, h.user_id, u.username, u.first_name, h.action,
                       h.from_status, h.to_status, h.note, h.created_at
                FROM crm_lead_history h
                LEFT JOIN users u ON u.user_id = h.user_id
                WHERE h.lead_id = ?
                ORDER BY h.created_at ASC
            """, (lead_id,)) as cursor:
                async for row in cursor:
                    rows.append({
                        "id": row[0],
                        "user_id": row[1],
                        "username": row[2],
                        "first_name": row[3],
                        "action": row[4],
                        "from_status": row[5],
                        "to_status": row[6],
                        "note": row[7],
                        "created_at": row[8],
                    })
            return rows

    async def get_crm_stats(self, assigned_to: Optional[int] = None) -> dict:
        """Статистика по неудалённым лотам (deleted_at IS NULL)."""
        query = "SELECT status, COUNT(*) FROM crm_leads WHERE deleted_at IS NULL"
        params: list = []
        if assigned_to is not None:
            query += " AND assigned_to = ?"
            params.append(assigned_to)
        query += " GROUP BY status"

        counts = {s: 0 for s in self.CRM_STATUSES}
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                async for status, count in cursor:
                    counts[status] = count
        counts["total"] = sum(counts.values())

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM crm_leads WHERE deleted_at IS NOT NULL") as cursor:
                counts["deleted"] = (await cursor.fetchone())[0]
        return counts

    async def get_crm_managers(self) -> List[dict]:
        """Список пользователей с ролью Manager — для фильтра в панели."""
        async with aiosqlite.connect(self.db_path) as db:
            rows = []
            async with db.execute(
                "SELECT user_id, username, first_name FROM users WHERE role = 'Manager'"
            ) as cursor:
                async for row in cursor:
                    rows.append({"user_id": row[0], "username": row[1], "first_name": row[2]})
            return rows

