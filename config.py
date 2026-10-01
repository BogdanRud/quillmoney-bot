"""
Конфигурация бота
"""
import logging
import os
import stat
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger(__name__)


def _warn_if_env_readable_by_others() -> None:
    """На Linux/macOS предупреждаем, если .env читается не только владельцем —
    это значит, что любой другой пользователь на сервере может прочитать
    токен бота и JWT-секрет. На Windows этот режим прав не действует, проверка
    просто не сработает (там права настраиваются через ACL/icacls)."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            _logger.warning(
                ".env доступен другим пользователям системы (права %o). "
                "Выполните: chmod 600 %s",
                mode, env_path,
            )
    except FileNotFoundError:
        pass


_warn_if_env_readable_by_others()


def _parse_owners(raw: str) -> list:
    """Парсинг списка владельцев из BOT_OWNERS (@user1,@user2)."""
    if not raw or not raw.strip():
        return []
    owners = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.startswith("@"):
            item = f"@{item}"
        owners.append(item)
    return owners


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# Токен бота (обязательно в .env)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Владельцы бота (могут выдавать Admin другим пользователям)
BOT_OWNERS = _parse_owners(os.getenv("BOT_OWNERS", ""))

# Telegram ID владельца (автоматическая выдача Admin при регистрации)
_owner_id = os.getenv("OWNER_TELEGRAM_ID", "").strip()
OWNER_TELEGRAM_ID = int(_owner_id) if _owner_id else 0

# JWT для веб-панели (обязательно в .env)
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()

# Secure cookie (включить при HTTPS)
COOKIE_SECURE = _env_bool("COOKIE_SECURE", False)

# Белый список IP, которым разрешён доступ к веб-панели (через запятую).
# Пусто = ограничение отключено (полагайтесь на firewall сервера).
# Это доп. барьер на уровне приложения, а не замена firewall/Cloudflare.
ADMIN_IP_WHITELIST = [
    ip.strip() for ip in os.getenv("ADMIN_IP_WHITELIST", "").split(",") if ip.strip()
]

# Принудительный редирект HTTP -> HTTPS на уровне приложения.
# Включайте только если перед приложением НЕТ reverse-proxy (Nginx/Caddy),
# который уже терминирует TLS — иначе он сам видит исходный запрос как HTTP
# и получится бесконечный редирект (если не настроен X-Forwarded-Proto).
FORCE_HTTPS = _env_bool("FORCE_HTTPS", False)

# Минимальная сумма вывода
MIN_WITHDRAWAL = float(os.getenv("MIN_WITHDRAWAL", "3"))

# Канал "Выводы QuillMoney" — сюда постятся скриншоты выплат (вручную, из веб-панели)
_withdrawals_channel_id = os.getenv("WITHDRAWALS_CHANNEL_ID", "").strip()
WITHDRAWALS_CHANNEL_ID = int(_withdrawals_channel_id) if _withdrawals_channel_id else -1004406278506

# Время удержания подписки (дни)
SUBSCRIPTION_HOLD_DAYS = int(os.getenv("SUBSCRIPTION_HOLD_DAYS", "7"))

# Бонус рефереру за каждого приглашённого ($), 0 = только счётчик
REFERRAL_BONUS = float(os.getenv("REFERRAL_BONUS", "0.1"))

# Курс обмена Telegram Stars на доллары для оплаты рекламы (сколько звёзд за $1).
# Telegram не публикует официальный курс для ботов — задаётся вручную.
STARS_PER_USD = float(os.getenv("STARS_PER_USD", "50"))

# Интервал фоновой проверки подписок (часы)
SUBSCRIPTION_CHECK_INTERVAL_HOURS = int(os.getenv("SUBSCRIPTION_CHECK_INTERVAL_HOURS", "6"))

# Интервал довершения "зависших" регистраций по реферальной ссылке — люди,
# подписавшиеся на обязательные каналы, но не вернувшиеся нажать "Я подписался"
# (минуты)
PENDING_REFERRAL_RETRY_MINUTES = int(os.getenv("PENDING_REFERRAL_RETRY_MINUTES", "15"))

# Интервал проверки активности приглашённых рефералов — реферальный бонус
# начисляется рефереру не сразу при регистрации, а только когда приглашённый
# проявит хоть какую-то активность в боте (защита от накрутки) (минуты)
REFERRAL_ACTIVITY_CHECK_MINUTES = int(os.getenv("REFERRAL_ACTIVITY_CHECK_MINUTES", "2"))

# Пауза между сообщениями рассылки (секунды)
BROADCAST_DELAY_SECONDS = float(os.getenv("BROADCAST_DELAY_SECONDS", "0"))

# ===== Игры на деньги =====

# Минимальная ставка в игре "Колесо удачи" ($)
WHEEL_MIN_BET = float(os.getenv("WHEEL_MIN_BET", "0.5"))

# Секторы колеса (множители ставки). Ставки принимаются только с
# верифицированного баланса. Только 2 сектора ниже 1.0 (x0.5, x0.8) — этот
# набор в паре с весами ниже даёт разумный баланс: проигрыш заметно реже
# половины спинов (иначе даже без всякого перевеса бота ощущается как
# накрутка), но эдж бота при таком составе секторов физически не может
# быть большим без роста доли проигрышей — так что он тут скромный.
WHEEL_SEGMENTS = [0.5, 0.8, 1.0, 1.2, 1.3, 1.4, 1.5]

# Веса секторов — дают: 35% проигрыш, 32% ровно в ноль, 33% выигрыш,
# средний множитель 0.968 (~3.2% преимущества бота в долгосрочной
# перспективе).
WHEEL_WEIGHTS = [18, 17, 32, 18, 7, 5, 3]

# Путь к базе данных
DB_PATH = "bot_database.db"

# URL веб-панели (кнопка в боте для админов)
WEB_ADMIN_URL = os.getenv("WEB_ADMIN_URL", "http://YOUR_SERVER_IP:8000/admin").strip()

# Список доменов, которым разрешён CORS к веб-панели (через запятую).
# Пусто = CORS отключён (панель доступна только same-origin — рекомендуется).
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
