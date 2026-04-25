# Развёртывание на VPS

Скрипт `scripts/deploy_vps.sh` идемпотентно разворачивает бот на Ubuntu/Debian VPS как systemd-сервис.

## Первый запуск

На VPS под root (или через sudo):

```bash
# скопировать скрипт на VPS
curl -fsSL https://raw.githubusercontent.com/Mirzoevmurad/secretary-bot/main/scripts/deploy_vps.sh -o deploy.sh

# задать секреты и запустить
export SECRETARY_BOT_TOKEN='123456:ABC...'
export GROQ_API_KEY='gsk_...'
export ALLOWED_USER_IDS='123456789'
sudo -E bash deploy.sh
```

Если бот ещё не смержен в `main`, деплоим из feature-ветки:
```bash
sudo -E BRANCH=devin/<branch-name> bash deploy.sh
```

Скрипт:
- ставит `python3`, `ffmpeg`, `git`;
- создаёт пользователя `secretary`;
- клонирует репо в `/opt/secretary-bot`;
- разворачивает venv, ставит зависимости;
- пишет `/etc/secretary-bot.env` с chmod 600;
- создаёт и стартует systemd-юнит `secretary-bot.service` с автозапуском.

## Обновление

После правок в `main`:
```bash
sudo bash /root/deploy.sh
```
(ENV-переменные в `/etc/secretary-bot.env` сохраняются.)

## Диагностика

```bash
systemctl status secretary-bot
journalctl -u secretary-bot -f       # хвост логов в реальном времени
journalctl -u secretary-bot -n 100   # последние 100 строк
```

## Параметры через env-файл

Правьте `/etc/secretary-bot.env` вручную и перезапускайте:
```bash
sudo systemctl restart secretary-bot
```

## Остановка / удаление

```bash
sudo systemctl disable --now secretary-bot
sudo rm /etc/systemd/system/secretary-bot.service /etc/secretary-bot.env
sudo rm -rf /opt/secretary-bot
sudo userdel secretary
sudo systemctl daemon-reload
```
