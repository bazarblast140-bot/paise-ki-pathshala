"""Fixed chart shapes for the approved topic bank.

Every price on these charts is the price the matching script is allowed to
say. The painter does not invent a second set of levels.
"""
PATTERNS = {
    "pat_hammer": {
        "kind": "candles",
        "ohlc": ((128, 130, 120, 122), (122, 124, 112, 114), (114, 118, 100, 116)),
        "focus": {"hook": 2, "pehchan": 2, "matlab": 2, "karein": 2},
        "lines": ((100, "100"),),
        "marks": {
            "hook": "HAMMER LOW 100",
            "pehchan": "OPEN 114 CLOSE 116",
            "matlab": "BUYERS LIFTED 116",
            "karein": "STOP 100",
        },
    },
    "pat_doji": {
        "kind": "candles",
        "ohlc": ((120, 122, 112, 114), (114, 116, 108, 110), (110, 120, 100, 110)),
        "focus": {"hook": 2, "pehchan": 2, "matlab": 2, "karein": 2},
        "lines": ((100, "100"), (120, "120")),
        "marks": {
            "hook": "DOJI 110",
            "pehchan": "HIGH 120 LOW 100",
            "matlab": "OPEN = CLOSE 110",
            "karein": "WAIT FOR NEXT",
        },
    },
    "pat_bull_engulf": {
        "kind": "candles",
        "ohlc": ((118, 120, 112, 108), (106, 124, 104, 122)),
        "focus": {"hook": 1, "pehchan": 1, "matlab": 1, "karein": 1},
        "lines": ((104, "104"), (122, "122")),
        "marks": {
            "hook": "GREEN COVERS RED",
            "pehchan": "108 INSIDE 122",
            "matlab": "BUYERS 106 TO 122",
            "karein": "STOP 104",
        },
    },
    "pat_morning_star": {
        "kind": "candles",
        "ohlc": ((130, 132, 116, 118), (116, 118, 108, 112), (112, 128, 110, 126)),
        "focus": {"hook": 1, "pehchan": 2, "matlab": 2, "karein": 2},
        "lines": ((108, "108"), (126, "126")),
        "marks": {
            "hook": "MORNING STAR",
            "pehchan": "SMALL 112 THEN 126",
            "matlab": "CLOSE 126",
            "karein": "STOP 108",
        },
    },
    "pat_black_crows": {
        "kind": "candles",
        "ohlc": ((140, 142, 130, 132), (132, 134, 118, 120), (120, 122, 106, 108)),
        "focus": {"hook": 2, "pehchan": 1, "matlab": 2, "karein": 2},
        "lines": ((142, "142"), (106, "106")),
        "marks": {
            "hook": "THREE RED CLOSES",
            "pehchan": "132 THEN 120 THEN 108",
            "matlab": "SELLERS STEP DOWN",
            "karein": "STOP 142",
        },
    },
    "pat_double": {
        "kind": "double",
        "top": (100, 120, 108, 120, 100),
        "bottom": (110, 80, 96, 80, 104),
        "marks": {
            "hook": "TWO TOPS AT 120",
            "pehchan": "NECK 108 AND 96",
            "matlab": "BOTTOM LOWS AT 80",
            "karein": "BREAK 100 OR 104",
        },
    },
    "pat_head_shoulders": {
        "kind": "line",
        "points": (96, 110, 100, 130, 102, 112, 98),
        "lines": ((100, "100"), (130, "130")),
        "marks": {
            "hook": "HEAD 130",
            "pehchan": "SHOULDERS 110 112",
            "matlab": "NECKLINE 100",
            "karein": "BREAK 98",
        },
    },
    "pat_cup_handle": {
        "kind": "line",
        "points": (100, 90, 80, 90, 100, 94, 106),
        "lines": ((100, "100"), (80, "80"), (94, "94")),
        "marks": {
            "hook": "CUP RIM 100",
            "pehchan": "LOW 80 HANDLE 94",
            "matlab": "BACK TO 100",
            "karein": "BREAK 106",
        },
    },
    "pat_triangle": {
        "kind": "candles",
        "ohlc": (
            (108, 120, 100, 112),
            (110, 116, 104, 108),
            (107, 112, 106, 110),
            (109, 114, 108, 113),
            (112, 118, 110, 116),
        ),
        "focus": {"hook": 4, "pehchan": 0, "matlab": 4, "karein": 4},
        "lines": ((120, "120"), (100, "100"), (116, "116")),
        "marks": {
            "hook": "HIGHS FALL 120",
            "pehchan": "LOWS RISE 100",
            "matlab": "RANGE NARROWS",
            "karein": "CLOSE 116",
        },
    },
    "pat_flag": {
        "kind": "candles",
        "ohlc": (
            (100, 108, 98, 106),
            (106, 118, 104, 116),
            (116, 130, 114, 128),
            (128, 130, 122, 124),
            (124, 126, 122, 123),
            (123, 132, 122, 131),
        ),
        "focus": {"hook": 2, "pehchan": 3, "matlab": 5, "karein": 5},
        "lines": ((130, "130"), (122, "122"), (132, "132")),
        "marks": {
            "hook": "POLE TO 130",
            "pehchan": "FLAG HOLDS 122",
            "matlab": "PAUSE AFTER 130",
            "karein": "CLOSE 132",
        },
    },
    "pat_breakout": {
        "kind": "candles",
        "ohlc": (
            (104, 110, 100, 106),
            (106, 110, 102, 104),
            (104, 110, 100, 108),
            (108, 116, 107, 115),
            (114, 116, 110, 113),
        ),
        "focus": {"hook": 3, "pehchan": 3, "matlab": 4, "karein": 4},
        "lines": ((110, "110"), (100, "100"), (115, "115")),
        "marks": {
            "hook": "RANGE TOP 110",
            "pehchan": "CLOSE 115",
            "matlab": "RETEST 110",
            "karein": "HOLD 113",
        },
    },
    "pat_rsi": {
        "kind": "rsi",
        "ohlc": ((96, 104, 94, 102), (102, 112, 100, 110), (110, 118, 106, 108), (108, 112, 100, 103)),
        "rsi": (42, 55, 74, 61, 48, 28, 36),
        "lines": ((70, "70"), (30, "30")),
        "marks": {
            "hook": "RSI 70 AND 30",
            "pehchan": "70 OVERBOUGHT",
            "matlab": "30 OVERSOLD",
            "karein": "PRICE 110",
        },
    },
    "pat_support": {
        "kind": "candles",
        "ohlc": (
            (108, 112, 100, 110),
            (110, 120, 108, 118),
            (118, 120, 104, 108),
            (108, 112, 100, 111),
            (111, 120, 109, 119),
        ),
        "focus": {"hook": 0, "pehchan": 1, "matlab": 3, "karein": 4},
        "lines": ((100, "100"), (120, "120")),
        "marks": {
            "hook": "SUPPORT 100",
            "pehchan": "RESIST 120",
            "matlab": "BOUNCE OFF 100",
            "karein": "CLOSE 119",
        },
    },
    "pat_gap_up": {
        "kind": "candles",
        "ohlc": ((96, 100, 92, 98), (108, 114, 106, 112)),
        "focus": {"hook": 1, "pehchan": 1, "matlab": 0, "karein": 1},
        "lines": ((100, "100"), (108, "108")),
        "marks": {
            "hook": "GAP 100 TO 108",
            "pehchan": "OPEN 108",
            "matlab": "PRIOR HIGH 100",
            "karein": "CLOSE 112",
        },
    },
    "pat_stop_loss": {
        "kind": "candles",
        "ohlc": ((108, 114, 104, 112), (112, 116, 106, 110)),
        "focus": {"hook": 1, "pehchan": 1, "matlab": 0, "karein": 1},
        "lines": ((110, "110"), (104, "104")),
        "marks": {
            "hook": "ENTRY 110",
            "pehchan": "SWING LOW 106",
            "matlab": "STOP 104",
            "karein": "RISK TO 104",
        },
    },
}


# Seven owner-approved plan topics (22-Oct-2026). The chart is the trade_plan
# OHLC, so every price painted here is a code-computed price the script may say.
_PLAN_TOPIC_MARKS = {
    "pat_bear_engulf": ("BEARISH ENGULF", "RED COVERS GREEN"),
    "pat_shooting_star": ("SHOOTING STAR", "LONG UPPER WICK"),
    "pat_double_bottom": ("DOUBLE BOTTOM", "TWO LOWS + NECK"),
    "pat_evening_star": ("EVENING STAR", "CLOSE BELOW MID"),
    "pat_bear_flag": ("BEAR FLAG", "POLE + FLAG"),
    "pat_inside_bar": ("INSIDE BAR", "INSIDE MOTHER BAR"),
    "pat_ema_cross": ("EMA CROSS", "20 OVER 50"),
    # Six more owner-approved plan topics (29-Oct-2026).
    "pat_bull_harami": ("BULLISH HARAMI", "BABY INSIDE BODY"),
    "pat_tweezer_bottom": ("TWEEZER BOTTOM", "TWO SAME LOWS"),
    "pat_marubozu": ("MARUBOZU", "NO WICK BODY"),
    "pat_hanging_man": ("HANGING MAN", "LONG LOWER WICK"),
    "pat_dark_cloud": ("DARK CLOUD", "CLOSE BELOW MID"),
    "pat_white_soldiers": ("3 WHITE SOLDIERS", "3 GREEN HIGHER"),
}


def _plan_topic_specs():
    import trade_plan
    for key, (hook, pehchan) in _PLAN_TOPIC_MARKS.items():
        plan = trade_plan.plan_for(key)
        sr = trade_plan.fmt(plan.sr)
        PATTERNS[key] = {
            "kind": "candles",
            "ohlc": tuple(tuple(int(v) if float(v).is_integer() else v for v in c) for c in plan.ohlc),
            "focus": {"hook": plan.focus, "pehchan": plan.focus, "matlab": plan.focus, "karein": plan.focus},
            "lines": ((plan.sr, sr),),
            "marks": {"hook": hook, "pehchan": pehchan, "matlab": f"{plan.sr_name} {sr}", "karein": "PLAN"},
        }


_plan_topic_specs()

PLAN_BEATS = (
    "hook", "pehchan", "context", "entry", "sl", "target", "invalidation", "cta",
)
_PLAN_MARKS = {
    "context": "TREND + SUPPORT",
    "entry": "ENTRY",
    "sl": "SL",
    "target": "TARGET",
    "invalidation": "SKIP",
    "cta": "COMMENT",
}
_SHOWN = {
    "context": ("trend", "support"),
    "entry": ("trend", "support", "entry"),
    "sl": ("trend", "support", "entry", "sl"),
    "target": ("trend", "support", "entry", "sl", "target"),
    "invalidation": ("trend", "support", "entry", "sl", "target"),
    "cta": ("trend", "support", "entry", "sl", "target"),
}


def _fill_marks():
    """Every pattern has a label for the eight trade-plan beats."""
    for spec in PATTERNS.values():
        marks = spec.setdefault("marks", {})
        for beat, label in _PLAN_MARKS.items():
            marks.setdefault(beat, label)


_fill_marks()

EXTRA_MARKS = {
    "piercing": {
        "hook": "PIERCING",
        "pehchan": "GREEN THROUGH MID",
        **_PLAN_MARKS,
    },
    "risk_daily_loss_limit": {
        "hook": "DAILY LIMIT",
        "pehchan": "CAPITAL LIMIT",
        **_PLAN_MARKS,
    },
}


def mark_for(spec, beat):
    """A label that always exists. A new beat never raises KeyError."""
    label = str((spec or {}).get("marks", {}).get(beat) or "").strip()
    if label:
        return label
    return _PLAN_MARKS.get(beat) or "PLAN"


def marks_for(key):
    spec = PATTERNS.get(str(key or ""))
    if spec is not None:
        return dict(spec.get("marks") or {})
    return dict(EXTRA_MARKS.get(str(key or "")) or {})


def _price_text(value):
    number = float(value)
    if abs(number - round(number)) < 1e-6:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _levels(spec):
    """Entry, stop, target, and support from this spec's own prices."""
    ohlc = list((spec or {}).get("ohlc") or ())
    if not ohlc:
        points = list((spec or {}).get("points") or (spec or {}).get("top") or ())
        if len(points) < 2:
            return None
        ohlc = [(float(value), float(value), float(value), float(value)) for value in points]
    support = min(candle[2] for candle in ohlc)
    resist = max(candle[1] for candle in ohlc)
    short = ohlc[-1][3] < ohlc[0][3]
    if short:
        entry, sl = support, resist
        risk = max(sl - entry, 1.0)
        target = entry - 2 * risk
        sr, name = resist, "RESIST"
    else:
        entry, sl = resist, support
        risk = max(entry - sl, 1.0)
        target = entry + 2 * risk
        sr, name = support, "SUPPORT"
    return {
        "support": sr,
        "sr_name": name,
        "entry": entry,
        "sl": sl,
        "target": target,
        "start": (0, ohlc[0][3]),
        "end": (max(0, len(ohlc) - 2), ohlc[max(0, len(ohlc) - 2)][3]),
    }


def _include_plan(spec, beat, lo, hi):
    levels = _levels(spec) if beat in _SHOWN else None
    if not levels:
        return lo, hi, None
    values = (levels["support"], levels["entry"], levels["sl"], levels["target"])
    return min(lo, *values), max(hi, *values), levels


def _paint_levels(img, ax, levels, beat):
    import board_kit as K
    shown = _SHOWN.get(beat) or ()
    box = K.BOARD
    if "trend" in shown:
        start, end = levels["start"], levels["end"]
        K.arrow(img, (ax.x(start[0]), ax.y(start[1])), (ax.x(end[0]), ax.y(end[1])), K.YELLOW, 8, 26)
    if "support" in shown:
        K.dashed(
            img, box[0] + 24, box[2] - 24, ax.y(levels["support"]),
            K.BLUE, f"{levels['sr_name']} {_price_text(levels['support'])}",
            size=24, label_side="left",
        )
    if "entry" in shown:
        K.dashed(
            img, box[0] + 24, box[2] - 24, ax.y(levels["entry"]),
            K.YELLOW, f"ENTRY {_price_text(levels['entry'])}", size=24, label_side="right",
        )
    if "sl" in shown:
        K.dashed(
            img, box[0] + 24, box[2] - 24, ax.y(levels["sl"]),
            K.RED, f"SL {_price_text(levels['sl'])}", size=24, label_side="left",
        )
    if "target" in shown:
        K.dashed(
            img, box[0] + 24, box[2] - 24, ax.y(levels["target"]),
            K.GREEN, f"TARGET {_price_text(levels['target'])}", size=24, label_side="right",
        )


def gate_problems(scripts=None, marks=None):
    """Missing beats or chart marks across approved/*.json. Empty when the bank can render."""
    import trade_plan
    if scripts is None:
        import approved_bank
        scripts = approved_bank.all_scripts()
    problems = []
    for script in scripts or []:
        key = str((script or {}).get("key") or "")
        beats = (script or {}).get("beats") or {}
        missing = [beat for beat in trade_plan.BEATS if not str(beats.get(beat) or "").strip()]
        if missing:
            problems.append(f"{key} missing beats: {', '.join(missing)}")
        if isinstance(marks, dict) and key in marks:
            chart = marks[key] or {}
        else:
            chart = marks_for(key)
        if not chart:
            problems.append(f"{key} missing chart marks")
            continue
        absent = [beat for beat in trade_plan.BEATS if not str(chart.get(beat) or "").strip()]
        if absent:
            problems.append(f"{key} missing chart marks: {', '.join(absent)}")
    return problems


def reject_chart_gate():
    """Stop before any frame. A missing beat or mark is a message, not a traceback."""
    problems = gate_problems()
    if not problems:
        return
    print("[GATE] " + "; ".join(problems), flush=True)
    raise SystemExit(1)


def prices_of(spec):
    """Every price this chart draws, as the digit string the script must use."""
    found = set()

    def add(value):
        number = float(value)
        if number == int(number):
            found.add(str(int(number)))
        else:
            found.add(f"{number:.2f}".rstrip("0").rstrip("."))

    for candle in spec.get("ohlc") or ():
        for value in candle:
            add(value)
    for value in spec.get("points") or ():
        add(value)
    for value in spec.get("top") or ():
        add(value)
    for value in spec.get("bottom") or ():
        add(value)
    for value in spec.get("rsi") or ():
        add(value)
    for price, _label in spec.get("lines") or ():
        add(price)
    return found


def make_scene(key):
    """Eight beats. Each beat draws the same pattern and marks a different part."""
    spec = PATTERNS[key]

    def beat(name, img):
        import board_kit as K
        plain = K.background()
        _paint(plain, spec, name, annotate=False)
        _paint(img, spec, name, annotate=True)
        return plain, img

    return beat


def _paint(img, spec, beat, annotate):
    import board_kit as K
    from PIL import ImageDraw

    K.panel(img)
    box = K.BOARD
    grid = ImageDraw.Draw(img)
    for y in range(int(box[1]) + 8, int(box[3]) - 8, 36):
        grid.line([(box[0] + 12, y), (box[2] - 12, y)], fill=(120, 150, 190), width=2)
    kind = spec["kind"]
    if kind == "double":
        _double(img, spec, beat, annotate)
    elif kind == "line":
        _line(img, spec, beat, annotate)
    elif kind == "rsi":
        _rsi(img, spec, beat, annotate)
    else:
        _candles(img, spec, beat, annotate)
    if annotate:
        K.label(img, box[0] + 48, box[1] + 64, mark_for(spec, beat), K.INK, 32)


def _candles(img, spec, beat, annotate):
    import board_kit as K
    box = K.BOARD
    ohlc = list(spec["ohlc"])
    lo = min(candle[2] for candle in ohlc)
    hi = max(candle[1] for candle in ohlc)
    for price, _label in spec.get("lines") or ():
        lo = min(lo, float(price))
        hi = max(hi, float(price))
    lo, hi, levels = _include_plan(spec, beat, lo, hi)
    ax = K.candles(
        img, box, ohlc, lo, hi, grid=False, width_frac=0.5, pad=(70, 100, 70, 80),
    )
    if not annotate:
        return
    focus = int((spec.get("focus") or {}).get(beat, len(ohlc) - 1))
    candle = ohlc[focus]
    K.ring(
        img, ax.x(focus), (ax.y(candle[1]) + ax.y(candle[2])) / 2,
        34, 48, K.YELLOW,
    )
    for price, text in spec.get("lines") or ():
        K.dashed(
            img, box[0] + 24, box[2] - 24, ax.y(float(price)),
            K.YELLOW, text, size=26, label_side="left",
        )
    if levels:
        _paint_levels(img, ax, levels, beat)


def _line(img, spec, beat, annotate):
    import board_kit as K
    box = K.BOARD
    pts = list(spec["points"])
    lo, hi = min(pts), max(pts)
    for price, _label in spec.get("lines") or ():
        lo = min(lo, float(price))
        hi = max(hi, float(price))
    lo, hi, levels = _include_plan(spec, beat, lo, hi)
    ax = K.Axes(box, len(pts), lo, hi, pad=(70, 100, 70, 90))
    xy = [(ax.x(i), ax.y(value)) for i, value in enumerate(pts)]
    K.polyline(img, xy, K.YELLOW, 8)
    for i, value in enumerate(pts):
        K.ring(img, ax.x(i), ax.y(value), 14, 14, K.GREEN)
    if not annotate:
        return
    for price, text in spec.get("lines") or ():
        K.dashed(
            img, box[0] + 24, box[2] - 24, ax.y(float(price)),
            K.YELLOW, text, size=26, label_side="left",
        )
    if levels:
        _paint_levels(img, ax, levels, beat)


def _double(img, spec, beat, annotate):
    import board_kit as K
    box = K.BOARD
    mid = (box[1] + box[3]) / 2
    levels = _levels({"points": spec["top"]}) if beat in _SHOWN else None
    lo = hi = None
    if levels:
        values = (levels["support"], levels["entry"], levels["sl"], levels["target"])
        lo = min(min(spec["top"]), *values)
        hi = max(max(spec["top"]), *values)
    top = _one_line(
        img, (box[0], box[1], box[2], mid - 8), spec["top"],
        annotate and (beat in ("hook", "pehchan") or beat in _SHOWN),
        lo=lo, hi=hi,
    )
    _one_line(img, (box[0], mid + 8, box[2], box[3]), spec["bottom"], annotate and beat in ("matlab", "karein"))
    if top is not None and levels:
        _paint_levels(img, top, levels, beat)


def _one_line(img, box, pts, annotate, lo=None, hi=None):
    import board_kit as K
    pts = list(pts)
    lo = min(pts) if lo is None else lo
    hi = max(pts) if hi is None else hi
    ax = K.Axes(box, len(pts), lo, hi, pad=(36, 36, 36, 36))
    K.polyline(img, [(ax.x(i), ax.y(value)) for i, value in enumerate(pts)], K.YELLOW, 8)
    for i, value in enumerate(pts):
        K.ring(img, ax.x(i), ax.y(value), 12, 12, K.GREEN)
    if annotate:
        K.dashed(
            img, box[0] + 16, box[2] - 16, ax.y(pts[1]),
            K.YELLOW, str(int(pts[1])), size=24, label_side="left",
        )
    return ax


def _rsi(img, spec, beat, annotate):
    import board_kit as K
    box = K.BOARD
    mid = box[1] + (box[3] - box[1]) * 0.58
    ohlc = list(spec["ohlc"])
    lo = min(candle[2] for candle in ohlc)
    hi = max(candle[1] for candle in ohlc)
    lo, hi, levels = _include_plan(spec, beat, lo, hi)
    ax = K.candles(
        img, (box[0], box[1], box[2], mid - 8), ohlc, lo, hi,
        grid=False, width_frac=0.46, pad=(40, 70, 40, 36),
    )
    series = list(spec["rsi"])
    rx = K.Axes((box[0], mid + 8, box[2], box[3]), len(series), 0, 100, pad=(40, 36, 40, 36))
    K.polyline(img, [(rx.x(i), rx.y(value)) for i, value in enumerate(series)], K.BLUE, 6)
    if annotate:
        K.dashed(img, box[0] + 20, box[2] - 20, rx.y(70), K.RED, "70", size=24, label_side="left")
        K.dashed(img, box[0] + 20, box[2] - 20, rx.y(30), K.GREEN, "30", size=24, label_side="left")
        K.ring(img, ax.x(1), ax.y(ohlc[1][3]), 28, 28, K.YELLOW)
        if levels:
            _paint_levels(img, ax, levels, beat)
