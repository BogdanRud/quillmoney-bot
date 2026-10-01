# Скачивает последний бэкап проекта с сервера на этот компьютер.
# Требует встроенный OpenSSH-клиент Windows (ssh) — он есть по умолчанию
# в Windows 10/11. Проверить: Get-Command ssh
#
# Перед использованием заполните 4 переменные ниже своими значениями.
# Для запуска без пароля настройте SSH-ключ (рекомендуется, иначе
# Планировщик задач не сможет ввести пароль автоматически):
#   ssh-keygen -t ed25519                  (на этом компьютере, если ключа нет)
#   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh user@server "cat >> ~/.ssh/authorized_keys"
#
# Архив на сервере создаёт cron от root (см. backup.sh), но сам файл
# сохраняется с правами 644 (мирочитаемый) в каталоге, доступном на чтение
# участнику группы quillmoney — поэтому scp работает напрямую, без sudo.
#
# ВАЖНО: раньше здесь было "ssh ... sudo cat > file" — PowerShell перенаправление
# ">" прогоняет бинарный вывод внешней команды через текстовую кодировку
# консоли и портит его (добавляет BOM, ломает байты). Из-за этого начиная
# с 2026-06-27 ВСЕ локальные бэкапы, кроме самого первого, оказались битыми
# невалидными gzip-архивами — баг обнаружен и исправлен 2026-07-18. scp
# передаёт файл побайтово и не подвержен этой проблеме.

$ServerUser = "opsadmin"                      # пользователь на сервере (sudo, без прямого root-входа)
$ServerHost = "YOUR_SERVER_IP"                # IP или домен сервера
$RemotePath = "/home/quillmoney/QuillMoney/backups/latest.tar.gz"   # путь к архиву (см. backup.sh)
$LocalDir   = "$env:USERPROFILE\Desktop\QuillMoney_backups"

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$localFile = Join-Path $LocalDir "quillmoney_backup_$timestamp.tar.gz"

Write-Host "Скачиваю $RemotePath -> $localFile"
& scp "${ServerUser}@${ServerHost}:${RemotePath}" $localFile

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: $localFile"

    # Храним только последние 14 архивов локально, чтобы не забить диск
    Get-ChildItem -Path $LocalDir -Filter "quillmoney_backup_*.tar.gz" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 14 |
        Remove-Item -Force
} else {
    Write-Host "ОШИБКА: скачивание не удалось (код $LASTEXITCODE)"
}
