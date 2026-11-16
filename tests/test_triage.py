import zipfile

import ezdxf

from dxf2ifc.triage import fast_scan, read_index, score, shortlist, triage, write_shortlist
from tests.fixtures import make_house


def survey_file(path):
    """Twenty survey points: a block and a number each, like the Geolux N10 sample."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    doc.blocks.new("S061").add_circle((0, 0), 0.1)
    msp = doc.modelspace()
    for i in range(20):
        msp.add_blockref("S061", (76000 + i, 77000), dxfattribs={"layer": "GLX_POINTS_TOPO"})
        msp.add_text(f"{i}.861", dxfattribs={"layer": "GLX_ALTITUDES", "insert": (76000 + i, 77001)})
    doc.saveas(path)


def scan(path):
    with open(path, "rb") as stream:
        return fast_scan(stream)


def test_tc04_fast_scan_reads_the_house(tmp_path):
    make_house(tmp_path / "house.dxf", noise=True)
    result = scan(tmp_path / "house.dxf")
    assert result["units"] == 6
    assert result["counts"]["LINE"] >= 20 and result["counts"]["TEXT"] == 9
    assert result["counts"]["INSERT"] == 1 and result["counts"]["ARC"] == 1  # the arc sits inside the door block
    assert {"MUR", "PORTE", "TEXTE", "COTATION", "MOBILIER"} <= set(result["layers"])
    assert "BUREAU 1" in result["texts"] and "PORTE_90" in result["blocks"]
    assert "BLOCK" not in result["counts"]


def test_tc05_house_outranks_survey_points(tmp_path):
    make_house(tmp_path / "house.dxf")
    survey_file(tmp_path / "survey.dxf")
    house_score, house_signals = score(scan(tmp_path / "house.dxf"))
    survey_score, survey_signals = score(scan(tmp_path / "survey.dxf"))
    assert house_score > 0 >= survey_score
    assert {"wall_layer", "door_window", "storey_word"} <= set(house_signals)
    assert {"survey", "tiny"} <= set(survey_signals)


def test_score_signals():
    base = {"counts": {"LINE": 900}, "units": 6, "blocks": [], "layers": [], "texts": []}
    rooms = {**base, "texts": ["Bureau 1", "WC", "Cuisine", "12,50 m²", "8.2 m2", "30.0 m2", "Niveau 0"]}
    assert score(rooms) == (3 + 3 + 1 + 1, ["room_words", "areas", "storey_word", "many_lines"])
    section = {**base, "layers": ["BAT 4$0$COUPE AA", "TERRASSEMENT"]}
    assert score(section) == (1 - 2, ["many_lines", "section_or_site"])
    plan_with_sections = {**base, "layers": ["XREF$0$Murs porteurs", "COUPE"], "blocks": ["Fenster_120"]}
    assert score(plan_with_sections)[1] == ["wall_layer", "door_window", "many_lines"]


def test_tc06_zip_with_a_bad_file(tmp_path):
    make_house(tmp_path / "2022-N1_001.dxf")
    make_house(tmp_path / "2022-N1_002.dxf", colour_only=True)
    survey_file(tmp_path / "2022-N2_001.dxf")
    (tmp_path / "2022-N3_001.dxf").write_bytes(b"AutoCAD Binary DXF\r\n\x1a\x00\x36\x01\xff")  # cut off mid-tag
    archive = tmp_path / "set.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(tmp_path.glob("*.dxf")):
            zf.write(path, f"2022/{path.name}")
        zf.writestr("2022/readme.txt", "not a drawing")

    assert triage(archive, tmp_path / "index.csv", workers=1) == 4
    rows = {row["path"]: row for row in read_index(tmp_path / "index.csv")}
    assert len(rows) == 4
    assert rows["2022/2022-N3_001.dxf"]["n_entities"] == "0"  # a cut-off file counts as empty, the run goes on
    assert rows["2022/2022-N1_001.dxf"]["project_id"] == "2022-N1"
    assert int(rows["2022/2022-N1_001.dxf"]["score"]) > int(rows["2022/2022-N2_001.dxf"]["score"])
    assert rows["2022/2022-N1_001.dxf"]["ref_hit"] == "0"


def test_tc07_second_run_adds_nothing(tmp_path):
    folder = tmp_path / "set"
    for name in ("a_001", "a_002", "b_001", "b_002", "b_003"):
        make_house(folder / f"{name}.dxf")
    index = tmp_path / "index.csv"
    assert triage(folder, index, workers=2) == 5  # goes through the process pool
    assert triage(folder, index, workers=2) == 0
    assert len(read_index(index)) == 5
    make_house(folder / "c_001.dxf")
    assert triage(folder, index, workers=1) == 1
    assert len(read_index(index)) == 6


def test_shortlist_ranks_and_draws(tmp_path):
    folder = tmp_path / "set"
    make_house(folder / "plan_001.dxf", noise=True)
    survey_file(folder / "topo_001.dxf")
    index = tmp_path / "index.csv"
    triage(folder, index, workers=1)
    top, _ = shortlist(index, n=1)
    assert top[0]["path"].endswith("plan_001.dxf")
    write_shortlist(index, tmp_path / "short.md", n=2, thumbs_dir=tmp_path / "thumbs")
    text = (tmp_path / "short.md").read_text(encoding="utf-8")
    assert "plan_001.dxf" in text and "2 files scanned" in text
    assert (tmp_path / "thumbs" / "plan_001.png").stat().st_size > 1000


def test_binary_dxf_scans_like_text(tmp_path):
    make_house(tmp_path / "house.dxf", noise=True)
    ezdxf.readfile(tmp_path / "house.dxf").saveas(tmp_path / "house_bin.dxf", fmt="bin")
    assert (tmp_path / "house_bin.dxf").read_bytes().startswith(b"AutoCAD Binary DXF")
    text, binary = scan(tmp_path / "house.dxf"), scan(tmp_path / "house_bin.dxf")
    assert binary == text
    assert binary["units"] == 6 and binary["counts"]["LINE"] >= 20 and "BUREAU 1" in binary["texts"]
