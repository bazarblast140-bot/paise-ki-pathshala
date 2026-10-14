"""TradingView when the runner can see it. Otherwise a labeled NSE chart."""
from __future__ import annotations

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


def chart_url(symbol, interval="D"):
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
    from PIL import ImageFont
    for path in (
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


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


def capture_chart(symbol, dest, bars, opener=None, interval="D", fallback_label=None, allow_fallback=True):
    """Try the public TradingView chart. A real block becomes the yfinance chart."""
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
        _clear_capture_failure(dest)
        return {
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


def capture_intraday(symbol, directory, bars_5m, bars_15m=None, opener=None):
    """5-minute TradingView first. 15-minute if that capture fails. yfinance 5m only if both are blocked."""
    directory = Path(directory)
    shot5 = capture_chart(
        symbol, directory / "chart-5m.png", bars_5m, opener=opener, interval="5",
        fallback_label=FALLBACK_LABEL_5M, allow_fallback=False,
    )
    if shot5.get("source") == "tradingview":
        shot5["scale_bars"] = list(bars_5m or [])
        return shot5
    shot15 = capture_chart(
        symbol, directory / "chart-15m.png", bars_15m or bars_5m, opener=opener, interval="15",
        fallback_label=FALLBACK_LABEL_5M, allow_fallback=False,
    )
    if shot15.get("source") == "tradingview":
        shot15["interval"] = "15"
        shot15["reason"] = f"5m: {shot5.get('reason') or 'capture failed'}"
        shot15["scale_bars"] = list(bars_15m or bars_5m or [])
        return shot15
    dest = directory / "chart-5m.png"
    draw_yf_chart(bars_5m, dest, symbol, label=FALLBACK_LABEL_5M, tag="5m")
    return {
        "source": "yfinance",
        "path": str(dest),
        "url": chart_url(symbol, "5"),
        "label": FALLBACK_LABEL_5M,
        "reason": f"5m: {shot5.get('reason') or 'capture failed'}; 15m: {shot15.get('reason') or 'capture failed'}",
        "interval": "5",
        "plot": dict(YF_PLOT),
        "scale_bars": list(bars_5m or []),
    }


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


def _open_browser(play):
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
        viewport={"width": 1280, "height": 800},
        device_scale_factor=2,
        color_scheme="dark",
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        user_agent=_browser_user_agent(browser),
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, context


def _playwright_chart(symbol, dest, interval="D"):
    from playwright.sync_api import sync_playwright
    from realchart.session import now_ist
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = ""
    reason = ""
    ok = False
    tech = {}
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
            text = (page.inner_text("body") or "")[:4000]
            page.screenshot(path=str(dest))
            if walled(text):
                ok = False
                reason = "bot wall"
            elif ok and not chart_painted(dest):
                ok = False
                reason = "chart did not paint"
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
