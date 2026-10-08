# Motion library

Topic videos draw the chart from `motion_lib.py`. Each component takes the
numbers computed in `trade_plan` and a start/end from the Whisper word clock.
`window_progress` eases that span (smoothstep). Progress `1` is the settled
Chart Board mark: full candles, full arrow, full level line, the existing
risk box, and the 28×40 glow ring.

The chart stays in the top board (about 65% of the frame). Captions stay
under it. Aaj ka Nifty does not use this clock.

```python
import motion_lib
import trade_plan

plan = trade_plan.plan_for("pat_hammer")
clock = motion_lib.clock_from(topic, plan)  # topic carries _chart_overlays
# t is seconds on the voice. u is the scene playhead, 0 at the scene start.
t = trade_plan._scene_time(topic, "entry", 0, 0.4)
state = motion_lib.draw_plan(img, plan, shown, t, clock)
```

`state["counter"]["shown"]` equals `plan.target` once the target cue has
finished. `state["rr"]` carries `plan.risk`, `plan.reward`, and `plan.rr_text`.

## Candle build

Candles appear one by one. The newest body grows from its midpoint. Progress
1 matches `board_kit.candles`.

```python
box, lo, hi, pad = trade_plan._chart_geometry(plan, shown)
ax = motion_lib.candle_build(img, box, plan.ohlc, lo, hi, progress, pad)
```

## Trend arrow

The arrow draws from the first close to the focus close.

```python
motion_lib.trend_arrow(img, ax, plan, progress)
```

## Support / resistance band

A translucent band fades in around the computed support or resistance price.
It sits in the plot, under the dashed line.

```python
motion_lib.sr_band(img, ax, plan.sr, progress)
```

## Entry, stop, and target lines

The line draws left to right. The label pill appears after progress 0.45.
Progress 1 is the settled dashed line, with the price in the label.

```python
motion_lib.level_line(
    img, box, ax.y(plan.entry), (250, 204, 21),
    f"ENTRY {int(plan.entry)}", "right", progress,
)
```

Stop uses red and `plan.sl`. Target uses green and `plan.target`. Support
uses blue and `plan.sr`.

## Risk-reward box

Red on the risk side, green on the reward side, plus the risk, reward, and
RR text. Progress 1 paints the settled box (`trade_plan._rr_box`) and the
readout underneath.

```python
info = motion_lib.risk_reward_box(img, plan, progress)
# info["risk"] == plan.risk, info["reward"] == plan.reward, info["rr"] == plan.rr_text
```

## Key-candle highlight

A glow ring tightens onto the focus candle while that window zooms gently
(about 6%), then settles. Progress 1 is a 28×40 ring and zoom 1.

```python
mark = motion_lib.highlight_candle(img, ax, plan, progress)
# mark["zoom"] == 1.0 and mark["rx"] == 28.0 at progress 1
```

The encoder also eases the whole plate by a few percent, so a held scene
does not sit on one frame.

## Number counter

Counts up to the computed value. Progress 1 shows that value exactly.

```python
count = motion_lib.number_counter(img, plan.target, progress, label="TARGET")
# count["shown"] == plan.target at progress 1
```

## Whoosh

Main reveals (trend, support, entry, stop, target) get one soft whoosh.
ffmpeg builds it from filtered pink noise, high band then mid then low. It
is not a downloaded file. The mix sits about 24 dB under the voice RMS.
There is no music and no noise bed. A cue at t≈0 is skipped.

```python
motion_lib.render_whoosh("whoosh.wav")
motion_lib.mix_under("voice.wav", [8.0, 16.0, 24.0], "mixed.wav")
```

`board_short.assemble` calls `mix_under` when the frames carry
`motion_reveals`. Other shorts, including Aaj ka Nifty, keep the existing
spot-effect mix.
