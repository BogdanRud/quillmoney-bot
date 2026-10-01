# Безопасный деплой на сервер (Linux/VPS)

Эти файлы закрывают вектор, через который, вероятнее всего, произошла
утечка: панель администратора была открыта напрямую по HTTP на голом IP
(`http://YOUR_SERVER_IP:8000`), без шифрования и без файрвола.

## Порядок действий на сервере

1. **Создать отдельного пользователя** (не root, не текущий с правами на всё):
   ```
   useradd -r -s /usr/sbin/nologin -d /opt/quillmoney quillmoney
   ```
2. **Скопировать проект** в `/opt/quillmoney`, поставить зависимости в venv там же.
3. **Права на секреты**:
   ```
   chown -R quillmoney:quillmoney /opt/quillmoney
   chmod 600 /opt/quillmoney/.env /opt/quillmoney/bot_database.db
   ```
4. **Домен + HTTPS**: направить DNS-запись на сервер, поставить Nginx и Certbot:
   ```
   apt install nginx certbot python3-certbot-nginx
   cp deploy/proxy_params_quillmoney /etc/nginx/proxy_params_quillmoney
   cp deploy/nginx-admin.conf /etc/nginx/sites-available/quillmoney-admin
   # отредактировать your-domain.example -> реальный домен
   ln -s /etc/nginx/sites-available/quillmoney-admin /etc/nginx/sites-enabled/
   certbot --nginx -d your-domain.example
   nginx -t && systemctl reload nginx
   ```
5. **systemd-юниты**:
   ```
   cp deploy/quillmoney-bot.service /etc/systemd/system/
   cp deploy/quillmoney-webadmin.service /etc/systemd/system/
   # отредактировать пути/пользователя при необходимости
   systemctl daemon-reload
   systemctl enable --now quillmoney-bot quillmoney-webadmin
   ```
6. **Firewall** — закрыть порт 8000 снаружи, открыть только 80/443:
   ```
   ufw default deny incoming
   ufw allow OpenSSH
   ufw allow 'Nginx Full'
   ufw enable
   ufw status
   ```
   Порт 8000 не должен быть в списке `ufw status` вообще — uvicorn слушает
   только `YOUR_SERVER_IP`, так что снаружи он и так недостижим, но лишний слой
   защиты не помешает.
7. **.env**: выставить
   ```
   COOKIE_SECURE=true
   WEB_ADMIN_URL=https://your-domain.example/admin
   WEB_ADMIN_HOST=YOUR_SERVER_IP
   ```
   и сгенерировать новые `BOT_TOKEN` (через @BotFather) и `JWT_SECRET_KEY`
   (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).

## Проверка

- `curl -I https://your-domain.example/` → должен быть `200`, заголовки
  `Strict-Transport-Security`, `X-Frame-Options: DENY`.
- `curl http://<IP>:8000/` снаружи сервера → должен **не отвечать**
  (connection refused/timeout) — порт не должен быть достижим напрямую.

## Автоматический бэкап проекта на ваш компьютер

Сервер не может сам "достучаться" до домашнего/рабочего ПК (он за NAT, без
постоянного публичного IP) — поэтому архив на сервере создаётся по cron,
а скачивает его именно ваш компьютер по расписанию.

### 1. На сервере: создание архива по расписанию

```bash
chmod +x deploy/backup.sh
crontab -e
```
Добавить строку (бэкап каждый день в 03:00):
```
0 3 * * * /opt/quillmoney/deploy/backup.sh >> /opt/quillmoney/backups/backup.log 2>&1
```
Архивы складываются в `/opt/quillmoney/backups/`, старше 14 дней удаляются
автоматически, `latest.tar.gz` всегда указывает на самый свежий.

**Важно:** в архив попадает `.env` с токеном и JWT-секретом — это сделано
специально (полная копия), но значит, что архивы на вашем компьютере нужно
хранить так же бережно, как сами секреты (не заливать в облако без
шифрования, не пересылать кому попало).

### 2. На вашем компьютере (Windows): скачивание по расписанию

1. Откройте [deploy/pull-backup.ps1](pull-backup.ps1) и заполните 3 переменные
   в начале файла: `$ServerUser`, `$ServerHost`, `$RemotePath` (путь к
   `latest.tar.gz` на сервере).
2. Настройте вход по SSH-ключу без пароля (иначе Планировщик задач не
   сможет ввести пароль):
   ```
   ssh-keygen -t ed25519
   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh user@server "cat >> ~/.ssh/authorized_keys"
   ```
3. Проверьте вручную, что скрипт работает:
   ```
   powershell -ExecutionPolicy Bypass -File "deploy\pull-backup.ps1"
   ```
   Архив должен появиться в `Desktop\QuillMoney_backups\`.
4. Поставьте автозапуск через Планировщик задач Windows:
   - `taskschd.msc` → «Создать задачу»
   - Триггер: ежедневно, например в 06:00 (через 3 часа после бэкапа на сервере)
   - Действие: запустить программу
     - Программа: `powershell.exe`
     - Аргументы: `-ExecutionPolicy Bypass -File "C:\Users\Bohdan2\Desktop\QuillMoney 22.06\deploy\pull-backup.ps1"`
   - Поставьте галочку «Выполнять, даже если пользователь не выполнил вход»

После этого у вас на Desktop будет накапливаться `QuillMoney_backups\` с
полными архивами проекта за последние 14 дней, без какого-либо ручного
действия.
