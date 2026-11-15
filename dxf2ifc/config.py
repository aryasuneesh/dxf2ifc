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
