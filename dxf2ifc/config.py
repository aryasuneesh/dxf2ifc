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

# --- extract (section 05) ---
ANGLE_TOL = 2.0  # degrees, lines this close in direction count as parallel
WALL_MIN_T, WALL_MAX_T = 0.05, 0.60  # m, distance between the two faces of a wall
UNPAIRED_MIN_LEN = 1.0  # m, wall lines this long with no partner are reported
MERGE_GAP = 0.02  # m, wall pieces closer than this are one wall
GAP_MIN, GAP_MAX = 0.60, 2.00  # m, a gap of this size in a wall is an opening
CORNER_REACH = 0.60  # m, a wall end this close to another wall is joined to it
CONF_COLOUR_ONLY_CAP = 0.6  # walls and openings guessed from line weight never score higher
CONF_DEFAULT_CAP = 0.5  # an element that uses a default value never scores higher
WALL_MATCH_TOL = 0.10  # m, tolerance when comparing wall axes with a known answer

DOOR_TAG_DIST = 1.5  # m, door tag to opening centre
DOOR_SYMBOL_DIST = 1.0  # m, door symbol to opening centre
DOOR_ARC_TOL = 0.15  # a swing arc's radius may differ from the gap width by this share
DOOR_HEIGHT_DIST = 2.0  # m, height note to opening centre
WINDOW_DIST = 0.5  # m, window-layer geometry to opening centre
DEFAULT_WINDOW_SILL, DEFAULT_WINDOW_HEIGHT = 0.90, 1.20  # m, flagged when used

ROOM_WORDS = (r"bureau|salle|wc|cuisine|chambre|couloir|hall|local|circulation|sanitaire|"
              r"nettoyage|archive|r[ée]union|stockage|d[ée]p[oô]t|s[ée]jour|"
              r"b[uü]ro|zimmer|k[uü]che|flur|lager|raum|office|room|kitchen|corridor|storage")
STOREY_WORDS = (r"niveau|(^|[^a-z])niv\.?\s?[+\-]?\d|[ée]tage|(^|[^a-z])rdc($|[^a-z])|rez[- ]de|sous-sol|"
                r"erdgeschoss|obergeschoss|untergeschoss|(^|[^a-z])level\s")
ROOM_NAME_DIST = 3.0  # a room name sits within this many text heights of its area
RASTER_PX = 0.02  # m per pixel when filling rooms
RASTER_MAX_SIDE = 6000  # px, larger plans use a coarser pixel
RASTER_MARGIN = 1.0  # m of empty space around the walls
SEED_SEARCH = 0.5  # m, how far to look for free space when a label sits on a wall
ROOM_MIN_AREA = 1.0  # m2, smaller unlabelled enclosed spaces are ignored
ROOM_SIMPLIFY = 0.03  # m, simplification of room outlines
SLAB_CLOSE = 0.3  # m, gaps closed when outlining the slab
EXTERNAL_DIST = 0.5  # m, a wall this close to the outline is external
SINGLE_LINE_T = 0.10  # m, nominal thickness of a wall drawn as one line
CONF_SINGLE_LINE = 0.3  # confidence of such a wall
CLUSTER_REACH = 0.5  # m, walls this close belong to one group
CLUSTER_PAD = 2.0  # m, groups within this distance of the main group's extent are kept
FRAME_SPAN = 0.9  # a wall group spanning this share of the drawing both ways is the sheet frame

# door swings (quarter circles)
SWING_R_MIN, SWING_R_MAX = 0.5, 1.4  # m, radius of a door swing = door leaf width
SWING_SWEEP = (40, 100)  # degrees; doors are drawn open at 45 or 90
SWING_MIN_EDGES = 4  # a polyline needs this many edges to be tested as an arc
SWING_FIT_TOL = 0.03  # m, how far polyline points may lie off the fitted circle
SWING_JOIN_TOL = 0.05  # m, arc pieces with centres and radii this close are one swing
SWING_GAP_SLACK = 0.2  # m, a closed door this close to a wall gap's span is that gap's door
SWING_PARALLEL_DEG = 15  # a door lies along its wall within this angle
SWING_WALL_DIST = 0.25  # m, door line to wall face
SWING_WALL_REACH = 0.4  # m, door may start this far beyond the end of its wall
SWING_LEAF_TOL = 0.12  # m, a drawn leaf runs from the hinge to one end of the swing
