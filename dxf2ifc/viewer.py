"""A 3D page for the browser: the IFC's geometry coloured by confidence, click an element to see its trust data.

The IFC is turned into triangles here, in Python, so the page needs no IFC parser: one HTML file with
three.js inlined (dxf2ifc/vendor, MIT), so it works offline and from a plain file. Every element keeps its IFC GlobalId, the stable id a portal can attach remarks to.
"""

import html
import json
import multiprocessing
import re
from functools import cache
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as element

from . import config

KINDS = {"IfcWall": "walls", "IfcWallStandardCase": "walls", "IfcDoor": "openings", "IfcWindow": "openings",
         "IfcSpace": "rooms", "IfcSlab": "slabs", "IfcRoof": "slabs", "IfcStair": "stairs",
         "IfcSensor": "extras", "IfcDistributionControlElement": "extras", "IfcFireSuppressionTerminal": "extras",
         "IfcAlarm": "extras", "IfcFlowTerminal": "extras", "IfcBuildingElementProxy": "fire safety"}


def elements(ifc_path) -> list[dict]:
    """One entry per element with geometry: ids, trust data, vertices (x, y, z rounded to mm) and faces."""
    model = ifcopenshell.open(str(ifc_path))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    iterator = ifcopenshell.geom.iterator(settings, model, multiprocessing.cpu_count())
    result = []
    if not iterator.initialize():
        return result
    while True:
        shape = iterator.get()
        product = model.by_guid(shape.guid)
        if product.is_a("IfcOpeningElement"):  # the void cut in a wall, not something to show
            if not iterator.next():
                break
            continue
        trust = element.get_pset(product, "SECO_Trust") or {}
        container = element.get_container(product)
        result.append({
            "id": shape.guid, "type": product.is_a(), "kind": KINDS.get(product.is_a(), "other"),
            "name": product.Name or "", "long_name": getattr(product, "LongName", None) or "", "storey": container.Name if container else "",
            "level": str(trust.get("ConfidenceLevel", "")).lower(), "confidence": trust.get("Confidence"),
            "status": trust.get("Status", ""), "rule": trust.get("Rule", ""),
            "source": f"{trust.get('SourceFile', '')} / {trust.get('SourceLayer', '')}".strip(" /"),
            "printed_area": trust.get("PrintedArea"),
            "v": [round(x, 3) for x in shape.geometry.verts], "f": list(shape.geometry.faces)})
        if not iterator.next():
            break
    return result


VENDOR = Path(__file__).parent / "vendor"


@cache
def three_bundle() -> str:
    """three.js and OrbitControls as one inline script: each module wrapped in a function, exports returned.
    ponytail: regex on the two pinned files (three 0.160.0); re-check the asserts when upgrading three."""
    core = (VENDOR / "three.module.min.js").read_text(encoding="utf-8")
    start = core.rindex("export{")
    names = ",".join(f"{b}:{a}" for a, b in re.findall(r"([\w$]+) as ([\w$]+)", core[start:]))
    orbit = (VENDOR / "OrbitControls.js").read_text(encoding="utf-8")
    imports = re.match(r"\s*import\s*\{([^}]*)\}\s*from\s*'three';", orbit)
    assert names and imports and "export { OrbitControls };" in orbit and "</script" not in core + orbit
    orbit = orbit[imports.end():].replace("export { OrbitControls };", "")
    return (f"const THREE=(()=>{{{core[:start]}\nreturn {{{names}}};}})();\n"
            f"const {{OrbitControls}}=(()=>{{const {{{imports.group(1)}}}=THREE;\n{orbit}\nreturn {{OrbitControls}};}})();")


def write_viewer(ifc_path, out_path, title=None) -> int:
    """Write the page next to the IFC. Returns the number of elements shown."""
    items = elements(ifc_path)
    colours = {name: "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb) for name, rgb in config.CONFIDENCE_COLOURS.items()}
    page = (PAGE.replace("__TITLE__", html.escape(title or Path(ifc_path).stem))
            .replace("__COLOURS__", json.dumps(colours))
            .replace("__DATA__", json.dumps(items, separators=(",", ":")).replace("</", "<\\/"))
            .replace("__THREE__", three_bundle()))
    Path(out_path).write_text(page, encoding="utf-8")
    return len(items)


def write_compare(out_path, title) -> None:
    """Review page (the plan) and 3D view side by side. "show in 3D" links in the left pane target the right pane
    by its name; browsers allow that when the pages are served (python -m dxf2ifc serve, or any local server)."""
    Path(out_path).write_text(COMPARE.replace("__TITLE__", html.escape(title)), encoding="utf-8")


COMPARE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>__TITLE__: plan and 3D</title>
<style>html,body{margin:0;height:100%;background:#fff;color:#1c1c1c;font:14px Segoe UI,Arial,sans-serif}
body{display:flex;flex-direction:column} header{padding:6px 12px;border-bottom:1px solid #ddd}
main{flex:1;display:flex;min-height:0} iframe{flex:1;border:0;min-width:0} iframe+iframe{border-left:2px solid #ccc}</style>
</head><body><header><b>__TITLE__</b>: the plan and what to check on the left, the model on the right.
Click "show in 3D" next to an item to see it on the right.</header>
<main><iframe src="review.html" title="Plan and review list"></iframe>
<iframe src="viewer.html" name="viewer" title="3D model"></iframe></main></body></html>"""


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__ 3D</title>
<style>
:root{color-scheme:light}
html,body{margin:0;height:100%;font:15px/1.5 Segoe UI,Arial,sans-serif;background:#f4f5f7;color:#1c1c1c}
#view{position:fixed;inset:0} #view canvas{display:block}
.panel{position:fixed;background:#fff;border:1px solid #c9ccd1;border-radius:8px;padding:12px 14px;box-shadow:0 2px 8px #0002}
#tools{top:12px;left:12px;width:270px;max-height:calc(100vh - 24px);overflow:auto}
#info{top:12px;right:12px;width:320px;max-height:70vh;overflow:auto}
h1{font-size:16px;margin:0 0 6px;word-break:break-word} h2{font-size:14px;margin:12px 0 4px;color:#444}
label{display:block;cursor:pointer;padding:2px 0}
.sw{display:inline-block;width:13px;height:13px;border-radius:3px;margin-right:7px;vertical-align:-2px;border:1px solid #0003}
.badge{display:inline-block;background:#b3261e;color:#fff;border-radius:3px;padding:1px 6px;font-size:12px;font-weight:600}
.row{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0}
button{font:inherit;padding:4px 10px;border:1px solid #8a8f98;border-radius:5px;background:#fff;color:#1c1c1c;cursor:pointer}
button:hover{background:#eef2f8}
:focus-visible{outline:3px solid #1d5fbf;outline-offset:2px}
table{border-collapse:collapse;width:100%} td{padding:3px 4px;border-bottom:1px solid #e3e3e3;vertical-align:top}
td:first-child{color:#555;white-space:nowrap;padding-right:10px} code{font-size:13px;word-break:break-all}
.hint{color:#555;font-size:13px;margin-top:8px}
@media (max-width:700px){#tools{width:auto;right:12px;max-height:45vh} #info{top:auto;bottom:12px;left:12px;right:12px;width:auto;max-height:35vh}}
</style>
</head><body>
<div id="view" role="img" aria-label="3D model of __TITLE__. Use the buttons in the panel to move the view and step through elements."></div>
<div id="tools" class="panel" role="region" aria-label="View controls"><h1>__TITLE__</h1><span class="badge">TO BE VERIFIED</span>
<h2>Colours</h2><div id="legend"></div>
<h2>Show</h2><div id="layers"></div>
<label><input type="checkbox" id="sensors"> simulated room sensors</label>
<h2>View</h2>
<div class="row"><button id="fit" title="Show the whole model (key R)">Whole model</button>
<button id="zin" title="Zoom in (key +)">Zoom in</button><button id="zout" title="Zoom out (key -)">Zoom out</button></div>
<h2>Step through</h2>
<div class="row"><button id="prev">Previous</button><button id="next">Next</button>
<select id="filter" aria-label="Which elements to step through"><option value="low">low confidence</option>
<option value="medium">medium</option><option value="all">all</option></select></div>
<div class="hint">Mouse: drag to turn, scroll to zoom, right-drag to pan, click an element.
Keyboard: arrow keys pan, + and - zoom, R shows the whole model, N and P step through elements.</div></div>
<div id="info" class="panel" role="region" aria-label="Selected element" aria-live="polite">Click an element, or use Next, to see where it came from and how sure the tool is.</div>
<script>__THREE__</script>
<script>
const DATA = __DATA__, COLOURS = __COLOURS__;
const scene = new THREE.Scene(); scene.background = new THREE.Color(0xf4f5f7);
const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 5000);
camera.up.set(0, 0, 1);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(devicePixelRatio); renderer.setSize(innerWidth, innerHeight);
document.getElementById("view").appendChild(renderer.domElement);
scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 2.2));
const sun = new THREE.DirectionalLight(0xffffff, 1.2); sun.position.set(1, -2, 3); scene.add(sun);
const groups = {}, meshes = [], box = new THREE.Box3();
for (const item of DATA) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(item.v, 3)); g.setIndex(item.f); g.computeVertexNormals();
  const room = item.kind === "rooms";
  const m = new THREE.MeshLambertMaterial({ color: COLOURS[item.level] || "#9aa0a6", transparent: room,
    opacity: room ? 0.35 : 1, depthWrite: !room, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(g, m); mesh.userData = item; meshes.push(mesh);
  (groups[item.kind] ||= new THREE.Group()).add(mesh); box.expandByObject(mesh);
}
for (const [kind, group] of Object.entries(groups)) {
  scene.add(group);
  const label = document.createElement("label");
  label.innerHTML = `<input type="checkbox" checked> ${kind} (${group.children.length})`;
  label.firstChild.onchange = e => { group.visible = e.target.checked; };
  document.getElementById("layers").appendChild(label);
}
document.getElementById("legend").innerHTML = Object.entries(COLOURS)
  .map(([k, c]) => `<span class="sw" style="background:${c}"></span>${k} confidence`).join("<br>");
const centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).length() || 10;
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(centre); camera.position.copy(centre).add(new THREE.Vector3(size * 0.25, -size * 0.35, size * 0.3));
controls.update();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// Simulated IoT: one temperature and CO2 sensor per room, bound by the room's IFC GlobalId.
// A portal would read real values keyed by the same id; nothing here is measured.
const hash = s => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const reading = id => { const h = hash(id), t = Date.now() / 1000;
  return { temp: 19 + (h % 50) / 10 + 1.2 * Math.sin(t / 20 + h), co2: 450 + (h % 400) + 80 * Math.sin(t / 30 + h / 7) }; };
const rooms = meshes.filter(m => m.userData.kind === "rooms");
let sensorsOn = false;
const showSensors = () => { for (const m of rooms) {
  if (!sensorsOn) { m.material.color.set(COLOURS[m.userData.level] || "#9aa0a6"); continue; }
  const k = Math.min(1, Math.max(0, (reading(m.userData.id).temp - 18) / 8));
  m.material.color.setRGB(k, 0.35, 1 - k); } };
document.getElementById("sensors").onchange = e => { sensorsOn = e.target.checked; showSensors();
  if (sensorsOn && groups.rooms) { groups.rooms.visible = true; } if (picked) describe(picked); };
setInterval(() => { if (sensorsOn) { showSensors(); if (picked && picked.userData.kind === "rooms") describe(picked); } }, 2000);
let picked = null;
const ray = new THREE.Raycaster(), mouse = new THREE.Vector2(); let down = null;
renderer.domElement.addEventListener("pointerdown", e => { down = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener("pointerup", e => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
  mouse.set(e.clientX / innerWidth * 2 - 1, -(e.clientY / innerHeight) * 2 + 1); ray.setFromCamera(mouse, camera);
  const hit = ray.intersectObjects(meshes.filter(m => m.parent.visible)).find(h => h.object.userData.kind !== "rooms")
    || ray.intersectObjects(meshes.filter(m => m.parent.visible))[0];
  if (picked) picked.material.emissive.setHex(0); picked = hit ? hit.object : null;
  if (!picked) return;
  picked.material.emissive.setHex(0x333333);
  describe(picked);
});
function describe(mesh) {
  const d = mesh.userData, rows = [["Type", d.type], ["Name", d.long_name ? `${d.long_name} (${d.name})` : d.name], ["Storey", d.storey],
    ["Confidence", d.confidence == null ? "" : `${d.confidence} (${d.level})`], ["Status", d.status], ["Rule", d.rule],
    ["Source", d.source], ["Printed area", d.printed_area ? d.printed_area + " m2" : ""], ["IFC GlobalId", `<code>${esc(d.id)}</code>`]];
  if (sensorsOn && d.kind === "rooms") { const r = reading(d.id);
    rows.push(["Temperature", `${r.temp.toFixed(1)} °C (simulated)`], ["CO2", `${Math.round(r.co2)} ppm (simulated)`]); }
  document.getElementById("info").innerHTML = "<table>" + rows.filter(r => r[1] !== "" && r[1] != null)
    .map(([k, v]) => `<tr><td>${k}</td><td>${k === "IFC GlobalId" ? v : esc(v)}</td></tr>`).join("") + "</table>";
}
// viewer.html#<GlobalId> selects that element: links from the review page or a portal land on it
function select(id) {
  const mesh = meshes.find(m => m.userData.id === id); if (!mesh) return false;
  if (picked) picked.material.emissive.setHex(0);
  picked = mesh; mesh.parent.visible = true; mesh.material.emissive.setHex(0x333333); describe(mesh);
  const b = new THREE.Box3().setFromObject(mesh), c = b.getCenter(new THREE.Vector3()), r = Math.max(b.getSize(new THREE.Vector3()).length(), 4);
  controls.target.copy(c); camera.position.copy(c).add(new THREE.Vector3(r, -r * 1.4, r * 1.2)); controls.update(); return true;
}
// Buttons and keys do everything the mouse does, so the page works without a mouse.
const home = camera.position.clone();
const fit = () => { controls.target.copy(centre); camera.position.copy(home); controls.update(); };
const zoom = f => { camera.position.sub(controls.target).multiplyScalar(f).add(controls.target); controls.update(); };
let step = -1;
const stepThrough = dir => {
  const want = document.getElementById("filter").value;
  const list = meshes.filter(m => want === "all" || m.userData.level === want)
    .sort((a, b) => (a.userData.confidence ?? 1) - (b.userData.confidence ?? 1));
  if (!list.length) { document.getElementById("info").textContent = `No ${want} confidence elements.`; return; }
  step = (step + dir + list.length) % list.length; select(list[step].userData.id);
  document.getElementById("info").insertAdjacentHTML("afterbegin", `<p><b>${step + 1} of ${list.length}</b> ${want === "all" ? "" : want + " confidence"}</p>`);
};
document.getElementById("fit").onclick = fit;
document.getElementById("zin").onclick = () => zoom(0.8);
document.getElementById("zout").onclick = () => zoom(1.25);
document.getElementById("next").onclick = () => stepThrough(1);
document.getElementById("prev").onclick = () => stepThrough(-1);
document.getElementById("filter").onchange = () => { step = -1; };
controls.listenToKeyEvents(window);
addEventListener("keydown", e => {
  if (e.target.closest("input,select,textarea")) return;
  const act = {"+": () => zoom(0.8), "=": () => zoom(0.8), "-": () => zoom(1.25), r: fit, R: fit,
               n: () => stepThrough(1), N: () => stepThrough(1), p: () => stepThrough(-1), P: () => stepThrough(-1)}[e.key];
  if (act) { act(); e.preventDefault(); }
});
window.viewer = { select, meshes };
const fromHash = () => location.hash.length > 1 && select(decodeURIComponent(location.hash.slice(1)));
addEventListener("hashchange", fromHash); fromHash();
addEventListener("resize", () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });
renderer.setAnimationLoop(() => renderer.render(scene, camera));
</script></body></html>
"""
