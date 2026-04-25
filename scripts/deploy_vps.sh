#!/usr/bin/env bash
# Идемпотентный деплой secretary-bot на VPS как systemd-сервис.
# Требует: Ubuntu/Debian, root, заранее сохранённые SECRETARY_BOT_TOKEN и GROQ_API_KEY в окружении.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Mirzoevmurad/secretary-bot.git}"
BRANCH="${BRANCH:-main}"
APP_USER="${APP_USER:-secretary}"
APP_DIR="${APP_DIR:-/opt/secretary-bot}"
ENV_FILE="${ENV_FILE:-/etc/secretary-bot.env}"
SERVICE_NAME="${SERVICE_NAME:-secretary-bot}"

log() { echo -e "\033[1m[secretary]\033[0m $*"; }
die() { echo "FATAL: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Запускайте от root (sudo)"

# Приоритет переменных: значения из текущего окружения > значения из $ENV_FILE.
# Это нужно, чтобы при ротации токена / ключа можно было передать новый секрет
# через env, а старое значение из файла его не перезаписало.
declare -A USER_ENV
for v in SECRETARY_BOT_TOKEN GROQ_API_KEY ALLOWED_USER_IDS TELEGRAM_OWNER_ID \
         STT_MODEL LLM_MODEL KEEP_AUDIO DEFAULT_LANG MAX_AUDIO_MB \
         TZ_NAME DEFAULT_ADVANCE_MINUTES; do
    if [[ -n "${!v:-}" ]]; then
        USER_ENV[$v]="${!v}"
    fi
done

# Подтягиваем env-файл, если есть.
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# Восстанавливаем переменные, переданные пользователем (имеют приоритет).
for v in "${!USER_ENV[@]}"; do
    export "$v"="${USER_ENV[$v]}"
done

[[ -n "${SECRETARY_BOT_TOKEN:-}" ]] || die "SECRETARY_BOT_TOKEN не задан (передайте в окружении или подготовьте $ENV_FILE)"
[[ -n "${GROQ_API_KEY:-}" ]] || die "GROQ_API_KEY не задан (передайте в окружении или подготовьте $ENV_FILE)"
[[ -n "${ALLOWED_USER_IDS:-${TELEGRAM_OWNER_ID:-}}" ]] || die "ALLOWED_USER_IDS (или TELEGRAM_OWNER_ID) не задан"

log "Устанавливаю системные пакеты..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git ffmpeg ca-certificates

if ! id "$APP_USER" &>/dev/null; then
    log "Создаю системного пользователя $APP_USER..."
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

if [[ -d "$APP_DIR/.git" ]]; then
    log "Обновляю репозиторий в $APP_DIR..."
    sudo -u "$APP_USER" git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard FETCH_HEAD
else
    log "Клонирую $REPO_URL → $APP_DIR..."
    mkdir -p "$APP_DIR"
    chown "$APP_USER:$APP_USER" "$APP_DIR"
    sudo -u "$APP_USER" git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

log "Устанавливаю Python-зависимости в venv..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade -q pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

ALLOWED="${ALLOWED_USER_IDS:-${TELEGRAM_OWNER_ID}}"
log "Пишу $ENV_FILE (chmod 600)..."
cat >"$ENV_FILE" <<ENV
SECRETARY_BOT_TOKEN=${SECRETARY_BOT_TOKEN}
GROQ_API_KEY=${GROQ_API_KEY}
ALLOWED_USER_IDS=${ALLOWED}
DB_PATH=${APP_DIR}/data/secretary.sqlite
STT_MODEL=${STT_MODEL:-whisper-large-v3-turbo}
LLM_MODEL=${LLM_MODEL:-llama-3.3-70b-versatile}
KEEP_AUDIO=${KEEP_AUDIO:-false}
DEFAULT_LANG=${DEFAULT_LANG:-auto}
MAX_AUDIO_MB=${MAX_AUDIO_MB:-25}
TZ_NAME=${TZ_NAME:-Europe/Moscow}
DEFAULT_ADVANCE_MINUTES=${DEFAULT_ADVANCE_MINUTES:-5}
ENV
chmod 600 "$ENV_FILE"
chown root:"$APP_USER" "$ENV_FILE"

log "Создаю systemd-юнит /etc/systemd/system/${SERVICE_NAME}.service..."
cat >"/etc/systemd/system/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=Secretary Telegram bot (voice → structured notes via Groq)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=HOME=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/bot.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
# базовая изоляция
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=false
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null
systemctl restart "${SERVICE_NAME}"

sleep 3
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "Сервис ${SERVICE_NAME} запущен. Напишите боту /start."
else
    log "ERROR: сервис не стартовал. Логи:"
    journalctl -u "${SERVICE_NAME}" -n 30 --no-pager
    exit 1
fi

echo
echo "============================================================"
log "Готово."
echo "Статус:    systemctl status ${SERVICE_NAME}"
echo "Логи:      journalctl -u ${SERVICE_NAME} -f"
echo "Обновить:  sudo bash $0"
echo "============================================================"
