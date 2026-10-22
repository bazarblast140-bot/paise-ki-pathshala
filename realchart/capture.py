"""TradingView when the runner can see it. Otherwise a labeled NSE chart."""
from __future__ import annotations

import re
from pathlib import Path

BOT_WALL = (
    "captcha",
    "unusual traffic",
    "verify you are",
    "access denied",
    "robot",
    "are you a human",
)
COOKIE_LABELS = ("Accept all", "Accept All", "Allow all", "I agree", "Agree and continue", "Accept")
FALLBACK_LABEL = "Chart: NSE data (yfinance)"
FALLBACK_LABEL_5M = "Chart: NSE data (yfinance, 5m)"
YF_PLOT = {"left": 40, "top": 80, "width": 1000, "height": 900}


def walled(text):
    low = " ".join(str(text or "").lower().split())
    return any(phrase in low for phrase in BOT_WALL)


import os

# Box mode: drive the box's own signed-in Chrome over its local DevTools port and
# open the saved layout (EMA 20/50 live there). No cookie or profile is read or
# copied; only a new tab is opened and closed again.
CDP_URL = os.environ.get("REALCHART_CDP", "")
TV_LAYOUT = os.environ.get("REALCHART_TV_LAYOUT", "")


def chart_url(symbol, interval="D"):
    if TV_LAYOUT:
        return f"https://www.tradingview.com/chart/{TV_LAYOUT}/?symbol={symbol}&interval={interval}"
    return f"https://www.tradingview.com/chart/?symbol={symbol}&interval={interval}&theme=dark"


def technicals_url(symbol):
    return f"https://www.tradingview.com/symbols/{str(symbol).replace(':', '-')}/technicals/"


def draw_yf_chart(bars, dest, symbol, label=None, tag="1D"):
    """Candles from the OHLC we already computed. The label says it is not TradingView."""
    from PIL import Image, ImageDraw, ImageFont
    rows = list(bars or [])[-80:]
    image = Image.new("RGB", (1080, 1080), (22, 26, 32))
    draw = ImageDraw.Draw(image)
    font = _font(28)
    draw.text((24, 16), f"{symbol}  {tag}", fill=(230, 236, 240), font=font)
    draw.text((24, 1020), label or FALLBACK_LABEL, fill=(255, 214, 10), font=font)
    if len(rows) < 2:
        image.save(dest)
        return dest
    highs = [float(row["h"]) for row in rows]
    lows = [float(row["l"]) for row in rows]
    top, bot = max(highs), min(lows)
    span = max(1.0, top - bot)
    left, width, plot_top, plot_h = YF_PLOT["left"], YF_PLOT["width"], YF_PLOT["top"], YF_PLOT["height"]
    step = width / len(rows)
    for index, row in enumerate(rows):
        x = left + index * step + step / 2
        y_high = plot_top + (top - float(row["h"])) / span * plot_h
        y_low = plot_top + (top - float(row["l"])) / span * plot_h
        y_open = plot_top + (top - float(row["o"])) / span * plot_h
        y_close = plot_top + (top - float(row["c"])) / span * plot_h
        color = (34, 181, 60) if float(row["c"]) >= float(row["o"]) else (240, 30, 50)
        draw.line((x, y_high, x, y_low), fill=color, width=2)
        draw.rectangle((x - 4, min(y_open, y_close), x + 4, max(y_open, y_close) or min(y_open, y_close) + 1), fill=color)
    image.save(dest)
    return dest


def _font(size):
    from realchart.fonts import font
    return font(size)


def parse_technicals(text):
    """EMA20, EMA50 and RSI14 from the public Technicals page, when the text has them."""
    import re
    patterns = {
        "ema20": r"Exponential Moving Average \(20\)\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
        "ema50": r"Exponential Moving Average \(50\)\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
        "rsi14": r"Relative Strength Index \(14\)\s*([0-9]+(?:\.[0-9]+)?)",
    }
    found = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, str(text or ""), re.I)
        if not match:
            return {}
        found[key] = float(match.group(1).replace(",", ""))
    return found


LEGEND = re.compile(
    r"O\s*([0-9][0-9,]*\.?[0-9]*)\s*H\s*([0-9][0-9,]*\.?[0-9]*)\s*L\s*([0-9][0-9,]*\.?[0-9]*)\s*C\s*([0-9][0-9,]*\.?[0-9]*)"
)
# Daily legend must equal the session bar. A stale or cached chart differs by
# far more than this; the 5m legend is checked against the last 5m bar.
LEGEND_TOL = 0.0015


def ema_status(text):
    """Which of TradingView's own EMA 20 / EMA 50 studies the legend shows with a value.

    An EMA the chart cannot compute (e.g. an anonymous view-only layout) shows its
    title with no number, so a title alone does not count."""
    found = {}
    for length in (20, 50):
        hit = re.search(rf"EMA\s*{length}\b(?:\s*close)?\s*([0-9][0-9,]*\.[0-9]+)", str(text or ""))
        found[f"ema{length}"] = bool(hit)
    return found


def parse_legend(text):
    """O/H/L/C of the last bar from the TradingView legend text (mouse off the chart)."""
    blob = str(text or "")
    start = blob.find("Nifty 50 Index")
    match = LEGEND.search(blob[start:] if start >= 0 else blob)
    if not match:
        return {}
    o, h, l, c = (float(group.replace(",", "")) for group in match.groups())
    return {"o": o, "h": h, "l": l, "c": c}


def _legend_matches(legend, bar):
    for key in ("o", "h", "l", "c"):
        want = bar.get(key)
        if want is None:
            continue
        got = legend.get(key)
        if got is None or abs(float(got) - float(want)) > abs(float(want)) * LEGEND_TOL:
            return f"legend {key.upper()} {got} vs {float(want):.2f}"
    return ""


def verify_last_candle(legend, expect):
    """Empty when the legend's last bar is a bar of the expected session, else the reason.

    expect: {"date": "YYYY-MM-DD", "o"/"h"/"l"/"c": ...} for the daily bar, or
    {"date": ..., "bars": [...]} for intraday, where Yahoo adds closing-tick
    bars after TradingView's last 5m bar, so any of the session's last bars
    may match. Every given field must match within LEGEND_TOL, so a chart that
    stopped in August (cached, logged-out snapshot, wrong symbol) is refused.
    """
    if not expect:
        return ""
    if not legend:
        return "legend unreadable, last candle date unknown"
    candidates = list(expect.get("bars") or []) or [expect]
    reasons = [_legend_matches(legend, bar) for bar in candidates]
    if any(not reason for reason in reasons):
        return ""
    return f"last candle is not {expect.get('date')}: {reasons[-1]}"


def _browser_user_agent(browser):
    """Match the installed Chrome and drop the Headless token runners get blocked for."""
    version = str(getattr(browser, "version", "") or "131.0.0.0").split()[-1]
    return (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{version} Safari/537.36"
    )


def _dismiss_cookies(page):
    for label in COOKIE_LABELS:
        try:
            button = page.get_by_role("button", name=label)
            if button.count() == 0:
                continue
            first = button.first
            if first.is_visible():
                first.click(timeout=2000)
                return label
        except Exception:  # noqa: BLE001 — a missing banner is not a failed chart
            continue
    return ""


def _wait_chart(page):
    page.wait_for_selector("canvas, [data-name='legend-source-item']", state="visible", timeout=25000)
    page.wait_for_timeout(1500)


def _keep_capture_failure(dest, shot, reason):
    """Keep the TradingView page and the error. The yfinance chart then replaces dest."""
    import shutil
    dest = Path(dest)
    note = dest.with_name(f"{dest.stem}-capture.txt")
    image = dest.with_name(f"{dest.stem}-capture.png")
    source = Path(shot.get("path") or dest)
    if source.is_file() and source.resolve() != image.resolve():
        shutil.copyfile(source, image)
    text = " ".join(str(shot.get("text") or "").split())[:800]
    note.write_text(f"reason: {reason}\nerror: {text}\n", encoding="utf-8")
    return {"note": str(note), "image": str(image) if image.is_file() else ""}


def _clear_capture_failure(dest):
    dest = Path(dest)
    for name in (f"{dest.stem}-capture.txt", f"{dest.stem}-capture.png"):
        path = dest.with_name(name)
        if path.is_file():
            path.unlink()


def capture_chart(symbol, dest, bars, opener=None, interval="D", fallback_label=None, allow_fallback=True, expect=None):
    """Try the public TradingView chart. A real block becomes the yfinance chart.

    With expect, a TradingView shot whose last candle is not that session is
    refused (source "stale"); the build holds on it. It never falls back.
    """
    if opener is None:
        def opener(sym, path, _interval=interval):
            return _playwright_chart(sym, path, interval=_interval)
    try:
        shot = opener(symbol, dest)
    except Exception as exc:  # noqa: BLE001 — the runner must still render
        shot = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "text": str(exc)}
    text = f"{shot.get('text') or ''} {shot.get('reason') or ''}"
    label = fallback_label or (FALLBACK_LABEL_5M if str(interval) in ("5", "15") else FALLBACK_LABEL)
    if shot.get("ok") and not walled(text):
        stale = verify_last_candle(shot.get("legend") or {}, expect)
        if stale:
            kept = _keep_capture_failure(dest, shot, stale)
            return {
                "source": "stale",
                "path": str(dest),
                "url": chart_url(symbol, interval),
                "label": "TradingView",
                "reason": stale,
                "interval": str(interval),
                "legend": shot.get("legend") or {},
                "capture_note": kept["note"],
                "technicals": {},
            }
        _clear_capture_failure(dest)
        return {
            "legend": shot.get("legend") or {},
            "ema": shot.get("ema") or ema_status(shot.get("text") or ""),
            "drawings_visible": bool(shot.get("drawings_visible")),
            "last_candle_date": (expect or {}).get("date") or "",
            "source": "tradingview",
            "path": shot.get("path") or str(dest),
            "url": chart_url(symbol, interval),
            "captured_at": shot.get("captured_at") or "",
            "label": "TradingView",
            "reason": "",
            "interval": str(interval),
            "technicals": shot.get("technicals") or {},
        }
    reason = "bot wall" if walled(text) else (shot.get("reason") or "chart capture failed")
    kept = _keep_capture_failure(dest, shot, reason)
    if not allow_fallback:
        return {
            "source": "blocked",
            "path": str(dest),
            "url": chart_url(symbol, interval),
            "label": label,
            "reason": reason,
            "interval": str(interval),
            "capture_note": kept["note"],
            "capture_image": kept["image"],
            "technicals": {},
        }
    tag = "5m" if str(interval) == "5" else ("15m" if str(interval) == "15" else "1D")
    draw_yf_chart(bars, dest, symbol, label=label, tag=tag)
    drawn = {
        "last_candle_date": str((list(bars or [])[-1:] or [{}])[0].get("date") or "")[:10],
        "source": "yfinance",
        "path": str(dest),
        "url": chart_url(symbol, interval),
        "captured_at": "",
        "label": label,
        "reason": reason,
        "interval": str(interval),
        "capture_note": kept["note"],
        "capture_image": kept["image"],
        "technicals": {},
    }
    if str(interval) in ("5", "15"):
        drawn["plot"] = dict(YF_PLOT)
        drawn["scale_bars"] = list(bars or [])
    return drawn


def capture_intraday(symbol, directory, bars_5m, bars_15m=None, opener=None, expect5=None, expect15=None):
    """5-minute TradingView first. 15-minute if that capture fails. yfinance 5m only if both are blocked."""
    directory = Path(directory)
    shot5 = capture_chart(
        symbol, directory / "chart-5m.png", bars_5m, opener=opener, interval="5",
        fallback_label=FALLBACK_LABEL_5M, allow_fallback=False, expect=expect5,
    )
    if shot5.get("source") == "stale":
        return shot5
    if shot5.get("source") == "tradingview":
        shot5["scale_bars"] = list(bars_5m or [])
        return shot5
    shot15 = capture_chart(
        symbol, directory / "chart-15m.png", bars_15m or bars_5m, opener=opener, interval="15",
        fallback_label=FALLBACK_LABEL_5M, allow_fallback=False, expect=expect15,
    )
    if shot15.get("source") == "stale":
        return shot15
    if shot15.get("source") == "tradingview":
        shot15["interval"] = "15"
        shot15["reason"] = f"5m: {shot5.get('reason') or 'capture failed'}"
        shot15["scale_bars"] = list(bars_15m or bars_5m or [])
        return shot15
    dest = directory / "chart-5m.png"
    draw_yf_chart(bars_5m, dest, symbol, label=FALLBACK_LABEL_5M, tag="5m")
    return {
        "last_candle_date": str((list(bars_5m or [])[-1:] or [{}])[0].get("date") or "")[:10],
        "source": "yfinance",
        "path": str(dest),
        "url": chart_url(symbol, "5"),
        "label": FALLBACK_LABEL_5M,
        "reason": f"5m: {shot5.get('reason') or 'capture failed'}; 15m: {shot15.get('reason') or 'capture failed'}",
        "interval": "5",
        "plot": dict(YF_PLOT),
        "scale_bars": list(bars_5m or []),
    }


def crosshair_visible(path):
    """True when a long dashed grey line (TradingView's crosshair) crosses the shot.

    The crosshair is ~#797979 dashes covering ~55% of a row or column; grid
    lines are near-black and price lines are coloured. Full resolution: a
    thumbnail blurs the 2px dashes away.
    """
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        rgb = np.asarray(image.convert("RGB")).astype(int)
    height, width = rgb.shape[:2]
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    grey = (rgb[..., 0] >= 90) & (rgb[..., 0] <= 200) & (spread <= 14)
    # Ignore the price axis on the right and the time axis at the bottom.
    plot = grey[: int(height * 0.93), : int(width * 0.85)]
    if plot.size == 0:
        return False
    rows = plot.mean(axis=1)
    cols = plot[int(height * 0.06):].mean(axis=0)
    return bool(rows.max() > 0.35 or cols.max() > 0.35)


def chart_painted(path):
    """True when the shot has candle colors. A WebGL chart that never paints is not TradingView."""
    from PIL import Image
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = rgb.load()
        green = red = samples = 0
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                r, g, b = pixels[x, y]
                samples += 1
                if g > r + 25 and g > 60:
                    green += 1
                elif r > g + 25 and r > 60:
                    red += 1
    if samples == 0:
        return False
    return (green + red) / samples >= 0.01


SAVE_PATHS = re.compile(r"save|charts-storage|/chart/[^/]+/(content|update)|layout|drawing|study_templates", re.I)


def _block_saves(route):
    request = route.request
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and "tradingview.com" in request.url \
            and SAVE_PATHS.search(request.url):
        return route.abort()
    return route.continue_()


def layout_drawings_visible(png):
    """True when the saved layout's own blue horizontal lines (TradingView #2962FF) cross the chart."""
    from PIL import Image
    with Image.open(png) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    span = range(int(width * 0.05), int(width * 0.8), 4)
    for y in range(0, height, 1):
        blue = sum(1 for x in span if _is_tv_blue(image.getpixel((x, y))))
        if blue > 0.6 * len(span):
            return True
    return False


def _is_tv_blue(px):
    r, g, b = px
    return b > 200 and r < 90 and 70 < g < 130


class _BoxTab:
    """Stands in for a browser: closing it closes only our tab in the box Chrome."""
    def __init__(self, context):
        self.context = context
        self.pages = []

    def close(self):
        for page in self.pages:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass


class _BoxContext:
    def __init__(self, context, tab):
        self._context, self._tab = context, tab

    def new_page(self):
        page = self._context.new_page()
        self._tab.pages.append(page)
        # Nothing this tab does may be saved to the account or the saved layout.
        page.route("**/*", _block_saves)
        page.set_viewport_size({"width": 1280, "height": 1100})
        try:
            cdp = self._context.new_cdp_session(page)
            cdp.send("Emulation.setDeviceMetricsOverride",
                     {"width": 1280, "height": 1100, "deviceScaleFactor": 2, "mobile": False})
            cdp.send("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Kolkata"})
        except Exception:  # noqa: BLE001
            pass
        return page


def _open_browser(play):
    if CDP_URL:
        browser = play.chromium.connect_over_cdp(CDP_URL)
        tab = _BoxTab(browser.contexts[0])
        return tab, _BoxContext(browser.contexts[0], tab)
    browser = play.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-webgl",
            "--ignore-gpu-blocklist",
        ],
        ignore_default_args=["--enable-automation"],
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 1100},
        device_scale_factor=2,
        color_scheme="dark",
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        user_agent=_browser_user_agent(browser),
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, context


ZOOM_NOTCHES = {"D": 4, "5": 2, "15": 2}
HIDE_UI = ("[class*='toast'],[class*='Toast'],[class*='floating-toolbar'],[class*='floatingToolbar'],"
           "[data-name='floating-toolbar'],[class*='promo'],[class*='Promo'],[data-dialog-name]{display:none!important}")
# Signed-in layouts pop promo cards (e.g. "Try AI Copilot"); hide any small
# fixed/absolute box whose text is a promo, without touching the chart.
HIDE_PROMOS = """() => {
  for (const el of document.querySelectorAll('div,section,aside')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 400) continue;
    if (/Copilot|Upgrade|Try (it|now)|free trial/i.test(t)) {
      const pos = getComputedStyle(el).position;
      if (pos === 'fixed' || pos === 'absolute') el.style.setProperty('display', 'none', 'important');
    }
  }
}"""


def _parking_spot(page, box, low=False):
    """Bottom-left corner of the page, below the drawing toolbar and outside the pane.

    Tried on 15 Oct: parking over the right widget bar or the time axis left
    TradingView's crosshair on the 5m chart; this corner cleared it on 1D and 5m.
    """
    view = page.viewport_size or {"width": 1280, "height": 1100}
    return 5, view["height"] - (4 if low else 10)


HIDE_ALL_JS = """(click) => {
    const button = [...document.querySelectorAll('[data-name="hide-all"] button')]
        .find(e => /(hide|show) all drawings/i.test(e.getAttribute('aria-label') || ''));
    if (!button) return 'missing';
    const label = button.getAttribute('aria-label') || '';
    const hidden = /show all drawings/i.test(label) || button.getAttribute('aria-pressed') === 'true';
    if (click && !hidden) button.click();
    return hidden ? 'hidden' : 'shown';
}"""


HIDE_ALL_API_JS = """() => {
    // View-only daily tabs have no drawing toolbar; use the chart widget's own
    // in-memory "hide all drawings" toggle. Nothing is removed or saved.
    try {
        const h = window.TradingViewApi && TradingViewApi.hideAllDrawingTools();
        if (!h) return 'missing';
        if (!h.value()) h.setValue(true);
        return h.value() ? 'hidden' : 'shown';
    } catch (e) { return 'missing'; }
}"""


def hide_drawings(page, tries=3):
    """Click TradingView's "Hide all drawings" toolbar toggle (this tab only). True once hidden.

    Never touches "Remove drawings" (data-name removeAllDrawingTools); the state is not saved.
    """
    try:
        page.wait_for_selector('[data-name="hide-all"] button', timeout=4000)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(tries):
        try:
            if page.evaluate(HIDE_ALL_JS, True) == "missing":
                if page.evaluate(HIDE_ALL_API_JS) == "hidden":
                    page.wait_for_timeout(800)
                    return True
                page.wait_for_timeout(1000)
                continue
            page.wait_for_timeout(800)
            if page.evaluate(HIDE_ALL_JS, False) == "hidden":
                return True
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(1000)
    return False


def _frame_chart(page, interval):
    """IST axis, zoom on the latest candles, mouse off the plot, then the chart pane only.

    The old full-page shot carried the watchlist and toolbars, so after a
    1040px thumbnail the six-month daily range read as an August chart.
    """
    if CDP_URL:
        # The saved layout carries Rajesh's own drawings (blue lines at old
        # levels). Hide them for this tab only with TradingView's own
        # "Hide all drawings" toolbar button; saves are blocked (_block_saves).
        # Keyboard shortcuts depended on window focus and stopped working (16-Oct).
        hide_drawings(page)
    try:
        zone = page.locator("[data-name='time-zone-menu']")
        if zone.count():
            zone.first.click(timeout=3000)
            page.wait_for_timeout(500)
            kolkata = page.get_by_text("Kolkata")
            if kolkata.count():
                kolkata.first.click(timeout=3000)
            page.wait_for_timeout(800)
    except Exception:  # noqa: BLE001 — the date check below still guards the data
        pass
    box = None
    try:
        box = page.locator(".chart-container").first.bounding_box()
    except Exception:  # noqa: BLE001
        box = None
    if box:
        # Keyboard zoom (Ctrl+Up) keeps the latest candle at the right edge.
        # Mouse-wheel zoom was ignored headless and left a hint toast.
        page.mouse.move(box["x"] + box["width"] - 140, box["y"] + box["height"] * 0.45)
        for _ in range(ZOOM_NOTCHES.get(str(interval), 0)):
            page.keyboard.press("Control+ArrowUp")
            page.wait_for_timeout(200)
        page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    # Leave over the right-hand panel: no crosshair, and the legend shows the
    # last bar instead of a hovered one.
    page.mouse.move(*_parking_spot(page, box), steps=15)
    page.wait_for_timeout(300)
    try:
        # Belt and braces: tell every chart canvas the pointer has left.
        page.evaluate(
            """() => { for (const c of document.querySelectorAll('.chart-container canvas')) {
                for (const t of ['mouseleave', 'mouseout', 'pointerleave', 'pointerout']) {
                    c.dispatchEvent(new MouseEvent(t, {bubbles: true}));
                } } }"""
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        page.add_style_tag(content=HIDE_UI)
        try:
            page.keyboard.press("Escape")
            page.evaluate(HIDE_PROMOS)
        except Exception:  # noqa: BLE001
            pass
        page.evaluate(
            """() => { for (const el of document.querySelectorAll('div,span')) {
                const text = el.textContent || '';
                if (text.length < 160 && /Press and hold/.test(text) && !el.querySelector('canvas')) {
                    el.style.visibility = 'hidden';
                } } }"""
        )
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(1200)
    return box


def _playwright_chart(symbol, dest, interval="D"):
    from playwright.sync_api import sync_playwright
    from realchart.session import now_ist
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = ""
    reason = ""
    ok = False
    tech = {}
    legend = {}
    with sync_playwright() as play:
        browser, context = _open_browser(play)
        page = context.new_page()
        try:
            page.goto(chart_url(symbol, interval), timeout=45000, wait_until="domcontentloaded")
            _dismiss_cookies(page)
            try:
                _wait_chart(page)
                ok = True
            except Exception as exc:  # noqa: BLE001
                reason = f"canvas missing: {type(exc).__name__}: {exc}"
            clip = _frame_chart(page, interval) if ok else None
            text = (page.inner_text("body") or "")[:4000]
            legend = parse_legend(text)
            if clip:
                page.screenshot(path=str(dest), clip=clip)
            else:
                page.screenshot(path=str(dest))
            if walled(text):
                ok = False
                reason = "bot wall"
            elif ok and not chart_painted(dest):
                ok = False
                reason = "chart did not paint"
            elif ok and crosshair_visible(dest):
                # One retry with the pointer parked again, then refuse the shot.
                page.mouse.move(*_parking_spot(page, None, low=True), steps=10)
                page.wait_for_timeout(800)
                page.screenshot(path=str(dest), clip=clip) if clip else page.screenshot(path=str(dest))
                if crosshair_visible(dest):
                    ok = False
                    reason = "crosshair visible in capture"
            elif ok and str(interval) == "D":
                page.goto(technicals_url(symbol), timeout=45000, wait_until="domcontentloaded")
                _dismiss_cookies(page)
                page.wait_for_timeout(2000)
                tech_text = page.inner_text("body") or ""
                if not walled(tech_text):
                    tech = {"text": tech_text[:4000]}
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"[:500]
            try:
                text = (page.inner_text("body") or "")[:4000]
                page.screenshot(path=str(dest))
            except Exception:  # noqa: BLE001
                pass
        finally:
            browser.close()
    if walled(text):
        ok = False
        reason = "bot wall"
    return {
        "ok": ok,
        "reason": reason,
        "text": text,
        "path": str(dest),
        "captured_at": now_ist().strftime("%Y-%m-%d %H:%M IST"),
        "technicals": tech,
        "legend": legend,
        "ema": ema_status(text),
        "drawings_visible": bool(CDP_URL) and Path(dest).is_file() and layout_drawings_visible(dest),
    }


def capture_article(url, dest, opener=None):
    if opener is None:
        opener = _playwright_article
    try:
        return opener(url, dest)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def _playwright_article(url, dest):
    from playwright.sync_api import sync_playwright
    from realchart.session import now_ist
    with sync_playwright() as play:
        browser = play.chromium.launch()
        device = play.devices["iPhone 13"]
        context = browser.new_context(**{**device, "device_scale_factor": 3, "locale": "en-IN", "timezone_id": "Asia/Kolkata"})
        page = context.new_page()
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        page.add_style_tag(content="iframe,[class*=advert],[id*=google_ads]{visibility:hidden!important}")
        text = page.inner_text("body")
        page.screenshot(path=str(dest), full_page=False)
        browser.close()
    if walled(text):
        return {"ok": False, "reason": "bot wall", "text": text[:500]}
    return {"ok": True, "path": str(dest), "captured_at": now_ist().strftime("%Y-%m-%d %H:%M IST")}
