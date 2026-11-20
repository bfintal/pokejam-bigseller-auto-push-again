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
JOURNALD_NAMESPACE="pokejam"
JOURNALD_CONF="/etc/systemd/journald@${JOURNALD_NAMESPACE}.conf"

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

# Install the journald namespace config for log size limiting (50 MB, 7 days)
# This keeps this service's logs separate from other services
install -o root -g root -m 644 "${INSTALL_DIR}/systemd/journald@${JOURNALD_NAMESPACE}.conf" "${JOURNALD_CONF}"

# Enable the journald namespace instance (idempotent)
# The socket is what actually receives logs; the service starts on demand
systemctl enable "systemd-journald@${JOURNALD_NAMESPACE}.socket" 2>/dev/null || true
systemctl start "systemd-journald@${JOURNALD_NAMESPACE}.socket" 2>/dev/null || true

install -o root -g root -m 644 "${INSTALL_DIR}/systemd/${SERVICE_NAME}.service" "${SERVICE_FILE}"
systemctl daemon-reload

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  systemctl restart "${SERVICE_NAME}"
  echo "Updated the code and restarted the running service."
else
  echo "Installed. The service is not started."
fi

echo ""
echo "Cookie file: ${COOKIE_FILE}"
echo "Start: sudo systemctl enable --now ${SERVICE_NAME}"
echo "Stop:  sudo systemctl disable --now ${SERVICE_NAME}"
echo "Logs:  sudo journalctl --namespace=${JOURNALD_NAMESPACE} -u ${SERVICE_NAME} -f"
echo ""
echo "Log limits: 50 MB max, 7 days retention (isolated from other services)"
