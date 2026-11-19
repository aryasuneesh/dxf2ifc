"""Drawing + roles -> plan.json. Rules only: no model, no network.

Walls come from pairs of parallel lines, openings from gaps in walls, rooms from filling
the space around each room label, the storey from level text, the slab from the outline.
"""

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from . import config
from .roles import rule_role
from .schema import validate_plan

AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m\s?[2²]", re.IGNORECASE)
DOOR_TAG_RE = re.compile(r"^P[.\-]?\d", re.IGNORECASE)
LEVEL_RE = re.compile(r"^[+\-]\s?\d+[.,]\d{2}$")
OUVERTURE_RE = re.compile(r"ouverture\s*:?\s*(\d{3})", re.IGNORECASE)
HEIGHT_RE = re.compile(r"(?:ouverture|HL)\s*:?\s*(\d{3})", re.IGNORECASE)
STOREY_RE = re.compile(config.STOREY_WORDS, re.IGNORECASE)
FIRE_RE = re.compile(config.FIRE_LAYER, re.IGNORECASE)
ROOM_RE = re.compile(rf"(^|[^a-zà-ü])({config.ROOM_WORDS})", re.IGNORECASE)


@dataclass
class _Wall:
    ux: float  # unit direction of the axis
    uy: float
    c: float  # signed offset of the axis line from the origin
    lo: float  # start and end along the direction
    hi: float
    t: float  # thickness
    handles: list
    layer: str
    conf: float
    rule: str = "parallel_pair"

    def point(self, along):
        return (self.ux * along - self.uy * self.c, self.uy * along + self.ux * self.c)

    @property
    def axis(self):
        return [self.point(self.lo), self.point(self.hi)]

    def corners(self):
        half = self.t / 2
        (x1, y1), (x2, y2) = self.axis
        nx, ny = -self.uy * half, self.ux * half
        return [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]


def _cap(confidence, family):
    return min(confidence, config.CONF_COLOUR_ONLY_CAP) if family == "colour_only" else confidence


def _center(label):
    """Rough middle of a text: TEXT stores only its lower-left corner."""
    half = len(label.text) * config.CHAR_W_MIN * label.height / 2
    angle = math.radians(label.rotation)
    return (label.x + half * math.cos(angle) - label.height / 2 * math.sin(angle),
            label.y + half * math.sin(angle) + label.height / 2 * math.cos(angle))


# ---------------------------------------------------------------- walls

def _wall_candidates(drawing, roles):
    if roles["family"] == "layered":
        conf = {name: entry["confidence"] for name, entry in roles["layers"].items() if entry["role"] == "wall"}
        picked = [(s, conf[s.layer]) for s in drawing.segments if s.layer in conf]
    elif roles["wall_filter"]:
        layers, weight = set(roles["wall_filter"]["layers"]), roles["wall_filter"]["min_lineweight"]
        picked = [(s, config.CONF_COLOUR_ONLY_CAP) for s in drawing.segments
                  if s.layer in layers and s.lineweight >= weight]
    else:
        picked = []
    return [(s, c) for s, c in picked if s.length >= config.MIN_WALL_SEG]


def _free(lo, hi, used):
    """Parts of [lo, hi] not covered by the used intervals."""
    parts = [(lo, hi)]
    for a, b in used:
        parts = [piece for x, y in parts for piece in
                 ([(x, y)] if b <= x or a >= y else [(x, a)] * (a > x) + [(b, y)] * (b < y))]
    return parts


def _pair_walls(candidates):
    """Pair parallel lines 5-60 cm apart into wall pieces; long lines with no partner become thin walls.

    # ponytail: angles are binned in fixed 2-degree steps and each line scans its neighbours
    # by offset, O(n^2) in the worst case. Fine to ~5,000 candidates. Upgrade: shapely STRtree.
    """
    bins = defaultdict(list)
    n_bins = round(180 / config.ANGLE_TOL)
    for index, (seg, _) in enumerate(candidates):
        angle = math.degrees(math.atan2(seg.y2 - seg.y1, seg.x2 - seg.x1)) % 180
        bins[round(angle / config.ANGLE_TOL) % n_bins].append((index, angle))

    pieces, used = [], defaultdict(list)
    for bin_id, members in bins.items():
        centre = bin_id * config.ANGLE_TOL
        offsets = [((angle - centre + 90) % 180 - 90, candidates[i][0].length) for i, angle in members]
        mean = centre + sum(d * w for d, w in offsets) / sum(w for _, w in offsets)
        ux, uy = math.cos(math.radians(mean)), math.sin(math.radians(mean))
        lines = []
        for index, _ in members:
            s = candidates[index][0]
            t1, t2 = ux * s.x1 + uy * s.y1, ux * s.x2 + uy * s.y2
            c = -uy * (s.x1 + s.x2) / 2 + ux * (s.y1 + s.y2) / 2
            lines.append((c, min(t1, t2), max(t1, t2), index))
        lines.sort()

        pairs = []
        for a, (ca, lo_a, hi_a, ia) in enumerate(lines):
            for cb, lo_b, hi_b, ib in lines[a + 1:]:
                gap = cb - ca
                if gap > config.WALL_MAX_T:
                    break
                lo, hi = max(lo_a, lo_b), min(hi_a, hi_b)
                shorter = min(hi_a - lo_a, hi_b - lo_b)
                if gap >= config.WALL_MIN_T and hi - lo >= max(config.MIN_WALL_SEG, 0.5 * shorter):
                    pairs.append((gap, lo - hi, ia, ib, lo, hi, (ca + cb) / 2, shorter))

        for gap, _, ia, ib, lo, hi, c_mid, shorter in sorted(pairs):  # nearest partner first
            for part_lo, part_hi in _free(lo, hi, used[ia] + used[ib]):
                if part_hi - part_lo < config.MIN_WALL_SEG:
                    continue
                used[ia].append((part_lo, part_hi))
                used[ib].append((part_lo, part_hi))
                (sa, conf_a), (sb, conf_b) = candidates[ia], candidates[ib]
                conf = min(conf_a, conf_b) * (0.5 + 0.5 * min(1.0, (part_hi - part_lo) / shorter))
                pieces.append(_Wall(ux, uy, c_mid, part_lo, part_hi, gap, [sa.handle, sb.handle], sa.layer, conf))

        # A long wall-layer line with no partner is a wall drawn as one line (glazing, thin partition).
        for c, lo, hi, index in lines:
            if not used[index] and hi - lo >= config.UNPAIRED_MIN_LEN:
                seg = candidates[index][0]
                pieces.append(_Wall(ux, uy, c, lo, hi, config.SINGLE_LINE_T, [seg.handle], seg.layer,
                                    config.CONF_SINGLE_LINE, "single_line"))
    return pieces


def _merge_walls(pieces):
    """Join pieces on one line. A gap of door size becomes an opening and the wall runs through it."""
    groups = defaultdict(list)
    for w in pieces:
        groups[(round(w.ux, 3), round(w.uy, 3), round(w.c, 2), round(w.t, 2), w.rule)].append(w)
    walls, gaps = [], []
    for group in groups.values():
        group.sort(key=lambda w: w.lo)
        current, weight = group[0], group[0].hi - group[0].lo
        for nxt in group[1:]:
            gap = nxt.lo - current.hi
            if gap <= config.GAP_MAX:
                if gap >= config.GAP_MIN:
                    gaps.append((current, (current.hi + nxt.lo) / 2, gap))
                elif gap > config.MERGE_GAP:  # too small for a door, too big to ignore
                    walls.append(current)
                    current, weight = nxt, nxt.hi - nxt.lo
                    continue
                length = nxt.hi - nxt.lo
                current.conf = (current.conf * weight + nxt.conf * length) / (weight + length)
                weight += length
                current.hi = max(current.hi, nxt.hi)
                current.handles += [h for h in nxt.handles if h not in current.handles]
            else:
                walls.append(current)
                current, weight = nxt, nxt.hi - nxt.lo
        walls.append(current)
    return walls, gaps


def _main_building(walls, extent):
    """Drop walls that are not the building: the sheet frame, the title block, legends.

    extent = (xmin, ymin, xmax, ymax) of everything drawn. Walls are grouped by touching.
    A group is a sheet frame when it spans almost the whole drawing and holds few of the walls.
    Of the rest, the group with the largest footprint is the building; groups inside it stay.
    # ponytail: a frameless drawing whose outer walls span the whole extent, with a loose core
    # holding most of the walls, would lose its shell. Upgrade: detect the title block instead.
    """
    if len(walls) < 2:
        return walls, 0
    merged = unary_union([Polygon(w.corners()).buffer(config.CLUSTER_REACH) for w in walls])
    groups = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    if len(groups) == 1:
        return walls, 0
    members = defaultdict(list)
    for w in walls:
        middle = Point(w.point((w.lo + w.hi) / 2))
        members[next((i for i, g in enumerate(groups) if g.contains(middle)), 0)].append(w)

    width, height = extent[2] - extent[0], extent[3] - extent[1]
    spans = lambda b: b[2] - b[0] >= config.FRAME_SPAN * width and b[3] - b[1] >= config.FRAME_SPAN * height
    frames = {i for i in members if spans(groups[i].bounds)}
    if sum(len(members[i]) for i in frames) >= 0.5 * len(walls):
        frames = set()  # too much of the drawing to be a frame: it is the building
    rest = [i for i in members if i not in frames]

    def footprint(i):
        x1, y1, x2, y2 = groups[i].bounds
        return (any(w.rule == "parallel_pair" for w in members[i]), (x2 - x1) * (y2 - y1))

    # Grow from the largest group: a building drawn in loose pieces (walls cut at every door) is
    # chained together piece by piece; groups inside its extent stay too.
    # ponytail: O(groups^2) per round; fine to a few thousand groups.
    pad = config.CLUSTER_PAD
    building, todo = {max(rest, key=footprint)}, set(rest)
    todo -= building
    grown = True
    while grown:
        grown = False
        shape = unary_union([groups[i] for i in building])
        x1, y1, x2, y2 = shape.bounds
        for i in list(todo):
            a1, b1, a2, b2 = groups[i].bounds
            inside = a1 >= x1 - pad and b1 >= y1 - pad and a2 <= x2 + pad and b2 <= y2 + pad
            if inside or groups[i].distance(shape) <= pad:
                building.add(i)
                todo.discard(i)
                grown = True
    kept = [w for i in building for w in members[i]]
    return kept, len(walls) - len(kept)


def _close_corners(walls):
    """Extend each wall end to the far face of a wall it meets, so corners have no notch."""
    snapshot = [(w.axis, w.ux, w.uy, w.t, w.hi - w.lo) for w in walls]
    for i, wall in enumerate(walls):
        (start, end), ux, uy = snapshot[i][0], wall.ux, wall.uy
        for which, (px, py), (ox, oy) in (("lo", start, (-ux, -uy)), ("hi", end, (ux, uy))):
            best = None
            for j, ((w0, _), wx, wy, wt, wlen) in enumerate(snapshot):
                cross = ox * wy - oy * wx
                if j == i or abs(cross) < 0.5:
                    continue
                dx, dy = w0[0] - px, w0[1] - py
                reach = (dx * wy - dy * wx) / cross  # distance from the end to the other axis
                along = (dx * oy - dy * ox) / cross  # where on the other wall that is
                if abs(reach) <= config.CORNER_REACH and -config.CORNER_REACH <= along <= wlen + config.CORNER_REACH:
                    extend = reach + wt / 2 / abs(cross)
                    if extend > 0 and (best is None or abs(reach) < best[0]):
                        best = (abs(reach), extend)
            if best:
                if which == "lo":
                    wall.lo -= best[1]
                else:
                    wall.hi += best[1]


# ---------------------------------------------------------------- door swings

def _door_swings(drawing):
    """Quarter circles of door size: true arcs, and polylines whose points lie on a circle.

    PDF exports cut one swing into two or more polylines, so pieces sharing a centre are joined.
    Returns [{"hinge": (x, y), "ends": [(x, y), (x, y)], "r": radius, "handle": str}].
    """
    pieces = []  # (cx, cy, r, sweep in degrees, [end points], handle)
    for a in drawing.arcs:
        if config.SWING_R_MIN <= a.r <= config.SWING_R_MAX:
            ends = [(a.cx + a.r * math.cos(math.radians(d)), a.cy + a.r * math.sin(math.radians(d)))
                    for d in (a.a0, a.a1)]
            pieces.append((a.cx, a.cy, a.r, (a.a1 - a.a0) % 360, ends, a.handle))
    chains = defaultdict(list)
    for seg in drawing.segments:
        if seg.poly_id and not seg.closed:
            chains[seg.poly_id].append(seg)
    for segs in chains.values():
        if len(segs) < config.SWING_MIN_EDGES:
            continue
        pts = np.array([(segs[0].x1, segs[0].y1)] + [(seg.x2, seg.y2) for seg in segs])
        if np.ptp(pts, axis=0).max() > 2 * config.SWING_R_MAX:
            continue
        (p, q, c0), *_ = np.linalg.lstsq(np.c_[pts, np.ones(len(pts))], -(pts ** 2).sum(axis=1), rcond=None)
        cx, cy = -p / 2, -q / 2
        r2 = cx * cx + cy * cy - c0
        if r2 <= 0 or not config.SWING_R_MIN <= math.sqrt(r2) <= config.SWING_R_MAX:
            continue
        r = math.sqrt(r2)
        if np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r).max() > config.SWING_FIT_TOL:
            continue
        sweep = math.degrees(sum(seg.length for seg in segs) / r)
        pieces.append((cx, cy, r, sweep, [tuple(pts[0]), tuple(pts[-1])], segs[0].handle))

    joined = []  # pieces around one centre
    for cx, cy, r, sweep, ends, handle in pieces:
        for group in joined:
            if math.dist((cx, cy), group["hinge"]) <= config.SWING_JOIN_TOL and abs(r - group["r"]) <= config.SWING_JOIN_TOL:
                group["sweep"] += sweep
                group["points"] += ends
                break
        else:
            joined.append({"hinge": (float(cx), float(cy)), "r": float(r), "sweep": sweep,
                           "points": list(ends), "handle": handle})
    swings = []
    for group in joined:
        if config.SWING_SWEEP[0] <= group["sweep"] <= config.SWING_SWEEP[1]:
            pts = group["points"]  # the two outermost points are the ends of the whole swing
            a, b = max(((u, v) for u in pts for v in pts), key=lambda pair: math.dist(*pair))
            if math.dist(a, b) < config.SWING_R_MIN / 2:  # pieces that close on themselves are not a door
                continue
            swings.append({"hinge": group["hinge"], "ends": [tuple(map(float, a)), tuple(map(float, b))],
                           "r": group["r"], "handle": group["handle"]})
    return swings


def _host_wall(walls, hinge, end):
    """The wall a door from hinge to end sits in: parallel, on the same line, touching or covering it."""
    length = math.dist(hinge, end)
    vx, vy = (end[0] - hinge[0]) / length, (end[1] - hinge[1]) / length
    mx, my = (hinge[0] + end[0]) / 2, (hinge[1] + end[1]) / 2
    best = None
    for wall in walls:
        if abs(wall.ux * vx + wall.uy * vy) < math.cos(math.radians(config.SWING_PARALLEL_DEG)):
            continue
        off = abs(-wall.uy * mx + wall.ux * my - wall.c)
        a, b = sorted(wall.ux * x + wall.uy * y for x, y in (hinge, end))
        apart = max(wall.lo - b, a - wall.hi, 0.0)
        if off <= wall.t / 2 + config.SWING_WALL_DIST and apart <= config.SWING_WALL_REACH:
            if best is None or (off, apart) < best[0]:
                best = ((off, apart), wall, a, b)
    return best


def _place_swings(swings, walls, gaps, drawing):
    """Turn each door swing into an opening in a wall, unless a gap already stands for that door.

    The host wall is stretched across the door, so a doorway drawn without a break still closes
    the room. A swing with no wall beside it gets a short thin wall of its own. Returns how many
    swings could not be placed.
    """
    lines = np.array([(seg.x1, seg.y1, seg.x2, seg.y2) for seg in drawing.segments]).reshape(-1, 4)
    tol = config.SWING_LEAF_TOL

    def leaf_at(hinge, end):  # a drawn line from the hinge to this end of the swing
        a_h = np.hypot(lines[:, 0] - hinge[0], lines[:, 1] - hinge[1]) < tol
        b_h = np.hypot(lines[:, 2] - hinge[0], lines[:, 3] - hinge[1]) < tol
        a_e = np.hypot(lines[:, 0] - end[0], lines[:, 1] - end[1]) < tol
        b_e = np.hypot(lines[:, 2] - end[0], lines[:, 3] - end[1]) < tol
        return bool((a_h & b_e | b_h & a_e).any())

    wall_gaps = list(gaps)  # only gaps found in the walls can absorb a swing
    unplaced = 0
    for swing in swings:
        hinge, r = swing["hinge"], swing["r"]
        mids = [((hinge[0] + e[0]) / 2, (hinge[1] + e[1]) / 2) for e in swing["ends"]]
        hosts = [_host_wall(walls, hinge, end) for end in swing["ends"]]
        closed = None
        if bool(hosts[0]) != bool(hosts[1]):  # only one end lies along a wall: that is the closed door
            closed = 0 if hosts[0] else 1
        else:  # walls do not decide: the drawn leaf marks the open position
            leaves = [leaf_at(hinge, end) for end in swing["ends"]]
            if leaves[0] != leaves[1]:
                closed = 1 if leaves[0] else 0
            elif hosts[0] and hosts[1]:
                closed = 0 if hosts[0][0] <= hosts[1][0] else 1

        # a gap in the wall at this spot already is this door (one gap can hold both leaves of a double door)
        spots = mids if closed is None else [mids[closed]]
        same = [g for g in wall_gaps if any(
            math.dist(m, g["wall"].point(g["along"])) <= g["width"] / 2 + config.SWING_GAP_SLACK for m in spots)]
        if same:
            same[0]["symbols"].append(swing["handle"])
            continue
        if closed is None:
            unplaced += 1
            continue

        end, host = swing["ends"][closed], hosts[closed]
        if host:
            _, wall, a, b = host
            wall.lo, wall.hi = min(wall.lo, a), max(wall.hi, b)
        else:  # free-standing door: give it a thin wall to sit in
            vx, vy = (end[0] - hinge[0]) / r, (end[1] - hinge[1]) / r
            if vx < 0 or (abs(vx) < 1e-9 and vy < 0):
                vx, vy = -vx, -vy
            a, b = sorted(vx * x + vy * y for x, y in (hinge, end))
            wall = _Wall(vx, vy, -vy * hinge[0] + vx * hinge[1], a, b, config.SINGLE_LINE_T,
                         [swing["handle"]], "", config.CONF_SINGLE_LINE, "door_stub")
            walls.append(wall)
        gaps.append({"wall": wall, "along": (a + b) / 2, "width": b - a, "origin": "swing",
                     "symbols": [swing["handle"]]})
    return unplaced


# ---------------------------------------------------------------- openings

def _classify_openings(gaps, drawing, roles, family, used_labels, flags):
    role = {name: entry["role"] for name, entry in roles["layers"].items()}
    door_things = [((s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2, s.handle) for s in drawing.segments if role.get(s.layer) == "door"]
    door_things += [(b.x, b.y, b.handle) for b in drawing.blockrefs
                    if role.get(b.layer) == "door" or rule_role(b.name) == "door"]
    window_things = [((s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2, s.handle) for s in drawing.segments if role.get(s.layer) == "window"]
    window_things += [(b.x, b.y, b.handle) for b in drawing.blockrefs if role.get(b.layer) == "window"]
    # "HL: 300 (ouverture: 230, ...)": the opening is the door, HL includes the panel above it
    heights = [(label, int(m.group(1)) / 100, rank) for label in drawing.labels
               for rank, pattern in enumerate((OUVERTURE_RE, HEIGHT_RE)) if (m := pattern.search(label.text))]

    openings = [{"wall": g["wall"], "center": g["wall"].point(g["along"]), "width": g["width"], "tag": None,
                 "handles": [], "origin": g["origin"], "swings": g["symbols"]} for g in gaps]

    tags = []  # (text, centre, handles); tags glued into one label are taken apart again
    for label in drawing.labels:
        words = label.text.split()
        if words and all(DOOR_TAG_RE.match(word) for word in words):
            used_labels.add(id(label))
            x, y = _center(label)
            step = (len(label.text) / len(words)) * config.CHAR_W_MIN * label.height
            angle = math.radians(label.rotation)
            for k, word in enumerate(words):
                shift = (k - (len(words) - 1) / 2) * step
                tags.append((word, (x + shift * math.cos(angle), y + shift * math.sin(angle)), label.handles))
    reach = sorted((math.dist(at, o["center"]), ti, oi)
                   for ti, (_, at, _) in enumerate(tags) for oi, o in enumerate(openings))
    tagged = set()
    for distance, ti, oi in reach:  # each tag goes to its nearest free opening
        if distance > config.DOOR_TAG_DIST:
            break
        if ti not in tagged and openings[oi]["tag"] is None:
            tagged.add(ti)
            openings[oi]["tag"] = tags[ti][0]
            openings[oi]["handles"] += tags[ti][2]
    for ti, (text, _, _) in enumerate(tags):
        if ti not in tagged:
            flags.append({"element_id": "file", "severity": "review",
                          "reason": f"door tag without opening: {text}"})

    def near(things, center, limit):
        return [h for x, y, h in things if math.dist((x, y), center) <= limit]

    result = []
    for o in openings:
        center, width = o["center"], o["width"]
        symbols = near(door_things, center, config.DOOR_SYMBOL_DIST) + o["swings"]
        windows = near(window_things, center, config.WINDOW_DIST)
        sill, default_height = 0.0, True
        if o["origin"] == "swing":
            kind, conf, rule = ("door", 0.85, "swing+tag") if o["tag"] else ("door", 0.70, "swing")
        elif o["tag"] and symbols:
            kind, conf, rule = "door", 0.90, "gap+tag+symbol"
        elif o["tag"]:
            kind, conf, rule = "door", 0.85, "gap+tag"
        elif symbols:
            kind, conf, rule = "door", 0.80, "gap+symbol"
        elif windows:
            kind, conf, rule = "window", 0.75, "gap+window_layer"
        else:
            kind, conf, rule = "opening", 0.40, "gap_only"

        height = config.DEFAULT_DOOR_HEIGHT
        if kind == "window":
            sill, height = config.DEFAULT_WINDOW_SILL, config.DEFAULT_WINDOW_HEIGHT
        else:
            close = [(rank, math.dist(_center(label), center), value, label) for label, value, rank in heights]
            close = [item for item in close if item[1] <= config.DOOR_HEIGHT_DIST]
            if close:
                _, _, height, label = min(close, key=lambda item: item[:2])
                used_labels.add(id(label))
                default_height = False
        if default_height:
            conf = min(conf, config.CONF_DEFAULT_CAP)
        handles = o["handles"] + [h for h in dict.fromkeys(symbols + windows) if h][:5]
        result.append({"kind": kind, "wall": o["wall"], "center": center, "width_m": width,
                       "height_m": height, "sill_m": sill, "tag": o["tag"],
                       "confidence": _cap(conf, family), "rule": rule, "default_height": default_height,
                       "src": {"layer": o["wall"].layer, "handles": handles}})
    return result


# ---------------------------------------------------------------- rooms

def _room_seeds(drawing, used_labels):
    """Room labels: a name with an area next to it, a bare area, or a known room word."""
    def area_of(label):
        match = AREA_RE.search(label.text)
        return float(match.group(1).replace(",", ".")) if match else None

    def plain(label):
        return not (area_of(label) or DOOR_TAG_RE.match(label.text) or LEVEL_RE.match(label.text)
                    or STOREY_RE.search(label.text) or HEIGHT_RE.search(label.text))

    names = [label for label in drawing.labels if plain(label) and re.search(r"[A-Za-zÀ-ü]{2}", label.text)]
    seeds, taken = [], set()
    for label in drawing.labels:
        area = area_of(label)
        if area is None:
            continue
        used_labels.add(id(label))
        own_name = AREA_RE.sub("", label.text).strip(" -:/")
        if re.search(r"[A-Za-zÀ-ü]{2}", own_name):  # "BUREAU 1 32.2 m2" in one text
            seeds.append({"name": own_name, "area": area, "at": _center(label), "labels": [label]})
            continue
        limit = config.ROOM_NAME_DIST * (label.height or 0.25)
        # compare text middles: a name and its area are usually centred on each other
        near = [(math.dist(_center(n), _center(label)), n) for n in names
                if id(n) not in taken and n.y >= label.y - 0.5 * (label.height or 0.25)]
        near = [item for item in near if item[0] <= limit]
        if near:
            name = min(near, key=lambda item: item[0])[1]
            taken.add(id(name))
            used_labels.add(id(name))
            seeds.append({"name": name.text, "area": area, "at": _center(name), "labels": [name, label]})
        else:
            seeds.append({"name": None, "area": area, "at": _center(label), "labels": [label]})
    for name in names:
        if id(name) not in taken and ROOM_RE.search(name.text):
            seeds.append({"name": name.text, "area": None, "at": _center(name), "labels": [name]})
    return seeds


def _rooms(walls, seeds, used_labels, flags, barriers=()):
    """Raster the walls, then every enclosed free region is a room; labels inside name it."""
    if not walls:
        return []
    corners = np.array([p for w in walls for p in w.corners()])
    (x_min, y_min), (x_max, y_max) = corners.min(axis=0), corners.max(axis=0)
    margin = config.RASTER_MARGIN
    px = max(config.RASTER_PX, max(x_max - x_min, y_max - y_min) / config.RASTER_MAX_SIDE)
    x0, y0 = x_min - margin, y_min - margin
    width = math.ceil((x_max - x_min + 2 * margin) / px)
    height = math.ceil((y_max - y_min + 2 * margin) / px)
    image = np.zeros((height, width), np.uint8)
    sub = 16  # sub-pixel vertices; whole-pixel rounding shrank rooms by up to 3 %
    for w in walls:
        pts = np.round((np.array(w.corners()) - (x0, y0)) / px * sub - sub / 2).astype(np.int32)
        cv2.fillPoly(image, [pts], 255, shift=4)
    for seg in barriers:  # window lines close the facade where glazing is wider than a door gap
        a = np.round((np.array([[seg.x1, seg.y1], [seg.x2, seg.y2]]) - (x0, y0)) / px * sub - sub / 2).astype(np.int32)
        cv2.line(image, tuple(a[0]), tuple(a[1]), 255, 1, shift=4)

    count, labels, stats, _ = cv2.connectedComponentsWithStats((image == 0).astype(np.uint8), connectivity=4)
    outside = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))) - {0}

    def region_at(x, y):
        col, row = int((x - x0) / px), int((y - y0) / px)
        if not (0 <= col < width and 0 <= row < height):
            return None
        if labels[row, col]:
            return int(labels[row, col])
        r = round(config.SEED_SEARCH / px)  # the label sits on a wall: take the nearest free pixel
        r0, c0 = max(0, row - r), max(0, col - r)
        window = labels[r0:row + r + 1, c0:col + r + 1]
        rows, cols = np.nonzero(window)
        if not len(rows):
            return None
        nearest = np.argmin((rows + r0 - row) ** 2 + (cols + c0 - col) ** 2)
        return int(window[rows[nearest], cols[nearest]])

    by_region = defaultdict(list)
    for seed in seeds:
        x, y = seed["at"]
        inside_plan = x_min <= x <= x_max and y_min <= y <= y_max
        region = region_at(x, y) if inside_plan else None
        if region is None or region in outside:
            if inside_plan:  # labels outside the building (title block, legend) are simply not rooms
                flags.append({"element_id": "file", "severity": "review",
                              "reason": f"room not closed: {seed['name'] or 'unnamed'}"
                                        + (f" ({seed['area']} m2 printed)" if seed["area"] else "")})
                used_labels.update(id(label) for label in seed["labels"])
            continue
        by_region[region].append(seed)
        used_labels.update(id(label) for label in seed["labels"])

    rooms, unnamed = [], 0
    for region in range(1, count):
        area = float(stats[region, cv2.CC_STAT_AREA]) * px * px
        found = by_region.get(region, [])
        if region in outside or (not found and area < config.ROOM_MIN_AREA):
            continue
        left, top = stats[region, cv2.CC_STAT_LEFT], stats[region, cv2.CC_STAT_TOP]
        w, h = stats[region, cv2.CC_STAT_WIDTH], stats[region, cv2.CC_STAT_HEIGHT]
        mask = (labels[top:top + h, left:left + w] == region).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        outline = cv2.approxPolyDP(max(contours, key=cv2.contourArea), config.ROOM_SIMPLIFY / px, True)
        if len(outline) < 3:
            continue
        polygon = [[round(x0 + (left + c + 0.5) * px, 3), round(y0 + (top + r + 0.5) * px, 3)]
                   for c, r in outline[:, 0, :]]
        # the wall raster covers every pixel it touches: give back half a pixel along the outline
        area += 0.5 * cv2.arcLength(max(contours, key=cv2.contourArea), True) * px * px

        named = [s["name"] for s in found if s["name"]]
        printed = [s["area"] for s in found if s["area"]]
        if found:
            name = " / ".join(named) if named else f"Room {len(rooms) + 1}"
            area_printed = round(sum(printed), 2) if printed and len(printed) == len(found) else None
        else:
            unnamed += 1
            name, area_printed = f"Unnamed {unnamed}", None
        if len(found) > 1:
            flags.append({"element_id": "file", "severity": "review",
                          "reason": f"several room labels in one closed space: {name}"})
        if not found:
            conf, rule = 0.40, "enclosed_region"
        elif area_printed is None:
            conf, rule = 0.60, "flood_fill"
        else:
            off = abs(area - area_printed) / area_printed
            conf, rule = (0.95 if off < 0.03 else 0.70 if off < 0.10 else 0.30), "flood_fill"
        source_labels = [label for s in found for label in s["labels"]]
        rooms.append({"name": name, "polygon": polygon, "area_m2": round(area, 2),
                      "area_printed_m2": area_printed, "confidence": conf, "rule": rule,
                      "src": {"layer": source_labels[0].layer if source_labels else "",
                              "handles": [h for label in source_labels for h in label.handles]}})
    return rooms


# ---------------------------------------------------------------- stairs

def _tread_runs(segments):
    """Runs of parallel lines of equal length, a step apart: the treads of a flight."""
    bins = defaultdict(list)
    for seg in segments:
        angle = math.degrees(math.atan2(seg.y2 - seg.y1, seg.x2 - seg.x1)) % 180
        bins[round(angle / config.ANGLE_TOL) % round(180 / config.ANGLE_TOL)].append(seg)
    runs = []
    for members in bins.values():
        if len(members) < config.TREAD_MIN_COUNT:
            continue
        a = math.radians(statistics.median(math.degrees(math.atan2(s.y2 - s.y1, s.x2 - s.x1)) % 180 for s in members))
        ux, uy = math.cos(a), math.sin(a)
        lines = sorted(((-uy * (s.x1 + s.x2) / 2 + ux * (s.y1 + s.y2) / 2,
                         min(ux * s.x1 + uy * s.y1, ux * s.x2 + uy * s.y2),
                         max(ux * s.x1 + uy * s.y1, ux * s.x2 + uy * s.y2), s) for s in members),
                       key=lambda line: line[:3])
        used = set()
        for i, (c, lo, hi, seg) in enumerate(lines):
            if i in used:
                continue
            run, last = [i], (c, lo, hi)
            for j in range(i + 1, len(lines)):
                cj, loj, hij, _ = lines[j]
                gap = cj - last[0]
                if gap > config.TREAD_GAP_MAX:
                    break
                same = abs((hij - loj) - (last[2] - last[1])) <= config.TREAD_LEN_TOL * (last[2] - last[1])
                overlap = min(hij, last[2]) - max(loj, last[1]) >= 0.8 * (last[2] - last[1])
                if j not in used and gap >= config.TREAD_GAP_MIN and same and overlap:
                    run.append(j)
                    last = (cj, loj, hij)
            if len(run) >= config.TREAD_MIN_COUNT:
                used.update(run)
                runs.append([lines[k][3] for k in run])
    return runs


def _stairs(drawing, roles, flags, building):
    """Stair outlines, from stair layers or from tread runs. No steps or rise are modelled.
    Only stairs whose centre is inside the building's extent count: ramps, kerbs and site steps are outside it."""
    x1, y1, x2, y2 = building
    inside = lambda shape: x1 <= shape.centroid.x <= x2 and y1 <= shape.centroid.y <= y2
    stair_layers = {name for name, entry in roles["layers"].items() if entry["role"] == "stair"}
    found = []  # (polygon, confidence, rule, segments)
    on_layer = [s for s in drawing.segments if s.layer in stair_layers]
    if on_layer:
        merged = unary_union([LineString([(s.x1, s.y1), (s.x2, s.y2)]).buffer(config.STAIR_LAYER_REACH) for s in on_layer])
        for shape in getattr(merged, "geoms", [merged]):
            outline = shape.buffer(-config.STAIR_LAYER_REACH).minimum_rotated_rectangle
            if outline.area >= config.STAIR_MIN_AREA and inside(outline):
                parts = [s for s in on_layer if shape.contains(Point((s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2))]
                found.append((outline, config.CONF_STAIR_LAYER, "stair_layer", parts))
    candidates = [s for s in drawing.segments  # stair-layer lines were judged above, as one outline
                  if config.TREAD_LEN_MIN <= s.length <= config.TREAD_LEN_MAX and s.layer not in stair_layers]
    if len(candidates) <= config.MAX_TREAD_CANDIDATES:
        flights = [(unary_union([LineString([(s.x1, s.y1), (s.x2, s.y2)]) for s in run]).minimum_rotated_rectangle, run)
                   for run in _tread_runs(candidates)]
        joined = unary_union([outline.buffer(config.FLIGHT_JOIN / 2) for outline, _ in flights])
        for area in getattr(joined, "geoms", [joined]) if flights else []:  # flights of one stair core
            members = [(o, run) for o, run in flights if area.intersects(o)]
            outline = unary_union([o for o, _ in members]).minimum_rotated_rectangle
            if (outline.area >= config.FLIGHT_MIN_AREA and inside(outline)
                    and not any(outline.intersects(other) for other, *_ in found)):
                found.append((outline, config.CONF_STAIR_TREADS, "tread_run", [s for _, run in members for s in run]))
    stairs = []
    for polygon, conf, rule, parts in found:
        points = [[round(x, 3), round(y, 3)] for x, y in list(polygon.exterior.coords)[:-1]]
        stairs.append({"id": f"s{len(stairs) + 1}", "polygon": points, "confidence": conf, "rule": rule,
                       "src": {"layer": parts[0].layer, "handles": list(dict.fromkeys(s.handle for s in parts))}})
    if stairs:
        flags.append({"element_id": "file", "severity": "review",
                      "reason": f"{len(stairs)} stairs are modelled as a block outline only (no steps, no rise); "
                                "check their position"})
    return stairs


# ---------------------------------------------------------------- fire safety

def _fire_kind(*names):
    text = " ".join(names)
    return next((kind for kind, pattern in config.FIRE_KINDS if re.search(pattern, text, re.IGNORECASE)), "fire_equipment")


def _fire_symbols(drawing, building, flags):
    """Extinguishers, detectors and exit signs: one per block, or per small line cluster, on a fire layer.
    Only symbols inside the building's extent count; legends and title blocks sit outside it."""
    x1, y1, x2, y2 = building
    inside = lambda x, y: x1 <= x <= x2 and y1 <= y <= y2
    found = []  # (kind, x, y, confidence, rule, layer, handles)
    for ref in drawing.blockrefs:
        if (FIRE_RE.search(ref.layer) or FIRE_RE.search(ref.name)) and inside(ref.x, ref.y):
            found.append((_fire_kind(ref.layer, ref.name), ref.x, ref.y, config.CONF_FIRE_BLOCK, "fire_block",
                          ref.layer, [ref.handle]))
    from_blocks = {layer for *_, layer, _ in found}
    fire_layer = lambda item: item.block is None and FIRE_RE.search(item.layer) and item.layer not in from_blocks
    loose = [(LineString([(g.x1, g.y1), (g.x2, g.y2)]), g) for g in drawing.segments if fire_layer(g)]
    loose += [(Point(a.cx, a.cy).buffer(a.r).exterior, a) for a in drawing.arcs if fire_layer(a)]  # circles, arcs
    if loose:
        merged = unary_union([shape.buffer(config.FIRE_CLUSTER_REACH) for shape, _ in loose])
        for shape in getattr(merged, "geoms", [merged]):
            a1, b1, a2, b2 = shape.bounds
            span = max(a2 - a1, b2 - b1) - 2 * config.FIRE_CLUSTER_REACH
            cx, cy = (a1 + a2) / 2, (b1 + b2) / 2
            if span <= config.FIRE_SYMBOL_MAX and inside(cx, cy):
                parts = [g for line, g in loose if shape.contains(line.centroid)]
                if parts:
                    found.append((_fire_kind(parts[0].layer), cx, cy, config.CONF_FIRE_CLUSTER, "fire_cluster",
                                  parts[0].layer, list(dict.fromkeys(g.handle for g in parts))))
    symbols = [{"id": f"f{i + 1}", "kind": kind, "position": [round(x, 3), round(y, 3)], "confidence": conf,
                "rule": rule, "src": {"layer": layer, "handles": handles}}
               for i, (kind, x, y, conf, rule, layer, handles) in enumerate(sorted(found, key=lambda f: (f[1], f[2])))]
    if symbols:
        counts = Counter(s["kind"].replace("_", " ") for s in symbols)
        flags.append({"element_id": "file", "severity": "review",
                      "reason": "fire-safety symbols placed from their drawing symbol, position only: "
                                + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))})
    return symbols


# ---------------------------------------------------------------- storey, slab

def _storey(drawing, elevation, height, used_labels, flags):
    name = None
    for label in drawing.labels:
        if STOREY_RE.search(label.text) and len(label.text) <= 40:
            name = name or label.text
            used_labels.add(id(label))
    if not name:
        match = re.search(r"NIV\d+", Path(drawing.path).stem, re.IGNORECASE)
        name = match.group(0).upper() if match else drawing.project

    confidence = 0.9
    if elevation is None:
        levels = [label for label in drawing.labels if LEVEL_RE.match(label.text)]
        used_labels.update(id(label) for label in levels)
        values = [float(label.text.replace(" ", "").replace(",", ".")) for label in levels]
        if values:
            counts = Counter(values).most_common()
            top = [value for value, n in counts if n == counts[0][1]]
            elevation = statistics.median_low(top)
            distinct = sorted(set(values))
            confidence = 0.5
            flags.append({"element_id": "storey", "severity": "review" if len(distinct) > 1 else "info",
                          "reason": f"storey elevation {elevation:+.2f} m read from level text; "
                                    f"values on the drawing: {', '.join(f'{v:+.2f}' for v in distinct)}"})
        else:
            elevation, confidence = 0.0, 0.3
            flags.append({"element_id": "storey", "severity": "review",
                          "reason": "no level text found; storey elevation set to 0.00 m"})
    height_source = "argument"
    if height is None:
        height, height_source, confidence = config.DEFAULT_STOREY_HEIGHT, "default", min(confidence, config.CONF_DEFAULT_CAP)
        flags.append({"element_id": "storey", "severity": "review",
                      "reason": f"storey height is a default ({height:.2f} m)"})
    return {"name": name, "elevation_m": float(elevation), "height_m": float(height),
            "height_source": height_source, "confidence": confidence}


def _slab(drawing, walls, rooms, flags):
    shapes = [Polygon(w.corners()) for w in walls] + [Polygon(r["polygon"]) for r in rooms]
    shapes = [s if s.is_valid else s.buffer(0) for s in shapes]
    merged = unary_union(shapes).buffer(config.SLAB_CLOSE).buffer(-config.SLAB_CLOSE) if shapes else None
    if merged is not None and not merged.is_empty:
        largest = max(merged.geoms, key=lambda g: g.area) if hasattr(merged, "geoms") else merged
        boundary = largest.exterior.simplify(0.02)
        rule = "default_thickness"
    else:  # nothing to outline: use the drawing's extent so the file is still valid
        xs = [v for s in drawing.segments for v in (s.x1, s.x2)] or [0.0, 1.0]
        ys = [v for s in drawing.segments for v in (s.y1, s.y2)] or [0.0, 1.0]
        boundary = Polygon([(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]).exterior
        rule = "drawing_extent"
    flags.append({"element_id": "slab", "severity": "review",
                  "reason": f"slab thickness is a default ({config.DEFAULT_SLAB_THICKNESS:.2f} m)"})
    slab = {"outline": [[round(x, 3), round(y, 3)] for x, y in list(boundary.coords)[:-1]],
            "thickness_m": config.DEFAULT_SLAB_THICKNESS,
            "confidence": 0.4 if rule == "default_thickness" else 0.1, "rule": rule}
    return slab, boundary


def _is_external(wall, boundary):
    (x1, y1), (x2, y2) = wall.axis
    points = [(x1 + (x2 - x1) * f, y1 + (y2 - y1) * f) for f in (0.1, 0.3, 0.5, 0.7, 0.9)]
    return sum(boundary.distance(Point(p)) <= config.EXTERNAL_DIST for p in points) >= 3


# ---------------------------------------------------------------- assemble

def extract(drawing, roles, elevation=None, height=None) -> dict:
    family = roles["family"]
    flags, used_labels = [], set()
    for warning in list(drawing.warnings) + list(roles.get("warnings", [])):
        flags.append({"element_id": "file", "severity": "review", "reason": warning})
    if family == "colour_only":
        flags.append({"element_id": "file", "severity": "review",
                      "reason": "layers carry no meaning in this file; walls were guessed from line weight"})

    candidates = _wall_candidates(drawing, roles)
    if len(candidates) > config.MAX_WALL_CANDIDATES:  # pairing would take hours; no floor plan has this many
        raise ValueError(f"{Path(drawing.path).name}: {len(candidates)} lines on the wall layers; "
                         "this looks like hatching or a detail sheet, not a floor plan")
    walls, gaps = _merge_walls(_pair_walls(candidates))
    xs = [v for seg in drawing.segments for v in (seg.x1, seg.x2)] or [0.0]
    ys = [v for seg in drawing.segments for v in (seg.y1, seg.y2)] or [0.0]
    walls, dropped = _main_building(walls, (min(xs), min(ys), max(xs), max(ys)))
    kept = {id(w) for w in walls}
    gaps = [{"wall": wall, "along": along, "width": width, "origin": "gap", "symbols": []}
            for wall, along, width in gaps if id(wall) in kept]
    explained = sum((w.hi - w.lo) * (2 if w.rule == "parallel_pair" else 1) for w in walls)
    unplaced = _place_swings(_door_swings(drawing), walls, gaps, drawing) if walls else 0
    if unplaced:
        flags.append({"element_id": "file", "severity": "review",
                      "reason": f"{unplaced} door swings could not be tied to a wall and were left out"})
    _close_corners(walls)
    if not walls:
        flags.append({"element_id": "file", "severity": "review", "reason": "no walls found"})
    if dropped:
        flags.append({"element_id": "file", "severity": "info",
                      "reason": f"{dropped} walls outside the main building were ignored "
                                "(sheet frame, title block, legend)"})
    singles = [w for w in walls if w.rule == "single_line"]
    if singles:
        flags.append({"element_id": "file", "severity": "review",
                      "reason": f"{len(singles)} walls ({sum(w.hi - w.lo for w in singles):.0f} m) are drawn as a "
                                f"single line; they were given a nominal thickness of {config.SINGLE_LINE_T:.2f} m"})

    openings = _classify_openings(gaps, drawing, roles, family, used_labels, flags)
    windows = {name for name, entry in roles["layers"].items() if entry["role"] == "window"}
    barriers = [seg for seg in drawing.segments if seg.layer in windows] if walls else []
    rooms = _rooms(walls, _room_seeds(drawing, used_labels), used_labels, flags, barriers)
    if walls and not rooms:
        flags.append({"element_id": "file", "severity": "review",
                      "reason": "no closed room found; this drawing may be an elevation, a section or a site plan"})
    storey = _storey(drawing, elevation, height, used_labels, flags)
    slab, boundary = _slab(drawing, walls, rooms, flags)

    walls.sort(key=lambda w: tuple(round(v, 2) for v in w.axis[0] + w.axis[1]))
    wall_id = {id(w): f"w{i + 1}" for i, w in enumerate(walls)}
    plan_walls = [{"id": wall_id[id(w)], "axis": [[round(v, 3) for v in p] for p in w.axis],
                   "thickness_m": round(w.t, 3),
                   "is_external": _is_external(w, boundary) if slab["rule"] == "default_thickness" else None,
                   "confidence": round(_cap(w.conf, family), 2), "rule": w.rule,
                   "src": {"layer": w.layer, "handles": w.handles}} for w in walls]

    openings.sort(key=lambda o: tuple(round(v, 2) for v in o["center"]))
    plan_openings = []
    defaulted = Counter("window" if o["kind"] == "window" else "door" for o in openings if o["default_height"])
    doors_total = sum(o["kind"] != "window" for o in openings)
    if defaulted["door"]:  # one line for all of them: a flag per door would bury the rest of the list
        flags.append({"element_id": "file", "severity": "review",
                      "reason": f"door height is a default ({config.DEFAULT_DOOR_HEIGHT:.2f} m) for "
                                f"{defaulted['door']} of {doors_total} doors and openings"})
    if defaulted["window"]:
        flags.append({"element_id": "file", "severity": "review",
                      "reason": f"window sill ({config.DEFAULT_WINDOW_SILL:.2f} m) and height "
                                f"({config.DEFAULT_WINDOW_HEIGHT:.2f} m) are defaults for {defaulted['window']} windows"})
    for i, o in enumerate(openings):
        oid = f"o{i + 1}"
        del o["default_height"]
        if o["kind"] == "opening":
            flags.append({"element_id": oid, "severity": "review",
                          "reason": f"gap of {o['width_m']:.2f} m in a wall with no door tag or symbol"})
        wall = o.pop("wall")
        plan_openings.append({"id": oid, **o, "wall_id": wall_id[id(wall)],
                              "center": [round(v, 3) for v in o["center"]], "width_m": round(o["width_m"], 3)})

    rooms.sort(key=lambda r: (min(p[0] for p in r["polygon"]), min(p[1] for p in r["polygon"])))
    for i, room in enumerate(rooms):
        room["id"] = f"r{i + 1}"
        if room["area_printed_m2"] and room["confidence"] <= 0.30:
            flags.append({"element_id": room["id"], "severity": "review",
                          "reason": f"room area {room['area_m2']} m2 differs from the printed {room['area_printed_m2']} m2"})

    if walls:
        corners = np.array([p for w in walls for p in w.corners()])
        building = (*corners.min(axis=0), *corners.max(axis=0))
        stairs, fire = _stairs(drawing, roles, flags, building), _fire_symbols(drawing, building, flags)
    else:
        stairs, fire = [], []
    role_of = {entry["role"] for entry in roles["layers"].values()}
    for missing in sorted(role_of & {"column"}):
        flags.append({"element_id": "file", "severity": "info",
                      "reason": f"{missing} layers exist but {missing}s are not modelled"})

    plan = {
        "schema_version": 1,
        "source": {"file": Path(drawing.path).name, "project": drawing.project, "units_in": drawing.units_in,
                   "scale_to_m": drawing.scale_to_m, "origin_offset": list(drawing.origin_offset), "family": family},
        "storey": storey, "walls": plan_walls, "openings": plan_openings,
        "rooms": [{"id": r["id"], **{k: v for k, v in r.items() if k != "id"}} for r in rooms],
        "slab": slab, "stairs": stairs, "fire": fire, "flags": flags,
        "stats": {"wall_layer_length_m": round(sum(s.length for s, _ in candidates), 2),
                  "wall_explained_length_m": round(explained, 2),
                  "labels_total": len(drawing.labels),
                  "labels_unused": sum(id(label) not in used_labels for label in drawing.labels),
                  "runtime_s": 0.0,
                  "llm_calls": int(roles["llm"]["provider"] != "none" and not roles["llm"]["cached"])},
    }
    # for the review page: texts nothing used may hold information the rules missed
    plan["unused_text"] = [label.text[:60] for label in drawing.labels
                           if id(label) not in used_labels and len(label.text) >= 2][:config.REVIEW_MAX_UNUSED_TEXT]
    errors = validate_plan(plan)
    if errors:
        raise ValueError("extract produced an invalid plan: " + "; ".join(errors[:5]))
    return plan
