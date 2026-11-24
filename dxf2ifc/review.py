"""The review page: the plan coloured by confidence and the list of what an engineer must check.

One self-contained HTML file: images are embedded, there is no script and no outside link.
# ponytail: crops are static pictures. Upgrade if SECO wants it: link each item to its element
# in a 3D viewer through the IFC id shown next to it.
"""

import base64
import html
import json
import io
import math
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import quote

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

from . import config
from .schema import level

HEX = {name: "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb) for name, rgb in config.CONFIDENCE_COLOURS.items()}
NOT_MODELLED = ["Curved walls", "Beams", "Ramps", "Roofs", "Furniture not drawn as a named block",
                "Anything shown only on sections or elevations (true heights, level changes)"]


LOW_REASONS = {
    "single_line": "wall drawn as a single line; its thickness is a nominal value",
    "door_stub": "door with no wall beside it; a short thin wall was added to hold it",
    "enclosed_region": "closed space with no room label",
    "gap_only": "gap in a wall with no door tag or symbol",
    "flood_fill": "room area is far from the area printed on the plan",
}


def _strip(axis, thickness):
    (x1, y1), (x2, y2) = axis
    length = math.hypot(x2 - x1, y2 - y1) or 1.0
    nx, ny = -(y2 - y1) / length * thickness / 2, (x2 - x1) / length * thickness / 2
    return [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)]


def _boxes(plan) -> dict:
    """Bounding box (xmin, ymin, xmax, ymax) of every element that has a place on the plan."""
    boxes = {}
    walls = {w["id"]: w for w in plan["walls"]}
    for wall in plan["walls"]:
        xs, ys = zip(*_strip(wall["axis"], wall["thickness_m"]))
        boxes[wall["id"]] = (min(xs), min(ys), max(xs), max(ys))
    for opening in plan["openings"]:
        (x, y), half = opening["center"], opening["width_m"] / 2
        boxes[opening["id"]] = (x - half, y - half, x + half, y + half)
    for room in plan["rooms"]:
        xs, ys = zip(*room["polygon"])
        boxes[room["id"]] = (min(xs), min(ys), max(xs), max(ys))
    return boxes


def _extent(plan):
    points = [p for w in plan["walls"] for p in w["axis"]] + [p for r in plan["rooms"] for p in r["polygon"]]
    points = points or plan["slab"]["outline"]
    xs, ys = zip(*points)
    return (min(xs), min(ys), max(xs), max(ys))


def plan_image(plan, drawing=None, bbox=None, size=1500, highlight=None, _lines=None) -> bytes:
    """PNG of the plan. bbox crops to one place (a margin is added); highlight outlines one element."""
    x1, y1, x2, y2 = bbox or _extent(plan)
    pad = config.REVIEW_CROP_MARGIN if bbox else config.REVIEW_PLAN_MARGIN
    x1, y1, x2, y2 = x1 - pad, y1 - pad, x2 + pad, y2 + pad
    width, height = max(x2 - x1, 0.1), max(y2 - y1, 0.1)
    scale = size / 100 / max(width, height)
    fig, ax = plt.subplots(figsize=(max(width * scale, 2), max(height * scale, 2)))

    lines = _lines if _lines is not None else _line_array(drawing)
    if len(lines):
        mid_x, mid_y = (lines[:, 0] + lines[:, 2]) / 2, (lines[:, 1] + lines[:, 3]) / 2
        near = lines[(mid_x > x1 - 2) & (mid_x < x2 + 2) & (mid_y > y1 - 2) & (mid_y < y2 + 2)]
        ax.add_collection(LineCollection(near.reshape(-1, 2, 2), colors="#b8b8b8", linewidths=0.4 if bbox else 0.25))

    def seen(xs, ys):  # drawing what is outside the view is what makes crops slow
        return max(xs) >= x1 and min(xs) <= x2 and max(ys) >= y1 and min(ys) <= y2

    small = max(7, min(16, 240 / max(width, height))) if not bbox else 7  # readable on small plans, not crowded on big ones
    for room in plan["rooms"]:
        xs, ys = zip(*room["polygon"])
        if not seen(xs, ys):
            continue
        ax.fill(xs, ys, color=HEX[level(room["confidence"])], alpha=0.25, linewidth=0)
        if not room["name"].startswith("Unnamed") or bbox:
            ax.text(sum(xs) / len(xs), sum(ys) / len(ys), f"{room['name'][:28]}\n{room['area_m2']:.1f} m2",
                    fontsize=small, ha="center", va="center", clip_on=True)
    walls = {w["id"]: w for w in plan["walls"]}
    for wall in plan["walls"]:
        xs, ys = zip(*_strip(wall["axis"], max(wall["thickness_m"], 0.08)))
        if not seen(xs, ys):
            continue
        ax.fill(xs, ys, color=HEX[level(wall["confidence"])], linewidth=0)
    for opening in plan["openings"]:
        wall = walls[opening["wall_id"]]
        (ax1, ay1), (ax2, ay2) = wall["axis"]
        length = math.hypot(ax2 - ax1, ay2 - ay1) or 1.0
        ux, uy, half = (ax2 - ax1) / length, (ay2 - ay1) / length, opening["width_m"] / 2
        cx, cy = opening["center"]
        if not seen((cx - half, cx + half), (cy - half, cy + half)):
            continue
        span =[[cx - ux * half, cy - uy * half], [cx + ux * half, cy + uy * half]]
        xs, ys = zip(*_strip(span, wall["thickness_m"] + 0.12))
        ax.fill(xs, ys, facecolor="white", edgecolor="black", linewidth=1.0)
        if bbox and opening["tag"]:
            ax.text(cx, cy, opening["tag"], fontsize=7, ha="center", va="center", clip_on=True)
    if highlight:
        hx1, hy1, hx2, hy2 = highlight
        ax.plot([hx1, hx2, hx2, hx1, hx1], [hy1, hy1, hy2, hy2, hy1], color="#1144cc", linewidth=1.6, linestyle="--")

    ax.set_xlim(x1, x2)
    ax.set_ylim(y1, y2)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.subplots_adjust(0, 0, 1, 1)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def _line_array(drawing):
    if drawing is None or not drawing.segments:
        return np.empty((0, 4))
    return np.array([(s.x1, s.y1, s.x2, s.y2) for s in drawing.segments])


def _ifc_ids(ifc_path, source_file) -> dict:
    """Plan element id -> IFC GlobalId. A door wins over the opening it fills; a bare opening (a gap) keeps its own
    id, and the 3D view draws it as a see-through box."""
    if not ifc_path:
        return {}
    import ifcopenshell
    import ifcopenshell.util.element as element

    ids = {}
    model = ifcopenshell.open(str(ifc_path))  # keep it alive: its entities die with it
    for product in model.by_type("IfcProduct"):
        trust = element.get_pset(product, "SECO_Trust") or {}
        if trust.get("SourceFile") == source_file and trust.get("ElementId"):
            if trust["ElementId"] not in ids or not product.is_a("IfcOpeningElement"):
                ids[trust["ElementId"]] = product.GlobalId
    return ids


def review_list(plan) -> list[dict]:
    """Flags an engineer should look at, least sure first; low-confidence elements nobody flagged are added."""
    elements = {e["id"]: e for kind in ("walls", "openings", "rooms") for e in plan[kind]}
    review = [f for f in plan["flags"] if f["severity"] == "review"]
    flagged = {f["element_id"] for f in review}
    for element in elements.values():
        if element["confidence"] < config.CONF_MEDIUM and element["id"] not in flagged:
            review.append({"element_id": element["id"], "severity": "review",
                           "reason": LOW_REASONS.get(element["rule"], f"low confidence ({element['rule']})")})
    review.sort(key=lambda f: elements[f["element_id"]]["confidence"] if f["element_id"] in elements else -1.0)
    return review


# Drawing-wide flags that are about every element of a type: the 3D view asks about them on those elements.
TYPE_FLAGS = (("stairs are modelled", "IfcStair"), ("fire-safety symbols", "IfcBuildingElementProxy"),
              ("door height is a default", "IfcDoor"), ("columns are modelled", "IfcColumn"),
              ("furniture and fittings are placed", ("IfcFurnishingElement", "IfcFlowTerminal", "IfcTransportElement")), ("window sill", "IfcWindow"))


def viewer_notes(plan, ifc_path) -> tuple[dict, list]:
    """What to check, for the 3D view: {IFC GlobalId: [reasons]} per element, and things that are not in the model
    at all (a door tag with no opening, a room that did not close), grouped: "Room not closed (6): a; b; ..."."""
    import ifcopenshell

    ids, notes, missing = _ifc_ids(ifc_path, plan["source"]["file"]), {}, {}
    model = ifcopenshell.open(str(ifc_path))
    add = lambda gid, reason: notes.setdefault(gid, []).append(reason)
    for flag in review_list(plan):
        reason, eid = flag["reason"][:1].upper() + flag["reason"][1:], flag["element_id"]
        kind = next((t for words, t in TYPE_FLAGS if words in flag["reason"]), None)
        if eid in ("storey", "slab") or (eid == "file" and "drawn as a single line" in reason):
            continue  # storey and slab values: one question below; single-line walls are asked wall by wall
        if kind:
            for product in (p for k in (kind if isinstance(kind, tuple) else (kind,)) for p in model.by_type(k)):
                add(product.GlobalId, reason)
        elif eid in ids:
            add(ids[eid], reason)
        else:
            head, _, tail = reason.partition(": ")
            missing.setdefault(head, []).append(tail.replace(" ()", ""))
    slab = next((ids[s] for s in ("slab",) if s in ids), None) or next((p.GlobalId for p in model.by_type("IfcSlab")), None)
    if slab:
        add(slab, "Values assumed for the whole storey: " + "; ".join(
            f"{what} {value} ({why.rstrip('.')})" for what, value, why in _defaults(plan)[:3]))
    general = [f"{head} ({len(tails)}): " + "; ".join(t for t in tails[:8] if t) + ("; …" if len(tails) > 8 else "")
               if any(tails) else head for head, tails in missing.items()]
    return notes, general


def _img(png: bytes, alt: str) -> str:
    return f'<img alt="{html.escape(alt)}" src="data:image/png;base64,{base64.b64encode(png).decode("ascii")}">'


def _defaults(plan) -> list[tuple[str, str, str]]:
    storey, slab = plan["storey"], plan["slab"]
    reasons = [f["reason"] for f in plan["flags"]]
    rows = [("Storey height", f"{storey['height_m']:.2f} m",
             "Storey height was not found on the drawing. A default was used." if storey["height_source"] == "default"
             else "Given when the tool was run."),
            ("Storey elevation", f"{storey['elevation_m']:+.2f} m",
             next((r for r in reasons if r.startswith(("storey elevation", "no level text"))), "Given when the tool was run.")),
            ("Slab thickness", f"{slab['thickness_m']:.2f} m", "Not shown on a plan. A default was used.")]
    door_note = next((r for r in reasons if r.startswith("door height is a default")), None)
    if door_note:
        rows.append(("Door height", f"{config.DEFAULT_DOOR_HEIGHT:.2f} m",
                     "Not found next to the door. Default used for "
                     + (door_note.split(" for ", 1)[1] if " for " in door_note else "some doors") + "."))
    if any(r.startswith("window sill") for r in reasons):
        rows.append(("Window sill and height", f"{config.DEFAULT_WINDOW_SILL:.2f} m / {config.DEFAULT_WINDOW_HEIGHT:.2f} m",
                     "Not shown on a plan. Defaults were used."))
    return rows


FEEDBACK = r"""<h2 id='send'>Send your feedback</h2>
<p class='meta'>Everything you type or mark on this page is saved automatically in this browser, so you can close it
and come back later. When you are done, answer the questions below and press the button.</p>
<form id='feedback'>
<p><label>Your name or initials (optional)<br><input name='name' size='30' autocomplete='name'></label></p>
<p><label>Your role<br><select name='role'><option>Structural engineer</option>
<option>MEP engineer</option><option>Architect</option><option>BIM modeller</option><option>Project manager</option>
<option>Facility manager</option><option>Student or other</option></select></label></p>
<p><label>Compared with redrawing the plan by hand, how useful is this? (1 = not at all, 5 = very)<br>
<input type='number' name='useful' min='1' max='5' required></label></p>
<p><label>What would you not trust?<br><textarea name='distrust' rows='3'></textarea></label></p>
<p><label>What should change first?<br><textarea name='change' rows='3'></textarea></label></p>
<p><label>Any other remarks or comments<br><textarea name='remarks' rows='4'></textarea></label></p>
<p><button type='submit' class='primary' id='go'>Send feedback</button></p>
<p id='sent' role='status'></p>
<div id='copy' hidden><p><label>Or copy this text into your email<br><textarea id='answers' rows='6' readonly></textarea></label></p>
<p><button type='button' id='copybtn'>Copy text</button></p></div></form>
<div id='progress' role='status'><span id='count'></span> <span id='autosave'></span> <a href='#send'>Send feedback</a></div>
<script>
const FILE = __FILE__, LOCAL = location.protocol === 'file:';
const KEY = 'dxf2ifc:' + FILE + ':' + location.pathname, items = [...document.querySelectorAll('.item')];
const form = document.getElementById('feedback'), $ = id => document.getElementById(id);
if (LOCAL) $('go').textContent = 'Save feedback file';
const keyOf = item => item.dataset.id + ' | ' + item.dataset.reason;
let saved = {}; try { saved = JSON.parse(localStorage.getItem(KEY)) || {}; } catch {}
saved.__started ||= Date.now();
const store = () => { try { localStorage.setItem(KEY, JSON.stringify(saved));
  $('autosave').textContent = '· saved automatically ' + new Date().toTimeString().slice(0, 5); } catch {
  $('autosave').textContent = '· this browser cannot keep your answers: send them before closing'; } };
const count = () => { const n = items.filter(i => (saved[keyOf(i)] || {}).verdict).length;
  $('count').textContent = n + ' of ' + items.length + ' items marked'; };
const show = item => { const v = (saved[keyOf(item)] || {}).verdict;
  item.querySelectorAll('.verdict button').forEach(b => { b.classList.toggle('on', b.dataset.v === v); b.setAttribute('aria-pressed', b.dataset.v === v); }); };
for (const item of items) {
  const entry = () => saved[keyOf(item)] ||= {};
  item.querySelectorAll('.verdict button').forEach(button => button.onclick = () => {
    Object.assign(entry(), {verdict: button.dataset.v, seconds: Math.round((Date.now() - saved.__started) / 1000)}); store(); show(item); count(); });
  const note = item.querySelector('textarea');
  note.value = (saved[keyOf(item)] || {}).comment || '';
  note.oninput = () => { entry().comment = note.value; store(); };
  show(item);
}
for (const [name, value] of Object.entries(saved.__form || {})) if (form.elements[name]) form.elements[name].value = value;
form.oninput = event => { if (event.target.name) { saved.__form = Object.fromEntries(new FormData(form)); store(); } };
count();
const answers = () => JSON.stringify({file: FILE, ...Object.fromEntries(new FormData(form)),
  minutes_on_page: +((Date.now() - saved.__started) / 60000).toFixed(1),
  verdicts: Object.fromEntries(Object.entries(saved).filter(([k, v]) => !k.startsWith('__') && (v.verdict || (v.comment || '').trim()))),
  sent: new Date().toISOString()}, null, 1);
const download = body => {
  const name = 'feedback-' + FILE.replace(/\.dxf$/i, '').replace(/[^\w-]+/g, '_') + '-' + new Date().toISOString().slice(0, 16).replace(/\D/g, '') + '.json';
  const link = Object.assign(document.createElement('a'), {download: name, href: URL.createObjectURL(new Blob([body], {type: 'application/json'}))});
  document.body.appendChild(link); link.click(); link.remove();
  $('answers').value = body; $('copy').hidden = false;
  return name;
};
$('copybtn').onclick = async () => { $('answers').select();
  try { await navigator.clipboard.writeText($('answers').value); } catch { document.execCommand('copy'); }
  $('copybtn').textContent = 'Copied'; };
form.onsubmit = async event => {
  event.preventDefault();
  const body = answers();
  if (!LOCAL) try {
    const reply = await fetch('/feedback', {method: 'POST', body, headers: {'Content-Type': 'application/json'}});
    if (reply.ok) { $('sent').textContent = 'Saved on the server. Thank you.'; return; }
  } catch {}
  const name = download(body);
  $('sent').innerHTML = '<b>Your answers were saved as a file named ' + name + '</b> (usually in your Downloads folder). '
    + 'Please email that file to the person who sent you this plan. Your answers also stay on this page, so you can change them and save again.';
};
</script>"""

STYLE = """
:root{color-scheme:light} html{background:#fff;scroll-behavior:smooth}
body{font:17px/1.6 Segoe UI,Arial,sans-serif;color:#1c1c1c;background:#fff;max-width:1000px;margin:0 auto;padding:0 20px 80px}
h1{font-size:28px;margin:24px 0 4px;word-break:break-word} h2{font-size:22px;margin:40px 0 12px;border-bottom:2px solid #ddd;padding-bottom:4px}
nav{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:8px 0;z-index:2;font-size:15px}
nav a{margin-right:14px;white-space:nowrap}
a{color:#1d5fbf} :focus-visible{outline:3px solid #1d5fbf;outline-offset:2px}
.badge{display:inline-block;background:#c62828;color:#fff;font-weight:700;padding:3px 10px;border-radius:4px;letter-spacing:.5px}
.howto{background:#eef4fc;border-left:5px solid #1d5fbf;padding:10px 16px;margin:16px 0} .howto ol{margin:6px 0;padding-left:22px}
.warn{background:#fff3cd;border-left:5px solid #e0a800;padding:8px 12px;margin:8px 0}
table{border-collapse:collapse;width:100%;margin:6px 0;font-size:16px} th,td{border:1px solid #ccc;padding:6px 10px;text-align:left;vertical-align:top}
th{background:#f1f1f1} img{max-width:100%;height:auto;border:1px solid #ccc}
.dot{display:inline-block;width:14px;height:14px;border-radius:50%;margin:0 6px 0 14px;vertical-align:middle;border:1px solid #0003}
.item{display:flex;flex-wrap:wrap;gap:18px;border:1px solid #c9c9c9;border-radius:8px;padding:14px;margin:14px 0}
.item img{width:300px;flex:none} .item>div{flex:1;min-width:260px} .item p{margin:0 0 8px} .meta{color:#4d4d4d;font-size:15px}
.pass{color:#1b7a33;font-weight:700} .fail{color:#c62828;font-weight:700} .na{color:#666}
code{background:#f1f1f1;padding:1px 4px;border-radius:3px;font-size:14px;word-break:break-all}
button{font:inherit;font-size:16px;padding:5px 14px;border:1px solid #777;border-radius:5px;background:#fff;color:#1c1c1c;cursor:pointer}
.verdict button.on[data-v=ok]{background:#1b7a33;color:#fff;border-color:#1b7a33}
.verdict button.on[data-v=wrong]{background:#c62828;color:#fff;border-color:#c62828}
.verdict button.on[data-v=unsure]{background:#5f6368;color:#fff;border-color:#5f6368}
.item textarea{width:100%;box-sizing:border-box;font:inherit;font-size:15px}
#feedback textarea,#feedback input,#feedback select{font:inherit;width:100%;max-width:640px;box-sizing:border-box}
#feedback input[type=number]{width:90px} button.primary{background:#1d5fbf;color:#fff;border-color:#1d5fbf}
#progress{position:fixed;bottom:0;left:0;right:0;background:#1c2a44;color:#fff;padding:8px 16px;text-align:center;font-size:15px}
#progress a{color:#fff;font-weight:700;margin-left:12px}
"""


def write_review(plan, checks, out_path, drawing=None, ifc_path=None) -> Path:
    esc = html.escape
    source, storey = plan["source"], plan["storey"]
    elements = {e["id"]: e for kind in ("walls", "openings", "rooms") for e in plan[kind]}
    boxes, ids, lines = _boxes(plan), _ifc_ids(ifc_path, source["file"]), _line_array(drawing)
    levels = Counter(level(e["confidence"]) for e in elements.values())
    kinds = Counter(o["kind"] for o in plan["openings"])
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Review: {esc(source['file'])}</title>",
           "<meta name='viewport' content='width=device-width,initial-scale=1'>",
           f"<style>{STYLE}</style></head><body>",
           "<nav aria-label='Sections'><a href='#plan'>Plan</a><a href='#assumed'>Assumed values</a>"
           "<a href='#review'>To review</a><a href='#rooms'>Rooms</a><a href='#checks'>Requirements</a>"
           "<a href='viewer.html' target='viewer'>3D view</a><a href='#send'>Send feedback</a></nav>",
           f"<h1>{esc(source['file'])}</h1>",
           f"<p><span class='badge'>TO BE VERIFIED</span> &nbsp; Storey <b>{esc(storey['name'])}</b> &middot; "
           f"made on {date.today().isoformat()} &middot; this model was built by a program from the 2D drawing. "
           f"<a href='viewer.html' target='viewer'>Open the 3D view</a></p>",
           f"<p>{len(plan['walls'])} walls, {kinds['door']} doors, {kinds['window']} windows, "
           f"{kinds['opening']} other openings, {len(plan['rooms'])} rooms.</p><p>"]
    for name, label in (("high", "High confidence"), ("medium", "Medium"), ("low", "Low")):
        out.append(f"<span class='dot' style='background:{HEX[name]}'></span>{label}: <b>{levels[name]}</b>")
    out.append("</p>")
    out.append("<div class='howto'><b>How to give feedback (about 10 minutes)</b><ol>"
               "<li>Look at the plan: is anything missing or in the wrong place?</li>"
               "<li>Go through <a href='#review'>To review</a>. For each item press Correct, Wrong or Not sure, "
               "and add a comment if you want. \"show in 3D\" opens the element in the 3D view.</li>"
               "<li>At the bottom, answer four short questions and press <a href='#send'>Send feedback</a>.</li></ol>"
               "Your marks are kept in this browser until you send them.</div>")

    for flag in plan["flags"]:  # things about the whole file come first
        if flag["element_id"] == "file" and flag["severity"] == "review" and flag["reason"].startswith(
                ("layers carry no meaning", "units guessed", "no closed room", "no walls", "stopped after")):
            out.append(f"<div class='warn'>{esc(flag['reason'][:1].upper() + flag['reason'][1:])}.</div>")

    out += ["<h2 id='plan'>The plan</h2>", _img(plan_image(plan, _lines=lines), "plan coloured by confidence"),
            "<p class='meta'>Walls are filled in their confidence colour, rooms are tinted, openings are white boxes. "
            "Grey lines are the original drawing.</p>"]

    out += ["<h2 id='assumed'>Values that were assumed</h2>", "<table><tr><th>What</th><th>Value used</th><th>Where it comes from</th></tr>"]
    out += [f"<tr><td>{esc(what)}</td><td>{esc(value)}</td><td>{esc(why)}</td></tr>" for what, value, why in _defaults(plan)]
    out.append("</table>")

    review = review_list(plan)
    out.append(f"<h2 id='review'>To review ({len(review)})</h2>")
    if not review:
        out.append("<p>Nothing was flagged.</p>")
    crops = 0
    for flag in review:
        eid, picture = flag["element_id"], ""
        if eid in boxes and crops < config.REVIEW_MAX_CROPS:
            crops += 1
            picture = _img(plan_image(plan, bbox=boxes[eid], size=config.REVIEW_CROP_PX, highlight=boxes[eid], _lines=lines),
                           f"crop around {eid}")
        meta = []
        if eid in elements:
            element = elements[eid]
            meta.append(f"Element <code>{esc(eid)}</code>, confidence {element['confidence']:.2f} ({level(element['confidence'])})")
            if eid in ids:
                meta.append(f"IFC id <code>{esc(ids[eid])}</code> "
                            f"(<a href='viewer.html#{quote(ids[eid], safe='')}' target='viewer'>show in 3D</a>)")
            src = element.get("src") or {}
            if src.get("layer") or src.get("handles"):
                meta.append(f"Drawing layer {esc(src.get('layer') or '-')}, entities {esc(', '.join(src.get('handles', [])[:6]) or '-')}")
        else:
            meta.append({"file": "Whole drawing", "storey": "Storey", "slab": "Floor slab"}.get(eid, esc(eid)))
        reason = flag["reason"][:1].upper() + flag["reason"][1:]
        out.append(f'<div class="item" data-id="{esc(eid)}" data-reason="{esc(reason[:80])}">'
                   f"{picture}<div><p><b>{esc(reason)}.</b></p>"
                   f"<p class='meta'>{'<br>'.join(meta)}</p>"
                   f"<p class='verdict' role='group' aria-label='Your verdict on: {esc(reason[:80])}'>"
                   "<button type='button' data-v='ok' aria-pressed='false'>Correct</button> "
                   "<button type='button' data-v='wrong' aria-pressed='false'>Wrong</button> "
                   "<button type='button' data-v='unsure' aria-pressed='false'>Not sure</button></p>"
                   "<label class='meta'>Comment (optional)<textarea rows='2'></textarea></label></div></div>")

    out += ["<h2 id='rooms'>Rooms</h2>", "<table><tr><th>Room</th><th>Computed m2</th><th>Printed m2</th><th>Difference</th>"
                              "<th>Confidence</th></tr>"]
    for room in sorted(plan["rooms"], key=lambda r: r["confidence"]):
        printed = room["area_printed_m2"]
        diff = f"{(room['area_m2'] - printed) / printed * 100:+.1f} %" if printed else "-"
        out.append(f"<tr><td>{esc(room['name'])}</td><td>{room['area_m2']:.1f}</td>"
                   f"<td>{f'{printed:.1f}' if printed else '-'}</td><td>{diff}</td>"
                   f"<td><span class='dot' style='margin-left:0;background:{HEX[level(room['confidence'])]}'></span>"
                   f"{level(room['confidence'])}</td></tr>")
    out.append("</table>")

    out += ["<h2>Not in the model</h2>", "<p>Add these by hand if the control needs them.</p><ul>"]
    out += [f"<li>{esc(item)}</li>" for item in NOT_MODELLED] + ["</ul>"]

    unused = plan.get("unused_text", [])
    if unused:
        out += ["<h2>Text on the drawing that was not used</h2>",
                f"<p>{plan['stats']['labels_unused']} texts matched nothing. They may hold information the program missed. "
                f"The first {len(unused)}:</p>", "<p class='meta'>" + " &middot; ".join(esc(t) for t in unused) + "</p>"]

    if checks:
        out += ["<h2 id='checks'>Minimum requirements</h2>",
                "<table><tr><th></th><th>Requirement</th><th>Result</th><th>Needed</th><th></th></tr>"]
        for c in checks:
            mark = {True: "<span class='pass'>pass</span>", False: "<span class='fail'>fail</span>",
                    None: "<span class='na'>not applicable</span>"}[c["passed"]]
            note = f"<br><span class='meta'>{esc(c['note'])}</span>" if c["note"] else ""
            out.append(f"<tr><td>{c['id']}</td><td>{esc(c['name'])}{note}</td><td>{esc(str(c['value']))}</td>"
                       f"<td>{esc(str(c['threshold']))}</td><td>{mark}</td></tr>")
        out.append("</table>")

    info = [f for f in plan["flags"] if f["severity"] == "info"]
    if info:
        out.append(f"<details><summary>Other notes ({len(info)})</summary><ul>")
        out += [f"<li>{esc(f['reason'])}</li>" for f in info] + ["</ul></details>"]
    out.append(FEEDBACK.replace("__FILE__", json.dumps(source["file"]).replace("</", "<\\/")))
    out.append("</body></html>")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out), encoding="utf-8")
    return out_path
