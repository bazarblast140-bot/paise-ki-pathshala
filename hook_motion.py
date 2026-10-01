"""First-two-seconds hook: solid gold typewriter, one grapheme cluster at a time.

The quiz Shorts use crimson. This board is already navy with gold emphasis
(board_kit YELLOW, RGB 250, 204, 21) and uses green only for an up-move.
Gold stays a highlight. Green would read as a buy signal, and crimson would
fight the red thumbnail ground. The fill is fully opaque: no fade, no karaoke
secondary colour. A dark drop shadow sits down-right of the glyphs.

Clusters come from regex \\X (UAX #29, including Devanagari conjuncts). A
cluster that still ends in a virama is joined to the next one, so a matra or
halant never becomes its own keystroke. The last cluster is on screen by 1.4s,
so the line is complete before the 2s mark. Narration is not delayed; the
clicks are a separate track.
"""
import regex

# ASS colour is &HAABBGGRR. RGB(250, 204, 21) -> B=15 G=CC R=FA.
GOLD = "&H0015CCFA&"
# Near-black drop shadow, RGB(16, 16, 24).
SHADOW = "&H00181010&"
FINISH_S = 1.4
VIRAMA = "\u094d"


def grapheme_clusters(text):
    """One reveal step per grapheme cluster. Conjuncts and matras stay whole."""
    parts = regex.findall(r"\X", text or "")
    merged = []
    i = 0
    while i < len(parts):
        cur = parts[i]
        while cur.endswith(VIRAMA) and i + 1 < len(parts):
            i += 1
            cur += parts[i]
        merged.append(cur)
        i += 1
    return merged


def reveal_steps(text, finish_by=FINISH_S):
    """(time, prefix) pairs. The last time is <= finish_by and the prefix is the full line."""
    clusters = grapheme_clusters(text)
    if not clusters:
        return []
    if len(clusters) == 1:
        times = [0.0]
    else:
        step = finish_by / (len(clusters) - 1)
        times = [i * step for i in range(len(clusters))]
    times[-1] = min(times[-1], finish_by)
    prefixes = []
    built = ""
    for cluster in clusters:
        built += cluster
        prefixes.append(built)
    return list(zip(times, prefixes))


def click_times(text, finish_by=FINISH_S):
    return [t for t, _prefix in reveal_steps(text, finish_by)]


def visible_at(text, t, finish_by=FINISH_S):
    shown = ""
    for when, prefix in reveal_steps(text, finish_by):
        if when <= t + 1e-9:
            shown = prefix
    return shown


def _escape(text):
    return (
        str(text)
        .replace("\\", "")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
    )


def _override(x, y, size):
    """Solid gold, opaque, dark offset shadow. No fade and no karaoke fill."""
    return (
        f"\\an8\\pos({int(x)},{int(y)})\\fs{int(size)}"
        f"\\c{GOLD}\\1a&H00&"
        f"\\bord2\\3c&H00000000&"
        f"\\shad0\\xshad4\\yshad4\\4c{SHADOW}"
        f"\\q2"
    )


def dialogues(text, t_end, x, y, size, finish_by=FINISH_S):
    """ASS lines for the typewriter. Events do not overlap, so two prefixes never stack."""
    from board_kit import ass_time

    steps = reveal_steps(text, finish_by)
    if not steps:
        return []
    t_end = max(float(t_end), steps[-1][0])
    style = _override(x, y, size)
    events = []
    for i, (t0, prefix) in enumerate(steps):
        t1 = steps[i + 1][0] if i + 1 < len(steps) else t_end
        if i + 1 < len(steps) and (t1 - t0) > 0.004:
            t1 -= 0.001
        events.append(
            f"Dialogue: 1,{ass_time(t0)},{ass_time(max(t1, t0))},T,,0,0,0,,"
            f"{{{style}}}{_escape(prefix)}"
        )
    return events
