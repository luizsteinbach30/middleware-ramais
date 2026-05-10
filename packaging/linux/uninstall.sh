#!/usr/bin/env bash
# Removes the Middleware USCall Monitor service.
# Pass --purge to also delete /var/lib/middleware-monitor (DB + backups).
set -euo pipefail
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

if [[ "$EUID" -ne 0 ]]; then echo "Run as root."; exit 1; fi

systemctl disable --now middleware-monitor.service 2>/dev/null || true
systemctl disable --now middleware-monitor-updater.timer 2>/dev/null || true
rm -f /etc/systemd/system/middleware-monitor.service \
      /etc/systemd/system/middleware-monitor-updater.service \
      /etc/systemd/system/middleware-monitor-updater.timer
systemctl daemon-reload

rm -rf /opt/middleware-monitor

if [[ $PURGE -eq 1 ]]; then
  rm -rf /var/lib/middleware-monitor /etc/middleware-monitor
  userdel mmonitor 2>/dev/null || true
  userdel mmupdater 2>/dev/null || true
fi

echo "Removed."
