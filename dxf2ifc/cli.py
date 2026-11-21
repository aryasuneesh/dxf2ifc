"""Command line: python -m dxf2ifc <command>. `run` does the whole chain on one DXF."""

import argparse
import json
import re
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .schema import level


def convert(dxf_path, out_dir, elevation=None, height=None, bbox=None, project=None) -> tuple[dict, list[dict]]:
    """DXF -> plan.json, roles.json, model.ifc, model_ifc2x3.ifc, review.html, viewer.html, compare.html in out_dir. Returns the plan and the checks."""
    from .extract import extract
    from .normalize import load
    from .qa import check
    from .review import plan_image, viewer_notes, write_review
    from .roles import assign_roles
    from .to_ifc import write_all
    from .viewer import write_compare, write_viewer

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    drawing = load(dxf_path, bbox=bbox)
    if project:
        drawing.project = project
    roles = assign_roles(drawing)
    plan = extract(drawing, roles, elevation=elevation, height=height)
    errors = write_all([plan], out_dir / "model.ifc")
    plan["stats"]["runtime_s"] = round(time.perf_counter() - start, 1)
    checks = check([plan], out_dir / "model.ifc", runtime_s=plan["stats"]["runtime_s"], errors=errors)
    write_review(plan, checks, out_dir / "review.html", drawing=drawing, ifc_path=out_dir / "model.ifc")
    notes, general = viewer_notes(plan, out_dir / "model.ifc")
    write_viewer(out_dir / "model.ifc", out_dir / "viewer.html", title=plan["source"]["file"],
                 plan_png=plan_image(plan, drawing=drawing), notes=notes, general=general)
    write_compare(out_dir / "compare.html", plan["source"]["file"])
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "roles.json").write_text(json.dumps(roles, indent=1, ensure_ascii=False), encoding="utf-8")
    return plan, checks


def summary(plan, checks, out_dir) -> str:
    source, stats = plan["source"], plan["stats"]
    elements = plan["walls"] + plan["openings"] + plan["rooms"]
    levels = Counter(level(e["confidence"]) for e in elements)
    share = lambda name: f"{levels[name] / len(elements):.0%}" if elements else "-"
    failed = [f"{c['id']} {c['name'].lower()}: {c['value']}" for c in checks if c["passed"] is False]
    lines = [
        f"File: {source['file']} ({source['family']}, {source['units_in']})",
        f"Walls {len(plan['walls'])} | Openings {len(plan['openings'])} | Rooms {len(plan['rooms'])} | "
        f"High {share('high')} Medium {share('medium')} Low {share('low')}",
        f"Requirements: {sum(c['passed'] is True for c in checks)} passed, {len(failed)} failed, "
        f"{sum(c['passed'] is None for c in checks)} n/a" + (f" ({'; '.join(failed)})" if failed else ""),
        f"To review: {sum(f['severity'] == 'review' for f in plan['flags'])} items",
        f"Time: {stats['runtime_s']} s | LLM calls: {stats['llm_calls']}",
        f"Open: {Path(out_dir) / 'review.html'}",
        f"Plan and 3D side by side (served): {Path(out_dir) / 'compare.html'}",
    ]
    return "\n".join(lines)


def merge(plan_paths, out_path) -> list[str]:
    """Several plan.json files (one per storey) into one building."""
    from .to_ifc import write_all
    from .viewer import write_viewer

    plans = sorted((json.loads(Path(p).read_text(encoding="utf-8")) for p in plan_paths),
                   key=lambda plan: plan["storey"]["elevation_m"])
    for lower, upper in zip(plans, plans[1:]):  # a default height gives way to the real gap between levels
        gap = round(upper["storey"]["elevation_m"] - lower["storey"]["elevation_m"], 2)
        if lower["storey"]["height_source"] == "default" and gap > 0:
            lower["storey"].update(height_m=gap, height_source="next_level")
    errors = write_all(plans, out_path, roof=True)  # the top storey gets an assumed flat roof
    if not errors:
        write_viewer(out_path, Path(out_path).with_suffix(".html"), title=plans[0]["source"]["project"])
    return errors


# ---------------------------------------------------------------- try-it page

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>DXF to IFC</title>
<style>body{font:17px/1.5 Segoe UI,Arial,sans-serif;max-width:720px;margin:40px auto;padding:0 20px}
input,button{font:inherit;padding:6px 10px;margin:4px 0} button{background:#1d5fbf;color:#fff;border:0;border-radius:4px;cursor:pointer}
label{display:block;margin-top:12px} pre{background:#f3f3f3;padding:12px;white-space:pre-wrap} .note{color:#555;font-size:15px}</style>
</head><body><h1>DXF to IFC</h1>
<p>Choose one floor plan as a DXF file. It is processed on this computer; nothing is sent anywhere.</p>
<label>DXF file <input type="file" id="file" accept=".dxf"></label>
<label>Storey elevation in metres (leave empty to read it from the drawing) <input id="elevation" size="8"></label>
<label>Storey height in metres (leave empty for 3.00) <input id="height" size="8"></label>
<p><button id="go">Convert</button> <span id="state" class="note"></span></p>
<pre id="result" hidden></pre><p id="links" hidden></p>
<script>
document.getElementById("go").onclick = async () => {
  const file = document.getElementById("file").files[0];
  if (!file) { document.getElementById("state").textContent = "Choose a file first."; return; }
  const query = new URLSearchParams({name: file.name, elevation: document.getElementById("elevation").value,
                                     height: document.getElementById("height").value});
  document.getElementById("state").textContent = "Working...";
  const reply = await fetch("/run?" + query, {method: "POST", body: file});
  const data = await reply.json();
  document.getElementById("state").textContent = reply.ok ? "Done." : "Could not convert this file.";
  const result = document.getElementById("result");
  result.hidden = false; result.textContent = reply.ok ? data.summary : data.error;
  const links = document.getElementById("links");
  links.hidden = !reply.ok;
  if (reply.ok) links.innerHTML = `<a href="/out/${data.id}/review.html" target="_blank">Open the review page</a> &middot; ` +
    `<a href="/out/${data.id}/compare.html" target="_blank">Plan and 3D side by side</a> &middot; ` +
    `<a href="/out/${data.id}/viewer.html" target="_blank">3D view</a> &middot; ` +
    `<a href="/out/${data.id}/model.ifc" download>IFC4</a> &middot; <a href="/out/${data.id}/model_ifc2x3.ifc" download>IFC2X3</a> &middot; <a href="/out/${data.id}/plan.json" download>plan.json</a>`;
};
</script></body></html>"""
SERVED = {"review.html": "text/html; charset=utf-8", "model.ifc": "application/octet-stream",
          "model_ifc2x3.ifc": "application/octet-stream", "viewer.html": "text/html; charset=utf-8",
          "compare.html": "text/html; charset=utf-8",
          "plan.json": "application/json", "roles.json": "application/json"}


def make_server(root, port=8000) -> ThreadingHTTPServer:
    """A local page to try the tool: upload a DXF, get the review page and the IFC. Bound to this machine only."""
    root = Path(root)
    lock, counter = threading.Lock(), [0]

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body, kind):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            match = re.fullmatch(r"/out/(\d+)/([a-z.]+)", path)
            if path == "/":
                self.reply(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif match and match.group(2) in SERVED and (root / match.group(1) / match.group(2)).is_file():
                self.reply(200, (root / match.group(1) / match.group(2)).read_bytes(), SERVED[match.group(2)])
            else:
                self.reply(404, b"not found", "text/plain")

        def do_POST(self):
            url = urlparse(self.path)
            size = int(self.headers.get("Content-Length") or 0)
            if url.path == "/feedback":  # a tester's answers from a review page: one JSON line each
                try:
                    answers = json.loads(self.rfile.read(size)) if 0 < size <= 64_000 else None
                except ValueError:
                    answers = None
                if not isinstance(answers, dict):
                    return self.reply(400, b'{"error": "send one JSON object"}', "application/json")
                with lock, (root / "feedback.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(answers, ensure_ascii=False) + "\n")
                return self.reply(200, b'{"saved": true}', "application/json")
            if url.path != "/run" or not 0 < size <= config.MAX_DXF_MB * 1_000_000:
                return self.reply(400, json.dumps({"error": "send one DXF file"}).encode(), "application/json")
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            number = lambda key: float(query[key].replace(",", ".")) if query.get(key, "").strip() else None
            with lock:  # one conversion at a time keeps memory use predictable
                counter[0] += 1
                run_dir = root / str(counter[0])
                run_dir.mkdir(parents=True, exist_ok=True)
                name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(query.get("name", "plan.dxf")).name) or "plan.dxf"
                (run_dir / name).write_bytes(self.rfile.read(size))
                try:
                    plan, checks = convert(run_dir / name, run_dir, elevation=number("elevation"), height=number("height"))
                    body, status = {"id": counter[0], "summary": summary(plan, checks, run_dir)}, 200
                except ValueError as err:
                    body, status = {"error": str(err)}, 422
            self.reply(status, json.dumps(body).encode("utf-8"), "application/json")

        def log_message(self, *args):  # keep the console quiet; file names are not for logs
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def feedback_summary(paths) -> str:
    """Totals over feedback files (feedback.jsonl from the server, or downloaded feedback-*.json)."""
    rows = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8").strip()
        rows += [json.loads(line) for line in text.splitlines() if line.strip()] if path.endswith(".jsonl") else [json.loads(text)]
    if not rows:
        return "no feedback yet"
    verdicts = [v.get("verdict") for row in rows for v in row.get("verdicts", {}).values() if v.get("verdict")]
    useful = [int(row["useful"]) for row in rows if str(row.get("useful", "")).isdigit()]
    lines = [f"Testers: {len(rows)} ({', '.join(f'{n} {role}' for role, n in Counter(r.get('role', '?') for r in rows).most_common())})",
             f"Usefulness: mean {sum(useful) / len(useful):.1f} of 5" if useful else "Usefulness: no answers",
             f"Items judged: {len(verdicts)}, wrong {verdicts.count('wrong')}, correct {verdicts.count('ok')}, "
             f"not sure {verdicts.count('unsure')}",
             f"Minutes on page: median {sorted(r.get('minutes_on_page', 0) for r in rows)[len(rows) // 2]}"]
    for key, title in (("distrust", "Would not trust"), ("change", "Change first"), ("remarks", "Other remarks")):
        notes = [r[key].strip() for r in rows if r.get(key, "").strip()]
        lines += [f"{title}:"] + [f"  - {note}" for note in notes]
    comments = [(r.get("file", "?"), item, v) for r in rows for item, v in r.get("verdicts", {}).items()
                if (v.get("comment") or "").strip()]
    lines += ["Comments on items:"] + [f"  - {file} | {item} [{v.get('verdict') or 'no verdict'}]: {v['comment'].strip()}"
                                       for file, item, v in comments]
    return "\n".join(lines)


# ---------------------------------------------------------------- entry point

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # file and layer names can hold letters the console cannot show
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="python -m dxf2ifc", description="2D DXF floor plans to IFC, with a review page.")
    parser.add_argument("--debug", action="store_true", help="show the full error trace")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("feedback", help="sum up user-test feedback files")
    p.add_argument("files", nargs="+", help="feedback.jsonl from the try-it server or downloaded feedback-*.json")

    p = sub.add_parser("run", help="convert one DXF")
    p.add_argument("dxf")
    p.add_argument("--out", required=True)
    p.add_argument("--elevation", type=float, help="storey elevation in metres")
    p.add_argument("--height", type=float, help="storey height in metres")
    p.add_argument("--bbox", help="xmin,ymin,xmax,ymax in metres: use only this part of the sheet")
    p.add_argument("--project", help="name that keeps element ids stable across revisions of the file")

    p = sub.add_parser("merge", help="several plan.json files into one building")
    p.add_argument("plans", nargs="+")
    p.add_argument("--out", required=True)

    p = sub.add_parser("triage", help="scan a folder or zip of DXF files")
    p.add_argument("source")
    p.add_argument("--out", default="data/dxf_index.csv")
    p.add_argument("--workers", type=int)

    p = sub.add_parser("shortlist", help="rank scanned files and draw thumbnails")
    p.add_argument("index")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--out", default="data/shortlist.md")
    p.add_argument("--thumbs", default="data/thumbs")

    p = sub.add_parser("batch", help="run the pipeline on every plan-like file of an index")
    p.add_argument("index")
    p.add_argument("--out", default="data/pipeline.csv")
    p.add_argument("--min-score", type=int, default=config.TRIAGE_PLAN_SCORE)
    p.add_argument("--max-mb", type=float, default=config.BATCH_MAX_MB)
    p.add_argument("--workers", type=int)

    sub.add_parser("bench", help="accuracy on synthetic plans with a known answer")

    p = sub.add_parser("profile-ifc", help="list what an IFC file contains")
    p.add_argument("ifc")
    p.add_argument("--out", required=True)

    p = sub.add_parser("save-mapping", help="keep a confirmed roles.json for the next file with the same layers")
    p.add_argument("roles")

    p = sub.add_parser("serve", help="local page to try the tool in a browser")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--out", default="out/web")

    p = sub.add_parser("share", help="website for testers: converted plans plus feedback, no upload")
    p.add_argument("folder", help="folder with one subfolder per converted plan")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--password", help="ask every visitor for this password")
    p = sub.add_parser("share-pack", help="zip converted plans and the website file for hosting")
    p.add_argument("plans", nargs="+", help="output folders of `run`")
    p.add_argument("--zip", default="site.zip")
    p.add_argument("--offline", action="store_true", help="a folder to email: start page, no server, feedback saved as files")
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (ValueError, OSError) as err:
        if args.debug:
            raise
        print(f"error: {err}", file=sys.stderr)
        return 2


def _dispatch(args) -> int:
    if args.command == "feedback":
        print(feedback_summary(args.files))
    elif args.command == "run":
        bbox = tuple(float(v) for v in args.bbox.split(",")) if args.bbox else None
        if bbox and len(bbox) != 4:
            raise ValueError("--bbox needs four numbers: xmin,ymin,xmax,ymax")
        plan, checks = convert(args.dxf, args.out, args.elevation, args.height, bbox, args.project)
        print(summary(plan, checks, args.out))
    elif args.command == "merge":
        errors = merge(args.plans, args.out)
        print(f"{len(args.plans)} storeys written to {args.out}, {len(errors)} validation errors")
        return 1 if errors else 0
    elif args.command == "triage":
        from .triage import triage
        print(f"scanned {triage(args.source, args.out, args.workers)} new files into {args.out}")
    elif args.command == "shortlist":
        from .triage import write_shortlist
        for row in write_shortlist(args.index, args.out, args.n, args.thumbs):
            print(row["score"], row["signals"], Path(row["path"]).name)
    elif args.command == "batch":
        from .batch import run_batch
        from .triage import read_index
        chosen = [row["path"] for row in read_index(args.index) if not row["error"]
                  and int(row["score"]) >= args.min_score and float(row["size_mb"]) <= args.max_mb]
        print(f"ran {run_batch(chosen, args.out, args.workers)} of {len(chosen)} selected files into {args.out}")
    elif args.command == "bench":
        from .qa import bench
        print(f"{'Form of the plan':34} {'Wall precision':>14} {'Wall recall':>12} {'Rooms':>7} {'Doors':>7} {'Worst area error':>17}")
        for row in bench():
            print(f"{row['variant']:34} {row['wall_precision']:>14.1%} {row['wall_recall']:>12.1%} {row['rooms']:>7} "
                  f"{row['doors']:>7} {row['max_area_error_pct']:>15.2f} %")
    elif args.command == "profile-ifc":
        from .qa import profile_ifc
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(profile_ifc(args.ifc), indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"profile written to {args.out}")
    elif args.command == "save-mapping":
        from .roles import save_mapping
        print(f"mapping saved to {save_mapping(json.loads(Path(args.roles).read_text(encoding='utf-8')))}")
    elif args.command == "share-pack":
        from .share import pack
        print(f"{args.zip}: {', '.join(pack(args.plans, args.zip, offline=args.offline))}")
    elif args.command in ("serve", "share"):
        if args.command == "share":
            from .share import serve
            server = serve(args.folder, args.port, args.password)
        else:
            server = make_server(args.out, args.port)
        print(f"Open http://127.0.0.1:{args.port} in a browser. Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0
