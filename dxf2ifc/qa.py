"""Checks on the result: the minimum requirements (planning/spec-final.md section 5) and accuracy
on synthetic plans where the true answer is known."""

import math
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as element
import ifcopenshell.validate
import ifcopenshell.util.placement
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree
from shapely.ops import unary_union

from . import config


def _samples(axes, step):
    points = []
    for (x1, y1), (x2, y2) in axes:
        n = max(1, round(math.hypot(x2 - x1, y2 - y1) / step))
        points += [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n + 1)]
    return points


def wall_pr(pred_axes, true_axes, tol=config.WALL_MATCH_TOL, step=0.05) -> tuple[float, float]:
    """Length-weighted precision and recall of wall axes: a point counts if the other set is within tol."""
    def share(axes, others):
        if not axes or not others:
            return 0.0
        target = unary_union([LineString(a) for a in others])
        points = _samples(axes, step)
        return sum(target.distance(Point(p)) <= tol + 1e-9 for p in points) / len(points)

    return share(pred_axes, true_axes), share(true_axes, pred_axes)


def profile_ifc(path) -> dict:
    """What an IFC file contains: classes, storeys, property sets per class. Slow on large files."""
    ifc = ifcopenshell.open(str(path))
    products = ifc.by_type("IfcProduct")
    psets = defaultdict(Counter)
    for product in products:
        for name in element.get_psets(product):
            psets[product.is_a()][name] += 1
    doors = ifc.by_type("IfcDoor")
    return {"schema": ifc.schema,
            "counts": dict(Counter(p.is_a() for p in products).most_common()),
            "storeys": [{"name": s.Name, "elevation": round(float(s.Elevation or 0), 3)}
                        for s in sorted(ifc.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0)],
            "psets": {cls: dict(names.most_common()) for cls, names in psets.items()},
            "doors_with_size": sum(bool(d.OverallWidth and d.OverallHeight) for d in doors),
            "doors": len(doors)}


def _solids(product):
    """(footprint, z from, z to) of each extruded solid in a product's body, in storey-free model coordinates."""
    z0 = ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)[2, 3]
    out = []
    for representation in (product.Representation.Representations if product.Representation else []):
        for item in representation.Items:
            if not item.is_a("IfcExtrudedAreaSolid") or not item.SweptArea.is_a("IfcArbitraryClosedProfileDef"):
                continue
            curve = item.SweptArea.OuterCurve
            points = ([p.Coordinates for p in curve.Points] if curve.is_a("IfcPolyline") else curve.Points.CoordList)
            bottom = z0 + (item.Position.Location.Coordinates[2] if item.Position else 0.0)
            out.append((Polygon([tuple(p[:2]) for p in points]).buffer(0), bottom, bottom + item.Depth))
    return out


def overlaps(ifc, tol=1e-4) -> list[tuple[str, str]]:
    """Pairs of physical elements that share space (more than tol m2 in plan and 1 mm in height).
    A door or window inside the opening of its own wall does not count: the opening is cut from the wall."""
    skip = ("IfcSpace", "IfcOpeningElement", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcAnnotation")
    host = {}
    for rel in ifc.by_type("IfcRelFillsElement"):
        for walls in rel.RelatingOpeningElement.VoidsElements:
            host[rel.RelatedBuildingElement.id()] = walls.RelatingBuildingElement.id()
    solids = [(p, *s) for p in ifc.by_type("IfcProduct") if not p.is_a() in skip for s in _solids(p)]
    tree = STRtree([s[1] for s in solids])
    pairs = set()
    for i, (a, shape, z0, z1) in enumerate(solids):
        for j in tree.query(shape):
            b, other, w0, w1 = solids[j]
            if j <= i or a.id() == b.id() or host.get(a.id()) == b.id() or host.get(b.id()) == a.id():
                continue
            if min(z1, w1) - max(z0, w0) > 1e-3 and shape.intersection(other).area > tol:
                pairs.add(tuple(sorted((a.GlobalId, b.GlobalId))))
    return sorted(pairs)


def _share(items, ok):
    """(text, passed) for 'every item must satisfy ok'. No items: not applicable."""
    items = list(items)
    if not items:
        return "none in the model", None
    good = sum(bool(ok(item)) for item in items)
    return f"{good} of {len(items)}", good == len(items)


def check(plans, ifc_path, runtime_s=None, errors=None) -> list[dict]:
    """The minimum-requirements table. passed is True, False, or None when a rule does not apply."""
    from .to_ifc import write_ifc

    plans = [plans] if isinstance(plans, dict) else list(plans)
    ifc = ifcopenshell.open(str(ifc_path))
    trust = lambda product: (element.get_pset(product, "SECO_Trust") or {}).get("Confidence") is not None
    rows = []

    def add(rid, name, value, threshold, passed, note=""):
        rows.append({"id": rid, "name": name, "value": value, "threshold": threshold, "passed": passed, "note": note})

    if errors is None:
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(ifc, logger)
        errors = [statement["message"] for statement in logger.statements]
    add("R1", "The IFC file is valid", f"{len(errors)} errors", "0 errors", not errors,
        "; ".join(sorted(set(errors))[:3]))

    storeys = ifc.by_type("IfcBuildingStorey")
    tree = all(ifc.by_type(c) for c in ("IfcProject", "IfcSite", "IfcBuilding")) and bool(storeys)
    add("R2", "Project, site, building and storeys with name and elevation",
        f"{len(storeys)} storeys", "all named, with an elevation",
        tree and all(s.Name and s.Elevation is not None for s in storeys))

    value, passed = _share(ifc.by_type("IfcWall"),
                           lambda w: w.Representation and element.get_pset(w, "Pset_WallCommon") is not None)
    add("R3", "Walls have a body, a thickness and Pset_WallCommon", value, "all", passed)

    value, passed = _share(ifc.by_type("IfcDoor"),
                           lambda d: d.OverallWidth and d.OverallHeight and d.FillsVoids
                           and d.FillsVoids[0].RelatingOpeningElement.VoidsElements)
    add("R4", "Doors have a width and a height and sit in an opening in a wall", value, "all", passed)

    slabs = len(ifc.by_type("IfcSlab"))
    add("R5", "One floor slab per storey", f"{slabs} slabs", f"{len(storeys)}", slabs >= len(storeys) > 0)

    value, passed = _share(ifc.by_type("IfcSpace"),
                           lambda s: s.LongName and (element.get_pset(s, "Qto_SpaceBaseQuantities") or {}).get("NetFloorArea"))
    add("R6", "Rooms have a name and a floor area", value, "all", passed)

    elements = [p for cls in ("IfcWall", "IfcDoor", "IfcWindow", "IfcSpace", "IfcSlab") for p in ifc.by_type(cls)]
    value, passed = _share(elements, trust)
    add("R7", "Elements carry a confidence and their source", value, "all", passed)

    with tempfile.TemporaryDirectory() as folder:
        write_ifc(plans, Path(folder) / "again.ifc")
        again = ifcopenshell.open(str(Path(folder) / "again.ifc"))
        same = sorted(r.GlobalId for r in ifc.by_type("IfcRoot")) == sorted(r.GlobalId for r in again.by_type("IfcRoot"))
    add("R8", "A second run gives the same element ids", "identical" if same else "different", "identical", same)

    printed = [r for plan in plans for r in plan["rooms"] if r["area_printed_m2"]]
    close = sum(abs(r["area_m2"] - r["area_printed_m2"]) / r["area_printed_m2"] <= config.R9_AREA_TOL for r in printed)
    add("R9", f"Room area within {config.R9_AREA_TOL:.0%} of the area printed on the plan",
        f"{close} of {len(printed)} rooms" if printed else "no printed areas",
        f"{config.R9_MIN_SHARE:.0%} of rooms", close / len(printed) >= config.R9_MIN_SHARE if printed else None)

    drawn = sum(plan["stats"]["wall_layer_length_m"] for plan in plans)
    used = sum(plan["stats"]["wall_explained_length_m"] for plan in plans)
    add("R10", "Wall lines turned into walls", f"{min(used / drawn, 1):.0%}".replace("%", " %") if drawn else "no wall lines",
        f"{config.R10_MIN_COVERAGE:.0%}".replace("%", " %"), used / drawn >= config.R10_MIN_COVERAGE if drawn else None)

    clash = overlaps(ifc)
    add("R13", "No two elements share space", f"{len(clash)} overlapping pairs", "0", not clash)

    seconds = runtime_s if runtime_s is not None else sum(plan["stats"]["runtime_s"] for plan in plans)
    calls = max(plan["stats"]["llm_calls"] for plan in plans)
    add("R12", "Fast and free to run", f"{seconds:.1f} s, {calls} LLM calls",
        f"under {config.R12_MAX_SECONDS} s per run, at most 1 LLM call per plan",
        seconds < config.R12_MAX_SECONDS and calls <= 1)
    return rows


BENCH_VARIANTS = [
    ("metres, named layers", {}),
    ("millimetres", {"units": "mm"}),
    ("colour-only layers, split words", {"colour_only": True, "units": "mm"}),
    ("national-grid coordinates", {"offset": (79000, 104000)}),
    ("xref layer prefix", {"xref_prefix": "BAT 4$0$"}),
    ("plan inside a block", {"in_block": True}),
    ("with furniture noise", {"noise": True}),
]


def bench() -> list[dict]:
    """Accuracy on the synthetic house in every form real files take (requirement R11)."""
    from tests.fixtures import make_house  # the house lives with the tests; run from the repo root

    from .extract import extract
    from .normalize import load
    from .roles import assign_roles

    rows = []
    with tempfile.TemporaryDirectory() as folder:
        for name, variant in BENCH_VARIANTS:
            path = Path(folder) / "house.dxf"
            truth = make_house(path, **variant)
            drawing = load(path)
            plan = extract(drawing, assign_roles(drawing, mappings_dir=Path(folder) / "none"))
            precision, recall = wall_pr([w["axis"] for w in plan["walls"]], [(a, b) for a, b, _ in truth["wall_axes"]])
            rooms = {r["name"]: r["area_m2"] for r in plan["rooms"]}
            found = [t for t in truth["rooms"] if t["name"] in rooms]
            doors = sum(any(math.dist(o["center"], t["center"]) < 0.2 and abs(o["width_m"] - t["width"]) < 0.05
                            for o in plan["openings"] if o["kind"] == "door") for t in truth["doors"])
            rows.append({"variant": name, "wall_precision": round(precision, 3), "wall_recall": round(recall, 3),
                         "rooms": f"{len(found)} of {len(truth['rooms'])}", "doors": f"{doors} of {len(truth['doors'])}",
                         "max_area_error_pct": round(max((abs(rooms[t["name"]] - t["area"]) / t["area"] * 100
                                                          for t in found), default=100.0), 2)})
    return rows
