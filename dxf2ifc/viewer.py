"""A 3D page for the browser: the IFC's geometry coloured by confidence, click an element to see its trust data.

The IFC is turned into triangles here, in Python, so the page needs no IFC parser: one HTML file with
three.js from a CDN. Every element keeps its IFC GlobalId, the stable id a portal can attach remarks to.
"""

import html
import json
import multiprocessing
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


def write_viewer(ifc_path, out_path, title=None) -> int:
    """Write the page next to the IFC. Returns the number of elements shown."""
    items = elements(ifc_path)
    colours = {name: "#%02x%02x%02x" % tuple(round(c * 255) for c in rgb) for name, rgb in config.CONFIDENCE_COLOURS.items()}
    page = (PAGE.replace("__TITLE__", html.escape(title or Path(ifc_path).stem))
            .replace("__COLOURS__", json.dumps(colours))
            .replace("__DATA__", json.dumps(items, separators=(",", ":")).replace("</", "<\\/")))
    Path(out_path).write_text(page, encoding="utf-8")
    return len(items)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__ 3D</title>
<style>
html,body{margin:0;height:100%;font:14px/1.45 Segoe UI,Arial,sans-serif;background:#f4f5f7;color:#1c1c1c}
#view{position:fixed;inset:0}
.panel{position:fixed;background:#fff;border:1px solid #d6d8dc;border-radius:8px;padding:10px 12px;box-shadow:0 2px 8px #0001}
#tools{top:12px;left:12px;max-width:260px}
#info{top:12px;right:12px;width:300px;max-height:80vh;overflow:auto}
h1{font-size:15px;margin:0 0 6px} label{display:block;cursor:pointer}
.sw{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.badge{display:inline-block;background:#b3261e;color:#fff;border-radius:3px;padding:0 5px;font-size:11px;font-weight:600}
table{border-collapse:collapse;width:100%} td{padding:2px 4px;border-bottom:1px solid #eee;vertical-align:top}
td:first-child{color:#666;white-space:nowrap} code{font-size:12px;word-break:break-all}
@media (max-width:640px){#info{top:auto;bottom:12px;left:12px;right:12px;width:auto;max-height:40vh}}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script></head><body>
<div id="view"></div>
<div id="tools" class="panel"><h1>__TITLE__</h1><span class="badge">TO BE VERIFIED</span>
<div id="legend" style="margin:8px 0"></div><div id="layers"></div>
<div style="margin-top:6px;color:#666">Drag to turn, scroll to zoom, right-drag to pan. Click an element.</div></div>
<div id="info" class="panel">Click an element to see where it came from and how sure the tool is.</div>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
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
controls.target.copy(centre); camera.position.copy(centre).add(new THREE.Vector3(size * 0.5, -size * 0.7, size * 0.6));
controls.update();
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
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
  const d = picked.userData, rows = [["Type", d.type], ["Name", d.name], ["Storey", d.storey],
    ["Confidence", d.confidence == null ? "" : `${d.confidence} (${d.level})`], ["Status", d.status], ["Rule", d.rule],
    ["Source", d.source], ["Printed area", d.printed_area ? d.printed_area + " m2" : ""], ["IFC GlobalId", `<code>${esc(d.id)}</code>`]];
  document.getElementById("info").innerHTML = "<table>" + rows.filter(r => r[1] !== "" && r[1] != null)
    .map(([k, v]) => `<tr><td>${k}</td><td>${k === "IFC GlobalId" ? v : esc(v)}</td></tr>`).join("") + "</table>";
});
addEventListener("resize", () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });
renderer.setAnimationLoop(() => renderer.render(scene, camera));
</script></body></html>
"""
