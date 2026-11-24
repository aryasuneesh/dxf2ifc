import math

import ezdxf
import pytest

from shapely.geometry import Polygon

from dxf2ifc.extract import extract
from dxf2ifc.normalize import load
from dxf2ifc.qa import wall_pr
from dxf2ifc.roles import assign_roles
from dxf2ifc.schema import validate_plan
from tests.fixtures import make_house


def run(tmp_path, edit=None, **variant):
    path = tmp_path / "house.dxf"
    truth = make_house(path, **variant)
    if edit:
        doc = ezdxf.readfile(path)
        edit(doc.modelspace())
        doc.saveas(path)
    drawing = load(path)
    roles = assign_roles(drawing, mappings_dir=tmp_path / "mappings", cache_dir=tmp_path / "cache")
    return extract(drawing, roles), truth


def pr(plan, truth):
    return wall_pr([w["axis"] for w in plan["walls"]], [(a, b) for a, b, _ in truth["wall_axes"]])


def test_tc20_walls(tmp_path):
    plan, truth = run(tmp_path)
    precision, recall = pr(plan, truth)
    assert precision >= 0.95 and recall >= 0.95
    assert len(plan["walls"]) == 5
    assert sorted(round(w["thickness_m"], 2) for w in plan["walls"]) == [0.10, 0.20, 0.20, 0.20, 0.20]
    partition = next(w for w in plan["walls"] if round(w["thickness_m"], 2) == 0.10)
    assert partition["is_external"] is False
    assert sum(w["is_external"] for w in plan["walls"]) == 4
    assert all(w["confidence"] >= 0.8 and w["src"]["handles"] for w in plan["walls"])
    stats = plan["stats"]
    assert stats["wall_explained_length_m"] / stats["wall_layer_length_m"] >= 0.95


def test_tc21_rooms(tmp_path):
    plan, truth = run(tmp_path)
    rooms = {r["name"]: r for r in plan["rooms"]}
    assert set(rooms) == {"BUREAU 1", "BUREAU 2"}
    for expected in truth["rooms"]:
        room = rooms[expected["name"]]
        assert room["area_m2"] == pytest.approx(expected["area"], rel=0.03)
        assert room["area_printed_m2"] == expected["area"]
        assert room["confidence"] == 0.95 and len(room["polygon"]) >= 4


def test_tc22_openings(tmp_path):
    plan, truth = run(tmp_path)
    assert len(plan["openings"]) == 2
    walls = {w["id"]: w for w in plan["walls"]}
    for door in truth["doors"]:
        found = min(plan["openings"], key=lambda o: math.dist(o["center"], door["center"]))
        assert math.dist(found["center"], door["center"]) < 0.1
        assert found["width_m"] == pytest.approx(door["width"], abs=0.05)
        assert found["kind"] == "door" and found["tag"] == door["tag"]
        assert found["wall_id"] in walls
    rules = sorted(o["rule"] for o in plan["openings"])
    assert rules == ["gap+tag", "gap+tag+symbol"]
    assert any("door height is a default" in f["reason"] for f in plan["flags"])


def test_tc23_noise_does_not_become_walls(tmp_path):
    plan, truth = run(tmp_path, noise=True)
    precision, recall = pr(plan, truth)
    assert precision >= 0.95 and recall >= 0.95
    assert len(plan["rooms"]) == 2


@pytest.mark.parametrize("variant", [
    {"colour_only": True, "units": "mm"},
    {"in_block": True, "offset": (79000, 104000)},
    {"xref_prefix": "BAT 4$0$", "units": "mm"},
], ids=["colour_only_mm", "in_block_offset", "xref_mm"])
def test_tc24_variants(tmp_path, variant):
    plan, truth = run(tmp_path, **variant)
    precision, recall = pr(plan, truth)
    assert recall >= 0.90 and precision >= 0.90
    assert {r["name"] for r in plan["rooms"]} == {"BUREAU 1", "BUREAU 2"}
    assert len(plan["openings"]) == 2
    if variant.get("colour_only"):
        assert plan["source"]["family"] == "colour_only"
        assert all(w["confidence"] <= 0.6 for w in plan["walls"])


def test_tc25_open_room_is_flagged_not_written(tmp_path):
    def remove_east_wall(msp):
        for line in list(msp.query("LINE")):
            if line.dxf.start.x > 9.7 and line.dxf.end.x > 9.7:
                msp.delete_entity(line)

    plan, _ = run(tmp_path, edit=remove_east_wall)
    assert [r["name"] for r in plan["rooms"]] == ["BUREAU 1"]
    assert any("room not closed" in f["reason"] and "BUREAU 2" in f["reason"] for f in plan["flags"])
    assert validate_plan(plan) == []


def test_tc26_storey(tmp_path):
    plan, truth = run(tmp_path)
    storey = plan["storey"]
    assert storey["name"] == "NIVEAU +01"
    assert storey["elevation_m"] == pytest.approx(3.20)
    assert storey["height_source"] == "default" and storey["height_m"] == 3.0
    assert any("storey height" in f["reason"] for f in plan["flags"])


def test_storey_arguments_win(tmp_path):
    make_house(tmp_path / "h.dxf")
    drawing = load(tmp_path / "h.dxf")
    roles = assign_roles(drawing, mappings_dir=tmp_path / "m", cache_dir=tmp_path / "c")
    plan = extract(drawing, roles, elevation=7.48, height=3.13)
    assert plan["storey"]["elevation_m"] == 7.48 and plan["storey"]["height_m"] == 3.13
    assert plan["storey"]["height_source"] == "argument"


def test_tc27_plan_follows_contract(tmp_path):
    plan, _ = run(tmp_path, noise=True)
    assert validate_plan(plan) == []
    for element in plan["walls"] + plan["openings"] + plan["rooms"]:
        assert 0 <= element["confidence"] <= 1 and element["rule"] and "src" in element
    assert [w["id"] for w in plan["walls"]] == ["w1", "w2", "w3", "w4", "w5"]
    assert plan["slab"]["outline"] and plan["slab"]["thickness_m"] == 0.25
    assert plan["stats"]["labels_total"] == 9 and plan["stats"]["labels_unused"] == 1  # the "5.75" dimension text


def test_rerun_gives_same_ids(tmp_path):
    first, _ = run(tmp_path)
    second, _ = run(tmp_path)
    assert first == second


def test_no_walls_still_gives_a_valid_plan(tmp_path):
    def remove_walls(msp):
        for line in list(msp.query('LINE[layer=="MUR"]')):
            msp.delete_entity(line)

    plan, _ = run(tmp_path, edit=remove_walls)
    assert plan["walls"] == [] and plan["rooms"] == []
    assert validate_plan(plan) == []
    assert any("no walls" in f["reason"] for f in plan["flags"])


def test_sheet_frame_is_dropped(tmp_path):
    def add_frame(msp):  # a double-line sheet border well away from the house
        for off in (0.0, 0.1):
            a, b = -8 - off, 18 + off
            for start, end in (((a, a), (b, a)), ((b, a), (b, b)), ((b, b), (a, b)), ((a, b), (a, a))):
                msp.add_line(start, end, dxfattribs={"layer": "MUR"})

    plan, _ = run(tmp_path, edit=add_frame)
    assert len(plan["walls"]) == 5
    assert {r["name"] for r in plan["rooms"]} == {"BUREAU 1", "BUREAU 2"}
    assert any("outside the main building" in f["reason"] and f["severity"] == "info" for f in plan["flags"])


def test_single_line_wall_closes_rooms(tmp_path):
    def add_glazing(msp):  # a partition drawn as one line across BUREAU 1
        msp.add_line((1.5, 0.2), (1.5, 5.8), dxfattribs={"layer": "MUR"})

    plan, _ = run(tmp_path, edit=add_glazing)
    single = [w for w in plan["walls"] if w["rule"] == "single_line"]
    assert len(single) == 1 and single[0]["confidence"] <= 0.3
    assert single[0]["thickness_m"] == 0.10
    assert len(plan["rooms"]) == 3
    assert any(r["name"].startswith("Unnamed") and r["confidence"] == 0.4 for r in plan["rooms"])
    assert any("single line" in f["reason"] for f in plan["flags"])


def test_glued_door_tags_are_separated(tmp_path):
    def glue(msp):
        for text in list(msp.query("TEXT")):
            if text.dxf.text == "P.01.02":
                text.dxf.text = "P.01.02 P.01.09"

    plan, _ = run(tmp_path, edit=glue)
    assert sorted(o["tag"] for o in plan["openings"]) == ["P.01.01", "P.01.02"]
    assert any("door tag without opening: P.01.09" in f["reason"] for f in plan["flags"])


@pytest.mark.parametrize("variant", [{}, {"colour_only": True}], ids=["block_arc", "polyline_halves"])
def test_door_in_wall_drawn_without_a_gap(tmp_path, variant):
    def fill_partition_gap(msp):  # the partition runs through the doorway; only the swing shows the door
        for x in (5.95, 6.05):
            if variant:
                msp.add_lwpolyline([(x, 1.55), (x, 2.45)], dxfattribs={"layer": "A-CONTOUR", "lineweight": 9})
            else:
                msp.add_line((x, 1.55), (x, 2.45), dxfattribs={"layer": "MUR"})

    plan, truth = run(tmp_path, edit=fill_partition_gap, **variant)
    assert len(plan["openings"]) == 2
    door = min(plan["openings"], key=lambda o: math.dist(o["center"], (6.0, 2.0)))
    assert math.dist(door["center"], (6.0, 2.0)) < 0.05
    assert door["width_m"] == pytest.approx(0.90, abs=0.03)
    assert door["kind"] == "door" and door["rule"] == "swing+tag" and door["tag"] == "P.01.01"
    wall = next(w for w in plan["walls"] if w["id"] == door["wall_id"])
    assert round(wall["thickness_m"], 2) == 0.10
    assert {r["name"] for r in plan["rooms"]} == {"BUREAU 1", "BUREAU 2"}


def test_free_standing_door_gets_its_own_thin_wall(tmp_path):
    def add_door(msp):  # a swing in the middle of BUREAU 1 with no wall near it
        msp.add_arc((2.0, 4.0), 0.8, 0, 90, dxfattribs={"layer": "PORTE"})
        msp.add_line((2.0, 4.0), (2.8, 4.0), dxfattribs={"layer": "PORTE"})

    plan, _ = run(tmp_path, edit=add_door)
    assert len(plan["openings"]) == 3
    door = min(plan["openings"], key=lambda o: math.dist(o["center"], (2.0, 4.4)))
    assert door["rule"] == "swing" and door["width_m"] == pytest.approx(0.8, abs=0.02)
    stub = next(w for w in plan["walls"] if w["id"] == door["wall_id"])
    assert stub["rule"] == "door_stub" and stub["confidence"] <= 0.3


def test_opening_height_prefers_the_door_over_the_panel_above(tmp_path):
    def add_note(msp):
        msp.add_text("HL : 300", dxfattribs={"layer": "TEXTE", "height": 0.15, "insert": (4.6, 1.6)})
        msp.add_text("(ouverture : 230,", dxfattribs={"layer": "TEXTE", "height": 0.15, "insert": (4.6, 1.3)})

    plan, _ = run(tmp_path, edit=add_note)
    door = next(o for o in plan["openings"] if o["tag"] == "P.01.01")
    assert door["height_m"] == 2.30 and door["confidence"] == 0.90


def test_drawing_without_rooms_is_called_out(tmp_path):
    def keep_one_wall(msp):  # only the south wall is left: walls but nothing closed
        for line in list(msp.query('LINE[layer=="MUR"]')):
            if max(line.dxf.start.y, line.dxf.end.y) > 0.25:
                msp.delete_entity(line)

    plan, _ = run(tmp_path, edit=keep_one_wall)
    assert plan["walls"] and plan["rooms"] == []
    assert any("may be an elevation" in f["reason"] for f in plan["flags"])


def test_too_many_wall_lines_is_refused_quickly(tmp_path, monkeypatch):
    from dxf2ifc import config
    monkeypatch.setattr(config, "MAX_WALL_CANDIDATES", 10)
    with pytest.raises(ValueError, match="not a floor plan"):
        run(tmp_path)


def test_arcs_that_close_on_themselves_are_not_doors(tmp_path):
    def add_circle_in_two_halves(msp):  # a round column or a symbol, same centre and radius
        msp.add_arc((2.0, 4.0), 0.8, 0, 45, dxfattribs={"layer": "PORTE"})
        msp.add_arc((2.0, 4.0), 0.8, 45, 0, dxfattribs={"layer": "PORTE"})

    plan, _ = run(tmp_path, edit=add_circle_in_two_halves)
    assert len(plan["openings"]) == 2


def _box(x, y, w, h, t=0.2):
    """Four walls around a w x h free space whose corner is (x, y)."""
    from dxf2ifc.extract import _Wall
    return [_Wall(1, 0, y - t / 2, x - t, x + w + t, t, [], "w", 1.0),
            _Wall(1, 0, y + h + t / 2, x - t, x + w + t, t, [], "w", 1.0),
            _Wall(0, 1, -(x - t / 2), y - t, y + h + t, t, [], "w", 1.0),
            _Wall(0, 1, -(x + w + t / 2), y - t, y + h + t, t, [], "w", 1.0)]


@pytest.mark.parametrize("x, y, w, h", [(0.37, 1.11, 4.0, 5.0), (2.9, 0.4, 3.13, 2.07), (5.5, 3.3, 10.3, 7.7)])
def test_room_area_has_no_raster_bias(x, y, w, h):
    from dxf2ifc.extract import _rooms
    rooms = _rooms(_box(x, y, w, h), [{"name": "A", "area": None, "at": (x + w / 2, y + h / 2), "labels": []}], set(), [])
    assert rooms[0]["area_m2"] == pytest.approx(w * h, rel=0.012)


def test_building_in_loose_pieces_is_kept():
    from dxf2ifc.extract import _main_building
    # three blocks in an L, each 1.5 m from the next: none lies inside the largest one's extent
    walls = _box(0, 0, 10, 8) + _box(11.9, 0, 6, 6) + _box(11.9, 7.9, 6, 6) + _box(60, 60, 2, 1)
    kept, dropped = _main_building(walls, (0, 0, 70, 70))
    assert len(kept) == 12 and dropped == 4


def test_stair_found_from_treads_on_any_layer(tmp_path):
    def add_treads(msp):  # ten 1.2 m treads, 28 cm apart, on a layer named after nothing
        for i in range(10):
            msp.add_line((1.0, 1.0 + 0.28 * i), (2.2, 1.0 + 0.28 * i), dxfattribs={"layer": "TRAITS"})
    plan, _ = run(tmp_path, edit=add_treads)
    assert len(plan["stairs"]) == 1 and plan["stairs"][0]["rule"] == "tread_run"
    xs, ys = zip(*plan["stairs"][0]["polygon"])
    assert max(xs) - min(xs) == pytest.approx(1.2, abs=0.01) and max(ys) - min(ys) == pytest.approx(2.52, abs=0.01)
    assert any("stairs are modelled from their outline" in f["reason"] for f in plan["flags"])


def test_stair_layer_outline(tmp_path):
    def add_stair(msp):
        msp.add_lwpolyline([(7, 1), (9, 1), (9, 4), (7, 4)], close=True, dxfattribs={"layer": "ESCALIER"})
        msp.add_line((7, 1), (9, 4), dxfattribs={"layer": "ESCALIER"})
    plan, _ = run(tmp_path, edit=add_stair)
    assert [s["rule"] for s in plan["stairs"]] == ["stair_layer"]
    assert Polygon(plan["stairs"][0]["polygon"]).area == pytest.approx(6.0, rel=0.02)


def test_no_stairs_in_plain_house(tmp_path):
    plan, _ = run(tmp_path)
    assert plan["stairs"] == []


def test_columns_round_and_square(tmp_path):
    def add_columns(msp):
        for start in (0, 90, 180, 270):  # a round column drawn as four quarter arcs, as in SECO plans
            msp.add_arc((3, 3), 0.175, start, start + 90, dxfattribs={"layer": "POTEAUX"})
        msp.add_lwpolyline([(6, 2), (6.3, 2), (6.3, 2.3), (6, 2.3)], close=True, dxfattribs={"layer": "POTEAUX"})
        msp.add_line((6, 2), (6.3, 2.3), dxfattribs={"layer": "POTEAUX"})  # hatch line inside it
        msp.add_line((1, 1), (9, 1.01), dxfattribs={"layer": "POTEAUX"})  # a grid line, not a column
    plan, _ = run(tmp_path, edit=add_columns)
    columns = sorted(plan["columns"], key=lambda c: c["polygon"][0][0])
    assert [c["round"] for c in columns] == [True, False]
    assert Polygon(columns[0]["polygon"]).area == pytest.approx(3.1416 * 0.175 ** 2, rel=0.05)
    assert Polygon(columns[1]["polygon"]).area == pytest.approx(0.09, rel=0.02)
    assert any("2 columns are modelled" in f["reason"] for f in plan["flags"])


def test_furniture_and_fittings_from_block_names(tmp_path):
    def add_blocks(msp):
        doc = msp.doc
        wc = doc.blocks.new("WC 18")
        wc.add_lwpolyline([(0, 0), (0.4, 0), (0.4, 0.65), (0, 0.65)], close=True)
        logo = doc.blocks.new("Cartouche")  # not furniture: ignored
        logo.add_line((0, 0), (1, 0))
        msp.add_blockref("WC 18", (2, 2), dxfattribs={"layer": "SANITAIRE"})
        msp.add_blockref("Cartouche", (3, 3), dxfattribs={"layer": "0"})
    plan, _ = run(tmp_path, edit=add_blocks)
    [wc] = plan["equipment"]
    assert wc["kind"] == "toilet" and wc["name"] == "WC"
    assert Polygon(wc["polygon"]).area == pytest.approx(0.26, rel=0.02)


def test_fence_posts_are_not_columns():
    from dxf2ifc.roles import rule_role
    assert rule_role("P501_CLOTURE-POTEAU") is None and rule_role("10 POTEAUX") == "column"


def test_fire_symbols_from_blocks_and_clusters(tmp_path):
    def add_fire(msp):
        doc = msp.doc
        block = doc.blocks.new("EXTINCTEUR_6KG")
        block.add_circle((0, 0), 0.15)
        msp.add_blockref("EXTINCTEUR_6KG", (3.0, 4.0), dxfattribs={"layer": "8-PROTECTION INCENDIE"})
        msp.add_circle((7.5, 3.0), 0.1, dxfattribs={"layer": "DETECTION INCENDIE"})  # a loose detector symbol
        msp.add_line((1, 1), (9, 1), dxfattribs={"layer": "FIRE LANE"})  # long: a lane, not a symbol
        msp.add_blockref("EXTINCTEUR_6KG", (60.0, 60.0), dxfattribs={"layer": "8-PROTECTION INCENDIE"})  # legend
    plan, _ = run(tmp_path, edit=add_fire)
    kinds = sorted((f["kind"], f["rule"]) for f in plan["fire"])
    assert kinds == [("detector", "fire_cluster"), ("extinguisher", "fire_block")]
    assert any("fire-safety symbols" in f["reason"] for f in plan["flags"])
