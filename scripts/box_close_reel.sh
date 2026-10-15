#!/usr/bin/env bash
# Box-side close Reel: capture daily + 5m NIFTY from the box's own signed-in
# TradingView (saved layout with EMA 20/50) over Chrome's local DevTools port,
# then render. No cookies or profile files are read, copied or exported.
# Usage: scripts/box_close_reel.sh [OUT_DIR]   (env: REALCHART_CDP, REALCHART_TV_LAYOUT)
set -euo pipefail
cd "$(dirname "$0")/.."
export REALCHART_CDP="${REALCHART_CDP:-http://127.0.0.1:9226}"
export REALCHART_TV_LAYOUT="${REALCHART_TV_LAYOUT:-ckfLnl7v}"
export GEMINI_SECRETS_DIR="${GEMINI_SECRETS_DIR:-/home/box/.secrets}"
export REALCHART_NO_ALERT="${REALCHART_NO_ALERT:-1}"
day="$(TZ=Asia/Kolkata date +%F)"
out="${1:-/workspace/paise-review/box-close-$day}"
mkdir -p "$out"
curl -fsS "$REALCHART_CDP/json/version" >/dev/null || { echo "box Chrome DevTools not reachable at $REALCHART_CDP" >&2; exit 2; }
python3 -m realchart run --kind close --out "$out"
