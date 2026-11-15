import copy
import json
from pathlib import Path

import ezdxf
import pytest

from dxf2ifc.schema import level, validate_plan
from tests.fixtures import make_house

FIXTURE = json.loads((Path(__file__).parent / "fixture_plan.json").read_text(encoding="utf-8"))

VARIANTS = [
    {},
    {"units": "mm"},
    {"colour_only": True},
    {"offset": (79000, 104000)},
    {"xref_prefix": "BAT 4$0$"},
    {"in_block": True},
    {"noise": True},
    {"units": "mm", "insunits": 0},
]


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: ",".join(v) or "default")
def test_tc01_house_is_readable(tmp_path, variant):
    truth = make_house(tmp_path / "house.dxf", **variant)
    doc = ezdxf.readfile(tmp_path / "house.dxf")
    assert len(doc.modelspace()) > 0
    assert len(truth["rooms"]) == 2 and len(truth["doors"]) == 2
    assert len(truth["wall_axes"]) == 5


def test_tc01_variants_change_the_file(tmp_path):
    make_house(tmp_path / "a.dxf", colour_only=True, units="mm", xref_prefix="X$0$")
    msp = ezdxf.readfile(tmp_path / "a.dxf").modelspace()
    assert {e.dxftype() for e in msp} == {"LWPOLYLINE", "TEXT"}
    assert {e.dxf.layer for e in msp} == {"X$0$A-CONTOUR", "X$0$A-TEXTE"}
    weights = [e.dxf.lineweight for e in msp.query("LWPOLYLINE")]
    assert weights.count(5) > weights.count(9) >= 10
    assert "BUREAU" in [e.dxf.text for e in msp.query("TEXT")]  # words are split

    make_house(tmp_path / "b.dxf", in_block=True, offset=(79000, 104000))
    msp = ezdxf.readfile(tmp_path / "b.dxf").modelspace()
    assert [e.dxftype() for e in msp] == ["INSERT"]


def test_tc02_fixture_plan_is_valid():
    assert validate_plan(FIXTURE) == []


def test_tc03_missing_field_is_named():
    plan = copy.deepcopy(FIXTURE)
    del plan["walls"][0]["thickness_m"]
    assert validate_plan(plan) == ["walls[0].thickness_m: missing"]


def test_bad_values_are_reported():
    plan = copy.deepcopy(FIXTURE)
    plan["walls"][1]["confidence"] = 1.4
    plan["openings"][0]["wall_id"] = "w99"
    plan["rooms"][0]["polygon"] = [[0, 0], [1, 1]]
    plan["flags"][0]["severity"] = "urgent"
    plan["rooms"][1]["area_printed_m2"] = None  # allowed
    errors = validate_plan(plan)
    assert len(errors) == 4
    assert any("walls[1].confidence" in e for e in errors)
    assert any("openings[0].wall_id" in e for e in errors)
    assert any("rooms[0].polygon" in e for e in errors)
    assert any("flags[0].severity" in e for e in errors)


def test_level():
    assert [level(c) for c in (0.95, 0.8, 0.6, 0.5, 0.3)] == ["high", "high", "medium", "medium", "low"]
