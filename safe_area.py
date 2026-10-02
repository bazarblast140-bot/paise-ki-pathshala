"""Hard safe area for on-screen labels.

Every label box must stay inside a 6% margin of the 1080x1920 frame.
The craft gate fails when a box crosses that edge. Scene art also keeps
labels out of the YouTube top bar and the caption band.
"""

MARGIN = 0.06
FRAME_W = 1080
FRAME_H = 1920
# YouTube chrome: y 0-170 is the top bar. Teaching labels stay in the picture.
# Below the card, three bands never share a line of text:
#   subtitle  white captions, the beat line, and the poll
#   brand     PKP, the source line, and the grey disclaimer
#   progress  the bar, geometry only, under the brand line
CONTENT_TOP = 188
CONTENT_BOTTOM = 1060
SUBTITLE_TOP = 1160
SUBTITLE_BOTTOM = 1560
BRAND_TOP = 1588
BRAND_BOTTOM = 1720
HEAD_Y = 1172
KEY_Y = 1288
CAPTION_Y = 1376
CTA_Y = 1464
SOURCE_Y = 1604
BRAND_Y = 1668


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


def intersects(a, b):
    """True when the two boxes have a positive area in common. A shared edge does not count."""
    ax0, ay0, ax1, ay1 = (float(v) for v in a)
    bx0, by0, bx1, by1 = (float(v) for v in b)
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def overlapping(boxes):
    """Pairs of boxes that cover the same pixels. Order is the input order."""
    rects = [tuple(float(v) for v in box) for box in (boxes or [])]
    hits = []
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if intersects(rects[i], rects[j]):
                hits.append((rects[i], rects[j]))
    return hits
