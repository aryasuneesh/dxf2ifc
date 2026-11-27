"""A 3D page for the browser: the IFC's geometry coloured by confidence, click an element to see its trust data.

The IFC is turned into triangles here, in Python, so the page needs no IFC parser: one HTML file with
three.js inlined (dxf2ifc/vendor, MIT), so it works offline and from a plain file. Every element keeps its IFC GlobalId, the stable id a portal can attach remarks to.
"""

import base64
import html
import json
import multiprocessing
import re
from functools import cache
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as element
import ifcopenshell.util.placement as placement

from . import config

KINDS = {"IfcWall": "walls", "IfcWallStandardCase": "walls", "IfcDoor": "openings", "IfcWindow": "openings",
         "IfcSpace": "rooms", "IfcSlab": "slabs", "IfcRoof": "slabs", "IfcStair": "stairs", "IfcColumn": "columns", "IfcRailing": "railings", "IfcBeam": "beams", "IfcCovering": "ceilings",
         "IfcFurniture": "furniture", "IfcFurnishingElement": "furniture", "IfcSanitaryTerminal": "fittings",
         "IfcFlowTerminal": "fittings", "IfcElectricAppliance": "fittings", "IfcSpaceHeater": "fittings",
         "IfcTransportElement": "furniture",
         "IfcSensor": "extras", "IfcDistributionControlElement": "extras", "IfcFireSuppressionTerminal": "extras",
         "IfcAlarm": "extras", "IfcBuildingElementProxy": "fire safety",
         "IfcOpeningElement": "gaps"}  # a gap in a wall with no door: drawn as a see-through box


def elements(ifc_path) -> list[dict]:
    """One entry per element with geometry: ids, trust data, vertices (x, y, z rounded to mm) and faces."""
    model = ifcopenshell.open(str(ifc_path))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    iterator = ifcopenshell.geom.iterator(settings, model, multiprocessing.cpu_count())
    result = []
    if not iterator.initialize():
        return result
    while True:
        shape = iterator.get()
        product = model.by_guid(shape.guid)
        if product.is_a("IfcOpeningElement") and product.HasFillings:  # a door or window fills it: show that instead
            if not iterator.next():
                break
            continue
        trust = element.get_pset(product, "SECO_Trust") or {}
        container = element.get_container(product)
        result.append({
            "id": shape.guid, "type": product.is_a(), "kind": KINDS.get(product.is_a(), "other"),
            "name": product.Name or "", "long_name": getattr(product, "LongName", None) or "", "storey": container.Name if container else "",
            "level": str(trust.get("ConfidenceLevel", "")).lower(), "confidence": trust.get("Confidence"),
            "status": trust.get("Status", ""), "rule": trust.get("Rule", ""),
            "source": f"{trust.get('SourceFile', '')} / {trust.get('SourceLayer', '')}".strip(" /"),
            "printed_area": trust.get("PrintedArea"), "material": _material(product), "props": _props(product),
            "v": [round(x, 3) for x in shape.geometry.verts], "f": list(shape.geometry.faces),
            "glass": _glass(shape.geometry) if not product.is_a("IfcSpace") else []})
        if not iterator.next():
            break
    return result


def levels(ifc_path) -> dict:
    """Storeys (name, elevation) and grid axes (tag, two points in model coordinates) for labels in the page."""
    model = ifcopenshell.open(str(ifc_path))
    storeys = [{"name": s.Name or "", "elevation": float(s.Elevation or 0)} for s in model.by_type("IfcBuildingStorey")]
    grid = []
    for g in model.by_type("IfcGrid"):
        matrix = placement.get_local_placement(g.ObjectPlacement)
        for axis in [*(g.UAxes or []), *(g.VAxes or [])]:
            points = [matrix @ [*p.Coordinates[:2], 0, 1] for p in axis.AxisCurve.Points]
            grid.append({"tag": axis.AxisTag or "", "a": [round(float(c), 3) for c in points[0][:3]],
                         "b": [round(float(c), 3) for c in points[-1][:3]]})
    return {"storeys": sorted(storeys, key=lambda s: s["elevation"]), "grid": grid}


UNITS = (("Area", "m²"), ("Volume", "m³"), ("Length", "m"), ("Width", "m"), ("Height", "m"), ("Depth", "m"),
         ("Perimeter", "m"), ("Thickness", "m"))


def _props(product) -> list[list[str]]:
    """[label, value] of the element's type, use and every standard quantity and property, for the details panel."""
    rows = []
    kind = element.get_type(product)
    if kind is not None:
        rows.append(["Element type", kind.Name or kind.is_a()])
    if getattr(product, "ObjectType", None):
        rows.append(["Use" if product.is_a("IfcSpace") else "Kind", product.ObjectType])
    for pset, values in sorted(element.get_psets(product).items()):
        if pset.startswith("SECO_"):
            continue
        for key, value in values.items():
            if key == "id" or value in (None, ""):
                continue
            label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key).capitalize()
            if isinstance(value, bool):
                value = "yes" if value else "no"
            elif isinstance(value, float):
                unit = next((u for word, u in UNITS if word in key), "")
                value = f"{value:.2f} {unit}".strip() if unit or not value.is_integer() else str(int(value))
            rows.append([label, str(value)])
    return rows


def _material(product) -> str:
    material = element.get_material(product, should_inherit=True)
    if material is None:
        return ""
    if material.is_a("IfcMaterialLayerSetUsage"):
        material = material.ForLayerSet
    if material.is_a("IfcMaterialLayerSet"):
        return " + ".join(layer.Material.Name for layer in material.MaterialLayers if layer.Material)
    return getattr(material, "Name", "") or ""


def _glass(geometry) -> list[int]:
    """Triangles with a see-through style (window panes), as indices: the page draws them as glass."""
    clear = {i for i, material in enumerate(geometry.materials) if material.transparency > 0}
    return [t for t, m in enumerate(geometry.material_ids) if m in clear] if clear else []


VENDOR = Path(__file__).parent / "vendor"


@cache
def three_bundle() -> str:
    """three.js and OrbitControls as one inline script: each module wrapped in a function, exports returned.
    ponytail: regex on the two pinned files (three 0.160.0); re-check the asserts when upgrading three."""
    core = (VENDOR / "three.module.min.js").read_text(encoding="utf-8")
    start = core.rindex("export{")
    names = ",".join(f"{b}:{a}" for a, b in re.findall(r"([\w$]+) as ([\w$]+)", core[start:]))
    orbit = (VENDOR / "OrbitControls.js").read_text(encoding="utf-8")
    imports = re.match(r"\s*import\s*\{([^}]*)\}\s*from\s*'three';", orbit)
    assert names and imports and "export { OrbitControls };" in orbit and "</script" not in core + orbit
    orbit = orbit[imports.end():].replace("export { OrbitControls };", "")
    return (f"const THREE=(()=>{{{core[:start]}\nreturn {{{names}}};}})();\n"
            f"const {{OrbitControls}}=(()=>{{const {{{imports.group(1)}}}=THREE;\n{orbit}\nreturn {{OrbitControls}};}})();")


def write_viewer(ifc_path, out_path, title=None, plan_png=None, notes=None, general=None) -> int:
    """Write the page next to the IFC. Returns the number of elements shown.
    plan_png: the whole 2D plan, shown in a corner panel. notes: {GlobalId: what to check}. general: notes on the whole drawing."""
    items = elements(ifc_path)
    title = title or Path(ifc_path).stem
    colours = {name: "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb) for name, rgb in config.CONFIDENCE_COLOURS.items()}
    setup = {"title": title, "colours": colours, "notes": notes or {}, "general": general or [], **levels(ifc_path)}
    plan = (f'<img id="planimg" alt="The 2D plan" src="data:image/png;base64,{base64.b64encode(plan_png).decode("ascii")}">'
            if plan_png else "")
    script_json = lambda value: json.dumps(value, separators=(",", ":")).replace("</", "<\\/")
    page = (PAGE.replace("__TITLE__", html.escape(title))
            .replace("__PLAN__", plan)
            .replace("__SETUP__", script_json(setup))
            .replace("__DATA__", script_json(items))
            .replace("__THREE__", three_bundle()))
    Path(out_path).write_text(page, encoding="utf-8")
    return len(items)


def write_compare(out_path, title) -> None:
    """Review page (the plan) and 3D view side by side. "show in 3D" links in the left pane target the right pane
    by its name; browsers allow that when the pages are served (python -m dxf2ifc serve, or any local server)."""
    Path(out_path).write_text(COMPARE.replace("__TITLE__", html.escape(title)), encoding="utf-8")


COMPARE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>__TITLE__: plan and 3D</title>
<style>html,body{margin:0;height:100%;background:#fff;color:#1c1c1c;font:14px Segoe UI,Arial,sans-serif}
body{display:flex;flex-direction:column} header{padding:6px 12px;border-bottom:1px solid #ddd}
main{flex:1;display:flex;min-height:0} iframe{flex:1;border:0;min-width:0} iframe+iframe{border-left:2px solid #ccc}</style>
</head><body><header><b>__TITLE__</b>: the plan and what to check on the left, the model on the right.
Click "show in 3D" next to an item to see it on the right.</header>
<main><iframe src="review.html" title="Plan and review list"></iframe>
<iframe src="viewer.html" name="viewer" title="3D model"></iframe></main></body></html>"""




PAGE = (Path(__file__).parent / "viewer_page.html").read_text(encoding="utf-8")
