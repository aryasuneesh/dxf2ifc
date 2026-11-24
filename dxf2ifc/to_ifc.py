"""plan.json -> IFC. No extraction logic lives here: a value missing from the plan is an error.

Every body is an extruded outline in plan coordinates, lifted to the storey elevation.
# ponytail: walls are plain extrusions, not axis-based "standard case" walls with types and
# material layers. Viewers and checks do not care; add IfcWallType per thickness if SECO asks.
"""

import math
import re
import uuid
from pathlib import Path

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.type
import ifcopenshell.api.owner
import ifcopenshell.api.owner.settings
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.style
import ifcopenshell.api.unit
import ifcopenshell.guid
import ifcopenshell.validate
import numpy as np
from ifcopenshell.util.shape_builder import ShapeBuilder

from . import config
from .schema import level

api = ifcopenshell.api


class _Ids:
    """GlobalIds that depend only on the input, so a rerun gives the same model."""

    def __init__(self):
        self.seen = set()

    def make(self, *parts) -> str:
        key, n = ":".join(str(p) for p in parts), 1
        guid = ifcopenshell.guid.compress(uuid.uuid5(config.GUID_NAMESPACE, key).hex)
        while guid in self.seen:  # two elements built from the same source lines
            n += 1
            guid = ifcopenshell.guid.compress(uuid.uuid5(config.GUID_NAMESPACE, f"{key}#{n}").hex)
        self.seen.add(guid)
        return guid


class _Writer:
    def __init__(self, plans, schema):
        self.plans = sorted(plans, key=lambda plan: plan["storey"]["elevation_m"])
        self.name = self.plans[0]["source"]["project"]
        self.f = api.project.create_file(version=schema)
        self.old = schema == "IFC2X3"
        self.ids = _Ids()
        self.storeys = []  # in elevation order, as written
        self.builder = ShapeBuilder(self.f)
        f = self.f
        if self.old:  # IFC2X3 wants an owner history on every entity
            user = api.owner.add_person_and_organisation(
                f, person=api.owner.add_person(f), organisation=api.owner.add_organisation(f))
            application = api.owner.add_application(f)
            api.owner.settings.get_user = lambda ifc: user
            api.owner.settings.get_application = lambda ifc: application
        else:  # a previous IFC2X3 run in this process must not leak its owner into this file
            api.owner.settings.get_user = lambda ifc: None
            api.owner.settings.get_application = lambda ifc: None

        self.project = self.root("IfcProject", self.name, "project")
        api.unit.assign_unit(f, units=[api.unit.add_si_unit(f, unit_type=kind)
                                       for kind in ("LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT")])
        model = api.context.add_context(f, context_type="Model")
        self.body = api.context.add_context(f, context_type="Model", context_identifier="Body",
                                            target_view="MODEL_VIEW", parent=model)
        self.styles, self.wall_types = {}, {}
        for name, (red, green, blue) in config.CONFIDENCE_COLOURS.items():
            for kind, transparency in (("solid", 0.0), ("space", config.SPACE_TRANSPARENCY)):
                style = api.style.add_style(f, name=f"confidence {name} ({kind})")
                attributes = {"SurfaceColour": {"Name": None, "Red": red, "Green": green, "Blue": blue}}
                if not self.old:
                    attributes["Transparency"] = transparency
                api.style.add_surface_style(f, style=style, ifc_class="IfcSurfaceStyleShading", attributes=attributes)
                self.styles[name, kind] = style

        self.glass = api.style.add_style(f, name="glass")
        attributes = {"SurfaceColour": {"Name": None, "Red": 0.62, "Green": 0.8, "Blue": 0.92}}
        if not self.old:
            attributes["Transparency"] = config.GLASS_TRANSPARENCY
        api.style.add_surface_style(f, style=self.glass, ifc_class="IfcSurfaceStyleShading", attributes=attributes)

        self.site = self.root("IfcSite", "Site", "site")
        self.building = self.root("IfcBuilding", self.name, "building")
        api.aggregate.assign_object(f, products=[self.site], relating_object=self.project)
        api.aggregate.assign_object(f, products=[self.building], relating_object=self.site)
        source = self.plans[0]["source"]
        self.pset(self.site, "SECO_Georef", {"OffsetX": float(source["origin_offset"][0]),
                                             "OffsetY": float(source["origin_offset"][1]),
                                             "SourceUnits": source["units_in"]})

    def root(self, ifc_class, name, *key, predefined_type=None):
        entity = api.root.create_entity(self.f, ifc_class=ifc_class, name=name, predefined_type=predefined_type)
        entity.GlobalId = self.ids.make(self.name, ifc_class, *key)
        return entity

    def pset(self, product, name, properties):
        properties = {key: value for key, value in properties.items() if value is not None}
        api.pset.edit_pset(self.f, pset=api.pset.add_pset(self.f, product=product, name=name), properties=properties)

    def trust(self, product, plan, element_id, confidence, rule, src=None, printed_area=None):
        src = src or {}
        self.pset(product, "SECO_Trust", {
            "ElementId": element_id,
            "Confidence": float(confidence),
            "ConfidenceLevel": level(confidence).capitalize(),
            "Status": "To be verified",
            "Rule": rule,
            "SourceFile": plan["source"]["file"],
            "SourceLayer": src.get("layer") or None,
            "SourceHandles": ",".join(src.get("handles", [])[:config.MAX_SOURCE_HANDLES]) or None,
            "PrintedArea": None if printed_area is None else float(printed_area)})

    def place(self, product, elevation):
        matrix = np.eye(4)
        matrix[2, 3] = elevation
        api.geometry.edit_object_placement(self.f, product=product, matrix=matrix)

    def solid(self, product, outline, bottom, depth, confidence=None, kind="solid"):
        """Give the product a body: the outline pushed up by depth, starting at bottom (above the storey)."""
        self.body_of(product, [(outline, bottom, depth)], confidence, kind)

    def body_of(self, product, parts, confidence=None, kind="solid"):
        """A body of several extruded outlines, (outline, bottom, depth) or (outline, bottom, depth, "glass"):
        the steps of a stair, the frame and leaf of a door. Glass parts get the glass style, the rest the confidence colour."""
        items = [(self.builder.extrude(self.builder.polyline([(float(x), float(y)) for x, y in part[0]], closed=True),
                                       magnitude=float(part[2]), position=(0.0, 0.0, float(part[1]))), part[3:] == ("glass",))
                 for part in parts]
        shape = self.builder.get_representation(self.body, [item for item, _ in items])
        api.geometry.assign_representation(self.f, product=product, representation=shape)
        if confidence is not None:
            for item, glass in items:
                api.style.assign_item_style(self.f, item=item, style=self.glass if glass else self.styles[level(confidence), kind])

    def wall_type(self, thickness, layer):
        """One IfcWallType per thickness and material, with a single material layer of that thickness."""
        name = next((m for m, pattern in config.WALL_MATERIAL_WORDS if re.search(pattern, layer or "", re.IGNORECASE)), None)
        name = name or next(m for limit, m in config.WALL_MATERIAL_BY_THICKNESS if thickness <= limit)
        key = (round(thickness, 3), name)
        if key not in self.wall_types:
            label = f"{round(thickness * 1000)} mm {name}"
            wall_type = self.root("IfcWallType", f"Wall {label}", "walltype", *key, predefined_type="STANDARD")
            material = api.material.add_material(self.f, name=name, category=None if self.old else name.split(" (")[0].lower())
            layers = api.material.add_material_set(self.f, name=label, set_type="IfcMaterialLayerSet")
            layer_item = api.material.add_layer(self.f, layer_set=layers, material=material)
            api.material.edit_layer(self.f, layer=layer_item, attributes={"LayerThickness": float(thickness)})
            api.material.assign_material(self.f, products=[wall_type], material=layers)
            self.wall_types[key] = wall_type
        return self.wall_types[key]

    def storey(self, plan):
        f, info = self.f, plan["storey"]
        tag, elevation, height = plan["source"]["project"], info["elevation_m"], info["height_m"]
        key = lambda element: (tag, ",".join(sorted(element["src"]["handles"])) or element["id"])

        storey = self.root("IfcBuildingStorey", info["name"], "storey", tag)
        storey.Elevation = float(elevation)
        self.storeys.append(storey)
        api.aggregate.assign_object(f, products=[storey], relating_object=self.building)
        self.place(storey, elevation)
        self.trust(storey, plan, "storey", info["confidence"], f"height:{info['height_source']}")

        walls = {}
        for wall in plan["walls"]:
            thickness = wall["thickness_m"]
            product = self.root("IfcWall", f"Wall {round(thickness * 1000)} mm", *key(wall))
            self.place(product, elevation)
            self.solid(product, _strip(wall["axis"], thickness), 0.0, height, wall["confidence"])
            self.pset(product, "Pset_WallCommon", {"Reference": f"{round(thickness * 1000)} mm",
                                                   "IsExternal": wall["is_external"]})
            self.trust(product, plan, wall["id"], wall["confidence"], wall["rule"], wall["src"])
            api.type.assign_type(f, related_objects=[product], relating_type=self.wall_type(thickness, wall["src"].get("layer")))
            walls[wall["id"]] = (product, wall)

        for wall in plan.get("curved_walls", []):  # the ring between the two arcs, full storey height
            product = self.root("IfcWall", f"Curved wall {round(wall['thickness_m'] * 1000)} mm", *key(wall))
            self.place(product, elevation)
            self.solid(product, _ring(wall), 0.0, height, wall["confidence"])
            self.pset(product, "Pset_WallCommon", {"Reference": f"{round(wall['thickness_m'] * 1000)} mm curved"})
            self.trust(product, plan, wall["id"], wall["confidence"], wall["rule"], wall["src"])
            walls[wall["id"]] = (product, None)

        fillings = []
        for opening in plan["openings"]:
            host, wall = walls[opening["wall_id"]]
            (x1, y1), (x2, y2) = wall["axis"]
            length = math.hypot(x2 - x1, y2 - y1)
            ux, uy = (x2 - x1) / length, (y2 - y1) / length
            half = opening["width_m"] / 2
            cx, cy = opening["center"]
            span = [[cx - ux * half, cy - uy * half], [cx + ux * half, cy + uy * half]]
            ident = (tag, opening["id"], ",".join(sorted(opening["src"]["handles"])))

            void = self.root("IfcOpeningElement", f"Opening {opening['id']}", "void", *ident)
            self.place(void, elevation)
            self.solid(void, _strip(span, wall["thickness_m"] + config.OPENING_OVERCUT), opening["sill_m"], opening["height_m"])
            api.feature.add_feature(f, feature=void, element=host)
            self.trust(void, plan, opening["id"], opening["confidence"], opening["rule"], opening["src"])

            if opening["kind"] in ("door", "window"):
                ifc_class = "IfcDoor" if opening["kind"] == "door" else "IfcWindow"
                product = self.root(ifc_class, opening["tag"] or f"{opening['kind'].capitalize()} {opening['id']}", *ident)
                product.OverallHeight, product.OverallWidth = float(opening["height_m"]), float(opening["width_m"])
                self.place(product, elevation)
                parts = _door(span, wall, opening) if opening["kind"] == "door" else _window(span, wall, opening)
                self.body_of(product, parts, opening["confidence"])
                if opening["kind"] == "door" and opening.get("swing") and not self.old:
                    product.OperationType = _door_operation(span, opening["swing"])
                api.feature.add_filling(f, opening=void, element=product)
                self.pset(product, f"Pset_{ifc_class[3:]}Common", {"Reference": opening["tag"], "IsExternal": wall["is_external"]})
                self.trust(product, plan, opening["id"], opening["confidence"], opening["rule"], opening["src"])
                fillings.append(product)

        spaces = []
        for room in plan["rooms"]:
            space = self.root("IfcSpace", room["id"].upper(), *key(room))
            space.LongName = room["name"]
            if self.old:
                space.InteriorOrExteriorSpace = "INTERNAL"  # mandatory in IFC2X3
            self.place(space, elevation)
            self.solid(space, room["polygon"], 0.0, height, room["confidence"], kind="space")
            quantities = api.pset.add_qto(f, product=space, name="Qto_SpaceBaseQuantities")
            api.pset.edit_qto(f, qto=quantities, properties={"NetFloorArea": float(room["area_m2"])})
            self.pset(space, "Pset_SpaceCommon", {"Reference": room["name"]})
            self.trust(space, plan, room["id"], room["confidence"], room["rule"], room["src"], room["area_printed_m2"])
            spaces.append(space)

        slab_info = plan["slab"]
        slab = self.root("IfcSlab", "Floor slab", "slab", tag, predefined_type="FLOOR")
        self.place(slab, elevation)
        self.solid(slab, slab_info["outline"], -slab_info["thickness_m"], slab_info["thickness_m"], slab_info["confidence"])
        self.pset(slab, "Pset_SlabCommon", {"IsExternal": False})
        self.trust(slab, plan, "slab", slab_info["confidence"], slab_info["rule"])

        stairs = []
        for stair in plan.get("stairs", []):  # steps laid out in the outline; the direction up is a guess
            product = self.root("IfcStair", f"Stair {stair['id']}", *key(stair))
            if self.old:
                product.ShapeType = "NOTDEFINED"  # mandatory in IFC2X3
            self.place(product, elevation)
            steps = (_numbered_steps(stair["polygon"], stair["path"], height, stair.get("risers")) if stair.get("path")
                     else _steps(stair["polygon"], height))
            self.body_of(product, steps, stair["confidence"])
            self.trust(product, plan, stair["id"], stair["confidence"], stair["rule"] + ("+numbers" if stair.get("path") else ""), stair["src"])
            stairs.append(product)

        columns = []
        for column in plan.get("columns", []):  # floor to ceiling: plans give no column heights
            product = self.root("IfcColumn", f"Column {column['id']}" + (" (round)" if column["round"] else ""), *key(column))
            self.place(product, elevation)
            self.solid(product, column["polygon"], 0.0, height, column["confidence"])
            self.trust(product, plan, column["id"], column["confidence"], column["rule"], column["src"])
            columns.append(product)

        for item in plan.get("equipment", []):  # a box of the block's footprint, typical height
            ifc_class, predefined = _EQUIPMENT_CLASS[item["kind"]][self.old]
            product = self.root(ifc_class, item["name"], *key(item), predefined_type=predefined)
            if ifc_class in ("IfcBuildingElementProxy", "IfcFlowTerminal", "IfcFurnishingElement"):
                product.ObjectType = item["kind"]
            _, bottom, top = config.EQUIPMENT[item["kind"]]
            self.place(product, elevation)
            self.solid(product, item["polygon"], bottom, top - bottom, item["confidence"])
            self.trust(product, plan, item["id"], item["confidence"], item["rule"], item["src"])
            columns.append(product)

        fire = []
        for symbol in plan.get("fire", []):  # a 30 cm box where the symbol is; the type is in ObjectType
            names = {"extinguisher": "Fire extinguisher", "detector": "Fire detector",
                     "exit_sign": "Emergency exit sign", "fire_equipment": "Fire-safety equipment"}
            product = self.root("IfcBuildingElementProxy", names[symbol["kind"]], *key(symbol))
            product.ObjectType = names[symbol["kind"]]
            mount = config.FIRE_MOUNT_HEIGHT[symbol["kind"]]
            bottom = height - 0.4 if mount is None else mount
            x, y = symbol["position"]
            self.place(product, elevation)
            self.solid(product, [(x - 0.15, y - 0.15), (x + 0.15, y - 0.15), (x + 0.15, y + 0.15), (x - 0.15, y + 0.15)],
                       bottom, 0.3, symbol["confidence"])
            self.trust(product, plan, symbol["id"], symbol["confidence"], symbol["rule"], symbol["src"])
            fire.append(product)

        contained = [product for product, _ in walls.values()] + fillings + [slab] + stairs + columns + fire
        api.spatial.assign_container(f, products=contained, relating_structure=storey)
        if spaces:
            api.aggregate.assign_object(f, products=spaces, relating_object=storey)

    def roof(self, plan):
        """A flat roof over the top storey. No plan shows a roof, so it is an assumption, flagged as such."""
        info, tag = plan["storey"], plan["source"]["project"]
        roof = self.root("IfcRoof", "Roof (assumed flat)", "roof", tag)
        if self.old:
            roof.ShapeType = "FLAT_ROOF"
        else:
            roof.PredefinedType = "FLAT_ROOF"
        self.place(roof, info["elevation_m"] + info["height_m"])
        self.solid(roof, plan["slab"]["outline"], 0.0, config.DEFAULT_SLAB_THICKNESS, config.CONF_ROOF)
        self.trust(roof, plan, "roof", config.CONF_ROOF, "assumed_flat_roof")
        api.spatial.assign_container(self.f, products=[roof], relating_structure=self.storeys[-1])

    def finish(self, out_path):
        # relations and property sets get their ids from their order, which the input fixes
        for index, entity in enumerate(self.f.by_type("IfcRoot")):
            if entity.GlobalId not in self.ids.seen:
                entity.GlobalId = self.ids.make(self.name, "auto", entity.is_a(), index)
        if self.old:  # the library leaves the owner history off some relations
            history = self.f.by_type("IfcOwnerHistory")[0]
            for entity in self.f.by_type("IfcRoot"):
                if entity.OwnerHistory is None:
                    entity.OwnerHistory = history
        logger = ifcopenshell.validate.json_logger()
        ifcopenshell.validate.validate(self.f, logger)
        self.f.write(str(out_path))  # written even with errors, so it can be inspected
        return [statement["message"] for statement in logger.statements]


def _strip(axis, thickness):
    """Rectangle of the given thickness centred on a two-point axis."""
    (x1, y1), (x2, y2) = axis
    length = math.hypot(x2 - x1, y2 - y1)
    nx, ny = -(y2 - y1) / length * thickness / 2, (x2 - x1) / length * thickness / 2
    return [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]


_SANITARY = lambda kind: (("IfcSanitaryTerminal", kind), ("IfcFlowTerminal", None))
_APPLIANCE = lambda kind: (("IfcElectricAppliance", kind), ("IfcFlowTerminal", None))
_FURNITURE = lambda kind: (("IfcFurniture", kind), ("IfcFurnishingElement", None))
# kind -> ((IFC4 class, predefined type), (IFC2X3 class, None)): IFC2X3 has neither the classes nor the types
_EQUIPMENT_CLASS = {
    "toilet": _SANITARY("TOILETPAN"), "bath": _SANITARY("BATH"), "shower": _SANITARY("SHOWER"),
    "basin": _SANITARY("WASHHANDBASIN"), "washer": _APPLIANCE("WASHINGMACHINE"), "dryer": _APPLIANCE("TUMBLEDRYER"),
    "fridge": _APPLIANCE("FRIDGE_FREEZER"), "cooker": _APPLIANCE("ELECTRICCOOKER"),
    "heater": (("IfcSpaceHeater", "RADIATOR"), ("IfcFlowTerminal", None)),
    "wardrobe": _FURNITURE("SHELF"), "bed": _FURNITURE("BED"), "sofa": _FURNITURE("SOFA"), "table": _FURNITURE("TABLE"),
    "chair": _FURNITURE("CHAIR"), "kitchen": _FURNITURE("NOTDEFINED"),
    "lift": (("IfcTransportElement", "ELEVATOR"), ("IfcTransportElement", None)),
}


def _ring(wall):
    """Outline of a curved wall: the outer arc, then the inner arc back, about every 5 degrees."""
    (cx, cy), r, half = wall["center"], wall["radius_m"], wall["thickness_m"] / 2
    count = max(2, math.ceil((wall["end_deg"] - wall["start_deg"]) / 5))
    angles = np.radians(np.linspace(wall["start_deg"], wall["end_deg"], count + 1))
    outer = [(cx + (r + half) * math.cos(a), cy + (r + half) * math.sin(a)) for a in angles]
    inner = [(cx + (r - half) * math.cos(a), cy + (r - half) * math.sin(a)) for a in angles[::-1]]
    return outer + inner


def _frame(span):
    """Local frame of an opening: origin at the start of the span on the wall axis, u along it, n across."""
    (x1, y1), (x2, y2) = span
    width = math.hypot(x2 - x1, y2 - y1)
    u = np.array([(x2 - x1) / width, (y2 - y1) / width])
    return np.array([x1, y1], float), u, np.array([-u[1], u[0]]), width


def _box(o, u, n, s0, s1, t0, t1, z0, z1, glass=False):
    """A box in an opening's frame: s along the wall, t across it, z up."""
    outline = [tuple(map(float, o + u * s + n * t)) for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1))]
    return (outline, z0, z1 - z0, "glass") if glass else (outline, z0, z1 - z0)


def _swing_side(span, swing):
    """(hinge at the start of the span?, side it opens to: +1 along n, -1 against); no swing: (True, 1)."""
    if not swing:
        return True, 1.0
    o, u, n, w = _frame(span)
    hinge, into = np.array(swing["hinge"], float) - o, np.array(swing["into"], float) - o
    return bool(hinge @ u < w / 2), (1.0 if into @ n >= 0 else -1.0)


def _door(span, wall, opening):
    """Frame (two jambs and a head), leaf, handle and, when the drawing shows it, the swing as a thin arc on the floor."""
    o, u, n, w = _frame(span)
    h, f, d = opening["height_m"], config.FRAME_WIDTH, min(wall["thickness_m"], config.FRAME_DEPTH)
    parts = [_box(o, u, n, 0, f, -d / 2, d / 2, 0, h), _box(o, u, n, w - f, w, -d / 2, d / 2, 0, h),
             _box(o, u, n, 0, w, -d / 2, d / 2, h - f, h)]
    hinge_at_start, side = _swing_side(span, opening.get("swing"))
    t0, t1 = sorted((side * d / 2, side * (d / 2 - config.LEAF_THICKNESS)))
    parts.append(_box(o, u, n, f, w - f, t0, t1, 0.01, h - f))
    s_handle = w - f - 0.12 if hinge_at_start else f + 0.04
    parts.append(_box(o, u, n, s_handle, s_handle + 0.08, -d / 2 - 0.05, d / 2 + 0.05, 1.0, 1.03))
    if opening.get("swing"):  # quarter circle from the closed leaf to the open position, on the side it opens to
        r, sign = w - 2 * f, (1.0 if hinge_at_start else -1.0)
        centre = o + u * (f if hinge_at_start else w - f) + n * side * d / 2
        angles = np.linspace(0, math.pi / 2, 13)
        ring = [centre + u * sign * r * math.cos(a) + n * side * r * math.sin(a) for a in angles]
        ring += [centre + u * sign * (r - 0.03) * math.cos(a) + n * side * (r - 0.03) * math.sin(a) for a in angles[::-1]]
        parts.append(([tuple(map(float, p)) for p in ring], 0.0, 0.005))
    return parts


def _window(span, wall, opening):
    """Frame all round, a glass pane, and a middle bar when the window is wide."""
    o, u, n, w = _frame(span)
    b, h, f = opening["sill_m"], opening["sill_m"] + opening["height_m"], config.FRAME_WIDTH
    d = min(wall["thickness_m"], config.FRAME_DEPTH)
    parts = [_box(o, u, n, 0, f, -d / 2, d / 2, b, h), _box(o, u, n, w - f, w, -d / 2, d / 2, b, h),
             _box(o, u, n, 0, w, -d / 2, d / 2, b, b + f), _box(o, u, n, 0, w, -d / 2, d / 2, h - f, h),
             _box(o, u, n, f, w - f, -0.01, 0.01, b + f, h - f, glass=True)]
    if w > config.WINDOW_SPLIT:
        parts.append(_box(o, u, n, w / 2 - f / 2, w / 2 + f / 2, -d / 2, d / 2, b + f, h - f))
    return parts


def _door_operation(span, swing):
    """IFC door operation: hinge on the left or right, seen from the side the door opens to."""
    at_start, side = _swing_side(span, swing)
    return "SINGLE_SWING_LEFT" if at_start == (side > 0) else "SINGLE_SWING_RIGHT"


def _numbered_steps(outline, path, height, risers=None):
    """Steps along the tread numbers of the drawing. Each tread is centred on its number, as deep as the distance
    to the next number, as wide as the flight: up to the outline, and not past half way to the other flight of a
    U stair. Landings fill the long gaps between numbers.
    ponytail: no balustrade; a sloped handrail needs a swept solid, add it if engineers ask."""
    from shapely.geometry import LineString, Polygon

    pts = np.array(path, float)
    shape, n = Polygon(outline), len(pts)
    rise = height / max(risers or 0, n + 1)  # the last number is the last tread; one more riser reaches the floor
    steps = []
    for i, p in enumerate(pts):
        d = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        going = np.linalg.norm(pts[min(i + 1, n - 1)] - p) if i < n - 1 else np.linalg.norm(p - pts[i - 1])
        if going > 3 * np.median(np.linalg.norm(np.diff(pts, axis=0), axis=1)):  # a landing comes next: keep the tread
            going = np.linalg.norm(p - pts[i - 1]) if i else 0.3
        u = d / (np.linalg.norm(d) or 1)
        v = np.array([-u[1], u[0]])
        half = []
        for side in (1, -1):
            ray = LineString([p, p + side * v * 10])
            hit = ray.intersection(shape.exterior)
            reach = min((np.linalg.norm(np.array(q.coords[0]) - p) for q in getattr(hit, "geoms", [hit]) if not q.is_empty),
                        default=0.6)
            others = [np.dot(q - p, side * v) for j, q in enumerate(pts) if abs(j - i) > 3 and abs(np.dot(q - p, u)) < going]
            others = [o for o in others if o > 0]
            if others:
                reach = min(reach, min(others) / 2)
            half.append(min(reach, 1.5))
        top = (i + 1) * rise
        corners = [p + u * a * going / 2 + v * b for a, b in ((-1, -half[1]), (1, -half[1]), (1, half[0]), (-1, half[0]))]
        bottom = max(0.0, top - rise - config.STAIR_WAIST)
        steps.append(([tuple(map(float, c)) for c in corners], bottom, top - bottom))
    landing = [j for j in range(n - 1) if np.linalg.norm(pts[j + 1] - pts[j]) > 3 * np.median(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    for j in landing:  # the platform between two flights, at the height of the tread before it
        both = Polygon(steps[j][0]).union(Polygon(steps[j + 1][0])).minimum_rotated_rectangle
        steps.append(([tuple(c) for c in list(both.exterior.coords)[:-1]], max(0.0, (j + 1) * rise - config.STAIR_WAIST), min((j + 1) * rise, config.STAIR_WAIST)))
    return steps


def _steps(outline, height):
    """The steps of a stair that fills a rectangular outline, as [(footprint, bottom, depth)].
    Narrow outline: one straight flight along its length. Wide outline: a U stair, two flights side by side
    with a half landing at the far end. Risers of about config.STAIR_RISE up to the next floor.
    ponytail: the direction up and the U/straight choice come from the outline alone; read the tread lines and
    the arrow on the drawing if engineers say the direction matters. Not a rectangle: one block, as before."""
    if len(outline) != 4:
        return [(outline, 0.0, height)]
    p0, p1, p3 = (np.array(outline[i], float) for i in (0, 1, 3))
    a, b = p1 - p0, p3 - p0
    u, v = (a, b) if np.linalg.norm(a) >= np.linalg.norm(b) else (b, a)
    length, width = np.linalg.norm(u), np.linalg.norm(v)
    u, v = u / length, v / width
    risers = max(2, round(height / config.STAIR_RISE))
    rise = height / risers

    def step(s0, s1, t0, t1, top):
        corners = [p0 + u * s + v * t for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1))]
        bottom = max(0.0, top - rise - config.STAIR_WAIST)
        return [(float(x), float(y)) for x, y in corners], bottom, top - bottom

    if width < config.STAIR_U_WIDTH:
        going = length / risers
        return [step(i * going, (i + 1) * going, 0, width, (i + 1) * rise) for i in range(risers)]
    first, landing = risers // 2, min(width / 2, length / 3)
    run = length - landing
    up = [step(i * run / first, (i + 1) * run / first, 0, width / 2, (i + 1) * rise) for i in range(first)]
    rest = risers - first
    back = [step(run - (j + 1) * run / rest, run - j * run / rest, width / 2, width, (first + j + 1) * rise)
            for j in range(rest)]
    return up + [step(run, length, 0, width, first * rise)] + back


def write_ifc(plans: list[dict], out_path, schema=config.IFC_SCHEMA, roof=False) -> list[str]:
    """Write one building, one storey per plan, and a flat roof on top if asked.
    Returns validation errors (empty when valid)."""
    writer = _Writer(plans, schema)
    for plan in writer.plans:
        writer.storey(plan)
    if roof:
        writer.roof(writer.plans[-1])
    return writer.finish(out_path)


def write_all(plans: list[dict], out_path, roof=False) -> list[str]:
    """The main IFC (config.IFC_SCHEMA) plus one copy per extra schema, e.g. model_ifc2x3.ifc."""
    import copy

    errors = write_ifc(copy.deepcopy(plans), out_path, roof=roof)
    for schema in config.IFC_EXTRA_SCHEMAS:
        extra = Path(out_path).with_name(f"{Path(out_path).stem}_{schema.lower()}.ifc")
        errors += [f"{schema}: {e}" for e in write_ifc(copy.deepcopy(plans), extra, schema, roof)]
    return errors
