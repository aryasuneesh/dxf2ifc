"""Decide what each layer holds: saved mapping, then rules, then evidence, then an optional LLM.

If no layer turns out to hold walls, the file is "colour_only" and walls are picked by line weight.
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from . import config, llm

TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB"}
SYSTEM_PROMPT = (
    "You classify CAD layers of a building floor plan. Reply with one JSON object that maps each "
    "layer name you are asked about to exactly one of: " + ", ".join(config.ROLES) + ". "
    "Layer names may be French, German or English. Use \"other\" when unsure.")


def rule_role(layer: str) -> str | None:
    """Role from the layer name alone, or None. First match in config.ROLE_PATTERNS wins."""
    for role, pattern in config.ROLE_PATTERNS:
        if re.search(pattern, layer, re.IGNORECASE):
            return role
    return None


def _evidence_role(types: dict) -> str | None:
    kinds = set(types)
    if not kinds:
        return None
    if kinds == {"DIMENSION"}:
        return "dimension"
    if kinds <= TEXT_TYPES:
        return "text"
    if kinds == {"HATCH"}:
        return "hatch"
    return None


def _segments(stats: dict) -> int:
    return sum(stats["lineweights"].values())


def _mapping_key(layer_names) -> str:
    return hashlib.sha1("\n".join(sorted(layer_names)).encode("utf-8")).hexdigest()


def build_prompt(drawing, unplaced: list[str], placed: dict) -> str:
    """Layer names, entity counts, masked text samples and block names. Never geometry.

    Digits in text samples are replaced by 0, so no dimension or area value leaves the machine
    while the shape of the text ("00.0 m0", "P.00.00") still tells the model what the layer is.
    """
    blocks = {}
    for ref in drawing.blockrefs:
        blocks.setdefault(ref.layer, Counter())[ref.name] += 1
    samples_left = config.LLM_MAX_TEXT_SAMPLES
    lines = ["Classify these layers:"]
    for name in unplaced:
        stats = drawing.layer_stats[name]
        texts = [re.sub(r"\d", "0", t[:config.LLM_TEXT_CUT]) for t in stats["texts"]
                 if len(t) <= config.LLM_TEXT_SKIP_OVER][:min(3, samples_left)]
        samples_left -= len(texts)
        counts = ", ".join(f"{kind} {n}" for kind, n in sorted(stats["types"].items()))
        names = ", ".join(block for block, _ in blocks.get(name, Counter()).most_common(5))
        lines.append(f"- {name} | entities: {counts} | texts: {texts} | blocks: {names or '-'}")
    if placed:
        lines.append("Already classified, for context: "
                     + ", ".join(f"{name}={role}" for name, role in sorted(placed.items())))
    return "\n".join(lines)


def _wall_filter(drawing, layers: dict, warnings: list) -> dict | None:
    """Colour-only file: the longest layer of unknown role, lines heavier than its most common weight."""
    candidates = {name: stats for name, stats in drawing.layer_stats.items()
                  if _segments(stats) and layers[name]["role"] == "other"}
    if not candidates:
        warnings.append("no line work found, so no walls can be extracted")
        return None
    name = max(candidates, key=lambda n: candidates[n]["length_m"])
    weights = candidates[name]["lineweights"]
    common = max(weights, key=weights.get)
    heavier = sorted(w for w, n in weights.items() if w > common and n >= config.MIN_WALL_WEIGHT_COUNT)
    if heavier:
        minimum = heavier[0]
    else:
        minimum = common
        warnings.append(f"layer {name} has one usable line weight; every line is a wall candidate")
    return {"layers": [name], "min_lineweight": minimum, "min_length_m": config.MIN_WALL_SEG}


def save_mapping(roles: dict, mappings_dir=None) -> Path:
    """Store roles a person has confirmed. The next file with the same layer set reuses them."""
    folder = Path(mappings_dir or config.MAPPINGS_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{roles['mapping_key']}.json"
    saved = {**roles, "layers": {name: {"role": entry["role"], "source": "mapping", "confidence": 1.0}
                                 for name, entry in roles["layers"].items()},
             "llm": {"provider": "none", "model": None, "cached": False}}
    path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def assign_roles(drawing, mappings_dir=None, cache_dir=None) -> dict:
    key = _mapping_key(drawing.layer_stats)
    saved = Path(mappings_dir or config.MAPPINGS_DIR) / f"{key}.json"
    if saved.is_file():
        return json.loads(saved.read_text(encoding="utf-8"))

    layers, warnings = {}, []
    for name, stats in drawing.layer_stats.items():
        role = rule_role(name)
        if role:
            layers[name] = {"role": role, "source": "rule", "confidence": config.CONF_RULE}
            continue
        role = _evidence_role(stats["types"])
        if role:
            layers[name] = {"role": role, "source": "evidence", "confidence": config.CONF_RULE}
        else:
            layers[name] = {"role": "other", "source": "none", "confidence": 0.0}

    used = {"provider": "none", "model": None, "cached": False}
    unplaced = [name for name, entry in layers.items() if entry["source"] == "none"
                and _segments(drawing.layer_stats[name]) >= config.LLM_MIN_SEGMENTS]
    if unplaced and llm.enabled():
        cfg = llm.settings()
        placed = {name: entry["role"] for name, entry in layers.items() if entry["source"] != "none"}
        answer, cached = llm.chat_json(SYSTEM_PROMPT, build_prompt(drawing, unplaced, placed), cache_dir)
        used = {"provider": cfg["provider"], "model": cfg["model"], "cached": cached}
        for name, role in (answer or {}).items():
            if name in unplaced and role in config.ROLES:
                layers[name] = {"role": role, "source": "llm", "confidence": config.CONF_LLM}

    has_walls = any(entry["role"] == "wall" for entry in layers.values())
    return {"family": "layered" if has_walls else "colour_only",
            "layers": layers,
            "wall_filter": None if has_walls else _wall_filter(drawing, layers, warnings),
            "llm": used, "warnings": warnings, "mapping_key": key}
