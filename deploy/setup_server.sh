#!/bin/bash
# setup_server.sh — install + enable tech_radar_agent as a 24/7 systemd service.
# Mirrors jobs_agent/deploy/setup_server.sh. Run once on the target Ubuntu VPS.
set -euo pipefail

APP_DIR="/home/ubuntu/tech-radar-agent"
SERVICE_NAME="tech_radar"

echo "========================================================"
echo "🚀 Tech-Radar Agent — server setup (Ubuntu + systemd)"
echo "========================================================"

# 1. System packages
echo "📦 Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip curl ca-certificates

# 2. Ensure target dir
echo "📁 Ensuring app directory at ${APP_DIR}..."
sudo mkdir -p "${APP_DIR}"
sudo chown -R ubuntu:ubuntu "${APP_DIR}"

# 3. Virtualenv + deps
echo "🐍 Creating virtualenv + installing Python deps..."
cd "${APP_DIR}"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. .env sanity check
if [ ! -f "${APP_DIR}/.env" ]; then
    echo "⚠️  ${APP_DIR}/.env is missing — copy from .env.example and fill in:"
    echo "    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_THREAD_ID / OPENROUTER_API_KEY"
    if [ -f "${APP_DIR}/.env.example" ]; then
        sudo -u ubuntu cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
        sudo chown ubuntu:ubuntu "${APP_DIR}/.env"
        chmod 600 "${APP_DIR}/.env"
    fi
fi

# 5. systemd unit
echo "⚙️  Installing systemd service ${SERVICE_NAME}.service..."
sudo cp "${APP_DIR}/deploy/tech_radar.service" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "========================================================"
echo "✅ Tech-Radar daemon is installed and running."
echo "Check status:  sudo systemctl status ${SERVICE_NAME}"
echo "Tail logs:     sudo journalctl -u ${SERVICE_NAME} -f"
echo "========================================================"
