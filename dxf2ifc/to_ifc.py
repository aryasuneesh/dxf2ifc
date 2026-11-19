"""plan.json -> IFC. No extraction logic lives here: a value missing from the plan is an error.

Every body is an extruded outline in plan coordinates, lifted to the storey elevation.
# ponytail: walls are plain extrusions, not axis-based "standard case" walls with types and
# material layers. Viewers and checks do not care; add IfcWallType per thickness if SECO asks.
"""

import math
import uuid
from pathlib import Path

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
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
        self.styles = {}
        for name, (red, green, blue) in config.CONFIDENCE_COLOURS.items():
            for kind, transparency in (("solid", 0.0), ("space", config.SPACE_TRANSPARENCY)):
                style = api.style.add_style(f, name=f"confidence {name} ({kind})")
                attributes = {"SurfaceColour": {"Name": None, "Red": red, "Green": green, "Blue": blue}}
                if not self.old:
                    attributes["Transparency"] = transparency
                api.style.add_surface_style(f, style=style, ifc_class="IfcSurfaceStyleShading", attributes=attributes)
                self.styles[name, kind] = style

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
        curve = self.builder.polyline([(float(x), float(y)) for x, y in outline], closed=True)
        item = self.builder.extrude(curve, magnitude=float(depth), position=(0.0, 0.0, float(bottom)))
        shape = self.builder.get_representation(self.body, [item])
        api.geometry.assign_representation(self.f, product=product, representation=shape)
        if confidence is not None:
            api.style.assign_representation_styles(self.f, shape_representation=shape,
                                                   styles=[self.styles[level(confidence), kind]])

    def storey(self, plan):
        f, info = self.f, plan["storey"]
        tag, elevation, height = plan["source"]["project"], info["elevation_m"], info["height_m"]
        key = lambda element: (tag, ",".join(sorted(element["src"]["handles"])) or element["id"])

        storey = self.root("IfcBuildingStorey", info["name"], "storey", tag)
        storey.Elevation = float(elevation)
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
            walls[wall["id"]] = (product, wall)

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
                self.solid(product, _strip(span, config.LEAF_THICKNESS), opening["sill_m"], opening["height_m"], opening["confidence"])
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

        contained = [product for product, _ in walls.values()] + fillings + [slab]
        api.spatial.assign_container(f, products=contained, relating_structure=storey)
        if spaces:
            api.aggregate.assign_object(f, products=spaces, relating_object=storey)

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


def write_ifc(plans: list[dict], out_path, schema=config.IFC_SCHEMA) -> list[str]:
    """Write one building, one storey per plan. Returns validation errors (empty when valid)."""
    writer = _Writer(plans, schema)
    for plan in writer.plans:
        writer.storey(plan)
    return writer.finish(out_path)


def write_all(plans: list[dict], out_path) -> list[str]:
    """The main IFC (config.IFC_SCHEMA) plus one copy per extra schema, e.g. model_ifc2x3.ifc."""
    import copy

    errors = write_ifc(copy.deepcopy(plans), out_path)
    for schema in config.IFC_EXTRA_SCHEMAS:
        extra = Path(out_path).with_name(f"{Path(out_path).stem}_{schema.lower()}.ifc")
        errors += [f"{schema}: {e}" for e in write_ifc(copy.deepcopy(plans), extra, schema)]
    return errors
