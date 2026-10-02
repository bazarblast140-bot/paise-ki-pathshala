"""Hard safe area for on-screen labels.

Every label box must stay inside a 6% margin of the 1080x1920 frame.
The craft gate fails when a box crosses that edge. Scene art also keeps
labels out of the YouTube top bar and the caption band.
"""

MARGIN = 0.06
FRAME_W = 1080
FRAME_H = 1920
# YouTube chrome: y 0-170 is the top bar, y 1140+ is captions. Scene labels
# sit in the picture band as well as inside the 6% frame margin.
CONTENT_TOP = 188
CONTENT_BOTTOM = 1060


def limits(width=FRAME_W, height=FRAME_H):
    """(left, top, right, bottom) of the 6% safe rectangle."""
    return (
        width * MARGIN,
        height * MARGIN,
        width * (1.0 - MARGIN),
        height * (1.0 - MARGIN),
    )


def inside(box, width=FRAME_W, height=FRAME_H):
    """True when the whole box is inside the 6% margin."""
    x0, y0, x1, y1 = (float(v) for v in box)
    left, top, right, bottom = limits(width, height)
    return x0 >= left - 0.51 and y0 >= top - 0.51 and x1 <= right + 0.51 and y1 <= bottom + 0.51


def check_boxes(boxes, width=FRAME_W, height=FRAME_H):
    """(ok, bad_boxes). An empty list is ok: there is nothing outside."""
    bad = [tuple(float(v) for v in box) for box in (boxes or []) if not inside(box, width, height)]
    return (not bad), bad
