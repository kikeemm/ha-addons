#!/command/with-contenv bashio

export TELEGRAM_TOKEN="$(bashio::config 'telegram_token')"
export GITHUB_TOKEN="$(bashio::config 'github_token')"
export GITHUB_REPO="$(bashio::config 'github_repo')"
export INBOX_PATH="$(bashio::config 'inbox_path')"
export ALLOWED_CHAT_ID="$(bashio::config 'allowed_chat_id')"

bashio::log.info "Bot de Telegram iniciado (repo: ${GITHUB_REPO})"
exec python3 -u /bot.py
