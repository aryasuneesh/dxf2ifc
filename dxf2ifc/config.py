"""Every threshold and default lives here. Sections add their own constants."""

IFC_SCHEMA = "IFC4"  # the kickoff spec asks for IFC4
IFC_EXTRA_SCHEMAS = ("IFC2X3",)  # also written, as model_ifc2x3.ifc: SECO's reference model is IFC2X3

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
    ("hatch", rf"hach|hatch|schraff|{_B}patt"),  # AIA layer names: A-WALL-PATT
    ("wall", rf"{_B}murs?{_E}|cloison|voile|ma[cç]onnerie|porteu[rs]|w[aä]nd|mauer|{_B}walls?{_E}"),
    ("door", rf"{_B}portes?{_E}|ouvrant|t(ü|ue)r|door"),
    ("window", r"fen[eê]tre|ch[aâ]ssis|vitrage|fenster|window|glaz"),
    ("column", rf"^(?!.*(cl[oô]ture|fence|zaun)).*(poteau|pilier|st(ü|ue)tze|column|{_B}cols{_E})"),  # not fence posts
    ("stair", rf"escalier|treppe|stair|{_B}strs{_E}"),
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

# stairs: a stair-layer outline, or a run of parallel treads on any layer
STAIR_LAYER_REACH = 0.6  # m, stair-layer lines this close are one stair (flights, landing, railing)
STAIR_MIN_AREA = 3.0  # m2, smaller stair-layer groups are arrows, labels or loose pieces
TREAD_LEN_MIN, TREAD_LEN_MAX = 0.8, 2.5  # m, width of a flight
TREAD_GAP_MIN, TREAD_GAP_MAX = 0.20, 0.36  # m, going of one step
TREAD_LEN_TOL = 0.10  # treads of one flight differ in length by less than this share
TREAD_MIN_COUNT = 6  # fewer parallel treads are not a stair
FLIGHT_JOIN = 0.6  # m, flights and landings this close are one stair
FLIGHT_MIN_AREA = 1.5  # m2, a smaller run of treads is a pattern, not a stair
CONF_STAIR_LAYER, CONF_STAIR_TREADS = 0.6, 0.5  # outline found; steps and direction are assumed
STAIR_RISE = 0.175  # m, target riser height of the assumed steps
STAIR_U_WIDTH = 1.8  # m, an outline at least this wide holds two flights side by side (U stair, half landing)
STAIR_WAIST = 0.2  # m, concrete under each step
MAX_TREAD_CANDIDATES = 50_000  # above this the tread search is skipped

# columns: closed outlines, circles and hatches on column layers
COLUMN_REACH = 0.05  # m, pieces this close are one column (outline, hatch, the quarter arcs of a circle)
COLUMN_MIN, COLUMN_MAX = 0.1, 1.5  # m, side or diameter; outside this it is a grid line, a label or a wall
CONF_COLUMN = 0.7  # outline from a column layer; full storey height assumed

# fire-safety symbols (kickoff "welcome extra"): blocks or small clusters on fire layers
FIRE_LAYER = (r"incendi|fire|brandm|brandschutz|feuerl|l[öo]sch|extinct|extingu|(^|[^a-z])ria($|[^a-z])|baes|"
              r"pictogramme|rauchmelder|sprinkl")
FIRE_KINDS = [  # first match on layer + block name wins
    ("extinguisher", r"extinct|extingu|cylinder|l[öo]sch|(^|[^a-z])ria($|[^a-z])|hose|robinet|hydrant"),
    ("detector", r"d[ée]tect|melder|smoke|rauch|sprinkl"),
    ("exit_sign", r"exit|sortie|secours|baes|notausgang|pictogramme|assembly"),
]
FIRE_SYMBOL_MAX = 1.5  # m, a cluster larger than this is a pipe, a lane or a hatch, not a symbol
FIRE_CLUSTER_REACH = 0.15  # m, lines this close are one symbol
CONF_FIRE_BLOCK, CONF_FIRE_CLUSTER = 0.7, 0.5
FIRE_MOUNT_HEIGHT = {"extinguisher": 1.0, "detector": None, "exit_sign": 2.2, "fire_equipment": 1.0}  # None: ceiling

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

# --- IFC writer (section 06) ---
import uuid as _uuid

GUID_NAMESPACE = _uuid.UUID("5ec0b1a2-d2f1-4c0e-9a11-0d5f2a1c7e06")  # fixed, so reruns give the same GlobalIds
CONFIDENCE_COLOURS = {"high": (0.2, 0.7, 0.3), "medium": (1.0, 0.6, 0.1), "low": (0.85, 0.2, 0.2)}
SPACE_TRANSPARENCY = 0.7  # rooms are see-through
OPENING_OVERCUT = 0.10  # m, an opening is this much deeper than its wall so it cuts cleanly
LEAF_THICKNESS = 0.04  # m, door leaf
FRAME_WIDTH, FRAME_DEPTH = 0.05, 0.12  # m, door and window frames (seen face, depth across the wall)
WINDOW_SPLIT = 1.2  # m, a wider window gets a middle bar
GLASS_TRANSPARENCY = 0.6
CONF_ROOF = 0.3  # the roof is never drawn on a floor plan: always an assumption
MAX_SOURCE_HANDLES = 20  # DXF handles listed per element

# --- triage (section 02) ---
TRIAGE_WALL = r"(^|[^a-z])murs?($|[^a-z])|cloison|voile|ma[cç]onnerie|w[aä]nd|mauer|(^|[^a-z])walls?($|[^a-z])"
TRIAGE_OPENING = r"(^|[^a-z])portes?($|[^a-z])|t(ü|ue)r|door|fen[eê]tre|fenster|window"
TRIAGE_SURVEY = r"topo|points|altitude|repere|borne|parcelle|cadastr|vermessung"
TRIAGE_SECTION = r"coupe|schnitt|fa[cç]ade|pignon|terrassement"
TRIAGE_REF_CODES = ("24-010", "010B68")  # project code of the reference IFC
TRIAGE_PLAN_SCORE = 6  # files scoring this or more are counted as plan-like
TRIAGE_THUMB_MAX_MB = 60  # larger files get no thumbnail

# --- batch runs over many files ---
BATCH_MAX_MB = 25  # larger drawings are skipped in dataset-wide runs

# --- checks and review page (section 07) ---
R9_AREA_TOL = 0.05  # a room passes when its area is within this share of the printed area
R9_MIN_SHARE = 0.80  # share of labelled rooms that must pass
R10_MIN_COVERAGE = 0.85  # share of wall-line length that must end up in a wall
R12_MAX_SECONDS = 60  # one run on a laptop
REVIEW_PLAN_MARGIN = 2.0  # m of drawing shown around the building on the full plan
REVIEW_CROP_MARGIN = 2.0  # m shown around a flagged element
REVIEW_CROP_PX = 400  # size of a crop
REVIEW_MAX_CROPS = 40  # further items are listed without a picture
REVIEW_MAX_UNUSED_TEXT = 50  # unused texts listed on the page
MAX_WALL_CANDIDATES = 60_000  # more wall-layer lines than this is refused (hatching, detail sheets)
