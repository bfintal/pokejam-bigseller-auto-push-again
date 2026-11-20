#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this as root: sudo bash install.sh" >&2
  exit 1
fi

REPO_URL="https://github.com/bfintal/pokejam-bigseller-auto-push-again.git"
INSTALL_DIR="/opt/pokejam-bigseller-auto-push"
COOKIE_DIR="/etc/pokejam-bigseller"
COOKIE_FILE="${COOKIE_DIR}/cookie"
SERVICE_NAME="pokejam-bigseller-auto-push"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 git ca-certificates

if [[ -d "${INSTALL_DIR}/.git" ]]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

if ! id -u pokejam >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin pokejam
fi

install -d -o pokejam -g pokejam -m 700 "${COOKIE_DIR}"
if [[ ! -f "${COOKIE_FILE}" ]]; then
  install -o pokejam -g pokejam -m 600 /dev/null "${COOKIE_FILE}"
else
  chown pokejam:pokejam "${COOKIE_FILE}"
  chmod 600 "${COOKIE_FILE}"
fi

chown -R root:root "${INSTALL_DIR}"
chmod -R a+rX "${INSTALL_DIR}"

install -o root -g root -m 644 "${INSTALL_DIR}/systemd/${SERVICE_NAME}.service" "${SERVICE_FILE}"
systemctl daemon-reload

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  systemctl restart "${SERVICE_NAME}"
  echo "Updated the code and restarted the running service."
else
  echo "Installed. The service is not started."
fi

echo "Cookie file: ${COOKIE_FILE}"
echo "Start: sudo systemctl enable --now ${SERVICE_NAME}"
echo "Stop:  sudo systemctl disable --now ${SERVICE_NAME}"
echo "Logs:  sudo journalctl -u ${SERVICE_NAME} -f"
