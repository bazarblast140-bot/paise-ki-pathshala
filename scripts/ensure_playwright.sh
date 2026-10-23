#!/usr/bin/env bash
# Pre-build check (23-Oct-2026): Playwright's chromium went missing on 22 and 23 Oct.
# Keep browsers in a persistent box path and reinstall when a headless launch fails.
set -uo pipefail
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/home/box/agent-data/pkp/ms-playwright}"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"
check() {
  python3 - <<'PY' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch().close()
PY
}
if check; then echo "[box] playwright chromium OK ($PLAYWRIGHT_BROWSERS_PATH)"; exit 0; fi
echo "[box] playwright chromium missing; reinstalling into $PLAYWRIGHT_BROWSERS_PATH" >&2
python3 -m playwright install chromium >&2 || python3 -m playwright install --with-deps chromium >&2
check && { echo "[box] playwright chromium reinstalled"; exit 0; }
echo "HOLD: playwright chromium still not launchable after reinstall" >&2
exit 3
