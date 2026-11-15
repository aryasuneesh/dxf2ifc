"""Every threshold and default lives here. Sections add their own constants."""

IFC_SCHEMA = "IFC4"  # reference file is IFC2X3; ask SECO which they want

DEFAULT_STOREY_HEIGHT = 3.00  # m, flagged when used
DEFAULT_SLAB_THICKNESS = 0.25  # m, flagged when used
DEFAULT_DOOR_HEIGHT = 2.10  # m, flagged when used

CONF_HIGH = 0.8  # confidence >= this is "high"
CONF_MEDIUM = 0.5  # confidence >= this is "medium", below is "low"
