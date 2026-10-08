# Motion library

Topic videos draw the moving chart from `motion_lib.py`. The settled still
that pixel checks read stays the existing Chart Board (`trade_plan._draw_price`).
The encoded picture is `motion_at`: one continuous canvas, numbers from the
trade plan, timing from the Whisper word clock. Aaj ka Nifty does not use
this clock.

The chart stays in the top board (60–65% of the frame). Captions stay in the
bottom band.

## Style spec

Taken from top Shorts (2.1M / 2.1M / 451K views).

1. **Hook.** The finished red/green RR box is visible from 0 to 2 s, then
   fades out. A big Hindi question title is in by 300 ms. The open is never
   an empty chart. Context candles keep building through about 5 s.
2. **Candle pacing.** The first context candle is finished at t=0. The rest
   are staggered across the hook, each drawing in about 150 ms, so the hook
   does not sit still. The 2–3 key candles slow to one candle per beat.
3. **Rhythm.** One new element every 2–3 s. Each element animates in over
   250–400 ms with ease-out cubic. After the hook the chart is one continuous
   canvas: no hard cut between beats, and overlays accumulate.
4. **Trade lines.** ENTRY, SL, and TARGET draw left to right on the spoken
   word. ENTRY `#F5D90A`, SL `#EF4444`, TARGET `#22C55E`, solid 6 px. The
   label pill pops 120 ms after the line, with the price. Support and
   resistance are dashed white, with a translucent band.
5. **Labels.** One or two words, bold white, 44–56 px, joined to the candle
   by a curved dashed arrow.
6. **RR box.** A tall green reward box over a short red risk box,
   semi-transparent (alpha about 0.30), anchored at the key candle, with
   `RR 1:x` text. It is on screen for the first 2 s, fades out before
   pehchan, and is drawn again on the target beat. It does not cover the
   hammer through pehchan and context.
7. **Key candle.** Other candles dim to 35% opacity. The key candle gets a
   yellow `#F5C518` outline and pushes in from 1.0× to 1.35× over 600 ms
   (ease-in-out), then eases back before the next beat.
8. **Look.** Flat background `#161616`. Bull `#22B53C`, bear `#F01E32`.
   Yellow is only for attention. Captions are one block, at most two shaped
   lines, in the bottom band. The disclaimer is at least 36 px. The chart
   fills the top 60–65%. A soft whoosh sits about −20 dB under the voice, at most 4 per video, and only on
   major reveals: the RR box, ENTRY, TARGET, and the key-candle zoom. The
   video ends on what kills the trade: price breaks the SL and the SL line
   flashes red.

## Clock

```python
import motion_lib
import trade_plan

plan = trade_plan.plan_for("pat_hammer")
clock = motion_lib.clock_from(topic, plan)  # topic carries _beat_times and _chart_overlays
t = trade_plan._scene_time(topic, "entry", 0, 0.4)
state = motion_lib.draw_plan(img, plan, shown, t, clock)
```

`clock["rr_hook"]` shows the finished box from 0 to 2 s, fades it, then
redraws it when the target beat starts. `clock["title"]` is `(0, 0.30)`.
`state["counter"]["shown"]` equals `plan.target` once the target cue has
finished, and that counter is not painted on the chart. `state["rr"]` carries
`plan.risk`, `plan.reward`, and `plan.rr_text`.

`motion_geometry` uses one price scale for the whole video, so a new beat
does not jump the canvas. Neighbouring plan scenes both animate, so the
encoder does not wipe between them.

## Candles

The first context candle finishes at t=0. Later context candles are spread
across the hook and each still draws in 150 ms. Each key candle waits for
pehchan, context, or entry.

```python
context, key = motion_lib.candle_roles(plan)
windows = motion_lib.candle_windows(plan, topic["_beat_times"])
box, lo, hi, pad = motion_lib.motion_geometry(plan)
ax = motion_lib.candle_build(
    img, box, plan.ohlc, lo, hi, 1.0, pad, per_candle=progresses,
)
```

Bull bodies are `#22B53C`. Bear bodies are `#F01E32`.

## Trend arrow

The arrow draws from the first close to the focus close, in attention yellow.

```python
motion_lib.trend_arrow(img, ax, plan, progress)
```

## Support / resistance

A translucent white band fades in on the computed price. The line itself is
dashed white.

```python
motion_lib.sr_band(img, ax, plan.sr, progress)
motion_lib.level_line(
    img, box, ax.y(plan.sr), motion_lib.WHITE,
    f"SR {int(plan.sr)}", "left", progress, dashed=True, anchor=None,
)
```

## Entry, stop, and target

Solid 6 px, left to right, on the spoken word. The pill (48 px, inside
44–56) pops 120 ms after the line finishes. A curved dashed arrow joins the
pill to the key candle.

```python
motion_lib.level_line(
    img, box, ax.y(plan.entry), motion_lib.ENTRY,
    f"ENTRY {int(plan.entry)}", "right", progress,
    pill=motion_lib._pill_on(t, clock["entry"]), anchor=anchor,
)
```

Stop uses `motion_lib.SL` and `plan.sl`. Target uses `motion_lib.TARGET`
and `plan.target`.

## Risk-reward box

Tall green reward over short red risk, alpha 0.30, anchored on the focus
candle. The label is `RR 1:x`.

```python
info = motion_lib.risk_reward_box(img, plan, progress, ax)
# info["risk"] == plan.risk, info["reward"] == plan.reward, info["text"] == "RR 1:2"
```

## Key candle

`key_zoom_at` pushes 1.0× → 1.35× over 600 ms (ease-in-out), holds the
attention color, then eases back. Other candles draw at 35% opacity while
the push is active.

```python
zoom, attention = motion_lib.key_zoom_at(t, zoom_start)
```

## Number counter

Counts up to the computed value over about 500 ms. Progress 1 is exact.
The glyphs are 72 px (inside 64–80).

```python
count = motion_lib.number_counter(img, plan.target, progress, label="TARGET")
# count["shown"] == plan.target at progress 1
```

## Captions

`paint_caption` draws 68 px (inside 64–72) in the existing subtitle band.
One keyword (`टारगेट`, `स्टॉप`, `एंट्री`, `सपोर्ट`, `रेसिस्टेंस`, `रिस्क`,
`हैमर`, `कैंडल`) is `#F5C518`. The hook beat shows `hook_line` once the
title has started.

## Stop break

On the invalidation beat the price marker travels from entry to the stop,
and the stop line flashes red.

```python
motion_lib.sl_break(img, ax, plan, progress, flash_on=True)
```

## Whoosh

At most four cues: the RR box at t=0, the key-candle zoom, ENTRY, and
TARGET. ffmpeg builds each whoosh from filtered pink noise (high band, then
mid, then low). It is not a downloaded file. The mix sits about 20 dB under
the voice RMS. There is no music. Other shorts, including Aaj ka Nifty, keep
the existing spot-effect mix.

```python
motion_lib.render_whoosh("whoosh.wav")
motion_lib.mix_under("voice.wav", [0.0, 8.0, 24.0, 40.0], "mixed.wav")
```

`board_short.assemble` calls `mix_under` when the frames carry
`motion_reveals`.
