"""Read any DXF into one uniform Drawing: metres, local origin, blocks flattened.

After this step no other module needs to know which DXF family the file came from.
"""

import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf import recover

from . import config

logging.getLogger("ezdxf").setLevel(logging.ERROR)  # flattening blocks is chatty about DIMASSOC


@dataclass
class Segment:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str  # xref prefix removed, upper case
    raw_layer: str
    handle: str  # handle of the top-level entity
    lineweight: int  # resolved through BYLAYER; -1 if unknown
    poly_id: str | None  # handle of the parent polyline, if any
    closed: bool  # the parent polyline is closed
    block: str | None  # top-level block name if it came from an INSERT

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


@dataclass
class Arc:
    cx: float
    cy: float
    r: float
    a0: float  # degrees, counter-clockwise from a0 to a1
    a1: float
    layer: str
    raw_layer: str
    handle: str
    block: str | None


@dataclass
class Label:
    text: str
    x: float
    y: float
    height: float
    rotation: float
    layer: str
    handles: list[str]


@dataclass
class BlockRef:
    name: str
    x: float
    y: float
    rotation: float
    layer: str
    handle: str


@dataclass
class Fill:
    polygon: list[tuple[float, float]]
    layer: str
    handle: str
    solid: bool


@dataclass
class Drawing:
    path: str
    project: str
    units_in: str
    scale_to_m: float
    origin_offset: tuple[float, float]  # lower-left corner, in input units
    segments: list[Segment] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    blockrefs: list[BlockRef] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    layer_stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def clean_layer(raw: str) -> str:
    """'BAT 4$0$Mur' (layer from a bound xref) -> 'MUR'."""
    return raw.rsplit("$0$", 1)[-1].strip().upper()


def _read(path: Path):
    if not path.is_file():
        raise ValueError(f"{path.name}: file not found")
    size_mb = path.stat().st_size / 1e6
    if size_mb > config.MAX_DXF_MB:
        raise ValueError(f"{path.name}: {size_mb:.0f} MB is over the {config.MAX_DXF_MB} MB limit")
    try:
        return ezdxf.readfile(path)
    except ezdxf.DXFStructureError:
        try:
            return recover.readfile(path)[0]
        except (ezdxf.DXFError, OSError) as err:
            raise ValueError(f"{path.name}: damaged DXF ({err})") from err
    except (ezdxf.DXFError, OSError, UnicodeDecodeError) as err:
        raise ValueError(f"{path.name}: not a DXF file ({err})") from err


class _Collector:
    """Walks modelspace, flattens INSERTs, and gathers primitives in input units."""

    def __init__(self, doc):
        self.doc = doc
        self.drawing = Drawing("", "", "", 1.0, (0.0, 0.0))
        self.types = defaultdict(Counter)  # raw layer -> entity type -> count
        self.count = 0
        self.capped = False

    def lineweight(self, entity, raw_layer):
        weight = entity.dxf.get("lineweight", -1)
        if weight < 0 and raw_layer in self.doc.layers:  # BYLAYER / BYBLOCK / default
            weight = self.doc.layers.get(raw_layer).dxf.get("lineweight", -1)
        return weight if weight >= 0 else -1

    def walk(self, entity, inherit=None, top=None, depth=0):
        """inherit: layer taken by content drawn on layer 0. top: (handle, block) of the top INSERT."""
        if self.count >= config.MAX_FLAT_ENTITIES:
            self.capped = True
            return
        self.count += 1
        kind = entity.dxftype()
        raw_layer = entity.dxf.get("layer", "0")
        if raw_layer == "0" and inherit:
            raw_layer = inherit
        handle, block = top or (entity.dxf.handle or "", None)
        self.types[raw_layer][kind] += 1
        d = self.drawing

        if kind == "INSERT":
            if depth >= config.MAX_BLOCK_DEPTH:
                return
            pos = entity.dxf.insert
            d.blockrefs.append(BlockRef(entity.dxf.name, pos.x, pos.y, entity.dxf.get("rotation", 0.0),
                                        raw_layer, handle))
            child_top = top or (handle, entity.dxf.name)
            try:
                children = list(entity.attribs) + list(entity.virtual_entities())
            except Exception:  # a broken block must not stop the whole file
                d.warnings.append(f"block {entity.dxf.name} could not be read")
                return
            for child in children:
                self.walk(child, raw_layer, child_top, depth + 1)

        elif kind == "LINE":
            self.segment(entity.dxf.start, entity.dxf.end, entity, raw_layer, handle, block)

        elif kind == "LWPOLYLINE" or (kind == "POLYLINE" and entity.is_2d_polyline):
            poly_id = entity.dxf.handle or handle
            weight = self.lineweight(entity, raw_layer)
            for part in entity.virtual_entities():  # LINE per edge, ARC per bulged edge
                if part.dxftype() == "LINE":
                    self.segment(part.dxf.start, part.dxf.end, entity, raw_layer, handle, block,
                                 poly_id=poly_id, closed=bool(entity.is_closed), weight=weight)
                elif part.dxftype() == "ARC":
                    self.arc(part, raw_layer, handle, block)

        elif kind in ("ARC", "CIRCLE"):
            self.arc(entity, raw_layer, handle, block)

        elif kind in ("TEXT", "ATTRIB", "MTEXT"):
            if kind == "MTEXT":
                text, height = entity.plain_text().replace("\n", " "), entity.dxf.get("char_height", 0.0)
            else:
                text, height = entity.dxf.get("text", ""), entity.dxf.get("height", 0.0)
            pos = entity.dxf.get("insert")
            if text.strip() and pos is not None:
                d.labels.append(Label(text.strip(), pos.x, pos.y, height, entity.dxf.get("rotation", 0.0),
                                      raw_layer, [handle]))

        elif kind == "HATCH":
            for boundary in entity.paths:
                vertices = getattr(boundary, "vertices", None)  # polyline paths only
                if vertices and len(vertices) >= 3:
                    d.fills.append(Fill([(v[0], v[1]) for v in vertices], raw_layer, handle,
                                        bool(entity.dxf.get("solid_fill", 0))))
        # everything else (DIMENSION, SPLINE, ELLIPSE, ...) is only counted

    def segment(self, a, b, entity, raw_layer, handle, block, poly_id=None, closed=False, weight=None):
        if weight is None:
            weight = self.lineweight(entity, raw_layer)
        self.drawing.segments.append(Segment(a.x, a.y, b.x, b.y, raw_layer, raw_layer, handle,
                                             weight, poly_id, closed, block))

    def arc(self, entity, raw_layer, handle, block):
        center = entity.dxf.center
        a0, a1 = (0.0, 360.0) if entity.dxftype() == "CIRCLE" else (entity.dxf.start_angle, entity.dxf.end_angle)
        if entity.dxf.get("extrusion", (0, 0, 1))[2] < 0:  # mirrored block: flip back to world
            center = entity.ocs().to_wcs(center)
            a0, a1 = 180.0 - a1, 180.0 - a0
        self.drawing.arcs.append(Arc(center.x, center.y, entity.dxf.radius, a0, a1,
                                     raw_layer, raw_layer, handle, block))


def _extent(d: Drawing):
    """Lower-left corner and larger side. Header extents are unreliable, so measure."""
    points = [(s.x1, s.y1) for s in d.segments] + [(s.x2, s.y2) for s in d.segments]
    if not points:
        points = [(a.cx, a.cy) for a in d.arcs] + [(t.x, t.y) for t in d.labels] + \
                 [(b.x, b.y) for b in d.blockrefs]
    if not points:
        return (0.0, 0.0), 0.0
    xy = np.array(points)
    if len(xy) < config.EXTENT_TRIM_MIN_POINTS:
        low, high = xy.min(axis=0), xy.max(axis=0)
    else:  # ignore stray entities far from the plan
        low, high = np.percentile(xy, 1, axis=0), np.percentile(xy, 99, axis=0)
    return (float(low[0]), float(low[1])), float(max(high - low))


def _units(doc, larger_side, warnings):
    code = doc.header.get("$INSUNITS", 0)
    if code in config.INSUNITS:
        return config.INSUNITS[code]
    name, scale = "m", 1.0
    if larger_side > config.GUESS_MM_ABOVE:
        name, scale = "mm", 0.001
    elif larger_side > config.GUESS_CM_ABOVE:
        name, scale = "cm", 0.01
    warnings.append(f"units guessed as {name} from the drawing size (no unit in the file)")
    return name, scale


def group_labels(labels: list[Label]) -> list[Label]:
    """Join words that the exporting program split into separate texts.

    TEXT has no stored width, so the width of a word is estimated from its length.
    # ponytail: joins neighbours on one baseline even if they are separate labels
    # that happen to sit close together. Upgrade: measure with the real font.
    """
    def along_across(label):
        angle = math.radians(label.rotation)
        return (label.x * math.cos(angle) + label.y * math.sin(angle),
                -label.x * math.sin(angle) + label.y * math.cos(angle))

    groups: list[tuple[Label, Label]] = []  # (merged label, its last word)
    for word in sorted(labels, key=lambda label: along_across(label)[0]):
        along, across = along_across(word)
        for i, (merged, last) in enumerate(groups):
            h = last.height or word.height
            if not h:
                continue
            last_along, last_across = along_across(last)
            step = along - last_along
            if (abs(word.rotation - last.rotation) < config.LABEL_ROT_TOL
                    and abs(word.height - last.height) <= config.LABEL_HEIGHT_TOL * h
                    and abs(across - last_across) < config.LABEL_LINE_TOL * h
                    and word.layer == last.layer
                    and 0.5 * len(last.text) * config.CHAR_W_MIN * h <= step
                    <= (len(last.text) * config.CHAR_W_MAX + config.WORD_GAP) * h):
                merged.text += " " + word.text
                merged.handles += word.handles
                groups[i] = (merged, word)
                break
        else:
            groups.append((Label(word.text, word.x, word.y, word.height, word.rotation,
                                 word.layer, list(word.handles)), word))
    return [merged for merged, _ in groups]


def load(path, bbox=None) -> Drawing:
    """bbox = (xmin, ymin, xmax, ymax) in local metres keeps only that part of the sheet."""
    path = Path(path)
    doc = _read(path)
    collector = _Collector(doc)
    for entity in doc.modelspace():
        collector.walk(entity)
    d = collector.drawing
    d.path, d.project = str(path), path.stem
    if collector.capped:
        d.warnings.append(f"stopped after {config.MAX_FLAT_ENTITIES} entities; the drawing is incomplete")

    (ox, oy), larger_side = _extent(d)
    d.units_in, d.scale_to_m = _units(doc, larger_side, d.warnings)
    d.origin_offset = (ox, oy)
    k = d.scale_to_m
    for s in d.segments:
        s.x1, s.y1, s.x2, s.y2 = (s.x1 - ox) * k, (s.y1 - oy) * k, (s.x2 - ox) * k, (s.y2 - oy) * k
    for a in d.arcs:
        a.cx, a.cy, a.r = (a.cx - ox) * k, (a.cy - oy) * k, a.r * k
    for t in d.labels:
        t.x, t.y, t.height = (t.x - ox) * k, (t.y - oy) * k, t.height * k
    for b in d.blockrefs:
        b.x, b.y = (b.x - ox) * k, (b.y - oy) * k
    for f in d.fills:
        f.polygon = [((x - ox) * k, (y - oy) * k) for x, y in f.polygon]

    for item in d.segments + d.arcs + d.labels + d.blockrefs + d.fills:
        item.layer = clean_layer(item.layer)
    d.segments = [s for s in d.segments if s.length >= config.MIN_SEGMENT]

    if bbox:
        xmin, ymin, xmax, ymax = bbox
        inside = lambda x, y: xmin <= x <= xmax and ymin <= y <= ymax
        d.segments = [s for s in d.segments if inside((s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2)]
        d.arcs = [a for a in d.arcs if inside(a.cx, a.cy)]
        d.labels = [t for t in d.labels if inside(t.x, t.y)]
        d.blockrefs = [b for b in d.blockrefs if inside(b.x, b.y)]
        d.fills = [f for f in d.fills if inside(*np.mean(f.polygon, axis=0))]

    d.labels = group_labels(d.labels)

    stats = defaultdict(lambda: {"n": 0, "types": Counter(), "length_m": 0.0,
                                 "lineweights": Counter(), "texts": []})
    for raw_layer, types in collector.types.items():  # counted before any bbox filter
        entry = stats[clean_layer(raw_layer)]
        entry["types"].update(types)
        entry["n"] += sum(types.values())
    for s in d.segments:
        stats[s.layer]["length_m"] += s.length
        stats[s.layer]["lineweights"][s.lineweight] += 1
    for t in d.labels:
        if len(stats[t.layer]["texts"]) < 10:
            stats[t.layer]["texts"].append(t.text)
    d.layer_stats = {name: {**entry, "types": dict(entry["types"]), "lineweights": dict(entry["lineweights"])}
                     for name, entry in stats.items()}
    return d
