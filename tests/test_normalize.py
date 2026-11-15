import pytest

from dxf2ifc.normalize import load
from tests.fixtures import make_house


def house(tmp_path, **variant):
    make_house(tmp_path / "house.dxf", **variant)
    return load(tmp_path / "house.dxf")


def coords(drawing, layer="MUR"):
    return [(s.x1, s.y1, s.x2, s.y2) for s in drawing.segments if s.layer == layer]


def test_default_house(tmp_path):
    d = house(tmp_path)
    assert d.units_in == "m" and d.scale_to_m == 1.0 and d.warnings == []
    assert len(coords(d)) == 18
    assert len(d.arcs) == 1 and d.arcs[0].layer == "PORTE" and d.arcs[0].r == pytest.approx(0.9)
    assert [b.name for b in d.blockrefs] == ["PORTE_90"]
    assert len(d.labels) == 9
    assert d.layer_stats["MUR"]["length_m"] == pytest.approx(70.4, abs=0.01)  # 69.8 + caps
    assert d.layer_stats["TEXTE"]["types"] == {"TEXT": 8}
    assert all(s.handle for s in d.segments)


def test_tc08_mm_matches_metres(tmp_path):
    metres = coords(house(tmp_path))
    mm = house(tmp_path, units="mm")
    assert mm.scale_to_m == 0.001 and mm.units_in == "mm"
    assert len(coords(mm)) == len(metres)
    for a, b in zip(metres, coords(mm)):
        assert a == pytest.approx(b, abs=0.001)
    assert mm.arcs[0].r == pytest.approx(0.9)
    assert mm.labels[0].height == pytest.approx(0.25)


def test_tc09_offset_becomes_local(tmp_path):
    d = house(tmp_path, offset=(79000, 104000))
    assert d.origin_offset == pytest.approx((79000, 104000))
    xs = [v for s in d.segments for v in (s.x1, s.x2)]
    ys = [v for s in d.segments for v in (s.y1, s.y2)]
    assert min(xs) == pytest.approx(0) and min(ys) == pytest.approx(0)
    assert max(xs) == pytest.approx(10) and max(ys) == pytest.approx(6)


def test_tc10_block_is_flattened(tmp_path):
    plain = house(tmp_path)
    d = house(tmp_path, in_block=True, offset=(79000, 104000))
    assert len(coords(d)) == len(coords(plain)) == 18
    for a, b in zip(coords(plain), coords(d)):
        assert a == pytest.approx(b, abs=0.001)
    # nested door block: content on layer 0 inherits the door layer, not the outer INSERT's
    assert len(d.arcs) == 1 and d.arcs[0].layer == "PORTE"
    assert {b.name for b in d.blockrefs} == {"PLAN", "PORTE_90"}
    assert {s.block for s in d.segments} == {"PLAN"}
    assert len({s.handle for s in d.segments}) == 1  # all carry the top INSERT's handle


def test_tc11_xref_prefix_is_stripped(tmp_path):
    d = house(tmp_path, xref_prefix="BAT 4$0$")
    walls = [s for s in d.segments if s.layer == "MUR"]
    assert len(walls) == 18
    assert walls[0].raw_layer == "BAT 4$0$MUR"
    assert "MUR" in d.layer_stats and "BAT 4$0$MUR" not in d.layer_stats


def test_tc12_split_words_are_joined(tmp_path):
    d = house(tmp_path, colour_only=True)
    texts = {label.text for label in d.labels}
    assert {"BUREAU 1", "32.2 m2", "BUREAU 2", "21.0 m2", "NIVEAU +01", "P.01.01"} <= texts
    assert len(d.labels) == 9
    joined = next(label for label in d.labels if label.text == "BUREAU 1")
    assert len(joined.handles) == 2
    weights = {s.lineweight for s in d.segments}
    assert weights == {5, 9}
    assert all(s.poly_id for s in d.segments)


def test_tc13_unitless_is_guessed(tmp_path):
    d = house(tmp_path, units="mm", insunits=0)
    assert d.scale_to_m == 0.001
    assert any("units guessed" in w for w in d.warnings)


def test_bbox_keeps_one_part(tmp_path):
    make_house(tmp_path / "house.dxf")
    d = load(tmp_path / "house.dxf", bbox=(6.5, 0.5, 9.5, 5.5))  # inside BUREAU 2
    assert {label.text for label in d.labels} == {"BUREAU 2", "21.0 m2"}
    assert coords(d) == []


def test_not_a_dxf(tmp_path):
    bad = tmp_path / "notes.dxf"
    bad.write_text("this is not a drawing", encoding="utf-8")
    with pytest.raises(ValueError):
        load(bad)
    with pytest.raises(ValueError):
        load(tmp_path / "missing.dxf")
