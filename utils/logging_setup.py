"""
Единая настройка логирования: пишет и в консоль (для screen/tmux), и в файлы
с ротацией — независимо от того, как запущен процесс (screen, nohup,
systemd, просто терминал). Раньше при закрытии screen-сессии вся история
терялась, потому что нигде кроме экрана не сохранялась.

Ротация (RotatingFileHandler) нужна, чтобы файлы не росли бесконечно и не
забили диск за месяцы работы.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

MAX_BYTES = 10 * 1024 * 1024  # 10 МБ на файл
BACKUP_COUNT = 5              # хранить 5 старых файлов (bot_log.txt.1 и т.д.)


def setup_logging(log_file: str, err_file: str, level=logging.INFO) -> None:
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    root = logging.getLogger()
    root.setLevel(level)

    # Не плодим дублирующиеся хендлеры при повторном вызове (например,
    # если модуль импортируют из нескольких входных точек).
    if getattr(root, "_quillmoney_configured", False):
        return
    root._quillmoney_configured = True

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(level)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)
    root.addHandler(file_handler)

    err_handler = RotatingFileHandler(
        err_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    err_handler.setFormatter(fmt)
    err_handler.setLevel(logging.WARNING)
    root.addHandler(err_handler)

    # Логируем необработанные исключения, которые иначе ушли бы только в
    # stderr и потерялись бы вместе с закрытой screen-сессией.
    def _log_uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "Необработанное исключение", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _log_uncaught
