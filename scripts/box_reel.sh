#!/usr/bin/env bash
# Box-side Reel: capture from the box's signed-in TradingView (saved layout with
# EMA 20/50) over Chrome's local DevTools port, render, QA, and (with --publish)
# hand a PASS to publish-artifact.yml. No cookies or profile files are read or exported.
#   scripts/box_reel.sh close [--publish] [OUT_DIR]
set -euo pipefail
cd "$(dirname "$0")/.."
kind="${1:?kind: close|ta|news}"; shift
publish=0
if [ "${1:-}" = "--publish" ]; then publish=1; shift; fi
export REALCHART_CDP="${REALCHART_CDP:-http://127.0.0.1:9226}"
export REALCHART_TV_LAYOUT="${REALCHART_TV_LAYOUT:-ckfLnl7v}"
export REALCHART_NO_ALERT="${REALCHART_NO_ALERT:-1}"
export GEMINI_SECRETS_DIR="${GEMINI_SECRETS_DIR:-/home/box/.secrets}"
export REALCHART_SERIES_FILE="${REALCHART_SERIES_FILE:-/home/box/agent-data/pkp/series.json}"
day="$(TZ=Asia/Kolkata date +%F)"
out="${1:-/workspace/paise-review/box-$kind-$day}"
mkdir -p "$out" "$(dirname "$REALCHART_SERIES_FILE")"
git pull -q --ff-only || echo "[box] git pull failed; using the checked-out code" >&2
curl -fsS "$REALCHART_CDP/json/version" >/dev/null || { echo "HOLD: box Chrome DevTools not reachable at $REALCHART_CDP" >&2; exit 2; }
set +e
python3 -m realchart run --kind "$kind" --out "$out"
code=$?
set -e
if [ "$code" != "0" ]; then
  echo "HOLD: $kind build exit $code; see $out/qa.json" >&2
  exit "$code"
fi
if [ "$publish" = "1" ]; then
  python3 scripts/box_publish.py "$out"
fi
