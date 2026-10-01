"""
Запуск веб-панели администратора
"""
import os
import uvicorn
import asyncio
from web_admin.main import app
from database.db import Database
from utils.logging_setup import setup_logging

async def init_db():
    """Инициализация базы данных"""
    db = Database()
    await db.init_db()
    print("✅ База данных инициализирована")

if __name__ == "__main__":
    # Логи (включая access-лог uvicorn — кто, когда, какой запрос) пишутся
    # в файлы с ротацией, не зависят от screen/nohup/терминала.
    setup_logging("webadmin_log.txt", "webadmin_err.txt")

    # Инициализируем БД перед запуском
    asyncio.run(init_db())

    # По умолчанию слушаем только локально (YOUR_SERVER_IP) — наружу панель должен
    # открывать reverse-proxy (Nginx) с HTTPS, см. deploy/nginx-admin.conf.
    # Если очень нужно слушать на всех интерфейсах напрямую (НЕ рекомендуется,
    # это и было одной из причин компрометации), задайте WEB_ADMIN_HOST=YOUR_SERVER_IP
    # в .env осознанно.
    host = os.getenv("WEB_ADMIN_HOST", "YOUR_SERVER_IP")
    port = int(os.getenv("WEB_ADMIN_PORT", "8000"))
    if host == "YOUR_SERVER_IP":
        print(
            "⚠️  ВНИМАНИЕ: WEB_ADMIN_HOST=YOUR_SERVER_IP — панель слушает на всех "
            "интерфейсах напрямую, без reverse-proxy. Делайте это только "
            "если знаете, что делаете, и используете HTTPS + firewall."
        )
    # log_config=None — чтобы uvicorn не перезатёр наши хендлеры логирования
    # своей конфигурацией по умолчанию (которая иначе пишет только в консоль).
    uvicorn.run(app, host=host, port=port, log_config=None)

