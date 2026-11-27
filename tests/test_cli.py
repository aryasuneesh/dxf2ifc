import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import ifcopenshell
import pytest

from dxf2ifc import cli, llm
from tests.fixtures import make_house

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ("model.ifc", "plan.json", "review.html", "roles.json")


def test_tc40_run_writes_four_files(tmp_path, capsys):
    make_house(tmp_path / "house.dxf", noise=True)
    assert cli.main(["run", str(tmp_path / "house.dxf"), "--out", str(tmp_path / "out")]) == 0
    for name in OUTPUTS:
        assert (tmp_path / "out" / name).stat().st_size > 500
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "File: house.dxf (layered, m)"
    assert lines[1].startswith("Walls 5 | Openings 2 | Rooms 2 | High ")
    assert lines[2].startswith("Requirements: 12 passed, 0 failed, 0 n/a")
    assert lines[4].endswith("LLM calls: 0") and lines[5].endswith("review.html")
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert plan["stats"]["runtime_s"] > 0
    compare = (tmp_path / "out" / "compare.html").read_text(encoding="utf-8")
    assert 'src="review.html"' in compare and 'name="viewer"' in compare


def test_run_as_a_module(tmp_path):
    make_house(tmp_path / "house.dxf")
    result = subprocess.run([sys.executable, "-m", "dxf2ifc", "run", str(tmp_path / "house.dxf"),
                             "--out", str(tmp_path / "out"), "--elevation", "7.48", "--height", "3.13", "--project", "office"],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Walls 5" in result.stdout
    plan = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    assert plan["storey"]["elevation_m"] == 7.48 and plan["storey"]["height_m"] == 3.13
    assert plan["source"]["project"] == "office"


def test_tc41_works_with_no_network(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise urllib.error.URLError("network is down")

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(llm.urllib.request, "urlopen", no_network)
    monkeypatch.chdir(tmp_path)  # no .env here either
    make_house(tmp_path / "house.dxf", colour_only=True, units="mm")
    assert cli.main(["run", str(tmp_path / "house.dxf"), "--out", str(tmp_path / "out")]) == 0
    assert all((tmp_path / "out" / name).is_file() for name in OUTPUTS)


@pytest.mark.parametrize("make", ["text", "missing"])
def test_tc42_bad_input_gives_one_line_and_code_2(tmp_path, capsys, make):
    bad = tmp_path / "notes.dxf"
    if make == "text":
        bad.write_text("this is not a drawing", encoding="utf-8")
    assert cli.main(["run", str(bad), "--out", str(tmp_path / "out")]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error: notes.dxf") and captured.err.count("\n") == 1
    assert "Traceback" not in captured.err and captured.out == ""


def test_tc43_bench_prints_every_variant(capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert cli.main(["bench"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 + 7 and "Wall precision" in lines[0]
    assert all("100.0%" in line and "2 of 2" in line for line in lines[1:])


def test_merge_two_storeys(tmp_path, capsys):
    plans = []
    for name, elevation in (("lower", 3.2), ("upper", 6.5)):
        make_house(tmp_path / f"{name}.dxf")
        cli.main(["run", str(tmp_path / f"{name}.dxf"), "--out", str(tmp_path / name), "--elevation", str(elevation)])
        plans.append(str(tmp_path / name / "plan.json"))
    capsys.readouterr()
    assert cli.main(["merge", *reversed(plans), "--out", str(tmp_path / "building.ifc")]) == 0
    assert "2 storeys" in capsys.readouterr().out
    ifc = ifcopenshell.open(str(tmp_path / "building.ifc"))
    assert [round(s.Elevation, 2) for s in ifc.by_type("IfcBuildingStorey")] == [3.2, 6.5]
    assert len(ifc.by_type("IfcWall")) == 10
    heights = sorted(round(max(v[2] for v in _corners(wall)) - min(v[2] for v in _corners(wall)), 2)
                     for wall in ifc.by_type("IfcWall"))
    assert heights == [3.0] * 5 + [3.3] * 5  # the lower storey now reaches the upper one
    roof = ifc.by_type("IfcRoof")
    assert len(roof) == 1 and roof[0].ContainedInStructure[0].RelatingStructure.Elevation == 6.5
    assert ifcopenshell.open(str(tmp_path / "building_ifc2x3.ifc")).by_type("IfcRoof")
    assert (tmp_path / "building.html").is_file()  # the 3D view of the building


def _corners(wall):
    import ifcopenshell.geom
    import numpy as np
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    return np.array(ifcopenshell.geom.create_shape(settings, wall).geometry.verts).reshape(-1, 3)


def test_profile_and_mapping_commands(tmp_path, capsys, monkeypatch):
    make_house(tmp_path / "house.dxf")
    cli.main(["run", str(tmp_path / "house.dxf"), "--out", str(tmp_path / "out")])
    assert cli.main(["profile-ifc", str(tmp_path / "out" / "model.ifc"), "--out", str(tmp_path / "profile.json")]) == 0
    assert json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))["counts"]["IfcWall"] == 5
    monkeypatch.setattr(cli.config, "MAPPINGS_DIR", tmp_path / "mappings")
    assert cli.main(["save-mapping", str(tmp_path / "out" / "roles.json")]) == 0
    assert len(list((tmp_path / "mappings").glob("*.json"))) == 1


def test_try_it_page(tmp_path):
    make_house(tmp_path / "house.dxf")
    server = cli.make_server(tmp_path / "web", port=0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(base + "/", timeout=30).read().decode("utf-8")
        assert "Drop DXF floor plans here" in page
        request = urllib.request.Request(base + "/run?name=house.dxf&elevation=4,35&height=", method="POST",
                                         data=(tmp_path / "house.dxf").read_bytes())
        reply = json.loads(urllib.request.urlopen(request, timeout=120).read())
        assert reply["id"] == 1 and "Walls 5" in reply["summary"]
        review = urllib.request.urlopen(f"{base}/out/1/review.html", timeout=30).read().decode("utf-8")
        assert "TO BE VERIFIED" in review and "+4.35 m" in review
        assert urllib.request.urlopen(f"{base}/out/1/model.ifc", timeout=30).read().startswith(b"ISO-10303-21")
        cards = json.loads(urllib.request.urlopen(base + "/api/runs", timeout=30).read())
        assert [c["file"] for c in cards] == ["house.dxf"] and cards[0]["walls"] == 5 and cards[0]["thumb"]
        assert urllib.request.urlopen(f"{base}/out/1/plan.png", timeout=30).read().startswith(b"\x89PNG")

        for bad in ("/out/1/house.dxf", "/out/../cli.py", "/out/1/../../x", "/nothing"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + bad, timeout=30)
            assert error.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/run?name=x.dxf", method="POST", data=b"junk"), timeout=60)
        assert error.value.code == 422 and "not a DXF" in json.loads(error.value.read())["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_feedback_is_saved_and_summed_up(tmp_path, capsys):
    import threading
    import urllib.request
    server = cli.make_server(tmp_path, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/feedback"
    for role, useful, verdict in (("Structural engineer", "4", "ok"), ("Architect", "2", "wrong")):
        body = json.dumps({"role": role, "useful": useful, "distrust": "door heights", "change": "",
                           "minutes_on_page": 6.5, "verdicts": {"w1 | thin wall": {"verdict": verdict, "seconds": 40}}})
        urllib.request.urlopen(urllib.request.Request(url, data=body.encode(), method="POST"))
    bad = urllib.request.Request(url, data=b"not json", method="POST")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(bad)
    server.shutdown()
    assert cli.main(["feedback", str(tmp_path / "feedback.jsonl")]) == 0
    out = capsys.readouterr().out
    assert "Testers: 2" in out and "mean 3.0 of 5" in out and "wrong 1, correct 1" in out and "door heights" in out


def test_review_page_has_verdicts_and_form(tmp_path):
    make_house(tmp_path / "h.dxf")
    cli.main(["run", str(tmp_path / "h.dxf"), "--out", str(tmp_path / "o")])
    page = (tmp_path / "o" / "review.html").read_text(encoding="utf-8")
    assert "data-v='wrong'" in page and "id='feedback'" in page and "viewer.html" in page


def test_share_site_serves_plans_and_saves_feedback(tmp_path):
    import base64
    import io
    from dxf2ifc.share import make_app, pack
    make_house(tmp_path / "house.dxf")
    assert cli.main(["run", str(tmp_path / "house.dxf"), "--out", str(tmp_path / "plans" / "house")]) == 0
    app = make_app(tmp_path / "plans", password="pw")

    def call(path, method="GET", body=b"", password="pw"):
        seen = {}
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
               "HTTP_AUTHORIZATION": "Basic " + base64.b64encode(f"any:{password}".encode()).decode()}
        out = b"".join(app(env, lambda status, headers: seen.update(status=status)))
        return seen["status"], out

    assert call("/", password="nope")[0].startswith("401")
    status, page = call("/")
    assert status.startswith("200") and b"house/compare.html" in page
    assert call("/house/viewer.html")[0].startswith("200")
    assert call("/house/plan.json")[0].startswith("404") and call("/../house/review.html")[0].startswith("404")
    answer = {"file": "house.dxf", "remarks": "doors look fine",
              "verdicts": {"w1 | Single line": {"verdict": "unsure", "comment": "check thickness"}}}
    assert call("/feedback", "POST", json.dumps(answer).encode())[0].startswith("200")
    summary = cli.feedback_summary([str(tmp_path / "plans" / "feedback.jsonl")])
    assert "not sure 1" in summary and "doors look fine" in summary and "check thickness" in summary
    assert pack([tmp_path / "plans" / "house"], tmp_path / "site.zip") == ["house"]
    import zipfile
    names = zipfile.ZipFile(tmp_path / "site.zip").namelist()
    assert "site/share.py" in names and "site/plans/house/compare.html" in names and not any("plan.json" in n for n in names)
    assert pack([tmp_path / "plans" / "house"], tmp_path / "mail.zip", offline=True) == ["house"]
    names = zipfile.ZipFile(tmp_path / "mail.zip").namelist()
    top = "dxf2ifc prototype/"
    assert {top + n for n in ("START HERE.html", "start.bat", "plans/house/viewer.html", "plans/house/compare.html",
                              "app/dxf2ifc/cli.py", "app/requirements.txt")} <= set(names)
    assert not any("plan.json" in n or "__pycache__" in n or n.endswith((".dxf", ".env")) for n in names)
    start = zipfile.ZipFile(tmp_path / "mail.zip").read(top + "START HERE.html").decode("utf-8")
    assert "const STATIC = null;" not in start and '"id": "house"' in start
