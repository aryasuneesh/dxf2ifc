import copy
import json
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as element
import numpy as np
import pytest

from dxf2ifc.extract import extract
from dxf2ifc.normalize import load
from dxf2ifc.roles import assign_roles
from dxf2ifc.to_ifc import write_ifc
from tests.fixtures import make_house

FIXTURE = json.loads((Path(__file__).parent / "fixture_plan.json").read_text(encoding="utf-8"))


@pytest.fixture
def model(tmp_path):
    errors = write_ifc([copy.deepcopy(FIXTURE)], tmp_path / "model.ifc")
    return ifcopenshell.open(str(tmp_path / "model.ifc")), errors


def trust(product):
    return element.get_pset(product, "SECO_Trust") or {}


def test_tc28_file_is_valid(model):
    ifc, errors = model
    assert errors == []
    assert ifc.schema == "IFC4"


def test_tc29_spatial_tree(model):
    ifc, _ = model
    assert [len(ifc.by_type(c)) for c in ("IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey")] == [1, 1, 1, 1]
    storey = ifc.by_type("IfcBuildingStorey")[0]
    assert storey.Name == "NIVEAU +01" and storey.Elevation == pytest.approx(3.20)
    assert element.get_aggregate(storey).is_a("IfcBuilding")
    assert element.get_aggregate(element.get_aggregate(storey)).is_a("IfcSite")
    units = {u.UnitType: (u.Prefix, u.Name) for u in ifc.by_type("IfcSIUnit")}
    assert units["LENGTHUNIT"] == (None, "METRE")


def test_tc30_walls(model):
    ifc, _ = model
    walls = ifc.by_type("IfcWall")
    assert len(walls) == 5
    for wall in walls:
        assert wall.Representation is not None
        assert "IsExternal" in element.get_pset(wall, "Pset_WallCommon")
        assert element.get_container(wall).is_a("IfcBuildingStorey")
    assert sorted(w.Name for w in walls) == ["Wall 100 mm"] + ["Wall 200 mm"] * 4


def test_wall_geometry_is_where_the_plan_says(model):
    ifc, _ = model
    wall = next(w for w in ifc.by_type("IfcWall") if trust(w)["ElementId"] == "w1")  # south wall
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    verts = np.array(ifcopenshell.geom.create_shape(settings, wall).geometry.verts).reshape(-1, 3)
    assert verts.min(axis=0) == pytest.approx([0.0, 0.0, 3.2], abs=1e-6)
    assert verts.max(axis=0) == pytest.approx([10.0, 0.2, 6.2], abs=1e-6)


def test_tc31_doors_sit_in_openings_in_walls(model):
    ifc, _ = model
    doors = ifc.by_type("IfcDoor")
    assert len(doors) == 2 and len(ifc.by_type("IfcOpeningElement")) == 2
    for door in doors:
        assert door.OverallHeight == pytest.approx(2.10)
        assert door.OverallWidth in (pytest.approx(0.90), pytest.approx(1.00))
        opening = door.FillsVoids[0].RelatingOpeningElement
        assert opening.VoidsElements[0].RelatingBuildingElement.is_a("IfcWall")
        assert element.get_pset(door, "Pset_DoorCommon")["Reference"].startswith("P.01.")


def test_tc32_rooms(model):
    ifc, _ = model
    spaces = {s.LongName: s for s in ifc.by_type("IfcSpace")}
    assert set(spaces) == {"BUREAU 1", "BUREAU 2"}
    for name, area in (("BUREAU 1", 32.2), ("BUREAU 2", 21.0)):
        quantities = element.get_pset(spaces[name], "Qto_SpaceBaseQuantities")
        assert quantities["NetFloorArea"] == pytest.approx(area, abs=0.1)
        assert element.get_aggregate(spaces[name]).is_a("IfcBuildingStorey")
        assert trust(spaces[name])["PrintedArea"] == pytest.approx(area)
    assert len(ifc.by_type("IfcSlab")) == 1


def test_tc33_every_element_carries_trust(model):
    ifc, _ = model
    products = [p for p in ifc.by_type("IfcProduct") if not p.is_a("IfcSite") and not p.is_a("IfcBuilding")]
    assert len(products) == 1 + 5 + 2 + 2 + 2 + 1  # storey, walls, openings, doors, spaces, slab
    for product in products:
        props = trust(product)
        assert 0 <= props["Confidence"] <= 1, product
        assert props["Status"] == "To be verified"
        assert props["ConfidenceLevel"] in ("High", "Medium", "Low")
        assert props["SourceFile"] == "house.dxf"
    wall = next(w for w in ifc.by_type("IfcWall") if trust(w)["ElementId"] == "w1")
    assert trust(wall)["SourceHandles"] == "A1,A2,A3,A4" and trust(wall)["SourceLayer"] == "MUR"


def test_elements_are_coloured_by_confidence(model):
    ifc, _ = model
    assert len(ifc.by_type("IfcSurfaceStyle")) == 6  # three levels, solid and see-through
    assert len(ifc.by_type("IfcStyledItem")) == 5 + 2 + 2 + 1  # walls, doors, spaces, slab


def test_tc34_same_input_gives_same_ids(tmp_path):
    ids = []
    for name in ("a.ifc", "b.ifc"):
        write_ifc([copy.deepcopy(FIXTURE)], tmp_path / name)
        ifc = ifcopenshell.open(str(tmp_path / name))
        ids.append(sorted(root.GlobalId for root in ifc.by_type("IfcRoot")))
    assert ids[0] == ids[1]
    assert len(set(ids[0])) == len(ids[0])


def test_ids_stay_unique_when_two_walls_share_their_source_lines(tmp_path):
    plan = copy.deepcopy(FIXTURE)
    plan["walls"][1]["src"] = copy.deepcopy(plan["walls"][0]["src"])
    assert write_ifc([plan], tmp_path / "m.ifc") == []
    ifc = ifcopenshell.open(str(tmp_path / "m.ifc"))
    ids = [root.GlobalId for root in ifc.by_type("IfcRoot")]
    assert len(set(ids)) == len(ids)


def test_tc35_ifc2x3(tmp_path):
    errors = write_ifc([copy.deepcopy(FIXTURE)], tmp_path / "old.ifc", schema="IFC2X3")
    assert errors == []
    ifc = ifcopenshell.open(str(tmp_path / "old.ifc"))
    assert ifc.schema == "IFC2X3" and len(ifc.by_type("IfcWall")) == 5


def test_two_storeys_in_one_building(tmp_path):
    upper = copy.deepcopy(FIXTURE)
    upper["storey"].update(name="NIVEAU +02", elevation_m=6.20)
    upper["source"].update(file="house2.dxf", project="house2")
    assert write_ifc([upper, copy.deepcopy(FIXTURE)], tmp_path / "b.ifc") == []
    ifc = ifcopenshell.open(str(tmp_path / "b.ifc"))
    assert [s.Name for s in ifc.by_type("IfcBuildingStorey")] == ["NIVEAU +01", "NIVEAU +02"]
    assert len(ifc.by_type("IfcBuilding")) == 1 and len(ifc.by_type("IfcWall")) == 10
    ids = [root.GlobalId for root in ifc.by_type("IfcRoot")]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("variant", [{}, {"colour_only": True, "units": "mm"}], ids=["layered", "colour_only"])
def test_extracted_plan_writes_a_valid_ifc(tmp_path, variant):
    make_house(tmp_path / "h.dxf", **variant)
    drawing = load(tmp_path / "h.dxf")
    plan = extract(drawing, assign_roles(drawing, mappings_dir=tmp_path / "m", cache_dir=tmp_path / "c"))
    assert write_ifc([plan], tmp_path / "h.ifc") == []
    ifc = ifcopenshell.open(str(tmp_path / "h.ifc"))
    assert len(ifc.by_type("IfcWall")) == 5 and len(ifc.by_type("IfcDoor")) == 2 and len(ifc.by_type("IfcSpace")) == 2
    site = ifc.by_type("IfcSite")[0]
    assert element.get_pset(site, "SECO_Georef")["SourceUnits"] == variant.get("units", "m")


def test_both_schemas_are_written_and_valid(tmp_path):
    from dxf2ifc.to_ifc import write_all
    assert write_all([copy.deepcopy(FIXTURE)], tmp_path / "model.ifc") == []
    assert ifcopenshell.open(tmp_path / "model.ifc").schema == "IFC4"
    old = ifcopenshell.open(tmp_path / "model_ifc2x3.ifc")
    assert old.schema == "IFC2X3" and len(old.by_type("IfcSpace")) == 2
    # writing IFC4 again after IFC2X3 in the same process stays clean
    assert write_all([copy.deepcopy(FIXTURE)], tmp_path / "again.ifc") == []
