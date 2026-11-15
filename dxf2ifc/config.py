"""Every threshold and default lives here. Sections add their own constants."""

IFC_SCHEMA = "IFC4"  # reference file is IFC2X3; ask SECO which they want

DEFAULT_STOREY_HEIGHT = 3.00  # m, flagged when used
DEFAULT_SLAB_THICKNESS = 0.25  # m, flagged when used
DEFAULT_DOOR_HEIGHT = 2.10  # m, flagged when used

CONF_HIGH = 0.8  # confidence >= this is "high"
CONF_MEDIUM = 0.5  # confidence >= this is "medium", below is "low"

# --- normalize (section 03) ---
MAX_DXF_MB = 200  # larger files are refused
MAX_FLAT_ENTITIES = 500_000  # stop flattening blocks after this many entities
MAX_BLOCK_DEPTH = 8  # nested INSERT levels followed
MIN_SEGMENT = 0.001  # m, shorter segments are dropped
EXTENT_TRIM_MIN_POINTS = 1000  # above this, the extent ignores the outer 1 % of points
INSUNITS = {1: ("in", 0.0254), 2: ("ft", 0.3048), 4: ("mm", 0.001), 5: ("cm", 0.01), 6: ("m", 1.0)}
GUESS_MM_ABOVE = 5000  # unitless file: larger side above this is taken as mm
GUESS_CM_ABOVE = 500  # ... above this as cm, otherwise m
LABEL_ROT_TOL = 2.0  # degrees, words on one line
LABEL_HEIGHT_TOL = 0.10  # share of text height
LABEL_LINE_TOL = 0.3  # baseline offset, share of text height
CHAR_W_MIN, CHAR_W_MAX = 0.6, 1.0  # character width range, share of text height
WORD_GAP = 1.2  # largest gap between words, share of text height

# --- roles (section 04) ---
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
MAPPINGS_DIR = _ROOT / "mappings"  # confirmed layer mappings, one JSON per layer set
CACHE_DIR = _ROOT / ".cache"

ROLES = ("wall", "door", "window", "column", "stair", "text", "dimension", "hatch",
         "furniture", "grid", "annotation", "other")
_B, _E = r"(^|[^a-z])", r"($|[^a-z])"  # not inside a longer word (MUR yes, MURET and ARMURE no)
# First match wins: "HATCH MUR" is hatching, "MUR PORTEUR" is a wall before PORTE makes it a door.
ROLE_PATTERNS = [
    ("hatch", r"hach|hatch|schraff"),
    ("wall", rf"{_B}murs?{_E}|cloison|voile|ma[cç]onnerie|porteu[rs]|w[aä]nd|mauer|{_B}walls?{_E}"),
    ("door", rf"{_B}portes?{_E}|ouvrant|t(ü|ue)r|door"),
    ("window", r"fen[eê]tre|ch[aâ]ssis|vitrage|fenster|window|glaz"),
    ("column", r"poteau|pilier|st(ü|ue)tze|column"),
    ("stair", r"escalier|treppe|stair"),
    ("dimension", rf"{_B}cot(e|es|ation|ations)?{_E}|{_B}dim|bema(ss|ß)|ma(ss|ß)(kette|linie)"),
    ("text", rf"text|annot|beschrift|label|{_B}txt"),
    ("furniture", r"mobilier|meuble|sanit|m(ö|oe)bel|furn"),
    ("grid", rf"{_B}axes?{_E}|trame|{_B}grid|raster|achse"),
]
CONF_RULE = 0.9  # role from a name rule or from entity types
CONF_LLM = 0.7  # role proposed by the LLM
MIN_WALL_SEG = 0.30  # m, shorter lines are never wall faces
MIN_WALL_WEIGHT_COUNT = 10  # colour-only fallback: a line weight needs this many lines to count

LLM_PROVIDERS = {"groq": "https://api.groq.com/openai/v1",
                 "openrouter": "https://openrouter.ai/api/v1",
                 "ollama": "http://localhost:11434/v1"}
LLM_TIMEOUT = 20  # s
LLM_RETRY_WAIT = 5  # s, after a rate-limit reply
LLM_MIN_SEGMENTS = 20  # smaller unknown layers are not worth a question
LLM_MAX_TEXT_SAMPLES = 40  # per request, over all layers
LLM_TEXT_CUT = 30  # characters kept of a text sample
LLM_TEXT_SKIP_OVER = 40  # longer texts are free-form notes: never sent
