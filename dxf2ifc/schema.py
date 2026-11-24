"""The plan.json v1 contract. See planning/sections/section-01-scaffold-contract.md."""

from . import config

NUM = (int, float)
SRC = {"layer": str, "handles": list}

# field -> type (or tuple of types). None in a tuple means null is allowed.
SOURCE = {"file": str, "project": str, "units_in": str, "scale_to_m": NUM,
          "origin_offset": list, "family": str}
STOREY = {"name": str, "elevation_m": NUM, "height_m": NUM,
          "height_source": str, "confidence": NUM}
WALL = {"id": str, "axis": list, "thickness_m": NUM, "is_external": (bool, None),
        "confidence": NUM, "rule": str, "src": dict}
OPENING = {"id": str, "kind": str, "wall_id": str, "center": list, "width_m": NUM,
           "height_m": NUM, "sill_m": NUM, "tag": (str, None),
           "confidence": NUM, "rule": str, "src": dict}
ROOM = {"id": str, "name": str, "polygon": list, "area_m2": NUM,
        "area_printed_m2": (int, float, None), "confidence": NUM, "rule": str, "src": dict}
COLUMN = {"id": str, "round": bool, "polygon": list, "confidence": NUM, "rule": str, "src": dict}
EQUIPMENT = {"id": str, "kind": str, "name": str, "polygon": list, "confidence": NUM, "rule": str, "src": dict}
CURVED_WALL = {"id": str, "center": list, "radius_m": NUM, "thickness_m": NUM, "start_deg": NUM, "end_deg": NUM,
               "confidence": NUM, "rule": str, "src": dict}
BEAM = {"id": str, "axis": list, "width_m": NUM, "confidence": NUM, "rule": str, "src": dict}
FIRE = {"id": str, "kind": str, "position": list, "confidence": NUM, "rule": str, "src": dict}
STAIR = {"id": str, "polygon": list, "confidence": NUM, "rule": str, "src": dict}
SLAB = {"outline": list, "thickness_m": NUM, "confidence": NUM, "rule": str}
FLAG = {"element_id": str, "severity": str, "reason": str}
STATS = {"wall_layer_length_m": NUM, "wall_explained_length_m": NUM,
         "labels_total": int, "labels_unused": int, "runtime_s": NUM, "llm_calls": int}

FAMILIES = {"layered", "colour_only"}
KINDS = {"door", "window", "opening"}
SEVERITIES = {"review", "info"}


def level(confidence: float) -> str:
    if confidence >= config.CONF_HIGH:
        return "high"
    return "medium" if confidence >= config.CONF_MEDIUM else "low"


def _fields(obj, spec, where, errors):
    """Check required keys and types; return True if obj is a usable dict."""
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be an object")
        return False
    for key, types in spec.items():
        types = types if isinstance(types, tuple) else (types,)
        nullable = None in types
        types = tuple(t for t in types if t is not None)
        if key not in obj:
            errors.append(f"{where}.{key}: missing")
        elif obj[key] is None:
            if not nullable:
                errors.append(f"{where}.{key}: must not be null")
        elif not isinstance(obj[key], types) or (isinstance(obj[key], bool) and bool not in types):
            errors.append(f"{where}.{key}: wrong type {type(obj[key]).__name__}")
    conf = obj.get("confidence")
    if isinstance(conf, NUM) and not isinstance(conf, bool) and not 0 <= conf <= 1:
        errors.append(f"{where}.confidence: {conf} is outside 0..1")
    if "src" in spec and isinstance(obj.get("src"), dict):
        _fields(obj["src"], SRC, f"{where}.src", errors)
    return True


def _points(value, minimum, where, errors):
    ok = isinstance(value, list) and len(value) >= minimum and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(c, NUM) for c in p) for p in value)
    if not ok:
        errors.append(f"{where}: needs at least {minimum} [x, y] points")


def validate_plan(plan: dict) -> list[str]:
    """Return a list of problems; an empty list means the plan follows the contract."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan: must be an object"]
    if plan.get("schema_version") != 1:
        errors.append("schema_version: must be 1")

    if _fields(plan.get("source"), SOURCE, "source", errors):
        if plan["source"].get("family") not in FAMILIES:
            errors.append(f"source.family: must be one of {sorted(FAMILIES)}")
    _fields(plan.get("storey"), STOREY, "storey", errors)

    for key in ("walls", "openings", "rooms", "flags"):
        if not isinstance(plan.get(key), list):
            errors.append(f"{key}: must be a list")

    wall_ids = set()
    for i, wall in enumerate(plan.get("walls") or []):
        where = f"walls[{i}]"
        if _fields(wall, WALL, where, errors):
            wall_ids.add(wall.get("id"))
            if "axis" in wall:
                _points(wall["axis"], 2, f"{where}.axis", errors)

    for i, opening in enumerate(plan.get("openings") or []):
        where = f"openings[{i}]"
        if _fields(opening, OPENING, where, errors):
            if opening.get("kind") not in KINDS:
                errors.append(f"{where}.kind: must be one of {sorted(KINDS)}")
            if opening.get("wall_id") not in wall_ids:
                errors.append(f"{where}.wall_id: no wall with id {opening.get('wall_id')!r}")

    for i, room in enumerate(plan.get("rooms") or []):
        where = f"rooms[{i}]"
        if _fields(room, ROOM, where, errors) and "polygon" in room:
            _points(room["polygon"], 3, f"{where}.polygon", errors)

    for i, stair in enumerate(plan.get("stairs") or []):  # optional: plans from before 19 Nov have none
        if _fields(stair, STAIR, f"stairs[{i}]", errors) and "polygon" in stair:
            _points(stair["polygon"], 3, f"stairs[{i}].polygon", errors)

    for i, column in enumerate(plan.get("columns") or []):  # optional, like stairs
        if _fields(column, COLUMN, f"columns[{i}]", errors) and "polygon" in column:
            _points(column["polygon"], 3, f"columns[{i}].polygon", errors)

    for i, item in enumerate(plan.get("equipment") or []):  # optional, like stairs
        if _fields(item, EQUIPMENT, f"equipment[{i}]", errors) and "polygon" in item:
            _points(item["polygon"], 3, f"equipment[{i}].polygon", errors)
            if item.get("kind") not in config.EQUIPMENT:
                errors.append(f"equipment[{i}].kind: unknown {item.get('kind')!r}")

    for i, beam in enumerate(plan.get("beams") or []):  # optional, like stairs
        if _fields(beam, BEAM, f"beams[{i}]", errors) and "axis" in beam:
            _points(beam["axis"], 2, f"beams[{i}].axis", errors)

    for i, wall in enumerate(plan.get("curved_walls") or []):  # optional, like stairs
        if _fields(wall, CURVED_WALL, f"curved_walls[{i}]", errors) and isinstance(wall.get("thickness_m"), NUM) \
                and not 0 < wall["thickness_m"] < wall.get("radius_m", 0) * 2:
            errors.append(f"curved_walls[{i}].thickness_m: must be positive and less than the diameter")

    for i, symbol in enumerate(plan.get("fire") or []):  # optional, like stairs
        _fields(symbol, FIRE, f"fire[{i}]", errors)

    if _fields(plan.get("slab"), SLAB, "slab", errors) and "outline" in plan["slab"]:
        _points(plan["slab"]["outline"], 3, "slab.outline", errors)

    for i, flag in enumerate(plan.get("flags") or []):
        where = f"flags[{i}]"
        if _fields(flag, FLAG, where, errors) and flag.get("severity") not in SEVERITIES:
            errors.append(f"{where}.severity: must be one of {sorted(SEVERITIES)}")

    _fields(plan.get("stats"), STATS, "stats", errors)
    return errors
