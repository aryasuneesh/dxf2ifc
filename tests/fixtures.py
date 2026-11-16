"""Synthetic two-room house used by every test. No real plan is ever needed.

Layout (metres, origin at the outer south-west corner):
- outer box 10.0 x 6.0, walls 0.20 thick
- partition centred on x = 6.0, 0.10 thick
- door 0.90 wide in the partition at y = 2.0, door 1.00 wide in the south wall at x = 3.0
- BUREAU 1 (32.2 m2) west of the partition, BUREAU 2 (21.0 m2) east of it

Every entity stays inside the outer box, so the drawing's lower-left corner is the
house corner and the returned truth is valid in local coordinates for every variant.

Split words (colour_only) are placed assuming a character is 0.6 x text height wide,
with a gap of 0.5 x text height. normalize.group_labels uses the same estimate.
"""

import math
from pathlib import Path

import ezdxf

TEXT_H = 0.25  # m
CHAR_W = 0.6  # character width as a share of text height

LAYERS = {"wall": "MUR", "door": "PORTE", "text": "TEXTE", "dim": "COTATION", "furn": "MOBILIER"}
COLOUR_LAYERS = {"wall": "A-CONTOUR", "door": "A-CONTOUR", "dim": "A-CONTOUR",
                 "furn": "A-CONTOUR", "text": "A-TEXTE"}
THICK, THIN = 9, 5  # line weights seen in the SECO files


def _wall_lines():
    """Both faces of every wall, broken at the door gaps, plus the gap end caps."""
    lines = [
        # south wall, gap x 2.5..3.5
        (0, 0, 2.5, 0), (3.5, 0, 10, 0), (0.2, 0.2, 2.5, 0.2), (3.5, 0.2, 9.8, 0.2),
        (2.5, 0, 2.5, 0.2), (3.5, 0, 3.5, 0.2),
        # east, north, west
        (10, 0, 10, 6), (9.8, 0.2, 9.8, 5.8),
        (0, 6, 10, 6), (0.2, 5.8, 9.8, 5.8),
        (0, 0, 0, 6), (0.2, 0.2, 0.2, 5.8),
        # partition, gap y 1.55..2.45
        (5.95, 0.2, 5.95, 1.55), (5.95, 2.45, 5.95, 5.8),
        (6.05, 0.2, 6.05, 1.55), (6.05, 2.45, 6.05, 5.8),
        (5.95, 1.55, 6.05, 1.55), (5.95, 2.45, 6.05, 2.45),
    ]
    return [(*line, "wall", THICK) for line in lines]


def _door_swing_halves(steps=4):
    """The swing as two 45-degree polylines of straight pieces (how PDF exports show an arc)."""
    hx, hy, r = 6.0, 1.55, 0.9
    return [[(hx + r * math.cos(math.radians(start + 45 * i / steps)),
              hy + r * math.sin(math.radians(start + 45 * i / steps))) for i in range(steps + 1)]
            for start in (0, 45)]


def _rect(x, y, w, h, key):
    return [(x, y, x + w, y, key, THIN), (x + w, y, x + w, y + h, key, THIN),
            (x + w, y + h, x, y + h, key, THIN), (x, y + h, x, y, key, THIN)]


def make_house(path, units="m", colour_only=False, offset=(0, 0), xref_prefix="",
               in_block=False, noise=False, insunits=None) -> dict:
    """Write the house as a DXF and return the true answer in local metres.

    units       "m" or "mm"
    colour_only layers named by colour, walls told apart only by line weight, text split into words
    offset      shift of the whole plan, in drawing units (national-grid style coordinates)
    xref_prefix put in front of every layer name, e.g. "BAT 4$0$"
    in_block    the whole plan sits in one block placed with a single INSERT
    noise       adds furniture drawn as double lines 0.4 m apart
    insunits    overrides $INSUNITS (0 = unitless)
    """
    k = {"m": 1.0, "mm": 1000.0}[units]
    names = COLOUR_LAYERS if colour_only else LAYERS
    layer = lambda key: xref_prefix + names[key]

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = insunits if insunits is not None else {"m": 6, "mm": 4}[units]
    for key in names:
        if layer(key) not in doc.layers:
            doc.layers.add(layer(key))

    msp = doc.modelspace()
    target = doc.blocks.new("PLAN") if in_block else msp
    ox, oy = (0, 0) if in_block else offset
    pt = lambda x, y: (x * k + ox, y * k + oy)

    lines = _wall_lines()
    lines.append((0.2, 5.5, 5.95, 5.5, "dim", THIN))  # dimension line
    if colour_only:
        lines.append((6.0, 1.55, 6.9, 1.55, "door", THIN))  # the open door leaf
        for half in _door_swing_halves():
            target.add_lwpolyline([pt(x, y) for x, y in half],
                                  dxfattribs={"layer": layer("door"), "lineweight": THIN})
        for i in range(8):  # thin clutter, so the thin weight is the most common one
            lines += _rect(0.6 + 0.6 * i, 4.2, 0.4, 0.4, "furn")
    if noise:
        for y in (3.6, 1.0):  # double lines 0.4 m apart that must not become walls
            lines += [(1.0, y, 2.5, y, "furn", THIN), (1.0, y + 0.4, 2.5, y + 0.4, "furn", THIN)]
        lines += [(7.0, 4.6, 7.0, 5.6, "furn", THIN), (7.4, 4.6, 7.4, 5.6, "furn", THIN)]

    for x1, y1, x2, y2, key, weight in lines:
        attribs = {"layer": layer(key), "lineweight": weight}
        if colour_only:
            target.add_lwpolyline([pt(x1, y1), pt(x2, y2)], dxfattribs=attribs)
        else:
            target.add_line(pt(x1, y1), pt(x2, y2), dxfattribs=attribs)

    if not colour_only:  # door symbol as a block; its content on layer 0 inherits PORTE
        door = doc.blocks.new("PORTE_90")
        door.add_line((0, 0), (0.9 * k, 0), dxfattribs={"layer": "0"})
        door.add_arc((0, 0), 0.9 * k, 0, 90, dxfattribs={"layer": "0"})
        target.add_blockref("PORTE_90", pt(6.0, 1.55), dxfattribs={"layer": layer("door")})

    texts = [("BUREAU 1", 2.5, 3.2, "text"), ("32.2 m2", 2.5, 2.8, "text"),
             ("BUREAU 2", 7.2, 3.2, "text"), ("21.0 m2", 7.2, 2.8, "text"),
             ("NIVEAU +01", 0.5, 5.1, "text"), ("+3.20", 0.5, 4.75, "text"),
             ("P.01.01", 4.6, 2.0, "text"), ("P.01.02", 2.6, 0.6, "text"),
             ("5.75", 2.8, 5.55, "dim")]
    for text, x, y, key in texts:
        words = text.split() if colour_only else [text]
        for word in words:
            target.add_text(word, dxfattribs={"layer": layer(key), "height": TEXT_H * k,
                                              "insert": pt(x, y)})
            x += (len(word) * CHAR_W + 0.5) * TEXT_H

    if in_block:
        msp.add_blockref("PLAN", offset, dxfattribs={"layer": "0"})

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)

    return {
        "wall_axes": [((0.0, 0.1), (10.0, 0.1), 0.20), ((9.9, 0.0), (9.9, 6.0), 0.20),
                      ((0.0, 5.9), (10.0, 5.9), 0.20), ((0.1, 0.0), (0.1, 6.0), 0.20),
                      ((6.0, 0.2), (6.0, 5.8), 0.10)],
        "rooms": [{"name": "BUREAU 1", "area": 32.2}, {"name": "BUREAU 2", "area": 21.0}],
        "doors": [{"center": (6.0, 2.0), "width": 0.90, "tag": "P.01.01"},
                  {"center": (3.0, 0.1), "width": 1.00, "tag": "P.01.02"}],
        "storey": {"name": "NIVEAU +01", "elevation": 3.20},
    }
