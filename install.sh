#!/bin/bash
set -Eeuo pipefail

PREFERRED_PORT=8088
PORT_MODE=auto
REQUESTED_PORT=""
BIND_ADDRESS=0.0.0.0
GPIO=14
START_TEMP=45
FULL_TEMP=75
MIN_DUTY=45
HYSTERESIS=2
UPDATE_CURVE=false
RESET_PASSWORD=false
PROMPT_PORT=false
GPIO_SET=false
START_TEMP_SET=false
FULL_TEMP_SET=false
MIN_DUTY_SET=false
HYSTERESIS_SET=false
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/pifandashboard
PREVIOUS_DIR=/opt/pifandashboard.previous
ETC_DIR=/etc/pifandashboard
ENV_FILE="$ETC_DIR/web.env"
FAN_ENV_FILE="$ETC_DIR/fan.env"
WEB_DROPIN_DIR=/etc/systemd/system/pifandashboard-web.service.d
NETWORK_DROPIN="$WEB_DROPIN_DIR/network.conf"
AUTH_FILE="$ETC_DIR/auth.json"
DATA_DIR=/var/lib/pifandashboard
CONFIG_DIR="$DATA_DIR/config"
WEB_DATA_DIR="$DATA_DIR/web"
CONFIG_FILE="$CONFIG_DIR/fan.json"
PI_LIST_FILE="$WEB_DATA_DIR/pi_list.json"
SHARED_GROUP=pifandashboard
AUTH_GROUP=pifandashboard-auth
WEB_USER=pifandashboard-web
FAN_USER=pifandashboard-fan
STAGE_DIR=""
BACKUP_DIR=""
SWAPPED=false
INSTALL_COMPLETE=false
STATE_BACKED_UP=false
NEW_PASSWORD=""

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]

  --port auto|PORT       Preserve an installed port, otherwise choose 8088+
  --prompt-port          Ask before using an automatically selected fallback
  --bind ADDRESS         Listen address (default: 0.0.0.0 on the private LAN)
  --gpio GPIO            BCM GPIO: 12, 13, 14, 18, or 19
  --start-temp C         Fan start temperature, 20-75
  --full-temp C          Full-duty temperature, at least 5 C above start, max 90
  --min-duty PERCENT     Starting PWM duty, 35-100
  --hysteresis C         Fan stop hysteresis, 0-10
  --update-curve         Apply supplied curve values during an upgrade
  --reset-password       Generate a replacement dashboard password
  -h, --help             Show this help
EOF
}

need_value() {
  if [[ $# -lt 2 || -z "$2" ]]; then
    echo "Missing value for $1" >&2
    usage >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      need_value "$@"
      REQUESTED_PORT="$2"
      [[ "$REQUESTED_PORT" == auto ]] || PORT_MODE=explicit
      shift 2
      ;;
    --prompt-port) PROMPT_PORT=true; shift ;;
    --bind) need_value "$@"; BIND_ADDRESS="$2"; shift 2 ;;
    --gpio) need_value "$@"; GPIO="$2"; GPIO_SET=true; shift 2 ;;
    --start-temp) need_value "$@"; START_TEMP="$2"; START_TEMP_SET=true; shift 2 ;;
    --full-temp) need_value "$@"; FULL_TEMP="$2"; FULL_TEMP_SET=true; shift 2 ;;
    --min-duty) need_value "$@"; MIN_DUTY="$2"; MIN_DUTY_SET=true; shift 2 ;;
    --hysteresis) need_value "$@"; HYSTERESIS="$2"; HYSTERESIS_SET=true; shift 2 ;;
    --update-curve) UPDATE_CURVE=true; shift ;;
    --reset-password) RESET_PASSWORD=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi
for command in systemctl ss python3 install dpkg-query runuser ip find apt-get apt-cache getent useradd groupadd usermod; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command is missing: $command" >&2; exit 1; }
done
for required_path in "$SOURCE_DIR/app" "$SOURCE_DIR/webinterface/fandashboard"; do
  [[ -d "$required_path" && ! -L "$required_path" ]] || {
    echo "Required installer directory is missing or unsafe: $required_path" >&2
    exit 1
  }
done
for required_path in "$SOURCE_DIR/systemd/pifandashboard-fan.service" \
                     "$SOURCE_DIR/systemd/pifandashboard-web.service"; do
  [[ -f "$required_path" && ! -L "$required_path" ]] || {
    echo "Required installer file is missing or unsafe: $required_path" >&2
    exit 1
  }
done
if [[ ! -d /run/systemd/system ]]; then
  echo "This installer requires systemd." >&2
  exit 1
fi
if ! BIND_ADDRESS="$(python3 -c 'import ipaddress,sys; value=ipaddress.ip_address(sys.argv[1]); value.version == 4 or sys.exit(1); print(value)' "$BIND_ADDRESS" 2>/dev/null)"; then
  echo "--bind must be a valid IPv4 address." >&2
  exit 2
fi
if [[ "$BIND_ADDRESS" != "0.0.0.0" && "$BIND_ADDRESS" != "127.0.0.1" ]] && \
   ! ip -o -4 addr show | awk '{sub(/\/.*/, "", $4); print $4}' | grep -Fxq "$BIND_ADDRESS"; then
  echo "--bind address $BIND_ADDRESS is not assigned to this Pi." >&2
  exit 2
fi
LAN_ADDRESS="$(ip -o -4 addr show scope global | awk 'NR==1 {print $4}')"
LAN_IP="${LAN_ADDRESS%%/*}"
[[ -n "$LAN_IP" ]] || LAN_IP=127.0.0.1
LAN_ALLOW_LINES="$(ip -o -4 addr show scope global | awk '{print $4}' | python3 -c '
import ipaddress, sys
seen = set()
allowed = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
for line in sys.stdin:
    try:
        network = ipaddress.ip_network(line.strip(), strict=False)
    except ValueError:
        continue
    if any(network.subnet_of(candidate) for candidate in allowed) and network not in seen:
        print(f"IPAddressAllow={network}")
        seen.add(network)
')"
[[ -n "$LAN_ALLOW_LINES" ]] || LAN_ALLOW_LINES='IPAddressAllow=127.0.0.0/8'

# Validate every curve argument before apt, services, or files are touched.
VALIDATION_FILE="$(mktemp /tmp/pifandashboard-config.XXXXXX)"
rm -f -- "$VALIDATION_FILE"
PYTHONPATH="$SOURCE_DIR" python3 -m app.config "$VALIDATION_FILE" --write \
  --gpio "$GPIO" --start-temp "$START_TEMP" --full-temp "$FULL_TEMP" \
  --min-duty "$MIN_DUTY" --hysteresis "$HYSTERESIS"
rm -f -- "$VALIDATION_FILE"

if [[ "$GPIO" == 14 && ( "$GPIO_SET" == true || ! -e "$CONFIG_FILE" ) ]] && \
   grep -qE '(^| )console=(serial0|ttyAMA0|ttyS0),' /proc/cmdline; then
  echo "GPIO14 is used by the serial console. Run 'sudo raspi-config nonint do_serial 1', reboot, then retry." >&2
  exit 1
fi

is_valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1024 && 10#$1 <= 65535 ))
}

current_port() {
  local value=""
  if [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]]; then
    value="$(sed -n 's/^PIFAN_PORT=//p' "$ENV_FILE" | tail -1)"
  fi
  if ! is_valid_port "${value:-}" && systemctl cat pifandashboard-web.service >/dev/null 2>&1; then
    value="$(systemctl show pifandashboard-web.service -p Environment --value 2>/dev/null | sed -n 's/.*PIFAN_PORT=\([0-9][0-9]*\).*/\1/p')"
  fi
  is_valid_port "${value:-}" && printf '%s' "$value"
}

port_is_listening() {
  ss -H -ltn "sport = :$1" | grep -q .
}

CURRENT_PORT="$(current_port || true)"
SERVICE_ACTIVE=false
systemctl is-active --quiet pifandashboard-web.service && SERVICE_ACTIVE=true

port_available() {
  local candidate="$1"
  if ! port_is_listening "$candidate"; then
    return 0
  fi
  [[ "$SERVICE_ACTIVE" == true && "$CURRENT_PORT" == "$candidate" ]]
}

select_auto_port() {
  local candidate
  for candidate in $(seq "$PREFERRED_PORT" 8188); do
    if port_available "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

if [[ "$PORT_MODE" == explicit ]]; then
  is_valid_port "$REQUESTED_PORT" || { echo "Port must be between 1024 and 65535." >&2; exit 2; }
  port_available "$REQUESTED_PORT" || { echo "Port $REQUESTED_PORT is already owned by another service." >&2; exit 1; }
  PORT="$REQUESTED_PORT"
elif is_valid_port "${CURRENT_PORT:-}" && port_available "$CURRENT_PORT"; then
  PORT="$CURRENT_PORT"
else
  if is_valid_port "${CURRENT_PORT:-}"; then
    echo "Saved port $CURRENT_PORT is now occupied by another service; selecting a replacement."
  fi
  PORT="$(select_auto_port || true)"
  [[ -n "$PORT" ]] || { echo "No free dashboard port was found in 8088-8188." >&2; exit 1; }
  if [[ "$PORT" != "$PREFERRED_PORT" ]]; then
    echo "Port $PREFERRED_PORT is occupied; selected port $PORT automatically."
    if [[ "$PROMPT_PORT" == true && -r /dev/tty ]]; then
      read -r -p "Use port $PORT? [Y/n] " answer </dev/tty
      if [[ "${answer:-y}" =~ ^[Nn]$ ]]; then
        read -r -p "Enter a port (1024-65535): " chosen </dev/tty
        if ! is_valid_port "$chosen" || ! port_available "$chosen"; then
          echo "That port is invalid or occupied." >&2
          exit 1
        fi
        PORT="$chosen"
      fi
    fi
  fi
fi

if (( $(df -Pk /opt | awk 'NR==2 {print $4}') < 51200 )); then
  echo "At least 50 MB of free space is required under /opt." >&2
  exit 1
fi
if (( $(df -Pi /opt | awk 'NR==2 {print $4}') < 100 )); then
  echo "At least 100 free inodes are required under /opt." >&2
  exit 1
fi

missing_packages=()
for package in python3-flask python3-waitress rsync curl; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'ok installed' || missing_packages+=("$package")
done
if ! python3 -c 'import RPi.GPIO' >/dev/null 2>&1; then
  if apt-cache show python3-rpi-lgpio >/dev/null 2>&1; then
    missing_packages+=(python3-rpi-lgpio)
  else
    missing_packages+=(python3-rpi.gpio)
  fi
fi
if (( ${#missing_packages[@]} )); then
  export DEBIAN_FRONTEND=noninteractive
  if ! apt-get install -y "${missing_packages[@]}"; then
    apt-get update
    apt-get install -y "${missing_packages[@]}"
  fi
fi

getent group "$SHARED_GROUP" >/dev/null || groupadd --system "$SHARED_GROUP"
getent group "$AUTH_GROUP" >/dev/null || groupadd --system "$AUTH_GROUP"
getent group gpio >/dev/null || { echo "The required gpio group does not exist." >&2; exit 1; }

verify_service_user() {
  local user="$1" allowed_groups="$2" entry uid gid home shell group
  entry="$(getent passwd "$user")"
  IFS=: read -r _ _ uid gid _ home shell <<<"$entry"
  if (( uid == 0 || uid >= 1000 )) || [[ "$gid" != "$(getent group "$SHARED_GROUP" | cut -d: -f3)" ]] || \
     [[ "$home" != /nonexistent ]] || [[ "$shell" != */nologin ]]; then
    echo "Refusing unexpected pre-existing account configuration for $user." >&2
    exit 1
  fi
  for group in $(id -nG "$user"); do
    [[ " $allowed_groups " == *" $group "* ]] || {
      echo "Refusing $user because it belongs to unexpected group $group." >&2
      exit 1
    }
  done
}
if id "$WEB_USER" >/dev/null 2>&1; then
  verify_service_user "$WEB_USER" "$SHARED_GROUP $AUTH_GROUP"
else
  useradd --system --gid "$SHARED_GROUP" --groups "$AUTH_GROUP" --home /nonexistent --shell /usr/sbin/nologin "$WEB_USER"
fi
if id "$FAN_USER" >/dev/null 2>&1; then
  verify_service_user "$FAN_USER" "$SHARED_GROUP gpio"
else
  useradd --system --gid "$SHARED_GROUP" --groups gpio --home /nonexistent --shell /usr/sbin/nologin "$FAN_USER"
fi
usermod -a -G "$AUTH_GROUP" "$WEB_USER"
usermod -a -G gpio "$FAN_USER"
verify_service_user "$WEB_USER" "$SHARED_GROUP $AUTH_GROUP"
verify_service_user "$FAN_USER" "$SHARED_GROUP gpio"

for path in "$ETC_DIR" "$DATA_DIR" "$CONFIG_DIR" "$WEB_DATA_DIR"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlinked installation path: $path" >&2; exit 1; }
done
install -d -o root -g root -m 0755 "$ETC_DIR" "$DATA_DIR"
install -d -o "$WEB_USER" -g "$SHARED_GROUP" -m 2750 "$CONFIG_DIR"
install -d -o "$WEB_USER" -g "$SHARED_GROUP" -m 0750 "$WEB_DATA_DIR"

for path in "$CONFIG_FILE" "$PI_LIST_FILE" "$AUTH_FILE" "$ENV_FILE" "$FAN_ENV_FILE" "$NETWORK_DROPIN"; do
  [[ ! -L "$path" ]] || { echo "Refusing symlinked state file: $path" >&2; exit 1; }
  [[ ! -e "$path" || -f "$path" ]] || { echo "State path is not a regular file: $path" >&2; exit 1; }
done

check_mutable_state_files() {
  local path
  for path in "$CONFIG_FILE" "$PI_LIST_FILE"; do
    [[ ! -L "$path" ]] || { echo "Refusing symlinked state file: $path" >&2; return 1; }
    [[ ! -e "$path" || -f "$path" ]] || { echo "State path is not a regular file: $path" >&2; return 1; }
  done
}

WAS_ENABLED=false
WAS_ACTIVE=false
systemctl is-enabled --quiet pifandashboard-web.service 2>/dev/null && WAS_ENABLED=true
systemctl is-active --quiet pifandashboard-web.service 2>/dev/null && WAS_ACTIVE=true

BACKUP_DIR="$(mktemp -d /tmp/pifandashboard-backup.XXXXXX)"
for unit in pifandashboard-fan.service pifandashboard-web.service; do
  [[ -f "/etc/systemd/system/$unit" ]] && cp -a "/etc/systemd/system/$unit" "$BACKUP_DIR/"
done
[[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "$BACKUP_DIR/web.env"
[[ -f "$FAN_ENV_FILE" ]] && cp -a "$FAN_ENV_FILE" "$BACKUP_DIR/fan.env"
[[ -f "$NETWORK_DROPIN" ]] && cp -a "$NETWORK_DROPIN" "$BACKUP_DIR/network.conf"

safe_remove() {
  case "$1" in
    /opt/pifandashboard.stage.*|/opt/pifandashboard.previous) rm -rf -- "$1" ;;
    *) echo "Refusing unsafe cleanup path: $1" >&2; return 1 ;;
  esac
}

rollback() {
  local exit_code=$?
  [[ "$INSTALL_COMPLETE" == true ]] && return
  trap - ERR INT TERM
  set +e
  if ! mkdir "$BACKUP_DIR/.rollback-in-progress" 2>/dev/null; then
    exit "$exit_code"
  fi
  echo "Installation failed; restoring the previous dashboard." >&2
  systemctl stop pifandashboard-web.service pifandashboard-fan.service >/dev/null 2>&1 || true
  if [[ "$SWAPPED" == true && -d "$PREVIOUS_DIR" ]]; then
    [[ -d "$INSTALL_DIR" ]] && mv "$INSTALL_DIR" "${INSTALL_DIR}.failed"
    mv "$PREVIOUS_DIR" "$INSTALL_DIR"
    [[ -d "${INSTALL_DIR}.failed" ]] && rm -rf -- "${INSTALL_DIR}.failed"
  elif [[ "$SWAPPED" == true && -d "$INSTALL_DIR" ]]; then
    rm -rf -- "$INSTALL_DIR"
  fi
  for unit in pifandashboard-fan.service pifandashboard-web.service; do
    if [[ -f "$BACKUP_DIR/$unit" ]]; then
      cp -a "$BACKUP_DIR/$unit" "/etc/systemd/system/$unit"
    else
      rm -f "/etc/systemd/system/$unit"
    fi
  done
  if [[ -f "$BACKUP_DIR/web.env" ]]; then
    cp -a "$BACKUP_DIR/web.env" "$ENV_FILE"
  else
    rm -f "$ENV_FILE"
  fi
  if [[ -f "$BACKUP_DIR/fan.env" ]]; then
    cp -a "$BACKUP_DIR/fan.env" "$FAN_ENV_FILE"
  else
    rm -f "$FAN_ENV_FILE"
  fi
  if [[ -f "$BACKUP_DIR/network.conf" ]]; then
    install -d -o root -g root -m 0755 "$WEB_DROPIN_DIR"
    cp -a "$BACKUP_DIR/network.conf" "$NETWORK_DROPIN"
  else
    rm -f "$NETWORK_DROPIN"
  fi
  if [[ "$STATE_BACKED_UP" == true ]]; then
    for entry in config:fan.json list:pi_list.json auth:auth.json; do
      name="${entry%%:*}"
      backup_name="${entry#*:}"
      case "$name" in
        config) target="$CONFIG_FILE" ;;
        list) target="$PI_LIST_FILE" ;;
        auth) target="$AUTH_FILE" ;;
      esac
      if [[ -f "$BACKUP_DIR/$backup_name" ]]; then
        cp -a "$BACKUP_DIR/$backup_name" "$target"
      else
        rm -f "$target"
      fi
    done
  fi
  systemctl daemon-reload || true
  if [[ "$WAS_ENABLED" == true ]]; then
    systemctl enable pifandashboard-fan.service pifandashboard-web.service >/dev/null 2>&1 || true
  else
    systemctl disable pifandashboard-fan.service pifandashboard-web.service >/dev/null 2>&1 || true
  fi
  if [[ "$WAS_ACTIVE" == true ]]; then
    systemctl start pifandashboard-fan.service pifandashboard-web.service >/dev/null 2>&1 || true
  fi
  if [[ -n "$STAGE_DIR" && -d "$STAGE_DIR" ]]; then
    safe_remove "$STAGE_DIR" || true
  fi
  rm -rf -- "$BACKUP_DIR"
  exit "$exit_code"
}
trap rollback ERR INT TERM

STAGE_DIR="$(mktemp -d /opt/pifandashboard.stage.XXXXXX)"
SPECIAL_SOURCE="$(find "$SOURCE_DIR/app" "$SOURCE_DIR/webinterface/fandashboard" \! -type f \! -type d -print -quit)"
[[ -z "$SPECIAL_SOURCE" ]] || { echo "Refusing non-regular source path: $SPECIAL_SOURCE" >&2; false; }
install -d -m 0755 "$STAGE_DIR/app" "$STAGE_DIR/web"
rsync -a --delete --exclude '__pycache__' "$SOURCE_DIR/app/" "$STAGE_DIR/app/"
rsync -a --delete "$SOURCE_DIR/webinterface/fandashboard/" "$STAGE_DIR/web/"
chown -R root:root "$STAGE_DIR"
chmod -R u=rwX,go=rX "$STAGE_DIR"
PYTHONPATH="$STAGE_DIR" python3 -m compileall -q "$STAGE_DIR/app"
STATE_PYTHONPATH="$STAGE_DIR"

systemctl stop pifandashboard-web.service pifandashboard-fan.service >/dev/null 2>&1 || true
check_mutable_state_files
[[ -f "$CONFIG_FILE" ]] && cp -a "$CONFIG_FILE" "$BACKUP_DIR/fan.json"
[[ -f "$PI_LIST_FILE" ]] && cp -a "$PI_LIST_FILE" "$BACKUP_DIR/pi_list.json"
[[ -f "$AUTH_FILE" ]] && cp -a "$AUTH_FILE" "$BACKUP_DIR/auth.json"
STATE_BACKED_UP=true

# Once the old dashboard has stopped, every listener on the chosen port is foreign.
if port_is_listening "$PORT"; then
  if [[ "$PORT_MODE" == explicit ]]; then
    echo "Port $PORT became occupied during installation." >&2
    false
  fi
  PREVIOUS_SELECTION="$PORT"
  SERVICE_ACTIVE=false
  PORT="$(select_auto_port || true)"
  [[ -n "$PORT" ]] || { echo "No free dashboard port was found in 8088-8188." >&2; false; }
  echo "Port $PREVIOUS_SELECTION became occupied; selected port $PORT instead."
fi

# Migrate the first-generation state only after its writers have stopped.
if [[ ! -e "$CONFIG_FILE" ]]; then
  old_config=""
  for candidate in "$DATA_DIR/fan.json" "$ETC_DIR/fan.json"; do
    if [[ -f "$candidate" && ! -L "$candidate" ]]; then old_config="$candidate"; break; fi
  done
  if [[ -n "$old_config" ]]; then
    PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.config "$old_config"
    install -o "$WEB_USER" -g "$SHARED_GROUP" -m 0640 "$old_config" "$CONFIG_FILE"
    runuser -u "$WEB_USER" -- env PYTHONPATH="$STATE_PYTHONPATH" \
      python3 -m app.config "$CONFIG_FILE" --canonicalize
  else
    PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.config "$CONFIG_FILE" --write \
      --gpio "$GPIO" --start-temp "$START_TEMP" --full-temp "$FULL_TEMP" \
      --min-duty "$MIN_DUTY" --hysteresis "$HYSTERESIS"
    chown "$WEB_USER:$SHARED_GROUP" "$CONFIG_FILE"
  fi
elif [[ "$UPDATE_CURVE" == true ]]; then
  runuser -u "$WEB_USER" -- env PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.config "$CONFIG_FILE" --write \
    --gpio "$GPIO" --start-temp "$START_TEMP" --full-temp "$FULL_TEMP" \
    --min-duty "$MIN_DUTY" --hysteresis "$HYSTERESIS"
elif [[ "$GPIO_SET" == true || "$START_TEMP_SET" == true || "$FULL_TEMP_SET" == true || \
        "$MIN_DUTY_SET" == true || "$HYSTERESIS_SET" == true ]]; then
  PATCH_ARGUMENTS=()
  [[ "$GPIO_SET" == true ]] && PATCH_ARGUMENTS+=(--gpio "$GPIO")
  [[ "$START_TEMP_SET" == true ]] && PATCH_ARGUMENTS+=(--start-temp "$START_TEMP")
  [[ "$FULL_TEMP_SET" == true ]] && PATCH_ARGUMENTS+=(--full-temp "$FULL_TEMP")
  [[ "$MIN_DUTY_SET" == true ]] && PATCH_ARGUMENTS+=(--min-duty "$MIN_DUTY")
  [[ "$HYSTERESIS_SET" == true ]] && PATCH_ARGUMENTS+=(--hysteresis "$HYSTERESIS")
  runuser -u "$WEB_USER" -- env PYTHONPATH="$STATE_PYTHONPATH" \
    python3 -m app.config "$CONFIG_FILE" --patch "${PATCH_ARGUMENTS[@]}"
else
  runuser -u "$WEB_USER" -- env PYTHONPATH="$STATE_PYTHONPATH" \
    python3 -m app.config "$CONFIG_FILE" --canonicalize
fi
EFFECTIVE_GPIO="$(PYTHONPATH="$STATE_PYTHONPATH" python3 -c 'import sys; from app.config import read_fan_config; print(read_fan_config(sys.argv[1])["gpio"])' "$CONFIG_FILE")"
if [[ "$EFFECTIVE_GPIO" == 14 ]] && grep -qE '(^| )console=(serial0|ttyAMA0|ttyS0),' /proc/cmdline; then
  echo "GPIO14 is used by the serial console. Run 'sudo raspi-config nonint do_serial 1', reboot, then retry." >&2
  false
fi

if [[ ! -e "$PI_LIST_FILE" ]]; then
  old_list="$DATA_DIR/pi_list.json"
  if [[ -f "$old_list" && ! -L "$old_list" ]]; then
    PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.state "$old_list"
    install -o "$WEB_USER" -g "$SHARED_GROUP" -m 0640 "$old_list" "$PI_LIST_FILE"
  else
    install -o "$WEB_USER" -g "$SHARED_GROUP" -m 0640 /dev/null "$PI_LIST_FILE"
    printf '[]\n' >"$PI_LIST_FILE"
    chown "$WEB_USER:$SHARED_GROUP" "$PI_LIST_FILE"
  fi
fi
PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.state "$PI_LIST_FILE"
chown "$WEB_USER:$SHARED_GROUP" "$CONFIG_FILE" "$PI_LIST_FILE"
chmod 0640 "$CONFIG_FILE" "$PI_LIST_FILE"

if [[ ! -e "$AUTH_FILE" ]]; then
  NEW_PASSWORD="$(PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.auth create "$AUTH_FILE")"
  chown root:"$AUTH_GROUP" "$AUTH_FILE"
  chmod 0640 "$AUTH_FILE"
elif [[ "$RESET_PASSWORD" == true ]]; then
  NEW_PASSWORD="$(PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.auth reset "$AUTH_FILE")"
  chown root:"$AUTH_GROUP" "$AUTH_FILE"
  chmod 0640 "$AUTH_FILE"
elif [[ ! -f "$AUTH_FILE" ]]; then
  echo "Authentication path is not a regular file." >&2
  false
fi
PYTHONPATH="$STATE_PYTHONPATH" python3 -m app.auth validate "$AUTH_FILE"
AUTH_GROUP_ID="$(getent group "$AUTH_GROUP" | cut -d: -f3)"
if [[ "$(stat -c %u "$AUTH_FILE")" != 0 ]] || \
   [[ "$(stat -c %g "$AUTH_FILE")" != "$AUTH_GROUP_ID" ]] || \
   [[ "$(stat -c %a "$AUTH_FILE")" != 640 ]]; then
  echo "Authentication file has unsafe ownership or permissions." >&2
  false
fi

umask 077
ENV_TEMP="$(mktemp "$ETC_DIR/.web.env.XXXXXX")"
FAN_ENV_TEMP="$(mktemp "$ETC_DIR/.fan.env.XXXXXX")"
printf 'PIFAN_BIND=%s\nPIFAN_PORT=%s\n' "$BIND_ADDRESS" "$PORT" >"$ENV_TEMP"
printf 'PIFAN_GPIO=%s\n' "$EFFECTIVE_GPIO" >"$FAN_ENV_TEMP"
chown root:root "$ENV_TEMP" "$FAN_ENV_TEMP"
chmod 0600 "$ENV_TEMP" "$FAN_ENV_TEMP"
mv -f "$ENV_TEMP" "$ENV_FILE"
mv -f "$FAN_ENV_TEMP" "$FAN_ENV_FILE"
umask 022

if [[ -d "$PREVIOUS_DIR" ]]; then safe_remove "$PREVIOUS_DIR"; fi
if [[ -d "$INSTALL_DIR" ]]; then mv "$INSTALL_DIR" "$PREVIOUS_DIR"; fi
mv "$STAGE_DIR" "$INSTALL_DIR"
STAGE_DIR=""
SWAPPED=true

install -o root -g root -m 0644 "$SOURCE_DIR/systemd/pifandashboard-fan.service" /etc/systemd/system/
install -o root -g root -m 0644 "$SOURCE_DIR/systemd/pifandashboard-web.service" /etc/systemd/system/
install -d -o root -g root -m 0755 "$WEB_DROPIN_DIR"
NETWORK_TEMP="$(mktemp "$WEB_DROPIN_DIR/.network.conf.XXXXXX")"
printf '[Service]\n%s\n' "$LAN_ALLOW_LINES" >"$NETWORK_TEMP"
chown root:root "$NETWORK_TEMP"
chmod 0644 "$NETWORK_TEMP"
mv -f "$NETWORK_TEMP" "$NETWORK_DROPIN"
systemctl daemon-reload
systemctl enable pifandashboard-fan.service pifandashboard-web.service >/dev/null
systemctl restart pifandashboard-fan.service pifandashboard-web.service

PI_IP="$LAN_IP"
[[ "$BIND_ADDRESS" == "127.0.0.1" ]] && PI_IP=127.0.0.1
[[ "$BIND_ADDRESS" != "0.0.0.0" ]] && PI_IP="$BIND_ADDRESS"
PI_NAME="$(hostname -s | cut -c1-40)"
runuser -u "$WEB_USER" -- env PYTHONPATH="$INSTALL_DIR" \
  python3 -m app.state "$PI_LIST_FILE" --update-self --ip "$PI_IP" --port "$PORT" --name "$PI_NAME"

HEALTH_HOST=127.0.0.1
[[ "$BIND_ADDRESS" != "0.0.0.0" && "$BIND_ADDRESS" != "127.0.0.1" ]] && HEALTH_HOST="$BIND_ADDRESS"
for attempt in $(seq 1 20); do
  if systemctl is-active --quiet pifandashboard-fan.service pifandashboard-web.service && \
     curl -fsS --max-time 2 "http://$HEALTH_HOST:$PORT/health" >/dev/null && \
     curl -fsS --max-time 2 "http://$HEALTH_HOST:$PORT/status" >/dev/null; then
    break
  fi
  if [[ "$attempt" == 20 ]]; then
    echo "Dashboard health check failed." >&2
    false
  fi
  sleep 1
done

if [[ "$WAS_ENABLED" == false && -d "$PREVIOUS_DIR" ]]; then
  systemctl disable pifandashboard-fan.service pifandashboard-web.service >/dev/null
fi
if [[ "$WAS_ACTIVE" == false && -d "$PREVIOUS_DIR" ]]; then
  systemctl stop pifandashboard-web.service pifandashboard-fan.service
fi

INSTALL_COMPLETE=true
trap - ERR INT TERM
rm -rf -- "$BACKUP_DIR"

echo ""
echo "Pi Fan Dashboard installed successfully."
if [[ "$BIND_ADDRESS" == "127.0.0.1" ]]; then
  echo "URL on the Pi: http://127.0.0.1:$PORT/"
  echo "Remote access: ssh -L $PORT:127.0.0.1:$PORT <user>@$LAN_IP, then open http://127.0.0.1:$PORT/"
else
  echo "URL: http://$PI_IP:$PORT/"
fi
if [[ -n "$NEW_PASSWORD" ]]; then
  echo "Dashboard password (shown once): $NEW_PASSWORD"
  echo "Store it safely. Reset it later with: sudo /usr/bin/python3 /opt/pifandashboard/app/auth.py reset $AUTH_FILE"
else
  echo "Existing dashboard password preserved."
fi
echo "Selected port is stored in $ENV_FILE and will be preserved on upgrades."
echo "Pi-hole, nginx, UFW, and /var/www were not changed."
