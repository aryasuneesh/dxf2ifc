"""A small website for testers: the converted plans (review page, 3D view, side by side) plus a feedback endpoint.

Standard library only, so the same file runs on a free PythonAnywhere account (WSGI) and on a laptop:
    python -m dxf2ifc share out/demo --password SECRET
No upload or conversion here: plans are converted beforehand and copied in by `share-pack`.
"""

import base64
import hmac
import html
import json
import re
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FILES = {"compare.html": "text/html; charset=utf-8", "review.html": "text/html; charset=utf-8",
         "viewer.html": "text/html; charset=utf-8", "model.ifc": "application/octet-stream",
         "model_ifc2x3.ifc": "application/octet-stream"}
MAX_FEEDBACK = 200_000  # bytes; comments on every item of a long review list fit easily
_lock = threading.Lock()


def plans(root) -> list[str]:
    return sorted(p.name for p in Path(root).iterdir() if (p / "review.html").is_file())


def index_page(names, offline=False) -> bytes:
    """Start page. Online: each plan opens side by side. Offline (zip sent by email): review page and 3D in two tabs."""
    e = html.escape
    rows = "".join(f"<li><a href='{e(n)}/viewer.html'>{e(n)}</a> <span>(<a href='{e(n)}/review.html'>full report</a>)</span></li>"
                   if offline else
                   f"<li><a href='{e(n)}/compare.html'>{e(n)}</a> <span>(<a href='{e(n)}/review.html'>review page only</a> · "
                   f"<a href='{e(n)}/viewer.html'>3D only</a>)</span></li>" for n in names)
    steps = ("<li>Unzip the whole folder first (opening files inside the zip does not work), then open a plan below "
             "in Chrome or Edge. It opens as a 3D model with the 2D plan in the top right corner.</li>"
             "<li>Press <b>Next</b> to go through a few questions. Each covers all elements with the same doubt; "
             "they blink in the model. Say if they are Correct, Wrong or Not sure, and add a comment if you like. The first time, the browser asks where to save your feedback file: "
             "pick a folder you will find again. Every answer is written to that file straight away.</li>"
             "<li>At the end, open <b>Overall questions</b> on the left and answer them. Then email the feedback file "
             "(one per plan) to the person who sent you this folder. In other browsers, press <b>Download feedback</b> to get the file.</li>"
             if offline else
             "<li>Open a plan. The drawing and a list of things to check are on the left, the 3D model on the right.</li>"
             "<li>For each item in \"To review\", press Correct, Wrong or Not sure, and add a comment if you like.</li>"
             "<li>At the bottom of the left side, answer four short questions and press Send feedback.</li>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>DXF to IFC: user test</title>
<style>:root{{color-scheme:light}} body{{font:17px/1.6 Segoe UI,Arial,sans-serif;color:#1c1c1c;background:#fff;
max-width:760px;margin:40px auto;padding:0 20px}} a{{color:#1d5fbf}} li{{margin:10px 0}} span{{color:#555;font-size:15px}}
.box{{background:#eef4fc;border-left:5px solid #1d5fbf;padding:10px 16px}}</style></head><body>
<h1>From a 2D plan to a model you can check</h1>
<p>A program turned each drawing below into a 3D building model (IFC). We would like your opinion as an engineer.</p>
<div class="box"><b>What to do (about 10 minutes per plan)</b><ol>
{steps}</ol></div>
<h2>Plans</h2><ul>{rows or "<li>No plans yet.</li>"}</ul>
<p><span>{"Nothing on these pages is sent anywhere; works without internet." if offline else
'On a small screen, use "review page only"; its "show in 3D" links open the model in a new tab.'}</span></p>
</body></html>""".encode("utf-8")


def make_app(root, password=None):
    """WSGI app serving the plans in `root` (one folder per plan). With a password, every request needs it
    (browser login box, any user name)."""
    root = Path(root)

    def app(environ, start_response):
        def reply(status, body, kind="text/plain; charset=utf-8", extra=()):
            start_response(status, [("Content-Type", kind), ("Content-Length", str(len(body))),
                                    ("X-Content-Type-Options", "nosniff"), *extra])
            return [body]

        if password:
            given = environ.get("HTTP_AUTHORIZATION", "")
            try:
                secret = base64.b64decode(given[6:]).decode("utf-8").partition(":")[2] if given.startswith("Basic ") else ""
            except ValueError:
                secret = ""
            if not hmac.compare_digest(secret.encode(), password.encode()):
                return reply("401 Unauthorized", b"Password needed.", extra=[("WWW-Authenticate", 'Basic realm="user test"')])

        method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/")
        if method == "POST" and path == "/feedback":
            try:
                size = int(environ.get("CONTENT_LENGTH") or 0)
                answers = json.loads(environ["wsgi.input"].read(size)) if 0 < size <= MAX_FEEDBACK else None
            except ValueError:
                answers = None
            if not isinstance(answers, dict):
                return reply("400 Bad Request", b'{"error": "send one JSON object"}', "application/json")
            answers["received"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with _lock, (root / "feedback.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(answers, ensure_ascii=False) + "\n")
            return reply("200 OK", b'{"saved": true}', "application/json")
        if method not in ("GET", "HEAD"):
            return reply("405 Method Not Allowed", b"not allowed")
        if path == "/":
            return reply("200 OK", index_page(plans(root)), "text/html; charset=utf-8")
        match = re.fullmatch(r"/([\w.-]+)/([\w.]+)", path)
        if match and not match.group(1).startswith(".") and match.group(2) in FILES:
            target = root / match.group(1) / match.group(2)
            if target.is_file():
                return reply("200 OK", target.read_bytes(), FILES[match.group(2)])
        return reply("404 Not Found", b"not found")

    return app


def pack(plan_dirs, zip_path, offline=False) -> list[str]:
    """Zip converted plans (pages and IFC files; no plan.json, no DXF).
    Default: for hosting, with this file as the website. offline=True: to email, with a start page instead."""
    folders = [Path(f) for f in plan_dirs]
    for folder in folders:
        if not (folder / "review.html").is_file():
            raise ValueError(f"{folder} has no review.html; run the plan first")
    top, files = ("dxf2ifc user test", ("review.html", "viewer.html", "model.ifc")) if offline else ("site/plans", FILES)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        if offline:
            archive.writestr(f"{top}/START HERE.html", index_page([f.name for f in folders], offline=True))
        else:
            archive.write(__file__, "site/share.py")
        for folder in folders:
            for name in files:
                if (folder / name).is_file():
                    archive.write(folder / name, f"{top}/{folder.name}/{name}")
    return [f.name for f in folders]


def serve(root, port=8000, password=None, host="127.0.0.1"):
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    class Quiet(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    return make_server(host, port, make_app(root, password), handler_class=Quiet)
