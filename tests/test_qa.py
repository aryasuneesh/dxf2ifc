import copy
import json
import re
from pathlib import Path

import pytest

from dxf2ifc.extract import extract
from dxf2ifc.normalize import load
from dxf2ifc.qa import bench, check, profile_ifc, wall_pr
from dxf2ifc.review import plan_image, write_review
from dxf2ifc.roles import assign_roles
from dxf2ifc.to_ifc import write_ifc
from tests.fixtures import make_house

FIXTURE = json.loads((Path(__file__).parent / "fixture_plan.json").read_text(encoding="utf-8"))


def built(tmp_path, plan=None):
    plan = copy.deepcopy(plan or FIXTURE)
    errors = write_ifc([plan], tmp_path / "model.ifc")
    return plan, tmp_path / "model.ifc", errors


def by_id(checks):
    return {c["id"]: c for c in checks}


def test_tc36_wall_precision_and_recall():
    axes = [((0, 0), (10, 0)), ((0, 0), (0, 6)), ((10, 0), (10, 6))]
    assert wall_pr(axes, axes) == (1.0, 1.0)
    precision, recall = wall_pr(axes[:2], axes)
    assert precision == 1.0 and recall == pytest.approx(16 / 22, abs=0.02)
    precision, recall = wall_pr(axes + [((20, 0), (20, 6))], axes)
    assert recall == 1.0 and precision < 0.8
    assert wall_pr([], axes) == (0.0, 0.0)


def test_tc37_requirements_on_the_fixture(tmp_path):
    plan, ifc_path, errors = built(tmp_path)
    checks = by_id(check([plan], ifc_path, runtime_s=1.5, errors=errors))
    assert list(checks) == ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R12"]
    for rid in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R12"):
        assert checks[rid]["passed"] is True, checks[rid]
    assert checks["R9"]["value"] == "2 of 2 rooms" and checks["R9"]["passed"] is True
    assert checks["R10"]["value"] == "98 %" and checks["R10"]["passed"] is True
    assert all(c["name"] and c["threshold"] for c in checks.values())


def test_requirements_fail_when_they_should(tmp_path):
    plan = copy.deepcopy(FIXTURE)
    plan["stats"]["wall_explained_length_m"] = 30.0
    for room in plan["rooms"]:
        room["area_printed_m2"] = None
    plan, ifc_path, errors = built(tmp_path, plan)
    checks = by_id(check([plan], ifc_path, runtime_s=75, errors=["broken"]))
    assert checks["R1"]["passed"] is False and checks["R10"]["passed"] is False and checks["R12"]["passed"] is False
    assert checks["R9"]["passed"] is None  # no printed areas: not applicable


def test_tc38_room_off_its_printed_area(tmp_path):
    plan = copy.deepcopy(FIXTURE)
    plan["rooms"][0].update(area_printed_m2=40.0, confidence=0.3)
    plan["flags"].append({"element_id": "r1", "severity": "review",
                          "reason": "room area 32.2 m2 differs from the printed 40.0 m2"})
    plan, ifc_path, errors = built(tmp_path, plan)
    checks = check([plan], ifc_path, runtime_s=1, errors=errors)
    assert by_id(checks)["R9"]["value"] == "1 of 2 rooms" and by_id(checks)["R9"]["passed"] is False
    write_review(plan, checks, tmp_path / "review.html", ifc_path=ifc_path)
    html = (tmp_path / "review.html").read_text(encoding="utf-8")
    assert "differs from the printed 40.0 m2" in html
    assert re.search(r"BUREAU 1</td><td>32\.2</td><td>40\.0</td><td>-19\.5 %", html)


def test_tc39_review_page(tmp_path):
    make_house(tmp_path / "house.dxf", noise=True)
    drawing = load(tmp_path / "house.dxf")
    plan = extract(drawing, assign_roles(drawing, mappings_dir=tmp_path / "m", cache_dir=tmp_path / "c"))
    errors = write_ifc([plan], tmp_path / "model.ifc")
    checks = check([plan], tmp_path / "model.ifc", runtime_s=2, errors=errors)
    write_review(plan, checks, tmp_path / "review.html", drawing=drawing, ifc_path=tmp_path / "model.ifc")
    html = (tmp_path / "review.html").read_text(encoding="utf-8")

    assert "TO BE VERIFIED" in html and "house.dxf" in html and "NIVEAU +01" in html
    assert "<script" not in html and "http://" not in html and "https://" not in html  # works offline, runs nothing
    assert html.count("data:image/png;base64,") >= 1 + 2  # the plan and a crop for each door
    review_flags = [f for f in plan["flags"] if f["severity"] == "review"]
    assert html.count('class="item"') == len(review_flags)
    assert "Storey height was not found on the drawing" in html
    assert "3.00 m" in html and "default" in html
    assert re.search(r"R1</td>.*?pass", html, re.S) and "Wall lines turned into walls" in html
    guid = re.search(r"IFC id <code>([0-9A-Za-z_$]{22})</code>", html)
    assert guid, "flagged doors show the id of their IFC element"
    assert "5.75" in html  # the dimension text nothing used is listed
    assert len(html) < 5_000_000


def test_review_without_drawing_or_ifc(tmp_path):
    write_review(copy.deepcopy(FIXTURE), [], tmp_path / "r.html")
    html = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "TO BE VERIFIED" in html and "BUREAU 2" in html


def test_plan_image_and_crop():
    full = plan_image(FIXTURE)
    crop = plan_image(FIXTURE, bbox=(5, 1, 7, 3), size=400)
    assert full[:8] == crop[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(full) > len(crop) > 2000


def test_profile_of_an_ifc(tmp_path):
    _, ifc_path, _ = built(tmp_path)
    profile = profile_ifc(ifc_path)
    assert profile["schema"] == "IFC4"
    assert profile["counts"]["IfcWall"] == 5 and profile["counts"]["IfcSpace"] == 2
    assert profile["storeys"] == [{"name": "NIVEAU +01", "elevation": 3.2}]
    assert "Pset_WallCommon" in profile["psets"]["IfcWall"] and "SECO_Trust" in profile["psets"]["IfcDoor"]


def test_tc43_bench_covers_every_variant():
    rows = bench()
    assert [row["variant"] for row in rows] == ["metres, named layers", "millimetres", "colour-only layers, split words",
                                                "national-grid coordinates", "xref layer prefix", "plan inside a block",
                                                "with furniture noise"]
    for row in rows:
        assert row["wall_precision"] >= 0.95 and row["wall_recall"] >= 0.95, row
        assert row["rooms"] == "2 of 2" and row["doors"] == "2 of 2" and row["max_area_error_pct"] < 3
