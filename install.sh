#!/bin/bash
set -euo pipefail

PORT=8088
GPIO=14
START_TEMP=45
FULL_TEMP=75
MIN_DUTY=45
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo "Usage: sudo ./install.sh [--port 8088] [--gpio 14] [--start-temp 45] [--full-temp 75] [--min-duty 45]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --gpio) GPIO="$2"; shift 2 ;;
    --start-temp) START_TEMP="$2"; shift 2 ;;
    --full-temp) FULL_TEMP="$2"; shift 2 ;;
    --min-duty) MIN_DUTY="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  echo "Port must be between 1024 and 65535." >&2
  exit 2
fi
if ss -ltnH "sport = :$PORT" | grep -q . && ! systemctl is-active --quiet pifandashboard-web.service; then
  echo "Port $PORT is already in use. Choose another with --port." >&2
  exit 1
fi
if grep -qE '(^| )console=(serial0|ttyAMA0|ttyS0),' /proc/cmdline && [[ "$GPIO" == "14" ]]; then
  echo "GPIO14 is being used by the serial console. Disable it with 'sudo raspi-config nonint do_serial 1', then reboot." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-flask python3-psutil rsync
if apt-cache show python3-rpi-lgpio >/dev/null 2>&1; then
  apt-get install -y python3-rpi-lgpio
else
  apt-get install -y python3-rpi.gpio
fi

id pifandashboard >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin pifandashboard
install -d -m 0755 /opt/pifandashboard/app /opt/pifandashboard/web
install -d -o pifandashboard -g pifandashboard -m 0755 /var/lib/pifandashboard
install -d -o root -g pifandashboard -m 0775 /etc/pifandashboard
install -m 0755 "$SOURCE_DIR/app/fan_control.py" "$SOURCE_DIR/app/dashboard.py" /opt/pifandashboard/app/
rsync -a --delete "$SOURCE_DIR/webinterface/fandashboard/" /opt/pifandashboard/web/
chmod -R u=rwX,go=rX /opt/pifandashboard

if [[ ! -f /var/lib/pifandashboard/pi_list.json ]]; then
  PI_IP="$(hostname -I | awk '{print $1}')"
  printf '[{"name":"%s","ip":"%s","port":%s}]\n' "$(hostname)" "$PI_IP" "$PORT" >/var/lib/pifandashboard/pi_list.json
fi
chown pifandashboard:pifandashboard /var/lib/pifandashboard/pi_list.json
if [[ ! -f /etc/pifandashboard/fan.json ]]; then
  printf '{"gpio":%s,"start_temp":%s,"full_temp":%s,"min_duty":%s}\n' "$GPIO" "$START_TEMP" "$FULL_TEMP" "$MIN_DUTY" >/etc/pifandashboard/fan.json
fi
chown root:pifandashboard /etc/pifandashboard/fan.json
chmod 0664 /etc/pifandashboard/fan.json

install -m 0644 "$SOURCE_DIR/systemd/pifandashboard-fan.service" /etc/systemd/system/
sed "s/Environment=PIFAN_PORT=.*/Environment=PIFAN_PORT=$PORT/" "$SOURCE_DIR/systemd/pifandashboard-web.service" >/etc/systemd/system/pifandashboard-web.service
chmod 0644 /etc/systemd/system/pifandashboard-web.service
systemctl daemon-reload
systemctl enable --now pifandashboard-fan.service pifandashboard-web.service
systemctl restart pifandashboard-fan.service pifandashboard-web.service

echo "Pi Fan Dashboard is running at http://$(hostname -I | awk '{print $1}'):$PORT/"
echo "This installer did not install or change nginx, Pi-hole, or /var/www."
