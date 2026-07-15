#!/bin/bash
set -euo pipefail

PURGE=false
if [[ ${1:-} == "--purge" ]]; then
  PURGE=true
  shift
fi
if [[ $# -ne 0 ]]; then
  echo "Usage: sudo ./uninstall.sh [--purge]" >&2
  exit 2
fi
if [[ ${EUID} -ne 0 ]]; then
  echo "Run this uninstaller with sudo." >&2
  exit 1
fi

safe_remove_tree() {
  case "$1" in
    /opt/pifandashboard|/opt/pifandashboard.previous|/var/lib/pifandashboard|/etc/pifandashboard|\
    /etc/systemd/system/pifandashboard-web.service.d|/etc/systemd/system/pifandashboard-fan.service.d) ;;
    *) echo "Refusing unexpected removal path: $1" >&2; exit 1 ;;
  esac
  if command -v findmnt >/dev/null 2>&1 && findmnt -rn --mountpoint "$1" >/dev/null 2>&1; then
    echo "Refusing to remove mounted filesystem: $1" >&2
    exit 1
  fi
  rm -rf -- "$1"
}

systemctl disable --now pifandashboard-web.service pifandashboard-fan.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/pifandashboard-web.service /etc/systemd/system/pifandashboard-fan.service
safe_remove_tree /etc/systemd/system/pifandashboard-web.service.d
safe_remove_tree /etc/systemd/system/pifandashboard-fan.service.d
systemctl daemon-reload
systemctl reset-failed pifandashboard-web.service pifandashboard-fan.service >/dev/null 2>&1 || true

safe_remove_tree /opt/pifandashboard
safe_remove_tree /opt/pifandashboard.previous

if [[ "$PURGE" == true ]]; then
  if [[ -r /dev/tty ]]; then
    read -r -p "Permanently delete the fan curve, Pi list, and dashboard password? [y/N] " answer </dev/tty
  else
    answer=n
  fi
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    safe_remove_tree /var/lib/pifandashboard
    safe_remove_tree /etc/pifandashboard
    userdel pifandashboard-web >/dev/null 2>&1 || true
    userdel pifandashboard-fan >/dev/null 2>&1 || true
    if entry="$(getent passwd pifandashboard 2>/dev/null)"; then
      legacy_uid="$(printf '%s' "$entry" | cut -d: -f3)"
      legacy_home="$(printf '%s' "$entry" | cut -d: -f6)"
      legacy_shell="$(printf '%s' "$entry" | cut -d: -f7)"
      if [[ "$legacy_uid" -lt 1000 && "$legacy_home" == /nonexistent && "$legacy_shell" == */nologin ]] && \
         ! pgrep -u pifandashboard >/dev/null 2>&1; then
        userdel pifandashboard >/dev/null 2>&1 || true
      fi
    fi
    groupdel pifandashboard-auth >/dev/null 2>&1 || true
    groupdel pifandashboard >/dev/null 2>&1 || true
    echo "Persistent dashboard data removed."
  else
    echo "Purge cancelled; persistent dashboard data was preserved."
  fi
else
  echo "Fan curve, Pi list, port, and password were preserved."
fi

echo "Pi Fan Dashboard removed. Pi-hole, nginx, UFW, and /var/www were not changed."
