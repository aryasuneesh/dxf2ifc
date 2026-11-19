import json
import re
import urllib.error
from types import SimpleNamespace

import ezdxf
import pytest

from dxf2ifc import llm, roles
from dxf2ifc.normalize import load
from tests.fixtures import make_house


def fake_drawing(**layers):
    """A drawing with only what assign_roles reads. Value = number of LINE segments."""
    stats = {name: {"n": n, "types": {"LINE": n}, "length_m": float(n), "lineweights": {-1: n}, "texts": []}
             for name, n in layers.items()}
    return SimpleNamespace(layer_stats=stats, blockrefs=[], path="plan.dxf")


class FakeLLM:
    """Stands in for urlopen. Counts calls, keeps request bodies, raises `errors` first."""

    def __init__(self, content=None, errors=()):
        self.content, self.errors, self.calls, self.bodies = content, list(errors), 0, []

    @property
    def body(self):
        return self.bodies[-1]

    def __call__(self, request, timeout=None):
        self.calls += 1
        self.bodies.append(json.loads(request.data))
        if self.errors:
            raise self.errors.pop(0)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": self.content}}]}).encode()


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "error", {}, None)


@pytest.fixture
def use_llm(monkeypatch):
    def install(content=None, error=None, errors=()):
        fake = FakeLLM(content, [error] if error else errors)
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("LLM_MODEL", "test-model")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
        monkeypatch.setattr(llm.config, "LLM_RETRY_WAIT", 0)
        return fake
    return install


def assign(drawing, tmp_path):
    return roles.assign_roles(drawing, mappings_dir=tmp_path / "mappings", cache_dir=tmp_path / "cache")


@pytest.mark.parametrize("name, role", [
    ("MUR", "wall"), ("WAND_TRAGEND", "wall"), ("A-WALL", "wall"), ("AUSSENWAND", "wall"),
    ("MUR PORTEUR", "wall"), ("CLOISONS", "wall"),
    ("PORTE", "door"), ("PORTES INT", "door"), ("TUEREN", "door"),
    ("COTATION", "dimension"), ("BEST_COTATION", "dimension"),
    ("FENETRES", "window"), ("POTEAU", "column"), ("ESCALIER", "stair"),
    ("A-TEXTE", "text"), ("HACHURE", "hatch"), ("MOBILIER", "furniture"), ("AXES", "grid"),
    ("HATCH MUR", "hatch"), ("MASSLINIE 1_50", "dimension"), ("SANITÄR", "furniture"),
    ("A-WALL-____-PATT", "hatch"), ("A-WALL-____-MCUT", "wall"), ("S-COLS-____-MCUT", "column"),
    ("MURET", None), ("ARMURE", None), ("A-CONTOUR", None), ("STRUKTUR", None), ("0", None),
])
def test_tc14_rules(name, role):
    assert roles.rule_role(name) == role


def test_layered_house(tmp_path):
    make_house(tmp_path / "h.dxf", noise=True)
    result = assign(load(tmp_path / "h.dxf"), tmp_path)
    assert result["family"] == "layered" and result["wall_filter"] is None
    got = {name: entry["role"] for name, entry in result["layers"].items()}
    assert got == {"MUR": "wall", "PORTE": "door", "TEXTE": "text",
                   "COTATION": "dimension", "MOBILIER": "furniture"}
    assert result["layers"]["MUR"] == {"role": "wall", "source": "rule", "confidence": 0.9}
    assert result["llm"] == {"provider": "none", "model": None, "cached": False}


def test_tc15_colour_only_falls_back_to_line_weight(tmp_path):
    make_house(tmp_path / "h.dxf", colour_only=True)
    result = assign(load(tmp_path / "h.dxf"), tmp_path)
    assert result["family"] == "colour_only"
    assert result["wall_filter"] == {"layers": ["A-CONTOUR"], "min_lineweight": 9, "min_length_m": 0.3}
    assert result["layers"]["A-TEXTE"]["role"] == "text"
    assert result["layers"]["A-CONTOUR"]["role"] == "other"


def test_evidence(tmp_path):
    drawing = fake_drawing(Z1=5)
    drawing.layer_stats["Z1"]["types"] = {"DIMENSION": 5}
    drawing.layer_stats["Z2"] = {"n": 3, "types": {"MTEXT": 2, "TEXT": 1}, "length_m": 0, "lineweights": {}, "texts": []}
    result = assign(drawing, tmp_path)
    assert result["layers"]["Z1"] == {"role": "dimension", "source": "evidence", "confidence": 0.9}
    assert result["layers"]["Z2"]["role"] == "text"


def test_tc16_llm_places_unknown_layer(tmp_path, use_llm):
    fake = use_llm(content='{"BEST_XYZ": "furniture", "NOT_A_LAYER": "wall", "SMALL": "wall"}')
    result = assign(fake_drawing(MUR=40, BEST_XYZ=30, SMALL=3), tmp_path)
    assert fake.calls == 1
    assert result["layers"]["BEST_XYZ"] == {"role": "furniture", "source": "llm", "confidence": 0.7}
    assert result["layers"]["SMALL"]["role"] == "other"  # too small to be asked about
    assert "NOT_A_LAYER" not in result["layers"]
    assert result["llm"] == {"provider": "groq", "model": "test-model", "cached": False}
    assert fake.body["model"] == "test-model" and fake.body["temperature"] == 0


def test_llm_wall_makes_file_layered(tmp_path, use_llm):
    use_llm(content='```json\n{"GO_STRUCT": "wall"}\n```')
    result = assign(fake_drawing(GO_STRUCT=200), tmp_path)
    assert result["family"] == "layered" and result["layers"]["GO_STRUCT"]["source"] == "llm"


@pytest.mark.parametrize("content, error", [
    ("sorry, I cannot do that", None),
    ('{"BEST_XYZ": "spaceship"}', None),
    ('["wall"]', None),
    (None, TimeoutError("timed out")),
    (None, OSError("no network")),
])
def test_tc17_bad_llm_is_ignored(tmp_path, use_llm, content, error):
    use_llm(content=content, error=error)
    result = assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    assert result["layers"]["BEST_XYZ"] == {"role": "other", "source": "none", "confidence": 0.0}
    assert result["layers"]["MUR"]["role"] == "wall"


def test_rate_limit_and_missing_json_mode_are_retried(tmp_path, use_llm):
    fake = use_llm(content='{"BEST_XYZ": "furniture"}', errors=[http_error(429), http_error(400)])
    result = assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    assert fake.calls == 3
    assert "response_format" in fake.bodies[0] and "response_format" not in fake.bodies[2]
    assert result["layers"]["BEST_XYZ"]["role"] == "furniture"


def test_server_error_gives_up_at_once(tmp_path, use_llm):
    fake = use_llm(content="{}", errors=[http_error(500)])
    result = assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    assert fake.calls == 1 and result["layers"]["BEST_XYZ"]["role"] == "other"


def test_tc18_second_call_uses_cache(tmp_path, use_llm):
    fake = use_llm(content='{"BEST_XYZ": "furniture"}')
    assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    second = assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    assert fake.calls == 1
    assert second["llm"]["cached"] is True
    assert second["layers"]["BEST_XYZ"]["role"] == "furniture"


def test_no_llm_call_when_disabled_or_nothing_to_ask(tmp_path, use_llm, monkeypatch):
    fake = use_llm(content="{}")
    assign(fake_drawing(MUR=40, PORTE=30), tmp_path)  # everything placed by rules
    assert fake.calls == 0
    monkeypatch.setenv("LLM_PROVIDER", "none")
    assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    assert fake.calls == 0


def test_tc19_prompt_has_no_coordinates(tmp_path, use_llm):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    for i in range(25):
        msp.add_line((1234.567 + i, 987.654), (1239.321 + i, 993.876), dxfattribs={"layer": "ZZ_STUFF"})
    msp.add_text("48.73 m2", dxfattribs={"layer": "ZZ_STUFF", "height": 0.25, "insert": (1236.5, 990.1)})
    msp.add_text("a very long note that goes on and on about the building",
                 dxfattribs={"layer": "ZZ_STUFF", "height": 0.25, "insert": (1236.5, 991.1)})
    doc.saveas(tmp_path / "secret_project_name.dxf")
    fake = use_llm(content='{"ZZ_STUFF": "furniture"}')
    assign(load(tmp_path / "secret_project_name.dxf"), tmp_path)

    prompt = " ".join(message["content"] for message in fake.body["messages"])
    assert "ZZ_STUFF" in prompt
    assert "00.00 m0" in prompt  # digits in text samples are masked
    for leak in ("1234", "987", "4.754", "6.222", "48.73", "secret_project_name", "goes on and on"):
        assert leak not in prompt
    assert not re.search(r"[1-9]\d*\.\d", prompt)  # no decimal number survives


def test_saved_mapping_is_reused(tmp_path, use_llm):
    fake = use_llm(content='{"BEST_XYZ": "furniture"}')
    first = assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    first["layers"]["BEST_XYZ"]["role"] = "column"  # a person corrects it
    roles.save_mapping(first, mappings_dir=tmp_path / "mappings")
    again = assign(fake_drawing(MUR=40, BEST_XYZ=30), tmp_path)
    assert fake.calls == 1
    assert again["layers"]["BEST_XYZ"] == {"role": "column", "source": "mapping", "confidence": 1.0}
    assert again["family"] == "layered"
