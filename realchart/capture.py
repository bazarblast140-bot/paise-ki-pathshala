"""TradingView when the runner can see it. Otherwise a labeled NSE chart."""
from __future__ import annotations

BOT_WALL = (
    "captcha",
    "unusual traffic",
    "verify you are",
    "access denied",
    "sign in to",
    "log in to",
    "robot",
    "are you a human",
)
FALLBACK_LABEL = "Chart: NSE data (yfinance)"


def walled(text):
    low = " ".join(str(text or "").lower().split())
    return any(phrase in low for phrase in BOT_WALL)


def chart_url(symbol):
    return f"https://www.tradingview.com/chart/?symbol={symbol}&interval=D&theme=dark"


def technicals_url(symbol):
    return f"https://www.tradingview.com/symbols/{str(symbol).replace(':', '-')}/technicals/"


def draw_yf_chart(bars, dest, symbol):
    """Candles from the OHLC we already computed. The label says it is not TradingView."""
    from PIL import Image, ImageDraw, ImageFont
    rows = list(bars or [])[-80:]
    image = Image.new("RGB", (1080, 1080), (22, 26, 32))
    draw = ImageDraw.Draw(image)
    font = _font(28)
    draw.text((24, 16), f"{symbol}  1D", fill=(230, 236, 240), font=font)
    draw.text((24, 1020), FALLBACK_LABEL, fill=(255, 214, 10), font=font)
    if len(rows) < 2:
        image.save(dest)
        return dest
    highs = [float(row["h"]) for row in rows]
    lows = [float(row["l"]) for row in rows]
    top, bot = max(highs), min(lows)
    span = max(1.0, top - bot)
    left, width, plot_top, plot_h = 40, 1000, 80, 900
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


def capture_chart(symbol, dest, bars, opener=None):
    """Try the public TradingView chart. A bot wall becomes the yfinance chart."""
    if opener is None:
        opener = _playwright_chart
    try:
        shot = opener(symbol, dest)
    except Exception as exc:  # noqa: BLE001 — the runner must still render
        shot = {"ok": False, "reason": type(exc).__name__, "text": str(exc)}
    text = f"{shot.get('text') or ''} {shot.get('reason') or ''}"
    if shot.get("ok") and not walled(text):
        return {
            "source": "tradingview",
            "path": shot.get("path") or str(dest),
            "url": chart_url(symbol),
            "captured_at": shot.get("captured_at") or "",
            "label": "TradingView",
            "technicals": shot.get("technicals") or {},
        }
    draw_yf_chart(bars, dest, symbol)
    return {
        "source": "yfinance",
        "path": str(dest),
        "url": chart_url(symbol),
        "captured_at": "",
        "label": FALLBACK_LABEL,
        "reason": "bot wall" if walled(text) else (shot.get("reason") or "chart capture failed"),
        "technicals": {},
    }


def _playwright_chart(symbol, dest):
    from playwright.sync_api import sync_playwright
    from realchart.session import now_ist
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1180}, device_scale_factor=2, color_scheme="dark")
        page.goto(chart_url(symbol), timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        text = page.inner_text("body")
        page.screenshot(path=str(dest))
        tech = {}
        if not walled(text):
            page.goto(technicals_url(symbol), timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            tech_text = page.inner_text("body")
            tech = {"text": tech_text[:4000]}
        browser.close()
    return {"ok": True, "text": text, "path": str(dest), "captured_at": now_ist().strftime("%Y-%m-%d %H:%M IST"), "technicals": tech}


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
