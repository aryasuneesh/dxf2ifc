import json
from pathlib import Path

import ifcopenshell

from dxf2ifc.extract import _grid
from dxf2ifc.normalize import Drawing, Label, Segment
from dxf2ifc.to_ifc import write_ifc
from dxf2ifc.viewer import levels


def seg(x1, y1, x2, y2, layer="S-GRID"):
    return Segment(x1, y1, x2, y2, layer, layer, "1", -1, None, False, None)


def test_grid_lines_get_the_tag_in_their_bubble():
    drawing = Drawing("g.dxf", "g", "m", 1.0, (0.0, 0.0))
    drawing.segments = [seg(0, 0, 0, 10), seg(0, 10, 0, 20),  # one axis drawn in two pieces
                        seg(5, 0, 5, 20), seg(-2, 3, 12, 3), seg(1, 1, 1.5, 1)]  # the last is too short: a tick
    drawing.labels = [Label("A.", 0.1, -1.0, 0.3, 0, "S-GRID", ["2"]), Label("B", 5, 21, 0.3, 0, "S-GRID", ["3"]),
                      Label("1", -3, 3.1, 0.3, 0, "S-GRID", ["4"]), Label("far", 30, 30, 0.3, 0, "S-GRID", ["5"])]
    grid = _grid(drawing, {"layers": {"S-GRID": {"role": "grid"}}})
    assert sorted(g["tag"] for g in grid) == ["1", "A", "B"]
    a = next(g for g in grid if g["tag"] == "A")
    assert a["line"] == [[0.0, 0.0], [0.0, 20.0]]


def test_grid_becomes_ifc_grid_and_viewer_labels(tmp_path):
    plan = json.loads((Path(__file__).parent / "fixture_plan.json").read_text(encoding="utf-8"))
    plan["grid"] = [{"tag": "A", "line": [[0, -1], [0, 9]], "layer": "S-GRID"}, {"tag": "B", "line": [[5, -1], [5, 9]], "layer": "S-GRID"},
                    {"tag": "1", "line": [[-1, 0], [11, 0]], "layer": "S-GRID"}]
    write_ifc([plan], tmp_path / "m.ifc")
    model = ifcopenshell.open(str(tmp_path / "m.ifc"))
    (grid,) = model.by_type("IfcGrid")
    assert sorted(a.AxisTag for a in grid.UAxes) == ["A", "B"] and [a.AxisTag for a in grid.VAxes] == ["1"]
    info = levels(tmp_path / "m.ifc")
    assert {g["tag"] for g in info["grid"]} == {"A", "B", "1"} and info["storeys"][0]["name"]
