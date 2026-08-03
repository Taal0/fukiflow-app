#!/usr/bin/env bash
# Run on the VPS as root from /opt/fukiflow after repos are deployed.
set -euo pipefail

ROOT=/opt/fukiflow

# Stop any hand-started uvicorn so ports are free
pkill -f 'uvicorn app.main:app' 2>/dev/null || true
sleep 1

install -m 644 "$ROOT/fukiflow-vision/deploy/systemd/fukiflow-vision.service" \
  /etc/systemd/system/fukiflow-vision.service
install -m 644 "$ROOT/fukiflow-app/deploy/systemd/fukiflow-app.service" \
  /etc/systemd/system/fukiflow-app.service

systemctl daemon-reload
systemctl enable --now fukiflow-vision.service
systemctl enable --now fukiflow-app.service
systemctl enable nginx

systemctl --no-pager --full status fukiflow-vision.service fukiflow-app.service nginx | cat
echo
curl -sS http://127.0.0.1:8001/health; echo
curl -sS http://127.0.0.1:8000/health; echo
curl -sS http://127.0.0.1:8000/api/chapters; echo
