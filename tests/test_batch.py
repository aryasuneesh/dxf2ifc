import csv

from dxf2ifc.batch import run_batch
from tests.fixtures import make_house


def test_batch_records_results_and_survives_bad_files(tmp_path):
    make_house(tmp_path / "a.dxf")
    make_house(tmp_path / "b.dxf", colour_only=True, units="mm")
    (tmp_path / "bad.dxf").write_text("not a drawing", encoding="utf-8")
    paths = [tmp_path / name for name in ("a.dxf", "b.dxf", "bad.dxf")]
    out = tmp_path / "results.csv"

    assert run_batch(paths, out, workers=1) == 3
    assert run_batch(paths, out, workers=1) == 0  # nothing left to do
    with out.open(newline="", encoding="utf-8") as handle:
        rows = {row["path"]: row for row in csv.DictReader(handle)}
    good = rows[str(tmp_path / "a.dxf")]
    assert good["error"] == "" and good["family"] == "layered"
    assert (good["walls"], good["doors"], good["rooms_named"]) == ("5", "2", "2")
    assert (good["rooms_with_area"], good["area_within_3"]) == ("2", "2")
    assert float(good["coverage"]) >= 0.95
    assert rows[str(tmp_path / "b.dxf")]["family"] == "colour_only"
    assert rows[str(tmp_path / "bad.dxf")]["error"].startswith("ValueError")
