#!/bin/bash
# Создаёт архив проекта на сервере (БД + код + .env, без venv/мусора).
# Запускать на сервере: bash deploy/backup.sh
# Для автоматизации — добавить в cron (см. ниже).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
KEEP_DAYS=14   # сколько дней хранить локальные архивы на сервере

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
ARCHIVE_NAME="quillmoney_backup_${TIMESTAMP}.tar.gz"

cd "$PROJECT_DIR"
tar -czf "$BACKUP_DIR/$ARCHIVE_NAME" \
    --exclude='venv*' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='backups' \
    --exclude='.git' \
    .

echo "Создан архив: $BACKUP_DIR/$ARCHIVE_NAME"

# Удаляем архивы старше KEEP_DAYS дней, чтобы не забить диск на сервере
# (полные копии всё равно будут утянуты на ваш компьютер скриптом pull-backup.ps1)
find "$BACKUP_DIR" -name "quillmoney_backup_*.tar.gz" -mtime +"$KEEP_DAYS" -delete

# Создаём/обновляем симлинк на самый свежий архив — удобно для скрипта
# на стороне Windows, который тянет именно "latest", а не гадает имя файла.
ln -sf "$BACKUP_DIR/$ARCHIVE_NAME" "$BACKUP_DIR/latest.tar.gz"
